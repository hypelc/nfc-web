import os

DATABASE_URL = os.environ["DATABASE_URL"]
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_PUBLISHABLE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL",
    "https://nfc-web.onrender.com",
).rstrip("/")
GOOGLE_BUSINESS_CLIENT_ID = os.environ.get("GOOGLE_BUSINESS_CLIENT_ID", "")
GOOGLE_BUSINESS_CLIENT_SECRET = os.environ.get(
    "GOOGLE_BUSINESS_CLIENT_SECRET",
    "",
)
GOOGLE_BUSINESS_TOKEN_ENCRYPTION_KEY = os.environ.get(
    "GOOGLE_BUSINESS_TOKEN_ENCRYPTION_KEY",
    "",
)
GOOGLE_BUSINESS_FRONTEND_URL = os.environ.get(
    "GOOGLE_BUSINESS_FRONTEND_URL",
    "",
).rstrip("/")
GOOGLE_BUSINESS_REDIRECT_URI = os.environ.get(
    "GOOGLE_BUSINESS_REDIRECT_URI",
    f"{PUBLIC_BASE_URL}/integrations/google-business/callback",
)
CORS_ORIGINS = [
    origem.strip()
    for origem in os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if origem.strip()
]
