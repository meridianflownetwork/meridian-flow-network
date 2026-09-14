"""
Create / verify Meridian tables and optionally seed client_catalog.

DDL is applied from schema.sql through setup_schema logic (DATABASE_URL).
This file keeps the --seed entrypoint used by the matching pipeline.

Usage:
    python setup_database.py --seed
    python setup_database.py --verify-only
    python setup_database.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bootstrap import ensure_runtime

ensure_runtime(reexec_script=str(Path(__file__).resolve()))

from meridian.brand import banner, fail, info, kv_table, success, warn
from meridian.config import load_settings
from meridian.db import TABLES, connect
from meridian.sql import SCHEMA_FILE, apply_schema_with_fallback, load_all_sql, print_fallback, split_sql
from meridian.auth import ensure_bootstrap_identities
from meridian.portal_fixtures import ensure_revision_requested_lead
from meridian.targets import SEED_CLIENTS


def probe_tables(client) -> list[str]:
    info("database", "Verifying tables through the Data API")
    missing: list[str] = []
    for table in TABLES:
        try:
            client.table(table).select("id").limit(1).execute()
            success("database", f"{table} reachable")
        except Exception as exc:
            message = str(exc).lower()
            if "could not find" in message or "pgrst205" in message or "does not exist" in message:
                missing.append(table)
                warn("database", f"{table} missing from Data API")
            else:
                warn("database", f"{table} exists but this API key cannot read it ({exc})")
    return missing


def seed_clients(client) -> None:
    existing = client.table("client_catalog").select("client_name").execute().data or []
    known = {row["client_name"] for row in existing}
    fresh = []
    for row in SEED_CLIENTS:
        if row["client_name"] in known:
            continue
        payload = {key: value for key, value in row.items() if value is not None}
        fresh.append(payload)
    if not fresh:
        info("database", "Client catalog already seeded")
        return
    client.table("client_catalog").insert(fresh).execute()
    success("database", f"Seeded {len(fresh)} European manufacturing clients")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply schema.sql and optionally seed client_catalog.")
    parser.add_argument("--dry-run", action="store_true", help="Print schema.sql and exit.")
    parser.add_argument("--verify-only", action="store_true", help="Skip DDL; only probe tables.")
    parser.add_argument("--seed", action="store_true", help="Insert boutique European client profiles if missing.")
    return parser.parse_args()


def main() -> None:
    banner("database & configuration setup")
    args = parse_args()

    if args.dry_run:
        statements = split_sql(load_all_sql())
        info("database", f"{SCHEMA_FILE} + migrations - {len(statements)} statements")
        sys.stdout.buffer.write(load_all_sql().encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n")
        return

    settings = load_settings(require=("SUPABASE_URL", "SUPABASE_KEY"))
    candidates = settings.candidate_database_urls()
    kv_table(
        "CONNECTION",
        [
            ("INTERPRETER", sys.executable),
            ("PROJECT", settings.project_ref),
            ("DDL CHANNEL", "postgres" if candidates else "none - SQL Editor fallback"),
        ],
    )

    if not args.verify_only:
        if candidates:
            try:
                apply_schema_with_fallback(candidates)
            except Exception as exc:
                if args.seed:
                    warn("database", f"DDL skipped ({exc}); seeding if tables already exist")
                else:
                    fail("database", f"Postgres DDL failed: {exc}")
        else:
            print_fallback()

    client = connect(settings)
    missing = probe_tables(client)
    if missing:
        fail(
            "database",
            "Unreachable tables: "
            + ", ".join(missing)
            + ". Add DATABASE_URL or paste schema.sql into the Supabase SQL Editor.",
        )
    if args.seed:
        seed_clients(client)
        fixture = ensure_revision_requested_lead(client)
        if fixture:
            success("database", "Revision-requested portal sample lead is in place")
        try:
            ensure_bootstrap_identities()
        except Exception as exc:
            warn("database", f"Login identities not seeded ({exc})")
    success("database", "Foundational schema is ready")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
