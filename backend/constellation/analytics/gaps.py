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
        reasons.append(
            "No neighbour meets both S ≥ 0.45 and M ≥ 0.25 in the indexed cluster."
        )
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
            "M"
            if float(properties.get("M") or 0) < 0.25
            else "V"
            if float(properties.get("V") or 0) < 0.25
            else "P"
            if float(properties.get("P") or 0) < 0.45
            else "S"
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
    disease_node = snapshot.node_by_id.get(disease_id, {})
    disease_properties = disease_node.get("properties", {})
    gene_symbol = disease_properties.get("gene_symbol", disease_node.get("label", "gene"))
    gene_id = disease_properties.get("gene_id", "")
    known_terms: dict[str, tuple[str, int, str]] = {}
    for edge in snapshot.edges_by_subject.get(gene_id, []):
        if edge["predicate"] not in {"annotated_to", "participates_in"}:
            continue
        if edge.get("source") not in {"go", "reactome"}:
            continue
        term = snapshot.node_by_id.get(edge["object"], {})
        count = int(
            term.get("properties", {}).get("human_gene_count")
            or edge.get("properties", {}).get("human_gene_count")
            or 1_000_000
        )
        known_terms[edge["object"]] = (term.get("label", edge["object"]), count, edge["edge_id"])
    specific_terms = sorted(
        known_terms.items(),
        key=lambda item: (item[1][1], item[1][0].casefold(), item[0]),
    )[:2]
    term_text = ", ".join(f"{name} ({term_id})" for term_id, (name, _, _) in specific_terms)
    term_refs = [edge_id for _, (_, _, edge_id) in specific_terms]
    coverage_edges = [
        edge["edge_id"]
        for edge in snapshot.edges_by_subject.get(disease_id, [])
        if edge["predicate"] in {"has_phenotype", "source_query", "has_source_count"}
    ][:3]
    if not coverage_edges:
        coverage_edges = [
            edge["edge_id"]
            for edge in snapshot.edges_by_subject.get(disease_id, [])
            if edge["predicate"] == "has_phenotype"
        ][:3]
    rows = []
    for lead in nearest_leads(snapshot, disease_id):
        layer = lead["missing_layer"]
        lead_symbol = lead["disease"]["gene_symbol"]
        pair_edges = [
            edge
            for other_id, edge in _disease_edges(snapshot, disease_id)
            if other_id == lead["disease"]["id"]
        ]
        pair_edge = max(
            pair_edges,
            key=lambda edge: float(edge.get("properties", {}).get("S") or 0),
            default=None,
        )
        refs = [pair_edge["edge_id"]] if pair_edge else []
        if layer == "M":
            process_terms = f" ({term_text})" if term_text else ""
            text = (
                f"A functional study testing whether {gene_symbol}{process_terms} "
                f"participates in a mechanism shared with {lead_symbol} would populate "
                "the mechanism layer."
            )
            refs.extend(term_refs)
        elif layer == "V":
            variant_refs = [
                edge["edge_id"]
                for symbol in (gene_symbol, lead_symbol)
                for gene in snapshot.nodes
                if gene.get("type") == "gene"
                and gene.get("properties", {}).get("symbol") == symbol
                for edge in snapshot.edges_by_subject.get(gene["id"], [])
                if edge["predicate"] == "has_variant_class"
            ][:4]
            text = (
                f"A curated variant-effect assay comparing {gene_symbol} and "
                f"{lead_symbol} variant classes would clarify their functional "
                "concordance in the variant-effect layer."
            )
            refs.extend(variant_refs)
        elif layer == "P":
            phenotype_refs = [
                edge["edge_id"]
                for candidate_id in (disease_id, lead["disease"]["id"])
                for edge in snapshot.edges_by_subject.get(candidate_id, [])
                if edge["predicate"] == "has_phenotype"
            ][:6]
            text = (
                f"A curated case series recording age of onset and specific HPO terms "
                f"for {gene_symbol} and {lead_symbol} would strengthen the phenotype "
                "layer."
            )
            refs.extend(phenotype_refs)
        elif layer == "S":
            text = (
                f"A larger case-control phenotype dataset comparing {gene_symbol} "
                f"with {lead_symbol} could raise the fused score above the 0.45 "
                "threshold."
            )
        elif layer == "asset":
            text = (
                f"A natural-history protocol with prospectively collected outcome "
                f"measures mapped to {gene_symbol} phenotypes, and assessed against "
                f"{lead_symbol}, would add reusable-asset evidence."
            )
            refs.extend(coverage_edges)
        else:
            text = (
                f"A cross-disease co-authorship or institutional-affiliation record "
                f"linking investigators for {gene_symbol} and {lead_symbol} would "
                "populate the bridge-person layer."
            )
        rows.append(
            {
                "layer": layer,
                "text": text,
                "edge_ids": list(dict.fromkeys(refs or coverage_edges)),
            }
        )
    if not rows:
        rows.append(
            {
                "layer": "asset",
                "text": (
                    f"A natural-history protocol with outcome measures mapped to "
                    f"{gene_symbol} phenotypes could improve phenotype coverage."
                ),
                "edge_ids": coverage_edges,
            }
        )
    return rows
