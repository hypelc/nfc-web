-- Uma nova autorização substitui tentativas pendentes da mesma empresa.
-- Estados existentes antes desta migration não possuem as garantias novas e
-- permanecem explicitamente inelegíveis para conclusão.

ALTER TABLE public.google_business_oauth_states
    ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS concurrency_guarded BOOLEAN NOT NULL DEFAULT FALSE;

REVOKE ALL ON TABLE public.google_business_oauth_states
    FROM anon, authenticated;
