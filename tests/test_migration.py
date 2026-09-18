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
