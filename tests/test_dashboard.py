import unittest
from datetime import datetime, timezone
import os
from unittest.mock import patch

from fastapi import HTTPException

os.environ.setdefault("DATABASE_URL", "postgresql://test.invalid/test")

from app.routers.dashboard import (
    arquivar_empresa,
    listar_estabelecimentos,
    resetar_acessos_empresa,
    restaurar_empresa,
)


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

    def fetchall(self):
        return []


class ConexaoFalsa:
    def __init__(self, respostas):
        self.cursor_falso = CursorFalso(respostas)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def cursor(self):
        return self.cursor_falso


ADMIN = {"id": "admin-user"}


class DashboardLifecycleTests(unittest.TestCase):
    def test_arquivar_preserva_dados_e_registra_auditoria_na_mesma_transacao(self):
        arquivada_em = datetime(2026, 9, 17, 22, 0, tzinfo=timezone.utc)
        conexao = ConexaoFalsa([
            ("admin", None),
            (7, "Café Central", None),
            (7, "Café Central", arquivada_em),
        ])

        with patch("app.routers.dashboard.abrir_conexao", return_value=conexao):
            resultado = arquivar_empresa(7, ADMIN)

        self.assertEqual(resultado["id"], 7)
        self.assertEqual(resultado["arquivada_em"], arquivada_em)
        sqls = [sql.upper() for sql, _ in conexao.cursor_falso.comandos]
        self.assertFalse(any("DELETE" in sql for sql in sqls))
        self.assertEqual(conexao.cursor_falso.comandos[-1][1], (
            "admin-user",
            "establishment_archived",
            "establishment",
            7,
        ))

    def test_reset_antigo_apenas_muda_marco_e_preserva_eventos(self):
        marco_anterior = datetime(2026, 8, 1, tzinfo=timezone.utc)
        marco_novo = datetime(2026, 9, 17, 22, 0, tzinfo=timezone.utc)
        conexao = ConexaoFalsa([
            ("admin", None),
            (7, "Café Central", marco_anterior, None),
            (7, "Café Central", marco_novo),
        ])

        with patch("app.routers.dashboard.abrir_conexao", return_value=conexao):
            resultado = resetar_acessos_empresa(7, ADMIN)

        self.assertEqual(resultado["periodo_anterior_iniciado_em"], marco_anterior)
        self.assertEqual(resultado["novo_periodo_iniciado_em"], marco_novo)
        self.assertNotIn("acessos_excluidos", resultado)
        sqls = [sql.upper() for sql, _ in conexao.cursor_falso.comandos]
        self.assertFalse(any("DELETE" in sql for sql in sqls))

    def test_restaurar_nao_reescreve_estado_individual_dos_qr_codes(self):
        arquivada_em = datetime(2026, 9, 16, tzinfo=timezone.utc)
        conexao = ConexaoFalsa([
            ("admin", None),
            (7, "Café Central", arquivada_em),
            (7, "Café Central"),
        ])

        with patch("app.routers.dashboard.abrir_conexao", return_value=conexao):
            resultado = restaurar_empresa(7, ADMIN)

        self.assertEqual(resultado, {"id": 7, "nome": "Café Central"})
        sqls = [sql.upper() for sql, _ in conexao.cursor_falso.comandos]
        self.assertFalse(any("QR_CODES" in sql and "UPDATE" in sql for sql in sqls))
        self.assertEqual(conexao.cursor_falso.comandos[-1][1][1], "establishment_restored")

    def test_cliente_sem_estabelecimento_recebe_recusa_controlada(self):
        conexao = ConexaoFalsa([("client", None)])

        with patch("app.routers.dashboard.abrir_conexao", return_value=conexao):
            with self.assertRaises(HTTPException) as contexto:
                listar_estabelecimentos({"id": "client-user"})

        self.assertEqual(contexto.exception.status_code, 403)
        self.assertEqual(contexto.exception.detail, "Cliente sem empresa vinculada")
