"""Load and apply schema.sql over a direct Postgres URI — never via PostgREST."""

from __future__ import annotations

import re
from pathlib import Path

from meridian.brand import error, info, success, warn

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = ROOT / "schema.sql"
MIGRATIONS_DIR = ROOT / "migrations"


def load_schema_sql() -> str:
    if not SCHEMA_FILE.exists():
        raise FileNotFoundError(f"schema.sql is missing at {SCHEMA_FILE}")
    return SCHEMA_FILE.read_text(encoding="utf-8")


def migration_files() -> list[Path]:
    if not MIGRATIONS_DIR.exists():
        return []
    return sorted(path for path in MIGRATIONS_DIR.glob("*.sql") if path.is_file())


def load_all_sql() -> str:
    parts = [load_schema_sql()]
    for path in migration_files():
        parts.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def split_sql(script: str) -> list[str]:
    """Split a SQL script on semicolons, respecting quotes and $dollar$ bodies."""
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(script)
    in_single = False
    dollar_tag: str | None = None

    while i < n:
        if dollar_tag is None and not in_single and script.startswith("--", i):
            newline = script.find("\n", i)
            i = n if newline < 0 else newline + 1
            continue
        if dollar_tag is None and not in_single and script.startswith("/*", i):
            end = script.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if in_single:
            buf.append(script[i])
            if script[i] == "'":
                if i + 1 < n and script[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if dollar_tag is None and script[i] == "'":
            buf.append("'")
            in_single = True
            i += 1
            continue
        if dollar_tag is None and script[i] == "$":
            match = re.match(r"\$[A-Za-z0-9_]*\$", script[i:])
            if match:
                dollar_tag = match.group(0)
                buf.append(dollar_tag)
                i += len(dollar_tag)
                continue
        if dollar_tag is not None:
            if script.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
            buf.append(script[i])
            i += 1
            continue
        if script[i] == ";":
            statement = "".join(buf).strip()
            if statement:
                statements.append(statement)
            buf = []
            i += 1
            continue
        buf.append(script[i])
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def apply_schema_via_database_url(database_url: str) -> int:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "psycopg is required for DDL. Re-run setup_schema.py so it can install requirements.txt."
        ) from exc

    from urllib.parse import urlparse

    host = urlparse(database_url).hostname or "postgres"
    extra = ", ".join(path.name for path in migration_files())
    statements = split_sql(load_all_sql())
    label = SCHEMA_FILE.name + (f" + {extra}" if extra else "")
    info("schema", f"Applying {len(statements)} statements from {label} via {host}")
    with psycopg.connect(database_url, connect_timeout=20) as conn:
        with conn.transaction():
            for statement in statements:
                conn.execute(statement)
    success("schema", f"Schema committed via {host}")
    return len(statements)


def apply_schema_with_fallback(urls: list[str]) -> str:
    last_error: Exception | None = None
    for url in urls:
        from urllib.parse import urlparse

        host = urlparse(url).hostname or "postgres"
        try:
            apply_schema_via_database_url(url)
            return url
        except Exception as exc:
            warn("schema", f"{host} unavailable ({exc})")
            last_error = exc
    if last_error:
        raise last_error
    raise RuntimeError("No database URLs to try")


def fallback_instructions() -> str:
    return (
        "supabase-py / PostgREST cannot run CREATE TABLE with a publishable API key.\n"
        "\n"
        "Use one of these paths:\n"
        "\n"
        "  A) Direct Postgres (preferred)\n"
        "     Supabase -> Project Settings -> Database -> Connection string -> URI\n"
        "     Use the direct connection (port 5432), then add to .env:\n"
        "       DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@db.<ref>.supabase.co:5432/postgres?sslmode=require\n"
        "     Re-run:  .\\setup_schema.ps1\n"
        "\n"
        "  B) SQL Editor fallback\n"
        f"     Open Supabase -> SQL Editor, paste the full contents of:\n"
        f"       {SCHEMA_FILE}\n"
        "     then paste migrations/002_agency_multitenancy.sql and Run again.\n"
        "     Click Run, then:  .\\setup_schema.ps1 --verify-only\n"
    )


def print_fallback() -> None:
    error("schema", "No DATABASE_URL (or SUPABASE_DB_PASSWORD) - DDL skipped")
    for line in fallback_instructions().splitlines():
        warn("schema", line if line else " ")
