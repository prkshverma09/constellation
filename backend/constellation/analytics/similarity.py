from __future__ import annotations

from collections import defaultdict
from typing import Any

WEIGHTS = {"P": 0.5, "M": 0.3, "V": 0.2, "threshold": 0.45}


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


def fused_similarity(
    disease_ids: list[str],
    disease_gene: dict[str, str],
    phenotypes: dict[str, set[str]],
    pathways: dict[str, set[str]],
    variant_classes: dict[str, str],
    string_scores: dict[tuple[str, str], float],
    semsim_scores: dict[tuple[str, str], float],
) -> list[dict[str, Any]]:
    maximum = max(semsim_scores.values(), default=0.0)
    rows: list[dict[str, Any]] = []
    for index, disease_a in enumerate(disease_ids):
        for disease_b in disease_ids[index + 1 :]:
            gene_a, gene_b = disease_gene[disease_a], disease_gene[disease_b]
            raw_p = semsim_scores.get(
                (disease_a, disease_b), semsim_scores.get((disease_b, disease_a), 0.0)
            )
            p = raw_p / maximum if maximum else 0.0
            pa, pb = pathways.get(gene_a, set()), pathways.get(gene_b, set())
            union = pa | pb
            jaccard = len(pa & pb) / len(union) if union else 0.0
            string_score = string_scores.get((min(gene_a, gene_b), max(gene_a, gene_b)), 0.0)
            m = max(jaccard, 1.0 if string_score >= 0.7 else 0.0)
            va, vb = variant_classes.get(gene_a, "unknown"), variant_classes.get(gene_b, "unknown")
            v = 1.0 if va == vb and va != "unknown" else 0.5 if "unknown" in {va, vb} else 0.0
            score = 0.5 * p + 0.3 * m + 0.2 * v
            shared = sorted(pa & pb)
            rows.append(
                {
                    "disease_a": disease_a,
                    "disease_b": disease_b,
                    "gene_a": gene_a,
                    "gene_b": gene_b,
                    "P": p,
                    "M": m,
                    "V": v,
                    "S": score,
                    "supported": score >= 0.45 and m > 0,
                    "shared_pathways": shared,
                    "string_score": string_score or None,
                    "variant_class_a": va,
                    "variant_class_b": vb,
                    "phenotype_count": len(
                        phenotypes.get(disease_a, set()) & phenotypes.get(disease_b, set())
                    ),
                    "layer_edges": defaultdict(list),
                }
            )
    return rows
