from __future__ import annotations

from typing import Any

from constellation.graph.store import Snapshot


def asset_coverage(
    asset: dict[str, Any], disease_id: str, snapshot: Snapshot
) -> dict[str, Any] | None:
    disease_terms = [
        edge["object"]
        for edge in snapshot.edges_by_subject.get(disease_id, [])
        if edge["predicate"] == "has_phenotype"
    ]
    if not disease_terms:
        return None
    outcomes = set(asset.get("properties", {}).get("outcome_phenotypes", []))
    parents = {
        node_id: set(node.get("properties", {}).get("parents", []))
        for node_id, node in snapshot.node_by_id.items()
        if node.get("type") == "phenotype"
    }
    covered: set[str] = set()
    for phenotype_id in disease_terms:
        if any(
            phenotype_id == outcome or phenotype_id in parents.get(outcome, set())
            for outcome in outcomes
        ):
            covered.add(phenotype_id)
    weights = {
        term: float(
            snapshot.node_by_id.get(term, {}).get("properties", {}).get("information_content", 1.0)
        )
        for term in disease_terms
    }
    numerator = sum(weights[term] for term in covered)
    denominator = sum(weights.values())
    return {
        "value": numerator / denominator if denominator else 0.0,
        "numerator_ic": numerator,
        "denominator_ic": denominator,
        "n_matched": len(covered),
        "n_total": len(disease_terms),
        "matched": [
            {"id": term, "label": snapshot.node_by_id.get(term, {}).get("label", term)}
            for term in sorted(covered)
        ],
        "unmatched": [
            {"id": term, "label": snapshot.node_by_id.get(term, {}).get("label", term)}
            for term in sorted(set(disease_terms) - covered)
        ],
    }


def eligibility_diff(
    asset: dict[str, Any], disease_id: str, snapshot: Snapshot
) -> list[dict[str, str]]:
    properties = asset.get("properties", {})
    target = snapshot.node_by_id.get(disease_id, {}).get("properties", {})
    asset_class = properties.get("variant_class", "unknown")
    target_class = target.get("variant_class", "unknown")
    status = (
        "needs_expert_review"
        if "unknown" in {asset_class, target_class}
        else ("matches" if asset_class == target_class else "differs")
    )
    minimum = properties.get("minimum_age", "")
    maximum = properties.get("maximum_age", "")
    age_window = f"{minimum or 'not specified'}–{maximum or 'not specified'}"
    genotype = properties.get("eligibility", "not specified")
    return [
        {
            "field": "age_window",
            "asset_value": age_window,
            "target_value": "not specified",
            "status": "needs_expert_review",
        },
        {
            "field": "genotype_requirement",
            "asset_value": genotype,
            "target_value": "not specified",
            "status": "needs_expert_review",
        },
        {
            "field": "variant_class",
            "asset_value": asset_class,
            "target_value": target_class,
            "status": status,
        },
    ]
