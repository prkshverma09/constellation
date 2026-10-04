from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from constellation.config import DATA, SCHEMA_VERSION

ALLOWED_NAMESPACES = {
    "HGNC",
    "MONDO",
    "HP",
    "GO",
    "PMID",
    "NCT",
    "NIH",
    "R-HSA",
    "UniProtKB",
    "ENSEMBL",
    "OMIM",
    "ORPHA",
    "NCBITaxon",
    "constellation",
}
ALLOWED_SOURCES = {
    "analytics",
    "clinvar",
    "ctgov",
    "go",
    "monarch",
    "openai_web_search",
    "pubmed",
    "reactome",
    "reporter",
    "seed_list",
    "string",
    "user_contribution",
}
CURIE_PATTERN = re.compile(r"^(?:[A-Za-z][A-Za-z0-9_-]*):[A-Za-z0-9_.:-]+$")
INTERNAL_CURIE_PATTERN = re.compile(r"^constellation:[A-Za-z0-9_.:-]+(?:/[A-Za-z0-9_.:-]+)*$")
REACTOME_CURIE_PATTERN = re.compile(r"^R-HSA-[0-9]+(?:\.[0-9]+)?$")
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
URL_PATTERN = re.compile(r"\bhttps?://\S+", re.IGNORECASE)
PHONE_PATTERN = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)")
LABELED_CONTACT_PATTERN = re.compile(
    r"(?:e-?mail|phone|telephone|tel|fax|mobile|contact)\s*[:=]\s*[^,;\n]+",
    re.IGNORECASE,
)


def validate_curie(identifier: str) -> bool:
    if identifier.startswith("constellation:"):
        return bool(INTERNAL_CURIE_PATTERN.fullmatch(identifier))
    if identifier.startswith("R-HSA-"):
        return bool(REACTOME_CURIE_PATTERN.fullmatch(identifier))
    if not CURIE_PATTERN.fullmatch(identifier):
        return False
    namespace = identifier.split(":", 1)[0]
    return namespace in ALLOWED_NAMESPACES


def edge_id(subject: str, predicate: str, obj: str, source: str, record: str) -> str:
    key = "\x1f".join((subject, predicate, obj, source, record))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def make_edge(
    subject: str,
    subject_label: str,
    predicate: str,
    obj: str,
    object_label: str,
    *,
    evidence_class: str,
    source: str,
    source_record: str,
    source_url: str | None = None,
    confidence: float = 1.0,
    quote: str | None = None,
    polarity: str | None = None,
    method: str | None = None,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "edge_id": edge_id(subject, predicate, obj, source, source_record),
        "subject": subject,
        "subject_label": subject_label,
        "predicate": predicate,
        "object": obj,
        "object_label": object_label,
        "evidence_class": evidence_class,
        "source": source,
        "source_record": source_record,
        "source_url": source_url,
        "retrieved_at": date.today().isoformat(),
        "confidence": max(0.0, min(1.0, confidence)),
        "quote": quote,
        "polarity": polarity,
        "contradicted_by": [],
        "method": method,
        "schema_version": SCHEMA_VERSION,
        "properties": properties or {},
    }


def reject_edge(edge: dict[str, Any], reason: str) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "rejected_edges.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"edge": edge, "reason": reason}, ensure_ascii=False) + "\n")


def validate_edge(edge: dict[str, Any], known_ids: set[str]) -> str | None:
    if any(not validate_curie(str(edge.get(field, ""))) for field in ("subject", "object")):
        return "subject/object CURIE namespace is not whitelisted"
    if edge.get("source") not in ALLOWED_SOURCES:
        return "source is not whitelisted"
    if edge.get("subject") not in known_ids or edge.get("object") not in known_ids:
        return "subject/object absent from source-validated node IDs"
    if edge.get("evidence_class") not in {
        "observed",
        "curated",
        "inferred",
        "llm_extracted",
        "web_discovered",
        "proposed",
    }:
        return "unknown evidence class"
    if not 0 <= float(edge.get("confidence", -1)) <= 1:
        return "confidence outside [0,1]"
    if edge.get("evidence_class") == "llm_extracted" and not edge.get("quote"):
        return "LLM-extracted claim requires a supporting quote"
    return None


def iso_datetime() -> str:
    return datetime.now(UTC).isoformat()


def person_curie(name: str, affiliation: str = "") -> str:
    identity = f"{name.casefold().strip()}\x1f{sanitize_affiliation(affiliation).casefold()}"
    digest = hashlib.sha1(identity.encode()).hexdigest()[:16]
    return f"constellation:person/{digest}"


def sanitize_affiliation(value: Any) -> str:
    if isinstance(value, dict):
        value = (
            value.get("org_name")
            or value.get("organization_name")
            or value.get("name")
            or value.get("institution")
            or ""
        )
    text = str(value or "")
    text = EMAIL_PATTERN.sub("", text)
    text = URL_PATTERN.sub("", text)
    text = LABELED_CONTACT_PATTERN.sub("", text)
    text = PHONE_PATTERN.sub("", text)
    return re.sub(r"\s+", " ", text).strip(" \t\r\n,;|")


def sanitize_affiliations(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in values:
        affiliation = sanitize_affiliation(item)
        key = affiliation.casefold()
        if affiliation and key not in seen:
            cleaned.append(affiliation)
            seen.add(key)
    return cleaned


def sanitize_snapshot_record(value: dict[str, Any]) -> None:
    def sanitize(item: Any, key: str = "") -> Any:
        if isinstance(item, dict):
            return {child_key: sanitize(child, child_key) for child_key, child in item.items()}
        if isinstance(item, list):
            if key.casefold() == "affiliations":
                return sanitize_affiliations(item)
            return [sanitize(child, key) for child in item]
        if isinstance(item, str):
            if key.casefold() == "affiliations" or key.casefold() == "affiliation":
                return sanitize_affiliation(item)
            return EMAIL_PATTERN.sub("", item)
        return item

    value.update({key: sanitize(item, key) for key, item in value.items()})


def json_safe(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def read_rejected_count() -> int:
    path: Path = DATA / "rejected_edges.jsonl"
    if not path.exists():
        return 0
    return sum(1 for row in path.open(encoding="utf-8") if row.strip())
