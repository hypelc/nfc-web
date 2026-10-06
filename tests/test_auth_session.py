import base64
import json
import os
import unittest
from io import BytesIO
from unittest.mock import patch

from fastapi.security import HTTPAuthorizationCredentials

os.environ.setdefault("DATABASE_URL", "postgresql://test.invalid/test")

from app.auth import obter_usuario_atual


USER_ID = "00000000-0000-0000-0000-000000000011"
SESSION_ID = "00000000-0000-0000-0000-000000000021"


def token_com_claims(claims):
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode()
    return f"header.{payload.rstrip('=')}.signature"


class SupabaseSessionClaimTests(unittest.TestCase):
    def obter_usuario(self, token):
        resposta_auth = BytesIO(
            json.dumps({"id": USER_ID, "email": "cliente@example.invalid"}).encode()
        )
        credenciais = HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials=token,
        )
        with (
            patch("app.auth.SUPABASE_URL", "https://auth.example.invalid"),
            patch("app.auth.SUPABASE_PUBLISHABLE_KEY", "test-key"),
            patch("app.auth.urlopen", return_value=resposta_auth),
        ):
            return obter_usuario_atual(credenciais)

    def test_expoe_session_id_apenas_do_bearer_validado_para_o_mesmo_usuario(self):
        usuario = self.obter_usuario(
            token_com_claims({"sub": USER_ID, "session_id": SESSION_ID})
        )

        self.assertEqual(usuario["id"], USER_ID)
        self.assertEqual(usuario["session_id"], SESSION_ID)

    def test_nao_usa_claim_ausente_ou_de_outro_usuario_para_vincular_sessao(self):
        sem_sessao = self.obter_usuario(token_com_claims({"sub": USER_ID}))
        outro_usuario = self.obter_usuario(
            token_com_claims(
                {
                    "sub": "00000000-0000-0000-0000-000000000099",
                    "session_id": SESSION_ID,
                }
            )
        )

        self.assertIsNone(sem_sessao["session_id"])
        self.assertIsNone(outro_usuario["session_id"])


if __name__ == "__main__":
    unittest.main()
