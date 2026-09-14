"""Idempotent portal demo fixtures — revision_requested sample leads."""

from __future__ import annotations

from typing import Any

DEFAULT_TENANT_ID = "00000000-0000-4000-a000-000000000001"

REVISION_LEAD_NAME = "PETRONAS Downstream Maintenance Upgrade — Package B"
REVISION_SCORE = 88.42
REVISION_FEEDBACK = (
    "Adjust introduction to emphasize ISO 10418 compliance and shorten "
    "valve-train lead time reference to 6 weeks."
)
REVISION_DRAFT_STATUS = "Revision pending copy update by system agent"

REVISION_DRAFT = (
    f"Subject: Technical introduction — {REVISION_LEAD_NAME}\n"
    "Language: English\n\n"
    f"Draft status: {REVISION_DRAFT_STATUS}\n\n"
    "Dear Procurement,\n\n"
    "This introduction is held for revision. The applications engineer will "
    "update the letter to name ISO 10418 on the hydrocarbon valve train and "
    "to state a six-week lead time. This is not a bid. It is an offer of a "
    "20-minute diligence call.\n"
)


def _first_active_client(db) -> dict[str, Any] | None:
    rows = (
        db.table("client_catalog")
        .select("id, client_name, tenant_id, portal_token, pipeline_enabled")
        .order("client_name")
        .execute()
        .data
        or []
    )
    active = [
        row
        for row in rows
        if row.get("portal_token") and row.get("pipeline_enabled") is not False
    ]
    return active[0] if active else None


def ensure_revision_requested_lead(
    db,
    client_id: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any] | None:
    """Attach one realistic revision_requested lead to the active portal house."""
    client = None
    if client_id:
        found = (
            db.table("client_catalog")
            .select("id, client_name, tenant_id")
            .eq("id", client_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        client = found[0] if found else None
    if client is None:
        client = _first_active_client(db)
    if client is None:
        return None

    house_id = client["id"]
    house_tenant = tenant_id or client.get("tenant_id") or DEFAULT_TENANT_ID
    buyer_payload = {
        "company_name": REVISION_LEAD_NAME,
        "country": "Malaysia",
        "target_industry": "oil, gas and petrochemicals",
        "technical_requirements": (
            "Downstream maintenance upgrade, Package B: hydrocarbon valve train, "
            "ISO 10418 / ATEX process isolation, six-week lead time on replacement "
            "manifolds. Required: ISO 9001, ISO 10418, PED."
        ),
        "raw_text": (
            f"{REVISION_LEAD_NAME}. PETRONAS downstream turnaround package. "
            "Valve-train and manifold pre-qualification."
        ),
        "status": "enriched",
        "signal_type": "foreign_procurement_tender",
        "source_name": "PETRONAS",
        "region": "Southeast Asia",
        "filing_reference": "PKG-B-10418",
    }
    existing_buyer = (
        db.table("scraped_buyers")
        .select("id")
        .eq("company_name", REVISION_LEAD_NAME)
        .limit(1)
        .execute()
        .data
        or []
    )
    if existing_buyer:
        buyer_id = existing_buyer[0]["id"]
        db.table("scraped_buyers").update(buyer_payload).eq("id", buyer_id).execute()
    else:
        created = db.table("scraped_buyers").insert(buyer_payload).execute().data or []
        if not created:
            return None
        buyer_id = created[0]["id"]

    lead_payload = {
        "buyer_id": buyer_id,
        "client_id": house_id,
        "tenant_id": house_tenant,
        "match_score": REVISION_SCORE,
        "approval_status": "revision_requested",
        "revision_note": REVISION_FEEDBACK,
        "outreach_draft": REVISION_DRAFT,
    }
    existing_lead = (
        db.table("matched_leads")
        .select("id")
        .eq("buyer_id", buyer_id)
        .eq("client_id", house_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if existing_lead:
        db.table("matched_leads").update(lead_payload).eq("id", existing_lead[0]["id"]).execute()
        lead_id = existing_lead[0]["id"]
    else:
        created_lead = db.table("matched_leads").insert(lead_payload).execute().data or []
        if not created_lead:
            return None
        lead_id = created_lead[0]["id"]
    return {"buyer_id": buyer_id, "lead_id": lead_id, "client_id": house_id}
