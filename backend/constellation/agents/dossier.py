from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from constellation.analytics.bridges import find_bridges
from constellation.analytics.coverage import asset_coverage
from constellation.analytics.gaps import (
    disease_assets,
    gap_reasons,
    is_gap,
    what_would_change,
)
from constellation.config import CACHE, agent_model
from constellation.graph.store import Snapshot

SECTION_TITLES = {
    "who_shares": "Who shares our characteristics",
    "what_exists": "What already exists",
    "what_differs": "What differs and must be verified",
    "who_to_contact": "Who to contact",
    "next_step": "Proposed first joint step",
    "coverage": "Search coverage",
}
WRITER_VERSION = "writer-v2"
NUMBER_TOKEN = re.compile(r"\d+(?:\.\d+)?%?")
IDENTIFIER = re.compile(
    r"\b(?:GO|HP|MONDO|HGNC):[A-Za-z0-9_.:-]+\b"
    r"|\bR-HSA-\d+\b|\bNCT\d+\b|\bPMID\s*:?\s*\d+\b",
    re.IGNORECASE,
)
NCT_IDENTIFIER = re.compile(r"\bNCT\d+\b")
PERSON_NAME = re.compile(
    r"^(?:Verify\s+)?(?:identity\s+for\s+)?"
    r"([A-Z][\w’'.-]*(?:\s+(?:[A-Z]\.?|[A-Z][\w’'.-]*)){1,3})\b"
)
logger = logging.getLogger(__name__)


def dossier_cache_path(disease: str, persona: str, snapshot_hash: str, mode: str) -> Path:
    key = json.dumps([disease, persona, snapshot_hash, mode], separators=(",", ":"))
    return CACHE / f"{hashlib.sha256(key.encode()).hexdigest()}.json"


def live_dossier_cache_path(
    disease: str,
    persona: str,
    comparator_id: str | None,
    snapshot_hash: str,
    model: str | None = None,
) -> Path:
    key = json.dumps(
        [
            disease,
            persona,
            comparator_id,
            snapshot_hash,
            model or agent_model(),
            WRITER_VERSION,
        ],
        separators=(",", ":"),
    )
    return CACHE / f"dossier-live-{hashlib.sha256(key.encode()).hexdigest()}.json"


def _pair_evidence_ids(snapshot: Snapshot, edge: dict[str, Any]) -> list[str]:
    ids = [edge["edge_id"]]
    disease_ids = [edge["subject"], edge["object"]]
    genes = [
        snapshot.node_by_id.get(disease_id, {}).get("properties", {}).get("gene_id")
        for disease_id in disease_ids
    ]
    pathways = set(edge.get("properties", {}).get("shared_pathways", []))
    for disease_id in disease_ids:
        ids.extend(
            [
                row["edge_id"]
                for row in snapshot.edges_by_subject.get(disease_id, [])
                if row["predicate"] == "has_phenotype"
            ][:5]
        )
    for gene_id in genes:
        ids.extend(
            [
                row["edge_id"]
                for row in snapshot.edges_by_subject.get(gene_id or "", [])
                if row["predicate"] in {"participates_in", "annotated_to"}
                and (
                    not pathways
                    or row["object"] in pathways
                    or row.get("source") == "string"
                )
            ][:5]
        )
        ids.extend(
            [
                row["edge_id"]
                for row in snapshot.edges_by_subject.get(gene_id or "", [])
                if row["predicate"] == "has_variant_class"
            ][:2]
        )
    return ids


def _live_evidence_pack(
    snapshot: Snapshot,
    disease_id: str,
    draft: dict[str, Any],
    comparator_id: str | None,
) -> list[dict[str, Any]]:
    edge_ids: list[str] = []
    for section in draft.get("sections", []):
        for sentence in section.get("sentences", []):
            edge_ids.extend(sentence.get("edge_ids", []))

    cluster = snapshot.cluster_for(disease_id)
    member_ids = set(cluster.get("member_ids", [])) if cluster else {disease_id}
    pair_targets = set(member_ids - {disease_id})
    pair_targets.update(
        row["disease_id"] for row in _partial_overlap_rows(snapshot, disease_id)
    )
    if cluster:
        counterexample_id = cluster.get("counterexample_id")
        if counterexample_id:
            pair_targets.add(counterexample_id)
    for target in sorted(pair_targets):
        edge_ids.extend(
            edge_id
            for edge in _pair_edges(snapshot, disease_id, target)
            for edge_id in _pair_evidence_ids(snapshot, edge)
        )

    relevant_diseases = {disease_id}
    if comparator_id and comparator_id in snapshot.node_by_id:
        relevant_diseases.add(comparator_id)
        edge_ids.extend(_coverage_edge_ids(snapshot, comparator_id))
        for edge in _pair_edges(snapshot, disease_id, comparator_id):
            edge_ids.extend(_pair_evidence_ids(snapshot, edge))
        for bridge in find_bridges(snapshot, disease_id, comparator_id)[:5]:
            edge_ids.extend(
                proof.get("edge_id", "")
                for proof in bridge.get("proving_edges", [])
            )
    edge_ids.extend(_coverage_edge_ids(snapshot, disease_id))
    for item in (draft.get("gap_plan") or {}).get("what_would_change", []):
        edge_ids.extend(item.get("edge_ids", []))

    assets = {
        asset["id"]: asset
        for target_id in relevant_diseases
        for asset in disease_assets(snapshot, target_id)
    }
    for asset in assets.values():
        for edge in snapshot.edges_by_subject.get(asset["id"], []):
            if edge["predicate"] in {"serves", "measures_phenotype"}:
                edge_ids.append(edge["edge_id"])
            if edge["predicate"] == "serves":
                edge_ids.extend(
                    phenotype["edge_id"]
                    for phenotype in snapshot.edges_by_subject.get(edge["object"], [])
                    if phenotype["predicate"] == "has_phenotype"
                )
        for target_id in relevant_diseases:
            coverage = asset_coverage(asset, target_id, snapshot)
            edge_ids.extend(_coverage_edge_ids(snapshot, target_id))
            if coverage:
                covered_terms = {
                    item["id"]
                    for item in [*coverage["matched"], *coverage["unmatched"]]
                }
                edge_ids.extend(
                    edge["edge_id"]
                    for edge in snapshot.edges_by_subject.get(target_id, [])
                    if edge["predicate"] == "has_phenotype"
                    and edge["object"] in covered_terms
                )

    rows = []
    seen: set[str] = set()
    for edge_id in edge_ids:
        edge = snapshot.edge_by_id.get(edge_id)
        if not edge or edge_id in seen:
            continue
        seen.add(edge_id)
        rows.append(
            {
                "edge_id": edge_id,
                "subject_label": edge.get("subject_label", ""),
                "predicate": edge.get("predicate", ""),
                "object_label": edge.get("object_label", ""),
                "evidence_class": edge.get("evidence_class", ""),
                "source": edge.get("source", ""),
                "source_record": edge.get("source_record", ""),
                "confidence": edge.get("confidence"),
                "quote": str(edge.get("quote") or "")[:200],
            }
        )
        if len(rows) >= 400:
            break
    return rows


def _number_tokens(value: str) -> set[str]:
    return set(NUMBER_TOKEN.findall(IDENTIFIER.sub("", value)))


def _slice_gene_symbols(snapshot: Snapshot) -> set[str]:
    return {
        str(symbol)
        for node in snapshot.nodes
        if node.get("type") == "disease"
        and (symbol := node.get("properties", {}).get("gene_symbol"))
    }


def _mentioned_gene_symbols(text: str, symbols: set[str]) -> set[str]:
    return {
        symbol
        for symbol in symbols
        if re.search(rf"\b{re.escape(symbol)}\b", text)
    }


def _cited_gene_symbols(
    snapshot: Snapshot,
    edge_ids: list[str],
    slice_symbols: set[str],
) -> set[str]:
    supported = set()
    for edge_id in edge_ids:
        edge = snapshot.edge_by_id[edge_id]
        for key in ("subject_label", "object_label"):
            supported.update(
                _mentioned_gene_symbols(str(edge.get(key) or ""), slice_symbols)
            )
        for node_id in (edge.get("subject"), edge.get("object")):
            node = snapshot.node_by_id.get(node_id, {})
            properties = node.get("properties", {})
            symbol = properties.get("gene_symbol")
            if symbol in slice_symbols:
                supported.add(symbol)
            gene_node = snapshot.node_by_id.get(properties.get("gene_id"), {})
            gene_symbol = gene_node.get("properties", {}).get("gene_symbol")
            if gene_symbol in slice_symbols:
                supported.add(gene_symbol)
    return supported


def _draft_next_step_anchors(
    draft_sections: dict[str, dict[str, Any]],
) -> set[str]:
    next_step = draft_sections.get("next_step", {})
    next_text = " ".join(
        sentence.get("text", "") for sentence in next_step.get("sentences", [])
    )
    anchors = set(NCT_IDENTIFIER.findall(next_text))
    for sentence in draft_sections.get("who_to_contact", {}).get("sentences", []):
        match = PERSON_NAME.match(sentence.get("text", ""))
        if match and match.group(1).casefold() in next_text.casefold():
            anchors.add(match.group(1))
    return anchors


def _mentions_draft_anchor(text: str, anchors: set[str]) -> bool:
    folded = text.casefold()
    return any(anchor.casefold() in folded for anchor in anchors)


def _mentions_comparator(text: str, comparator_terms: set[str]) -> bool:
    for term in comparator_terms:
        if term and re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE):
            return True
    return False


def _offline_section_map(draft: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {section["key"]: section for section in draft.get("sections", [])}


def _markdown(sections: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        f"## {section['title']}\n"
        + "\n".join(
            f"{sentence['text']} [{', '.join(sentence['edge_ids'])}]"
            for sentence in section["sentences"]
        )
        for section in sections
    )


def _coverage_edge_ids(
    snapshot: Snapshot,
    disease_id: str,
    limit: int = 6,
) -> list[str]:
    disease = snapshot.node_by_id.get(disease_id, {})
    gene_id = disease.get("properties", {}).get("gene_id", "")
    relevant = (
        snapshot.edges_by_subject.get(disease_id, [])
        + snapshot.edges_by_object.get(disease_id, [])
        + snapshot.edges_by_subject.get(gene_id, [])
        + snapshot.edges_by_object.get(gene_id, [])
    )
    priority = {
        "source_query": 0,
        "source_count": 0,
        "has_source_count": 0,
        "coverage": 0,
        "has_phenotype": 1,
        "causes": 2,
    }
    ids = []
    seen = set()
    for edge in sorted(
        relevant,
        key=lambda row: (priority.get(row["predicate"], 3), row["edge_id"]),
    ):
        edge_id = edge["edge_id"]
        if edge["predicate"] not in priority or edge_id in seen:
            continue
        seen.add(edge_id)
        ids.append(edge_id)
    return ids[:limit]


def _sentence(text: str, edge_ids: list[str]) -> dict[str, Any]:
    return {"text": text, "edge_ids": list(dict.fromkeys(edge_ids))}


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
            refs = list(dict.fromkeys(sentence.get("edge_ids", [])))
            if refs and all(edge_id in snapshot.edge_by_id for edge_id in refs):
                valid_sentences.append({**sentence, "edge_ids": refs})
            else:
                dropped += 1
        if valid_sentences:
            sections.append({**section, "sentences": valid_sentences})
    coverage_refs = _coverage_edge_ids(snapshot, disease_id)
    required = tuple(SECTION_TITLES)
    found = {section["key"] for section in sections}
    for key in required:
        if key not in found and coverage_refs:
            sections.append(
                {
                    "key": key,
                    "title": SECTION_TITLES[key],
                    "sentences": [
                        _sentence(
                            "No item is mapped for this section; disease coverage "
                            "remains limited to the cited source records.",
                            coverage_refs[:3],
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


def _pair_edges(snapshot: Snapshot, left: str, right: str) -> list[dict[str, Any]]:
    predicates = {"shares_mechanism_with", "phenotypically_similar_to"}
    rows = [
        edge
        for edge in snapshot.edges_by_subject.get(left, [])
        if edge["object"] == right and edge["predicate"] in predicates
    ] + [
        edge
        for edge in snapshot.edges_by_subject.get(right, [])
        if edge["object"] == left and edge["predicate"] in predicates
    ]
    return list({edge["edge_id"]: edge for edge in rows}.values())


def _partial_overlap_rows(snapshot: Snapshot, disease_id: str) -> list[dict[str, Any]]:
    edges = (
        snapshot.edges_by_subject.get(disease_id, [])
        + snapshot.edges_by_object.get(disease_id, [])
    )
    rows = []
    for edge in edges:
        if edge["predicate"] not in {"shares_mechanism_with", "phenotypically_similar_to"}:
            continue
        other_id = edge["object"] if edge["subject"] == disease_id else edge["subject"]
        other = snapshot.node_by_id.get(other_id)
        if not other or other.get("type") != "disease":
            continue
        properties = edge.get("properties", {})
        mechanism = float(properties.get("M") or 0)
        score = float(properties.get("S") or 0)
        if not (0 < mechanism < 0.25 or (mechanism >= 0.25 and score < 0.45)):
            continue
        rows.append(
            {
                "disease_id": other_id,
                "label": _member_label(other),
                "edge": edge,
                "M": mechanism,
                "S": score,
                "excluded_by": "M" if mechanism < 0.25 else "S",
            }
        )
    rows.sort(key=lambda row: (-row["M"], -row["S"], row["label"].casefold()))
    return rows


def _mechanism_edge_ids(
    snapshot: Snapshot,
    disease_a: str,
    disease_b: str,
    term_ids: set[str],
) -> list[str]:
    gene_ids = {
        snapshot.node_by_id.get(disease_id, {}).get("properties", {}).get("gene_id")
        for disease_id in (disease_a, disease_b)
    }
    return list(
        dict.fromkeys(
            edge["edge_id"]
            for gene_id in gene_ids
            if gene_id
            for edge in snapshot.edges_by_subject.get(gene_id, [])
            if edge["source"] in {"go", "reactome"}
            and edge["object"] in term_ids
        )
    )


def _member_label(node: dict[str, Any]) -> str:
    properties = node.get("properties", {})
    gene_symbol = properties.get("gene_symbol")
    disease_name = node.get("label", node["id"])
    if gene_symbol:
        return f"{gene_symbol}-related disease ({disease_name})"
    return disease_name


def _shared_terms(snapshot: Snapshot, edge: dict[str, Any]) -> list[dict[str, str]]:
    terms = []
    for value in edge.get("properties", {}).get("shared_pathways", []) or []:
        if isinstance(value, dict):
            identifier = str(value.get("id") or "")
            name = str(value.get("name") or "")
        else:
            identifier = str(value)
            name = ""
        if not identifier:
            continue
        terms.append(
            {
                "id": identifier,
                "name": name or snapshot.node_by_id.get(identifier, {}).get("label", identifier),
            }
        )
    return terms


def _mechanism_label(snapshot: Snapshot, edge: dict[str, Any]) -> str:
    properties = edge.get("properties", {})
    label = properties.get("mechanism_label")
    if isinstance(label, str) and label.strip():
        return label
    terms = _shared_terms(snapshot, edge)
    if terms:
        return ", ".join(term["name"] for term in terms[:2])
    if float(properties.get("string_mech") or 0) >= 0.7:
        return (
            "physical/curated interaction "
            f"(STRING exp/db {float(properties['string_mech']):.2f})"
        )
    return "shared mechanism evidence is not assigned"


def _variant_class_evidence(
    snapshot: Snapshot,
    gene_id: str,
    class_name: str,
) -> dict[str, Any] | None:
    for class_edge in snapshot.edges_by_subject.get(gene_id, []):
        if class_edge["predicate"] != "has_variant_class":
            continue
        if class_edge["object_label"].casefold() != class_name.casefold():
            continue
        quote = class_edge.get("quote")
        refs = [class_edge["edge_id"]]
        if not quote:
            for claim in snapshot.edges:
                if (
                    claim["predicate"] == "claims"
                    and claim.get("properties", {}).get("gene_id") == gene_id
                    and claim["object_label"].casefold()
                    == class_edge["object_label"].casefold()
                    and claim.get("quote")
                ):
                    quote = claim["quote"]
                    refs.append(claim["edge_id"])
                    break
        if quote:
            return {
                "class": class_edge["object_label"],
                "quote": " ".join(str(quote).split()),
                "edge_ids": refs,
            }
    return None


def build_dossier(
    snapshot: Snapshot,
    disease_id: str,
    persona: str = "maria",
    *,
    mode: str = "offline",
    comparator_id: str | None = None,
) -> dict[str, Any]:
    disease = snapshot.node_by_id.get(disease_id)
    if not disease:
        raise KeyError(disease_id)
    properties = disease.get("properties", {})
    gene_id = properties.get("gene_id", "")
    gene_symbol = properties.get("gene_symbol", disease["label"])
    coverage_refs = _coverage_edge_ids(snapshot, disease_id)
    cluster = snapshot.cluster_for(disease_id)
    member_ids = set(cluster.get("member_ids", [])) if cluster else {disease_id}
    cluster_rows = []
    for other_id in member_ids - {disease_id}:
        other = snapshot.node_by_id.get(other_id)
        pair_edges = _pair_edges(snapshot, disease_id, other_id)
        if not other or not pair_edges:
            continue
        edge = max(
            pair_edges,
            key=lambda row: float(row.get("properties", {}).get("S") or 0),
        )
        cluster_rows.append(
            {
                "disease_id": other_id,
                "label": _member_label(other),
                "edge": edge,
                "score": float(edge.get("properties", {}).get("S") or 0),
            }
        )
    cluster_rows.sort(key=lambda row: (-row["score"], row["label"].casefold()))
    partial_rows = _partial_overlap_rows(snapshot, disease_id)
    partial_prose_rows = partial_rows[:3]
    selected_partial = next(
        (row for row in partial_rows if row["disease_id"] == comparator_id),
        None,
    )
    if selected_partial and selected_partial not in partial_prose_rows:
        partial_prose_rows.append(selected_partial)
    shared_sentences = []
    if cluster:
        membership_edge = next(
            (
                edge
                for edge in snapshot.edges_by_subject.get(disease_id, [])
                if edge["predicate"] == "member_of" and edge["object"] == cluster["id"]
            ),
            None,
        )
        if membership_edge:
            cluster_label = cluster.get("label") or "shared-pathway"
            if persona == "devon":
                membership_text = (
                    f"{gene_symbol} is grouped with {len(member_ids)} related diseases "
                    f"under {cluster_label}."
                )
            elif persona == "osei":
                membership_text = (
                    f"Verify {gene_symbol} membership in {cluster_label} "
                    f"(stability {cluster.get('stability', 0):.2f})."
                )
            elif persona == "priya":
                membership_text = (
                    f"Mechanism cluster label: {cluster_label}; membership stability "
                    f"{cluster.get('stability', 0):.2f}."
                )
            else:
                membership_text = (
                    f"{gene_symbol}-related disease is grouped in the "
                    f"{cluster_label} research cluster."
                )
            shared_sentences.append(
                _sentence(membership_text, [membership_edge["edge_id"]])
            )
    for row in cluster_rows:
        edge = row["edge"]
        mechanism = _mechanism_label(snapshot, edge)
        terms = _shared_terms(snapshot, edge)
        term_ids = ", ".join(term["id"] for term in terms[:2])
        if persona == "devon":
            text = f"{row['label']} shares a research pattern with {gene_symbol}."
        elif persona == "osei":
            text = (
                f"Verify {row['label']}: shared mechanism evidence is {mechanism} "
                f"(S {row['score']:.2f})."
            )
        elif persona == "priya":
            identifiers = term_ids or "STRING channel evidence"
            text = (
                f"Mechanism first: {mechanism} links {row['label']} "
                f"(terms {identifiers}; S {row['score']:.2f})."
            )
        else:
            text = (
                f"{row['label']} shares mechanism evidence with {gene_symbol}: "
                f"{mechanism}."
            )
        shared_sentences.append(_sentence(text, [edge["edge_id"]]))
    if not shared_sentences and coverage_refs:
        shared_sentences.append(
            _sentence(
                "No other supported mechanism neighbour is mapped in the current snapshot.",
                coverage_refs[:3],
            )
        )

    cluster_assets = disease_assets(snapshot, disease_id)
    asset_sentences = []
    for asset in cluster_assets[:3]:
        serves_edges = [
            edge
            for edge in snapshot.edges_by_subject.get(asset["id"], [])
            if edge["predicate"] == "serves" and edge["object"] in member_ids
        ]
        if not serves_edges:
            continue
        served = ", ".join(
            _member_label(snapshot.node_by_id[edge["object"]])
            for edge in serves_edges[:2]
            if edge["object"] in snapshot.node_by_id
        )
        if persona in {"maria", "devon"}:
            asset_properties = asset.get("properties", {})
            record_id = asset_properties.get("record_id")
            asset_name = (
                f"{asset['label']} ({record_id})" if record_id else asset["label"]
            )
            if (
                asset_properties.get("asset_type") == "natural_history_study"
                and asset_properties.get("overall_status") == "RECRUITING"
            ):
                text = f"The {asset_name} already enrolls {gene_symbol} patients."
            elif asset_properties.get("asset_type") == "natural_history_study":
                text = f"The {asset_name} is a study record for {gene_symbol} patients."
            else:
                text = (
                    f"The {asset_name} is a reusable research resource for "
                    f"{gene_symbol}."
                )
        elif persona == "osei":
            text = f"Verify cluster asset {asset['label']} and its mapped disease links."
        else:
            text = f"Cluster asset {asset['label']} is mapped to {served}."
        asset_sentences.append(
            _sentence(text, [edge["edge_id"] for edge in serves_edges[:2]])
        )

    coverage_candidates = []
    comparator_neighbor = next(
        (
            row
            for row in [*cluster_rows, *partial_rows]
            if row["disease_id"] == comparator_id
        ),
        None,
    )
    top_neighbor = comparator_neighbor or (cluster_rows[0] if cluster_rows else None)
    top_neighbor_label = (
        top_neighbor["label"] if top_neighbor else "the leading neighbour"
    )
    if top_neighbor:
        neighbor_id = top_neighbor["disease_id"]
        neighbor_coverage_refs = _coverage_edge_ids(snapshot, neighbor_id, limit=3)
        for asset in cluster_assets:
            coverage = asset_coverage(asset, neighbor_id, snapshot)
            serves_edges = [
                edge
                for edge in snapshot.edges_by_subject.get(asset["id"], [])
                if edge["predicate"] == "serves" and edge["object"] in member_ids
            ]
            if coverage is None or not serves_edges:
                continue
            measure_edges = [
                edge
                for edge in snapshot.edges_by_subject.get(asset["id"], [])
                if edge["predicate"] == "measures_phenotype"
            ]
            refs = [
                serves_edges[0]["edge_id"],
                *neighbor_coverage_refs,
                *(edge["edge_id"] for edge in measure_edges[:2]),
                top_neighbor["edge"]["edge_id"],
            ]
            coverage_candidates.append((asset, coverage, refs))
    coverage_candidates.sort(
        key=lambda row: (
            -float(row[1].get("value") or 0),
            -int(row[1].get("n_matched") or 0),
            row[0]["label"].casefold(),
        )
    )
    best_coverage = coverage_candidates[0] if coverage_candidates else None
    if best_coverage:
        asset, coverage, refs = best_coverage
        percent = round(float(coverage["value"]) * 100)
        if persona in {"maria", "devon"}:
            text = (
                f"{asset['label']} covers {percent}% of "
                f"{top_neighbor_label} phenotype information "
                f"({coverage['n_matched']} of {coverage['n_total']} terms)."
            )
        else:
            text = (
                f"{asset['label']} is mapped to {top_neighbor_label}; it covers {percent}% "
                f"of the target disease phenotype information "
                f"({coverage['n_matched']} of {coverage['n_total']} terms; IC "
                f"{coverage['numerator_ic']:.2f}/{coverage['denominator_ic']:.2f})."
            )
        asset_sentences.append(_sentence(text, refs))

    exact_group_sentences = []
    for group in sorted(snapshot.nodes, key=lambda node: node.get("label", "").casefold()):
        if group.get("type") != "patient_group":
            continue
        group_properties = group.get("properties", {})
        if group_properties.get("match_kind", "exact") != "exact":
            continue
        serves_edges = [
            edge
            for edge in snapshot.edges_by_subject.get(group["id"], [])
            if edge["predicate"] == "serves" and edge["object"] in member_ids
        ]
        for edge in serves_edges[:1]:
            if persona == "devon":
                text = f"{group['label']} is a disease-specific foundation."
            elif persona == "osei":
                text = f"Verify exact foundation link: {group['label']}."
            else:
                text = f"Exact-match foundation {group['label']} supports this disease group."
            exact_group_sentences.append(_sentence(text, [edge["edge_id"]]))
    asset_sentences.extend(exact_group_sentences[:3])
    if not asset_sentences and coverage_refs:
        asset_sentences.append(
            _sentence(
                "No reusable cluster asset or exact-match foundation is mapped.",
                coverage_refs[:3],
            )
        )

    differs = []
    variant_differences = []
    for row in [*cluster_rows, *partial_prose_rows]:
        pair_edge = row["edge"]
        pair_properties = pair_edge.get("properties", {})
        if float(pair_properties.get("V") or 0) != 0:
            continue
        if pair_edge["subject"] == disease_id:
            class_self = pair_properties.get("variant_class_a", "unknown")
            class_other = pair_properties.get("variant_class_b", "unknown")
        else:
            class_self = pair_properties.get("variant_class_b", "unknown")
            class_other = pair_properties.get("variant_class_a", "unknown")
        other_gene_id = snapshot.node_by_id[row["disease_id"]].get("properties", {}).get(
            "gene_id", ""
        )
        evidence_self = _variant_class_evidence(snapshot, gene_id, str(class_self))
        evidence_other = _variant_class_evidence(snapshot, other_gene_id, str(class_other))
        if not evidence_self or not evidence_other:
            continue
        if persona == "devon":
            text = (
                f"Variant evidence differs (V=0): {gene_symbol} ({evidence_self['class']}), "
                f"“{evidence_self['quote']}”; {row['label']} "
                f"({evidence_other['class']}), “{evidence_other['quote']}”."
            )
        elif persona == "osei":
            text = (
                f"Verify V=0 against {row['label']}: {gene_symbol} is {evidence_self['class']} "
                f"(“{evidence_self['quote']}”); the comparator is {evidence_other['class']} "
                f"(“{evidence_other['quote']}”)."
            )
        else:
            text = (
                f"Variant evidence differs (V=0) against {row['label']}: "
                f"{gene_symbol} is {evidence_self['class']} "
                f"(“{evidence_self['quote']}”); the comparator is {evidence_other['class']} "
                f"(“{evidence_other['quote']}”)."
            )
        variant_differences.append(
            _sentence(
                text,
                [
                    pair_edge["edge_id"],
                    *evidence_self["edge_ids"],
                    *evidence_other["edge_ids"],
                ],
            )
        )

    counterexample_id = cluster.get("counterexample_id") if cluster else None
    counterexample = snapshot.node_by_id.get(counterexample_id or "")
    if counterexample_id and counterexample:
        counterexample_edges = _pair_edges(snapshot, disease_id, counterexample_id)
        if counterexample_edges:
            counterexample_edge = max(
                counterexample_edges,
                key=lambda edge: float(edge.get("properties", {}).get("P") or 0),
            )
            counterexample_properties = counterexample_edge.get("properties", {})
            counterexample_label = _member_label(counterexample)
            p_score = float(counterexample_properties.get("P") or 0)
            m_score = float(counterexample_properties.get("M") or 0)
            if persona == "devon":
                text = (
                    f"{counterexample_label} looks similar clinically but shares no "
                    "supported mechanism, so it is excluded."
                )
            elif persona == "osei":
                text = (
                    f"Verify the counterexample {counterexample_label}: clinical "
                    f"similarity P={p_score:.2f}, mechanism M={m_score:.2f}; it is "
                    "excluded by the mechanism layer."
                )
            elif persona == "priya":
                text = (
                    f"Mechanism counterexample: {counterexample_label} has P={p_score:.2f} "
                    f"but M={m_score:.2f}, below support; it is excluded."
                )
            else:
                text = (
                    f"{counterexample_label} looks similar clinically (P {p_score:.2f}) "
                    f"but shares no supported mechanism (M = {m_score:.2f}), so it is "
                    "excluded."
                )
            differs.append(_sentence(text, [counterexample_edge["edge_id"]]))

    for row in partial_prose_rows:
        edge = row["edge"]
        terms = _shared_terms(snapshot, edge)
        if not terms:
            continue
        term_names = ", ".join(term["name"] for term in terms[:2])
        term_ids = ", ".join(term["id"] for term in terms[:2])
        term_count = len(terms)
        if persona == "devon":
            text = (
                f"{row['label']} overlaps only weakly at {term_names}, a related process "
                "but a different step."
            )
        elif persona == "osei":
            text = (
                f"Verify {row['label']}: {term_count} shared process term(s) "
                f"({term_names}; {term_ids}); M={row['M']:.2f}, S={row['S']:.2f}, "
                f"excluded by {row['excluded_by']} layer."
            )
        elif persona == "priya":
            text = (
                f"Mechanism first: {row['label']} overlaps at {term_names} "
                f"({term_ids}; M={row['M']:.2f}, S={row['S']:.2f})."
            )
        elif row["excluded_by"] == "M":
            text = (
                f"{row['label']} overlaps only weakly: {term_count} shared process "
                f"term(s) ({term_names}; {term_ids}); M = {row['M']:.2f} is below "
                "the 0.25 support threshold."
            )
        else:
            text = (
                f"{row['label']} shares {term_count} process term(s) "
                f"({term_names}; {term_ids}; M = {row['M']:.2f}) but S = "
                f"{row['S']:.2f} is below the 0.45 fused-score threshold."
            )
        refs = [
            edge["edge_id"],
            *_mechanism_edge_ids(
                snapshot,
                disease_id,
                str(row["disease_id"]),
                {term["id"] for term in terms},
            ),
        ]
        differs.append(_sentence(text, refs))

    differs.extend(variant_differences)
    relevant_papers = {
        edge["subject"]
        for edge in snapshot.edges_by_object.get(gene_id, [])
        if edge["predicate"] == "mentions_gene"
    }
    contradictions = [
        edge
        for edge in snapshot.edges
        if (
            edge.get("polarity") == "contradicts" or edge["predicate"] == "contradicts"
        )
        and (
            edge["subject"] in relevant_papers
            or edge["subject"] == gene_id
            or edge["object"] == gene_id
            or edge.get("properties", {}).get("gene_id") == gene_id
        )
    ]
    for edge in contradictions:
        quote = edge.get("quote")
        text = (
            f"Contradictory evidence is indexed: “{quote}”."
            if quote
            else "A contradiction-tagged source record is indexed for this disease."
        )
        differs.append(_sentence(text, [edge["edge_id"]]))
    if not differs and coverage_refs:
        differs.append(
            _sentence(
                "No quote-backed V=0 variant-class pair or contradiction edge is mapped.",
                coverage_refs[:3],
            )
        )

    def verified_bridges(
        comparator: dict[str, Any] | None,
    ) -> list[tuple[Any, list[str], str, str]]:
        if not comparator:
            return []
        rows = []
        comparator_node = snapshot.node_by_id.get(comparator["disease_id"], {})
        comparator_gene = comparator_node.get("properties", {}).get(
            "gene_symbol", comparator["label"]
        )
        for bridge in find_bridges(snapshot, disease_id, comparator["disease_id"]):
            proof_ids = list(
                dict.fromkeys(
                    proof["edge_id"]
                    for proof in bridge.get("proving_edges", [])
                    if proof.get("edge_id") in snapshot.edge_by_id
                )
            )[:3]
            if proof_ids:
                rows.append((bridge, proof_ids, comparator["label"], comparator_gene))
        return rows

    cluster_bridge_rows = verified_bridges(top_neighbor)
    top_partial = selected_partial or (partial_rows[0] if partial_rows else None)
    partial_bridge_rows = verified_bridges(top_partial)
    if comparator_neighbor:
        contact_rows = cluster_bridge_rows[:3]
    else:
        contact_rows = cluster_bridge_rows[: 2 if partial_bridge_rows else 3]
        seen_bridge_ids = {row[0]["id"] for row in contact_rows}
        if partial_bridge_rows:
            top_partial_bridge = next(
                (row for row in partial_bridge_rows if row[0]["id"] not in seen_bridge_ids),
                None,
            )
            if top_partial_bridge:
                contact_rows.append(top_partial_bridge)
                seen_bridge_ids.add(top_partial_bridge[0]["id"])
        for row in [*cluster_bridge_rows[2:], *partial_bridge_rows]:
            if len(contact_rows) >= 3:
                break
            if row[0]["id"] not in seen_bridge_ids:
                contact_rows.append(row)
                seen_bridge_ids.add(row[0]["id"])
    contact_sentences = []
    for bridge, proof_ids, comparator_label, comparator_gene in contact_rows[:3]:
        if persona in {"maria", "devon"}:
            institution = next(iter(bridge.get("affiliations", [])), "")
            institution_text = f" ({institution})" if institution else ""
            has_paper = any(
                proof.get("kind") == "paper"
                for proof in bridge.get("proving_edges", [])
            )
            relation = (
                "has published on both"
                if has_paper
                else "appears in evidence for both"
            )
            text = (
                f"{bridge['display_name']}{institution_text} {relation} "
                f"{gene_symbol}- and {comparator_gene}-related disease."
            )
        elif persona == "osei":
            text = (
                f"Verify {bridge['display_name']} with {comparator_label}: "
                f"{bridge['why_same_person']}."
            )
        else:
            text = (
                f"{bridge['display_name']} is a verified bridge to {comparator_label}; "
                f"signals: {bridge['why_same_person']}."
            )
        contact_sentences.append(_sentence(text, proof_ids))
    if not contact_sentences and coverage_refs:
        contact_sentences.append(
            _sentence(
                "No corroborated cross-disease investigator bridge is mapped.",
                coverage_refs[:3],
            )
        )

    next_refs = list(coverage_refs[:3])
    if best_coverage:
        next_refs.extend(best_coverage[2])
    if top_neighbor:
        next_refs.append(top_neighbor["edge"]["edge_id"])
    if contact_rows:
        next_refs.extend(contact_rows[0][1])
    if best_coverage and contact_rows:
        asset, coverage, _ = best_coverage
        bridge = contact_rows[0][0]
        if comparator_neighbor:
            record_id = asset.get("properties", {}).get("record_id")
            asset_name = (
                f"{asset['label']} ({record_id})" if record_id else asset["label"]
            )
            if persona == "devon":
                step_text = (
                    f"Review {asset_name} eligibility for {top_neighbor_label} "
                    f"and contact {bridge['display_name']}."
                )
            elif persona == "osei":
                step_text = (
                    f"Verify {asset_name} eligibility for {top_neighbor_label} and "
                    f"confirm {bridge['display_name']} using {bridge['why_same_person']}."
                )
            else:
                step_text = (
                    f"Review {asset_name} eligibility for {top_neighbor_label} and "
                    f"verify {bridge['display_name']} as a contact."
                )
        elif persona == "devon":
            step_text = f"Review {asset['label']} and contact {bridge['display_name']}."
        elif persona == "osei":
            step_text = (
                f"Verify {asset['label']} coverage ({coverage['n_matched']}/"
                f"{coverage['n_total']}) and confirm {bridge['display_name']} using "
                f"{bridge['why_same_person']}."
            )
        else:
            step_text = (
                f"Review {asset['label']} coverage for {top_neighbor_label} "
                f"({coverage['value']:.2f}, {coverage['n_matched']}/{coverage['n_total']}) "
                f"and verify {bridge['display_name']} as a contact."
            )
    elif best_coverage:
        asset, coverage, _ = best_coverage
        if comparator_neighbor:
            record_id = asset.get("properties", {}).get("record_id")
            asset_name = (
                f"{asset['label']} ({record_id})" if record_id else asset["label"]
            )
            step_text = (
                f"Review {asset_name} eligibility for {top_neighbor_label}; "
                "no verified bridge is mapped."
            )
        else:
            step_text = (
                f"Review {asset['label']} coverage of {top_neighbor_label} "
                f"({coverage['value']:.2f}, {coverage['n_matched']}/{coverage['n_total']}); "
                "no verified bridge is mapped."
            )
    elif contact_rows:
        bridge = contact_rows[0][0]
        step_text = (
            f"Verify {bridge['display_name']} as a cross-disease contact using "
            f"{bridge['why_same_person']}; no cluster asset coverage is mapped."
        )
    else:
        step_text = (
            "Use the indexed disease coverage to plan the next experiment; "
            "no reusable asset or verified bridge is mapped."
        )
    next_sentences = (
        [_sentence(step_text, next_refs)]
        if next_refs
        else []
    )
    phenotype_count = sum(
        edge["predicate"] == "has_phenotype"
        for edge in snapshot.edges_by_subject.get(disease_id, [])
    )
    if best_coverage:
        asset, coverage, _ = best_coverage
        percent = round(float(coverage["value"]) * 100)
        coverage_text = (
            f"Coverage of {top_neighbor_label} phenotype information is {percent}% "
            f"({coverage['n_matched']} of {coverage['n_total']} terms; IC "
            f"{coverage['numerator_ic']:.2f}/{coverage['denominator_ic']:.2f}) using "
            f"{asset['label']}."
        )
        coverage_refs = best_coverage[2]
    elif persona == "devon":
        coverage_text = f"The snapshot records {phenotype_count} phenotype links."
    elif persona == "osei":
        coverage_text = (
            f"Verify source coverage: {phenotype_count} disease phenotype associations "
            "are represented in this snapshot."
        )
    else:
        coverage_text = (
            f"The snapshot represents {phenotype_count} indexed phenotype associations; "
            "unreturned records are not evidence of absence."
        )
    coverage_sentences = (
        [_sentence(coverage_text, coverage_refs[:3])] if coverage_refs else []
    )
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
    if persona == "osei":
        sections.sort(
            key=lambda section: [
                "what_differs",
                "coverage",
                "who_shares",
                "what_exists",
                "who_to_contact",
                "next_step",
            ].index(section["key"])
        )
    elif persona == "priya":
        sections.sort(
            key=lambda section: [
                "who_shares",
                "what_exists",
                "what_differs",
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


async def build_live_dossier(
    snapshot: Snapshot,
    disease_id: str,
    persona: str,
    comparator_id: str | None = None,
) -> dict[str, Any]:
    from constellation.agents.live import run_agent_chain

    started = time.perf_counter()
    model = agent_model()
    draft = build_dossier(
        snapshot,
        disease_id,
        persona,
        mode="live",
        comparator_id=comparator_id,
    )
    evidence_pack = _live_evidence_pack(snapshot, disease_id, draft, comparator_id)
    pack_ids = {row["edge_id"] for row in evidence_pack}
    draft_numbers = _number_tokens(
        " ".join(
            sentence.get("text", "")
            for section in draft.get("sections", [])
            for sentence in section.get("sentences", [])
        )
    )
    draft_sections = _offline_section_map(draft)
    draft_next_step_anchors = _draft_next_step_anchors(draft_sections)
    comparator_node = snapshot.node_by_id.get(comparator_id or "", {})
    comparator_terms = {
        str(term)
        for term in (
            comparator_node.get("properties", {}).get("gene_symbol"),
            comparator_node.get("label"),
        )
        if term
    }
    try:
        remaining = 180 - (time.perf_counter() - started)
        if remaining <= 0:
            raise TimeoutError("Live dossier preparation exceeded 180 seconds.")
        generated = await asyncio.wait_for(
            run_agent_chain(
                snapshot,
                disease_id,
                persona,
                draft,
                comparator_id,
                evidence_pack,
            ),
            timeout=remaining,
        )
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
        validated = validate_dossier(generated, snapshot, disease_id)
        writer_keys = {
            section.get("key")
            for section in generated.get("sections", [])
            if isinstance(section, dict)
        }
        validated_by_key = {
            section["key"]: section
            for section in validated.get("sections", [])
            if section.get("key") in SECTION_TITLES
        }
        fallback_sections = []
        guard_dropped = 0
        final_sections = []
        slice_gene_symbols = _slice_gene_symbols(snapshot)
        for key, title in SECTION_TITLES.items():
            section = validated_by_key.get(key, {"key": key, "title": title, "sentences": []})
            kept = []
            for sentence in section.get("sentences", []):
                refs = list(dict.fromkeys(sentence.get("edge_ids", [])))
                if not refs or any(edge_id not in pack_ids for edge_id in refs):
                    guard_dropped += 1
                    continue
                cited_fields = json.dumps(
                    [snapshot.edge_by_id[edge_id] for edge_id in refs],
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                if not _number_tokens(sentence.get("text", "")) <= (
                    draft_numbers | _number_tokens(cited_fields)
                ):
                    guard_dropped += 1
                    continue
                mentioned_symbols = _mentioned_gene_symbols(
                    sentence.get("text", ""),
                    slice_gene_symbols,
                )
                if not mentioned_symbols <= _cited_gene_symbols(
                    snapshot,
                    refs,
                    slice_gene_symbols,
                ):
                    guard_dropped += 1
                    continue
                kept.append({**sentence, "edge_ids": refs})
            if key == "who_to_contact" and comparator_terms:
                comparator_kept = [
                    sentence
                    for sentence in kept
                    if _mentions_comparator(sentence.get("text", ""), comparator_terms)
                ]
                guard_dropped += len(kept) - len(comparator_kept)
                kept = comparator_kept
            if key == "next_step":
                next_step_text = " ".join(
                    sentence.get("text", "") for sentence in kept
                )
                drifted = bool(kept) and not _mentions_draft_anchor(
                    next_step_text,
                    draft_next_step_anchors,
                )
                if comparator_terms and kept and not _mentions_comparator(
                    next_step_text,
                    comparator_terms,
                ):
                    drifted = True
                if drifted:
                    guard_dropped += len(kept)
                    kept = []
            if key not in writer_keys or not kept:
                fallback = draft_sections.get(key)
                if fallback:
                    section = fallback
                    fallback_sections.append(key)
                else:
                    section = {"key": key, "title": title, "sentences": []}
            else:
                section = {**section, "sentences": kept}
            final_sections.append(section)

        validated.update(
            {
                "sections": final_sections,
                "dropped_sentences": int(validated.get("dropped_sentences", 0))
                + guard_dropped,
                "fallback_sections": fallback_sections,
                "model": model,
                "mode": "live",
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "comparator_id": comparator_id,
            }
        )
        validated["markdown"] = _markdown(final_sections)
        cache_file = live_dossier_cache_path(
            disease_id,
            persona,
            comparator_id,
            snapshot.snapshot_hash,
            model,
        )
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(validated, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return validated
    except Exception as error:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        reason = str(error)
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            reason = reason.replace(api_key, "[redacted]")
        logger.warning(
            "Live dossier fallback for %s (%s): %s",
            disease_id,
            type(error).__name__,
            reason,
        )
        fallback = dict(draft)
        fallback.update(
            {
                "mode": "offline-fallback",
                "model": model,
                "elapsed_ms": elapsed_ms,
                "fallback_sections": list(SECTION_TITLES),
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "comparator_id": comparator_id,
            }
        )
        return fallback
