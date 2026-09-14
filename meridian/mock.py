"""
Local mock enrichment and outreach — used until OpenAI/Anthropic credits are funded.

Parses whatever we can from raw_text, then overlays a deterministic industrial
persona so buyers still score against the five seeded European manufacturers.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlparse

from meridian.scoring import extract_certs, extract_components

# Personas aligned to client_catalog so mock matching produces reviewable leads.
MOCK_PERSONAS: tuple[dict[str, Any], ...] = (
    {
        "country": "United Arab Emirates",
        "target_industry": "oil, gas and petrochemicals",
        "technical_requirements": (
            "Pre-qualification for corrosion-resistant hydraulic control valves, "
            "PED-certified manifolds and ATEX process fittings. "
            "Required: ISO 9001, PED, IATF 16949. Classes: hydraulic, valve, manifold, flange."
        ),
        "iso_certifications": ["ISO 9001", "PED", "IATF 16949", "ATEX"],
        "component_classes": ["valve", "hydraulic", "manifold", "fitting", "flange"],
    },
    {
        "country": "Singapore",
        "target_industry": "precision engineering and medical devices",
        "technical_requirements": (
            "CNC-machined shafts, spindles and stainless manifolds. "
            "Required: ISO 9001, ISO 13485, 5-axis capability, titanium/duplex. "
            "Classes: shaft, spindle, precision, manifold."
        ),
        "iso_certifications": ["ISO 9001", "ISO 13485", "ISO 14001"],
        "component_classes": ["shaft", "spindle", "precision", "manifold"],
    },
    {
        "country": "Vietnam",
        "target_industry": "automotive and industrial assembly",
        "technical_requirements": (
            "Precision bearings, linear guides, ball screws and high-grade fasteners "
            "for assembly lines. Required: ISO 9001, ISO 14001. "
            "Classes: bearing, linear guide, ball screw, fastener."
        ),
        "iso_certifications": ["ISO 9001", "ISO 14001"],
        "component_classes": ["bearing", "fastener", "linear guide", "ball screw"],
    },
    {
        "country": "Thailand",
        "target_industry": "refining and process plant",
        "technical_requirements": (
            "Heat-exchanger cores, shell-and-tube replacements, gaskets and PED flanges "
            "for turnaround. Required: ISO 9001, PED. Classes: heat exchanger, gasket, seal, flange."
        ),
        "iso_certifications": ["ISO 9001", "PED"],
        "component_classes": ["heat exchanger", "gasket", "seal", "flange"],
    },
    {
        "country": "Qatar",
        "target_industry": "energy and LNG",
        "technical_requirements": (
            "ATEX / IECEx process sensors, pressure transmitters and actuators. "
            "Required: ISO 9001, ATEX, IECEx. Classes: sensor, actuator."
        ),
        "iso_certifications": ["ISO 9001", "ATEX", "IECEx"],
        "component_classes": ["sensor", "actuator"],
    },
)

COUNTRY_HINTS = (
    ("united arab emirates", ("uae", "abu dhabi", "adnoc", "emirates")),
    ("singapore", ("singapore",)),
    ("malaysia", ("malaysia", "petronas", "kerteh", "pengerang")),
    ("thailand", ("thailand", "sriracha", "thai oil")),
    ("vietnam", ("vietnam", "viet nam", "hai phong", "vinfast")),
    ("qatar", ("qatar", "qatarenergy")),
    ("saudi arabia", ("saudi", "aramco", "ksa")),
    ("indonesia", ("indonesia", "jakarta")),
    ("oman", ("oman",)),
    ("egypt", ("egypt", "cairo")),
)


def _source_url(raw: str) -> str:
    if raw.startswith("SOURCE:"):
        return raw.split("\n", 1)[0].replace("SOURCE:", "", 1).strip()
    return ""


def _clean_company_name(row: dict[str, Any]) -> str:
    raw = row.get("raw_text") or ""
    name = (row.get("company_name") or "").strip()
    lowered = name.lower()
    junk = any(
        token in lowered
        for token in ("page not found", "page not found", "404", "not found")
    )
    if name and not junk and len(name) > 3:
        return re.sub(r"\s+", " ", name.split("|")[0]).strip()[:240]

    url = _source_url(raw).lower()
    host = (urlparse(_source_url(raw)).hostname or "").lower()
    if "adnoc" in url or "adnoc" in host:
        return "ADNOC Procurement"
    if "petronas" in url:
        return "PETRONAS Chemicals Group"
    if "ted.europa" in url:
        return "TED European Public Buyer"
    if "ungm.org" in url:
        return "UNGM Procuring Entity"
    if "trade.gov" in url:
        return "International Trade Administration"
    return name or host.replace("www.", "") or "Unnamed procurement desk"


def _detect_country(text: str) -> str:
    blob = text.lower()
    for country, hints in COUNTRY_HINTS:
        if any(hint in blob for hint in hints):
            return country.title() if country != "united arab emirates" else "United Arab Emirates"
    return ""


def _persona_for(row: dict[str, Any]) -> dict[str, Any]:
    key = str(row.get("id") or row.get("company_name") or "")
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return MOCK_PERSONAS[int(digest[:8], 16) % len(MOCK_PERSONAS)]


def mock_enrich(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("raw_text") or ""
    persona = dict(_persona_for(row))
    detected = _detect_country(raw + " " + (row.get("company_name") or ""))
    if detected:
        persona["country"] = detected

    certs = sorted(set(persona["iso_certifications"]) | extract_certs(raw))
    classes = sorted(set(persona["component_classes"]) | extract_components(raw))
    persona["iso_certifications"] = certs
    persona["component_classes"] = classes
    persona["company_name"] = _clean_company_name(row)
    persona["confidence"] = 0.42
    persona["technical_requirements"] = (
        f"[MOCK ENRICHMENT] {persona['technical_requirements']}\n"
        f"Certifications: {', '.join(certs)}\n"
        f"Component classes: {', '.join(classes)}"
    )
    return persona


def mock_outreach(lead: dict[str, Any]) -> dict[str, Any]:
    buyer = lead["buyer"]
    client = lead["client"]
    country = (buyer.get("country") or "").strip()
    buyer_name = buyer.get("company_name") or "the procurement desk"
    client_name = client.get("client_name") or "our European manufacturer"
    category = client.get("product_category") or "precision components"
    requirements = (buyer.get("technical_requirements") or "the published technical pack").split("\n")[0]
    score = lead.get("match_score")

    arabic = country.lower() in {
        "united arab emirates",
        "uae",
        "saudi arabia",
        "qatar",
        "kuwait",
        "bahrain",
        "oman",
        "egypt",
        "jordan",
        "iraq",
    }
    thai = country.lower() == "thailand"
    vietnamese = country.lower() == "vietnam"
    bahasa = country.lower() == "indonesia"
    malay = country.lower() == "malaysia"

    english_body = (
        f"Dear Procurement Director,\n\n"
        f"Meridian Flow Network is a private introduction desk. We write regarding "
        f"{buyer_name} in {country or 'your market'} and a possible technical conversation "
        f"with {client_name}, a boutique European house in {category}.\n\n"
        f"The overlap we recorded (match {score}) is: {requirements} "
        f"This is not a bid. It is an offer of a 20-minute diligence call with the "
        f"manufacturer's applications engineer, under NDA if you prefer.\n\n"
        f"If the category is live, reply with two windows this fortnight.\n\n"
        f"Yours sincerely,\n"
        f"Meridian Flow Network\n"
        f"Introductions Desk"
    )

    if arabic:
        language = "Arabic"
        subject = f"مقدمة تقنية — {client_name} / {buyer_name}"
        body = (
            f"المحترم مدير المشتريات،\n\n"
            f"تتقدم شبكة ميريديان فلو بمقدمة هادئة بين {buyer_name} والشركة الأوروبية "
            f"{client_name} في فئة {category}. ندعو إلى مكالمة تدقيق فني مدتها عشرون دقيقة.\n\n"
            f"--- English rendering ---\n\n"
            f"{english_body}"
        )
    elif thai:
        language = "Thai"
        subject = f"การแนะนำทางเทคนิค — {client_name}"
        body = (
            f"เรียน ผู้อำนวยการฝ่ายจัดซื้อ\n\n"
            f"Meridian Flow Network ขอแนะนำการหารือทางเทคนิคระหว่าง {buyer_name} "
            f"กับ {client_name} ({category}) เป็นเวลา 20 นาที\n\n"
            f"--- English rendering ---\n\n"
            f"{english_body}"
        )
    elif vietnamese:
        language = "Vietnamese"
        subject = f"Gioi thieu ky thuat — {client_name}"
        body = (
            f"Kinh gui Giam doc Mua sam,\n\n"
            f"Meridian Flow Network de nghi cuoc trao doi ky thuat 20 phut giua "
            f"{buyer_name} va {client_name} ({category}).\n\n"
            f"--- English rendering ---\n\n"
            f"{english_body}"
        )
    elif bahasa:
        language = "Bahasa Indonesia"
        subject = f"Pengantar teknis — {client_name}"
        body = (
            f"Yth. Direktur Pengadaan,\n\n"
            f"Meridian Flow Network mengusulkan diskusi teknis 20 menit antara "
            f"{buyer_name} dan {client_name} ({category}).\n\n"
            f"--- English rendering ---\n\n"
            f"{english_body}"
        )
    elif malay:
        language = "Malay"
        subject = f"Pengenalan teknikal — {client_name}"
        body = (
            f"YBhg. Pengarah Perolehan,\n\n"
            f"Meridian Flow Network mencadangkan perbincangan teknikal 20 minit antara "
            f"{buyer_name} dan {client_name} ({category}).\n\n"
            f"--- English rendering ---\n\n"
            f"{english_body}"
        )
    else:
        language = "English"
        subject = f"Technical introduction — {client_name} / {buyer_name}"
        body = english_body

    return {
        "language": language,
        "subject": f"[MOCK] {subject}",
        "body": "[MOCK DRAFT — generated without live LLM credits]\n\n" + body,
    }
