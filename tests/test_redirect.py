import unittest
import os
from unittest.mock import patch

from fastapi import HTTPException

os.environ.setdefault("DATABASE_URL", "postgresql://test.invalid/test")

from app.main import acessar_qr_code


class CursorFalso:
    def __init__(self, respostas):
        self.respostas = iter(respostas)
        self.comandos = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, sql, params=None):
        self.comandos.append((sql, params))

    def fetchone(self):
        return next(self.respostas)


class ConexaoFalsa:
    def __init__(self, respostas):
        self.cursor_falso = CursorFalso(respostas)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def cursor(self):
        return self.cursor_falso


class RedirectContractTests(unittest.TestCase):
    def test_qr_ativo_de_empresa_ativa_registra_origem_e_redireciona(self):
        conexao = ConexaoFalsa([(42, "https://example.com/avaliar")])

        with patch("app.main.abrir_conexao", return_value=conexao):
            resposta = acessar_qr_code("placa-42", source="nfc")

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta.headers["location"], "https://example.com/avaliar")
        self.assertIn("establishments.archived_at IS NULL", conexao.cursor_falso.comandos[0][0])
        self.assertEqual(conexao.cursor_falso.comandos[1][1], (42, "nfc"))

    def test_qr_de_empresa_arquivada_nao_redireciona(self):
        conexao = ConexaoFalsa([None])

        with patch("app.main.abrir_conexao", return_value=conexao):
            with self.assertRaises(HTTPException) as contexto:
                acessar_qr_code("placa-arquivada")

        self.assertEqual(contexto.exception.status_code, 404)
        self.assertEqual(len(conexao.cursor_falso.comandos), 1)

    def test_origem_invalida_e_recusada_antes_do_banco(self):
        with patch("app.main.abrir_conexao") as abrir_conexao:
            with self.assertRaises(HTTPException) as contexto:
                acessar_qr_code("placa-42", source="outro")

        self.assertEqual(contexto.exception.status_code, 400)
        abrir_conexao.assert_not_called()
