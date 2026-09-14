#!/usr/bin/env python3
"""
Apply Meridian Flow Network DDL.

PostgREST / supabase-py cannot CREATE TABLE. This script:

  1. Bootstraps .venv from a real Python interpreter and installs requirements.txt
  2. Applies schema.sql over DATABASE_URL (or a URI built from SUPABASE_DB_PASSWORD)
  3. If no Postgres URI is present, prints a SQL Editor fallback and exits cleanly
  4. Optionally verifies the tables through the Data API

Usage:
    .\\setup_schema.ps1
    .\\setup_schema.ps1 --dry-run
    .\\setup_schema.ps1 --verify-only
    python setup_schema.py          (also bootstraps if the Store stub is avoided)
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


def inspect_postgres(database_url: str) -> None:
    import psycopg

    info("schema", "Inspecting keys, foreign keys, and indexes over Postgres")
    with psycopg.connect(database_url, connect_timeout=20) as conn:
        tables = conn.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = ANY(%s)
            ORDER BY table_name
            """,
            [list(TABLES)],
        ).fetchall()
        fks = conn.execute(
            """
            SELECT tc.table_name, kcu.column_name, ccu.table_name, rc.delete_rule
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema = tc.table_schema
            JOIN information_schema.referential_constraints rc
              ON rc.constraint_name = tc.constraint_name
            WHERE tc.table_schema = 'public'
              AND tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_name = 'matched_leads'
            """
        ).fetchall()
        indexes = conn.execute(
            """
            SELECT tablename, indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = ANY(%s)
            ORDER BY tablename, indexname
            """,
            [list(TABLES)],
        ).fetchall()
        defaults = conn.execute(
            """
            SELECT table_name, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND column_name = 'id'
              AND table_name = ANY(%s)
            """,
            [list(TABLES)],
        ).fetchall()

    kv_table(
        "POSTGRES CATALOG",
        [
            ("TABLES", ", ".join(row[0] for row in tables) or "none"),
            ("UUID DEFAULTS", ", ".join(f"{row[0]}={row[1]}" for row in defaults) or "none"),
            (
                "FKS",
                "; ".join(f"{row[0]}.{row[1]}->{row[2]} ON DELETE {row[3]}" for row in fks) or "none",
            ),
            ("INDEXES", str(len(indexes))),
        ],
    )

    if len(tables) < len(TABLES):
        fail("schema", "Postgres is missing one or more Meridian tables")
    if not all(row[1] and "gen_random_uuid" in row[1] for row in defaults):
        warn("schema", "One or more id columns are not defaulting to gen_random_uuid()")
    lead_fks = [row for row in fks if row[1] in ("buyer_id", "client_id")]
    if len(lead_fks) < 2 or any(row[3] != "CASCADE" for row in lead_fks):
        fail("schema", "matched_leads buyer_id/client_id foreign keys are missing or not ON DELETE CASCADE")


def probe_tables(client) -> list[str]:
    info("schema", "Verifying tables through the Data API")
    missing: list[str] = []
    for table in TABLES:
        try:
            client.table(table).select("id").limit(1).execute()
            success("schema", f"{table} reachable")
        except Exception as exc:
            message = str(exc).lower()
            if "could not find" in message or "pgrst205" in message or "does not exist" in message:
                missing.append(table)
                warn("schema", f"{table} missing from Data API")
            else:
                warn("schema", f"{table} exists but this API key cannot read it ({exc})")
    return missing


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply schema.sql via DATABASE_URL or print the SQL Editor fallback.")
    parser.add_argument("--dry-run", action="store_true", help="Print statements from schema.sql and exit.")
    parser.add_argument("--verify-only", action="store_true", help="Skip DDL; only probe tables via supabase-py.")
    parser.add_argument("--fallback", action="store_true", help="Print the SQL Editor fallback and exit.")
    return parser.parse_args()


def main() -> None:
    banner("schema bootstrap")
    args = parse_args()

    if args.dry_run:
        statements = split_sql(load_all_sql())
        info("schema", f"{SCHEMA_FILE} + migrations - {len(statements)} statements")
        sys.stdout.buffer.write(load_all_sql().encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n")
        return

    if args.fallback:
        print_fallback()
        sys.exit(2)

    settings = load_settings()
    candidates = settings.candidate_database_urls()
    database_url = candidates[0] if candidates else ""
    kv_table(
        "DDL CHANNEL",
        [
            ("INTERPRETER", sys.executable),
            ("SCHEMA", str(SCHEMA_FILE)),
            ("DATABASE_URL", "set" if database_url else "missing"),
            ("CANDIDATES", str(len(candidates))),
            ("DATA API", settings.project_ref or "not configured"),
        ],
    )

    if not args.verify_only:
        if candidates:
            try:
                database_url = apply_schema_with_fallback(candidates)
            except Exception as exc:
                error_text = str(exc)
                fail(
                    "schema",
                    f"Postgres DDL failed: {error_text}\n"
                    "Direct db.* hosts are IPv6-only; the script also tries the IPv4 pooler. "
                    "Or paste schema.sql into the SQL Editor.",
                )
        else:
            print_fallback()
            if not settings.supabase_url:
                sys.exit(2)

    if database_url:
        inspect_postgres(database_url)

    if settings.supabase_url and (settings.supabase_key or settings.supabase_service_role_key):
        client = connect(settings)
        missing = probe_tables(client)
        if missing:
            fail(
                "schema",
                "Unreachable tables: "
                + ", ".join(missing)
                + ". Add DATABASE_URL or run schema.sql in the SQL Editor.",
            )
        success("schema", "Schema is ready")
        return

    if not database_url:
        sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
