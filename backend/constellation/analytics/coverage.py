from __future__ import annotations

import re
from typing import Any

from constellation.graph.store import Snapshot


def _ancestors(term_id: str, parents: dict[str, set[str]]) -> set[str]:
    found: set[str] = set()
    pending = list(parents.get(term_id, set()))
    while pending:
        parent = pending.pop()
        if parent == term_id or parent in found:
            continue
        found.add(parent)
        pending.extend(parents.get(parent, set()))
    return found


def _asset_phenotype_profile(asset: dict[str, Any], snapshot: Snapshot) -> set[str]:
    properties = asset.get("properties", {})
    profile = set(properties.get("phenotype_profile", []))
    profile.update(properties.get("outcome_phenotypes", []))
    if profile:
        return profile
    for serves in snapshot.edges_by_subject.get(asset["id"], []):
        if serves["predicate"] != "serves":
            continue
        profile.update(
            edge["object"]
            for edge in snapshot.edges_by_subject.get(serves["object"], [])
            if edge["predicate"] == "has_phenotype"
        )
    return profile


def asset_coverage(
    asset: dict[str, Any], disease_id: str, snapshot: Snapshot
) -> dict[str, Any] | None:
    disease_terms = sorted(
        {
            edge["object"]
            for edge in snapshot.edges_by_subject.get(disease_id, [])
            if edge["predicate"] == "has_phenotype"
        }
    )
    if not disease_terms:
        return None
    profile = _asset_phenotype_profile(asset, snapshot)
    parents = {
        node_id: set(node.get("properties", {}).get("parents", []))
        for node_id, node in snapshot.node_by_id.items()
        if node.get("type") == "phenotype"
    }
    ancestors_by_profile = {term: _ancestors(term, parents) for term in profile}
    weights = {
        term: float(
            snapshot.node_by_id.get(term, {}).get("properties", {}).get("information_content", 0.0)
        )
        for term in disease_terms
    }
    matched: list[dict[str, Any]] = []
    for term in disease_terms:
        if term in profile:
            match_type = "exact"
            matched_by = term
        else:
            descendants = sorted(
                profile_term
                for profile_term, term_ancestors in ancestors_by_profile.items()
                if profile_term != term and term in term_ancestors
            )
            if descendants:
                match_type = "descendant"
                matched_by = descendants[0]
            else:
                term_ancestors = _ancestors(term, parents)
                target_ic = weights[term]
                qualifying_ancestors = sorted(
                    (
                        profile_term
                        for profile_term in profile
                        if profile_term != term
                        and profile_term in term_ancestors
                        and float(
                            snapshot.node_by_id.get(profile_term, {})
                            .get("properties", {})
                            .get("information_content", 0.0)
                        )
                        >= 0.5 * target_ic
                    ),
                    key=lambda profile_term: (
                        -float(
                            snapshot.node_by_id.get(profile_term, {})
                            .get("properties", {})
                            .get("information_content", 0.0)
                        ),
                        profile_term,
                    ),
                )
                if not qualifying_ancestors:
                    continue
                match_type = "ancestor"
                matched_by = qualifying_ancestors[0]
        matched.append(
            {
                "id": term,
                "label": snapshot.node_by_id.get(term, {}).get("label", term),
                "match_type": match_type,
                "matched_by": {
                    "id": matched_by,
                    "label": snapshot.node_by_id.get(matched_by, {}).get("label", matched_by),
                },
            }
        )

    matched_ids = {item["id"] for item in matched}
    numerator = sum(weights[term] for term in matched_ids)
    denominator = sum(weights.values())
    return {
        "value": numerator / denominator if denominator else 0.0,
        "numerator_ic": numerator,
        "denominator_ic": denominator,
        "n_matched": len(matched_ids),
        "n_total": len(disease_terms),
        "matched": sorted(matched, key=lambda item: item["id"]),
        "unmatched": [
            {"id": term, "label": snapshot.node_by_id.get(term, {}).get("label", term)}
            for term in sorted(set(disease_terms) - matched_ids)
        ],
    }


def _criteria_sections(criteria: str) -> tuple[str, str]:
    match = re.search(r"exclusion\s+criteria\s*:?", criteria, flags=re.IGNORECASE)
    if not match:
        return criteria, ""
    return criteria[: match.start()], criteria[match.end() :]


def _symbols_in(text: str, known_symbols: set[str]) -> set[str]:
    by_folded = {symbol.casefold(): symbol for symbol in known_symbols}
    return {
        by_folded[token.casefold()]
        for token in re.findall(r"\b[A-Za-z][A-Za-z0-9]*\b", text)
        if token.casefold() in by_folded
    }


def _display_genes(symbols: set[str]) -> str:
    ordered = sorted(symbols)
    if not ordered:
        return ""
    return " or ".join(ordered)


def _age_in_years(value: Any) -> float | None:
    text = str(value or "").strip().casefold()
    if not text:
        return None
    if text == "birth":
        return 0.0
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(day|days|week|weeks|month|months|year|years)", text)
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2)
    factors = {
        "day": 1 / 365,
        "days": 1 / 365,
        "week": 7 / 365,
        "weeks": 7 / 365,
        "month": 1 / 12,
        "months": 1 / 12,
        "year": 1,
        "years": 1,
    }
    return amount * factors[unit]


def _onset_range(term_id: str, label: str) -> tuple[float, float] | None:
    text = label.casefold()
    if any(word in text for word in ("prenatal", "antenatal", "fetal")):
        return -1.0, 0.0
    if "neonatal" in text:
        return 0.0, 28 / 365
    if any(word in text for word in ("infantile", "infancy")):
        return 0.0, 2.0
    if "childhood" in text:
        return 1.0, 18.0
    if "juvenile" in text:
        return 5.0, 18.0
    if "adolescent" in text:
        return 12.0, 20.0
    if "adult" in text:
        return 18.0, 120.0
    if "late" in text:
        return 40.0, 120.0
    explicit = re.search(
        r"(before|after)\s+(?:age\s*)?(\d+(?:\.\d+)?)\s*(years?|months?|days?)",
        text,
    )
    if explicit:
        cutoff = _age_in_years(f"{explicit.group(2)} {explicit.group(3)}")
        if cutoff is not None:
            return (0.0, cutoff) if explicit.group(1) == "before" else (cutoff, 120.0)
    return None


def _format_age(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else "not specified"


def _age_comparison(
    asset_properties: dict[str, Any],
    disease_id: str,
    snapshot: Snapshot,
    inclusion_text: str = "",
) -> dict[str, str]:
    minimum = _age_in_years(asset_properties.get("minimum_age"))
    maximum = _age_in_years(asset_properties.get("maximum_age"))
    age_value = (
        f"{_format_age(asset_properties.get('minimum_age'))}–"
        f"{_format_age(asset_properties.get('maximum_age'))}"
    )
    if minimum is None and maximum is None and re.search(
        r"\bany age\b", inclusion_text, re.IGNORECASE
    ):
        minimum, maximum = 0.0, 120.0
        age_value = "any age"
    disease_terms = [
        edge["object"]
        for edge in snapshot.edges_by_subject.get(disease_id, [])
        if edge["predicate"] == "has_phenotype"
    ]
    parents = {
        node_id: set(node.get("properties", {}).get("parents", []))
        for node_id, node in snapshot.node_by_id.items()
        if node.get("type") == "phenotype"
    }
    onset_terms = [
        term
        for term in disease_terms
        if "HP:0003674" in _ancestors(term, parents)
        or "onset" in snapshot.node_by_id.get(term, {}).get("label", "").casefold()
    ]
    onset_ranges = [
        bounds
        for term in onset_terms
        if (
            bounds := _onset_range(
                term,
                snapshot.node_by_id.get(term, {}).get("label", ""),
            )
        )
    ]
    target_labels = [
        snapshot.node_by_id.get(term, {}).get("label", term) for term in onset_terms
    ]
    target_value = ", ".join(sorted(set(target_labels))) if target_labels else "no HPO onset terms"
    status = "needs_expert_review"
    if minimum is not None and maximum is not None and onset_ranges:
        onset_min = min(bounds[0] for bounds in onset_ranges)
        onset_max = max(bounds[1] for bounds in onset_ranges)
        if minimum <= onset_min and maximum >= onset_max:
            status = "matches"
        elif maximum < onset_min or minimum > onset_max:
            status = "differs"
    return {
        "field": "age_window",
        "asset_value": age_value,
        "target_value": target_value,
        "status": status,
    }


def eligibility_diff(
    asset: dict[str, Any], disease_id: str, snapshot: Snapshot
) -> list[dict[str, str]]:
    properties = asset.get("properties", {})
    gene_symbol = str(
        snapshot.node_by_id.get(disease_id, {}).get("properties", {}).get("gene_symbol", "")
    )
    known_symbols = {
        str(node.get("properties", {}).get("symbol") or node.get("label"))
        for node in snapshot.nodes
        if node.get("type") == "gene"
    }
    known_symbols.discard("")
    inclusion, exclusions = _criteria_sections(str(properties.get("eligibility", "")))
    required_genes = _symbols_in(inclusion, known_symbols)
    target_variant = f"{gene_symbol} variant" if gene_symbol else "variant not specified"
    if required_genes:
        requirement = _display_genes(required_genes)
        genotype_status = "matches" if gene_symbol in required_genes else "differs"
        genotype_value = f"requires {requirement} variant"
    else:
        genotype_status = "needs_expert_review"
        genotype_value = "gene requirement not specified"

    exclusion_match = re.search(
        r"(?:confirmed|known|pathogenic)\s+(?:mutation|variant)\s+in\s+a\s+gene\s+other\s+than\s+(.+?)(?=\s+that\b|[.;\n]|$)",
        exclusions,
        flags=re.IGNORECASE,
    )
    excluded_other_genes = (
        _symbols_in(exclusion_match.group(1), known_symbols) if exclusion_match else set()
    )
    if exclusion_match and excluded_other_genes:
        exclusion_value = f"excludes mutations outside {_display_genes(excluded_other_genes)}"
        exclusion_status = (
            "matches" if gene_symbol in excluded_other_genes else "differs"
        )
    else:
        exclusion_value = "no comparator-specific gene exclusion found"
        exclusion_status = "needs_expert_review"

    rows = [
        {
            "field": "genotype_requirement",
            "asset_value": genotype_value,
            "target_value": target_variant,
            "status": genotype_status,
        },
        _age_comparison(properties, disease_id, snapshot, inclusion),
        {
            "field": "study_type",
            "asset_value": str(properties.get("study_type") or "not reported"),
            "target_value": "—",
            "status": "matches",
        },
        {
            "field": "overall_status",
            "asset_value": str(properties.get("overall_status") or "not reported"),
            "target_value": "—",
            "status": "matches",
        },
        {
            "field": "enrollment",
            "asset_value": str(properties.get("enrollment") or "not reported"),
            "target_value": "—",
            "status": "matches",
        },
        {
            "field": "n_locations",
            "asset_value": str(properties.get("n_locations") or 0),
            "target_value": "—",
            "status": "matches",
        },
        {
            "field": "exclusion_other_gene",
            "asset_value": exclusion_value,
            "target_value": target_variant,
            "status": exclusion_status,
        },
    ]
    return rows
