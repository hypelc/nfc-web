-- Conexão server-side com o Google Business Profile.
-- Tokens ficam cifrados no backend e estas tabelas não são uma superfície da Data API.

CREATE TABLE IF NOT EXISTS public.google_business_oauth_states (
    id BIGSERIAL PRIMARY KEY,
    state_hash TEXT NOT NULL UNIQUE,
    dashboard_user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    establishment_id BIGINT NOT NULL REFERENCES establishments(id) ON DELETE CASCADE,
    session_id_hash TEXT NOT NULL
        CHECK (session_id_hash ~ '^[0-9a-f]{64}$'),
    expires_at TIMESTAMPTZ NOT NULL,
    authorization_code_encrypted TEXT,
    callback_received_at TIMESTAMPTZ,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_google_business_oauth_states_expires
ON public.google_business_oauth_states (expires_at);

CREATE TABLE IF NOT EXISTS public.google_business_connections (
    id BIGSERIAL PRIMARY KEY,
    establishment_id BIGINT NOT NULL UNIQUE
        REFERENCES establishments(id) ON DELETE CASCADE,
    dashboard_user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    access_token_encrypted TEXT NOT NULL,
    refresh_token_encrypted TEXT,
    access_token_expires_at TIMESTAMPTZ NOT NULL,
    granted_scope TEXT NOT NULL,
    google_location_name TEXT,
    google_location_title TEXT,
    status TEXT NOT NULL DEFAULT 'connected'
        CHECK (status IN ('connected', 'reauth_required', 'revoked')),
    last_error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_google_business_connections_user
ON public.google_business_connections (dashboard_user_id);

ALTER TABLE public.google_business_oauth_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.google_business_connections ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.google_business_oauth_states
FROM anon, authenticated;
REVOKE ALL ON SEQUENCE public.google_business_oauth_states_id_seq
FROM anon, authenticated;
REVOKE ALL ON TABLE public.google_business_connections
FROM anon, authenticated;
REVOKE ALL ON SEQUENCE public.google_business_connections_id_seq
FROM anon, authenticated;
