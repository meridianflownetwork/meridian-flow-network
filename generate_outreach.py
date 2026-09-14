"""
Multi-lingual introductory outreach generator.

For matched_leads in approval_status = draft, draft a culturally
calibrated introduction from Meridian Flow Network to the overseas
procurement director, then store it on outreach_draft.

Usage:
    python generate_outreach.py
    python generate_outreach.py --limit 15
    python generate_outreach.py --force
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from meridian.brand import banner, fail, info, kv_table, success, warn
from meridian.config import load_settings
from meridian.db import connect
from meridian.llm import LLMClient
from meridian.mock import mock_outreach
from meridian.scoring import country_region

LANGUAGE_BY_COUNTRY = {
    "united arab emirates": ("Arabic", "Gulf business Arabic with a parallel English close"),
    "uae": ("Arabic", "Gulf business Arabic with a parallel English close"),
    "saudi arabia": ("Arabic", "formal Najdi/Gulf commercial Arabic plus English"),
    "ksa": ("Arabic", "formal Najdi/Gulf commercial Arabic plus English"),
    "qatar": ("Arabic", "formal Gulf commercial Arabic plus English"),
    "kuwait": ("Arabic", "formal Gulf commercial Arabic plus English"),
    "bahrain": ("Arabic", "formal Gulf commercial Arabic plus English"),
    "oman": ("Arabic", "formal Gulf commercial Arabic plus English"),
    "egypt": ("Arabic", "formal Egyptian commercial Arabic plus English"),
    "jordan": ("Arabic", "formal Levantine commercial Arabic plus English"),
    "iraq": ("Arabic", "formal commercial Arabic plus English"),
    "thailand": ("Thai", "polite formal Thai with an English version beneath"),
    "vietnam": ("Vietnamese", "formal Vietnamese with an English version beneath"),
    "indonesia": ("Bahasa Indonesia", "formal Bahasa Indonesia with an English version beneath"),
    "malaysia": ("Malay", "formal Malay and English — English may lead"),
    "singapore": ("English", "precise international English, no slang"),
    "philippines": ("English", "formal Philippine business English"),
    "cambodia": ("English", "clear international English; Khmer only if natural"),
    "brunei": ("English", "formal English with optional Malay greeting"),
}

LANGUAGE_BY_REGION = {
    "middle east": ("Arabic", "formal Gulf commercial Arabic plus English"),
    "southeast asia": ("English", "clear ASEAN business English"),
}

OUTREACH_SYSTEM = """
You are the private introduction desk of Meridian Flow Network.
The network quietly connects boutique European precision-engineering
houses with enterprise procurement directors in Southeast Asia and
the Middle East. Tone: restrained, exact, never salesy. No emojis.
No exclamation marks. No fabricated claims, prices, or certifications.

Return ONE JSON object:
{
  "language": "primary language used",
  "subject": "email subject",
  "body": "full email including greeting, 3 short paragraphs, sign-off"
}

The email must:
- Address a procurement director / category manager, not a generic inbox.
- Open with a culturally correct greeting for the target language.
- Name the buyer organisation and the European manufacturer.
- Reference only the supplied technical overlap.
- Position Meridian as a discreet introduction network, not a broker blast.
- Invite a 20-minute technical diligence call.
- Close as: Meridian Flow Network / Introductions Desk
- If a non-English language is requested, write the primary letter in that
  language and append a faithful English rendering under a short divider.
""".strip()


def language_for(country: str | None) -> tuple[str, str]:
    key = (country or "").strip().lower()
    if key in LANGUAGE_BY_COUNTRY:
        return LANGUAGE_BY_COUNTRY[key]
    region = country_region(country)
    return LANGUAGE_BY_REGION.get(region, ("English", "formal international English"))


def _as_record(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return value[0] if value else {}
    return value if isinstance(value, dict) else {}


def load_drafts(client, limit: int, force: bool) -> list[dict[str, Any]]:
    query = (
        client.table("matched_leads")
        .select("id, buyer_id, client_id, match_score, outreach_draft, approval_status, revision_note")
        .in_("approval_status", ["draft", "revision_requested"])
        .order("match_score", desc=True)
    )
    if limit:
        query = query.limit(limit)
    rows = query.execute().data or []
    if not force:
        rows = [row for row in rows if not (row.get("outreach_draft") or "").strip()]
    return hydrate_leads(client, rows)


def hydrate_leads(client, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    buyer_ids = list({row["buyer_id"] for row in rows if row.get("buyer_id")})
    client_ids = list({row["client_id"] for row in rows if row.get("client_id")})
    buyers: dict[str, Any] = {}
    if buyer_ids:
        buyers = {
            item["id"]: item
            for item in (
                client.table("scraped_buyers")
                .select("id, company_name, country, target_industry, technical_requirements")
                .in_("id", buyer_ids)
                .execute()
                .data
                or []
            )
        }
    manufacturers: dict[str, Any] = {}
    if client_ids:
        manufacturers = {
            item["id"]: item
            for item in (
                client.table("client_catalog")
                .select("id, client_name, product_category, specs_json, target_regions")
                .in_("id", client_ids)
                .execute()
                .data
                or []
            )
        }
    hydrated: list[dict[str, Any]] = []
    for row in rows:
        row["scraped_buyers"] = buyers.get(row.get("buyer_id"), {})
        row["client_catalog"] = manufacturers.get(row.get("client_id"), {})
        hydrated.append(row)
    return hydrated


def flatten_lead(row: dict[str, Any]) -> dict[str, Any] | None:
    buyer = _as_record(row.get("scraped_buyers"))
    manufacturer = _as_record(row.get("client_catalog"))
    if not buyer or not manufacturer:
        return None
    return {
        "id": row["id"],
        "match_score": row.get("match_score"),
        "buyer": buyer,
        "client": manufacturer,
    }


def outreach_user(lead: dict[str, Any]) -> str:
    buyer = lead["buyer"]
    client = lead["client"]
    language, register = language_for(buyer.get("country"))
    specs = client.get("specs_json") or {}
    return (
        f"Primary language: {language}\n"
        f"Register: {register}\n"
        f"Match score: {lead.get('match_score')}\n\n"
        f"BUYER\n"
        f"Company: {buyer.get('company_name')}\n"
        f"Country: {buyer.get('country')}\n"
        f"Industry: {buyer.get('target_industry')}\n"
        f"Requirements: {buyer.get('technical_requirements')}\n\n"
        f"EUROPEAN CLIENT\n"
        f"Name: {client.get('client_name')}\n"
        f"Category: {client.get('product_category')}\n"
        f"Specs: {specs}\n"
        f"Target regions: {client.get('target_regions')}\n"
    )


def format_draft(payload: dict[str, Any]) -> str:
    subject = (payload.get("subject") or "Introduction — Meridian Flow Network").strip()
    language = (payload.get("language") or "English").strip()
    body = (payload.get("body") or "").strip()
    if not body:
        raise ValueError("model returned an empty body")
    return f"Subject: {subject}\nLanguage: {language}\n\n{body}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate multi-lingual outreach drafts for unmatched introductions.")
    parser.add_argument("--limit", type=int, default=20, help="Max draft leads to process.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing outreach_draft text.")
    parser.add_argument("--mock", action="store_true", help="Fill drafts from local templates (no LLM credits).")
    return parser.parse_args()


def main() -> None:
    banner("multi-lingual outreach generator")
    args = parse_args()
    settings = load_settings(require=("SUPABASE_URL", "SUPABASE_KEY"))
    mock = args.mock or settings.llm_provider == "mock"
    if not mock and not settings.llm_ready:
        fail("outreach", "Set OPENAI_API_KEY or ANTHROPIC_API_KEY, or pass --mock")

    llm = None if mock else LLMClient(settings)
    if mock:
        info("outreach", "Using local mock templates (pre-credit mode)")
    client = connect(settings)
    rows = load_drafts(client, args.limit, args.force)
    if not rows:
        warn("outreach", "No draft leads waiting for copy")
        return

    info("outreach", f"{len(rows)} introductions queued")
    written = 0
    skipped = 0

    for row in rows:
        lead = flatten_lead(row)
        if lead is None:
            skipped += 1
            warn("outreach", f"{row.get('id')}: missing buyer or client join")
            continue
        buyer_name = lead["buyer"].get("company_name") or lead["id"]
        language, _ = language_for(lead["buyer"].get("country"))
        try:
            payload = (
                mock_outreach(lead)
                if mock
                else llm.complete_json(
                    system=OUTREACH_SYSTEM,
                    user=outreach_user(lead),
                    temperature=0.4,
                )
            )
            draft = format_draft(payload)
            client.table("matched_leads").update(
                {
                    "outreach_draft": draft,
                    "approval_status": "draft",
                    "revision_note": None,
                }
            ).eq("id", lead["id"]).execute()
            written += 1
            success("outreach", f"{buyer_name} · {language} · score {lead.get('match_score')}")
        except Exception as exc:
            skipped += 1
            warn("outreach", f"{buyer_name} failed ({exc})")

    kv_table(
        "OUTREACH DESK",
        [
            ("WRITTEN", str(written)),
            ("SKIPPED", str(skipped)),
        ],
    )
    success("outreach", "Draft cycle complete — approval_status remains draft")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
