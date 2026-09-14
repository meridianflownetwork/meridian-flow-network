"""
Classify crawled pages into high-intent buyer signals.

Kinds stored on scraped_buyers.signal_type:
  buyer_profile
  foreign_procurement_tender
  regional_manufacturing_expansion
  public_compliance_filing
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

SIGNAL_TENDER = "foreign_procurement_tender"
SIGNAL_EXPANSION = "regional_manufacturing_expansion"
SIGNAL_FILING = "public_compliance_filing"
SIGNAL_PROFILE = "buyer_profile"
SIGNAL_TYPES = (SIGNAL_TENDER, SIGNAL_EXPANSION, SIGNAL_FILING, SIGNAL_PROFILE)

HOST_HINTS: dict[str, str] = {
    "ted.europa.eu": SIGNAL_TENDER,
    "ungm.org": SIGNAL_TENDER,
    "devbusiness.un.org": SIGNAL_TENDER,
    "projects.worldbank.org": SIGNAL_TENDER,
    "adb.org": SIGNAL_TENDER,
    "adnoc.ae": SIGNAL_TENDER,
    "petronas.com": SIGNAL_TENDER,
    "qatarenergy.qa": SIGNAL_TENDER,
    "aramco.com": SIGNAL_TENDER,
    "saipem.com": SIGNAL_TENDER,
    "ogp.org.uk": SIGNAL_TENDER,
    "zawya.com": SIGNAL_EXPANSION,
    "mida.gov.my": SIGNAL_EXPANSION,
    "boi.go.th": SIGNAL_EXPANSION,
    "vietnam-briefing.com": SIGNAL_EXPANSION,
    "aseanbriefing.com": SIGNAL_EXPANSION,
    "arabianbusiness.com": SIGNAL_EXPANSION,
    "meed.com": SIGNAL_EXPANSION,
    "industrialinfo.com": SIGNAL_EXPANSION,
    "trade.gov": SIGNAL_FILING,
    "sec.gov": SIGNAL_FILING,
    "federalregister.gov": SIGNAL_FILING,
    "wto.org": SIGNAL_FILING,
    "customs.gov.sg": SIGNAL_FILING,
    "trade.ec.europa.eu": SIGNAL_FILING,
}

KEYWORD_WEIGHTS: dict[str, tuple[str, ...]] = {
    SIGNAL_TENDER: (
        "tender",
        "invitation to tender",
        "invitation to bid",
        "request for quotation",
        "request for proposal",
        "request for bid",
        "procurement notice",
        "contract notice",
        "pre-qualification",
        "prequalification",
        "expression of interest",
        "call for tenders",
        "rfq",
        "rfp",
        "itt",
        "itb",
        "award notice",
        "supplier registration",
        "vendor registration",
    ),
    SIGNAL_EXPANSION: (
        "plant expansion",
        "capacity expansion",
        "new manufacturing",
        "new factory",
        "new plant",
        "greenfield",
        "brownfield",
        "industrial park",
        "manufacturing hub",
        "production line",
        "capex",
        "groundbreaking",
        "breaks ground",
        "commissioning",
        "debottleneck",
        "downstream complex",
        "industrial development",
    ),
    SIGNAL_FILING: (
        "form 10-k",
        "form 20-f",
        "8-k",
        "customs filing",
        "import license",
        "export license",
        "trade filing",
        "bill of lading",
        "preferential origin",
        "rules of origin",
        "anti-dumping",
        "supply chain disclosure",
        "conflict minerals",
        "cbam",
        "reach registration",
        "sourcing of components",
        "component sourcing",
        "authorized economic operator",
    ),
}

NOTICE_RE = re.compile(
    r"\b(?:RFQ|RFP|ITT|ITB|EOI)[A-Z0-9/_-]{4,40}"
    r"|\b(?:Tender|Notice|Contract)\s+(?:No\.?|Number|#)\s*[:/]?\s*[A-Z0-9/_-]{4,40}",
    re.IGNORECASE,
)

COUNTRY_HINTS = (
    ("United Arab Emirates", ("uae", "united arab emirates", "abu dhabi", "dubai", "adnoc")),
    ("Saudi Arabia", ("saudi", "ksa", "aramco", "riyadh", "jubail")),
    ("Qatar", ("qatar", "qatarenergy", "doha")),
    ("Malaysia", ("malaysia", "petronas", "kerteh", "pengerang", "kuala lumpur")),
    ("Singapore", ("singapore",)),
    ("Thailand", ("thailand", "bangkok", "sriracha", "map ta phut")),
    ("Vietnam", ("vietnam", "viet nam", "hai phong", "hanoi", "ho chi minh")),
    ("Indonesia", ("indonesia", "jakarta", "balikpapan")),
    ("Egypt", ("egypt", "cairo", "ain sokhna")),
    ("Oman", ("oman", "muscat", "sohar")),
)

REGION_HINTS = (
    ("Middle East", ("middle east", "gcc", "gulf", "uae", "saudi", "qatar", "oman", "bahrain", "kuwait")),
    ("Southeast Asia", ("southeast asia", "asean", "malaysia", "singapore", "thailand", "vietnam", "indonesia")),
)


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _haystack(text: str, url: str, title: str) -> str:
    return f"{title}\n{url}\n{text}".lower()


def _score(kind: str, blob: str) -> tuple[int, list[str]]:
    hits: list[str] = []
    score = 0
    for phrase in KEYWORD_WEIGHTS[kind]:
        if phrase in blob:
            hits.append(phrase)
            score += 2 if " " in phrase else 1
    return score, hits


def classify_signal(text: str, url: str = "", title: str = "", seed_kind: str = "") -> dict[str, Any]:
    blob = _haystack(text, url, title)
    host = _host(url)
    scores: dict[str, int] = {SIGNAL_PROFILE: 0}
    hits: dict[str, list[str]] = {}
    for kind in (SIGNAL_TENDER, SIGNAL_EXPANSION, SIGNAL_FILING):
        value, matched = _score(kind, blob)
        scores[kind] = value
        if matched:
            hits[kind] = matched[:8]

    host_kind = HOST_HINTS.get(host)
    if not host_kind:
        for suffix, kind in HOST_HINTS.items():
            if host.endswith(suffix):
                host_kind = kind
                break
    if host_kind:
        scores[host_kind] = scores.get(host_kind, 0) + 3
    if seed_kind in scores:
        scores[seed_kind] = scores.get(seed_kind, 0) + 2

    ranked = sorted(
        ((kind, value) for kind, value in scores.items() if kind != SIGNAL_PROFILE),
        key=lambda item: item[1],
        reverse=True,
    )
    best_kind, best_score = ranked[0] if ranked else (SIGNAL_PROFILE, 0)
    if best_score < 2:
        signal_type = seed_kind if seed_kind in SIGNAL_TYPES else SIGNAL_PROFILE
        confidence = 0.35 if signal_type != SIGNAL_PROFILE else 0.2
    else:
        signal_type = best_kind
        confidence = min(0.95, 0.4 + best_score * 0.08)

    notices = [match.group(0).strip() for match in NOTICE_RE.finditer(text or "")][:6]
    return {
        "signal_type": signal_type,
        "confidence": round(confidence, 2),
        "matched_keywords": hits.get(signal_type, []),
        "notice_ids": notices,
        "source_host": host,
        "seed_kind": seed_kind or "",
    }


def guess_country(text: str, url: str = "", title: str = "") -> str:
    blob = _haystack(text, url, title)
    for country, needles in COUNTRY_HINTS:
        if any(needle in blob for needle in needles):
            return country
    return ""


def guess_region(text: str, url: str = "", title: str = "", country: str = "") -> str:
    if country in {"United Arab Emirates", "Saudi Arabia", "Qatar", "Oman", "Egypt"}:
        return "Middle East"
    if country in {"Malaysia", "Singapore", "Thailand", "Vietnam", "Indonesia"}:
        return "Southeast Asia"
    blob = _haystack(text, url, title)
    for region, needles in REGION_HINTS:
        if any(needle in blob for needle in needles):
            return region
    return ""


def parse_published_at(item: dict[str, Any]) -> str | None:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    for key in ("date", "published", "publishedTime", "published_at", "og:article:published_time"):
        raw = metadata.get(key) or item.get(key)
        if not raw:
            continue
        text = str(raw).strip()
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                parsed = datetime.strptime(text[:19].replace("Z", ""), fmt.replace("Z", ""))
                return parsed.replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                continue
        if re.match(r"^\d{4}-\d{2}-\d{2}", text):
            return text[:10]
    return None


def filing_reference(classification: dict[str, Any], title: str) -> str:
    notices = classification.get("notice_ids") or []
    if notices:
        return str(notices[0])[:160]
    return (title or "")[:160]


def source_name(url: str) -> str:
    host = _host(url)
    return host or "unknown-source"
