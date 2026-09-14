-- Authentication sessions, password recovery, and retainer checkout records.
-- Additive. Safe to re-run. Does not alter scraper, leads, inquiries, or portal tokens.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS public.platform_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL
        CHECK (role IN ('operator', 'portal')),
    client_id UUID
        REFERENCES public.client_catalog (id) ON DELETE CASCADE,
    display_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT platform_users_email_key UNIQUE (email),
    CONSTRAINT platform_users_email_chk CHECK (
        email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'
    ),
    CONSTRAINT platform_users_role_client_chk CHECK (
        (role = 'operator' AND client_id IS NULL)
        OR (role = 'portal' AND client_id IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS public.auth_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL
        REFERENCES public.platform_users (id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL,
    scope TEXT NOT NULL
        CHECK (scope IN ('desk', 'portal')),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT auth_sessions_token_hash_key UNIQUE (token_hash)
);

CREATE TABLE IF NOT EXISTS public.password_reset_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL
        REFERENCES public.platform_users (id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT password_reset_tokens_token_hash_key UNIQUE (token_hash)
);

CREATE TABLE IF NOT EXISTS public.retainer_checkouts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stripe_session_id TEXT,
    customer_email TEXT,
    company_name TEXT,
    amount_pence INTEGER NOT NULL DEFAULT 400000,
    currency TEXT NOT NULL DEFAULT 'gbp',
    status TEXT NOT NULL DEFAULT 'created'
        CHECK (status IN ('created', 'completed', 'canceled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS set_updated_at ON public.platform_users;
CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON public.platform_users
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

DROP TRIGGER IF EXISTS set_updated_at ON public.retainer_checkouts;
CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON public.retainer_checkouts
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

CREATE INDEX IF NOT EXISTS platform_users_role_idx
    ON public.platform_users (role);
CREATE INDEX IF NOT EXISTS platform_users_client_id_idx
    ON public.platform_users (client_id);
CREATE INDEX IF NOT EXISTS auth_sessions_user_id_idx
    ON public.auth_sessions (user_id);
CREATE INDEX IF NOT EXISTS auth_sessions_expires_at_idx
    ON public.auth_sessions (expires_at);
CREATE INDEX IF NOT EXISTS password_reset_tokens_user_id_idx
    ON public.password_reset_tokens (user_id);
CREATE INDEX IF NOT EXISTS retainer_checkouts_stripe_session_id_idx
    ON public.retainer_checkouts (stripe_session_id);

COMMENT ON TABLE public.platform_users IS
    'Operator and manufacturer portal logins (email / password)';
COMMENT ON TABLE public.auth_sessions IS
    'HttpOnly session tokens for desk and portal surfaces';
COMMENT ON TABLE public.password_reset_tokens IS
    'One-time password recovery tokens';
COMMENT ON TABLE public.retainer_checkouts IS
    'Stripe Checkout sessions for the hidden monthly retainer';

GRANT ALL ON TABLE public.platform_users TO anon, authenticated, service_role;
GRANT ALL ON TABLE public.auth_sessions TO anon, authenticated, service_role;
GRANT ALL ON TABLE public.password_reset_tokens TO anon, authenticated, service_role;
GRANT ALL ON TABLE public.retainer_checkouts TO anon, authenticated, service_role;

ALTER TABLE public.platform_users DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.auth_sessions DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.password_reset_tokens DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.retainer_checkouts DISABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';
