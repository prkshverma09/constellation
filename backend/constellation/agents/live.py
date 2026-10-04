from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel

from constellation.config import CACHE, agent_model
from constellation.graph.store import Snapshot

SECTION_KEYS = (
    "who_shares",
    "what_exists",
    "what_differs",
    "who_to_contact",
    "next_step",
    "coverage",
)
PERSONA_STYLES = {
    "maria": (
        "Maria leads a patient group. Warm, plain English, short paragraphs; no scores or "
        "jargon unless explained in words; refer to diseases by gene (e.g. "
        "'STX1B-related disease'); she should be able to email this to a researcher."
    ),
    "devon": (
        "Devon is a newly diagnosed parent. Very short sentences at a grade-8 reading "
        "level; no abbreviations; no numbers except simple counts."
    ),
    "priya": (
        "Priya is a mechanism-focused researcher. Precise; include pathway and GO term "
        "names with IDs, and P/M/V/S values."
    ),
    "osei": (
        "Dr. Osei is a clinician-scientist verifying claims. Technical; start each "
        "section with what must be verified; include identifiers, evidence classes and "
        "identity signals."
    ),
}
WRITER_INSTRUCTIONS = (
    "You are the Writer for Constellation, a research-navigation tool for rare-disease patient "
    "groups and researchers. Rewrite the evidence-backed DRAFT dossier for the reader described "
    "below. Rules: (1) Use only facts present in EVIDENCE or DRAFT; do not add facts, numbers, "
    "names, identifiers or recommendations that are not supported there. (2) Every sentence "
    "must cite one or more edge_ids from EVIDENCE that directly support it; never cite an ID "
    "that is not in EVIDENCE. (3) Return exactly these section keys in this order, each with at "
    "least one sentence: {keys}. (4) Keep uncertainty explicit: partial overlaps, counterexamples, "
    "eligibility differences and items needing expert review must stay, and weak evidence must "
    "not be upgraded. (5) This is research navigation, not medical advice; do not suggest "
    "treatments or clinical decisions. (6) The proposed first joint step must be one concrete, "
    "verifiable action that names the asset, the person and what to verify, drawn from EVIDENCE. "
    "(8) The next_step section must keep the DRAFT next_step's asset, person and requested action; "
    "only adapt wording for the reader. When a comparator is given, who_to_contact and next_step "
    "must concern the comparator. "
    "(7) Navigator and Skeptic notes are guidance only, not evidence. Reader: {persona_style}"
)


def _usage_totals(*results: Any) -> dict[str, int]:
    totals = {"input_tokens": 0, "output_tokens": 0}
    for result in results:
        for response in getattr(result, "raw_responses", []) or []:
            usage = getattr(response, "usage", None)
            for key in totals:
                value = (
                    usage.get(key)
                    if isinstance(usage, dict)
                    else getattr(usage, key, 0)
                )
                totals[key] += int(value or 0)
    return totals


class Sentence(BaseModel):
    text: str
    edge_ids: list[str]


class Section(BaseModel):
    key: str
    title: str
    sentences: list[Sentence]


class DossierDraft(BaseModel):
    sections: list[Section]
    dropped_sentences: int = 0
    markdown: str = ""


async def run_agent_chain(
    snapshot: Snapshot,
    disease_id: str,
    persona: str,
    draft: dict[str, Any],
    comparator_id: str | None,
    evidence_pack: list[dict[str, Any]],
) -> dict[str, Any]:
    from agents import Agent, Runner, function_tool

    @function_tool
    def resolve(query: str) -> str:
        matches = [
            {"id": node["id"], "label": node["label"], "type": node["type"]}
            for node in snapshot.nodes
            if query.casefold() in node["label"].casefold()
            or query.casefold() == node["id"].casefold()
        ][:10]
        return json.dumps(matches)

    @function_tool
    def get_cluster(disease: str) -> str:
        cluster = snapshot.cluster_for(disease)
        return json.dumps(cluster or {})

    @function_tool
    def get_assets(cluster: str) -> str:
        node = snapshot.node_by_id.get(cluster, {})
        members = set(node.get("properties", {}).get("member_ids", []))
        assets = [
            asset
            for asset in snapshot.nodes
            if asset["type"] == "asset"
            and any(
                edge["subject"] == asset["id"]
                and edge["predicate"] == "serves"
                and edge["object"] in members
                for edge in snapshot.edges
            )
        ]
        return json.dumps(assets)

    @function_tool
    def get_bridges(a: str, b: str) -> str:
        from constellation.analytics.bridges import find_bridges

        return json.dumps(find_bridges(snapshot, a, b))

    @function_tool
    def get_coverage(disease: str) -> str:
        from constellation.analytics.gaps import (
            gap_reasons,
            nearest_leads,
            what_would_change,
        )

        return json.dumps(
            {
                "reasons": gap_reasons(snapshot, disease),
                "nearest_leads": nearest_leads(snapshot, disease),
                "what_would_change": what_would_change(snapshot, disease),
            }
        )

    @function_tool
    def find_contradicting(edge_id: str) -> str:
        edge = snapshot.edge_by_id.get(edge_id)
        if not edge:
            return "[]"
        return json.dumps(
            [
                row
                for row in snapshot.edges
                if row["subject"] == edge["subject"]
                and row["object"] == edge["object"]
                and row.get("polarity") == "contradicts"
            ]
        )

    @function_tool
    def pubmed_search(term: str) -> str:
        cache = snapshot.snapshot_dir.parent / "raw" / "pubmed_abstracts.json"
        if not cache.exists():
            return "[]"
        abstracts = json.loads(cache.read_text(encoding="utf-8"))
        query = term.casefold()
        return json.dumps(
            [
                {"pmid": pmid, "abstract": abstract[:300]}
                for pmid, abstract in abstracts.items()
                if query in abstract.casefold()
            ][:5]
        )

    @function_tool
    def compare_variant_effect(gene_a: str, gene_b: str) -> str:
        return json.dumps(
            {
                gene: [
                    edge["object_label"]
                    for edge in snapshot.edges_by_subject.get(gene, [])
                    if edge["predicate"] == "has_variant_class"
                ]
                for gene in (gene_a, gene_b)
            }
        )

    @function_tool
    def get_edge(edge_id: str) -> str:
        allowed = {row["edge_id"] for row in evidence_pack}
        row = next((item for item in evidence_pack if item["edge_id"] == edge_id), None)
        return json.dumps(row or {}) if edge_id in allowed else "{}"

    navigator = Agent(
        name="Navigator",
        model=agent_model(),
        instructions=(
            "Plan a cited rare-disease evidence path. Use graph tools only; do not invent evidence "
            "or identifiers. Return a short route and unresolved gaps. Your notes may only "
            "reference edge_ids from the evidence pack."
        ),
        tools=[resolve, get_cluster, get_assets, get_bridges, get_coverage],
    )
    skeptic = Agent(
        name="Skeptic",
        model=agent_model(),
        instructions=(
            "Check the proposed evidence route for contradictions and verification needs. Use the "
            "graph tools and distinguish evidence from absence. Your notes may only reference "
            "edge_ids from the evidence pack."
        ),
        tools=[find_contradicting, pubmed_search, compare_variant_effect],
    )
    writer = Agent(
        name="Writer",
        model=agent_model(),
        instructions=WRITER_INSTRUCTIONS.format(
            keys=", ".join(SECTION_KEYS),
            persona_style=PERSONA_STYLES.get(persona, PERSONA_STYLES["maria"]),
        ),
        output_type=DossierDraft,
        tools=[get_edge],
    )
    compact_summary = [
        {
            "key": section.get("key"),
            "sentences": [
                {
                    "text": sentence.get("text", ""),
                    "edge_ids": sentence.get("edge_ids", []),
                }
                for sentence in section.get("sentences", [])
            ],
        }
        for section in draft.get("sections", [])
    ]
    shared_context = {
        "disease": disease_id,
        "comparator": comparator_id,
        "draft_summary": compact_summary,
        "evidence_pack_edge_ids": [row["edge_id"] for row in evidence_pack],
    }
    navigator_result = await Runner.run(
        navigator,
        json.dumps(shared_context, ensure_ascii=False),
    )
    skeptic_result = await Runner.run(
        skeptic,
        json.dumps(
            {
                **shared_context,
                "navigation_notes": str(navigator_result.final_output),
                "instruction": "Review the route for contradictions and missing verification.",
            },
            ensure_ascii=False,
        ),
    )
    writer_input = json.dumps(
        {
            "disease": disease_id,
            "comparator": comparator_id,
            "persona": persona,
            "navigation_notes": str(navigator_result.final_output),
            "skeptic_notes": str(skeptic_result.final_output),
            "DRAFT": draft.get("sections", []),
            "EVIDENCE": evidence_pack,
        },
        ensure_ascii=False,
    )
    writer_result = await Runner.run(writer, writer_input)
    generated = writer_result.final_output
    if isinstance(generated, DossierDraft):
        output = generated.model_dump()
    elif isinstance(generated, dict):
        output = DossierDraft.model_validate(generated).model_dump()
    else:
        raise ValueError("Writer returned no structured dossier.")
    output["usage"] = _usage_totals(
        navigator_result,
        skeptic_result,
        writer_result,
    )
    return output


async def precompute_live_dossiers() -> None:
    from constellation.agents.dossier import (
        build_live_dossier,
        live_dossier_cache_path,
    )

    snapshot = Snapshot()
    stxbp1 = "MONDO:0012812"
    frrs1l = "MONDO:0014859"
    dnm1 = next(
        node["id"]
        for node in snapshot.nodes
        if node.get("type") == "disease"
        and node.get("properties", {}).get("gene_symbol") == "DNM1"
    )
    jobs = [
        (stxbp1, persona, None)
        for persona in ("maria", "devon", "priya", "osei")
    ]
    jobs.append((stxbp1, "maria", dnm1))
    jobs.extend(
        (frrs1l, persona, None)
        for persona in ("maria", "devon", "priya", "osei")
    )
    expected_cache_paths = {
        live_dossier_cache_path(
            disease_id,
            persona,
            comparator_id,
            snapshot.snapshot_hash,
            agent_model(),
        )
        for disease_id, persona, comparator_id in jobs
    }
    semaphore = asyncio.Semaphore(4)

    async def precompute(
        job: tuple[str, str, str | None],
    ) -> tuple[tuple[str, str, str | None], dict[str, Any]]:
        async with semaphore:
            disease_id, persona, comparator_id = job
            result = await build_live_dossier(
                snapshot,
                disease_id,
                persona,
                comparator_id,
            )
            print(
                f"{disease_id} persona={persona} vs={comparator_id or 'default'} "
                f"mode={result['mode']} dropped={result['dropped_sentences']} "
                f"fallback={len(result.get('fallback_sections', []))} "
                f"elapsed_ms={result.get('elapsed_ms', 0)} "
                f"input_tokens={result.get('usage', {}).get('input_tokens', 0)} "
                f"output_tokens={result.get('usage', {}).get('output_tokens', 0)}",
                flush=True,
            )
            return job, result

    results = await asyncio.gather(*(precompute(job) for job in jobs))
    failed = [
        f"{disease_id}/{persona}/{comparator_id or 'default'}"
        for (disease_id, persona, comparator_id), result in results
        if result["mode"] != "live"
    ]
    if failed:
        raise RuntimeError(f"Live dossier generation fell back for: {', '.join(failed)}")
    removed = 0
    for cache_path in CACHE.glob("dossier-live-*.json"):
        if cache_path in expected_cache_paths:
            continue
        cache_path.unlink()
        removed += 1
    print(f"Removed {removed} stale live dossier caches.")


if __name__ == "__main__":
    asyncio.run(precompute_live_dossiers())
