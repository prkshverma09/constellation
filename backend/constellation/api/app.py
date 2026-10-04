from __future__ import annotations

import json
import os
import time
import zipfile
from io import BytesIO, StringIO
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, HttpUrl

from constellation.agents.dossier import (
    build_dossier,
    build_live_dossier,
    dossier_cache_path,
    live_dossier_cache_path,
)
from constellation.analytics.bridges import find_bridges_with_unverified
from constellation.analytics.coverage import asset_coverage, eligibility_diff
from constellation.analytics.gaps import (
    gap_reasons,
    is_gap,
    nearest_leads,
    what_would_change,
)
from constellation.config import CACHE, DATA, SNAPSHOT, agent_model, configured_mode
from constellation.graph.store import Snapshot
from constellation.ledger import make_edge

app = FastAPI(title="Constellation API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3100",
        "http://127.0.0.1:3100",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DossierRequest(BaseModel):
    disease: str
    persona: Literal["maria", "devon", "priya", "osei"] = "maria"
    vs: str | None = None
    regenerate: bool = False


class ContributeRequest(BaseModel):
    url: HttpUrl
    sentence: str = Field(min_length=1, max_length=2000)
    edge_id: str
    polarity: Literal["supports", "contradicts"]


def get_snapshot() -> Snapshot:
    if not (SNAPSHOT / "nodes.parquet").exists():
        raise HTTPException(status_code=503, detail="Snapshot is not built; run `make data`.")
    return Snapshot()


def _disease_for_node(snapshot: Snapshot, node_id: str) -> dict[str, Any] | None:
    node = snapshot.node_by_id.get(node_id)
    if node and node["type"] == "disease":
        return snapshot.disease_ref(node_id)
    if node and node["type"] == "gene":
        return next(
            (
                snapshot.disease_ref(edge["object"])
                for edge in snapshot.edges_by_subject.get(node_id, [])
                if edge["predicate"] == "causes"
            ),
            None,
        )
    disease_ids = {
        edge["object"]
        for edge in snapshot.edges_by_subject.get(node_id, [])
        if edge["predicate"] == "serves"
    }
    if not disease_ids:
        linked_genes = {
            edge["subject"]
            for edge in snapshot.edges_by_object.get(node_id, [])
            if edge["predicate"] == "has_phenotype"
        }
        disease_ids |= linked_genes
    if not disease_ids and node:
        linked_genes = {
            edge["subject"]
            for edge in snapshot.edges_by_object.get(node_id, [])
            if edge["predicate"] == "participates_in"
        }
        disease_ids |= {
            edge["object"]
            for gene_id in linked_genes
            for edge in snapshot.edges_by_subject.get(gene_id, [])
            if edge["predicate"] == "causes"
        }
    return snapshot.disease_ref(sorted(disease_ids)[0]) if disease_ids else None


def _search_hits(snapshot: Snapshot, query: str) -> list[dict[str, Any]]:
    q = query.strip().casefold()
    if not q:
        return []
    hits = []
    rank = {"id": 0, "symbol": 1, "label": 2, "alias": 3, "synonym": 4, "org": 5}
    for node in snapshot.nodes:
        kind = node.get("type")
        if not isinstance(kind, str):
            continue
        node_type = {
            "disease": "disease",
            "gene": "gene",
            "phenotype": "phenotype",
            "patient_group": "patient_group",
            "mechanism": "mechanism",
        }.get(kind)
        if not node_type:
            continue
        properties = node.get("properties", {})
        values: list[tuple[str, str]] = []
        if q == node["id"].casefold():
            values.append(("id", node["id"]))
        if q == node["label"].casefold():
            values.append(("org" if node_type == "patient_group" else "label", node["label"]))
        if node_type == "gene" and q == str(properties.get("symbol", "")).casefold():
            values.append(("symbol", properties["symbol"]))
        for alias in properties.get("aliases", []) or []:
            if q == str(alias).casefold():
                values.append(("alias", alias))
        for synonym in properties.get("synonyms", []) or []:
            if q == str(synonym).casefold():
                values.append(("synonym", synonym))
        if not values:
            continue
        match_kind, matched = min(values, key=lambda item: rank[item[0]])
        hits.append(
            {
                "id": node["id"],
                "type": node_type,
                "label": node["label"],
                "matched_text": matched,
                "match_kind": match_kind,
                "disease": _disease_for_node(snapshot, node["id"]),
                "_rank": rank[match_kind],
            }
        )
    hits.sort(key=lambda hit: (hit["_rank"], hit["label"].casefold()))
    for hit in hits:
        hit.pop("_rank")
    return hits


def _gene_edges(snapshot: Snapshot, disease_id: str, predicate: str) -> list[dict[str, Any]]:
    disease = snapshot.node_by_id.get(disease_id)
    gene_id = disease.get("properties", {}).get("gene_id") if disease else None
    return [
        edge
        for edge in snapshot.edges_by_subject.get(gene_id or "", [])
        if edge["predicate"] == predicate
    ]


def _count(
    edges: list[dict[str, Any]],
    count: int | None = None,
) -> dict[str, Any]:
    return {
        "n": count if count is not None else len(edges),
        "edge_ids": [edge["edge_id"] for edge in edges[:50]],
    }


def _source_count(source_key: str) -> int | None:
    try:
        metrics = json.loads((DATA / "source_metrics.json").read_text(encoding="utf-8"))
        return int(metrics[source_key]["count"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _neighbor_rows(snapshot: Snapshot, disease_id: str) -> list[dict[str, Any]]:
    edges = [
        edge
        for edge in (
            snapshot.edges_by_subject.get(disease_id, [])
            + snapshot.edges_by_object.get(disease_id, [])
        )
        if edge["predicate"] in {"shares_mechanism_with", "phenotypically_similar_to"}
    ]
    unique: dict[str, dict[str, Any]] = {}
    for edge in edges:
        properties = edge.get("properties", {})
        other = edge["object"] if edge["subject"] == disease_id else edge["subject"]
        ref = snapshot.disease_ref(other)
        if not ref:
            continue
        gene_a = snapshot.node_by_id.get(disease_id, {}).get("properties", {}).get("gene_id")
        gene_b = snapshot.node_by_id.get(other, {}).get("properties", {}).get("gene_id")
        pathways = []
        for pathway_id in properties.get("shared_pathways", []):
            pathway = snapshot.node_by_id.get(pathway_id, {})
            pathways.append({"id": pathway_id, "name": pathway.get("label", pathway_id)})
        p_edges = [
            row["edge_id"]
            for source in (disease_id, other)
            for row in snapshot.edges_by_subject.get(source, [])
            if row["predicate"] == "has_phenotype"
        ][:30]
        m_edges = [
            row["edge_id"]
            for gene_id in (gene_a, gene_b)
            for row in snapshot.edges_by_subject.get(gene_id or "", [])
            if row["predicate"] in {"participates_in", "annotated_to"}
            and (
                not properties.get("shared_pathways")
                or row["object"] in properties["shared_pathways"]
                or row.get("source") == "string"
            )
        ][:30]
        v_edges = [
            row["edge_id"]
            for gene_id in (gene_a, gene_b)
            for row in snapshot.edges_by_subject.get(gene_id or "", [])
            if row["predicate"] == "has_variant_class"
        ]
        variant_a = properties.get("variant_class_a", "unknown")
        variant_b = properties.get("variant_class_b", "unknown")
        if edge["subject"] != disease_id:
            variant_a, variant_b = variant_b, variant_a
        unique[other] = {
            "disease": ref,
            "P": float(properties.get("P", 0)),
            "M": float(properties.get("M", 0)),
            "M_reactome": float(properties.get("M_reactome", 0)),
            "M_go": float(properties.get("M_go", 0)),
            "string_mech": float(properties.get("string_mech", 0)),
            "V": float(properties.get("V", 0)),
            "S": float(properties.get("S", 0)),
            "supported": bool(properties.get("supported")),
            "mechanism_label": (
                properties.get("mechanism_label")
                or "; ".join(item["name"] for item in pathways[:2])
                or (
                    f"physical/curated interaction "
                    f"(STRING exp/db {properties.get('string_mech', 0):.2f})"
                    if properties.get("string_mech", 0) >= 0.7
                    else ""
                )
            ),
            "shared_pathways": pathways,
            "string_score": properties.get("string_score"),
            "variant_class_a": variant_a,
            "variant_class_b": variant_b,
            "edge_id": edge["edge_id"],
            "layer_edge_ids": {"P": p_edges, "M": m_edges, "V": v_edges},
        }
    return sorted(unique.values(), key=lambda row: row["S"], reverse=True)


@app.get("/api/health")
def health() -> dict[str, Any]:
    snapshot = get_snapshot()
    mode = configured_mode()
    if not mode:
        mode = "cached" if any(CACHE.glob("*.json")) else "offline"
    return {
        "status": "ok",
        "snapshot_hash": snapshot.snapshot_hash,
        "llm_mode": mode,
        "llm_available": bool(os.environ.get("OPENAI_API_KEY")) and mode != "offline",
        "agent_model": agent_model(),
        "counts": {"nodes": len(snapshot.nodes), "edges": len(snapshot.edges)},
    }


@app.get("/api/search")
def search(q: str = Query(default="")) -> dict[str, Any]:
    hits = _search_hits(get_snapshot(), q)
    return {"query": q, "best": hits[0] if hits else None, "results": hits[:50]}


@app.get("/api/disease/{mondo}")
def disease_overview(mondo: str) -> dict[str, Any]:
    snapshot = get_snapshot()
    disease_id = mondo
    disease = snapshot.node_by_id.get(disease_id)
    if not disease or disease.get("type") != "disease":
        raise HTTPException(status_code=404, detail="Disease not found.")
    properties = disease.get("properties", {})
    gene_id = properties.get("gene_id", "")
    phenotype_edges = [
        edge
        for edge in snapshot.edges_by_subject.get(disease_id, [])
        if edge["predicate"] == "has_phenotype"
    ]
    variant_edges = [
        edge
        for edge in snapshot.edges_by_subject.get(gene_id, [])
        if edge["predicate"] == "has_variant_class"
    ]
    trial_edges = [
        edge
        for edge in snapshot.edges_by_object.get(gene_id, [])
        if edge["predicate"] == "studies_gene" and edge["subject"].startswith("NCT:")
    ]
    award_edges = [
        edge
        for edge in snapshot.edges_by_object.get(gene_id, [])
        if edge["predicate"] == "studies_gene"
        and edge["subject"].startswith("NIH:")
        and snapshot.node_by_id.get(edge["subject"], {}).get("properties", {}).get("active")
    ]
    paper_edges = [
        edge
        for edge in snapshot.edges_by_subject.get(gene_id, [])
        if edge["predicate"] == "mentions_gene"
    ]
    if not paper_edges:
        paper_ids = {
            edge["subject"]
            for edge in snapshot.edges_by_object.get(gene_id, [])
            if edge["predicate"] == "mentions_gene"
        }
        paper_edges = [
            edge
            for paper_id in paper_ids
            for edge in snapshot.edges_by_subject.get(paper_id, [])
            if edge["predicate"] == "mentions_gene"
        ]
    groups = [
        edge
        for edge in snapshot.edges_by_object.get(disease_id, [])
        if edge["predicate"] == "serves"
    ]
    groups = [
        edge
        for edge in groups
        if snapshot.node_by_id.get(edge["subject"], {}).get("type") == "patient_group"
    ]
    groups.sort(
        key=lambda edge: (
            edge.get("properties", {}).get("match_kind", "exact") != "exact",
            edge["subject_label"].casefold(),
        )
    )
    pathogenic_count = sum(
        int(edge.get("properties", {}).get("n_pathogenic", 0)) for edge in variant_edges
    )
    cause_edge = next(
        (
            edge
            for edge in snapshot.edges_by_object.get(disease_id, [])
            if edge["predicate"] == "causes"
        ),
        None,
    )
    technical_edge = (
        phenotype_edges or variant_edges or groups or ([cause_edge] if cause_edge else [])
    )
    technical = []
    plain = []
    cluster = snapshot.cluster_for(disease_id)
    gap = is_gap(snapshot, disease_id)
    member_ids = set(cluster.get("member_ids", [])) - {disease_id} if cluster else set()
    member_edges = [
        edge
        for edge in snapshot.edges_by_subject.get(disease_id, [])
        if edge["predicate"] == "member_of"
    ]
    supported_members = [
        row
        for row in _neighbor_rows(snapshot, disease_id)
        if row["supported"] and row["disease"]["id"] in member_ids
    ]
    cluster_node = (
        snapshot.node_by_id.get(member_edges[0]["object"], {}) if member_edges else {}
    )
    cluster_label = (cluster or {}).get("label") or cluster_node.get("label")
    member_names = [
        snapshot.node_by_id[member_id]["properties"].get("gene_symbol")
        or snapshot.node_by_id[member_id]["label"]
        for member_id in sorted(member_ids)
        if member_id in snapshot.node_by_id
    ][:3]
    membership_refs = [
        edge["edge_id"] for edge in member_edges[:1]
    ] + [row["edge_id"] for row in supported_members[:1]]
    if gap:
        gap_refs = [edge["edge_id"] for edge in phenotype_edges[:3]]
        if cause_edge:
            gap_refs.append(cause_edge["edge_id"])
        no_neighbor = {
            "text": (
                f"No supported mechanism neighbour exists yet for "
                f"{properties.get('gene_symbol', disease['label'])} in the indexed snapshot."
            ),
            "edge_ids": list(dict.fromkeys(gap_refs)),
        }
        technical.append(no_neighbor)
        plain.append(no_neighbor)

        specific_go_edges = []
        for edge in snapshot.edges_by_subject.get(gene_id, []):
            if edge.get("source") != "go" or edge["predicate"] != "annotated_to":
                continue
            term = snapshot.node_by_id.get(edge["object"], {})
            term_count = int(
                edge.get("properties", {}).get("human_gene_count")
                or term.get("properties", {}).get("human_gene_count")
                or 1_000_000
            )
            if term_count <= 500:
                specific_go_edges.append(
                    (
                        term_count,
                        str(term.get("label", edge["object"])),
                        edge["object"],
                        edge["edge_id"],
                    )
                )
        specific_go_edges.sort(key=lambda row: (row[0], row[1].casefold(), row[2]))
        specific_go_edges = list(
            {row[2]: row for row in specific_go_edges}.values()
        )[:3]
        if specific_go_edges:
            go_terms = ", ".join(
                f"{label} ({term_id})"
                for _, label, term_id, _ in specific_go_edges
            )
            go_sentence = {
                "text": (
                    f"{properties.get('gene_symbol', disease['label'])} has specific "
                    f"GO annotations for {go_terms}."
                ),
                "edge_ids": [row[3] for row in specific_go_edges],
            }
        else:
            go_sentence = {
                "text": (
                    f"No specific GO annotation is currently mapped for "
                    f"{properties.get('gene_symbol', disease['label'])}."
                ),
                "edge_ids": list(dict.fromkeys(gap_refs)),
            }
        technical.append(go_sentence)
        plain.append(go_sentence)
    elif membership_refs:
        member_text = ", ".join(member_names) or "related diseases"
        technical.append(
            {
                "text": (
                    f"{properties.get('gene_symbol', '')} is grouped with {member_text} "
                    f"under {cluster_label or 'the indexed mechanism cluster'}."
                ),
                "edge_ids": list(dict.fromkeys(membership_refs)),
            }
        )
        plain.append(
            {
                "text": (
                    f"The evidence groups {properties.get('gene_symbol', '')} with "
                    f"related diseases under {cluster_label or 'a shared research pattern'}."
                ),
                "edge_ids": list(dict.fromkeys(membership_refs)),
            }
        )
    if variant_edges:
        variant_edge = variant_edges[0]
        variant_class = variant_edge.get("object_label", "reported variant class")
        source = variant_edge.get("source", "indexed source")
        source_label = {"clinvar": "ClinVar", "orphanet": "Orphanet"}.get(
            source.casefold(), source
        )
        technical.append(
            {
                "text": (
                    f"Variant-class evidence records {variant_class} for "
                    f"{properties.get('gene_symbol', '')} in {source_label}."
                ),
                "edge_ids": [variant_edge["edge_id"]],
            }
        )
        plain.append(
            {
                "text": (
                    f"{properties.get('gene_symbol', '')} has a reported "
                    f"{variant_class} variant class in {source_label} records."
                ),
                "edge_ids": [variant_edge["edge_id"]],
            }
        )
    counterexample_id = cluster.get("counterexample_id") if cluster else None
    counterexample = next(
        (
            row
            for row in _neighbor_rows(snapshot, disease_id)
            if row["disease"]["id"] == counterexample_id
        ),
        None,
    )
    if counterexample and not gap:
        symbol = properties.get("gene_symbol", disease["label"])
        label = counterexample["disease"]["gene_symbol"]
        technical.append(
            {
                "text": (
                    f"{label}-related disease looks clinically similar "
                    f"(P {counterexample['P']:.2f}) but shares no supported mechanism "
                    f"(M = {counterexample['M']:.2f}), so it is excluded from the "
                    f"{symbol} cluster."
                ),
                "edge_ids": [counterexample["edge_id"]],
            }
        )
        plain.append(
            {
                "text": (
                    f"{label}-related disease looks similar in reported symptoms but "
                    "does not share enough mechanism evidence to join this group."
                ),
                "edge_ids": [counterexample["edge_id"]],
            }
        )
    if not technical and technical_edge:
        ref = technical_edge[0]["edge_id"]
        technical.append(
            {
                "text": (
                    f"The snapshot links {disease['label']} to "
                    f"{len(phenotype_edges)} phenotype terms and "
                    f"{len(variant_edges)} variant-effect classifications."
                ),
                "edge_ids": [ref],
            }
        )
        plain.append(
            {
                "text": (
                    f"The snapshot includes {len(phenotype_edges)} phenotype records "
                    "and evidence about variants."
                ),
                "edge_ids": [ref],
            }
        )
    return {
        "disease": {
            "id": disease_id,
            "name": disease["label"],
            "gene_id": gene_id,
            "gene_symbol": properties.get("gene_symbol", ""),
            "synonyms": properties.get("synonyms", []),
            "omim": properties.get("omim"),
            "orphacode": properties.get("orphacode"),
        },
        "counts": {
            "phenotypes": _count(phenotype_edges),
            "pathogenic_variants": _count(variant_edges, pathogenic_count),
            "trials": _count(
                trial_edges,
                _source_count(f"ctgov:{properties.get('gene_symbol', '')}"),
            ),
            "awards_active": _count(award_edges),
            "papers": _count(paper_edges),
            "patient_groups": _count(groups),
        },
        "patient_groups": [
            {
                "id": edge["subject"],
                "name": edge["subject_label"],
                "url": edge.get("source_url")
                or snapshot.node_by_id.get(edge["subject"], {})
                .get("properties", {})
                .get("url", ""),
                "edge_id": edge["edge_id"],
                "evidence_class": edge["evidence_class"],
                "match_kind": edge.get("properties", {}).get("match_kind", "exact"),
            }
            for edge in groups
        ],
        "summary": {"technical": technical, "plain": plain},
        "is_gap": is_gap(snapshot, disease_id),
    }


@app.get("/api/disease/{mondo}/cluster")
def disease_cluster(mondo: str) -> dict[str, Any]:
    snapshot = get_snapshot()
    disease_id = mondo
    if (
        disease_id not in snapshot.node_by_id
        or snapshot.node_by_id[disease_id].get("type") != "disease"
    ):
        raise HTTPException(status_code=404, detail="Disease not found.")
    slice_diseases = [
        disease
        for node in snapshot.nodes
        if node.get("type") == "disease"
        if (disease := snapshot.disease_ref(node["id"])) is not None
    ]
    cluster = snapshot.cluster_for(disease_id)
    if not cluster:
        return {
            "disease_id": disease_id,
            "cluster": None,
            "neighbours": [],
            "partial_overlaps": [],
            "slice_diseases": sorted(
                slice_diseases,
                key=lambda disease: (disease["gene_symbol"].casefold(), disease["id"]),
            ),
            "counterexample": None,
            "nearest_leads": [],
            "weights": {"P": 0.5, "M": 0.3, "V": 0.2, "threshold": 0.45},
        }
    neighbors = _neighbor_rows(snapshot, disease_id)
    supported = [row for row in neighbors if row["supported"]]
    nearest = [row for row in neighbors if not row["supported"]][:3]
    partial_overlaps = []
    for row in neighbors:
        mechanism = float(row["M"])
        score = float(row["S"])
        if 0 < mechanism < 0.25:
            excluded_by = "M"
        elif mechanism >= 0.25 and score < 0.45:
            excluded_by = "S"
        else:
            continue
        partial_overlaps.append({**row, "excluded_by": excluded_by})
    partial_overlaps.sort(
        key=lambda row: (
            -row["M"],
            -row["S"],
            row["disease"]["gene_symbol"].casefold(),
        )
    )
    counter_id = cluster.get("counterexample_id")
    counterexample = next((row for row in neighbors if row["disease"]["id"] == counter_id), None)
    if counterexample:
        counterexample = {**counterexample, "excluded_by": cluster.get("excluded_by") or "S"}
    cluster_payload = {
        key: cluster[key]
        for key in ("id", "label", "resolution", "seed", "stability", "member_ids")
    }
    return {
        "disease_id": disease_id,
        "cluster": cluster_payload,
        "neighbours": supported,
        "partial_overlaps": partial_overlaps,
        "slice_diseases": sorted(
            slice_diseases,
            key=lambda disease: (disease["gene_symbol"].casefold(), disease["id"]),
        ),
        "counterexample": counterexample,
        "nearest_leads": nearest,
        "weights": {"P": 0.5, "M": 0.3, "V": 0.2, "threshold": 0.45},
    }


@app.get("/api/edge/{edge_id}")
def edge_detail(edge_id: str) -> dict[str, Any]:
    snapshot = get_snapshot()
    edge = snapshot.edge_by_id.get(edge_id)
    if not edge:
        proposed_path = DATA / "proposed.jsonl"
        if proposed_path.exists():
            for line in proposed_path.read_text(encoding="utf-8").splitlines():
                proposed = json.loads(line)
                if proposed.get("edge_id") == edge_id:
                    return {**proposed, "contradicting": []}
        raise HTTPException(status_code=404, detail="Edge not found.")
    contradictions = [
        row
        for row in snapshot.edges
        if row["edge_id"] in edge.get("contradicted_by", [])
        or (
            row["subject"] == edge["subject"]
            and row["object"] == edge["object"]
            and row.get("polarity") == "contradicts"
        )
    ]
    return {**edge, "contradicting": contradictions}


@app.get("/api/cluster/{cluster_id:path}/assets")
def assets_for_cluster(cluster_id: str, for_disease: str = Query(alias="for")) -> dict[str, Any]:
    snapshot = get_snapshot()
    cluster = snapshot.node_by_id.get(cluster_id)
    if not cluster or cluster.get("type") != "cluster":
        raise HTTPException(status_code=404, detail="Cluster not found.")
    if (
        for_disease not in snapshot.node_by_id
        or snapshot.node_by_id[for_disease].get("type") != "disease"
    ):
        raise HTTPException(status_code=404, detail="Disease not found.")
    members = set(cluster.get("properties", {}).get("member_ids", []))
    if for_disease not in members:
        members.add(for_disease)
    assets = []
    for asset in snapshot.nodes:
        if asset.get("type") != "asset":
            continue
        serves_edges = [
            edge
            for edge in snapshot.edges_by_subject.get(asset["id"], [])
            if edge["predicate"] == "serves"
        ]
        if not any(edge["object"] in members for edge in serves_edges):
            continue
        properties = asset.get("properties", {})
        assets.append(
            {
                "id": asset["id"],
                "name": asset["label"],
                "asset_type": properties.get("asset_type", "protocol"),
                "url": properties.get("url", ""),
                "record_id": properties.get("record_id", ""),
                "serves": [
                    snapshot.disease_ref(edge["object"])
                    for edge in serves_edges
                    if snapshot.disease_ref(edge["object"])
                ],
                "coverage": asset_coverage(asset, for_disease, snapshot),
                "eligibility_diff": eligibility_diff(asset, for_disease, snapshot),
                "eligibility_text": properties.get("eligibility", ""),
                "edge_ids": [
                    edge["edge_id"] for edge in snapshot.edges_by_subject.get(asset["id"], [])
                ],
            }
        )
    return {"cluster_id": cluster_id, "for_disease": for_disease, "assets": assets}


@app.get("/api/bridges")
def bridges(a: str, b: str) -> dict[str, Any]:
    snapshot = get_snapshot()
    if (
        a not in snapshot.node_by_id
        or b not in snapshot.node_by_id
        or snapshot.node_by_id[a].get("type") != "disease"
        or snapshot.node_by_id[b].get("type") != "disease"
    ):
        raise HTTPException(status_code=404, detail="Disease not found.")
    people, unverified = find_bridges_with_unverified(snapshot, a, b)
    return {
        "a": snapshot.disease_ref(a),
        "b": snapshot.disease_ref(b),
        "people": people,
        "unverified_name_matches": unverified,
    }


@app.get("/api/disease/{mondo}/coverage")
def coverage(mondo: str) -> dict[str, Any]:
    snapshot = get_snapshot()
    disease_id = mondo
    disease = snapshot.node_by_id.get(disease_id)
    if not disease or disease.get("type") != "disease":
        raise HTTPException(status_code=404, detail="Disease not found.")
    gene_symbol = disease.get("properties", {}).get("gene_symbol", "")
    metrics_path = DATA / "source_metrics.json"
    try:
        all_metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        all_metrics = {}
    sources = [
        value
        for key, value in all_metrics.items()
        if key.endswith(gene_symbol) or key == "NCT06555965"
    ]
    reasons = gap_reasons(snapshot, disease_id)
    return {
        "disease_id": disease_id,
        "is_gap": bool(reasons),
        "reasons": reasons,
        "sources": sources,
        "nearest_leads": nearest_leads(snapshot, disease_id),
        "what_would_change": what_would_change(snapshot, disease_id),
    }


@app.post("/api/dossier")
async def dossier(request: DossierRequest) -> dict[str, Any]:
    started = time.perf_counter()
    snapshot = get_snapshot()
    if (
        request.disease not in snapshot.node_by_id
        or snapshot.node_by_id[request.disease].get("type") != "disease"
    ):
        raise HTTPException(status_code=404, detail="Disease not found.")
    mode = configured_mode()
    if not mode:
        mode = "cached" if any(CACHE.glob("*.json")) else "offline"
    cache_file = dossier_cache_path(
        request.disease, request.persona, snapshot.snapshot_hash, "offline"
    )
    live_cache = live_dossier_cache_path(
        request.disease,
        request.persona,
        request.vs,
        snapshot.snapshot_hash,
        agent_model(),
    )
    llm_available = bool(os.environ.get("OPENAI_API_KEY")) and mode != "offline"
    if llm_available and (mode == "live" or request.regenerate):
        result = await build_live_dossier(
            snapshot,
            request.disease,
            request.persona,
            request.vs,
        )
        result.setdefault("model", agent_model())
        result.setdefault("elapsed_ms", 0)
        result.setdefault("usage", {"input_tokens": 0, "output_tokens": 0})
        return result
    if mode == "cached" and live_cache.exists():
        result = json.loads(live_cache.read_text(encoding="utf-8"))
        result["mode"] = "cached-llm"
        result.setdefault("model", agent_model())
        result.setdefault("elapsed_ms", round((time.perf_counter() - started) * 1000))
        result.setdefault("usage", {"input_tokens": 0, "output_tokens": 0})
        return result
    if request.vs is None and cache_file.exists() and mode != "offline":
        result = json.loads(cache_file.read_text(encoding="utf-8"))
        result["mode"] = "cached"
        result.setdefault("model", "offline")
        result.setdefault("elapsed_ms", round((time.perf_counter() - started) * 1000))
        result.setdefault("usage", {"input_tokens": 0, "output_tokens": 0})
        return result
    result = build_dossier(
        snapshot,
        request.disease,
        request.persona,
        mode="offline",
        comparator_id=request.vs,
    )
    result.setdefault("model", "offline")
    result.setdefault("elapsed_ms", round((time.perf_counter() - started) * 1000))
    result.setdefault("usage", {"input_tokens": 0, "output_tokens": 0})
    if mode != "offline" and request.vs is None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


@app.get("/api/mechanism/search")
def mechanism_search(q: str = Query(default="")) -> dict[str, Any]:
    snapshot = get_snapshot()
    query = q.strip().casefold()
    matches = []
    for cluster in snapshot.clusters:
        member_ids = set(cluster.get("member_ids", []))
        pathways = cluster.get("shared_pathways", [])
        matching = [
            {"id": pathway, "name": snapshot.node_by_id.get(pathway, {}).get("label", pathway)}
            for pathway in pathways
            if query in snapshot.node_by_id.get(pathway, {}).get("label", pathway).casefold()
            or query in pathway.casefold()
        ]
        member_match = [
            node
            for node in snapshot.nodes
            if node.get("id") in member_ids
            and query in node.get("properties", {}).get("gene_symbol", "").casefold()
        ]
        label_match = query in cluster["label"].casefold()
        if matching or member_match or label_match:
            score = 1.0 if matching or label_match else 0.5
            matches.append(
                {
                    "id": cluster["id"],
                    "label": cluster["label"],
                    "score": score,
                    "member_ids": cluster.get("member_ids", []),
                    "matched_pathways": matching,
                }
            )
    return {"query": q, "clusters": sorted(matches, key=lambda row: row["score"], reverse=True)}


@app.post("/api/contribute")
def contribute(request: ContributeRequest) -> dict[str, Any]:
    snapshot = get_snapshot()
    original = snapshot.edge_by_id.get(request.edge_id)
    if not original:
        raise HTTPException(status_code=404, detail="Edge not found.")
    proposed = make_edge(
        original["subject"],
        original["subject_label"],
        original["predicate"],
        original["object"],
        original["object_label"],
        evidence_class="proposed",
        source="user_contribution",
        source_record=str(request.url),
        source_url=str(request.url),
        confidence=0.0,
        quote=request.sentence,
        polarity=request.polarity,
        method="contributor-submitted; excluded from analytics",
        properties={"based_on_edge": request.edge_id},
    )
    path = DATA / "proposed.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(proposed, ensure_ascii=False) + "\n")
    return proposed


@app.get("/api/export/kgx")
def export_kgx() -> StreamingResponse:
    snapshot = get_snapshot()
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        node_file = StringIO()
        node_file.write("id\tcategory\tname\tattributes\n")
        for node in snapshot.nodes:
            node_file.write(
                f"{node['id']}\tbiolink:{node['type']}\t{node['label']}\t"
                f"{json.dumps(node.get('properties', {}), ensure_ascii=False)}\n"
            )
        zipped.writestr("nodes.tsv", node_file.getvalue())
        edge_file = StringIO()
        edge_file.write("subject\tpredicate\tobject\tid\tattributes\n")
        for edge in snapshot.edges:
            edge_file.write(
                f"{edge['subject']}\tbiolink:{edge['predicate']}\t{edge['object']}\t{edge['edge_id']}\t"
                f"{json.dumps(edge, ensure_ascii=False)}\n"
            )
        zipped.writestr("edges.tsv", edge_file.getvalue())
    archive.seek(0)
    return StreamingResponse(
        archive,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="constellation-kgx.zip"'},
    )


@app.get("/openapi.json", include_in_schema=False)
def openapi_json() -> dict[str, Any]:
    return app.openapi()
