import os
import socket
import subprocess
import sys
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

import psycopg


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


class NaoSeguirRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@unittest.skipUnless(
    TEST_DATABASE_URL,
    "Defina TEST_DATABASE_URL para executar o contrato HTTP PostgreSQL descartável",
)
class PublicRedirectHttpContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = f"redirect_http_{uuid4().hex}"
        with psycopg.connect(TEST_DATABASE_URL) as conexao:
            conexao.execute(f"CREATE SCHEMA {cls.schema}")
            conexao.execute(f"SET search_path TO {cls.schema}")
            conexao.execute(
                """
                CREATE TABLE establishments (
                    id BIGINT PRIMARY KEY,
                    archived_at TIMESTAMPTZ
                );
                CREATE TABLE qr_codes (
                    id BIGINT PRIMARY KEY,
                    establishment_id BIGINT NOT NULL REFERENCES establishments(id),
                    code TEXT NOT NULL UNIQUE,
                    destination_url TEXT NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE
                );
                CREATE TABLE access_events (
                    id BIGSERIAL PRIMARY KEY,
                    qr_code_id BIGINT NOT NULL REFERENCES qr_codes(id),
                    source TEXT NOT NULL,
                    accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            conexao.execute(
                """
                INSERT INTO establishments (id, archived_at)
                VALUES (1, NULL), (2, NOW())
                """
            )
            conexao.execute(
                """
                INSERT INTO qr_codes
                    (id, establishment_id, code, destination_url, is_active)
                VALUES
                    (10, 1, 'CODIGO_TESTE', 'https://example.com/avaliar', TRUE),
                    (11, 1, 'CODIGO_INATIVO', 'https://example.com/inativo', FALSE),
                    (20, 2, 'CODIGO_ARQUIVADO', 'https://example.com/arquivado', TRUE)
                """
            )

        cls.port = cls.obter_porta_livre()
        ambiente = os.environ.copy()
        ambiente["DATABASE_URL"] = cls.url_com_search_path()
        ambiente["SUPABASE_URL"] = ""
        ambiente["SUPABASE_PUBLISHABLE_KEY"] = ""
        cls.processo = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(cls.port),
            ],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            env=ambiente,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        cls.opener = build_opener(NaoSeguirRedirects())
        cls.aguardar_aplicacao()

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "processo", None) is not None:
            cls.processo.terminate()
            try:
                cls.processo.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.processo.kill()
                cls.processo.wait(timeout=5)
            cls.processo.stdout.close()
            cls.processo.stderr.close()

        with psycopg.connect(TEST_DATABASE_URL) as conexao:
            conexao.execute(f"DROP SCHEMA {cls.schema} CASCADE")

    @classmethod
    def obter_porta_livre(cls):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    @classmethod
    def url_com_search_path(cls):
        partes = urlsplit(TEST_DATABASE_URL)
        query = parse_qsl(partes.query, keep_blank_values=True)
        query.append(("options", f"-csearch_path={cls.schema}"))
        return urlunsplit(
            (
                partes.scheme,
                partes.netloc,
                partes.path,
                urlencode(query),
                partes.fragment,
            )
        )

    @classmethod
    def aguardar_aplicacao(cls):
        url = f"http://127.0.0.1:{cls.port}/"
        limite = time.monotonic() + 10
        while time.monotonic() < limite:
            if cls.processo.poll() is not None:
                erro = cls.processo.stderr.read()
                raise AssertionError(f"Uvicorn encerrou antes do teste: {erro}")

            try:
                with cls.opener.open(Request(url), timeout=1) as resposta:
                    if resposta.status == 200:
                        return
            except (URLError, OSError):
                time.sleep(0.1)

        raise AssertionError("Uvicorn não ficou disponível para o teste HTTP")

    def setUp(self):
        with psycopg.connect(self.url_com_search_path()) as conexao:
            conexao.execute("TRUNCATE access_events RESTART IDENTITY")

    def requisitar(self, caminho):
        try:
            with self.opener.open(
                Request(f"http://127.0.0.1:{self.port}{caminho}"),
                timeout=5,
            ) as resposta:
                return resposta.status, resposta.headers.get("Location")
        except HTTPError as erro:
            try:
                return erro.code, erro.headers.get("Location")
            finally:
                erro.close()

    def contar_eventos(self):
        with psycopg.connect(self.url_com_search_path()) as conexao:
            return conexao.execute(
                "SELECT source, COUNT(*) FROM access_events GROUP BY source ORDER BY source"
            ).fetchall()

    def test_rotas_publicas_preservam_qr_nfc_destino_e_nao_seguem_redirect(self):
        self.assertEqual(
            self.requisitar("/q/CODIGO_TESTE"),
            (302, "https://example.com/avaliar"),
        )
        self.assertEqual(
            self.requisitar("/q/CODIGO_TESTE?source=qr"),
            (302, "https://example.com/avaliar"),
        )
        self.assertEqual(
            self.requisitar("/q/CODIGO_TESTE?source=nfc"),
            (302, "https://example.com/avaliar"),
        )
        self.assertEqual(self.contar_eventos(), [("nfc", 1), ("qr", 2)])

    def test_erros_publicos_nao_criam_eventos(self):
        casos = (
            "/q/NAO_EXISTE",
            "/q/CODIGO_INATIVO",
            "/q/CODIGO_ARQUIVADO",
            "/q/CODIGO_TESTE?source=bluetooth",
        )
        for caminho in casos:
            with self.subTest(caminho=caminho):
                status, destino = self.requisitar(caminho)
                esperado = 400 if "bluetooth" in caminho else 404
                self.assertEqual(status, esperado)
                self.assertIsNone(destino)
                self.assertEqual(self.contar_eventos(), [])
