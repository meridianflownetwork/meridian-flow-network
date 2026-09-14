"""
LLM enrichment and buyer ↔ client matching engine.

1. Load scraped_buyers where status = pending_enrichment
2. Ask OpenAI or Claude to structure company, country, industry, specs
3. Write structured fields back with status = enriched
4. Score each enriched buyer against client_catalog
5. Insert qualified pairs into matched_leads

Usage:
    python enrich_and_match.py
    python enrich_and_match.py --limit 10
    python enrich_and_match.py --threshold 55
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from meridian.brand import banner, fail, info, kv_table, success, warn
from meridian.config import load_settings
from meridian.db import connect
from meridian.llm import LLMClient
from meridian.mock import mock_enrich
from meridian.scoring import match_score

ENRICH_SYSTEM = """
You are the enrichment desk for Meridian Flow Network, an elite introduction
network pairing boutique European precision manufacturers with enterprise
procurement in Southeast Asia and the Middle East.

Read messy procurement-page text and return ONE JSON object with exactly:
{
  "company_name": "cleaned legal or trading name",
  "country": "ISO-style English country name",
  "target_industry": "short industry label",
  "technical_requirements": "concise spec sheet: ISO/ATEX/PED/IATF codes, component classes, materials, tolerances",
  "iso_certifications": ["ISO 9001"],
  "component_classes": ["valve", "cnc"],
  "confidence": 0.0
}

Rules:
- Never invent certifications that are not implied by the source text.
- If country is unclear, use an empty string.
- technical_requirements must be a single plain-text block, not nested JSON.
- confidence is 0-1.
""".strip()


def enrich_prompt(row: dict[str, Any]) -> str:
    return (
        f"Current company_name hint: {row.get('company_name') or ''}\n\n"
        f"RAW TEXT:\n{(row.get('raw_text') or '')[:12000]}"
    )


def apply_enrichment(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    certs = payload.get("iso_certifications") or []
    classes = payload.get("component_classes") or []
    requirements = (payload.get("technical_requirements") or "").strip()
    extras = []
    if certs:
        extras.append("Certifications: " + ", ".join(str(item) for item in certs))
    if classes:
        extras.append("Component classes: " + ", ".join(str(item) for item in classes))
    if extras:
        requirements = (requirements + "\n" + "\n".join(extras)).strip()

    company = (payload.get("company_name") or row.get("company_name") or "Unknown buyer").strip()
    return {
        "company_name": company[:240],
        "country": (payload.get("country") or "").strip(),
        "target_industry": (payload.get("target_industry") or "").strip(),
        "technical_requirements": requirements,
        "status": "enriched",
    }


def load_pending(client, limit: int) -> list[dict[str, Any]]:
    query = (
        client.table("scraped_buyers")
        .select("id, company_name, country, target_industry, technical_requirements, raw_text, status")
        .eq("status", "pending_enrichment")
        .order("created_at", desc=False)
    )
    if limit:
        query = query.limit(limit)
    return query.execute().data or []


def load_clients(client) -> list[dict[str, Any]]:
    rows = (
        client.table("client_catalog")
        .select(
            "id, client_name, product_category, specs_json, target_regions, "
            "tenant_id, pipeline_enabled"
        )
        .execute()
        .data
        or []
    )
    return [row for row in rows if row.get("pipeline_enabled") is not False]


def existing_pairs(client) -> set[tuple[str, str]]:
    rows = client.table("matched_leads").select("buyer_id, client_id").execute().data or []
    return {(row["buyer_id"], row["client_id"]) for row in rows}


def enrich_row(llm: LLMClient | None, row: dict[str, Any], *, mock: bool) -> dict[str, Any]:
    if mock:
        return apply_enrichment(row, mock_enrich(row))
    assert llm is not None
    payload = llm.complete_json(system=ENRICH_SYSTEM, user=enrich_prompt(row), temperature=0.1)
    if not payload:
        raise ValueError("empty enrichment payload")
    return apply_enrichment(row, payload)


def score_buyer(buyer: dict[str, Any], clients: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
    leads: list[dict[str, Any]] = []
    for client in clients:
        score = match_score(
            buyer_country=buyer.get("country"),
            buyer_industry=buyer.get("target_industry"),
            buyer_requirements=buyer.get("technical_requirements"),
            product_category=client.get("product_category"),
            specs_json=client.get("specs_json") or {},
            target_regions=client.get("target_regions") or [],
        )
        if score < threshold:
            continue
        lead = {
            "buyer_id": buyer["id"],
            "client_id": client["id"],
            "match_score": score,
            "outreach_draft": None,
            "approval_status": "draft",
        }
        if client.get("tenant_id"):
            lead["tenant_id"] = client["tenant_id"]
        leads.append(lead)
    leads.sort(key=lambda item: item["match_score"], reverse=True)
    return leads


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enrich pending buyers and score them against the client catalog.")
    parser.add_argument("--limit", type=int, default=25, help="Max pending buyers to process.")
    parser.add_argument("--threshold", type=float, default=None, help="Minimum match score (0-100).")
    parser.add_argument("--max-matches", type=int, default=3, help="Top matches to keep per buyer.")
    parser.add_argument("--mock", action="store_true", help="Local enrichment (no OpenAI/Anthropic credits).")
    return parser.parse_args()


def main() -> None:
    banner("llm enrichment & matching engine")
    args = parse_args()
    settings = load_settings(require=("SUPABASE_URL", "SUPABASE_KEY"))
    mock = args.mock or settings.llm_provider == "mock"
    threshold = args.threshold if args.threshold is not None else settings.match_score_threshold
    kv_table(
        "MATCHING WINDOW",
        [
            ("PROVIDER", "mock" if mock else settings.llm_provider),
            ("THRESHOLD", f"{threshold}"),
            ("LIMIT", str(args.limit)),
        ],
    )

    if not mock and not settings.llm_ready:
        fail("enrich", "Set OPENAI_API_KEY or ANTHROPIC_API_KEY, or pass --mock")

    llm = None if mock else LLMClient(settings)
    if mock:
        info("enrich", "Using local mock enrichment (pre-credit mode)")
    client = connect(settings)
    pending = load_pending(client, args.limit)
    catalog = load_clients(client)
    if not pending:
        warn("enrich", "No buyers in pending_enrichment")
        return
    if not catalog:
        warn("match", "client_catalog is empty — run python setup_database.py --seed")

    info("enrich", f"{len(pending)} buyers queued · {len(catalog)} clients on the bench")
    known_pairs = existing_pairs(client)
    enriched_n = 0
    failed_n = 0
    inserted_n = 0

    for row in pending:
        label = row.get("company_name") or row["id"]
        try:
            update = enrich_row(llm, row, mock=mock)
            client.table("scraped_buyers").update(update).eq("id", row["id"]).execute()
            row.update(update)
            enriched_n += 1
            info("enrich", f"{label} -> {update['country'] or 'unknown country'} / {update['target_industry'] or 'n/a'}")
        except Exception as exc:
            failed_n += 1
            warn("enrich", f"{label} failed ({exc})")
            try:
                client.table("scraped_buyers").update({"status": "failed_enrichment"}).eq("id", row["id"]).execute()
            except Exception as status_exc:
                warn("enrich", f"Could not mark failed_enrichment ({status_exc})")
            continue

        if not catalog:
            continue
        qualified = score_buyer(row, catalog, threshold)[: args.max_matches]
        fresh = [
            lead
            for lead in qualified
            if (lead["buyer_id"], lead["client_id"]) not in known_pairs
        ]
        if not fresh:
            info("match", f"{label}: no new pairs above {threshold}")
            continue
        try:
            client.table("matched_leads").insert(fresh).execute()
            inserted_n += len(fresh)
            for lead in fresh:
                known_pairs.add((lead["buyer_id"], lead["client_id"]))
            preview = ", ".join(f"{item['match_score']}" for item in fresh)
            success("match", f"{label}: {len(fresh)} leads · scores {preview}")
        except Exception as exc:
            warn("match", f"{label} insert failed ({exc})")
            for lead in fresh:
                try:
                    client.table("matched_leads").insert(lead).execute()
                    inserted_n += 1
                    known_pairs.add((lead["buyer_id"], lead["client_id"]))
                except Exception as row_exc:
                    warn("match", f"Pair skipped ({row_exc})")

    kv_table(
        "RUN RESULT",
        [
            ("ENRICHED", str(enriched_n)),
            ("FAILED", str(failed_n)),
            ("NEW LEADS", str(inserted_n)),
        ],
    )
    success("match", "Enrichment and matching cycle complete")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
