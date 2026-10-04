from __future__ import annotations

import hashlib
import json
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
    if properties.get("gene_symbol") == "DNM1":
        return "DNM1-related disease (developmental and epileptic encephalopathy 31)"
    return properties.get("gene_symbol") or node.get("label", node["id"])


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
    shared_sentences = []
    for row in cluster_rows:
        edge = row["edge"]
        mechanism = _mechanism_label(snapshot, edge)
        terms = _shared_terms(snapshot, edge)
        term_ids = ", ".join(term["id"] for term in terms[:2])
        if persona == "devon":
            text = f"{row['label']} is in the same research cluster."
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
            support_note = (
                "supported"
                if edge.get("properties", {}).get("supported")
                else "below the direct-pair support threshold"
            )
            text = (
                f"{row['label']} is a Leiden-cluster member (S {row['score']:.2f}, "
                f"{support_note}); "
                f"shared mechanism: {mechanism}."
            )
        shared_sentences.append(_sentence(text, [edge["edge_id"]]))
    if not shared_sentences and coverage_refs:
        shared_sentences.append(
            _sentence(
                "No other Leiden-cluster member is mapped in the current snapshot.",
                coverage_refs[:3],
            )
        )

    counterexample = cluster.get("counterexample_id") if cluster else None
    if cluster and counterexample and counterexample not in member_ids:
        counter_edges = _pair_edges(snapshot, disease_id, counterexample)
        if counter_edges:
            counter_edge = max(
                counter_edges,
                key=lambda row: float(row.get("properties", {}).get("P") or 0),
            )
            excluded_by = str(cluster.get("excluded_by") or "")
            exclusion = {
                "M": "M=0 (no mechanism support)",
                "V": "V=0 (variant classes differ)",
                "S": "S is below the support threshold",
            }.get(excluded_by, "the fused support rule")
            shared_sentences.append(
                _sentence(
                    f"The highest-phenotype non-member counterexample is excluded because "
                    f"{exclusion}.",
                    [counter_edge["edge_id"]],
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
        if persona == "devon":
            text = f"{asset['label']} is a possible cluster resource."
        elif persona == "osei":
            text = f"Verify cluster asset {asset['label']} and its mapped disease links."
        else:
            text = f"Cluster asset {asset['label']} is mapped to {served}."
        asset_sentences.append(
            _sentence(text, [edge["edge_id"] for edge in serves_edges[:2]])
        )

    coverage_candidates = []
    top_neighbor = cluster_rows[0] if cluster_rows else None
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
        if persona == "devon":
            text = (
                f"The leading neighbour has {coverage['n_matched']} of "
                f"{coverage['n_total']} phenotype terms covered by {asset['label']}."
            )
        elif persona == "osei":
            text = (
                f"Verify coverage for {top_neighbor_label}: {asset['label']} scores "
                f"{coverage['value']:.2f} ({coverage['n_matched']}/{coverage['n_total']})."
            )
        else:
            text = (
                f"For the top-S neighbour {top_neighbor_label}, {asset['label']} has "
                f"coverage {coverage['value']:.2f} "
                f"({coverage['n_matched']}/{coverage['n_total']} phenotype terms)."
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
                text = f"Exact-match foundation {group['label']} is mapped to this cluster."
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
    for row in [*cluster_rows, *partial_rows]:
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
                f"Variant evidence differs: {gene_symbol} ({evidence_self['class']}), "
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
                f"V=0 against {row['label']}: {gene_symbol} is {evidence_self['class']} "
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

    for row in partial_rows:
        edge = row["edge"]
        terms = _shared_terms(snapshot, edge)
        if not terms:
            continue
        term_names = ", ".join(term["name"] for term in terms[:2])
        term_ids = ", ".join(term["id"] for term in terms[:2])
        if persona == "devon":
            text = (
                f"{row['label']}: same process, different step / weak overlap at "
                f"{term_names} (M={row['M']:.2f})."
            )
        elif persona == "osei":
            text = (
                f"Verify {row['label']}: same process, different step / weak overlap at "
                f"{term_names} ({term_ids}; M={row['M']:.2f}, excluded by "
                f"{row['excluded_by']} layer)."
            )
        elif persona == "priya":
            text = (
                f"Mechanism first: {row['label']} has a same process, different step / "
                f"weak overlap at {term_names} ({term_ids}; M={row['M']:.2f})."
            )
        else:
            text = (
                f"{row['label']} has a same process, different step / weak overlap at "
                f"{term_names} (M={row['M']:.2f}; excluded by {row['excluded_by']} layer)."
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

    def verified_bridges(comparator: dict[str, Any] | None) -> list[tuple[Any, list[str], str]]:
        if not comparator:
            return []
        rows = []
        for bridge in find_bridges(snapshot, disease_id, comparator["disease_id"]):
            proof_ids = list(
                dict.fromkeys(
                    proof["edge_id"]
                    for proof in bridge.get("proving_edges", [])
                    if proof.get("edge_id") in snapshot.edge_by_id
                )
            )
            if proof_ids:
                rows.append((bridge, proof_ids, comparator["label"]))
        return rows

    cluster_bridge_rows = verified_bridges(top_neighbor)
    top_partial = partial_rows[0] if partial_rows else None
    partial_bridge_rows = verified_bridges(top_partial)
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
    for bridge, proof_ids, comparator_label in contact_rows[:3]:
        if persona == "devon":
            text = (
                f"{bridge['display_name']} is a verified bridge to {comparator_label}."
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
        if persona == "devon":
            step_text = f"Review {asset['label']} and contact {bridge['display_name']}."
        elif persona == "osei":
            step_text = (
                f"Verify {asset['label']} coverage ({coverage['n_matched']}/"
                f"{coverage['n_total']}) and confirm {bridge['display_name']} using "
                f"{bridge['why_same_person']}."
            )
        else:
            step_text = (
                f"Review {asset['label']} for the top-S neighbour "
                f"({coverage['value']:.2f}, {coverage['n_matched']}/{coverage['n_total']}) "
                f"and verify {bridge['display_name']} as a contact."
            )
    elif best_coverage:
        asset, coverage, _ = best_coverage
        step_text = (
            f"Review {asset['label']} coverage for {top_neighbor_label} "
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
    if persona == "devon":
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
