"""Load and validate environment configuration. Secrets are never logged."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote_plus, unquote, urlparse

POOLER_TARGETS = (
    ("aws-0-eu-west-1.pooler.supabase.com", 5432),
    ("aws-0-eu-west-1.pooler.supabase.com", 6543),
    ("aws-1-eu-west-1.pooler.supabase.com", 5432),
    ("aws-0-eu-central-1.pooler.supabase.com", 5432),
)

from dotenv import load_dotenv

from meridian.brand import fail


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_key: str
    supabase_service_role_key: str
    database_url: str
    supabase_db_password: str
    supabase_access_token: str
    apify_token: str
    apify_actor_id: str
    openai_api_key: str
    anthropic_api_key: str
    llm_provider: str
    llm_model: str
    match_score_threshold: float
    scrape_max_pages: int

    @property
    def data_api_key(self) -> str:
        return self.supabase_service_role_key or self.supabase_key

    @property
    def project_ref(self) -> str:
        host = urlparse(self.supabase_url).hostname or ""
        return host.split(".")[0]

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        if not self.supabase_db_password:
            return ""
        password = quote_plus(self.supabase_db_password)
        return (
            f"postgresql://postgres:{password}"
            f"@db.{self.project_ref}.supabase.co:5432/postgres?sslmode=require"
        )

    @property
    def llm_ready(self) -> bool:
        if self.llm_provider == "mock":
            return True
        if self.llm_provider == "anthropic":
            return bool(self.anthropic_api_key)
        return bool(self.openai_api_key)

    def _database_password(self) -> str:
        if self.supabase_db_password:
            return self.supabase_db_password
        if self.database_url:
            return unquote(urlparse(self.database_url).password or "")
        return ""

    def candidate_database_urls(self) -> list[str]:
        """Direct URI first, then IPv4 pooler hosts if db.* is IPv6-only."""
        urls: list[str] = []
        primary = self.resolved_database_url
        if primary:
            urls.append(primary)

        password = self._database_password()
        ref = self.project_ref
        if not password or not ref:
            return urls

        encoded = quote_plus(password)
        for host, port in POOLER_TARGETS:
            urls.append(
                f"postgresql://postgres.{ref}:{encoded}@{host}:{port}/postgres?sslmode=require"
            )

        # Preserve order, drop duplicates.
        seen: set[str] = set()
        unique: list[str] = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                unique.append(url)
        return unique


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def load_settings(*, require: tuple[str, ...] = ()) -> Settings:
    load_dotenv()
    settings = Settings(
        supabase_url=_get("SUPABASE_URL"),
        supabase_key=_get("SUPABASE_KEY"),
        supabase_service_role_key=_get("SUPABASE_SERVICE_ROLE_KEY"),
        database_url=_get("DATABASE_URL") or _get("SUPABASE_DB_URL"),
        supabase_db_password=_get("SUPABASE_DB_PASSWORD"),
        supabase_access_token=_get("SUPABASE_ACCESS_TOKEN"),
        apify_token=_get("APIFY_TOKEN"),
        apify_actor_id=_get("APIFY_ACTOR_ID", "apify/website-content-crawler"),
        openai_api_key=_get("OPENAI_API_KEY"),
        anthropic_api_key=_get("ANTHROPIC_API_KEY"),
        llm_provider=_get("LLM_PROVIDER", "openai").lower(),
        llm_model=_get("LLM_MODEL"),
        match_score_threshold=float(_get("MATCH_SCORE_THRESHOLD", "60")),
        scrape_max_pages=int(_get("SCRAPE_MAX_PAGES", "12")),
    )

    aliases = {
        "SUPABASE_URL": settings.supabase_url,
        "SUPABASE_KEY": settings.supabase_key or settings.supabase_service_role_key,
        "APIFY_TOKEN": settings.apify_token,
        "OPENAI_API_KEY": settings.openai_api_key,
        "ANTHROPIC_API_KEY": settings.anthropic_api_key,
    }
    missing = [name for name in require if not aliases.get(name)]
    if missing:
        fail("config", f"Missing required environment variables: {', '.join(missing)}")
    return settings
