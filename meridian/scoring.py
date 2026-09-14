"""Spec-alignment and regional compatibility scoring for buyer ↔ client pairs."""

from __future__ import annotations

import re
from typing import Any, Iterable

SEA_COUNTRIES = {
    "singapore",
    "malaysia",
    "thailand",
    "vietnam",
    "indonesia",
    "philippines",
    "cambodia",
    "laos",
    "myanmar",
    "brunei",
    "east timor",
    "timor-leste",
}

ME_COUNTRIES = {
    "united arab emirates",
    "uae",
    "saudi arabia",
    "ksa",
    "qatar",
    "kuwait",
    "bahrain",
    "oman",
    "jordan",
    "egypt",
    "iraq",
    "lebanon",
    "israel",
}

REGION_ALIASES = {
    "sea": "southeast asia",
    "southeast asia": "southeast asia",
    "south east asia": "southeast asia",
    "asean": "southeast asia",
    "me": "middle east",
    "middle east": "middle east",
    "gcc": "middle east",
    "mena": "middle east",
    "gulf": "middle east",
    "europe": "europe",
    "eu": "europe",
}

ISO_PATTERN = re.compile(
    r"\b(?:ISO\s*\d{4,5}(?::\d{4})?|IATF\s*16949|AS\s*9100|EN\s*9100|ATEX|PED|CE\b|RoHS|REACH)\b",
    re.IGNORECASE,
)

COMPONENT_TERMS = (
    "cnc",
    "machining",
    "precision",
    "valve",
    "hydraulic",
    "pneumatic",
    "bearing",
    "fastener",
    "casting",
    "forging",
    "sensor",
    "actuator",
    "gearbox",
    "turbine",
    "heat exchanger",
    "manifold",
    "fitting",
    "flange",
    "pump",
    "seal",
    "gasket",
    "shaft",
    "spindle",
    "linear guide",
    "ball screw",
)


def normalize(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def country_region(country: str | None) -> str:
    name = normalize(country)
    if name in SEA_COUNTRIES:
        return "southeast asia"
    if name in ME_COUNTRIES:
        return "middle east"
    return REGION_ALIASES.get(name, name)


def expand_regions(values: Iterable[str] | None) -> set[str]:
    regions: set[str] = set()
    for raw in values or []:
        token = normalize(str(raw))
        regions.add(REGION_ALIASES.get(token, token))
        mapped = country_region(token)
        if mapped:
            regions.add(mapped)
    return {item for item in regions if item}


def extract_certs(text: str | None) -> set[str]:
    return {match.group(0).upper().replace("  ", " ") for match in ISO_PATTERN.finditer(text or "")}


def extract_components(text: str | None) -> set[str]:
    blob = normalize(text)
    return {term for term in COMPONENT_TERMS if term in blob}


def _json_blob(specs: Any) -> str:
    if isinstance(specs, dict):
        parts: list[str] = []
        for key, value in specs.items():
            if isinstance(value, (list, tuple)):
                parts.append(f"{key}: {' '.join(str(item) for item in value)}")
            else:
                parts.append(f"{key}: {value}")
        return " ".join(parts)
    return str(specs or "")


def spec_alignment(buyer_requirements: str | None, client_specs: Any, product_category: str | None) -> float:
    buyer_text = normalize(buyer_requirements)
    client_text = normalize(f"{_json_blob(client_specs)} {product_category or ''}")
    if not buyer_text or not client_text:
        return 0.0

    buyer_certs = extract_certs(buyer_requirements)
    client_certs = extract_certs(client_text)
    cert_score = 0.0
    if buyer_certs and client_certs:
        cert_score = len(buyer_certs & client_certs) / len(buyer_certs)
    elif client_certs and buyer_text:
        cert_score = 0.35 if any(cert.lower() in buyer_text for cert in client_certs) else 0.1

    buyer_parts = extract_components(buyer_requirements)
    client_parts = extract_components(client_text)
    part_score = 0.0
    if buyer_parts and client_parts:
        part_score = len(buyer_parts & client_parts) / len(buyer_parts | client_parts)
    elif product_category and normalize(product_category) in buyer_text:
        part_score = 0.45

    tokens = {token for token in re.findall(r"[a-z0-9]{4,}", buyer_text) if token not in {"with", "from", "that"}}
    client_tokens = set(re.findall(r"[a-z0-9]{4,}", client_text))
    token_score = len(tokens & client_tokens) / max(len(tokens), 1)

    return max(0.0, min(1.0, 0.45 * cert_score + 0.40 * part_score + 0.15 * token_score))


def regional_compatibility(buyer_country: str | None, target_regions: Iterable[str] | None) -> float:
    buyer_region = country_region(buyer_country)
    targets = expand_regions(target_regions)
    if not buyer_region or not targets:
        return 0.25
    if buyer_region in targets or normalize(buyer_country) in targets:
        return 1.0
    if "southeast asia" in targets and buyer_region == "southeast asia":
        return 1.0
    if "middle east" in targets and buyer_region == "middle east":
        return 1.0
    return 0.05


def industry_overlap(buyer_industry: str | None, product_category: str | None, client_specs: Any) -> float:
    left = set(re.findall(r"[a-z0-9]{4,}", normalize(buyer_industry)))
    right = set(re.findall(r"[a-z0-9]{4,}", normalize(f"{product_category or ''} {_json_blob(client_specs)}")))
    if not left or not right:
        return 0.2 if not left else 0.0
    return len(left & right) / len(left)


def match_score(
    *,
    buyer_country: str | None,
    buyer_industry: str | None,
    buyer_requirements: str | None,
    product_category: str | None,
    specs_json: Any,
    target_regions: Iterable[str] | None,
) -> float:
    spec = spec_alignment(buyer_requirements, specs_json, product_category)
    region = regional_compatibility(buyer_country, target_regions)
    industry = industry_overlap(buyer_industry, product_category, specs_json)
    score = 100.0 * (0.45 * spec + 0.35 * region + 0.20 * industry)
    return round(max(0.0, min(100.0, score)), 3)
