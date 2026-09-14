"""£4,000 monthly retainer via tokenized Stripe Bacs Direct Debit Checkout."""

from __future__ import annotations

import os
import secrets
from typing import Any

from dotenv import load_dotenv

from meridian.brand import info, warn
from meridian.config import load_settings
from meridian.db import connect

DEFAULT_AMOUNT_PENCE = 400000
DEFAULT_CURRENCY = "gbp"
BACS_METHOD = "bacs_debit"
PLACEHOLDER_SECRETS = frozenset(
    {
        "",
        "sk_live_or_test",
        "sk_live_...",
        "sk_test_...",
        "pk_live_or_test",
        "pk_live_...",
        "whsec_",
        "your_secure_retainer_token_here",
    }
)
DEFAULT_LOOKUP_KEY = "retainer_monthly"
HANDLED_EVENTS = frozenset(
    {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
        "payment_intent.succeeded",
        "payment_intent.payment_failed",
        "invoice.paid",
        "invoice.payment_failed",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "mandate.updated",
    }
)


class BillingConfigError(RuntimeError):
    """A required billing environment variable is missing or still a placeholder."""


class BillingSignatureError(RuntimeError):
    """Stripe webhook signature could not be verified."""


class BillingGatewayError(RuntimeError):
    """Stripe accepted the request shape but could not create a session."""


def _reload_env() -> None:
    from pathlib import Path

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _env(name: str) -> str:
    _reload_env()
    return os.getenv(name, "").strip()


def _secret(name: str) -> str:
    value = _env(name)
    if value in PLACEHOLDER_SECRETS or value.endswith("..."):
        return ""
    return value


def retainer_amount_pence() -> int:
    raw = _env("RETAINER_AMOUNT_PENCE")
    if not raw:
        return DEFAULT_AMOUNT_PENCE
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_AMOUNT_PENCE
    return value if value > 0 else DEFAULT_AMOUNT_PENCE


def retainer_access_token() -> str:
    return _secret("RETAINER_ACCESS_TOKEN") or _env("RETAINER_ACCESS_TOKEN")


def access_granted(given: str | None) -> bool:
    expected = retainer_access_token()
    offered = (given or "").strip()
    if not expected or not offered:
        return False
    if len(expected) != len(offered):
        return False
    return secrets.compare_digest(expected, offered)


def stripe_secret_key() -> str:
    return _secret("STRIPE_SECRET_KEY")


def stripe_webhook_secret() -> str:
    return _secret("STRIPE_WEBHOOK_SECRET")


def stripe_ready() -> bool:
    return bool(stripe_secret_key())


def require_stripe_secret() -> str:
    key = stripe_secret_key()
    if not key:
        raise BillingConfigError("STRIPE_SECRET_KEY is missing")
    return key


def require_webhook_secret() -> str:
    secret = stripe_webhook_secret()
    if not secret:
        raise BillingConfigError("STRIPE_WEBHOOK_SECRET is missing")
    return secret


def _stripe_mod():
    import stripe

    return stripe


def _stripe_client():
    return _stripe_mod().StripeClient(require_stripe_secret())


def _client():
    return connect(load_settings(require=("SUPABASE_URL", "SUPABASE_KEY")))


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return dict(value)


def _ref(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("id") or "")
    return str(getattr(value, "id", "") or "")


def record_checkout(row: dict[str, Any]) -> None:
    try:
        _client().table("retainer_checkouts").insert(row).execute()
    except Exception as exc:
        warn("billing", f"Checkout row not stored ({exc})")


def _update_where(patch: dict[str, Any], column: str, value: str) -> bool:
    if not value:
        return False
    try:
        result = _client().table("retainer_checkouts").update(patch).eq(column, value).execute()
        return bool(result.data)
    except Exception as exc:
        warn("billing", f"Checkout update on {column} failed ({exc})")
        return False


def patch_checkout(patch: dict[str, Any], *, matches: dict[str, str]) -> bool:
    clean = {key: value for key, value in patch.items() if value is not None and value != ""}
    if not clean:
        return False
    order = (
        "stripe_session_id",
        "stripe_payment_intent_id",
        "stripe_mandate_id",
        "stripe_subscription_id",
        "stripe_customer_id",
    )
    for column in order:
        value = matches.get(column) or ""
        if _update_where(clean, column, value):
            return True
    return False


def retainer_lookup_key() -> str:
    return _env("STRIPE_LOOKUP_KEY") or DEFAULT_LOOKUP_KEY


def _price_id_from_row(row: Any) -> str:
    if isinstance(row, dict):
        return str(row.get("id") or "")
    return str(getattr(row, "id", "") or "")


def retainer_price_id(client: Any) -> str:
    lookup_key = retainer_lookup_key()
    listed = client.v1.prices.list(params={"lookup_keys": [lookup_key], "active": True, "limit": 1})
    data = _as_dict(listed).get("data") or []
    if data:
        found = _price_id_from_row(data[0])
        if found.startswith("price_"):
            return found
    configured = _env("STRIPE_RETAINER_PRICE_ID")
    if configured.startswith("price_") and not configured.endswith("..."):
        return configured
    raise BillingConfigError(f"No active price found for lookup key: {lookup_key}")


def _checkout_method_params() -> dict[str, Any]:
    configuration = _env("STRIPE_PAYMENT_METHOD_CONFIGURATION")
    if configuration.startswith("pmc_"):
        return {"payment_method_configuration": configuration}
    return {}


def _with_session_id(success_url: str) -> str:
    if "{CHECKOUT_SESSION_ID}" in success_url:
        return success_url
    joiner = "&" if "?" in success_url else "?"
    return f"{success_url}{joiner}session_id={{CHECKOUT_SESSION_ID}}"


def create_retainer_session(*, email: str, company_name: str, success_url: str, cancel_url: str) -> dict[str, Any]:
    client = _stripe_client()
    amount = retainer_amount_pence()
    metadata = {
        "kind": "monthly_retainer",
        "company_name": company_name or "",
        "billing_email": email,
    }
    customer_params: dict[str, Any] = {"email": email, "metadata": metadata}
    if company_name:
        customer_params["name"] = company_name
    try:
        customer = client.v1.customers.create(params=customer_params)
        session = client.v1.checkout.sessions.create(
            params={
                "mode": "subscription",
                "customer": customer.id,
                "locale": "en-GB",
                "billing_address_collection": "required",
                "success_url": _with_session_id(success_url),
                "cancel_url": cancel_url,
                "client_reference_id": email[:200],
                "metadata": metadata,
                "subscription_data": {
                    "description": "£4,000 monthly retainer — Bacs Direct Debit",
                    "metadata": metadata,
                },
                "line_items": [{"price": retainer_price_id(client), "quantity": 1}],
                **_checkout_method_params(),
            }
        )
    except BillingConfigError:
        raise
    except Exception as exc:
        warn("billing", f"Stripe Checkout session failed ({type(exc).__name__})")
        raise BillingGatewayError("Checkout could not be started") from exc

    url = getattr(session, "url", None) or _as_dict(session).get("url") or ""
    if not url:
        raise BillingGatewayError("Checkout could not be started")

    record_checkout(
        {
            "stripe_session_id": session.id,
            "stripe_customer_id": customer.id,
            "customer_email": email,
            "company_name": company_name or None,
            "amount_pence": amount,
            "currency": DEFAULT_CURRENCY,
            "status": "created",
            "last_event": "checkout.session.created",
        }
    )
    info("billing", "Stripe Bacs retainer Checkout session created")
    return {"id": session.id, "url": url}


def create_hosted_retainer_session(*, success_url: str, cancel_url: str) -> dict[str, Any]:
    client = _stripe_client()
    amount = retainer_amount_pence()
    metadata = {"kind": "monthly_retainer"}
    try:
        session = client.v1.checkout.sessions.create(
            params={
                "mode": "subscription",
                "locale": "en-GB",
                "billing_address_collection": "required",
                "success_url": _with_session_id(success_url),
                "cancel_url": cancel_url,
                "managed_payments": {"enabled": False},
                "metadata": metadata,
                "subscription_data": {
                    "description": "£4,000 monthly retainer — Bacs Direct Debit",
                    "metadata": metadata,
                },
                "line_items": [{"price": retainer_price_id(client), "quantity": 1}],
                **_checkout_method_params(),
            }
        )
    except BillingConfigError:
        raise
    except Exception as exc:
        warn("billing", f"Stripe Checkout session failed ({type(exc).__name__})")
        raise

    url = getattr(session, "url", None) or _as_dict(session).get("url") or ""
    if not url:
        raise BillingGatewayError("Checkout could not be started")

    record_checkout(
        {
            "stripe_session_id": session.id,
            "amount_pence": amount,
            "currency": DEFAULT_CURRENCY,
            "status": "created",
            "last_event": "checkout.session.created",
        }
    )
    info("billing", "Stripe hosted retainer Checkout session created")
    return {"id": session.id, "url": url}


def create_customer_portal(*, session_id: str, return_url: str) -> dict[str, Any]:
    sid = (session_id or "").strip()
    if not sid.startswith("cs_"):
        raise BillingGatewayError("Checkout session is missing")
    client = _stripe_client()
    try:
        session = client.v1.checkout.sessions.retrieve(sid)
        data = _as_dict(session)
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        kind = str(metadata.get("kind") or "")
        if kind and kind != "monthly_retainer":
            raise BillingGatewayError("Invoice portal is not ready yet")
        customer_id = _ref(data.get("customer"))
        if not customer_id:
            raise BillingGatewayError("Invoice portal is not ready yet")
        portal = client.v1.billing_portal.sessions.create(
            params={"customer": customer_id, "return_url": return_url}
        )
    except BillingConfigError:
        raise
    except BillingGatewayError:
        raise
    except Exception as exc:
        warn("billing", f"Customer Portal session failed ({type(exc).__name__})")
        raise BillingGatewayError("Invoice portal could not be opened") from exc

    url = getattr(portal, "url", None) or _as_dict(portal).get("url") or ""
    if not url:
        raise BillingGatewayError("Invoice portal could not be opened")
    return {"url": url}


def _customer_email(session: dict[str, Any]) -> str:
    details = session.get("customer_details")
    if isinstance(details, dict) and details.get("email"):
        return str(details["email"])
    return str(session.get("customer_email") or "")


def _mandate_id(intent: dict[str, Any]) -> str:
    direct = _ref(intent.get("mandate"))
    if direct:
        return direct
    options = intent.get("payment_method_options")
    if isinstance(options, dict):
        bacs = options.get("bacs_debit")
        if isinstance(bacs, dict):
            return _ref(bacs.get("mandate"))
    return ""


def _subscription_from_invoice(intent: dict[str, Any]) -> str:
    invoice = intent.get("invoice")
    if isinstance(invoice, dict):
        return _ref(invoice.get("subscription"))
    return ""


def _from_session(session: dict[str, Any], *, event_type: str) -> dict[str, Any]:
    payment_status = session.get("payment_status") or ""
    status = "completed"
    if event_type.endswith("async_payment_failed"):
        status = "failed"
    elif payment_status == "paid" or event_type.endswith("async_payment_succeeded"):
        status = "paid"
    elif session.get("status") == "expired":
        status = "canceled"
    return {
        "status": status,
        "last_event": event_type,
        "last_error": None,
        "stripe_session_id": session.get("id") or "",
        "stripe_customer_id": _ref(session.get("customer")),
        "stripe_subscription_id": _ref(session.get("subscription")),
        "stripe_payment_intent_id": _ref(session.get("payment_intent")),
        "customer_email": _customer_email(session),
    }


def _invoice_error(invoice: dict[str, Any]) -> str:
    for key in ("last_finalization_error", "last_payment_error"):
        error = invoice.get(key)
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str) and error:
            return error
    return str(invoice.get("status") or "")


def _from_invoice(invoice: dict[str, Any], *, event_type: str) -> dict[str, Any]:
    failed = event_type == "invoice.payment_failed"
    return {
        "status": "failed" if failed else "paid",
        "last_event": event_type,
        "last_error": _invoice_error(invoice) if failed else None,
        "stripe_customer_id": _ref(invoice.get("customer")),
        "stripe_subscription_id": _ref(invoice.get("subscription")),
        "stripe_payment_intent_id": _ref(invoice.get("payment_intent")),
    }


def _from_subscription(subscription: dict[str, Any], *, event_type: str) -> dict[str, Any]:
    raw = str(subscription.get("status") or "")
    if event_type.endswith("deleted") or raw in {"canceled", "incomplete_expired"}:
        status = "canceled"
    elif raw == "incomplete":
        status = "mandate_pending"
    elif raw in {"past_due", "unpaid"}:
        status = "failed"
    elif raw in {"active", "trialing"}:
        status = "paid"
    else:
        status = "mandate_updated"
    return {
        "status": status,
        "last_event": event_type,
        "stripe_subscription_id": subscription.get("id") or "",
        "stripe_customer_id": _ref(subscription.get("customer")),
    }


def _from_payment_intent(intent: dict[str, Any], *, event_type: str) -> dict[str, Any]:
    failed = event_type == "payment_intent.payment_failed"
    error = intent.get("last_payment_error") or {}
    if not isinstance(error, dict):
        error = _as_dict(error)
    message = error.get("message") or intent.get("status") or ""
    return {
        "status": "failed" if failed else "paid",
        "last_event": event_type,
        "last_error": message if failed else None,
        "stripe_payment_intent_id": intent.get("id") or "",
        "stripe_customer_id": _ref(intent.get("customer")),
        "stripe_mandate_id": _mandate_id(intent),
        "stripe_subscription_id": _subscription_from_invoice(intent),
    }


def _from_mandate(mandate: dict[str, Any]) -> dict[str, Any]:
    status = str(mandate.get("status") or "mandate_updated")
    mapped = "mandate_pending" if status in {"pending", "requires_confirmation"} else "mandate_updated"
    return {
        "status": mapped,
        "mandate_status": status,
        "last_event": "mandate.updated",
        "stripe_mandate_id": mandate.get("id") or "",
        "stripe_customer_id": _ref(mandate.get("customer")),
    }


def handle_webhook(payload: bytes, signature: str) -> dict[str, Any]:
    webhook_secret = require_webhook_secret()
    require_stripe_secret()
    if not signature:
        raise BillingSignatureError("stripe-signature header is missing")
    stripe = _stripe_mod()
    try:
        event = stripe.Webhook.construct_event(payload, signature, webhook_secret)
    except Exception as exc:
        raise BillingSignatureError("Invalid webhook") from exc

    event = _as_dict(event)
    event_type = str(event.get("type") or "")
    obj = _as_dict((event.get("data") or {}).get("object"))
    if event_type not in HANDLED_EVENTS:
        info("billing", f"Stripe event ignored · {event_type or 'unknown'}")
        return {"type": event_type, "handled": False}

    if event_type.startswith("checkout.session."):
        patch = _from_session(obj, event_type=event_type)
        matches = {
            "stripe_session_id": obj.get("id") or "",
            "stripe_customer_id": _ref(obj.get("customer")),
            "stripe_subscription_id": _ref(obj.get("subscription")),
        }
    elif event_type.startswith("payment_intent."):
        patch = _from_payment_intent(obj, event_type=event_type)
        matches = {
            "stripe_payment_intent_id": obj.get("id") or "",
            "stripe_customer_id": _ref(obj.get("customer")),
            "stripe_subscription_id": _subscription_from_invoice(obj),
        }
    elif event_type.startswith("invoice."):
        patch = _from_invoice(obj, event_type=event_type)
        matches = {
            "stripe_payment_intent_id": _ref(obj.get("payment_intent")),
            "stripe_subscription_id": _ref(obj.get("subscription")),
            "stripe_customer_id": _ref(obj.get("customer")),
        }
    elif event_type.startswith("customer.subscription."):
        patch = _from_subscription(obj, event_type=event_type)
        matches = {
            "stripe_subscription_id": obj.get("id") or "",
            "stripe_customer_id": _ref(obj.get("customer")),
        }
    else:
        patch = _from_mandate(obj)
        matches = {
            "stripe_mandate_id": obj.get("id") or "",
            "stripe_customer_id": _ref(obj.get("customer")),
        }

    applied = patch_checkout(patch, matches=matches)
    if not applied and event_type.startswith("checkout.session."):
        record_checkout(
            {
                "stripe_session_id": obj.get("id"),
                "stripe_customer_id": _ref(obj.get("customer")),
                "stripe_subscription_id": _ref(obj.get("subscription")),
                "customer_email": patch.get("customer_email"),
                "amount_pence": retainer_amount_pence(),
                "currency": DEFAULT_CURRENCY,
                "status": patch.get("status") or "completed",
                "last_event": event_type,
            }
        )
        applied = True
    info("billing", f"Stripe event · {event_type} · applied={applied}")
    return {"type": event_type, "handled": applied}


def parse_webhook(payload: bytes, signature: str) -> dict[str, Any] | None:
    return handle_webhook(payload, signature)
