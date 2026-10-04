from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from constellation.config import CACHE, extraction_model
from constellation.extract.heuristic import extract_heuristic
from constellation.extract.live import extract_live
from constellation.extract.models import ExtractionResult


def extraction_cache_path(
    paper: dict[str, Any],
    symbol: str,
    cache_dir: Path = CACHE,
) -> Path:
    key = json.dumps(
        [
            paper.get("pmid"),
            symbol,
            paper.get("abstract", ""),
            extraction_model(),
            "extraction-schema-v2",
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return cache_dir / f"extract-{hashlib.sha256(key.encode()).hexdigest()}.json"


def extract_claims(
    paper: dict[str, Any],
    symbol: str,
    genes: list[str],
    mode: str,
) -> tuple[ExtractionResult, str]:
    cache_path = extraction_cache_path(paper, symbol)
    if mode in {"cached", "live"} and cache_path.exists():
        return ExtractionResult.model_validate_json(
            cache_path.read_text(encoding="utf-8")
        ), "llm_extracted"
    if mode == "live":
        result = extract_live(
            paper,
            symbol,
            [gene for gene in genes if gene.casefold() != symbol.casefold()],
        )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return result, "llm_extracted"
    return extract_heuristic(paper, symbol), "inferred"
