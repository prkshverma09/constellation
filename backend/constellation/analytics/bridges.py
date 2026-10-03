from __future__ import annotations

from collections import defaultdict
from typing import Any

import igraph as ig

from constellation.graph.store import Snapshot


def _diseases_linked_to_record(snapshot: Snapshot, record_id: str) -> set[str]:
    paper_genes = {
        edge["object"]
        for edge in snapshot.edges_by_subject.get(record_id, [])
        if edge["predicate"] == "mentions_gene"
    }
    study_genes = {
        edge["object"]
        for edge in snapshot.edges_by_subject.get(record_id, [])
        if edge["predicate"] == "studies_gene"
    }
    genes = paper_genes | study_genes
    return {
        edge["object"]
        for gene_id in genes
        for edge in snapshot.edges_by_subject.get(gene_id, [])
        if edge["predicate"] == "causes"
    }


def find_bridges(snapshot: Snapshot, disease_a: str, disease_b: str) -> list[dict[str, Any]]:
    target_a = {disease_a}
    target_b = {disease_b}
    authorships: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in snapshot.edges:
        if edge["predicate"] in {"authored", "investigates", "funds"}:
            authorships[edge["subject"]].append(edge)
    bridges: list[dict[str, Any]] = []
    graph_edges: set[tuple[str, str]] = set()
    for person_id, edges in authorships.items():
        proving: list[dict[str, Any]] = []
        hit_a: set[str] = set()
        hit_b: set[str] = set()
        for edge in edges:
            record_id = edge["object"]
            for disease_id in _diseases_linked_to_record(snapshot, record_id):
                if disease_id not in target_a | target_b:
                    continue
                if disease_id in target_a:
                    hit_a.add(record_id)
                if disease_id in target_b:
                    hit_b.add(record_id)
                proving.append(
                    {
                        "edge_id": edge["edge_id"],
                        "kind": "paper"
                        if record_id.startswith("PMID:")
                        else "award"
                        if record_id.startswith("NIH:")
                        else "study",
                        "record_id": record_id,
                        "title": snapshot.node_by_id.get(record_id, {}).get("label", record_id),
                        "year": snapshot.node_by_id.get(record_id, {})
                        .get("properties", {})
                        .get("year"),
                        "url": edge.get("source_url") or "",
                        "disease_id": disease_id,
                    }
                )
        if not hit_a or not hit_b:
            continue
        for record_id in hit_a | hit_b:
            graph_edges.add((person_id, record_id))
            for linked_disease in _diseases_linked_to_record(snapshot, record_id):
                graph_edges.add((record_id, linked_disease))
        person = snapshot.node_by_id.get(person_id, {})
        affiliations = person.get("properties", {}).get("affiliations", [])
        bridges.append(
            {
                "id": person_id,
                "display_name": person.get("label", person_id),
                "affiliations": affiliations,
                "roles": sorted(
                    {
                        {
                            "authored": "author",
                            "funds": "pi",
                            "investigates": "study_official",
                        }[edge["predicate"]]
                        for edge in edges
                    }
                ),
                "n_a": len(hit_a),
                "n_b": len(hit_b),
                "score": float(min(len(hit_a), len(hit_b))),
                "proving_edges": proving,
                "why_same_person": (
                    "The source identifies the same full name on both evidence paths; "
                    "the person record retains the source-reported affiliation."
                ),
                "_recency": max(
                    (
                        int(
                            snapshot.node_by_id.get(record_id, {}).get("properties", {}).get("year")
                            or 0
                        )
                        for record_id in hit_a | hit_b
                    ),
                    default=0,
                ),
            }
        )
    vertices = sorted({identifier for edge in graph_edges for identifier in edge})
    if vertices:
        index = {identifier: position for position, identifier in enumerate(vertices)}
        graph = ig.Graph(n=len(vertices), edges=[(index[a], index[b]) for a, b in graph_edges])
        centrality = dict(zip(vertices, graph.betweenness(directed=False), strict=True))
    else:
        centrality = {}
    for bridge in bridges:
        bridge["betweenness"] = centrality.get(bridge["id"], 0.0)
        bridge.pop("_recency", None)
    return sorted(
        bridges,
        key=lambda row: (
            min(row["n_a"], row["n_b"]),
            row["betweenness"],
            max(
                (
                    int(
                        snapshot.node_by_id.get(edge["record_id"], {})
                        .get("properties", {})
                        .get("year")
                        or 0
                    )
                    for edge in row["proving_edges"]
                ),
                default=0,
            ),
        ),
        reverse=True,
    )
