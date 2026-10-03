from __future__ import annotations

from typing import Any

from constellation.graph.store import Snapshot


def _disease_edges(snapshot: Snapshot, disease_id: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (edge["object"], edge)
        for edge in snapshot.edges_by_subject.get(disease_id, [])
        if edge["predicate"] in {"phenotypically_similar_to", "shares_mechanism_with"}
    ] + [
        (edge["subject"], edge)
        for edge in snapshot.edges_by_object.get(disease_id, [])
        if edge["predicate"] in {"phenotypically_similar_to", "shares_mechanism_with"}
    ]


def disease_assets(snapshot: Snapshot, disease_id: str) -> list[dict[str, Any]]:
    cluster = snapshot.cluster_for(disease_id)
    members = set(cluster.get("member_ids", [])) if cluster else {disease_id}
    return [
        asset
        for asset in snapshot.nodes
        if asset.get("type") == "asset"
        and any(
            edge["subject"] == asset["id"]
            and edge["predicate"] == "serves"
            and edge["object"] in members
            for edge in snapshot.edges
        )
    ]


def has_bridge_people(snapshot: Snapshot, disease_id: str) -> bool:
    cluster = snapshot.cluster_for(disease_id)
    members = set(cluster.get("member_ids", [])) if cluster else {disease_id}
    for person in (node for node in snapshot.nodes if node.get("type") == "person"):
        papers = [
            edge["object"]
            for edge in snapshot.edges_by_subject.get(person["id"], [])
            if edge["predicate"] in {"authored", "investigates", "funds"}
        ]
        linked = {
            disease_id
            for paper in papers
            for edge in snapshot.edges_by_subject.get(paper, [])
            if edge["predicate"] == "mentions_gene"
            for cause in snapshot.edges_by_subject.get(edge["object"], [])
            if cause["predicate"] == "causes"
            for disease_id in [cause["object"]]
        }
        if len(linked & members) >= 2:
            return True
    return False


def gap_reasons(snapshot: Snapshot, disease_id: str) -> list[str]:
    cluster = snapshot.cluster_for(disease_id)
    members = set(cluster.get("member_ids", [])) if cluster else {disease_id}
    supported = [
        edge
        for other_id, edge in _disease_edges(snapshot, disease_id)
        if edge["predicate"] == "shares_mechanism_with" and other_id in members
    ]
    reasons: list[str] = []
    if not supported:
        reasons.append("No within-cluster mechanism neighbour meets the P/M/V support threshold.")
    if not disease_assets(snapshot, disease_id) and not has_bridge_people(snapshot, disease_id):
        reasons.append("No reusable asset or investigator bridge is mapped to this cluster.")
    return reasons


def is_gap(snapshot: Snapshot, disease_id: str) -> bool:
    return bool(gap_reasons(snapshot, disease_id))


def nearest_leads(snapshot: Snapshot, disease_id: str) -> list[dict[str, Any]]:
    rows = []
    for other_id, edge in _disease_edges(snapshot, disease_id):
        properties = edge.get("properties", {})
        if properties.get("supported"):
            continue
        missing_layer = (
            "M" if properties.get("M", 0) == 0 else "V" if properties.get("V", 0) == 0 else "asset"
        )
        ref = snapshot.disease_ref(other_id)
        if ref:
            rows.append(
                {
                    "disease": ref,
                    "S": properties.get("S", 0),
                    "missing_layer": missing_layer,
                }
            )
    return sorted(rows, key=lambda row: row["S"], reverse=True)[:3]


def what_would_change(snapshot: Snapshot, disease_id: str) -> list[dict[str, Any]]:
    rows = []
    for lead in nearest_leads(snapshot, disease_id):
        layer = lead["missing_layer"]
        text = {
            "M": "A functional or pathway study could establish a shared disease mechanism.",
            "V": "Variant classification evidence could resolve whether the variant effects align.",
            "asset": "An existing protocol could be assessed for adaptation to this disease.",
            "people": "A cross-disease investigator link could identify a collaboration lead.",
        }[layer]
        citing = next(
            (
                edge["edge_id"]
                for other_id, edge in _disease_edges(snapshot, disease_id)
                if other_id == lead["disease"]["id"]
            ),
            None,
        )
        rows.append(
            {
                "layer": layer,
                "text": text,
                "edge_ids": [citing] if citing else [],
            }
        )
    if not rows:
        citing = next(iter(snapshot.edges_by_subject.get(disease_id, [])), None)
        rows.append(
            {
                "layer": "asset",
                "text": (
                    "A protocol or registry outcome mapped to this disease's phenotypes "
                    "could improve coverage."
                ),
                "edge_ids": [citing["edge_id"]] if citing else [],
            }
        )
    return rows
