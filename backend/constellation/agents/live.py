from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from constellation.config import agent_model
from constellation.graph.store import Snapshot


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
        return json.dumps(snapshot.edge_by_id.get(edge_id, {}))

    navigator = Agent(
        name="Navigator",
        model=agent_model(),
        instructions=(
            "Plan a cited rare-disease evidence path. Use graph tools only; do not invent evidence "
            "or identifiers. Return a short route and unresolved gaps."
        ),
        tools=[resolve, get_cluster, get_assets, get_bridges, get_coverage],
    )
    skeptic = Agent(
        name="Skeptic",
        model=agent_model(),
        instructions=(
            "Check the proposed evidence route for contradictions and verification needs. Use the "
            "graph tools and distinguish evidence from absence."
        ),
        tools=[find_contradicting, pubmed_search, compare_variant_effect],
    )
    writer = Agent(
        name="Writer",
        model=agent_model(),
        instructions=(
            "Return exactly the supplied dossier section keys. "
            "Cite every sentence with one or more "
            "edge IDs from the supplied snapshot. Never add an edge ID not included in the input."
        ),
        output_type=DossierDraft,
        tools=[get_edge],
    )
    navigator_result = await Runner.run(
        navigator,
        f"Disease {disease_id}, persona {persona}. Produce an evidence route for the dossier.",
    )
    skeptic_result = await Runner.run(
        skeptic,
        "Review this route for contradictions or missing verification: "
        + str(navigator_result.final_output),
    )
    allowed_ids = list(snapshot.edge_by_id)
    writer_input = json.dumps(
        {
            "persona": persona,
            "navigation": str(navigator_result.final_output),
            "skeptic": str(skeptic_result.final_output),
            "draft": draft["sections"],
            "allowed_edge_ids": allowed_ids,
        }
    )
    writer_result = await Runner.run(writer, writer_input)
    generated = writer_result.final_output
    if isinstance(generated, DossierDraft):
        return generated.model_dump()
    if isinstance(generated, dict):
        return DossierDraft.model_validate(generated).model_dump()
    return {"sections": draft["sections"], "dropped_sentences": 0, "markdown": draft["markdown"]}
