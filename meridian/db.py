"""Supabase Data API client used by every operational module."""

from __future__ import annotations

from typing import Any

from supabase import Client, ClientOptions, create_client
from supabase.lib.client_options import DEFAULT_HEADERS

from meridian.brand import info
from meridian.config import Settings

PORTAL_TOKEN_HEADER = "x-client-portal-token"

TABLES = (
    "scraped_buyers",
    "client_catalog",
    "matched_leads",
    "tenants",
    "tenders_log",
    "pending_inquiries",
    "platform_users",
    "auth_sessions",
    "password_reset_tokens",
    "retainer_checkouts",
)


def connect(settings: Settings) -> Client:
    client = create_client(settings.supabase_url, settings.data_api_key)
    info("database", f"Data API connected · {settings.project_ref}")
    return client


def connect_portal(settings: Settings, portal_token: str) -> Client:
    """Data API client that presents a portal token so RLS scopes every query."""
    token = portal_token.strip()
    options = ClientOptions(
        headers={
            **DEFAULT_HEADERS,
            PORTAL_TOKEN_HEADER: token,
        }
    )
    client = create_client(settings.supabase_url, settings.data_api_key, options=options)
    info("database", f"Portal Data API · {settings.project_ref}")
    return client


def chunked(rows: list[dict[str, Any]], size: int = 50) -> list[list[dict[str, Any]]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]
