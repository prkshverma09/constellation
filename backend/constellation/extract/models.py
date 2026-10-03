from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Claim(BaseModel):
    subject_gene: str
    claim_type: Literal["mechanism", "variant_effect", "phenotype", "model", "therapy"]
    object_text: str
    object_hint_id: str | None = None
    polarity: Literal["supports", "contradicts"]
    quote: str
    confidence: float = Field(ge=0, le=1)
    evidence_level: Literal["in_vitro", "animal", "human_case", "human_cohort", "review"]


class InvestigatorRole(BaseModel):
    name: str
    role: Literal["first_author", "last_author", "corresponding_author"]


class ExtractionResult(BaseModel):
    pmid: str
    claims: list[Claim]
    investigator_roles: list[InvestigatorRole] = Field(default_factory=list)
