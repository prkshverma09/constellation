from __future__ import annotations

from typing import Any

from openai import OpenAI

from constellation.config import extraction_model
from constellation.extract.models import ExtractionResult


def extract_live(
    paper: dict[str, Any],
    genes: list[str],
    client: Any | None = None,
) -> ExtractionResult:
    api = client or OpenAI()
    response = api.responses.parse(
        model=extraction_model(),
        input=[
            {
                "role": "system",
                "content": (
                    "Extract only claims explicitly supported by the abstract. "
                    "Quotes must be exact substrings. Never invent identifiers. "
                    "Use a gene symbol from the supplied slice."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Allowed genes: {', '.join(genes)}\nPMID: {paper['pmid']}\n"
                    f"Title: {paper.get('title', '')}\nAbstract: {paper.get('abstract', '')}"
                ),
            },
        ],
        text_format=ExtractionResult,
    )
    result = getattr(response, "output_parsed", None)
    if isinstance(result, ExtractionResult):
        return result
    if isinstance(result, dict):
        return ExtractionResult.model_validate(result)
    raise ValueError("OpenAI structured extraction returned no parsed result")
