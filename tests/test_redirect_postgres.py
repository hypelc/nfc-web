import os
import unittest
from uuid import uuid4
from unittest.mock import patch

import psycopg
from fastapi import HTTPException


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
os.environ.setdefault(
    "DATABASE_URL",
    TEST_DATABASE_URL or "postgresql://test.invalid/test",
)

from app.main import acessar_qr_code


@unittest.skipUnless(
    TEST_DATABASE_URL,
    "Defina TEST_DATABASE_URL para executar o contrato PostgreSQL descartável",
)
class RedirectPostgresContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = f"redirect_contract_{uuid4().hex}"
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

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(TEST_DATABASE_URL) as conexao:
            conexao.execute(f"DROP SCHEMA {cls.schema} CASCADE")

    @classmethod
    def abrir_conexao_teste(cls):
        conexao = psycopg.connect(TEST_DATABASE_URL)
        conexao.execute(f"SET search_path TO {cls.schema}")
        return conexao

    def setUp(self):
        with self.abrir_conexao_teste() as conexao:
            conexao.execute("TRUNCATE access_events, qr_codes, establishments CASCADE")
            conexao.execute(
                "INSERT INTO establishments (id) VALUES (1)"
            )
            conexao.execute(
                """
                INSERT INTO qr_codes (id, establishment_id, code, destination_url)
                VALUES (10, 1, 'review-qr', 'https://example.com/avaliar')
                """
            )

    def contar_eventos(self):
        with self.abrir_conexao_teste() as conexao:
            return conexao.execute("SELECT COUNT(*) FROM access_events").fetchone()[0]

    def test_qr_ativo_de_empresa_ativa_redireciona_e_registra_evento(self):
        with patch("app.main.abrir_conexao", side_effect=self.abrir_conexao_teste):
            resposta = acessar_qr_code("review-qr", source="qr")

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta.headers["location"], "https://example.com/avaliar")
        self.assertEqual(self.contar_eventos(), 1)

    def test_empresa_arquivada_nao_redireciona_nem_registra_evento(self):
        with self.abrir_conexao_teste() as conexao:
            conexao.execute("UPDATE establishments SET archived_at = NOW()")

        with patch("app.main.abrir_conexao", side_effect=self.abrir_conexao_teste):
            with self.assertRaises(HTTPException) as contexto:
                acessar_qr_code("review-qr", source="nfc")

        self.assertEqual(contexto.exception.status_code, 404)
        self.assertEqual(self.contar_eventos(), 0)

    def test_empresa_restaurada_volta_a_redirecionar(self):
        with self.abrir_conexao_teste() as conexao:
            conexao.execute("UPDATE establishments SET archived_at = NOW()")
            conexao.execute("UPDATE establishments SET archived_at = NULL")

        with patch("app.main.abrir_conexao", side_effect=self.abrir_conexao_teste):
            resposta = acessar_qr_code("review-qr", source="nfc")

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(self.contar_eventos(), 1)

    def test_qr_individualmente_inativo_nao_redireciona(self):
        with self.abrir_conexao_teste() as conexao:
            conexao.execute("UPDATE qr_codes SET is_active = FALSE")

        with patch("app.main.abrir_conexao", side_effect=self.abrir_conexao_teste):
            with self.assertRaises(HTTPException) as contexto:
                acessar_qr_code("review-qr")

        self.assertEqual(contexto.exception.status_code, 404)
        self.assertEqual(self.contar_eventos(), 0)
