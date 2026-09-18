-- Evolução aditiva para operação com histórico preservado.
-- Aplicar primeiro em ambiente isolado e verificar as contagens antes de produção.

ALTER TABLE establishments
ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

ALTER TABLE establishments
ADD COLUMN IF NOT EXISTS stats_start_at TIMESTAMPTZ;

-- O marco inicial precisa manter os números que o painel já mostrava.
UPDATE establishments
SET stats_start_at = created_at
WHERE stats_start_at IS NULL;

ALTER TABLE establishments
ALTER COLUMN stats_start_at SET DEFAULT NOW();

ALTER TABLE establishments
ALTER COLUMN stats_start_at SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_establishments_archived_at
ON establishments (archived_at);

CREATE INDEX IF NOT EXISTS idx_access_events_qr_code_accessed_at
ON access_events (qr_code_id, accessed_at);

CREATE TABLE IF NOT EXISTS public.admin_audit_logs (
    id BIGSERIAL PRIMARY KEY,
    actor_user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id BIGINT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_admin_audit_logs_resource
ON public.admin_audit_logs (resource_type, resource_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_admin_audit_logs_actor
ON public.admin_audit_logs (actor_user_id, created_at DESC);

-- A auditoria é interna ao backend e não é uma superfície da Data API.
-- RLS sem políticas nega acesso por linha; os REVOKEs também impedem que
-- anon/authenticated alcancem a tabela mesmo quando o projeto tem grants
-- padrão no schema public. O backend usa a conexão server-side DATABASE_URL.
ALTER TABLE public.admin_audit_logs ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.admin_audit_logs FROM anon, authenticated;
REVOKE ALL ON SEQUENCE public.admin_audit_logs_id_seq FROM anon, authenticated;
