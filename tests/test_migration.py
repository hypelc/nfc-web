import unittest
from pathlib import Path


class NonDestructiveMigrationTests(unittest.TestCase):
    migration = (
        Path(__file__).resolve().parents[1]
        / "sql"
        / "003_non_destructive_dashboard.sql"
    ).read_text(encoding="utf-8")

    def test_auditoria_fica_protegida_contra_acesso_da_data_api(self):
        sql = self.migration.upper()

        self.assertIn("ALTER TABLE PUBLIC.ADMIN_AUDIT_LOGS ENABLE ROW LEVEL SECURITY", sql)
        self.assertIn(
            "REVOKE ALL ON TABLE PUBLIC.ADMIN_AUDIT_LOGS FROM ANON, AUTHENTICATED",
            sql,
        )
        self.assertIn(
            "REVOKE ALL ON SEQUENCE PUBLIC.ADMIN_AUDIT_LOGS_ID_SEQ FROM ANON, AUTHENTICATED",
            sql,
        )

    def test_migracao_nao_apaga_eventos(self):
        sql = self.migration.upper()

        self.assertNotIn("DELETE FROM ACCESS_EVENTS", sql)
        self.assertNotIn("TRUNCATE ACCESS_EVENTS", sql)

    def test_oauth_google_protege_tokens_e_state_da_data_api(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "sql"
            / "004_google_business_oauth.sql"
        ).read_text(encoding="utf-8").upper()

        self.assertIn(
            "ALTER TABLE PUBLIC.GOOGLE_BUSINESS_OAUTH_STATES ENABLE ROW LEVEL SECURITY",
            migration,
        )
        self.assertIn(
            "ALTER TABLE PUBLIC.GOOGLE_BUSINESS_CONNECTIONS ENABLE ROW LEVEL SECURITY",
            migration,
        )
        self.assertIn(
            "REVOKE ALL ON TABLE PUBLIC.GOOGLE_BUSINESS_CONNECTIONS",
            migration,
        )
        self.assertIn("ACCESS_TOKEN_ENCRYPTED", migration)
        self.assertIn("REFRESH_TOKEN_ENCRYPTED", migration)
        self.assertNotIn("DROP TABLE", migration)
