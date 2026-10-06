import os
import unittest
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg import sql


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "sql"
    / "004_google_business_oauth.sql"
)
MIGRATION_UPGRADE = (
    Path(__file__).resolve().parents[1]
    / "sql"
    / "005_google_business_oauth_session_binding.sql"
)
MIGRATION_CONCURRENCY = (
    Path(__file__).resolve().parents[1]
    / "sql"
    / "006_google_business_oauth_concurrency.sql"
)
MIGRATION_ATTEMPTS = (
    Path(__file__).resolve().parents[1]
    / "sql"
    / "007_google_business_oauth_attempts.sql"
)
LEGACY_SCHEMA = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "004_google_business_oauth_legacy.sql"
)


@unittest.skipUnless(
    TEST_DATABASE_URL,
    "Defina TEST_DATABASE_URL para o teste PostgreSQL descartável da migration",
)
class GoogleBusinessMigrationPostgresTests(unittest.TestCase):
    def test_executa_migration_real_e_verifica_schema_rls_e_privilegios(self):
        """Aplica as migrations reais 004 a 007 em schema limpo e faz rollback."""

        conexao = psycopg.connect(TEST_DATABASE_URL)
        try:
            with conexao.cursor() as cursor:
                cursor.execute("SET search_path TO public")
                cursor.execute("CREATE SCHEMA IF NOT EXISTS auth")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS auth.users (
                        id UUID PRIMARY KEY
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS public.establishments (
                        id BIGINT PRIMARY KEY
                    )
                    """
                )

                for role in ("anon", "authenticated"):
                    cursor.execute(
                        "SELECT 1 FROM pg_roles WHERE rolname = %s",
                        (role,),
                    )
                    if cursor.fetchone() is None:
                        cursor.execute(
                            sql.SQL("CREATE ROLE {} NOLOGIN").format(
                                sql.Identifier(role)
                            )
                        )

                cursor.execute(MIGRATION.read_text(encoding="utf-8"))
                cursor.execute(MIGRATION_UPGRADE.read_text(encoding="utf-8"))
                cursor.execute(MIGRATION_CONCURRENCY.read_text(encoding="utf-8"))
                cursor.execute(MIGRATION_CONCURRENCY.read_text(encoding="utf-8"))
                cursor.execute(MIGRATION_ATTEMPTS.read_text(encoding="utf-8"))
                cursor.execute(MIGRATION_ATTEMPTS.read_text(encoding="utf-8"))

                cursor.execute(
                    """
                    SELECT to_regclass('public.google_business_oauth_states'),
                           to_regclass('public.google_business_connections')
                    """
                )
                self.assertEqual(
                    cursor.fetchone(),
                    (
                        "google_business_oauth_states",
                        "google_business_connections",
                    ),
                )

                cursor.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'google_business_oauth_states'
                    """
                )
                state_columns = {row[0] for row in cursor.fetchall()}
                self.assertTrue(
                    {
                        "state_hash",
                        "dashboard_user_id",
                        "establishment_id",
                        "session_id_hash",
                        "authorization_code_encrypted",
                        "expires_at",
                        "consumed_at",
                        "connection_version_at_start",
                        "cancelled_at",
                        "concurrency_guarded",
                    }.issubset(state_columns)
                )

                cursor.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'google_business_connections'
                    """
                )
                connection_columns = {row[0] for row in cursor.fetchall()}
                self.assertTrue(
                    {
                        "establishment_id",
                        "dashboard_user_id",
                        "access_token_encrypted",
                        "refresh_token_encrypted",
                        "status",
                        "version",
                    }.issubset(connection_columns)
                )
                cursor.execute(
                    """
                    SELECT column_name, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'google_business_oauth_states'
                      AND column_name IN ('cancelled_at', 'concurrency_guarded')
                    ORDER BY column_name
                    """
                )
                self.assertEqual(
                    cursor.fetchall(),
                    [
                        ("cancelled_at", "YES", None),
                        ("concurrency_guarded", "NO", "false"),
                    ],
                )
                cursor.execute(
                    """
                    SELECT data_type, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'google_business_connections'
                      AND column_name = 'version'
                    """
                )
                version_type = cursor.fetchone()
                self.assertEqual(version_type[0:2], ("uuid", "NO"))
                self.assertIn("gen_random_uuid", version_type[2])

                cursor.execute(
                    """
                    SELECT conrelid::regclass::text, contype,
                           pg_get_constraintdef(oid)
                    FROM pg_constraint
                    WHERE conrelid IN (
                        'public.google_business_oauth_states'::regclass,
                        'public.google_business_connections'::regclass
                    )
                    """
                )
                constraints = cursor.fetchall()
                definitions = "\n".join(row[2] for row in constraints)
                self.assertIn("PRIMARY KEY", definitions)
                self.assertIn("UNIQUE (state_hash)", definitions)
                self.assertIn("UNIQUE (establishment_id)", definitions)
                self.assertIn("REFERENCES auth.users", definitions)
                self.assertIn("REFERENCES establishments", definitions)
                self.assertIn("CHECK", definitions)
                self.assertEqual(
                    sum(1 for row in constraints if row[1] == "f"),
                    4,
                )

                cursor.execute(
                    """
                    SELECT indexname
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND tablename IN (
                          'google_business_oauth_states',
                          'google_business_connections'
                      )
                    """
                )
                indexes = {row[0] for row in cursor.fetchall()}
                self.assertTrue(
                    {
                        "idx_google_business_oauth_states_expires",
                        "idx_google_business_connections_user",
                    }.issubset(indexes)
                )

                cursor.execute(
                    """
                    SELECT c.relname, c.relrowsecurity
                    FROM pg_class c
                    WHERE c.oid IN (
                        'public.google_business_oauth_states'::regclass,
                        'public.google_business_connections'::regclass
                    )
                    ORDER BY c.relname
                    """
                )
                self.assertEqual(
                    cursor.fetchall(),
                    [
                        ("google_business_connections", True),
                        ("google_business_oauth_states", True),
                    ],
                )

                cursor.execute(
                    """
                    SELECT count(*)
                    FROM pg_policies
                    WHERE schemaname = 'public'
                      AND tablename IN (
                          'google_business_oauth_states',
                          'google_business_connections'
                      )
                    """
                )
                self.assertEqual(cursor.fetchone()[0], 0)

                table_privileges = (
                    "SELECT",
                    "INSERT",
                    "UPDATE",
                    "DELETE",
                    "TRUNCATE",
                    "REFERENCES",
                    "TRIGGER",
                )
                for role in ("anon", "authenticated"):
                    for table in (
                        "google_business_oauth_states",
                        "google_business_connections",
                    ):
                        for privilege in table_privileges:
                            cursor.execute(
                                "SELECT has_table_privilege(%s, %s, %s)",
                                (role, f"public.{table}", privilege),
                            )
                            self.assertFalse(
                                cursor.fetchone()[0],
                                f"{role} manteve {privilege} em {table}",
                            )

                    for sequence in (
                        "google_business_oauth_states_id_seq",
                        "google_business_connections_id_seq",
                    ):
                        for privilege in ("USAGE", "SELECT", "UPDATE"):
                            cursor.execute(
                                "SELECT has_sequence_privilege(%s, %s, %s)",
                                (role, f"public.{sequence}", privilege),
                            )
                            self.assertFalse(
                                cursor.fetchone()[0],
                                f"{role} manteve {privilege} em {sequence}",
                            )
        finally:
            conexao.rollback()
            conexao.close()

    def test_migrations_005_a_007_atualizam_schema_legado_sem_afetar_conexao(self):
        conexao = psycopg.connect(TEST_DATABASE_URL)
        try:
            with conexao.cursor() as cursor:
                cursor.execute("SET search_path TO public")
                cursor.execute("CREATE SCHEMA IF NOT EXISTS auth")
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS auth.users (id UUID PRIMARY KEY)"
                )
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS public.establishments "
                    "(id BIGINT PRIMARY KEY)"
                )
                for role in ("anon", "authenticated"):
                    cursor.execute(
                        "SELECT 1 FROM pg_roles WHERE rolname = %s",
                        (role,),
                    )
                    if cursor.fetchone() is None:
                        cursor.execute(
                            sql.SQL("CREATE ROLE {} NOLOGIN").format(
                                sql.Identifier(role)
                            )
                        )
                cursor.execute(LEGACY_SCHEMA.read_text(encoding="utf-8"))
                cursor.execute(
                    "INSERT INTO auth.users (id) VALUES "
                    "('00000000-0000-0000-0000-000000000011')"
                )
                cursor.execute(
                    "INSERT INTO public.establishments (id) VALUES (1)"
                )
                cursor.execute(
                    """
                    INSERT INTO public.google_business_oauth_states (
                        state_hash, dashboard_user_id, establishment_id, expires_at
                    ) VALUES (
                        'state-antigo',
                        '00000000-0000-0000-0000-000000000011',
                        1,
                        NOW() + INTERVAL '5 minutes'
                    )
                    """
                )
                cursor.execute(
                    """
                    INSERT INTO public.google_business_connections (
                        establishment_id, dashboard_user_id,
                        access_token_encrypted, access_token_expires_at,
                        granted_scope, status
                    ) VALUES (
                        1,
                        '00000000-0000-0000-0000-000000000011',
                        'ciphertext-fixture', NOW() + INTERVAL '1 hour',
                        'business.manage', 'connected'
                    )
                    """
                )

                cursor.execute(MIGRATION_UPGRADE.read_text(encoding="utf-8"))
                cursor.execute(MIGRATION_CONCURRENCY.read_text(encoding="utf-8"))
                cursor.execute(
                    """
                    INSERT INTO public.google_business_oauth_states (
                        state_hash, dashboard_user_id, establishment_id,
                        session_id_hash, expires_at
                    ) VALUES (
                        'state-valid-antes-007',
                        '00000000-0000-0000-0000-000000000011',
                        1,
                        repeat('a', 64),
                        NOW() + INTERVAL '5 minutes'
                    )
                    """
                )
                cursor.execute(MIGRATION_ATTEMPTS.read_text(encoding="utf-8"))

                cursor.execute(
                    """
                    SELECT column_name, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'google_business_oauth_states'
                      AND column_name IN (
                          'session_id_hash',
                          'authorization_code_encrypted',
                          'callback_received_at',
                          'connection_version_at_start',
                          'cancelled_at',
                          'concurrency_guarded'
                      )
                    ORDER BY column_name
                    """
                )
                self.assertEqual(
                    cursor.fetchall(),
                    [
                        ("authorization_code_encrypted", "YES"),
                        ("callback_received_at", "YES"),
                        ("cancelled_at", "YES"),
                        ("concurrency_guarded", "NO"),
                        ("connection_version_at_start", "YES"),
                        ("session_id_hash", "NO"),
                    ],
                )

                cursor.execute(
                    """
                    SELECT concurrency_guarded, cancelled_at
                    FROM public.google_business_oauth_states
                    WHERE state_hash = 'state-valid-antes-007'
                    """
                )
                self.assertEqual(cursor.fetchone(), (False, None))

                cursor.execute(
                    """
                    SELECT access_token_encrypted, status, version
                    FROM public.google_business_connections
                    WHERE establishment_id = 1
                    """
                )
                conexao_atualizada = cursor.fetchone()
                self.assertEqual(conexao_atualizada[:2], ("ciphertext-fixture", "connected"))
                self.assertIsInstance(conexao_atualizada[2], UUID)

                cursor.execute(
                    """
                    SELECT pg_get_constraintdef(oid)
                    FROM pg_constraint
                    WHERE conrelid =
                        'public.google_business_oauth_states'::regclass
                      AND contype = 'c'
                    """
                )
                self.assertIn(
                    "session_id_hash",
                    " ".join(row[0] for row in cursor.fetchall()),
                )

                with self.assertRaises(psycopg.errors.NotNullViolation):
                    with conexao.transaction():
                        cursor.execute(
                            """
                            INSERT INTO public.google_business_oauth_states (
                                state_hash, dashboard_user_id, establishment_id,
                                expires_at, session_id_hash
                            ) VALUES (
                                'state-sem-sessao',
                                '00000000-0000-0000-0000-000000000011',
                                1, NOW() + INTERVAL '5 minutes', NULL
                            )
                            """
                        )
        finally:
            conexao.rollback()
            conexao.close()


if __name__ == "__main__":
    unittest.main()
