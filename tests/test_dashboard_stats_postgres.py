import os
import unittest
from datetime import date, datetime, timezone
from uuid import uuid4
from unittest.mock import patch

import psycopg
from fastapi import HTTPException


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
os.environ.setdefault(
    "DATABASE_URL",
    TEST_DATABASE_URL or "postgresql://test.invalid/test",
)

from app.routers.dashboard import (
    _consultar_serie_temporal,
    obter_resumo_admin,
    obter_resumo_dashboard,
)


@unittest.skipUnless(
    TEST_DATABASE_URL,
    "Defina TEST_DATABASE_URL para executar o contrato PostgreSQL descartável",
)
class DashboardStatsPostgresContractTests(unittest.TestCase):
    REFERENCE = datetime(2026, 9, 19, 3, 30, tzinfo=timezone.utc)
    CLIENT_A = "00000000-0000-0000-0000-000000000001"
    CLIENT_B = "00000000-0000-0000-0000-000000000002"
    ADMIN = "00000000-0000-0000-0000-000000000003"

    @classmethod
    def setUpClass(cls):
        cls.schema = f"dashboard_stats_{uuid4().hex}"
        with psycopg.connect(TEST_DATABASE_URL) as conexao:
            conexao.execute(f"CREATE SCHEMA {cls.schema}")
            conexao.execute(f"SET search_path TO {cls.schema}")
            conexao.execute(
                """
                CREATE TABLE establishments (
                    id BIGINT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    stats_start_at TIMESTAMPTZ NOT NULL,
                    archived_at TIMESTAMPTZ
                );
                CREATE TABLE qr_codes (
                    id BIGINT PRIMARY KEY,
                    establishment_id BIGINT NOT NULL REFERENCES establishments(id),
                    code TEXT NOT NULL UNIQUE,
                    destination_url TEXT NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE TABLE access_events (
                    id BIGSERIAL PRIMARY KEY,
                    qr_code_id BIGINT NOT NULL REFERENCES qr_codes(id),
                    source TEXT NOT NULL CHECK (source IN ('qr', 'nfc')),
                    accessed_at TIMESTAMPTZ NOT NULL
                );
                CREATE TABLE dashboard_accounts (
                    user_id UUID PRIMARY KEY,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'client')),
                    establishment_id BIGINT REFERENCES establishments(id)
                );
                CREATE INDEX idx_test_access_events_qr_accessed
                    ON access_events (qr_code_id, accessed_at);
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
            conexao.execute(
                "TRUNCATE access_events, dashboard_accounts, qr_codes, establishments CASCADE"
            )
            conexao.execute(
                """
                INSERT INTO establishments
                    (id, name, stats_start_at, archived_at)
                VALUES
                    (1, 'Empresa A', '2026-09-18 03:00:00+00', NULL),
                    (2, 'Empresa B', '2026-09-01 00:00:00+00', NULL)
                """
            )
            conexao.execute(
                """
                INSERT INTO qr_codes
                    (id, establishment_id, code, destination_url)
                VALUES
                    (101, 1, 'codigo-a', 'https://example.com/a'),
                    (201, 2, 'codigo-b', 'https://example.com/b')
                """
            )
            conexao.execute(
                """
                INSERT INTO dashboard_accounts (user_id, role, establishment_id)
                VALUES
                    (%s, 'client', 1),
                    (%s, 'client', 2),
                    (%s, 'admin', NULL)
                """,
                (self.CLIENT_A, self.CLIENT_B, self.ADMIN),
            )
            conexao.execute(
                """
                INSERT INTO access_events (id, qr_code_id, source, accessed_at)
                VALUES
                    (1, 101, 'qr',  '2026-09-18 02:59:00+00'),
                    (2, 101, 'qr',  '2026-09-18 03:00:00+00'),
                    (3, 101, 'nfc', '2026-09-19 02:59:00+00'),
                    (4, 101, 'qr',  '2026-09-19 03:00:00+00'),
                    (5, 101, 'nfc', '2026-09-19 03:15:00+00'),
                    (6, 101, 'nfc', '2026-09-19 03:15:00+00'),
                    (7, 201, 'nfc', '2026-09-19 03:10:00+00')
                """
            )

    def snapshot_eventos(self):
        with self.abrir_conexao_teste() as conexao:
            return conexao.execute(
                """
                SELECT qr_code_id, source, accessed_at
                FROM access_events
                ORDER BY id
                """
            ).fetchall()

    def consultar_empresa_a(self):
        with (
            patch(
                "app.routers.dashboard.abrir_conexao",
                side_effect=self.abrir_conexao_teste,
            ),
            patch(
                "app.routers.dashboard._obter_referencia_estatisticas",
                return_value=self.REFERENCE,
            ),
        ):
            return obter_resumo_dashboard(
                establishment_id=1,
                usuario={"id": self.CLIENT_A},
            )

    def test_cliente_so_consulta_seu_estabelecimento_e_nao_altera_eventos(self):
        antes = self.snapshot_eventos()

        with (
            patch(
                "app.routers.dashboard.abrir_conexao",
                side_effect=self.abrir_conexao_teste,
            ),
            patch(
                "app.routers.dashboard._obter_referencia_estatisticas",
                return_value=self.REFERENCE,
            ),
        ):
            with self.assertRaises(HTTPException) as contexto:
                obter_resumo_dashboard(
                    establishment_id=2,
                    usuario={"id": self.CLIENT_A},
                )

        self.assertEqual(contexto.exception.status_code, 403)
        self.assertEqual(self.snapshot_eventos(), antes)

    def test_serie_respeita_timezone_stats_start_zeros_e_acessos_repetidos(self):
        antes = self.snapshot_eventos()
        resultado = self.consultar_empresa_a()

        serie_30 = resultado["serie_temporal"]["30_dias"]
        serie_7 = resultado["serie_temporal"]["7_dias"]
        self.assertEqual(len(serie_30), 30)
        self.assertEqual(len(serie_7), 7)
        self.assertEqual(
            [ponto["data"] for ponto in serie_30],
            sorted(ponto["data"] for ponto in serie_30),
        )
        self.assertEqual(serie_30[0]["data"], date(2026, 8, 21))
        self.assertEqual(serie_30[-1], {"data": date(2026, 9, 19), "acessos": 3})
        self.assertEqual(
            next(p for p in serie_30 if p["data"] == date(2026, 9, 18)),
            {"data": date(2026, 9, 18), "acessos": 2},
        )
        self.assertTrue(all(ponto["acessos"] == 0 for ponto in serie_30[:27]))

        estatisticas = resultado["estatisticas"]
        self.assertEqual(estatisticas["total_acessos"], 5)
        self.assertEqual(estatisticas["acessos_hoje"], 3)
        self.assertEqual(estatisticas["acessos_ultimos_7_dias"], 5)
        self.assertEqual(
            estatisticas["acessos_ultimos_7_dias"],
            sum(ponto["acessos"] for ponto in serie_7),
        )
        self.assertEqual(estatisticas["acessos_qr"], 2)
        self.assertEqual(estatisticas["acessos_nfc"], 3)
        self.assertEqual(resultado["acessos_recentes"][0]["origem"], "nfc")
        self.assertEqual(self.snapshot_eventos(), antes)

    def test_leitura_mantem_snapshot_se_um_evento_for_confirmado_entre_consultas(self):
        original_series = _consultar_serie_temporal

        def inserir_entre_consultas(cursor, establishment_id, referencia):
            with self.abrir_conexao_teste() as outra_conexao:
                outra_conexao.execute(
                    """
                    INSERT INTO access_events (id, qr_code_id, source, accessed_at)
                    VALUES (8, 101, 'nfc', %s)
                    """,
                    (self.REFERENCE,),
                )
            return original_series(cursor, establishment_id, referencia)

        with (
            patch(
                "app.routers.dashboard.abrir_conexao",
                side_effect=self.abrir_conexao_teste,
            ),
            patch(
                "app.routers.dashboard._obter_referencia_estatisticas",
                return_value=self.REFERENCE,
            ),
            patch(
                "app.routers.dashboard._consultar_serie_temporal",
                side_effect=inserir_entre_consultas,
            ),
        ):
            resultado_durante_a_gravacao = obter_resumo_dashboard(
                establishment_id=1,
                usuario={"id": self.CLIENT_A},
            )

        estatisticas_durante = resultado_durante_a_gravacao["estatisticas"]
        self.assertEqual(estatisticas_durante["total_acessos"], 5)
        self.assertEqual(estatisticas_durante["acessos_ultimos_7_dias"], 5)
        self.assertEqual(estatisticas_durante["acessos_hoje"], 3)
        self.assertEqual(
            self.snapshot_eventos()[-1][1:],
            ("nfc", self.REFERENCE),
        )

        resultado_posterior = self.consultar_empresa_a()
        self.assertEqual(resultado_posterior["estatisticas"]["total_acessos"], 6)
        self.assertEqual(resultado_posterior["estatisticas"]["acessos_hoje"], 4)
        self.assertEqual(
            resultado_posterior["estatisticas"]["acessos_ultimos_7_dias"],
            6,
        )

    def test_admin_preserva_divisao_qr_nfc_e_isola_empresas_nos_indicadores(self):
        with (
            patch(
                "app.routers.dashboard.abrir_conexao",
                side_effect=self.abrir_conexao_teste,
            ),
            patch(
                "app.routers.dashboard._obter_referencia_estatisticas",
                return_value=self.REFERENCE,
            ),
        ):
            resultado = obter_resumo_admin({"id": self.ADMIN})

        self.assertEqual(resultado["indicadores"]["total_acessos"], 6)
        self.assertEqual(resultado["indicadores"]["acessos_qr"], 2)
        self.assertEqual(resultado["indicadores"]["acessos_nfc"], 4)
        por_empresa = {empresa["id"]: empresa for empresa in resultado["empresas"]}
        self.assertEqual(por_empresa[1]["total_acessos"], 5)
        self.assertEqual(por_empresa[1]["acessos_qr"], 2)
        self.assertEqual(por_empresa[1]["acessos_nfc"], 3)
        self.assertEqual(por_empresa[2]["total_acessos"], 1)
        self.assertEqual(por_empresa[2]["acessos_nfc"], 1)

    def test_admin_consulta_serie_de_uma_empresa_sem_misturar_eventos(self):
        with (
            patch(
                "app.routers.dashboard.abrir_conexao",
                side_effect=self.abrir_conexao_teste,
            ),
            patch(
                "app.routers.dashboard._obter_referencia_estatisticas",
                return_value=self.REFERENCE,
            ),
        ):
            resultado = obter_resumo_dashboard(
                establishment_id=2,
                usuario={"id": self.ADMIN},
            )

        self.assertEqual(resultado["estatisticas"]["total_acessos"], 1)
        self.assertEqual(resultado["estatisticas"]["acessos_nfc"], 1)
        self.assertEqual(
            sum(
                ponto["acessos"]
                for ponto in resultado["serie_temporal"]["30_dias"]
            ),
            1,
        )
