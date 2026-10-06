import base64
import binascii
import json
from uuid import UUID
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL


bearer_scheme = HTTPBearer(auto_error=False)


def _extrair_session_id(token: str, usuario_id: str) -> str | None:
    """Lê session_id somente após o Auth do Supabase validar o bearer token."""

    try:
        partes = token.split(".")
        if len(partes) != 3:
            return None
        payload_codificado = partes[1]
        payload = base64.urlsafe_b64decode(
            payload_codificado + "=" * (-len(payload_codificado) % 4)
        )
        claims = json.loads(payload)
        if claims.get("sub") != str(usuario_id):
            return None
        return str(UUID(claims["session_id"]))
    except (
        AttributeError,
        binascii.Error,
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return None


def obter_usuario_atual(
    credenciais: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    """Valida o token de acesso consultando o Supabase Auth."""

    if credenciais is None or credenciais.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticação necessária",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not SUPABASE_URL or not SUPABASE_PUBLISHABLE_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Autenticação não configurada no servidor",
        )

    endpoint = f"{SUPABASE_URL.rstrip('/')}/auth/v1/user"
    requisicao = Request(
        endpoint,
        headers={
            "apikey": SUPABASE_PUBLISHABLE_KEY,
            "Authorization": f"Bearer {credenciais.credentials}",
        },
        method="GET",
    )

    try:
        with urlopen(requisicao, timeout=5) as resposta:
            dados = json.loads(resposta.read().decode("utf-8"))
    except HTTPError as erro:
        if erro.code in (401, 403):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido ou expirado",
                headers={"WWW-Authenticate": "Bearer"},
            ) from erro

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Não foi possível validar a autenticação",
        ) from erro
    except URLError as erro:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Serviço de autenticação indisponível",
        ) from erro
    except (json.JSONDecodeError, UnicodeDecodeError) as erro:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Resposta inválida do serviço de autenticação",
        ) from erro

    usuario_id = dados.get("id")
    if not usuario_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token sem identificação de usuário",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "id": usuario_id,
        "email": dados.get("email"),
        # Os claims só são confiáveis porque o mesmo bearer acabou de ser
        # validado pelo endpoint /auth/v1/user acima.
        "session_id": _extrair_session_id(credenciais.credentials, usuario_id),
    }
