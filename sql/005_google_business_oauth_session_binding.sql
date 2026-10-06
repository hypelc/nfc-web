-- Atualiza instalações em que a versão inicial da 004 já criou as tabelas.
-- Estados OAuth antigos sem vínculo à sessão não podem concluir uma autorização
-- com segurança; somente esses registros temporários são descartados.

ALTER TABLE public.google_business_oauth_states
    ADD COLUMN IF NOT EXISTS session_id_hash TEXT,
    ADD COLUMN IF NOT EXISTS authorization_code_encrypted TEXT,
    ADD COLUMN IF NOT EXISTS callback_received_at TIMESTAMPTZ;

DELETE FROM public.google_business_oauth_states
WHERE session_id_hash IS NULL
   OR session_id_hash !~ '^[0-9a-f]{64}$';

ALTER TABLE public.google_business_oauth_states
    ALTER COLUMN session_id_hash SET NOT NULL;

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid =
            'public.google_business_oauth_states'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%session_id_hash%'
    ) THEN
        ALTER TABLE public.google_business_oauth_states
            ADD CONSTRAINT google_business_oauth_states_session_hash_format
            CHECK (session_id_hash ~ '^[0-9a-f]{64}$');
    END IF;
END;
$migration$;
