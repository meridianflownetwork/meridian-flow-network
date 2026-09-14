"""
Residential-proxy scraper pipeline.

Pulls public trade-registry and procurement-directory pages through an
Apify actor with Apify-managed RESIDENTIAL proxies, then inserts parsed
records into scraped_buyers as pending_enrichment.

Usage:
    python run_scraper.py
    python run_scraper.py --max-pages 8
    python run_scraper.py --fixture
    python run_scraper.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from meridian.brand import banner, fail, info, kv_table, success, warn
from meridian.config import load_settings
from meridian.db import chunked, connect
from meridian.signals import (
    SIGNAL_PROFILE,
    classify_signal,
    filing_reference,
    guess_country,
    guess_region,
    parse_published_at,
    source_name,
)
from meridian.targets import KIND_ALIASES, seed_kind_for_url, start_urls

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "sample_buyers.json"
SIGNAL_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "sample_signals.json"


def actor_input(urls: list[dict[str, Any]], max_pages: int) -> dict[str, Any]:
    return {
        "startUrls": urls,
        "maxCrawlPages": max_pages,
        "maxCrawlDepth": 2,
        "crawlerType": "playwright:adaptive",
        "respectRobotsTxtFile": True,
        "blockMedia": True,
        "proxyConfiguration": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"],
        },
    }


def source_url(item: dict[str, Any]) -> str:
    return str(item.get("url") or item.get("source") or "").strip()


def guess_company_name(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    title = str(metadata.get("title") or item.get("title") or "").strip()
    if title:
        return title.split("|")[0].split("—")[0].strip()[:240]
    host = urlparse(source_url(item)).hostname or "unknown-source"
    return host.replace("www.", "")


def item_text(item: dict[str, Any]) -> str:
    for key in ("text", "markdown", "readableText", "content"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(item, ensure_ascii=False)[:12000]


def seed_kind(item: dict[str, Any], url: str) -> str:
    user_data = item.get("userData") if isinstance(item.get("userData"), dict) else {}
    tagged = str(user_data.get("kind") or "").strip()
    if tagged:
        return tagged
    return seed_kind_for_url(url)


def parse_dataset_item(item: dict[str, Any]) -> dict[str, Any] | None:
    if item.get("company_name") and item.get("raw_text") and not item.get("text"):
        return enhance_parsed_row(item)
    text = item_text(item)
    url = source_url(item)
    if len(text) < 80:
        return None
    title = guess_company_name(item)
    kind = seed_kind(item, url)
    classification = classify_signal(text, url=url, title=title, seed_kind=kind)
    country = guess_country(text, url, title)
    region = guess_region(text, url, title, country)
    raw = f"SOURCE: {url}\nSIGNAL: {classification['signal_type']}\n\n{text}" if url else text
    return {
        "company_name": title[:240],
        "country": country,
        "target_industry": "",
        "technical_requirements": "",
        "raw_text": raw[:20000],
        "status": "pending_enrichment",
        "signal_type": classification["signal_type"],
        "source_url": url or None,
        "source_name": source_name(url),
        "region": region or None,
        "filing_reference": filing_reference(classification, title) or None,
        "published_at": parse_published_at(item),
        "signal_payload": classification,
    }


def enhance_parsed_row(row: dict[str, Any]) -> dict[str, Any]:
    """Add intent metadata to fixture rows that already look like scraped_buyers."""
    raw = str(row.get("raw_text") or "")
    url = ""
    if raw.startswith("SOURCE:"):
        url = raw.split("\n", 1)[0].replace("SOURCE:", "", 1).strip()
    title = str(row.get("company_name") or "")
    classification = classify_signal(raw, url=url, title=title, seed_kind=seed_kind_for_url(url))
    country = row.get("country") or guess_country(raw, url, title)
    payload = dict(row)
    payload.update(
        {
            "country": country,
            "signal_type": row.get("signal_type") or classification["signal_type"],
            "source_url": row.get("source_url") or url or None,
            "source_name": row.get("source_name") or source_name(url),
            "region": row.get("region") or guess_region(raw, url, title, country) or None,
            "filing_reference": row.get("filing_reference") or filing_reference(classification, title) or None,
            "published_at": row.get("published_at"),
            "signal_payload": row.get("signal_payload") or classification,
        }
    )
    return payload


def existing_sources(client) -> set[str]:
    seen: set[str] = set()
    try:
        rows = (
            client.table("scraped_buyers")
            .select("source_url, raw_text")
            .order("created_at", desc=True)
            .limit(500)
            .execute()
            .data
            or []
        )
    except Exception as exc:
        warn("scraper", f"Could not load existing buyers for dedupe ({exc})")
        return seen
    for row in rows:
        url = (row.get("source_url") or "").strip()
        if url:
            seen.add(url)
        raw = row.get("raw_text") or ""
        if raw.startswith("SOURCE:"):
            seen.add(raw.split("\n", 1)[0].replace("SOURCE:", "", 1).strip())
    return seen


def _tender_row(buyer: dict[str, Any]) -> dict[str, Any] | None:
    signal = buyer.get("signal_type") or SIGNAL_PROFILE
    if signal == SIGNAL_PROFILE:
        return None
    return {
        "buyer_id": buyer.get("id"),
        "signal_type": signal,
        "title": (buyer.get("company_name") or "Untitled signal")[:240],
        "country": buyer.get("country") or None,
        "region": buyer.get("region") or None,
        "source_url": buyer.get("source_url") or None,
        "source_name": buyer.get("source_name") or None,
        "filing_reference": buyer.get("filing_reference") or None,
        "published_at": buyer.get("published_at"),
        "raw_text": (buyer.get("raw_text") or "")[:8000] or None,
        "metadata": buyer.get("signal_payload") or {},
    }


def insert_buyers(client, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    inserted: list[dict[str, Any]] = []
    for batch in chunked(rows, 40):
        try:
            result = client.table("scraped_buyers").insert(batch).execute()
            inserted.extend(result.data or [])
        except Exception as exc:
            warn("scraper", f"Batch insert failed, retrying row-by-row ({exc})")
            for row in batch:
                try:
                    result = client.table("scraped_buyers").insert(row).execute()
                    inserted.extend(result.data or [row])
                except Exception as row_exc:
                    warn("scraper", f"Skipped {row.get('company_name')}: {row_exc}")
    tenders = [_tender_row(row) for row in inserted]
    tenders = [row for row in tenders if row]
    if tenders:
        try:
            client.table("tenders_log").insert(tenders).execute()
            success("scraper", f"Wrote {len(tenders)} rows to tenders_log")
        except Exception as exc:
            warn("scraper", f"tenders_log insert failed ({exc})")
    return len(inserted)


def _run_field(run: Any, *names: str) -> Any:
    """Read a field from an Apify Run pydantic model or a plain dict."""
    for name in names:
        if isinstance(run, dict) and run.get(name) is not None:
            return run[name]
        value = getattr(run, name, None)
        if value is not None:
            return value
        dump = getattr(run, "model_dump", None)
        if callable(dump):
            payload = dump(by_alias=True)
            if name in payload and payload[name] is not None:
                return payload[name]
    return None


def run_apify(settings, max_pages: int, kinds: list[str] | None = None) -> list[dict[str, Any]]:
    try:
        from apify_client import ApifyClient
    except ImportError as exc:
        fail("scraper", f"apify-client is not installed. pip install -r requirements.txt ({exc})")

    urls = start_urls(kinds)
    info("scraper", f"Starting {settings.apify_actor_id} · {len(urls)} seeds · max {max_pages} pages")
    info("scraper", "Proxy group RESIDENTIAL via Apify")

    client = ApifyClient(settings.apify_token)
    run = client.actor(settings.apify_actor_id).call(run_input=actor_input(urls, max_pages))
    if not run:
        fail("scraper", "Apify actor returned no run payload")

    dataset_id = _run_field(run, "defaultDatasetId", "default_dataset_id")
    if not dataset_id:
        fail("scraper", "Apify run has no defaultDatasetId")

    items = list(client.dataset(dataset_id).iterate_items())
    success("scraper", f"Apify delivered {len(items)} dataset items")
    return items


def load_apify_run(settings, run_id: str) -> list[dict[str, Any]]:
    from apify_client import ApifyClient

    client = ApifyClient(settings.apify_token)
    run = client.run(run_id).get()
    dataset_id = _run_field(run, "defaultDatasetId", "default_dataset_id")
    if not dataset_id:
        fail("scraper", f"Apify run {run_id} has no defaultDatasetId")
    items = list(client.dataset(dataset_id).iterate_items())
    success("scraper", f"Reused run {run_id} · {len(items)} dataset items")
    return items


def load_fixture(*, signals_only: bool = False) -> list[dict[str, Any]]:
    paths = [SIGNAL_FIXTURE_PATH] if signals_only else [FIXTURE_PATH, SIGNAL_FIXTURE_PATH]
    payload: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            if path == FIXTURE_PATH and not signals_only:
                fail("scraper", f"Fixture file missing: {path}")
            continue
        chunk = json.loads(path.read_text(encoding="utf-8"))
        payload.extend(chunk)
        info("scraper", f"Loaded {len(chunk)} rows from {path.name}")
    parsed = []
    for item in payload:
        row = parse_dataset_item(item)
        if row:
            parsed.append(row)
    return parsed


def parse_kinds(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    kinds: list[str] = []
    for item in raw.split(","):
        key = item.strip().lower()
        kind = KIND_ALIASES.get(key, key)
        if kind not in kinds:
            kinds.append(kind)
    return kinds


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape high-intent procurement signals via Apify residential proxies.")
    parser.add_argument("--max-pages", type=int, default=None, help="Cap crawl size (default SCRAPE_MAX_PAGES).")
    parser.add_argument("--fixture", action="store_true", help="Insert local sample buyers and signals instead of calling Apify.")
    parser.add_argument("--signals-only", action="store_true", help="With --fixture, load only high-intent signal samples.")
    parser.add_argument("--kinds", help="Comma list: tender,expansion,filing,profile")
    parser.add_argument("--dry-run", action="store_true", help="Parse only; do not write to Supabase.")
    parser.add_argument("--run-id", help="Reuse an existing Apify run dataset instead of starting a new crawl.")
    return parser.parse_args()


def main() -> None:
    banner("residential proxy & scraper pipeline")
    args = parse_args()
    settings = load_settings(require=("SUPABASE_URL", "SUPABASE_KEY"))
    if not args.fixture:
        if not settings.apify_token:
            fail("scraper", "APIFY_TOKEN is missing")

    max_pages = args.max_pages or settings.scrape_max_pages
    kinds = parse_kinds(args.kinds)
    seeds = start_urls(kinds)
    kv_table(
        "SCRAPE PLAN",
        [
            ("MODE", "fixture" if args.fixture else ("apify-reuse" if args.run_id else "apify-residential")),
            ("ACTOR", settings.apify_actor_id),
            ("MAX PAGES", str(max_pages)),
            ("SEEDS", str(len(seeds))),
            ("KINDS", ",".join(kinds) if kinds else "tender,expansion,filing"),
        ],
    )

    if args.fixture:
        parsed = load_fixture(signals_only=args.signals_only)
    elif args.run_id:
        items = load_apify_run(settings, args.run_id)
        parsed = []
        skipped = 0
        for item in items:
            row = parse_dataset_item(item)
            if row is None:
                skipped += 1
                continue
            parsed.append(row)
        if skipped:
            warn("scraper", f"Dropped {skipped} thin pages")
    else:
        items = run_apify(settings, max_pages, kinds)
        parsed = []
        skipped = 0
        for item in items:
            row = parse_dataset_item(item)
            if row is None:
                skipped += 1
                continue
            parsed.append(row)
        if skipped:
            warn("scraper", f"Dropped {skipped} thin pages")

    if args.dry_run:
        kinds_found = Counter(row.get("signal_type") or SIGNAL_PROFILE for row in parsed)
        kv_table("PARSED SIGNALS", [(key, str(value)) for key, value in kinds_found.items()])
        success("scraper", f"Dry-run parsed {len(parsed)} records — no writes")
        return

    client = connect(settings)
    known = existing_sources(client)
    fresh = []
    for row in parsed:
        header = (row.get("source_url") or "").strip()
        if not header:
            header = (row.get("raw_text") or "").split("\n", 1)[0].replace("SOURCE:", "", 1).strip()
        if header and header in known:
            continue
        fresh.append(row)

    if not fresh:
        warn("scraper", "Nothing new to insert")
        return

    inserted = insert_buyers(client, fresh)
    kinds_found = Counter(row.get("signal_type") or SIGNAL_PROFILE for row in fresh)
    kv_table("INSERTED SIGNALS", [(key, str(value)) for key, value in kinds_found.items()])
    success("scraper", f"Inserted {inserted} signals · status pending_enrichment")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
