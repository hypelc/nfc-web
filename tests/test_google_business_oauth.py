import os
import threading
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
from unittest.mock import MagicMock, patch

import psycopg
from cryptography.fernet import Fernet
from fastapi import HTTPException

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
os.environ.setdefault(
    "DATABASE_URL",
    TEST_DATABASE_URL or "postgresql://test.invalid/test",
)

from app import google_business
from app.google_business import (
    GoogleBusinessApiError,
    GoogleBusinessTokenError,
    GoogleToken,
)
from app.routers.google_business import (
    ConcluirOAuthRequest,
    SelecionarLocalRequest,
    _buscar_conexao,
    _obter_access_token,
    _salvar_conexao_oauth,
    callback_google_business,
    concluir_callback_google_business,
    desconectar_google,
    estado_conexao_google,
    iniciar_conexao_google,
    listar_locais_google,
    selecionar_local_google,
)


TOKEN_KEY = Fernet.generate_key().decode("ascii")
CLIENT_A = "00000000-0000-0000-0000-000000000011"
CLIENT_B = "00000000-0000-0000-0000-000000000012"
SESSION_A = "00000000-0000-0000-0000-000000000021"
SESSION_A_SECOND_BROWSER = "00000000-0000-0000-0000-000000000022"
SESSION_B = "00000000-0000-0000-0000-000000000023"


class GoogleBusinessOAuthUnitTests(unittest.TestCase):
    def test_url_de_autorizacao_tem_escopo_offline_state_e_nao_expoe_secret(self):
        with patch.multiple(
            google_business,
            GOOGLE_BUSINESS_CLIENT_ID="client-id",
            GOOGLE_BUSINESS_CLIENT_SECRET="client-secret",
            GOOGLE_BUSINESS_REDIRECT_URI=(
                "https://nfc-web.onrender.com/integrations/google-business/callback"
            ),
            GOOGLE_BUSINESS_TOKEN_ENCRYPTION_KEY=TOKEN_KEY,
        ):
            url = google_business.criar_url_autorizacao("state-seguro")

        parametros = parse_qs(urlparse(url).query)
        self.assertEqual(parametros["state"], ["state-seguro"])
        self.assertEqual(parametros["access_type"], ["offline"])
        self.assertEqual(parametros["prompt"], ["consent"])
        self.assertEqual(
            parametros["scope"],
            ["https://www.googleapis.com/auth/business.manage"],
        )
        self.assertNotIn("client-secret", url)

    def test_refresh_aceita_resposta_sem_scope_e_preserva_scope_concedido(self):
        with (
            patch.multiple(
                google_business,
                GOOGLE_BUSINESS_CLIENT_ID="client-id",
                GOOGLE_BUSINESS_CLIENT_SECRET="client-secret",
                GOOGLE_BUSINESS_REDIRECT_URI="https://example.test/callback",
                GOOGLE_BUSINESS_TOKEN_ENCRYPTION_KEY=TOKEN_KEY,
            ),
            patch.object(
                google_business,
                "_requisitar_google",
                return_value={"access_token": "novo-token", "expires_in": 3600},
            ),
        ):
            token = google_business.renovar_token(
                "refresh-token",
                google_business.GOOGLE_BUSINESS_SCOPE,
            )

        self.assertEqual(token.access_token, "novo-token")
        self.assertEqual(token.refresh_token, "refresh-token")
        self.assertEqual(token.granted_scope, google_business.GOOGLE_BUSINESS_SCOPE)

    def test_listagem_de_locais_percorre_contas_e_paginas_sem_expor_token(self):
        respostas = iter(
            [
                {
                    "accounts": [{"name": "accounts/1"}],
                    "nextPageToken": "segunda-conta",
                },
                {
                    "locations": [
                        {"name": "locations/10", "title": "Loja A"}
                    ],
                    "nextPageToken": "segunda-pagina",
                },
                {
                    "locations": [
                        {
                            "name": "locations/11",
                            "title": "Loja B",
                            "storeCode": "B",
                        }
                    ]
                },
                {"accounts": [{"name": "accounts/2"}]},
                {
                    "locations": [
                        {"name": "locations/20", "title": "Loja C"}
                    ]
                },
            ]
        )

        with patch.object(
            google_business,
            "_requisitar_google",
            side_effect=lambda *args, **kwargs: next(respostas),
        ):
            locais = google_business.listar_locais("token-que-nao-deve-sair")

        self.assertEqual([local["nome"] for local in locais], [
            "locations/10",
            "locations/11",
            "locations/20",
        ])
        self.assertEqual(locais[1]["store_code"], "B")

    def test_listagem_usa_page_size_maximo_da_api_de_contas(self):
        chamadas = []

        def requisitar(_metodo, url, *, parametros, **_kwargs):
            chamadas.append((url, parametros.copy()))
            if url.endswith("/accounts"):
                return {"accounts": [{"name": "accounts/1"}]}
            return {"locations": []}

        with patch.object(
            google_business,
            "_requisitar_google",
            side_effect=requisitar,
        ):
            google_business.listar_locais("token-de-teste")

        self.assertEqual(len(chamadas), 2)
        self.assertEqual(chamadas[0][1]["pageSize"], 20)

    def test_autorizacao_sem_refresh_nao_persiste_access_token_isolado(self):
        cursor = MagicMock()
        token = GoogleToken(
            access_token="access-token-conta-b",
            refresh_token=None,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            granted_scope=google_business.GOOGLE_BUSINESS_SCOPE,
        )

        with patch.multiple(
            google_business,
            GOOGLE_BUSINESS_TOKEN_ENCRYPTION_KEY=TOKEN_KEY,
        ):
            with self.assertRaises(GoogleBusinessTokenError):
                _salvar_conexao_oauth(cursor, 1, CLIENT_A, token)

        cursor.execute.assert_not_called()

    def test_callback_sem_refresh_bloqueia_reconexao_sem_salvar_access_novo(self):
        abrir_conexao = MagicMock()
        conexao = abrir_conexao.return_value.__enter__.return_value
        cursor = conexao.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (
            9,
            CLIENT_A,
            1,
            google_business.hash_session_id(SESSION_A),
            "codigo-cifrado",
            uuid4(),
        )
        cursor.rowcount = 1
        token_sem_refresh = GoogleToken(
            access_token="access-token-conta-b",
            refresh_token=None,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            granted_scope=google_business.GOOGLE_BUSINESS_SCOPE,
        )

        with (
            patch("app.routers.google_business.abrir_conexao", abrir_conexao),
            patch(
                "app.routers.google_business._exigir_sessao_supabase_ativa",
                return_value=SESSION_A,
            ),
            patch(
                "app.routers.google_business._obter_empresa_cliente",
                return_value=1,
            ),
            patch(
                "app.routers.google_business.decifrar_token",
                return_value="codigo-google",
            ),
            patch(
                "app.routers.google_business.trocar_codigo_por_token",
                return_value=token_sem_refresh,
            ) as trocar,
        ):
            with self.assertRaises(HTTPException) as contexto:
                concluir_callback_google_business(
                    ConcluirOAuthRequest(state="state-de-teste"),
                    {"id": CLIENT_A, "session_id": SESSION_A},
                )

        self.assertEqual(contexto.exception.status_code, 409)
        trocar.assert_called_once_with("codigo-google")
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(
            any("UPDATE google_business_connections" in sql for sql in statements)
        )
        self.assertTrue(
            any("refresh_token_encrypted = NULL" in sql for sql in statements)
        )
        self.assertFalse(
            any("INSERT INTO google_business_connections" in sql for sql in statements)
        )

    def test_limite_429_nao_pede_reautorizacao_nem_tenta_refresh(self):
        conexao = {
            "id": 17,
            "status": "connected",
            "access_token": "access-token-atual",
            "refresh_token": "refresh-token-atual",
            "access_token_expires_at": datetime.now(timezone.utc)
            + timedelta(hours=1),
            "granted_scope": google_business.GOOGLE_BUSINESS_SCOPE,
        }

        with (
            patch("app.routers.google_business.abrir_conexao"),
            patch(
                "app.routers.google_business._obter_empresa_cliente",
                return_value=1,
            ),
            patch(
                "app.routers.google_business._buscar_conexao",
                return_value=conexao,
            ),
            patch(
                "app.routers.google_business.listar_locais",
                side_effect=GoogleBusinessApiError(429),
            ),
            patch("app.routers.google_business.renovar_token") as renovar,
        ):
            with self.assertRaises(HTTPException) as contexto:
                listar_locais_google({"id": CLIENT_A})

        self.assertEqual(contexto.exception.status_code, 429)
        self.assertIn("limite", contexto.exception.detail.lower())
        renovar.assert_not_called()

    def test_callback_sem_state_valido_recusa_antes_de_trocar_codigo(self):
        abrir_conexao = MagicMock()
        conexao = abrir_conexao.return_value.__enter__.return_value
        cursor = conexao.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        with patch(
            "app.routers.google_business.abrir_conexao",
            abrir_conexao,
        ):
            with self.assertRaises(HTTPException) as contexto:
                callback_google_business(code="codigo", state="inexistente")

        self.assertEqual(contexto.exception.status_code, 400)
        abrir_conexao.assert_called_once()

    def test_callback_sem_state_recusa_sem_acessar_o_banco(self):
        abrir_conexao = MagicMock()
        with patch(
            "app.routers.google_business.abrir_conexao",
            abrir_conexao,
        ):
            with self.assertRaises(HTTPException) as contexto:
                callback_google_business(code="codigo", state=None, error=None)

        self.assertEqual(contexto.exception.status_code, 400)
        abrir_conexao.assert_not_called()


@unittest.skipUnless(
    TEST_DATABASE_URL,
    "Defina TEST_DATABASE_URL para executar o contrato OAuth PostgreSQL descartável",
)
class GoogleBusinessOAuthPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = f"google_oauth_{uuid4().hex}"
        with psycopg.connect(TEST_DATABASE_URL) as conexao:
            conexao.execute("CREATE SCHEMA IF NOT EXISTS auth")
            conexao.execute(
                """
                CREATE TABLE IF NOT EXISTS auth.users (
                    id UUID PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS auth.sessions (
                    id UUID PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE
                );
                """
            )
            conexao.execute(
                """
                INSERT INTO auth.users (id)
                VALUES (%s), (%s)
                ON CONFLICT (id) DO NOTHING
                """,
                (CLIENT_A, CLIENT_B),
            )
            conexao.execute(
                """
                INSERT INTO auth.sessions (id, user_id)
                VALUES (%s, %s), (%s, %s), (%s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    SESSION_A,
                    CLIENT_A,
                    SESSION_A_SECOND_BROWSER,
                    CLIENT_A,
                    SESSION_B,
                    CLIENT_B,
                ),
            )
            conexao.execute(f"CREATE SCHEMA {cls.schema}")
            conexao.execute(f"SET search_path TO {cls.schema}")
            conexao.execute(
                """
                CREATE TABLE establishments (
                    id BIGINT PRIMARY KEY,
                    name TEXT NOT NULL,
                    archived_at TIMESTAMPTZ
                );
                CREATE TABLE dashboard_accounts (
                    user_id UUID PRIMARY KEY,
                    role TEXT NOT NULL,
                    establishment_id BIGINT REFERENCES establishments(id)
                );
                CREATE TABLE google_business_oauth_states (
                    id BIGSERIAL PRIMARY KEY,
                    state_hash TEXT NOT NULL UNIQUE,
                    dashboard_user_id UUID NOT NULL,
                    establishment_id BIGINT NOT NULL REFERENCES establishments(id),
                    session_id_hash TEXT NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL,
                    authorization_code_encrypted TEXT,
                    callback_received_at TIMESTAMPTZ,
                    consumed_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    connection_version_at_start UUID,
                    cancelled_at TIMESTAMPTZ,
                    concurrency_guarded BOOLEAN NOT NULL DEFAULT FALSE
                );
                CREATE TABLE google_business_connections (
                    id BIGSERIAL PRIMARY KEY,
                    establishment_id BIGINT NOT NULL UNIQUE REFERENCES establishments(id),
                    dashboard_user_id UUID NOT NULL,
                    access_token_encrypted TEXT NOT NULL,
                    refresh_token_encrypted TEXT,
                    access_token_expires_at TIMESTAMPTZ NOT NULL,
                    granted_scope TEXT NOT NULL,
                    google_location_name TEXT,
                    google_location_title TEXT,
                    status TEXT NOT NULL,
                    last_error_code TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    version UUID NOT NULL DEFAULT gen_random_uuid()
                );
                """
            )

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(TEST_DATABASE_URL) as conexao:
            conexao.execute(f"DROP SCHEMA {cls.schema} CASCADE")
            conexao.execute(
                "DELETE FROM auth.users WHERE id IN (%s, %s)",
                (CLIENT_A, CLIENT_B),
            )

    @classmethod
    def abrir_conexao_teste(cls):
        conexao = psycopg.connect(TEST_DATABASE_URL)
        conexao.execute(f"SET search_path TO {cls.schema}")
        return conexao

    def setUp(self):
        with self.abrir_conexao_teste() as conexao:
            conexao.execute(
                "TRUNCATE google_business_connections, google_business_oauth_states, "
                "dashboard_accounts, establishments RESTART IDENTITY CASCADE"
            )
            conexao.execute(
                """
                INSERT INTO establishments (id, name, archived_at)
                VALUES (1, 'Empresa A', NULL), (2, 'Empresa B', NULL)
                """
            )
            conexao.execute(
                """
                INSERT INTO dashboard_accounts (user_id, role, establishment_id)
                VALUES (%s, 'client', 1), (%s, 'client', 2)
                """,
                (CLIENT_A, CLIENT_B),
            )
        self.database_patcher = patch(
            "app.routers.google_business.abrir_conexao",
            side_effect=self.abrir_conexao_teste,
        )
        self.database_patcher.start()
        self.addCleanup(self.database_patcher.stop)

    def config_oauth(self):
        return patch.multiple(
            google_business,
            GOOGLE_BUSINESS_CLIENT_ID="client-id",
            GOOGLE_BUSINESS_CLIENT_SECRET="client-secret",
            GOOGLE_BUSINESS_REDIRECT_URI=(
                "https://nfc-web.onrender.com/integrations/google-business/callback"
            ),
            GOOGLE_BUSINESS_TOKEN_ENCRYPTION_KEY=TOKEN_KEY,
        )

    def criar_state_para_cliente_a(self, session_id=SESSION_A):
        with self.config_oauth():
            resposta = iniciar_conexao_google(
                {"id": CLIENT_A, "session_id": session_id}
            )
        return parse_qs(urlparse(resposta["authorization_url"]).query)["state"][0]

    def test_inicio_vincula_state_a_empresa_do_usuario_sem_aceitar_id_do_browser(self):
        state = self.criar_state_para_cliente_a()

        with self.abrir_conexao_teste() as conexao:
            registro = conexao.execute(
                """
                SELECT state_hash, dashboard_user_id, establishment_id,
                       session_id_hash, consumed_at
                FROM google_business_oauth_states
                """
            ).fetchone()

        self.assertEqual(registro[0], google_business.hash_state(state))
        self.assertNotEqual(registro[0], state)
        self.assertEqual(str(registro[1]), CLIENT_A)
        self.assertEqual(registro[2], 1)
        self.assertEqual(registro[3], google_business.hash_session_id(SESSION_A))
        self.assertIsNone(registro[4])

    def test_inicio_registra_a_versao_da_conexao_existente(self):
        self.inserir_conexao_teste(
            access_token_expires_at=datetime.now(timezone.utc)
            + timedelta(hours=1),
        )
        with self.abrir_conexao_teste() as conexao:
            versao_conexao = conexao.execute(
                "SELECT version FROM google_business_connections WHERE establishment_id = 1"
            ).fetchone()[0]

        self.criar_state_para_cliente_a()

        with self.abrir_conexao_teste() as conexao:
            versao_inicial = conexao.execute(
                "SELECT connection_version_at_start FROM google_business_oauth_states"
            ).fetchone()[0]

        self.assertEqual(versao_inicial, versao_conexao)

    def receber_callback_google(self, state, code="codigo"):
        with (
            self.config_oauth(),
            patch(
                "app.routers.google_business.GOOGLE_BUSINESS_FRONTEND_URL",
                "https://frontend.example",
            ),
        ):
            return callback_google_business(code=code, state=state, error=None)

    def concluir_como_cliente_a(self, state, session_id=SESSION_A):
        return concluir_callback_google_business(
            ConcluirOAuthRequest(state=state),
            {"id": CLIENT_A, "session_id": session_id},
        )

    def token_de_teste(self):
        return GoogleToken(
            access_token="access-token-real",
            refresh_token="refresh-token-real",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            granted_scope=google_business.GOOGLE_BUSINESS_SCOPE,
        )

    def inserir_conexao_teste(
        self,
        *,
        access_token_expires_at,
        refresh_token="refresh-token-real",
        google_location_name=None,
        google_location_title=None,
    ):
        with self.config_oauth():
            with self.abrir_conexao_teste() as conexao:
                conexao.execute(
                    """
                    INSERT INTO google_business_connections (
                        establishment_id, dashboard_user_id,
                        access_token_encrypted, refresh_token_encrypted,
                        access_token_expires_at, granted_scope,
                        google_location_name, google_location_title, status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'connected')
                    """,
                    (
                        1,
                        CLIENT_A,
                        google_business.cifrar_token("access-token-antigo"),
                        google_business.cifrar_token(refresh_token),
                        access_token_expires_at,
                        google_business.GOOGLE_BUSINESS_SCOPE,
                        google_location_name,
                        google_location_title,
                    ),
                )

    def inserir_conexoes_da_mesma_conta_google(self):
        expira_em = datetime.now(timezone.utc) + timedelta(hours=1)
        with self.config_oauth(), self.abrir_conexao_teste() as conexao:
            access = google_business.cifrar_token("token-mesma-conta")
            refresh = google_business.cifrar_token("refresh-mesma-conta")
            for establishment_id, usuario_id, local in (
                (1, CLIENT_A, "locations/empresa-a"),
                (2, CLIENT_B, "locations/empresa-b"),
            ):
                conexao.execute(
                    """
                    INSERT INTO google_business_connections (
                        establishment_id, dashboard_user_id,
                        access_token_encrypted, refresh_token_encrypted,
                        access_token_expires_at, granted_scope,
                        google_location_name, google_location_title, status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'connected')
                    """,
                    (
                        establishment_id,
                        usuario_id,
                        access,
                        refresh,
                        expira_em,
                        google_business.GOOGLE_BUSINESS_SCOPE,
                        local,
                        f"Empresa {establishment_id}",
                    ),
                )
            return conexao.execute(
                """
                SELECT dashboard_user_id, access_token_encrypted,
                       refresh_token_encrypted, access_token_expires_at,
                       granted_scope, google_location_name, google_location_title,
                       status, last_error_code, version
                FROM google_business_connections WHERE establishment_id = 2
                """
            ).fetchone()

    def test_estado_reautorizacao_persiste_apos_resposta_de_erro(self):
        agora = datetime.now(timezone.utc)
        cenarios = (
            {
                "nome": "refresh ausente",
                "expira_em": agora - timedelta(minutes=1),
                "refresh_token": None,
                "erro_google": None,
                "erro_refresh": None,
                "codigo_esperado": "refresh_token_missing",
            },
            {
                "nome": "refresh negado",
                "expira_em": agora - timedelta(minutes=1),
                "refresh_token": "refresh-token-revogado",
                "erro_google": None,
                "erro_refresh": 400,
                "codigo_esperado": "refresh_denied",
            },
            {
                "nome": "Google retorna 401 sem refresh",
                "expira_em": agora + timedelta(hours=1),
                "refresh_token": None,
                "erro_google": 401,
                "erro_refresh": None,
                "codigo_esperado": "google_denied",
            },
            {
                "nome": "Google retorna 403",
                "expira_em": agora + timedelta(hours=1),
                "refresh_token": None,
                "erro_google": 403,
                "erro_refresh": None,
                "codigo_esperado": "google_denied",
            },
        )

        for cenario in cenarios:
            with self.subTest(cenario=cenario["nome"]):
                with self.abrir_conexao_teste() as conexao:
                    conexao.execute("TRUNCATE google_business_connections")
                self.inserir_conexao_teste(
                    access_token_expires_at=cenario["expira_em"],
                    refresh_token=cenario["refresh_token"],
                )

                erro_google = (
                    GoogleBusinessApiError(cenario["erro_google"])
                    if cenario["erro_google"] is not None
                    else None
                )
                erro_refresh = (
                    GoogleBusinessApiError(cenario["erro_refresh"])
                    if cenario["erro_refresh"] is not None
                    else None
                )
                with (
                    self.config_oauth(),
                    patch(
                        "app.routers.google_business.listar_locais",
                        side_effect=erro_google,
                    ),
                    patch(
                        "app.routers.google_business.renovar_token",
                        side_effect=erro_refresh,
                    ),
                ):
                    with self.assertRaises(HTTPException) as contexto:
                        listar_locais_google({"id": CLIENT_A})

                self.assertEqual(contexto.exception.status_code, 409)
                with self.abrir_conexao_teste() as conexao:
                    estado = conexao.execute(
                        """
                        SELECT status, last_error_code
                        FROM google_business_connections
                        WHERE establishment_id = 1
                        """
                    ).fetchone()
                self.assertEqual(
                    estado,
                    ("reauth_required", cenario["codigo_esperado"]),
                )

    def test_selecao_de_local_persiste_reautorizacao_apos_erro(self):
        with self.config_oauth():
            self.inserir_conexao_teste(
                access_token_expires_at=datetime.now(timezone.utc)
                - timedelta(minutes=1),
                refresh_token=None,
            )

            with self.assertRaises(HTTPException) as contexto:
                selecionar_local_google(
                    SelecionarLocalRequest(nome="accounts/1/locations/10"),
                    {"id": CLIENT_A},
                )

        self.assertEqual(contexto.exception.status_code, 409)
        with self.abrir_conexao_teste() as conexao:
            estado = conexao.execute(
                """
                SELECT status, last_error_code
                FROM google_business_connections
                WHERE establishment_id = 1
                """
            ).fetchone()
        self.assertEqual(estado, ("reauth_required", "refresh_token_missing"))

    def test_callback_so_conclui_na_sessao_iniciadora_e_cifra_tokens(self):
        state = self.criar_state_para_cliente_a()
        resposta = self.receber_callback_google(state)

        self.assertEqual(resposta.status_code, 303)
        local_de_retorno_esperado = (
            "https://frontend.example/dashboard#google_business=complete&"
            "google_business_state="
            + state
        )
        self.assertEqual(
            resposta.headers["location"],
            local_de_retorno_esperado,
        )
        self.assertNotIn("codigo", resposta.headers["location"])
        self.assertNotIn("access-token-real", resposta.headers["location"])
        self.assertNotIn("refresh-token-real", resposta.headers["location"])

        with self.abrir_conexao_teste() as conexao:
            pendente = conexao.execute(
                """
                SELECT authorization_code_encrypted, consumed_at
                FROM google_business_oauth_states
                """
            ).fetchone()
            self.assertNotEqual(pendente[0], "codigo")
            self.assertIsNone(pendente[1])
            self.assertIsNone(
                conexao.execute(
                    "SELECT 1 FROM google_business_connections"
                ).fetchone()
            )

        token = self.token_de_teste()
        with (
            self.config_oauth(),
            patch(
                "app.routers.google_business.trocar_codigo_por_token",
                return_value=token,
            ) as trocar,
        ):
            resultado = self.concluir_como_cliente_a(state)

        self.assertEqual(resultado, {"conectado": True})
        trocar.assert_called_once_with("codigo")
        with self.abrir_conexao_teste() as conexao:
            registro = conexao.execute(
                """
                SELECT access_token_encrypted, refresh_token_encrypted,
                       dashboard_user_id, establishment_id, status
                FROM google_business_connections
                """
            ).fetchone()
            estado = conexao.execute(
                """
                SELECT consumed_at, authorization_code_encrypted
                FROM google_business_oauth_states
                """
            ).fetchone()

        self.assertNotEqual(registro[0], "access-token-real")
        self.assertNotEqual(registro[1], "refresh-token-real")
        self.assertNotIn("access-token-real", registro[0])
        self.assertNotIn("refresh-token-real", registro[1])
        self.assertEqual(str(registro[2]), CLIENT_A)
        self.assertEqual(registro[3], 1)
        self.assertEqual(registro[4], "connected")
        self.assertIsNotNone(estado[0])
        self.assertIsNone(estado[1])

    def test_reconexao_limpa_local_google_selecionado_anteriormente(self):
        self.inserir_conexao_teste(
            access_token_expires_at=datetime.now(timezone.utc)
            + timedelta(hours=1),
            google_location_name="accounts/antiga/locations/10",
            google_location_title="Loja antiga",
        )
        state = self.criar_state_para_cliente_a()
        self.receber_callback_google(state)

        with (
            self.config_oauth(),
            patch(
                "app.routers.google_business.trocar_codigo_por_token",
                return_value=self.token_de_teste(),
            ),
        ):
            self.concluir_como_cliente_a(state)

        with self.abrir_conexao_teste() as conexao:
            local = conexao.execute(
                """
                SELECT google_location_name, google_location_title
                FROM google_business_connections
                WHERE establishment_id = 1
                """
            ).fetchone()

        self.assertEqual(local, (None, None))

    def test_reconexao_sem_refresh_nao_mistura_credenciais_de_contas(self):
        self.inserir_conexao_teste(
            access_token_expires_at=datetime.now(timezone.utc)
            + timedelta(hours=1),
            refresh_token="refresh-token-conta-a",
            google_location_name="accounts/antiga/locations/10",
            google_location_title="Loja antiga",
        )
        state = self.criar_state_para_cliente_a()
        self.receber_callback_google(state)
        token_sem_refresh = GoogleToken(
            access_token="access-token-conta-b",
            refresh_token=None,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            granted_scope=google_business.GOOGLE_BUSINESS_SCOPE,
        )

        with (
            self.config_oauth(),
            patch(
                "app.routers.google_business.trocar_codigo_por_token",
                return_value=token_sem_refresh,
            ),
        ):
            with self.assertRaises(HTTPException) as contexto:
                self.concluir_como_cliente_a(state)

        self.assertEqual(contexto.exception.status_code, 409)
        with self.config_oauth():
            with self.abrir_conexao_teste() as conexao:
                registro = conexao.execute(
                    """
                    SELECT status, access_token_encrypted, refresh_token_encrypted,
                           google_location_name, google_location_title
                    FROM google_business_connections
                    WHERE establishment_id = 1
                    """
                ).fetchone()
                access_token = google_business.decifrar_token(registro[1])
                refresh_token = google_business.decifrar_token(registro[2])

        self.assertEqual(registro[0], "reauth_required")
        self.assertEqual(access_token, "access-token-antigo")
        self.assertIsNone(refresh_token)
        self.assertEqual(registro[3:], (None, None))

        with (
            self.config_oauth(),
            patch("app.routers.google_business.listar_locais") as listar,
            patch("app.routers.google_business.renovar_token") as renovar,
        ):
            with self.assertRaises(HTTPException) as consulta:
                listar_locais_google({"id": CLIENT_A})

        self.assertEqual(consulta.exception.status_code, 409)
        listar.assert_not_called()
        renovar.assert_not_called()

    def test_callback_sem_refresh_atrasado_nao_invalida_reconexao_mais_nova(self):
        self.inserir_conexao_teste(
            access_token_expires_at=datetime.now(timezone.utc)
            + timedelta(hours=1),
        )
        state = self.criar_state_para_cliente_a()
        self.receber_callback_google(state, code="codigo-atrasado")
        token_sem_refresh = GoogleToken(
            access_token="access-token-atrasado",
            refresh_token=None,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            granted_scope=google_business.GOOGLE_BUSINESS_SCOPE,
        )
        with self.abrir_conexao_teste() as conexao:
            conexao.execute(
                "UPDATE google_business_connections SET version = gen_random_uuid() "
                "WHERE establishment_id = 1"
            )

        with (
            self.config_oauth(),
            patch(
                "app.routers.google_business.trocar_codigo_por_token",
                return_value=token_sem_refresh,
            ),
        ):
            with self.assertRaises(HTTPException) as contexto:
                self.concluir_como_cliente_a(state)

        self.assertEqual(contexto.exception.status_code, 409)
        with self.config_oauth(), self.abrir_conexao_teste() as conexao:
            registro = conexao.execute(
                """
                SELECT status, access_token_encrypted, refresh_token_encrypted,
                       version
                FROM google_business_connections WHERE establishment_id = 1
                """
            ).fetchone()
            access_token = google_business.decifrar_token(registro[1])
            refresh_token = google_business.decifrar_token(registro[2])

        self.assertEqual(registro[0], "connected")
        self.assertEqual(access_token, "access-token-antigo")
        self.assertEqual(refresh_token, "refresh-token-real")
        self.assertIsNotNone(registro[3])

    def test_callback_sem_refresh_anterior_a_reconexao_nao_impede_sucesso_posterior(self):
        self.inserir_conexao_teste(
            access_token_expires_at=datetime.now(timezone.utc)
            + timedelta(hours=1),
        )
        state_sem_refresh = self.criar_state_para_cliente_a()
        self.receber_callback_google(state_sem_refresh, code="codigo-sem-refresh")
        token_sem_refresh = GoogleToken(
            access_token="access-token-atrasado",
            refresh_token=None,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            granted_scope=google_business.GOOGLE_BUSINESS_SCOPE,
        )
        with (
            self.config_oauth(),
            patch(
                "app.routers.google_business.trocar_codigo_por_token",
                return_value=token_sem_refresh,
            ),
        ):
            with self.assertRaises(HTTPException) as contexto:
                self.concluir_como_cliente_a(state_sem_refresh)
        self.assertEqual(contexto.exception.status_code, 409)

        state_reconexao = self.criar_state_para_cliente_a()
        self.receber_callback_google(state_reconexao, code="codigo-reconexao")
        token_novo = GoogleToken(
            access_token="access-token-conta-b",
            refresh_token="refresh-token-conta-b",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            granted_scope=google_business.GOOGLE_BUSINESS_SCOPE,
        )
        with self.config_oauth(), patch(
            "app.routers.google_business.trocar_codigo_por_token",
            return_value=token_novo,
        ):
            self.concluir_como_cliente_a(state_reconexao)

        with self.config_oauth(), self.abrir_conexao_teste() as conexao:
            registro = conexao.execute(
                """
                SELECT status, access_token_encrypted, refresh_token_encrypted
                FROM google_business_connections WHERE establishment_id = 1
                """
            ).fetchone()
            access_token = google_business.decifrar_token(registro[1])
            refresh_token = google_business.decifrar_token(registro[2])

        self.assertEqual(registro[0], "connected")
        self.assertEqual(access_token, "access-token-conta-b")
        self.assertEqual(refresh_token, "refresh-token-conta-b")

    def test_ultima_autorizacao_iniciada_vence_em_ambas_as_ordens_de_callback(self):
        for concluir_primeiro in ("anterior", "mais_recente"):
            with self.subTest(primeiro=concluir_primeiro):
                state_anterior = self.criar_state_para_cliente_a()
                self.receber_callback_google(state_anterior, code="codigo-anterior")
                state_recente = self.criar_state_para_cliente_a()
                self.receber_callback_google(state_recente, code="codigo-recente")
                token_anterior = GoogleToken(
                    access_token="access-antigo",
                    refresh_token="refresh-antigo",
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                    granted_scope=google_business.GOOGLE_BUSINESS_SCOPE,
                )
                token_recente = GoogleToken(
                    access_token="access-recente",
                    refresh_token="refresh-recente",
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                    granted_scope=google_business.GOOGLE_BUSINESS_SCOPE,
                )
                callback_anterior = self.concluir_como_cliente_a
                if concluir_primeiro == "anterior":
                    with self.config_oauth(), patch(
                        "app.routers.google_business.trocar_codigo_por_token",
                        side_effect=lambda codigo: token_anterior
                        if codigo == "codigo-anterior"
                        else token_recente,
                    ) as trocar:
                        with self.assertRaises(HTTPException) as contexto:
                            callback_anterior(state_anterior)
                        self.assertEqual(contexto.exception.status_code, 400)
                        trocar.assert_not_called()
                        callback_anterior(state_recente)
                else:
                    with self.config_oauth(), patch(
                        "app.routers.google_business.trocar_codigo_por_token",
                        side_effect=lambda codigo: token_anterior
                        if codigo == "codigo-anterior"
                        else token_recente,
                    ) as trocar:
                        callback_anterior(state_recente)
                        with self.assertRaises(HTTPException) as contexto:
                            callback_anterior(state_anterior)
                        self.assertEqual(contexto.exception.status_code, 400)
                        self.assertEqual(trocar.call_count, 1)

                with self.config_oauth(), self.abrir_conexao_teste() as conexao:
                    registro = conexao.execute(
                        """
                        SELECT access_token_encrypted, refresh_token_encrypted
                        FROM google_business_connections WHERE establishment_id = 1
                        """
                    ).fetchone()
                    access_token = google_business.decifrar_token(registro[0])
                    refresh_token = google_business.decifrar_token(registro[1])
                self.assertEqual(access_token, "access-recente")
                self.assertEqual(refresh_token, "refresh-recente")

    def test_desconexao_local_isola_empresa_e_invalida_callback_pendente(self):
        state = self.criar_state_para_cliente_a()
        self.receber_callback_google(state)

        # Os dois vínculos representam a mesma conta Google em empresas
        # distintas; a desconexão deve afetar somente a linha da Empresa A.
        conexao_b_antes = self.inserir_conexoes_da_mesma_conta_google()

        with self.config_oauth(), patch.object(
            google_business, "_requisitar_google", return_value={}
        ) as requisitar_google:
            resposta = desconectar_google({"id": CLIENT_A})

        self.assertEqual(resposta, {"conectado": False})
        requisitar_google.assert_not_called()

        with self.abrir_conexao_teste() as conexao:
            self.assertIsNone(
                conexao.execute(
                    "SELECT 1 FROM google_business_connections WHERE establishment_id = 1"
                ).fetchone()
            )
            conexao_b_depois = conexao.execute(
                """
                SELECT dashboard_user_id, access_token_encrypted,
                       refresh_token_encrypted, access_token_expires_at,
                       granted_scope, google_location_name, google_location_title,
                       status, last_error_code, version
                FROM google_business_connections WHERE establishment_id = 2
                """
            ).fetchone()
            state_depois = conexao.execute(
                """
                SELECT cancelled_at, authorization_code_encrypted
                FROM google_business_oauth_states
                WHERE state_hash = %s
                """,
                (google_business.hash_state(state),),
            ).fetchone()
        self.assertEqual(conexao_b_depois, conexao_b_antes)
        self.assertIsNotNone(state_depois[0])
        self.assertIsNone(state_depois[1])

        with patch("app.routers.google_business.trocar_codigo_por_token") as trocar:
            with self.assertRaises(HTTPException) as contexto:
                self.concluir_como_cliente_a(state)
        self.assertIn(contexto.exception.status_code, (400, 409))
        trocar.assert_not_called()
        with self.abrir_conexao_teste() as conexao:
            self.assertIsNone(
                conexao.execute(
                    "SELECT 1 FROM google_business_connections WHERE establishment_id = 1"
                ).fetchone()
            )
            conexao_b_depois_callback = conexao.execute(
                """
                SELECT dashboard_user_id, access_token_encrypted,
                       refresh_token_encrypted, access_token_expires_at,
                       granted_scope, google_location_name, google_location_title,
                       status, last_error_code, version
                FROM google_business_connections WHERE establishment_id = 2
                """
            ).fetchone()
        self.assertEqual(conexao_b_depois_callback, conexao_b_antes)

    def test_callback_em_troca_de_codigo_nao_recria_vinculo_apos_desconexao(self):
        state = self.criar_state_para_cliente_a()
        self.receber_callback_google(state)
        conexao_b_antes = self.inserir_conexoes_da_mesma_conta_google()
        troca_iniciada = threading.Event()
        liberar_troca = threading.Event()
        resultado = {}

        def trocar_codigo_bloqueado(_codigo):
            troca_iniciada.set()
            if not liberar_troca.wait(timeout=5):
                raise TimeoutError("a troca simulada do código não foi liberada")
            return GoogleToken(
                access_token="access-novo-empresa-a",
                refresh_token="refresh-novo-empresa-a",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                granted_scope=google_business.GOOGLE_BUSINESS_SCOPE,
            )

        def concluir_callback():
            try:
                resultado["resposta"] = self.concluir_como_cliente_a(state)
            except BaseException as erro:
                resultado["erro"] = erro

        thread = threading.Thread(target=concluir_callback)
        with (
            self.config_oauth(),
            patch("app.routers.google_business.trocar_codigo_por_token",
                  side_effect=trocar_codigo_bloqueado) as trocar,
            patch.object(google_business, "_requisitar_google") as requisitar_google,
        ):
            thread.start()
            try:
                self.assertTrue(troca_iniciada.wait(timeout=5))
                resposta = desconectar_google({"id": CLIENT_A})
            finally:
                liberar_troca.set()
                thread.join(timeout=5)

        self.assertFalse(thread.is_alive(), "o callback não terminou após liberar a troca")
        self.assertEqual(resposta, {"conectado": False})
        self.assertIsInstance(resultado.get("erro"), HTTPException)
        self.assertEqual(resultado["erro"].status_code, 409)
        trocar.assert_called_once_with("codigo")
        requisitar_google.assert_not_called()
        with self.abrir_conexao_teste() as conexao:
            self.assertIsNone(
                conexao.execute(
                    "SELECT 1 FROM google_business_connections WHERE establishment_id = 1"
                ).fetchone()
            )
            conexao_b_depois = conexao.execute(
                """
                SELECT dashboard_user_id, access_token_encrypted,
                       refresh_token_encrypted, access_token_expires_at,
                       granted_scope, google_location_name, google_location_title,
                       status, last_error_code, version
                FROM google_business_connections WHERE establishment_id = 2
                """
            ).fetchone()
            state_depois = conexao.execute(
                """
                SELECT cancelled_at, authorization_code_encrypted
                FROM google_business_oauth_states
                WHERE state_hash = %s
                """,
                (google_business.hash_state(state),),
            ).fetchone()
        self.assertEqual(conexao_b_depois, conexao_b_antes)
        self.assertIsNotNone(state_depois[0])
        self.assertIsNone(state_depois[1])

    def test_callback_com_baseline_ausente_nao_sobrescreve_conexao_criada_depois(self):
        state = self.criar_state_para_cliente_a()
        self.receber_callback_google(state)
        self.inserir_conexao_teste(
            access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        with self.config_oauth(), patch(
            "app.routers.google_business.trocar_codigo_por_token",
            return_value=self.token_de_teste(),
        ) as trocar:
            with self.assertRaises(HTTPException) as contexto:
                self.concluir_como_cliente_a(state)

        self.assertEqual(contexto.exception.status_code, 409)
        trocar.assert_called_once()
        with self.config_oauth(), self.abrir_conexao_teste() as conexao:
            registro = conexao.execute(
                "SELECT access_token_encrypted FROM google_business_connections "
                "WHERE establishment_id = 1"
            ).fetchone()
            token_preservado = google_business.decifrar_token(registro[0])
        self.assertEqual(token_preservado, "access-token-antigo")

    def test_estado_pendente_de_antes_da_migration_nao_e_concluivel(self):
        state = self.criar_state_para_cliente_a()
        self.receber_callback_google(state)
        with self.abrir_conexao_teste() as conexao:
            conexao.execute(
                "UPDATE google_business_oauth_states SET concurrency_guarded = FALSE"
            )

        with patch("app.routers.google_business.trocar_codigo_por_token") as trocar:
            with self.assertRaises(HTTPException) as contexto:
                self.concluir_como_cliente_a(state)
        self.assertEqual(contexto.exception.status_code, 400)
        trocar.assert_not_called()

    def test_segunda_sessao_do_mesmo_usuario_nao_conclui_callback(self):
        state = self.criar_state_para_cliente_a()
        self.receber_callback_google(state)

        with patch(
            "app.routers.google_business.trocar_codigo_por_token"
        ) as trocar:
            with self.assertRaises(HTTPException) as contexto:
                self.concluir_como_cliente_a(state, SESSION_A_SECOND_BROWSER)

        self.assertEqual(contexto.exception.status_code, 400)
        trocar.assert_not_called()
        with self.abrir_conexao_teste() as conexao:
            pendente = conexao.execute(
                """
                SELECT consumed_at, authorization_code_encrypted
                FROM google_business_oauth_states
                """
            ).fetchone()
            self.assertIsNone(
                conexao.execute(
                    "SELECT 1 FROM google_business_connections"
                ).fetchone()
            )
        self.assertIsNone(pendente[0])
        self.assertIsNotNone(pendente[1])

        with (
            self.config_oauth(),
            patch(
                "app.routers.google_business.trocar_codigo_por_token",
                return_value=self.token_de_teste(),
            ) as trocar,
        ):
            self.assertEqual(
                self.concluir_como_cliente_a(state),
                {"conectado": True},
            )
        trocar.assert_called_once_with("codigo")

    def test_callback_expirado_reutilizado_e_finalizacao_repetida_sao_negados(self):
        state_expirado = self.criar_state_para_cliente_a()
        with self.abrir_conexao_teste() as conexao:
            conexao.execute(
                """
                UPDATE google_business_oauth_states
                SET expires_at = NOW() - INTERVAL '1 minute'
                """
            )
        with self.assertRaises(HTTPException) as callback_expirado:
            self.receber_callback_google(state_expirado)
        self.assertEqual(callback_expirado.exception.status_code, 400)

        with self.abrir_conexao_teste() as conexao:
            conexao.execute("DELETE FROM google_business_oauth_states")
        state = self.criar_state_para_cliente_a()
        self.receber_callback_google(state)
        with self.assertRaises(HTTPException) as callback_reutilizado:
            self.receber_callback_google(state, code="outro-codigo")
        self.assertEqual(callback_reutilizado.exception.status_code, 400)

        with (
            self.config_oauth(),
            patch(
                "app.routers.google_business.trocar_codigo_por_token",
                return_value=self.token_de_teste(),
            ),
        ):
            self.assertEqual(
                self.concluir_como_cliente_a(state),
                {"conectado": True},
            )
            with self.assertRaises(HTTPException) as finalizacao_repetida:
                self.concluir_como_cliente_a(state)
        self.assertEqual(finalizacao_repetida.exception.status_code, 400)

    def test_empresa_b_nao_enxerga_conexao_da_empresa_a(self):
        state = self.criar_state_para_cliente_a()
        self.receber_callback_google(state)

        with patch(
            "app.routers.google_business.trocar_codigo_por_token"
        ) as trocar:
            with self.assertRaises(HTTPException) as contexto:
                concluir_callback_google_business(
                    ConcluirOAuthRequest(state=state),
                    {"id": CLIENT_B, "session_id": SESSION_B},
                )
        self.assertEqual(contexto.exception.status_code, 400)
        trocar.assert_not_called()

        with (
            self.config_oauth(),
            patch(
                "app.routers.google_business.trocar_codigo_por_token",
                return_value=self.token_de_teste(),
            ),
        ):
            self.concluir_como_cliente_a(state)

        resultado = estado_conexao_google({"id": CLIENT_B})
        self.assertEqual(resultado, {
            "conectado": False,
            "status": "not_connected",
            "local": None,
        })

        with self.assertRaises(HTTPException) as contexto:
            selecionar_local_google(
                SelecionarLocalRequest(nome="accounts/1/locations/10"),
                {"id": CLIENT_B},
            )
        self.assertEqual(contexto.exception.status_code, 409)

    def test_cliente_seleciona_local_obtido_da_conta_conectada(self):
        state = self.criar_state_para_cliente_a()
        self.receber_callback_google(state)
        with (
            self.config_oauth(),
            patch(
                "app.routers.google_business.trocar_codigo_por_token",
                return_value=self.token_de_teste(),
            ),
        ):
            self.concluir_como_cliente_a(state)

        with (
            self.config_oauth(),
            patch(
                "app.routers.google_business.listar_locais",
                return_value=[
                    {
                        "nome": "locations/10",
                        "titulo": "Loja A",
                        "store_code": None,
                    }
                ],
            ),
        ):
            resposta = selecionar_local_google(
                SelecionarLocalRequest(nome="locations/10"),
                {"id": CLIENT_A},
            )

        self.assertEqual(
            resposta,
            {"local": {"nome": "locations/10", "titulo": "Loja A"}},
        )
        with self.abrir_conexao_teste() as conexao:
            vinculo = conexao.execute(
                """
                SELECT establishment_id, dashboard_user_id,
                       google_location_name, google_location_title
                FROM google_business_connections
                """
            ).fetchone()
        self.assertEqual(
            (vinculo[0], str(vinculo[1]), vinculo[2], vinculo[3]),
            (1, CLIENT_A, "locations/10", "Loja A"),
        )

    def test_cliente_lista_e_seleciona_local_no_formato_real_locations_id(self):
        self.inserir_conexao_teste(
            access_token_expires_at=datetime.now(timezone.utc)
            + timedelta(hours=1),
        )
        chamadas_google = []

        def resposta_google(_metodo, url, **_kwargs):
            chamadas_google.append(url)
            if url.endswith("/accounts"):
                return {"accounts": [{"name": "accounts/1"}]}
            if url.endswith("/accounts/1/locations"):
                return {"locations": [{"name": "locations/10", "title": "Loja A"}]}
            raise AssertionError(f"Listagem fora da conta autorizada: {url}")

        with self.config_oauth(), patch.object(
            google_business,
            "_requisitar_google",
            side_effect=resposta_google,
        ):
            listagem = listar_locais_google({"id": CLIENT_A})
            selecao = selecionar_local_google(
                SelecionarLocalRequest(nome="locations/10"),
                {"id": CLIENT_A},
            )

        self.assertEqual(
            listagem["locais"],
            [{"nome": "locations/10", "titulo": "Loja A", "store_code": None}],
        )
        self.assertEqual(
            selecao,
            {"local": {"nome": "locations/10", "titulo": "Loja A"}},
        )
        self.assertEqual(
            [url.rsplit("/", 1)[-1] for url in chamadas_google],
            ["accounts", "locations", "accounts", "locations"],
        )
        self.assertTrue(all("/accounts/1/locations" in url for url in chamadas_google[1::2]))
        with self.abrir_conexao_teste() as conexao:
            vinculo = conexao.execute(
                "SELECT google_location_name, google_location_title "
                "FROM google_business_connections WHERE establishment_id = 1"
            ).fetchone()
        self.assertEqual(vinculo, ("locations/10", "Loja A"))

    def test_cliente_nao_pode_selecionar_local_ausente_da_conta_google_conectada(self):
        self.inserir_conexao_teste(
            access_token_expires_at=datetime.now(timezone.utc)
            + timedelta(hours=1),
        )

        def resposta_google(_metodo, url, **_kwargs):
            if url.endswith("/accounts"):
                return {"accounts": [{"name": "accounts/1"}]}
            if url.endswith("/accounts/1/locations"):
                return {"locations": [{"name": "locations/10", "title": "Loja A"}]}
            raise AssertionError(f"Listagem fora da conta autorizada: {url}")

        with self.config_oauth(), patch.object(
            google_business,
            "_requisitar_google",
            side_effect=resposta_google,
        ):
            with self.assertRaises(HTTPException) as contexto:
                selecionar_local_google(
                    SelecionarLocalRequest(nome="locations/999"),
                    {"id": CLIENT_A},
                )

        self.assertEqual(contexto.exception.status_code, 403)
        with self.abrir_conexao_teste() as conexao:
            vinculo = conexao.execute(
                "SELECT google_location_name, google_location_title "
                "FROM google_business_connections WHERE establishment_id = 1"
            ).fetchone()
        self.assertEqual(vinculo, (None, None))

    def test_token_expirado_e_renovado_no_backend(self):
        token_atual = GoogleToken(
            access_token="access-token-expirado",
            refresh_token="refresh-token-real",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            granted_scope=google_business.GOOGLE_BUSINESS_SCOPE,
        )
        token_novo = GoogleToken(
            access_token="access-token-novo",
            refresh_token="refresh-token-real",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            granted_scope=google_business.GOOGLE_BUSINESS_SCOPE,
        )

        with self.config_oauth():
            with self.abrir_conexao_teste() as conexao:
                conexao.execute(
                    """
                    INSERT INTO google_business_connections (
                        establishment_id, dashboard_user_id,
                        access_token_encrypted, refresh_token_encrypted,
                        access_token_expires_at, granted_scope, status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 'connected')
                    """,
                    (
                        1,
                        CLIENT_A,
                        google_business.cifrar_token(token_atual.access_token),
                        google_business.cifrar_token(token_atual.refresh_token),
                        token_atual.expires_at,
                        token_atual.granted_scope,
                    ),
                )
            with self.abrir_conexao_teste() as conexao:
                with conexao.cursor() as cursor:
                    conexao_data = _buscar_conexao(cursor, 1, CLIENT_A)
            with patch(
                "app.routers.google_business.renovar_token",
                return_value=token_novo,
            ) as renovar:
                acesso = _obter_access_token(conexao_data)

        self.assertEqual(acesso, "access-token-novo")
        renovar.assert_called_once_with(
            "refresh-token-real",
            google_business.GOOGLE_BUSINESS_SCOPE,
        )

    def test_listagem_google_nao_mantem_lock_durante_chamada_externa(self):
        self.inserir_conexao_teste(
            access_token_expires_at=datetime.now(timezone.utc)
            + timedelta(hours=1),
        )
        chamada_iniciada = threading.Event()
        liberar_chamada = threading.Event()
        resultado = {}

        def listar_bloqueado(_access_token):
            chamada_iniciada.set()
            if not liberar_chamada.wait(timeout=5):
                raise TimeoutError("a chamada simulada não foi liberada")
            return [{"nome": "locations/10", "titulo": "Loja A"}]

        def executar_listagem():
            try:
                resultado["resposta"] = listar_locais_google({"id": CLIENT_A})
            except BaseException as erro:
                resultado["erro"] = erro

        with self.config_oauth(), patch(
            "app.routers.google_business.listar_locais",
            side_effect=listar_bloqueado,
        ):
            thread = threading.Thread(target=executar_listagem)
            thread.start()
            try:
                self.assertTrue(chamada_iniciada.wait(timeout=5))
                with self.abrir_conexao_teste() as conexao:
                    with conexao.transaction():
                        conexao.execute("SET LOCAL lock_timeout = '500ms'")
                        conexao.execute(
                            "UPDATE google_business_connections "
                            "SET version = gen_random_uuid() WHERE establishment_id = 1"
                        )
            finally:
                liberar_chamada.set()
                thread.join(timeout=5)

        self.assertFalse(thread.is_alive(), "a consulta externa ficou bloqueada")
        self.assertIsInstance(resultado.get("erro"), HTTPException)
        self.assertEqual(resultado["erro"].status_code, 409)

    def test_listagem_nao_devolve_locais_se_empresa_do_usuario_mudar_no_meio(self):
        self.inserir_conexao_teste(
            access_token_expires_at=datetime.now(timezone.utc)
            + timedelta(hours=1),
        )

        def listar_apos_remapeamento(_access_token):
            with self.abrir_conexao_teste() as conexao:
                conexao.execute(
                    "UPDATE dashboard_accounts SET establishment_id = 2 "
                    "WHERE user_id = %s",
                    (CLIENT_A,),
                )
            return [{"nome": "locations/10", "titulo": "Loja A"}]

        with self.config_oauth(), patch(
            "app.routers.google_business.listar_locais",
            side_effect=listar_apos_remapeamento,
        ):
            with self.assertRaises(HTTPException) as contexto:
                listar_locais_google({"id": CLIENT_A})

        self.assertEqual(contexto.exception.status_code, 403)

    def test_desconexoes_concorrentes_sao_idempotentes_e_nao_chamam_google(self):
        self.inserir_conexao_teste(
            access_token_expires_at=datetime.now(timezone.utc)
            + timedelta(hours=1),
        )
        barreira = threading.Barrier(3)
        respostas = []
        erros = []

        def executar_desconexao():
            try:
                barreira.wait(timeout=5)
                respostas.append(desconectar_google({"id": CLIENT_A}))
            except BaseException as erro:
                erros.append(erro)

        threads = [threading.Thread(target=executar_desconexao) for _ in range(2)]
        with patch.object(google_business, "_requisitar_google") as requisitar_google:
            with self.abrir_conexao_teste() as conexao_bloqueadora:
                conexao_bloqueadora.execute(
                    "SELECT id FROM establishments WHERE id = 1 FOR UPDATE"
                )
                for thread in threads:
                    thread.start()
                barreira.wait(timeout=5)

            for thread in threads:
                thread.join(timeout=5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(erros, [])
        self.assertEqual(respostas, [{"conectado": False}, {"conectado": False}])
        requisitar_google.assert_not_called()
        with self.abrir_conexao_teste() as conexao:
            self.assertIsNone(
                conexao.execute(
                    "SELECT 1 FROM google_business_connections WHERE establishment_id = 1"
                ).fetchone()
            )
