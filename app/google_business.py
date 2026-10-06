from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet, InvalidToken

from app.config import (
    GOOGLE_BUSINESS_CLIENT_ID,
    GOOGLE_BUSINESS_CLIENT_SECRET,
    GOOGLE_BUSINESS_REDIRECT_URI,
    GOOGLE_BUSINESS_TOKEN_ENCRYPTION_KEY,
)


GOOGLE_BUSINESS_SCOPE = "https://www.googleapis.com/auth/business.manage"
GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_ACCOUNT_MANAGEMENT_ENDPOINT = (
    "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
)
GOOGLE_BUSINESS_INFORMATION_ENDPOINT = (
    "https://mybusinessbusinessinformation.googleapis.com/v1"
)


class GoogleBusinessConfigurationError(RuntimeError):
    """Configuração ausente ou inválida para o OAuth server-side."""


class GoogleBusinessApiError(RuntimeError):
    def __init__(self, status_code: int | None = None):
        self.status_code = status_code
        super().__init__("Falha na comunicação com o Google Business Profile")


class GoogleBusinessTokenError(RuntimeError):
    """Token ausente, expirado sem refresh ou impossível de decifrar."""


@dataclass(frozen=True)
class GoogleToken:
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    granted_scope: str


def _agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def gerar_state() -> str:
    return secrets.token_urlsafe(32)


def hash_state(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def hash_session_id(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("ascii")).hexdigest()


def prazo_state() -> datetime:
    return _agora_utc() + timedelta(minutes=10)


def _fernet() -> Fernet:
    if not GOOGLE_BUSINESS_TOKEN_ENCRYPTION_KEY:
        raise GoogleBusinessConfigurationError(
            "Criptografia do OAuth Google não configurada"
        )

    try:
        return Fernet(GOOGLE_BUSINESS_TOKEN_ENCRYPTION_KEY.encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError) as erro:
        raise GoogleBusinessConfigurationError(
            "Chave de criptografia do OAuth Google inválida"
        ) from erro


def cifrar_token(token: str | None) -> str | None:
    if token is None:
        return None
    return _fernet().encrypt(token.encode("utf-8")).decode("ascii")


def decifrar_token(token_cifrado: str | None) -> str | None:
    if token_cifrado is None:
        return None

    try:
        return _fernet().decrypt(token_cifrado.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as erro:
        raise GoogleBusinessTokenError(
            "Não foi possível ler a credencial Google armazenada"
        ) from erro


def exigir_configuracao_oauth() -> None:
    if not GOOGLE_BUSINESS_CLIENT_ID or not GOOGLE_BUSINESS_CLIENT_SECRET:
        raise GoogleBusinessConfigurationError(
            "Credenciais OAuth do Google não configuradas"
        )
    if not GOOGLE_BUSINESS_REDIRECT_URI.startswith("https://"):
        raise GoogleBusinessConfigurationError(
            "O callback OAuth do Google precisa usar HTTPS"
        )
    _fernet()


def criar_url_autorizacao(state: str) -> str:
    exigir_configuracao_oauth()
    parametros = {
        "client_id": GOOGLE_BUSINESS_CLIENT_ID,
        "redirect_uri": GOOGLE_BUSINESS_REDIRECT_URI,
        "response_type": "code",
        "scope": GOOGLE_BUSINESS_SCOPE,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{urlencode(parametros)}"


def _requisitar_google(
    metodo: str,
    url: str,
    *,
    token: str | None = None,
    formulario: dict[str, str] | None = None,
    parametros: dict[str, str | int] | None = None,
) -> dict:
    if parametros:
        url = f"{url}?{urlencode(parametros)}"

    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = None
    if formulario is not None:
        data = urlencode(formulario).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    requisicao = Request(url, data=data, headers=headers, method=metodo)

    try:
        with urlopen(requisicao, timeout=10) as resposta:
            corpo = resposta.read()
    except HTTPError as erro:
        erro.close()
        raise GoogleBusinessApiError(erro.code) from erro
    except URLError as erro:
        raise GoogleBusinessApiError() from erro

    if not corpo:
        return {}

    try:
        dados = json.loads(corpo.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as erro:
        raise GoogleBusinessApiError() from erro

    if not isinstance(dados, dict):
        raise GoogleBusinessApiError()
    return dados


def trocar_codigo_por_token(code: str) -> GoogleToken:
    exigir_configuracao_oauth()
    dados = _requisitar_google(
        "POST",
        GOOGLE_TOKEN_ENDPOINT,
        formulario={
            "code": code,
            "client_id": GOOGLE_BUSINESS_CLIENT_ID,
            "client_secret": GOOGLE_BUSINESS_CLIENT_SECRET,
            "redirect_uri": GOOGLE_BUSINESS_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
    )
    return _token_a_partir_da_resposta(
        dados,
        fallback_scope=GOOGLE_BUSINESS_SCOPE,
    )


def renovar_token(refresh_token: str, granted_scope: str) -> GoogleToken:
    exigir_configuracao_oauth()
    dados = _requisitar_google(
        "POST",
        GOOGLE_TOKEN_ENDPOINT,
        formulario={
            "refresh_token": refresh_token,
            "client_id": GOOGLE_BUSINESS_CLIENT_ID,
            "client_secret": GOOGLE_BUSINESS_CLIENT_SECRET,
            "grant_type": "refresh_token",
        },
    )
    token = _token_a_partir_da_resposta(
        dados,
        refresh_token=refresh_token,
        fallback_scope=granted_scope,
    )
    return token


def _token_a_partir_da_resposta(
    dados: dict,
    *,
    refresh_token: str | None = None,
    fallback_scope: str | None = None,
) -> GoogleToken:
    access_token = dados.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise GoogleBusinessApiError()

    expires_in = dados.get("expires_in", 3600)
    if not isinstance(expires_in, int) or expires_in <= 0:
        raise GoogleBusinessApiError()

    resposta_scope = dados.get("scope")
    granted_scope = (
        resposta_scope
        if isinstance(resposta_scope, str)
        else (fallback_scope or "")
    )
    if GOOGLE_BUSINESS_SCOPE not in granted_scope.split():
        raise GoogleBusinessApiError()

    resposta_refresh = dados.get("refresh_token")
    if resposta_refresh is not None and not isinstance(resposta_refresh, str):
        raise GoogleBusinessApiError()

    return GoogleToken(
        access_token=access_token,
        refresh_token=resposta_refresh or refresh_token,
        expires_at=_agora_utc() + timedelta(seconds=expires_in),
        granted_scope=granted_scope,
    )


def listar_locais(access_token: str) -> list[dict[str, str | None]]:
    locais: dict[str, dict[str, str | None]] = {}

    for conta in _listar_paginas(
        GOOGLE_ACCOUNT_MANAGEMENT_ENDPOINT,
        "accounts",
        access_token,
        parametros={"pageSize": 20},
    ):
        nome_conta = conta.get("name")
        if not isinstance(nome_conta, str) or not nome_conta:
            continue

        endpoint = (
            f"{GOOGLE_BUSINESS_INFORMATION_ENDPOINT}/"
            f"{quote(nome_conta, safe='/')}/locations"
        )
        for local in _listar_paginas(
            endpoint,
            "locations",
            access_token,
            parametros={
                "pageSize": 100,
                "readMask": "name,title,storeCode,metadata",
            },
        ):
            nome_local = local.get("name")
            if not isinstance(nome_local, str) or not nome_local:
                continue
            titulo = local.get("title") or local.get("locationName")
            locais[nome_local] = {
                "nome": nome_local,
                "titulo": titulo if isinstance(titulo, str) else nome_local,
                "store_code": (
                    local.get("storeCode")
                    if isinstance(local.get("storeCode"), str)
                    else None
                ),
            }

    return list(locais.values())


def _listar_paginas(
    endpoint: str,
    chave: str,
    access_token: str,
    *,
    parametros: dict[str, str | int],
):
    pagina = dict(parametros)

    for _ in range(100):
        dados = _requisitar_google(
            "GET",
            endpoint,
            token=access_token,
            parametros=pagina,
        )
        itens = dados.get(chave, [])
        if not isinstance(itens, list):
            raise GoogleBusinessApiError()

        for item in itens:
            if isinstance(item, dict):
                yield item

        proxima = dados.get("nextPageToken")
        if not isinstance(proxima, str) or not proxima:
            return
        pagina["pageToken"] = proxima

    raise GoogleBusinessApiError()