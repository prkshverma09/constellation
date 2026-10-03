from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from constellation.analytics.bridges import find_bridges
from constellation.analytics.gaps import (
    disease_assets,
    gap_reasons,
    is_gap,
    what_would_change,
)
from constellation.config import CACHE
from constellation.graph.store import Snapshot

SECTION_TITLES = {
    "who_shares": "Who shares our characteristics",
    "what_exists": "What already exists",
    "what_differs": "What differs and must be verified",
    "who_to_contact": "Who to contact",
    "next_step": "Proposed first joint step",
    "coverage": "Search coverage",
}


def dossier_cache_path(disease: str, persona: str, snapshot_hash: str, mode: str) -> Path:
    key = json.dumps([disease, persona, snapshot_hash, mode], separators=(",", ":"))
    return CACHE / f"{hashlib.sha256(key.encode()).hexdigest()}.json"


def _edge_ids(
    snapshot: Snapshot,
    disease_id: str,
    predicates: set[str] | None = None,
    limit: int = 5,
) -> list[str]:
    return [
        edge["edge_id"]
        for edge in snapshot.edges_by_subject.get(disease_id, [])
        if predicates is None or edge["predicate"] in predicates
    ][:limit]


def _sentence(text: str, edge_ids: list[str]) -> dict[str, Any]:
    return {"text": text, "edge_ids": edge_ids}


def validate_dossier(
    payload: dict[str, Any],
    snapshot: Snapshot,
    disease_id: str,
) -> dict[str, Any]:
    dropped = 0
    sections = []
    for section in payload.get("sections", []):
        valid_sentences = []
        for sentence in section.get("sentences", []):
            refs = sentence.get("edge_ids", [])
            if refs and all(edge_id in snapshot.edge_by_id for edge_id in refs):
                valid_sentences.append(sentence)
            else:
                dropped += 1
        if valid_sentences:
            sections.append({**section, "sentences": valid_sentences})
    fallback = next(iter(snapshot.edges_by_subject.get(disease_id, [])), None)
    fallback_id = fallback["edge_id"] if fallback else None
    required = tuple(SECTION_TITLES)
    found = {section["key"] for section in sections}
    for key in required:
        if key not in found and fallback_id:
            sections.append(
                {
                    "key": key,
                    "title": SECTION_TITLES[key],
                    "sentences": [
                        _sentence(
                            "No source-backed item was mapped for this section "
                            "in the current snapshot.",
                            [fallback_id],
                        )
                    ],
                }
            )
    payload["sections"] = sections
    payload["dropped_sentences"] = dropped
    payload["markdown"] = "\n\n".join(
        f"## {section['title']}\n"
        + "\n".join(
            f"{sentence['text']} [{', '.join(sentence['edge_ids'])}]"
            for sentence in section["sentences"]
        )
        for section in sections
    )
    return payload


def build_dossier(
    snapshot: Snapshot,
    disease_id: str,
    persona: str = "maria",
    *,
    mode: str = "offline",
) -> dict[str, Any]:
    disease = snapshot.node_by_id.get(disease_id)
    if not disease:
        raise KeyError(disease_id)
    cause = next(
        (
            edge
            for edge in snapshot.edges_by_object.get(disease_id, [])
            if edge["predicate"] == "causes"
        ),
        next(iter(snapshot.edges_by_subject.get(disease_id, [])), None),
    )
    anchor = cause["edge_id"] if cause else None
    cluster = snapshot.cluster_for(disease_id)
    neighbors = [
        edge
        for edge in snapshot.edges_by_subject.get(disease_id, [])
        if edge["predicate"] in {"shares_mechanism_with", "phenotypically_similar_to"}
    ]
    neighbors.sort(key=lambda edge: float(edge.get("properties", {}).get("S", 0)), reverse=True)
    shared_sentences = []
    for edge in neighbors[:3]:
        other = snapshot.node_by_id.get(edge["object"])
        if other:
            score = edge.get("properties", {})
            text = (
                f"{other['label']} is a mechanism-supported neighbour (P {score.get('P', 0):.2f}, "
                f"M {score.get('M', 0):.2f}, V {score.get('V', 0):.2f}, S {score.get('S', 0):.2f})."
                if score.get("supported")
                else (
                    f"{other['label']} is the nearest phenotypic lead, "
                    "but the fused mechanism score is below threshold."
                )
            )
            shared_sentences.append(_sentence(text, [edge["edge_id"]]))
    if not shared_sentences and anchor:
        shared_sentences.append(
            _sentence(
                f"{disease.get('properties', {}).get('gene_symbol', disease['label'])} "
                f"is associated with {disease['label']}.",
                [anchor],
            )
        )
    assets = disease_assets(snapshot, disease_id)
    asset_sentences = []
    for asset in assets[:3]:
        edge = next(
            (
                row
                for row in snapshot.edges_by_subject.get(asset["id"], [])
                if row["predicate"] == "serves" and row["object"] == disease_id
            ),
            None,
        )
        if edge:
            asset_sentences.append(
                _sentence(
                    f"{asset['label']} is mapped as a reusable "
                    f"{asset['properties'].get('asset_type', 'asset')}.",
                    [edge["edge_id"]],
                )
            )
    if not asset_sentences and anchor:
        asset_sentences.append(
            _sentence(
                "No reusable asset is mapped to this disease in the current snapshot.", [anchor]
            )
        )
    variant_edges = [
        edge
        for edge in snapshot.edges_by_subject.get(
            disease.get("properties", {}).get("gene_id", ""), []
        )
        if edge["predicate"] == "has_variant_class"
    ]
    differs = [
        _sentence(
            f"The indexed variant-effect class is {edge['object_label']}; "
            "clinical interpretation should be checked against the cited source.",
            [edge["edge_id"]],
        )
        for edge in variant_edges[:2]
    ]
    if not differs and anchor:
        differs.append(
            _sentence("Variant-effect evidence remains to be verified for this disease.", [anchor])
        )
    bridges = []
    bridge_disease_id = None
    if cluster:
        for other_id in cluster.get("member_ids", []):
            if other_id == disease_id:
                continue
            bridges = find_bridges(snapshot, disease_id, other_id)
            if bridges:
                bridge_disease_id = other_id
                break
    contact_sentences = []
    for bridge in bridges[:3]:
        proving = bridge["proving_edges"]
        refs = [
            next(
                (row["edge_id"] for row in proving if row["disease_id"] == target),
                None,
            )
            for target in (disease_id, bridge_disease_id)
        ]
        edge_ids = [ref for ref in refs if ref]
        if edge_ids:
            contact_sentences.append(
                _sentence(
                    f"{bridge['display_name']} has source-linked evidence associated with "
                    "both disease groups.",
                    edge_ids,
                )
            )
    if not contact_sentences and anchor:
        contact_sentences.append(
            _sentence(
                "No cross-disease investigator bridge is verified in the current graph.", [anchor]
            )
        )
    lead_edge = neighbors[0]["edge_id"] if neighbors else anchor
    step_text = (
        f"Could the {assets[0]['label']} protocol be reviewed for a "
        f"phenotype-matched outcome in {disease['label']}?"
        if assets
        else (
            "Could a functional study test whether the leading mechanism evidence "
            f"applies to {disease['label']}?"
        )
    )
    next_sentences = [_sentence(step_text, [lead_edge] if lead_edge else [])] if lead_edge else []
    coverage_refs = _edge_ids(snapshot, disease_id, {"has_phenotype", "causes"}, 3)
    coverage_text = (
        "Coverage reflects only source responses represented in this snapshot; "
        "unindexed records are not evidence of absence."
    )
    coverage_sentences = [_sentence(coverage_text, coverage_refs or ([anchor] if anchor else []))]
    sections = [
        {"key": "who_shares", "title": SECTION_TITLES["who_shares"], "sentences": shared_sentences},
        {
            "key": "what_exists",
            "title": SECTION_TITLES["what_exists"],
            "sentences": asset_sentences,
        },
        {"key": "what_differs", "title": SECTION_TITLES["what_differs"], "sentences": differs},
        {
            "key": "who_to_contact",
            "title": SECTION_TITLES["who_to_contact"],
            "sentences": contact_sentences,
        },
        {"key": "next_step", "title": SECTION_TITLES["next_step"], "sentences": next_sentences},
        {"key": "coverage", "title": SECTION_TITLES["coverage"], "sentences": coverage_sentences},
    ]
    if persona == "devon":
        for section in sections:
            section["sentences"] = section["sentences"][:1]
    elif persona == "osei":
        sections.sort(
            key=lambda section: [
                "what_differs",
                "who_shares",
                "what_exists",
                "who_to_contact",
                "next_step",
                "coverage",
            ].index(section["key"])
        )
    elif persona == "priya":
        sections.sort(
            key=lambda section: [
                "who_shares",
                "what_differs",
                "what_exists",
                "who_to_contact",
                "next_step",
                "coverage",
            ].index(section["key"])
        )
    reasons = gap_reasons(snapshot, disease_id)
    gap_plan = (
        {"reasons": reasons, "what_would_change": what_would_change(snapshot, disease_id)}
        if is_gap(snapshot, disease_id)
        else None
    )
    output = {
        "disease_id": disease_id,
        "persona": persona,
        "mode": mode,
        "snapshot_hash": snapshot.snapshot_hash,
        "trace_id": str(uuid.uuid4()),
        "generated_at": datetime.now(UTC).isoformat(),
        "sections": sections,
        "dropped_sentences": 0,
        "gap_plan": gap_plan,
    }
    return validate_dossier(output, snapshot, disease_id)


async def build_live_dossier(snapshot: Snapshot, disease_id: str, persona: str) -> dict[str, Any]:
    from constellation.agents.live import run_agent_chain

    draft = build_dossier(snapshot, disease_id, persona, mode="live")
    generated = await run_agent_chain(snapshot, disease_id, persona, draft)
    generated.update(
        {
            "disease_id": disease_id,
            "persona": persona,
            "mode": "live",
            "snapshot_hash": snapshot.snapshot_hash,
            "trace_id": str(uuid.uuid4()),
            "generated_at": datetime.now(UTC).isoformat(),
            "gap_plan": draft["gap_plan"],
        }
    )
    return validate_dossier(generated, snapshot, disease_id)
