-- Versão otimista para impedir que respostas OAuth/API atrasadas sobrescrevam
-- uma conexão mais nova. Os estados em andamento existentes ficam sem baseline;
-- portanto não podem invalidar uma conexão por falta de informação histórica.

ALTER TABLE public.google_business_oauth_states
    ADD COLUMN IF NOT EXISTS connection_version_at_start UUID;

ALTER TABLE public.google_business_connections
    ADD COLUMN IF NOT EXISTS version UUID NOT NULL DEFAULT gen_random_uuid();
