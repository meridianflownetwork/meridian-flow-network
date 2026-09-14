-- Public manufacturer onboarding inquiries.
-- Additive. Safe to re-run.

CREATE TABLE IF NOT EXISTS public.pending_inquiries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    email TEXT NOT NULL,
    country TEXT,
    website TEXT,
    product_category TEXT,
    target_regions TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    capabilities TEXT,
    message TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'reviewing', 'accepted', 'declined')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pending_inquiries_email_chk CHECK (
        email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'
    )
);

DROP TRIGGER IF EXISTS set_updated_at ON public.pending_inquiries;
CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON public.pending_inquiries
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

CREATE INDEX IF NOT EXISTS pending_inquiries_status_idx
    ON public.pending_inquiries (status);
CREATE INDEX IF NOT EXISTS pending_inquiries_created_at_idx
    ON public.pending_inquiries (created_at DESC);
CREATE INDEX IF NOT EXISTS pending_inquiries_email_idx
    ON public.pending_inquiries (email);

COMMENT ON TABLE public.pending_inquiries IS
    'Request Access submissions from the public landing page';

GRANT ALL ON TABLE public.pending_inquiries TO anon, authenticated, service_role;

ALTER TABLE public.pending_inquiries ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS pending_inquiries_insert ON public.pending_inquiries;
CREATE POLICY pending_inquiries_insert ON public.pending_inquiries
    FOR INSERT
    TO anon, authenticated
    WITH CHECK (true);

DROP POLICY IF EXISTS pending_inquiries_operator_read ON public.pending_inquiries;
CREATE POLICY pending_inquiries_operator_read ON public.pending_inquiries
    FOR SELECT
    TO anon, authenticated
    USING (public.current_portal_client_id() IS NULL);

DROP POLICY IF EXISTS pending_inquiries_operator_write ON public.pending_inquiries;
CREATE POLICY pending_inquiries_operator_write ON public.pending_inquiries
    FOR UPDATE
    TO anon, authenticated
    USING (public.current_portal_client_id() IS NULL)
    WITH CHECK (public.current_portal_client_id() IS NULL);

NOTIFY pgrst, 'reload schema';
