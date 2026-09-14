-- Extra Stripe identifiers and statuses for Bacs Direct Debit webhooks.
-- Additive. Safe to re-run.

ALTER TABLE public.retainer_checkouts
    ADD COLUMN IF NOT EXISTS stripe_payment_intent_id TEXT;
ALTER TABLE public.retainer_checkouts
    ADD COLUMN IF NOT EXISTS stripe_mandate_id TEXT;
ALTER TABLE public.retainer_checkouts
    ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT;
ALTER TABLE public.retainer_checkouts
    ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT;
ALTER TABLE public.retainer_checkouts
    ADD COLUMN IF NOT EXISTS mandate_status TEXT;
ALTER TABLE public.retainer_checkouts
    ADD COLUMN IF NOT EXISTS last_event TEXT;
ALTER TABLE public.retainer_checkouts
    ADD COLUMN IF NOT EXISTS last_error TEXT;

ALTER TABLE public.retainer_checkouts
    DROP CONSTRAINT IF EXISTS retainer_checkouts_status_check;

ALTER TABLE public.retainer_checkouts
    ADD CONSTRAINT retainer_checkouts_status_check
    CHECK (status IN (
        'created',
        'completed',
        'canceled',
        'paid',
        'failed',
        'mandate_pending',
        'mandate_updated'
    ));

CREATE INDEX IF NOT EXISTS retainer_checkouts_payment_intent_idx
    ON public.retainer_checkouts (stripe_payment_intent_id);
CREATE INDEX IF NOT EXISTS retainer_checkouts_mandate_idx
    ON public.retainer_checkouts (stripe_mandate_id);
CREATE INDEX IF NOT EXISTS retainer_checkouts_customer_idx
    ON public.retainer_checkouts (stripe_customer_id);
CREATE INDEX IF NOT EXISTS retainer_checkouts_subscription_idx
    ON public.retainer_checkouts (stripe_subscription_id);

NOTIFY pgrst, 'reload schema';
