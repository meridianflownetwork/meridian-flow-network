-- Enable RLS on auth and retainer tables left open by 006.
-- Additive. Safe to re-run. Operator Data API (no portal header) keeps
-- login, reset, and checkout working. A portal token sees only that house.

ALTER TABLE public.platform_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.auth_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.password_reset_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.retainer_checkouts ENABLE ROW LEVEL SECURITY;

-- platform_users: operator (no token) administers all identities.
-- Portal token may only read that house's portal login.
DROP POLICY IF EXISTS platform_users_operator ON public.platform_users;
CREATE POLICY platform_users_operator ON public.platform_users
    FOR ALL
    TO anon, authenticated
    USING (public.current_portal_client_id() IS NULL)
    WITH CHECK (public.current_portal_client_id() IS NULL);

DROP POLICY IF EXISTS platform_users_portal_read ON public.platform_users;
CREATE POLICY platform_users_portal_read ON public.platform_users
    FOR SELECT
    TO anon, authenticated
    USING (
        public.current_portal_client_id() IS NOT NULL
        AND role = 'portal'
        AND client_id = public.current_portal_client_id()
    );

-- auth_sessions: operator manages every session; portal sees only its users.
DROP POLICY IF EXISTS auth_sessions_operator ON public.auth_sessions;
CREATE POLICY auth_sessions_operator ON public.auth_sessions
    FOR ALL
    TO anon, authenticated
    USING (public.current_portal_client_id() IS NULL)
    WITH CHECK (public.current_portal_client_id() IS NULL);

DROP POLICY IF EXISTS auth_sessions_portal ON public.auth_sessions;
CREATE POLICY auth_sessions_portal ON public.auth_sessions
    FOR ALL
    TO anon, authenticated
    USING (
        public.current_portal_client_id() IS NOT NULL
        AND user_id IN (
            SELECT id
            FROM public.platform_users
            WHERE role = 'portal'
              AND client_id = public.current_portal_client_id()
        )
    )
    WITH CHECK (
        public.current_portal_client_id() IS NOT NULL
        AND user_id IN (
            SELECT id
            FROM public.platform_users
            WHERE role = 'portal'
              AND client_id = public.current_portal_client_id()
        )
    );

-- password_reset_tokens: same operator / house split.
DROP POLICY IF EXISTS password_reset_tokens_operator ON public.password_reset_tokens;
CREATE POLICY password_reset_tokens_operator ON public.password_reset_tokens
    FOR ALL
    TO anon, authenticated
    USING (public.current_portal_client_id() IS NULL)
    WITH CHECK (public.current_portal_client_id() IS NULL);

DROP POLICY IF EXISTS password_reset_tokens_portal ON public.password_reset_tokens;
CREATE POLICY password_reset_tokens_portal ON public.password_reset_tokens
    FOR ALL
    TO anon, authenticated
    USING (
        public.current_portal_client_id() IS NOT NULL
        AND user_id IN (
            SELECT id
            FROM public.platform_users
            WHERE role = 'portal'
              AND client_id = public.current_portal_client_id()
        )
    )
    WITH CHECK (
        public.current_portal_client_id() IS NOT NULL
        AND user_id IN (
            SELECT id
            FROM public.platform_users
            WHERE role = 'portal'
              AND client_id = public.current_portal_client_id()
        )
    );

-- retainer_checkouts: operator billing only. Portal tokens cannot read rows.
DROP POLICY IF EXISTS retainer_checkouts_operator ON public.retainer_checkouts;
CREATE POLICY retainer_checkouts_operator ON public.retainer_checkouts
    FOR ALL
    TO anon, authenticated
    USING (public.current_portal_client_id() IS NULL)
    WITH CHECK (public.current_portal_client_id() IS NULL);

COMMENT ON TABLE public.platform_users IS
    'Operator and manufacturer portal logins. RLS: operator full access; portal token reads own house only';
COMMENT ON TABLE public.auth_sessions IS
    'HttpOnly session tokens. RLS: operator full access; portal token scoped to that house';
COMMENT ON TABLE public.password_reset_tokens IS
    'One-time password recovery tokens. RLS: operator full access; portal token scoped to that house';
COMMENT ON TABLE public.retainer_checkouts IS
    'Stripe retainer Checkout sessions. RLS: operator only';

NOTIFY pgrst, 'reload schema';
