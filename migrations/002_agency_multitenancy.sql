-- Meridian Flow Network — multi-tenant / white-label agency upgrade
-- Additive only. Safe to re-run. Does not drop or rename existing tables.
-- Apply via:  python setup_schema.py
-- Or paste this file into the Supabase SQL Editor.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- 1. Agency tenants (white-label operators)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name TEXT NOT NULL,
    sender_name TEXT,
    reply_to_email TEXT,
    domain TEXT,
    portal_token UUID NOT NULL DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT tenants_portal_token_key UNIQUE (portal_token),
    CONSTRAINT tenants_reply_to_email_chk CHECK (
        reply_to_email IS NULL OR reply_to_email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'
    )
);

INSERT INTO public.tenants (id, company_name, sender_name, domain)
VALUES (
    '00000000-0000-4000-a000-000000000001',
    'Meridian Flow Network',
    'Introductions Desk',
    NULL
)
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2. client_catalog branding + portal token (existing rows kept)
-- ---------------------------------------------------------------------------

ALTER TABLE public.client_catalog
    ADD COLUMN IF NOT EXISTS tenant_id UUID
        REFERENCES public.tenants (id) ON DELETE SET NULL;
ALTER TABLE public.client_catalog
    ADD COLUMN IF NOT EXISTS company_name TEXT;
ALTER TABLE public.client_catalog
    ADD COLUMN IF NOT EXISTS sender_name TEXT;
ALTER TABLE public.client_catalog
    ADD COLUMN IF NOT EXISTS reply_to_email TEXT;
ALTER TABLE public.client_catalog
    ADD COLUMN IF NOT EXISTS domain TEXT;
ALTER TABLE public.client_catalog
    ADD COLUMN IF NOT EXISTS portal_token UUID DEFAULT gen_random_uuid();

UPDATE public.client_catalog
SET portal_token = gen_random_uuid()
WHERE portal_token IS NULL;

ALTER TABLE public.client_catalog
    ALTER COLUMN portal_token SET NOT NULL;
ALTER TABLE public.client_catalog
    ALTER COLUMN portal_token SET DEFAULT gen_random_uuid();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'client_catalog_portal_token_key'
          AND conrelid = 'public.client_catalog'::regclass
    ) THEN
        ALTER TABLE public.client_catalog
            ADD CONSTRAINT client_catalog_portal_token_key UNIQUE (portal_token);
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'client_catalog_reply_to_email_chk'
          AND conrelid = 'public.client_catalog'::regclass
    ) THEN
        ALTER TABLE public.client_catalog
            ADD CONSTRAINT client_catalog_reply_to_email_chk CHECK (
                reply_to_email IS NULL OR reply_to_email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'
            );
    END IF;
END
$$;

UPDATE public.client_catalog
SET company_name = client_name
WHERE company_name IS NULL OR btrim(company_name) = '';

UPDATE public.client_catalog
SET tenant_id = '00000000-0000-4000-a000-000000000001'
WHERE tenant_id IS NULL;

UPDATE public.client_catalog
SET sender_name = COALESCE(NULLIF(btrim(sender_name), ''), 'Introductions Desk')
WHERE sender_name IS NULL OR btrim(sender_name) = '';

-- ---------------------------------------------------------------------------
-- 3. matched_leads stay bound to client_id; denormalize tenant_id
-- ---------------------------------------------------------------------------

ALTER TABLE public.matched_leads
    ALTER COLUMN client_id SET NOT NULL;

ALTER TABLE public.matched_leads
    ADD COLUMN IF NOT EXISTS tenant_id UUID
        REFERENCES public.tenants (id) ON DELETE SET NULL;

UPDATE public.matched_leads AS leads
SET tenant_id = catalog.tenant_id
FROM public.client_catalog AS catalog
WHERE leads.client_id = catalog.id
  AND leads.tenant_id IS NULL;

CREATE OR REPLACE FUNCTION public.sync_matched_lead_tenant()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.client_id IS NULL THEN
        RAISE EXCEPTION 'matched_leads.client_id is required';
    END IF;
    SELECT tenant_id INTO NEW.tenant_id
    FROM public.client_catalog
    WHERE id = NEW.client_id;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS sync_matched_lead_tenant ON public.matched_leads;
CREATE TRIGGER sync_matched_lead_tenant
    BEFORE INSERT OR UPDATE OF client_id ON public.matched_leads
    FOR EACH ROW
    EXECUTE FUNCTION public.sync_matched_lead_tenant();

-- ---------------------------------------------------------------------------
-- 4. scraped_buyers signal fields (existing buyer rows unchanged)
-- ---------------------------------------------------------------------------

ALTER TABLE public.scraped_buyers
    ADD COLUMN IF NOT EXISTS signal_type TEXT;
ALTER TABLE public.scraped_buyers
    ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE public.scraped_buyers
    ADD COLUMN IF NOT EXISTS source_name TEXT;
ALTER TABLE public.scraped_buyers
    ADD COLUMN IF NOT EXISTS region TEXT;
ALTER TABLE public.scraped_buyers
    ADD COLUMN IF NOT EXISTS filing_reference TEXT;
ALTER TABLE public.scraped_buyers
    ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ;
ALTER TABLE public.scraped_buyers
    ADD COLUMN IF NOT EXISTS signal_payload JSONB NOT NULL DEFAULT '{}'::jsonb;

UPDATE public.scraped_buyers
SET signal_type = 'buyer_profile'
WHERE signal_type IS NULL;

ALTER TABLE public.scraped_buyers
    ALTER COLUMN signal_type SET DEFAULT 'buyer_profile';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'scraped_buyers_signal_type_chk'
          AND conrelid = 'public.scraped_buyers'::regclass
    ) THEN
        ALTER TABLE public.scraped_buyers
            ADD CONSTRAINT scraped_buyers_signal_type_chk CHECK (
                signal_type IN (
                    'buyer_profile',
                    'foreign_procurement_tender',
                    'regional_manufacturing_expansion',
                    'public_compliance_filing'
                )
            );
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- 5. tenders_log — discrete advanced signals
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.tenders_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    buyer_id UUID REFERENCES public.scraped_buyers (id) ON DELETE SET NULL,
    client_id UUID REFERENCES public.client_catalog (id) ON DELETE CASCADE,
    tenant_id UUID REFERENCES public.tenants (id) ON DELETE SET NULL,
    signal_type TEXT NOT NULL
        CHECK (
            signal_type IN (
                'foreign_procurement_tender',
                'regional_manufacturing_expansion',
                'public_compliance_filing'
            )
        ),
    title TEXT NOT NULL,
    country TEXT,
    region TEXT,
    source_url TEXT,
    source_name TEXT,
    filing_reference TEXT,
    published_at TIMESTAMPTZ,
    raw_text TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS set_updated_at ON public.tenants;
CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON public.tenants
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

DROP TRIGGER IF EXISTS set_updated_at ON public.tenders_log;
CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON public.tenders_log
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

CREATE OR REPLACE FUNCTION public.sync_tender_tenant()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.client_id IS NOT NULL AND NEW.tenant_id IS NULL THEN
        SELECT tenant_id INTO NEW.tenant_id
        FROM public.client_catalog
        WHERE id = NEW.client_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS sync_tender_tenant ON public.tenders_log;
CREATE TRIGGER sync_tender_tenant
    BEFORE INSERT OR UPDATE OF client_id ON public.tenders_log
    FOR EACH ROW
    EXECUTE FUNCTION public.sync_tender_tenant();

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS client_catalog_tenant_id_idx
    ON public.client_catalog (tenant_id);
CREATE INDEX IF NOT EXISTS tenants_domain_idx
    ON public.tenants (domain);

CREATE INDEX IF NOT EXISTS matched_leads_tenant_id_idx
    ON public.matched_leads (tenant_id);
CREATE INDEX IF NOT EXISTS matched_leads_client_tenant_idx
    ON public.matched_leads (client_id, tenant_id);

CREATE INDEX IF NOT EXISTS scraped_buyers_signal_type_idx
    ON public.scraped_buyers (signal_type);
CREATE INDEX IF NOT EXISTS scraped_buyers_region_idx
    ON public.scraped_buyers (region);
CREATE INDEX IF NOT EXISTS scraped_buyers_published_at_idx
    ON public.scraped_buyers (published_at DESC);
CREATE INDEX IF NOT EXISTS scraped_buyers_signal_payload_gin
    ON public.scraped_buyers USING GIN (signal_payload);

CREATE INDEX IF NOT EXISTS tenders_log_client_id_idx
    ON public.tenders_log (client_id);
CREATE INDEX IF NOT EXISTS tenders_log_tenant_id_idx
    ON public.tenders_log (tenant_id);
CREATE INDEX IF NOT EXISTS tenders_log_buyer_id_idx
    ON public.tenders_log (buyer_id);
CREATE INDEX IF NOT EXISTS tenders_log_signal_type_idx
    ON public.tenders_log (signal_type);
CREATE INDEX IF NOT EXISTS tenders_log_published_at_idx
    ON public.tenders_log (published_at DESC);
CREATE INDEX IF NOT EXISTS tenders_log_metadata_gin
    ON public.tenders_log USING GIN (metadata);

-- ---------------------------------------------------------------------------
-- Portal identity: x-client-portal-token header or JWT claims
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.current_portal_client_id()
RETURNS uuid
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    headers json;
    claims json;
    token text;
    claim_id text;
    found uuid;
BEGIN
    BEGIN
        headers := current_setting('request.headers', true)::json;
    EXCEPTION WHEN others THEN
        headers := NULL;
    END;
    BEGIN
        claims := current_setting('request.jwt.claims', true)::json;
    EXCEPTION WHEN others THEN
        claims := NULL;
    END;

    token := NULLIF(btrim(COALESCE(
        headers ->> 'x-client-portal-token',
        claims ->> 'portal_token'
    )), '');

    IF token IS NOT NULL THEN
        SELECT id INTO found
        FROM public.client_catalog
        WHERE portal_token::text = token
        LIMIT 1;
        IF found IS NOT NULL THEN
            RETURN found;
        END IF;
    END IF;

    claim_id := NULLIF(btrim(COALESCE(claims ->> 'client_id', '')), '');
    IF claim_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
        RETURN claim_id::uuid;
    END IF;
    RETURN NULL;
END;
$$;

REVOKE ALL ON FUNCTION public.current_portal_client_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.current_portal_client_id() TO anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- RLS: portal token sees only that client; operator desk (no token) unchanged
-- service_role bypasses RLS. Pipeline/dashboard keep working on the publishable key.
-- ---------------------------------------------------------------------------

ALTER TABLE public.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.client_catalog ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.matched_leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scraped_buyers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tenders_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenants_isolation ON public.tenants;
CREATE POLICY tenants_isolation ON public.tenants
    FOR ALL
    TO anon, authenticated
    USING (
        public.current_portal_client_id() IS NULL
        OR id = (
            SELECT tenant_id FROM public.client_catalog
            WHERE id = public.current_portal_client_id()
        )
    )
    WITH CHECK (public.current_portal_client_id() IS NULL);

DROP POLICY IF EXISTS client_catalog_isolation ON public.client_catalog;
CREATE POLICY client_catalog_isolation ON public.client_catalog
    FOR ALL
    TO anon, authenticated
    USING (
        public.current_portal_client_id() IS NULL
        OR id = public.current_portal_client_id()
    )
    WITH CHECK (
        public.current_portal_client_id() IS NULL
        OR id = public.current_portal_client_id()
    );

DROP POLICY IF EXISTS matched_leads_client_isolation ON public.matched_leads;
CREATE POLICY matched_leads_client_isolation ON public.matched_leads
    FOR ALL
    TO anon, authenticated
    USING (
        client_id IS NOT NULL
        AND (
            public.current_portal_client_id() IS NULL
            OR client_id = public.current_portal_client_id()
        )
    )
    WITH CHECK (
        client_id IS NOT NULL
        AND (
            public.current_portal_client_id() IS NULL
            OR client_id = public.current_portal_client_id()
        )
    );

DROP POLICY IF EXISTS scraped_buyers_isolation ON public.scraped_buyers;
CREATE POLICY scraped_buyers_isolation ON public.scraped_buyers
    FOR ALL
    TO anon, authenticated
    USING (
        public.current_portal_client_id() IS NULL
        OR EXISTS (
            SELECT 1
            FROM public.matched_leads AS leads
            WHERE leads.buyer_id = scraped_buyers.id
              AND leads.client_id = public.current_portal_client_id()
        )
    )
    WITH CHECK (public.current_portal_client_id() IS NULL);

DROP POLICY IF EXISTS tenders_log_client_isolation ON public.tenders_log;
CREATE POLICY tenders_log_client_isolation ON public.tenders_log
    FOR ALL
    TO anon, authenticated
    USING (
        public.current_portal_client_id() IS NULL
        OR client_id = public.current_portal_client_id()
    )
    WITH CHECK (
        public.current_portal_client_id() IS NULL
        OR client_id = public.current_portal_client_id()
    );

COMMENT ON TABLE public.tenants IS
    'White-label agency operators: branding, sending identity, portal token';
COMMENT ON TABLE public.tenders_log IS
    'Foreign tenders, regional expansions, and public compliance filings';
COMMENT ON COLUMN public.client_catalog.company_name IS
    'White-label company name shown on outreach and the client portal';
COMMENT ON COLUMN public.client_catalog.portal_token IS
    'Secret UUID presented as x-client-portal-token for RLS isolation';
COMMENT ON COLUMN public.matched_leads.client_id IS
    'Required FK -> client_catalog.id; RLS scopes portal reads to this client';
COMMENT ON COLUMN public.scraped_buyers.signal_type IS
    'buyer_profile or an advanced procurement / expansion / filing signal';

GRANT ALL ON TABLE public.tenants TO anon, authenticated, service_role;
GRANT ALL ON TABLE public.tenders_log TO anon, authenticated, service_role;
GRANT ALL ON TABLE public.scraped_buyers TO anon, authenticated, service_role;
GRANT ALL ON TABLE public.client_catalog TO anon, authenticated, service_role;
GRANT ALL ON TABLE public.matched_leads TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
