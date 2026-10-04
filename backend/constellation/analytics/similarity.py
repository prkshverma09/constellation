from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from math import log2
from typing import Any

WEIGHTS = {"P": 0.5, "M": 0.3, "V": 0.2, "threshold": 0.45}


def string_mechanism_score(escore: float, dscore: float) -> float:
    return 1 - (1 - escore) * (1 - dscore)


def lowest_level_pathways(
    pathways_by_gene: dict[str, set[str]],
    parents: dict[str, set[str]],
) -> dict[str, set[str]]:
    if not parents:
        return {gene: set() for gene in pathways_by_gene}

    def is_ancestor(ancestor: str, pathway: str) -> bool:
        pending = list(parents.get(pathway, set()))
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == ancestor:
                return True
            if current not in visited:
                visited.add(current)
                pending.extend(parents.get(current, set()))
        return False

    return {
        gene: {
            pathway
            for pathway in pathways
            if not any(pathway != other and is_ancestor(pathway, other) for other in pathways)
        }
        for gene, pathways in pathways_by_gene.items()
    }


def pathway_depths(parents: dict[str, set[str]]) -> dict[str, int]:
    depths: dict[str, int] = {}

    def depth(pathway: str, visiting: set[str]) -> int:
        if pathway in depths:
            return depths[pathway]
        if pathway in visiting:
            return 0
        next_visiting = visiting | {pathway}
        parent_depths = [
            depth(parent, next_visiting)
            for parent in parents.get(pathway, set())
        ]
        value = 1 + max(parent_depths, default=-1) if parent_depths else 0
        depths[pathway] = value
        return value

    for pathway in set(parents) | {parent for values in parents.values() for parent in values}:
        depth(pathway, set())
    return depths


def fused_similarity(
    disease_ids: list[str],
    disease_gene: dict[str, str],
    phenotypes: dict[str, set[str]],
    pathways: dict[str, set[str]],
    variant_classes: dict[str, str],
    string_scores: Mapping[tuple[str, str], float | Mapping[str, float]],
    semsim_scores: dict[tuple[str, str], float],
    go_terms: dict[str, set[str]] | None = None,
    go_counts: dict[str, int] | None = None,
    reactome_depths: dict[str, int] | None = None,
    reactome_counts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    maximum = max(semsim_scores.values(), default=0.0)
    go_terms = go_terms or {}
    go_counts = go_counts or {}
    reactome_depths = reactome_depths or {}
    reactome_counts = reactome_counts or {}
    rows: list[dict[str, Any]] = []
    for index, disease_a in enumerate(disease_ids):
        for disease_b in disease_ids[index + 1 :]:
            gene_a, gene_b = disease_gene[disease_a], disease_gene[disease_b]
            raw_p = semsim_scores.get(
                (disease_a, disease_b), semsim_scores.get((disease_b, disease_a), 0.0)
            )
            p = raw_p / maximum if maximum else 0.0
            reactome_a, reactome_b = pathways.get(gene_a, set()), pathways.get(gene_b, set())
            reactome_union = reactome_a | reactome_b
            m_reactome = (
                len(reactome_a & reactome_b) / len(reactome_union) if reactome_union else 0.0
            )
            go_a, go_b = go_terms.get(gene_a, set()), go_terms.get(gene_b, set())
            go_union = go_a | go_b
            m_go = len(go_a & go_b) / len(go_union) if go_union else 0.0
            string_value = string_scores.get(
                (min(gene_a, gene_b), max(gene_a, gene_b)), 0.0
            )
            if isinstance(string_value, Mapping):
                string_score = float(string_value.get("string_score", 0.0))
                string_mech = float(string_value.get("string_mech", 0.0))
            else:
                string_score = float(string_value)
                string_mech = float(string_value)
            m = max(m_reactome, m_go, 1.0 if string_mech >= 0.7 else 0.0)
            va, vb = variant_classes.get(gene_a, "unknown"), variant_classes.get(gene_b, "unknown")
            v = 1.0 if va == vb and va != "unknown" else 0.5 if "unknown" in {va, vb} else 0.0
            score = 0.5 * p + 0.3 * m + 0.2 * v
            shared = (reactome_a & reactome_b) | (go_a & go_b)

            def term_specificity(term: str) -> float:
                if term.startswith("GO:"):
                    return log2(501 / (go_counts.get(term, 500) + 1))
                if term in reactome_counts:
                    return log2(501 / (reactome_counts[term] + 1))
                return float(reactome_depths.get(term, 0))

            shared_ordered = sorted(
                shared,
                key=lambda term: (-term_specificity(term), term),
            )
            rows.append(
                {
                    "disease_a": disease_a,
                    "disease_b": disease_b,
                    "gene_a": gene_a,
                    "gene_b": gene_b,
                    "P": p,
                    "M": m,
                    "M_reactome": m_reactome,
                    "M_go": m_go,
                    "string_mech": string_mech,
                    "V": v,
                    "S": score,
                    "supported": score >= 0.45 and m >= 0.25,
                    "shared_pathways": shared_ordered,
                    "string_score": string_score if string_score else None,
                    "variant_class_a": va,
                    "variant_class_b": vb,
                    "phenotype_count": len(
                        phenotypes.get(disease_a, set()) & phenotypes.get(disease_b, set())
                    ),
                    "layer_edges": defaultdict(list),
                }
            )
    return rows
