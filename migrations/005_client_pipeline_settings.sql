-- Client management: pause a manufacturer's automated matching pipeline.
-- Additive. Safe to re-run.

ALTER TABLE public.client_catalog
    ADD COLUMN IF NOT EXISTS pipeline_enabled BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN public.client_catalog.pipeline_enabled IS
    'When false, enrich_and_match skips this client for new introductions';

CREATE INDEX IF NOT EXISTS client_catalog_pipeline_enabled_idx
    ON public.client_catalog (pipeline_enabled);

NOTIFY pgrst, 'reload schema';
