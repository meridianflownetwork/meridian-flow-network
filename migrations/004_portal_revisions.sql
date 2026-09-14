-- Client portal: revision requests on outreach drafts.
-- Additive. Safe to re-run.

ALTER TABLE public.matched_leads
    ADD COLUMN IF NOT EXISTS revision_note TEXT;

DO $$
DECLARE
    constraint_name text;
BEGIN
    SELECT con.conname INTO constraint_name
    FROM pg_constraint AS con
    JOIN pg_class AS rel ON rel.oid = con.conrelid
    JOIN pg_namespace AS nsp ON nsp.oid = rel.relnamespace
    WHERE nsp.nspname = 'public'
      AND rel.relname = 'matched_leads'
      AND con.contype = 'c'
      AND pg_get_constraintdef(con.oid) ILIKE '%approval_status%';

    IF constraint_name IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE public.matched_leads DROP CONSTRAINT %I',
            constraint_name
        );
    END IF;
END
$$;

ALTER TABLE public.matched_leads
    ADD CONSTRAINT matched_leads_approval_status_chk
    CHECK (
        approval_status IN (
            'draft',
            'approved',
            'rejected',
            'sent',
            'revision_requested'
        )
    );

COMMENT ON COLUMN public.matched_leads.revision_note IS
    'Optional note from the client portal when requesting a draft revision';

NOTIFY pgrst, 'reload schema';
