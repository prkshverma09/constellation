from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openai import OpenAI

from constellation.config import extraction_model
from constellation.extract.models import ExtractionResult
from constellation.variant_classes import MECHANISM_CLASS_LABELS

EXTRACTION_SYSTEM_PROMPT = (
    "You extract evidence claims from one PubMed abstract for a rare-disease knowledge graph. "
    "Extract only claims the abstract states explicitly about the target gene. Every claim needs "
    "`quote`: an exact, contiguous substring copied character-for-character from the abstract "
    "(not the title) that states the claim; keep it under 300 characters and never paraphrase "
    "inside it. claim_type: mechanism = molecular or cellular function or pathway of the gene "
    "product; variant_effect = how pathogenic variants alter the protein, and object_text must "
    "be exactly one of: {variant_terms}; phenotype = a clinical feature reported in patients; "
    "model = an animal or cell model that exists or was studied; therapy = a treatment tested or "
    "proposed. polarity is \"contradicts\" only when the abstract reports evidence against the "
    "claim. evidence_level reflects the study design the abstract describes. confidence: 0.9 for "
    "an explicit, direct experimental or clinical statement; 0.6 for a stated but indirect "
    "finding; 0.3 for speculative wording such as \"may\" or \"suggests\". Never invent "
    "identifiers: set object_hint_id only if the abstract itself contains a GO, HPO, MONDO or "
    "Reactome ID. Leave investigator_roles empty unless the text states author roles. If nothing "
    "qualifies, return an empty claims list."
)

EXTRACTION_USER_PROMPT = (
    "Target gene: {symbol}\n"
    "Other slice genes (do not extract claims about them): {other_genes}\n"
    "PMID: {pmid}\n"
    "Title: {title}\n"
    "Abstract: {abstract}"
)


def extract_live(
    paper: dict[str, Any],
    symbol: str,
    other_genes: list[str],
    client: Any | None = None,
    usage_callback: Callable[[int, int], None] | None = None,
) -> ExtractionResult:
    api = client or OpenAI()
    response = api.responses.parse(
        model=extraction_model(),
        input=[
            {
                "role": "system",
                "content": EXTRACTION_SYSTEM_PROMPT.format(
                    variant_terms=", ".join(MECHANISM_CLASS_LABELS)
                ),
            },
            {
                "role": "user",
                "content": EXTRACTION_USER_PROMPT.format(
                    symbol=symbol,
                    other_genes=", ".join(other_genes),
                    pmid=paper["pmid"],
                    title=paper.get("title", ""),
                    abstract=paper.get("abstract", ""),
                ),
            },
        ],
        text_format=ExtractionResult,
    )
    usage = getattr(response, "usage", None)
    if usage is not None and usage_callback is not None:
        usage_callback(
            int(getattr(usage, "input_tokens", 0) or 0),
            int(getattr(usage, "output_tokens", 0) or 0),
        )
    result = getattr(response, "output_parsed", None)
    if isinstance(result, ExtractionResult):
        return result
    if isinstance(result, dict):
        return ExtractionResult.model_validate(result)
    raise ValueError("OpenAI structured extraction returned no parsed result")
