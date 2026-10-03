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
CURIE_PATTERN = re.compile(r"^(?:[A-Za-z][A-Za-z0-9_-]*):[A-Za-z0-9_.:-]+$")
INTERNAL_CURIE_PATTERN = re.compile(r"^constellation:[A-Za-z0-9_.:-]+(?:/[A-Za-z0-9_.:-]+)*$")


def validate_curie(identifier: str) -> bool:
    if identifier.startswith("constellation:"):
        return bool(INTERNAL_CURIE_PATTERN.fullmatch(identifier))
    if not CURIE_PATTERN.fullmatch(identifier):
        return False
    namespace = identifier.split(":", 1)[0]
    return namespace in ALLOWED_NAMESPACES or identifier.startswith("R-HSA-")


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
    identity = f"{name.casefold().strip()}\x1f{affiliation.casefold().strip()}"
    digest = hashlib.sha1(identity.encode()).hexdigest()[:16]
    return f"constellation:person/{digest}"


def json_safe(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def read_rejected_count() -> int:
    path: Path = DATA / "rejected_edges.jsonl"
    if not path.exists():
        return 0
    return sum(1 for row in path.open(encoding="utf-8") if row.strip())
