-- Meridian Flow Network — complete foundational schema
-- Paste this entire file into the Supabase SQL Editor (SQL -> New query -> Run)
-- if you do not have a DATABASE_URL. Safe to re-run.
-- Later upgrades live in migrations/ and are applied by setup_schema.py.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.scraped_buyers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name TEXT NOT NULL,
    country TEXT,
    target_industry TEXT,
    technical_requirements TEXT,
    raw_text TEXT,
    status TEXT NOT NULL DEFAULT 'pending_enrichment'
        CHECK (status IN ('pending_enrichment', 'enriched', 'failed_enrichment')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.client_catalog (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_name TEXT NOT NULL,
    product_category TEXT,
    specs_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    target_regions TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.matched_leads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    buyer_id UUID NOT NULL
        REFERENCES public.scraped_buyers (id) ON DELETE CASCADE,
    client_id UUID NOT NULL
        REFERENCES public.client_catalog (id) ON DELETE CASCADE,
    match_score NUMERIC NOT NULL
        CHECK (match_score >= 0 AND match_score <= 100),
    outreach_draft TEXT,
    approval_status TEXT NOT NULL DEFAULT 'draft'
        CHECK (approval_status IN ('draft', 'approved', 'rejected', 'sent', 'revision_requested')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT matched_leads_buyer_client_unique UNIQUE (buyer_id, client_id)
);

-- Columns for tables that already existed without timestamps / defaults
ALTER TABLE public.scraped_buyers
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE public.scraped_buyers
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE public.client_catalog
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE public.client_catalog
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE public.matched_leads
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE public.matched_leads
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- ---------------------------------------------------------------------------
-- updated_at trigger
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS set_updated_at ON public.scraped_buyers;
CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON public.scraped_buyers
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

DROP TRIGGER IF EXISTS set_updated_at ON public.client_catalog;
CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON public.client_catalog
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

DROP TRIGGER IF EXISTS set_updated_at ON public.matched_leads;
CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON public.matched_leads
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS scraped_buyers_status_idx
    ON public.scraped_buyers (status);
CREATE INDEX IF NOT EXISTS scraped_buyers_country_idx
    ON public.scraped_buyers (country);
CREATE INDEX IF NOT EXISTS scraped_buyers_target_industry_idx
    ON public.scraped_buyers (target_industry);
CREATE INDEX IF NOT EXISTS scraped_buyers_created_at_idx
    ON public.scraped_buyers (created_at DESC);

CREATE INDEX IF NOT EXISTS client_catalog_product_category_idx
    ON public.client_catalog (product_category);
CREATE INDEX IF NOT EXISTS client_catalog_specs_json_gin
    ON public.client_catalog USING GIN (specs_json);
CREATE INDEX IF NOT EXISTS client_catalog_target_regions_gin
    ON public.client_catalog USING GIN (target_regions);

CREATE INDEX IF NOT EXISTS matched_leads_buyer_id_idx
    ON public.matched_leads (buyer_id);
CREATE INDEX IF NOT EXISTS matched_leads_client_id_idx
    ON public.matched_leads (client_id);
CREATE INDEX IF NOT EXISTS matched_leads_approval_status_idx
    ON public.matched_leads (approval_status);
CREATE INDEX IF NOT EXISTS matched_leads_match_score_idx
    ON public.matched_leads (match_score DESC);

-- ---------------------------------------------------------------------------
-- Documentation + Data API grants
-- ---------------------------------------------------------------------------

COMMENT ON TABLE public.scraped_buyers IS
    'Raw and enriched international procurement targets';
COMMENT ON TABLE public.client_catalog IS
    'Manufacturing client specs and ISO certifications';
COMMENT ON TABLE public.matched_leads IS
    'Buyer-to-client matches with score and outreach draft';

COMMENT ON COLUMN public.scraped_buyers.id IS 'UUID primary key (gen_random_uuid)';
COMMENT ON COLUMN public.matched_leads.buyer_id IS 'FK -> scraped_buyers.id';
COMMENT ON COLUMN public.matched_leads.client_id IS 'FK -> client_catalog.id';

GRANT ALL ON TABLE public.scraped_buyers TO anon, authenticated, service_role;
GRANT ALL ON TABLE public.client_catalog TO anon, authenticated, service_role;
GRANT ALL ON TABLE public.matched_leads TO anon, authenticated, service_role;

-- Backend pipeline uses the publishable Data API key. Keep RLS off until
-- service-role auth is wired; otherwise inserts fail with 42501.
ALTER TABLE public.scraped_buyers DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.client_catalog DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.matched_leads DISABLE ROW LEVEL SECURITY;

NOTIFY pgrst, 'reload schema';
