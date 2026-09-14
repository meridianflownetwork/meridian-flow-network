"""Public high-intent start URLs for the Apify crawl."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from meridian.signals import (
    SIGNAL_EXPANSION,
    SIGNAL_FILING,
    SIGNAL_PROFILE,
    SIGNAL_TENDER,
)

# Titles are unused by Apify; userData.kind tags the seed so parsing keeps intent.
SIGNAL_SEEDS: dict[str, tuple[str, ...]] = {
    SIGNAL_TENDER: (
        "https://ted.europa.eu/en/simap/the-european-public-procurement-journal",
        "https://www.ungm.org/Public/Notice",
        "https://projects.worldbank.org/en/projects-operations/procurement",
        "https://www.adb.org/projects/tenders",
        "https://www.adnoc.ae/en/our-services/procurement",
        "https://www.petronas.com/who-we-are/doing-business-with-us",
        "https://www.qatarenergy.qa/en/DoingBusinessWithUs/Pages/default.aspx",
    ),
    SIGNAL_EXPANSION: (
        "https://www.zawya.com/en/projects",
        "https://www.mida.gov.my/",
        "https://www.boi.go.th/",
        "https://www.vietnam-briefing.com/news/category/manufacturing",
        "https://www.aseanbriefing.com/news/category/manufacturing",
        "https://www.arabianbusiness.com/industries/industrials",
    ),
    SIGNAL_FILING: (
        "https://www.trade.gov/export-solutions",
        "https://www.sec.gov/search-filings",
        "https://trade.ec.europa.eu/access-to-markets/en/home",
        "https://www.wto.org/english/tratop_e/tratop_e.htm",
    ),
    SIGNAL_PROFILE: (
        "https://www.adnoc.ae/en/our-services/procurement",
        "https://www.petronas.com/who-we-are/doing-business-with-us",
    ),
}

KIND_ALIASES = {
    "tender": SIGNAL_TENDER,
    "tenders": SIGNAL_TENDER,
    "expansion": SIGNAL_EXPANSION,
    "expansions": SIGNAL_EXPANSION,
    "filing": SIGNAL_FILING,
    "filings": SIGNAL_FILING,
    "compliance": SIGNAL_FILING,
    "profile": SIGNAL_PROFILE,
    "directory": SIGNAL_PROFILE,
}


def _kinds_from_env() -> list[str]:
    raw = os.getenv("SCRAPE_SIGNAL_KINDS", "").strip()
    if not raw:
        return [SIGNAL_TENDER, SIGNAL_EXPANSION, SIGNAL_FILING]
    kinds: list[str] = []
    for item in raw.split(","):
        key = item.strip().lower()
        kind = KIND_ALIASES.get(key, key)
        if kind in SIGNAL_SEEDS and kind not in kinds:
            kinds.append(kind)
    return kinds or [SIGNAL_TENDER, SIGNAL_EXPANSION, SIGNAL_FILING]


def seed_kind_for_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    for kind, urls in SIGNAL_SEEDS.items():
        for seed in urls:
            seed_host = (urlparse(seed).hostname or "").lower().removeprefix("www.")
            if host == seed_host or host.endswith(seed_host):
                return kind
    return ""


def start_urls(kinds: list[str] | None = None) -> list[dict[str, Any]]:
    raw = os.getenv("SCRAPE_START_URLS", "").strip()
    if raw:
        return [
            {"url": item.strip(), "userData": {"kind": seed_kind_for_url(item)}}
            for item in raw.split(",")
            if item.strip()
        ]
    selected = kinds or _kinds_from_env()
    seen: set[str] = set()
    payload: list[dict[str, str]] = []
    for kind in selected:
        for url in SIGNAL_SEEDS.get(kind, ()):
            if url in seen:
                continue
            seen.add(url)
            payload.append({"url": url, "userData": {"kind": kind}})
    return payload


def start_url_count(kinds: list[str] | None = None) -> int:
    return len(start_urls(kinds))


# Boutique European manufacturers used to seed client_catalog so matching has a bench.
SEED_CLIENTS: list[dict] = [
    {
        "client_name": "Hartmann Präzision GmbH",
        "product_category": "CNC precision machining and medical-grade components",
        "specs_json": {
            "iso": ["ISO 9001", "ISO 13485", "ISO 14001"],
            "capabilities": ["5-axis CNC", "tight-tolerance machining", "stainless and titanium"],
            "component_classes": ["shaft", "manifold", "spindle", "precision"],
            "moq_notes": "Prototype to mid-volume series",
        },
        "target_regions": ["Southeast Asia", "Middle East", "Singapore", "UAE"],
        "tenant_id": "00000000-0000-4000-a000-000000000001",
        "company_name": "Hartmann Präzision GmbH",
        "sender_name": "Introductions Desk",
        "reply_to_email": None,
        "domain": None,
    },
    {
        "client_name": "Vallecchia Hydraulics S.r.l.",
        "product_category": "Hydraulic valves, manifolds and industrial fittings",
        "specs_json": {
            "iso": ["ISO 9001", "IATF 16949", "PED"],
            "capabilities": ["hydraulic valves", "custom manifolds", "ATEX assemblies"],
            "component_classes": ["valve", "hydraulic", "manifold", "fitting", "flange"],
        },
        "target_regions": ["Middle East", "Saudi Arabia", "UAE", "Qatar"],
        "tenant_id": "00000000-0000-4000-a000-000000000001",
        "company_name": "Vallecchia Hydraulics S.r.l.",
        "sender_name": "Introductions Desk",
        "reply_to_email": None,
        "domain": None,
    },
    {
        "client_name": "Nordvik Precision AS",
        "product_category": "Industrial bearings, fasteners and linear motion",
        "specs_json": {
            "iso": ["ISO 9001", "ISO 14001"],
            "capabilities": ["precision bearings", "linear guides", "ball screws"],
            "component_classes": ["bearing", "fastener", "linear guide", "ball screw"],
        },
        "target_regions": ["Southeast Asia", "Vietnam", "Thailand", "Malaysia", "Indonesia"],
        "tenant_id": "00000000-0000-4000-a000-000000000001",
        "company_name": "Nordvik Precision AS",
        "sender_name": "Introductions Desk",
        "reply_to_email": None,
        "domain": None,
    },
    {
        "client_name": "Atelier Roche & Cie",
        "product_category": "Heat-exchanger cores and process-plant fabrications",
        "specs_json": {
            "iso": ["ISO 9001", "PED", "ASME"],
            "capabilities": ["heat exchanger", "shell and tube", "process fabrication"],
            "component_classes": ["heat exchanger", "gasket", "seal", "flange"],
        },
        "target_regions": ["Middle East", "Southeast Asia", "Egypt", "Oman"],
        "tenant_id": "00000000-0000-4000-a000-000000000001",
        "company_name": "Atelier Roche & Cie",
        "sender_name": "Introductions Desk",
        "reply_to_email": None,
        "domain": None,
    },
    {
        "client_name": "König Sensortechnik AG",
        "product_category": "Industrial sensors and ATEX instrumentation",
        "specs_json": {
            "iso": ["ISO 9001", "ATEX", "IECEx"],
            "capabilities": ["pressure sensors", "ATEX transmitters", "process instrumentation"],
            "component_classes": ["sensor", "actuator", "atex"],
        },
        "target_regions": ["Middle East", "Southeast Asia", "GCC"],
        "tenant_id": "00000000-0000-4000-a000-000000000001",
        "company_name": "König Sensortechnik AG",
        "sender_name": "Introductions Desk",
        "reply_to_email": None,
        "domain": None,
    },
]
