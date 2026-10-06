from datetime import timedelta
from urllib.parse import urlencode
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator

from app.auth import obter_usuario_atual
from app.config import GOOGLE_BUSINESS_FRONTEND_URL
from app.database import abrir_conexao
from app.google_business import (
    GoogleBusinessApiError,
    GoogleBusinessConfigurationError,
    GoogleBusinessTokenError,
    _agora_utc,
    cifrar_token,
    criar_url_autorizacao,
    decifrar_token,
    gerar_state,
    hash_session_id,
    hash_state,
    listar_locais,
    prazo_state,
    renovar_token,
    trocar_codigo_por_token,
)
from app.routers.dashboard import _obter_conta_dashboard


router = APIRouter()


class SelecionarLocalRequest(BaseModel):
    nome: str = Field(min_length=1, max_length=300)

    @field_validator("nome")
    @classmethod
    def validar_nome(cls, valor: str) -> str:
        valor = valor.strip()
        partes = valor.split("/")
        local_direto = (
            len(partes) == 2
            and partes[0] == "locations"
            and bool(partes[1])
        )
        local_da_conta = (
            len(partes) == 4
            and partes[0] == "accounts"
            and bool(partes[1])
            and partes[2] == "locations"
            and bool(partes[3])
        )
        if not (local_direto or local_da_conta):
            raise ValueError("Informe uma referência de local Google válida")
        return valor


class ConcluirOAuthRequest(BaseModel):
    state: str = Field(min_length=1, max_length=200)


class _ReautorizacaoNecessaria(Exception):
    def __init__(self, detalhe: str, codigo: str):
        self.detalhe = detalhe
        self.codigo = codigo
        super().__init__(detalhe)


def _erro_http_google(erro: GoogleBusinessApiError, detalhe: str) -> HTTPException:
    if erro.status_code == 429:
        return HTTPException(
            status_code=429,
            detail="O limite de requisições do Google foi atingido; aguarde e tente novamente.",
        )
    return HTTPException(status_code=502, detail=detalhe)


def _obter_empresa_cliente(cursor, usuario_id: str) -> int:
    role, establishment_id = _obter_conta_dashboard(cursor, usuario_id)

    if role != "client" or establishment_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A conexão Google está disponível para clientes vinculados a uma empresa",
        )

    cursor.execute(
        """
        SELECT id, archived_at
        FROM establishments
        WHERE id = %s
        """,
        (establishment_id,),
    )
    empresa = cursor.fetchone()

    if empresa is None:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    if empresa[1] is not None:
        raise HTTPException(
            status_code=403,
            detail="O acesso desta empresa está temporariamente indisponível",
        )

    return empresa[0]


def _bloquear_estabelecimento(cursor, establishment_id: int):
    cursor.execute(
        "SELECT id FROM establishments WHERE id = %s FOR UPDATE",
        (establishment_id,),
    )
    if cursor.fetchone() is None:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")


def _bloquear_empresa_cliente(cursor, usuario_id: str) -> int:
    establishment_id = _obter_empresa_cliente(cursor, usuario_id)
    _bloquear_estabelecimento(cursor, establishment_id)
    if _obter_empresa_cliente(cursor, usuario_id) != establishment_id:
        raise HTTPException(
            status_code=403,
            detail="A empresa mudou durante a operação Google",
        )
    return establishment_id


def _exigir_sessao_supabase_ativa(cursor, usuario: dict) -> str:
    session_id = usuario.get("session_id")
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A sessão atual não pode concluir a conexão Google",
        )

    try:
        session_uuid = UUID(session_id)
        user_uuid = UUID(usuario["id"])
    except (KeyError, TypeError, ValueError) as erro:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A sessão atual não pode concluir a conexão Google",
        ) from erro

    cursor.execute(
        """
        SELECT 1
        FROM auth.sessions
        WHERE id = %s AND user_id = %s
        """,
        (session_uuid, user_uuid),
    )
    if cursor.fetchone() is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A sessão atual expirou; entre novamente",
        )

    return str(session_uuid)


def _exigir_frontend_configurado():
    if not GOOGLE_BUSINESS_FRONTEND_URL:
        raise HTTPException(
            status_code=503,
            detail="Destino do frontend para o OAuth Google não configurado",
        )


def _redirecionar_frontend(resultado: str, *, state: str | None = None):
    _exigir_frontend_configurado()
    parametros = {"google_business": resultado}
    if state is not None:
        parametros["google_business_state"] = state
    return RedirectResponse(
        url=(
            f"{GOOGLE_BUSINESS_FRONTEND_URL}/dashboard#"
            f"{urlencode(parametros)}"
        ),
        status_code=303,
    )


def _salvar_conexao_oauth(
    cursor,
    establishment_id: int,
    usuario_id: str,
    token,
    versao_inicial=None,
):
    if not token.refresh_token:
        raise GoogleBusinessTokenError(
            "O Google não retornou uma credencial renovável para esta autorização"
        )

    cursor.execute(
        """
        SELECT version
        FROM google_business_connections
        WHERE establishment_id = %s
        FOR UPDATE
        """,
        (establishment_id,),
    )
    conexao_atual = cursor.fetchone()
    if versao_inicial is None:
        if conexao_atual is not None:
            return False
    elif conexao_atual is None or conexao_atual[0] != versao_inicial:
        return False

    cursor.execute(
        """
        INSERT INTO google_business_connections (
            establishment_id,
            dashboard_user_id,
            access_token_encrypted,
            refresh_token_encrypted,
            access_token_expires_at,
            granted_scope,
            status,
            version
        )
        VALUES (%s, %s, %s, %s, %s, %s, 'connected', %s)
        ON CONFLICT (establishment_id) DO UPDATE SET
            dashboard_user_id = EXCLUDED.dashboard_user_id,
            access_token_encrypted = EXCLUDED.access_token_encrypted,
            refresh_token_encrypted = EXCLUDED.refresh_token_encrypted,
            access_token_expires_at = EXCLUDED.access_token_expires_at,
            granted_scope = EXCLUDED.granted_scope,
            google_location_name = NULL,
            google_location_title = NULL,
            status = 'connected',
            last_error_code = NULL,
            updated_at = clock_timestamp(),
            version = EXCLUDED.version
        WHERE %s::uuid IS NOT NULL
          AND google_business_connections.version = %s
        RETURNING version
        """,
        (
            establishment_id,
            usuario_id,
            cifrar_token(token.access_token),
            cifrar_token(token.refresh_token),
            token.expires_at,
            token.granted_scope,
            uuid4(),
            versao_inicial,
            versao_inicial,
        ),
    )
    return cursor.fetchone() is not None


def _buscar_conexao(cursor, establishment_id: int, usuario_id: str):
    cursor.execute(
        """
        SELECT
            id,
            status,
            access_token_encrypted,
            refresh_token_encrypted,
            access_token_expires_at,
            granted_scope,
            google_location_name,
            google_location_title,
            version,
            last_error_code
        FROM google_business_connections
        WHERE establishment_id = %s
          AND dashboard_user_id = %s
        """,
        (establishment_id, usuario_id),
    )
    conexao = cursor.fetchone()

    if conexao is None:
        return None

    try:
        access_token = decifrar_token(conexao[2])
        refresh_token = decifrar_token(conexao[3])
    except (GoogleBusinessConfigurationError, GoogleBusinessTokenError) as erro:
        raise HTTPException(
            status_code=503,
            detail="Não foi possível ler a conexão Google; reconfigure a integração",
        ) from erro

    return {
        "id": conexao[0],
        "establishment_id": establishment_id,
        "dashboard_user_id": usuario_id,
        "status": conexao[1],
        "access_token": access_token,
        "refresh_token": refresh_token,
        "access_token_expires_at": conexao[4],
        "granted_scope": conexao[5],
        "google_location_name": conexao[6],
        "google_location_title": conexao[7],
        "version": conexao[8],
        "last_error_code": conexao[9],
    }


def _validar_empresa_da_conexao(cursor, conexao: dict):
    establishment_atual = _obter_empresa_cliente(
        cursor,
        conexao["dashboard_user_id"],
    )
    if establishment_atual != conexao["establishment_id"]:
        raise HTTPException(
            status_code=403,
            detail="A empresa mudou durante a consulta Google",
        )


def _marcar_reautorizacao(cursor, conexao: dict, codigo: str):
    cursor.execute(
        """
        UPDATE google_business_connections
        SET status = 'reauth_required',
            last_error_code = %s,
            updated_at = clock_timestamp(),
            version = %s
        WHERE id = %s
          AND version = %s
        RETURNING version
        """,
        (codigo, uuid4(), conexao["id"], conexao["version"]),
    )
    return cursor.fetchone()


def _atualizar_token(cursor, conexao: dict, token):
    cursor.execute(
        """
        UPDATE google_business_connections
        SET access_token_encrypted = %s,
            refresh_token_encrypted = COALESCE(%s, refresh_token_encrypted),
            access_token_expires_at = %s,
            granted_scope = %s,
            status = 'connected',
            last_error_code = NULL,
            updated_at = clock_timestamp(),
            version = %s
        WHERE id = %s
          AND version = %s
        RETURNING version
        """,
        (
            cifrar_token(token.access_token),
            cifrar_token(token.refresh_token),
            token.expires_at,
            token.granted_scope,
            uuid4(),
            conexao["id"],
            conexao["version"],
        ),
    )
    return cursor.fetchone()


def _persistir_token_refrescado(conexao: dict, token):
    with abrir_conexao() as banco:
        with banco.cursor() as cursor:
            _validar_empresa_da_conexao(cursor, conexao)
            versao = _atualizar_token(cursor, conexao, token)

    if versao is None:
        raise HTTPException(
            status_code=409,
            detail="A conexão Google mudou durante a consulta. Atualize o painel.",
        )

    conexao["version"] = versao[0]
    conexao["access_token"] = token.access_token
    conexao["access_token_expires_at"] = token.expires_at
    conexao["granted_scope"] = token.granted_scope
    if token.refresh_token:
        conexao["refresh_token"] = token.refresh_token


def _persistir_reautorizacao(conexao: dict, codigo: str):
    with abrir_conexao() as banco:
        with banco.cursor() as cursor:
            _validar_empresa_da_conexao(cursor, conexao)
            versao = _marcar_reautorizacao(cursor, conexao, codigo)

    if versao is None:
        raise HTTPException(
            status_code=409,
            detail="A conexão Google mudou durante a consulta. Atualize o painel.",
        )

    conexao["version"] = versao[0]


def _verificar_conexao_atual(conexao: dict):
    with abrir_conexao() as banco:
        with banco.cursor() as cursor:
            _validar_empresa_da_conexao(cursor, conexao)
            cursor.execute(
                """
                SELECT 1
                FROM google_business_connections
                WHERE id = %s
                  AND establishment_id = %s
                  AND dashboard_user_id = %s
                  AND version = %s
                """,
                (
                    conexao["id"],
                    conexao["establishment_id"],
                    conexao["dashboard_user_id"],
                    conexao["version"],
                ),
            )
            if cursor.fetchone() is None:
                raise HTTPException(
                    status_code=409,
                    detail="A conexão Google mudou durante a consulta. Atualize o painel.",
                )


def _obter_access_token(conexao: dict) -> str:
    if conexao["status"] != "connected":
        raise HTTPException(
            status_code=409,
            detail="Reconecte a conta Google para continuar",
        )

    access_token = conexao["access_token"]
    if not access_token:
        raise HTTPException(status_code=409, detail="Reconecte a conta Google")

    if conexao["access_token_expires_at"] > _agora_utc() + timedelta(seconds=60):
        return access_token

    refresh_token = conexao["refresh_token"]
    if not refresh_token:
        raise _ReautorizacaoNecessaria(
            "Reconecte a conta Google para continuar",
            "refresh_token_missing",
        )

    try:
        token = renovar_token(refresh_token, conexao["granted_scope"])
    except GoogleBusinessApiError as erro:
        if erro.status_code in (400, 401, 403):
            raise _ReautorizacaoNecessaria(
                "Reconecte a conta Google para continuar",
                "refresh_denied",
            ) from erro
        raise _erro_http_google(
            erro,
            "O Google está temporariamente indisponível",
        ) from erro

    _persistir_token_refrescado(conexao, token)
    return token.access_token


def _listar_locais_com_refresh(conexao: dict):
    access_token = _obter_access_token(conexao)

    try:
        return listar_locais(access_token)
    except GoogleBusinessApiError as erro:
        if erro.status_code != 401 or not conexao["refresh_token"]:
            if erro.status_code in (401, 403):
                raise _ReautorizacaoNecessaria(
                    "Reconecte a conta Google ou confirme a permissão Business Profile",
                    "google_denied",
                ) from erro
            raise _erro_http_google(
                erro,
                "Não foi possível consultar os locais no Google",
            ) from erro

        try:
            token = renovar_token(
                conexao["refresh_token"],
                conexao["granted_scope"],
            )
        except GoogleBusinessApiError as refresh_erro:
            if refresh_erro.status_code in (400, 401, 403):
                raise _ReautorizacaoNecessaria(
                    "Reconecte a conta Google para continuar",
                    "refresh_denied",
                ) from refresh_erro
            raise _erro_http_google(
                refresh_erro,
                "O Google está temporariamente indisponível",
            ) from refresh_erro

        _persistir_token_refrescado(conexao, token)
        try:
            return listar_locais(token.access_token)
        except GoogleBusinessApiError as retry_erro:
            if retry_erro.status_code in (401, 403):
                raise _ReautorizacaoNecessaria(
                    "Reconecte a conta Google ou confirme a permissão Business Profile",
                    "google_denied",
                ) from retry_erro
            raise _erro_http_google(
                retry_erro,
                "Não foi possível consultar os locais no Google",
            ) from retry_erro


@router.get("/dashboard/google-business/connect")
def iniciar_conexao_google(usuario: dict = Depends(obter_usuario_atual)):
    """Cria state vinculado ao tenant e à sessão Supabase que inicia o OAuth."""

    state = gerar_state()
    try:
        authorization_url = criar_url_autorizacao(state)
    except GoogleBusinessConfigurationError as erro:
        raise HTTPException(status_code=503, detail=str(erro)) from erro

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            session_id = _exigir_sessao_supabase_ativa(cursor, usuario)
            establishment_id = _bloquear_empresa_cliente(cursor, usuario["id"])
            cursor.execute(
                """
                DELETE FROM google_business_oauth_states
                WHERE expires_at <= clock_timestamp()
                """
            )
            cursor.execute(
                """
                SELECT version
                FROM google_business_connections
                WHERE establishment_id = %s
                FOR UPDATE
                """,
                (establishment_id,),
            )
            conexao_anterior = cursor.fetchone()
            versao_inicial = conexao_anterior[0] if conexao_anterior else None
            cursor.execute(
                """
                UPDATE google_business_oauth_states
                SET cancelled_at = clock_timestamp(),
                    authorization_code_encrypted = NULL
                WHERE establishment_id = %s
                  AND cancelled_at IS NULL
                  AND expires_at > clock_timestamp()
                """,
                (establishment_id,),
            )
            cursor.execute(
                """
                INSERT INTO google_business_oauth_states (
                    state_hash,
                    dashboard_user_id,
                    establishment_id,
                    session_id_hash,
                    expires_at,
                    connection_version_at_start,
                    concurrency_guarded
                )
                VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                """,
                (
                    hash_state(state),
                    usuario["id"],
                    establishment_id,
                    hash_session_id(session_id),
                    prazo_state(),
                    versao_inicial,
                ),
            )

    return {"authorization_url": authorization_url}


@router.get("/integrations/google-business/callback")
def callback_google_business(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    """Valida o retorno Google e guarda temporariamente o código cifrado.

    A troca do código e a persistência dos tokens só ocorrem no endpoint
    autenticado de conclusão, após confirmar a sessão Supabase iniciadora.
    """

    if not state:
        raise HTTPException(status_code=400, detail="State OAuth ausente")

    if error:
        with abrir_conexao() as conexao:
            with conexao.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT s.id
                    FROM google_business_oauth_states s
                    JOIN dashboard_accounts da
                        ON da.user_id = s.dashboard_user_id
                       AND da.establishment_id = s.establishment_id
                       AND da.role = 'client'
                    JOIN establishments e ON e.id = s.establishment_id
                    WHERE s.state_hash = %s
                      AND s.consumed_at IS NULL
                      AND s.cancelled_at IS NULL
                      AND s.concurrency_guarded
                      AND s.expires_at > clock_timestamp()
                      AND e.archived_at IS NULL
                    FOR UPDATE OF s
                    """,
                    (hash_state(state),),
                )
                state_data = cursor.fetchone()
                if state_data is None:
                    raise HTTPException(
                        status_code=400,
                        detail="State OAuth inválido, expirado ou reutilizado",
                    )
                cursor.execute(
                    """
                    UPDATE google_business_oauth_states
                    SET consumed_at = clock_timestamp()
                    WHERE id = %s
                      AND consumed_at IS NULL
                      AND expires_at > clock_timestamp()
                    """,
                    (state_data[0],),
                )
        return _redirecionar_frontend("authorization_denied")

    if not code:
        raise HTTPException(status_code=400, detail="Código OAuth ausente")

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            cursor.execute(
                """
                SELECT s.id
                FROM google_business_oauth_states s
                JOIN dashboard_accounts da
                    ON da.user_id = s.dashboard_user_id
                   AND da.establishment_id = s.establishment_id
                   AND da.role = 'client'
                JOIN establishments e ON e.id = s.establishment_id
                WHERE s.state_hash = %s
                  AND s.consumed_at IS NULL
                  AND s.cancelled_at IS NULL
                  AND s.concurrency_guarded
                  AND s.expires_at > clock_timestamp()
                  AND s.authorization_code_encrypted IS NULL
                  AND e.archived_at IS NULL
                FOR UPDATE OF s
                """,
                (hash_state(state),),
            )
            state_data = cursor.fetchone()

            if state_data is None:
                raise HTTPException(
                    status_code=400,
                    detail="State OAuth inválido, expirado ou reutilizado",
                )

            codigo_cifrado = cifrar_token(code)
            cursor.execute(
                """
                UPDATE google_business_oauth_states
                SET authorization_code_encrypted = %s,
                    callback_received_at = clock_timestamp()
                WHERE id = %s
                  AND consumed_at IS NULL
                  AND cancelled_at IS NULL
                  AND concurrency_guarded
                  AND expires_at > clock_timestamp()
                  AND authorization_code_encrypted IS NULL
                """,
                (codigo_cifrado, state_data[0]),
            )
            if cursor.rowcount != 1:
                raise HTTPException(
                    status_code=400,
                    detail="State OAuth expirado ou reutilizado",
                )

    return _redirecionar_frontend("complete", state=state)


@router.post("/dashboard/google-business/callback/complete")
def concluir_callback_google_business(
    dados: ConcluirOAuthRequest,
    usuario: dict = Depends(obter_usuario_atual),
):
    """Troca o código somente na sessão autenticada que iniciou o fluxo."""

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            session_id = _exigir_sessao_supabase_ativa(cursor, usuario)
            establishment_id = _bloquear_empresa_cliente(cursor, usuario["id"])
            cursor.execute(
                """
                SELECT id, dashboard_user_id, establishment_id,
                       session_id_hash, authorization_code_encrypted,
                       connection_version_at_start
                FROM google_business_oauth_states
                WHERE state_hash = %s
                  AND consumed_at IS NULL
                  AND cancelled_at IS NULL
                  AND concurrency_guarded
                  AND expires_at > clock_timestamp()
                  AND authorization_code_encrypted IS NOT NULL
                FOR UPDATE
                """,
                (hash_state(dados.state),),
            )
            state_data = cursor.fetchone()
            if state_data is None:
                raise HTTPException(
                    status_code=400,
                    detail="State OAuth inválido, expirado ou reutilizado",
                )

            (
                state_id,
                state_user_id,
                state_establishment_id,
                session_hash,
                code_encrypted,
                versao_inicial,
            ) = state_data
            if (
                str(state_user_id) != str(usuario["id"])
                or state_establishment_id != establishment_id
                or session_hash != hash_session_id(session_id)
            ):
                raise HTTPException(
                    status_code=400,
                    detail="State OAuth inválido, expirado ou de outra sessão",
                )

            try:
                code = decifrar_token(code_encrypted)
            except (GoogleBusinessConfigurationError, GoogleBusinessTokenError) as erro:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Não foi possível recuperar a autorização Google; "
                        "reinicie a conexão"
                    ),
                ) from erro

            cursor.execute(
                """
                UPDATE google_business_oauth_states
                SET consumed_at = clock_timestamp(),
                    authorization_code_encrypted = NULL
                WHERE id = %s
                  AND consumed_at IS NULL
                  AND expires_at > clock_timestamp()
                """,
                (state_id,),
            )
            if cursor.rowcount != 1:
                raise HTTPException(
                    status_code=400,
                    detail="State OAuth expirado ou reutilizado",
                )

    try:
        token = trocar_codigo_por_token(code)
    except (GoogleBusinessApiError, GoogleBusinessConfigurationError) as erro:
        if isinstance(erro, GoogleBusinessApiError):
            raise _erro_http_google(
                erro,
                "Não foi possível concluir a autorização Google; reinicie a conexão",
            ) from erro
        raise HTTPException(
            status_code=502,
            detail="Não foi possível concluir a autorização Google; reinicie a conexão",
        ) from erro

    refresh_token_ausente = not token.refresh_token
    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            establishment_atual = _bloquear_empresa_cliente(cursor, usuario["id"])
            sessao_atual = _exigir_sessao_supabase_ativa(cursor, usuario)
            if sessao_atual != session_id:
                raise HTTPException(
                    status_code=401,
                    detail="A sessão atual expirou; entre novamente",
                )
            if establishment_atual != state_establishment_id:
                raise HTTPException(
                    status_code=403,
                    detail="A empresa não corresponde mais à autorização iniciada",
                )
            cursor.execute(
                """
                SELECT 1
                FROM google_business_oauth_states
                WHERE id = %s
                  AND cancelled_at IS NULL
                  AND concurrency_guarded
                FOR UPDATE
                """,
                (state_id,),
            )
            if cursor.fetchone() is None:
                raise HTTPException(
                    status_code=409,
                    detail="Esta autorização Google foi substituída ou cancelada; reinicie a conexão.",
                )
            if refresh_token_ausente:
                if versao_inicial is not None:
                    cursor.execute(
                        """
                        UPDATE google_business_connections
                        SET status = 'reauth_required',
                            last_error_code = 'refresh_token_missing_on_reconnect',
                            refresh_token_encrypted = NULL,
                            google_location_name = NULL,
                            google_location_title = NULL,
                            updated_at = clock_timestamp(),
                            version = %s
                        WHERE establishment_id = %s
                          AND version = %s
                        RETURNING version
                        """,
                        (uuid4(), establishment_atual, versao_inicial),
                    )
                    if cursor.fetchone() is None:
                        raise HTTPException(
                            status_code=409,
                            detail="A conexão Google mudou durante a autorização. Atualize o painel.",
                        )
            else:
                salvou = _salvar_conexao_oauth(
                    cursor,
                    establishment_atual,
                    usuario["id"],
                    token,
                    versao_inicial,
                )
                if not salvou:
                    raise HTTPException(
                        status_code=409,
                        detail="A conexão Google mudou durante a autorização. Atualize o painel.",
                    )

    if refresh_token_ausente:
        raise HTTPException(
            status_code=409,
            detail=(
                "O Google não retornou uma credencial renovável. "
                "A conexão não foi concluída; confirme a conta e tente novamente."
            ),
        )

    return {"conectado": True}


@router.get("/dashboard/google-business/status")
def estado_conexao_google(usuario: dict = Depends(obter_usuario_atual)):
    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            establishment_id = _obter_empresa_cliente(cursor, usuario["id"])
            connection = _buscar_conexao(cursor, establishment_id, usuario["id"])

    if connection is None:
        return {"conectado": False, "status": "not_connected", "local": None}

    return {
        "conectado": connection["status"] == "connected",
        "status": connection["status"],
        "local": (
            {
                "nome": connection["google_location_name"],
                "titulo": connection["google_location_title"],
            }
            if connection["google_location_name"]
            else None
        ),
    }


@router.get("/dashboard/google-business/locations")
def listar_locais_google(usuario: dict = Depends(obter_usuario_atual)):
    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            establishment_id = _obter_empresa_cliente(cursor, usuario["id"])
            connection = _buscar_conexao(cursor, establishment_id, usuario["id"])
    if connection is None:
        raise HTTPException(
            status_code=409,
            detail="Conecte uma conta Google antes de listar os locais",
        )

    try:
        locais = _listar_locais_com_refresh(connection)
    except _ReautorizacaoNecessaria as erro:
        _persistir_reautorizacao(connection, erro.codigo)
        raise HTTPException(
            status_code=409,
            detail=erro.detalhe,
        ) from erro

    _verificar_conexao_atual(connection)

    return {"locais": locais}


@router.post("/dashboard/google-business/location")
def selecionar_local_google(
    dados: SelecionarLocalRequest,
    usuario: dict = Depends(obter_usuario_atual),
):
    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            establishment_id = _obter_empresa_cliente(cursor, usuario["id"])
            connection = _buscar_conexao(cursor, establishment_id, usuario["id"])
    if connection is None:
        raise HTTPException(
            status_code=409,
            detail="Conecte uma conta Google",
        )

    try:
        locais = _listar_locais_com_refresh(connection)
    except _ReautorizacaoNecessaria as erro:
        _persistir_reautorizacao(connection, erro.codigo)
        raise HTTPException(
            status_code=409,
            detail=erro.detalhe,
        ) from erro

    local = next(
        (item for item in locais if item["nome"] == dados.nome),
        None,
    )
    if local is None:
        raise HTTPException(
            status_code=403,
            detail="Esse local não pertence à conta Google conectada",
        )

    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            establishment_atual = _obter_empresa_cliente(cursor, usuario["id"])
            if establishment_atual != establishment_id:
                raise HTTPException(
                    status_code=403,
                    detail="A empresa mudou durante a consulta Google",
                )
            cursor.execute(
                """
                UPDATE google_business_connections
                SET google_location_name = %s,
                    google_location_title = %s,
                    updated_at = clock_timestamp(),
                    version = %s
                WHERE id = %s
                  AND establishment_id = %s
                  AND dashboard_user_id = %s
                  AND version = %s
                """,
                (
                    local["nome"],
                    local["titulo"],
                    uuid4(),
                    connection["id"],
                    establishment_id,
                    usuario["id"],
                    connection["version"],
                ),
            )
            if cursor.rowcount != 1:
                raise HTTPException(
                    status_code=409,
                    detail="A conexão Google mudou durante a consulta. Atualize o painel.",
                )

    return {
        "local": {
            "nome": local["nome"],
            "titulo": local["titulo"],
        }
    }


@router.delete("/dashboard/google-business")
def desconectar_google(usuario: dict = Depends(obter_usuario_atual)):
    """Remove localmente apenas a conexão da empresa autenticada."""
    with abrir_conexao() as conexao:
        with conexao.cursor() as cursor:
            establishment_id = _bloquear_empresa_cliente(cursor, usuario["id"])
            cursor.execute(
                """
                UPDATE google_business_oauth_states
                SET cancelled_at = clock_timestamp(),
                    authorization_code_encrypted = NULL
                WHERE establishment_id = %s
                  AND cancelled_at IS NULL
                """,
                (establishment_id,),
            )
            cursor.execute(
                """
                DELETE FROM google_business_connections
                WHERE establishment_id = %s
                """,
                (establishment_id,),
            )

    return {"conectado": False}
