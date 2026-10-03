from __future__ import annotations

from itertools import combinations
from typing import Any

import igraph as ig
import leidenalg


def adjusted_rand(left: list[int], right: list[int]) -> float:
    n = len(left)
    if n < 2:
        return 1.0
    contingency: dict[tuple[int, int], int] = {}
    left_counts: dict[int, int] = {}
    right_counts: dict[int, int] = {}
    for a, b in zip(left, right, strict=True):
        contingency[(a, b)] = contingency.get((a, b), 0) + 1
        left_counts[a] = left_counts.get(a, 0) + 1
        right_counts[b] = right_counts.get(b, 0) + 1

    def choose2(value: int) -> float:
        return value * (value - 1) / 2

    sum_cells = sum(choose2(value) for value in contingency.values())
    sum_left = sum(choose2(value) for value in left_counts.values())
    sum_right = sum(choose2(value) for value in right_counts.values())
    total = choose2(n)
    expected = sum_left * sum_right / total
    maximum = 0.5 * (sum_left + sum_right)
    return (sum_cells - expected) / (maximum - expected) if maximum != expected else 1.0


def leiden_sweep(
    disease_ids: list[str], scores: list[dict[str, Any]]
) -> tuple[dict[str, int], float, int, float]:
    index = {disease_id: position for position, disease_id in enumerate(disease_ids)}
    supported = [row for row in scores if row["supported"]]
    edges = [(index[row["disease_a"]], index[row["disease_b"]]) for row in supported]
    weights = [max(0.001, float(row["S"])) for row in supported]
    graph = ig.Graph(n=len(disease_ids), edges=edges, directed=False)
    graph.vs["name"] = disease_ids
    if weights:
        graph.es["weight"] = weights
    partitions: list[tuple[float, int, float, list[int]]] = []
    for resolution_index in range(9):
        resolution = round(0.6 + resolution_index * 0.1, 1)
        memberships = [
            leidenalg.find_partition(
                graph,
                leidenalg.RBConfigurationVertexPartition,
                weights="weight" if weights else None,
                seed=seed,
                resolution_parameter=resolution,
            ).membership
            for seed in range(20)
        ]
        stability = sum(adjusted_rand(a, b) for a, b in combinations(memberships, 2)) / 190
        partitions.append((resolution, 0, stability, memberships[0]))
    stable = next((row for row in partitions if row[2] >= 0.8), None)
    chosen = stable or max(partitions, key=lambda row: row[2])
    return (
        {disease_id: chosen[3][position] for position, disease_id in enumerate(disease_ids)},
        chosen[0],
        chosen[1],
        chosen[2],
    )


def cluster_records(
    disease_ids: list[str],
    scores: list[dict[str, Any]],
    disease_labels: dict[str, str],
    pathway_labels: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    membership, resolution, seed, stability = leiden_sweep(disease_ids, scores)
    grouped: dict[int, list[str]] = {}
    for disease_id, community in membership.items():
        grouped.setdefault(community, []).append(disease_id)
    clusters: list[dict[str, Any]] = []
    disease_lookup: dict[str, Any] = {}
    for community, members in sorted(grouped.items()):
        cluster_id = f"constellation:cluster/{community}"
        intra = [
            row
            for row in scores
            if row["disease_a"] in members and row["disease_b"] in members and row["supported"]
        ]
        pathways = sorted({term for row in intra for term in row["shared_pathways"]})
        labels = pathway_labels or {}
        label = (
            "shared Reactome pathways: "
            + ", ".join((labels.get(pathway) or pathway) for pathway in pathways[:2])
            if pathways
            else "shared STRING interaction mechanism"
            if any(float(row.get("string_score") or 0) >= 0.7 for row in intra)
            else "DEE mechanisms"
        )
        outsiders = [
            row for row in scores if (row["disease_a"] in members) != (row["disease_b"] in members)
        ]
        counter = max(outsiders, key=lambda row: row["P"], default=None)
        if counter:
            counter_id = (
                counter["disease_b"] if counter["disease_a"] in members else counter["disease_a"]
            )
            excluding = "M" if counter["M"] == 0 else "V" if counter["V"] == 0 else "S"
        else:
            counter_id, excluding = None, None
        clusters.append(
            {
                "id": cluster_id,
                "label": label,
                "resolution": resolution,
                "seed": seed,
                "stability": stability,
                "member_ids": sorted(members),
                "counterexample_id": counter_id,
                "excluded_by": excluding,
                "shared_pathways": pathways,
            }
        )
        for disease_id in members:
            disease_lookup[disease_id] = clusters[-1]
    return clusters, disease_lookup
