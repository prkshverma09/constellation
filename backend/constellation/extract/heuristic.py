from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from constellation.extract.models import Claim, ExtractionResult

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("haploinsufficiency", re.compile(r"\bhaploinsufficien(?:cy|t)\b", re.I)),
    ("loss_of_function", re.compile(r"\bloss[- ]of[- ]function\b|\bLOF\b", re.I)),
    ("dominant_negative", re.compile(r"\bdominant[- ]negative\b", re.I)),
    ("gain_of_function", re.compile(r"\bgain[- ]of[- ]function\b|\bGOF\b", re.I)),
    ("SNARE_complex", re.compile(r"\bSNARE\b", re.I)),
    (
        "synaptic_vesicle_cycle",
        re.compile(r"\bsynaptic vesicle(?: cycle| release| trafficking)?\b", re.I),
    ),
    ("presynaptic_release", re.compile(r"\bpresynaptic(?: vesicle)? release\b", re.I)),
    ("missense", re.compile(r"\bmissense\b", re.I)),
)


def sentence_spans(text: str) -> Iterable[str]:
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        normalized = sentence.strip()
        if len(normalized) > 12:
            yield normalized


def extract_heuristic(paper: dict[str, Any], symbol: str) -> ExtractionResult:
    claims: list[Claim] = []
    for sentence in sentence_spans(paper.get("abstract", "")):
        for label, pattern in PATTERNS:
            if pattern.search(sentence):
                claim_type = (
                    "variant_effect"
                    if label
                    in {
                        "haploinsufficiency",
                        "loss_of_function",
                        "dominant_negative",
                        "gain_of_function",
                        "missense",
                    }
                    else "mechanism"
                )
                claims.append(
                    Claim(
                        subject_gene=symbol,
                        claim_type=claim_type,
                        object_text=label,
                        polarity="supports",
                        quote=sentence,
                        confidence=0.55,
                        evidence_level="review",
                    )
                )
    unique: dict[tuple[str, str], Claim] = {
        (claim.quote, claim.object_text): claim for claim in claims
    }
    return ExtractionResult(pmid=str(paper["pmid"]), claims=list(unique.values()))
