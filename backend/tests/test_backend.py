from __future__ import annotations

import gzip
import importlib
import json
import math
import re
import urllib.request
import zipfile
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from urllib.parse import quote

import httpx
import yaml
from fastapi.testclient import TestClient

from constellation.agents.dossier import (
    SECTION_TITLES,
    build_dossier,
    dossier_cache_path,
    validate_dossier,
)
from constellation.analytics.bridges import find_bridges_with_unverified
from constellation.analytics.clusters import cluster_records
from constellation.analytics.coverage import asset_coverage, eligibility_diff
from constellation.analytics.gaps import is_gap
from constellation.analytics.similarity import (
    fused_similarity,
    lowest_level_pathways,
    string_mechanism_score,
)
from constellation.config import DATA
from constellation.extract.live import extract_live
from constellation.extract.models import Claim, ExtractionResult
from constellation.graph.store import Snapshot
from constellation.ingest.cache import CachedHTTP
from constellation.ingest.ontology import (
    parse_go_basic_obo,
    parse_goa_human_gaf,
    parse_hpo_information,
    parse_reactome_all_levels,
)
from constellation.ingest.sources import (
    hpo_information,
    monarch_semsim,
    monarch_semsim_multicompare,
    pubmed_records,
    reporter_projects,
    search_trials_with_count,
    uniprot_accession,
)
from constellation.ledger import edge_id, sanitize_affiliation, validate_curie

api_module = importlib.import_module("constellation.api.app")


def _demo_ids() -> tuple[str, str]:
    config = json.loads((DATA / "demo_config.json").read_text(encoding="utf-8"))
    return config["supported_disease"], config["gap_disease"]


def _edge_refs(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        edge_id_value = value.get("edge_id")
        if isinstance(edge_id_value, str):
            found.add(edge_id_value)
        edge_ids = value.get("edge_ids", [])
        if isinstance(edge_ids, list):
            found.update(item for item in edge_ids if isinstance(item, str))
        for child in value.values():
            found.update(_edge_refs(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_edge_refs(child))
    return found


def _coverage_ids(snapshot: Snapshot, disease_id: str) -> set[str]:
    disease = snapshot.node_by_id[disease_id]
    gene_id = disease.get("properties", {}).get("gene_id", "")
    edges = (
        snapshot.edges_by_subject.get(disease_id, [])
        + snapshot.edges_by_object.get(disease_id, [])
        + snapshot.edges_by_subject.get(gene_id, [])
        + snapshot.edges_by_object.get(gene_id, [])
    )
    coverage_predicates = {
        "source_query",
        "source_count",
        "has_source_count",
        "coverage",
        "has_phenotype",
        "causes",
    }
    return {
        edge["edge_id"]
        for edge in edges
        if edge["predicate"] in coverage_predicates
    }


def _assert_edges_resolve(
    payload: Any,
    snapshot: Snapshot,
    client: TestClient,
    resolved: set[str],
) -> None:
    references = _edge_refs(payload)
    assert references <= snapshot.edge_by_id.keys()
    for identifier in references - resolved:
        assert client.get(f"/api/edge/{identifier}").status_code == 200
    resolved.update(references)


def _bridge_snapshot(
    affiliation_a: str = "",
    affiliation_b: str = "",
    shared_record: bool = False,
    name_a: str = "Alex Smith",
    name_b: str = "Alex Smith",
) -> Snapshot:
    disease_a, disease_b = "MONDO:A", "MONDO:B"
    gene_a, gene_b = "HGNC:A", "HGNC:B"
    person_a, person_b = "constellation:person/a", "constellation:person/b"
    record_a = "PMID:1"
    record_b = record_a if shared_record else "PMID:2"
    nodes = [
        {
            "id": person_a,
            "type": "person",
            "label": name_a,
            "properties": {"affiliations": [affiliation_a] if affiliation_a else []},
        },
        {
            "id": person_b,
            "type": "person",
            "label": name_b,
            "properties": {"affiliations": [affiliation_b] if affiliation_b else []},
        },
        {"id": record_a, "type": "paper", "label": "A paper", "properties": {"year": 2024}},
        *(
            []
            if shared_record
            else [
                {
                    "id": record_b,
                    "type": "paper",
                    "label": "B paper",
                    "properties": {"year": 2023},
                }
            ]
        ),
    ]
    edges: list[dict[str, Any]] = []

    def add_edge(
        subject: str,
        predicate: str,
        obj: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        edges.append(
            {
                "edge_id": f"{subject}|{predicate}|{obj}",
                "subject": subject,
                "subject_label": subject,
                "predicate": predicate,
                "object": obj,
                "object_label": obj,
                "source": "pubmed",
                "source_url": None,
                "properties": properties or {},
            }
        )

    add_edge(
        person_a,
        "authored",
        record_a,
        {"affiliations": [affiliation_a] if affiliation_a else []},
    )
    if not shared_record:
        add_edge(
            person_b,
            "authored",
            record_b,
            {"affiliations": [affiliation_b] if affiliation_b else []},
        )
    add_edge(record_a, "mentions_gene", gene_a)
    add_edge(gene_a, "causes", disease_a)
    if shared_record:
        add_edge(record_a, "mentions_gene", gene_b)
        add_edge(gene_b, "causes", disease_b)
    else:
        add_edge(record_b, "mentions_gene", gene_b)
        add_edge(gene_b, "causes", disease_b)

    snapshot = Snapshot.__new__(Snapshot)
    snapshot.nodes = nodes
    snapshot.edges = edges
    snapshot.node_by_id = {node["id"]: node for node in nodes}
    snapshot.edges_by_subject = defaultdict(list)
    snapshot.edges_by_object = defaultdict(list)
    for edge in edges:
        snapshot.edges_by_subject[edge["subject"]].append(edge)
        snapshot.edges_by_object[edge["object"]].append(edge)
    return snapshot


def test_snapshot_ledger_invariants() -> None:
    snapshot = Snapshot()
    required = {
        "edge_id",
        "subject",
        "subject_label",
        "predicate",
        "object",
        "object_label",
        "evidence_class",
        "source",
        "source_record",
        "source_url",
        "retrieved_at",
        "confidence",
        "quote",
        "polarity",
        "contradicted_by",
        "method",
        "schema_version",
        "properties",
    }
    abstracts_path = DATA / "raw" / "pubmed_abstracts.json"
    abstracts = (
        json.loads(abstracts_path.read_text(encoding="utf-8")) if abstracts_path.exists() else {}
    )
    assert snapshot.nodes and snapshot.edges
    assert validate_curie("constellation:person/0123456789abcdef")
    assert validate_curie("GO:0099504")
    assert validate_curie("R-HSA-210500")
    assert not validate_curie("untrusted:person/0123456789abcdef")
    assert all(validate_curie(node["id"]) for node in snapshot.nodes)
    for edge in snapshot.edges:
        assert required <= edge.keys()
        assert validate_curie(edge["subject"])
        assert validate_curie(edge["object"])
        assert 0 <= edge["confidence"] <= 1
        assert edge["edge_id"] == edge_id(
            edge["subject"],
            edge["predicate"],
            edge["object"],
            edge["source"],
            edge["source_record"],
        )
        if edge["quote"] and edge["source_record"].isdigit():
            abstract = abstracts.get(f"PMID:{edge['source_record']}")
            if abstract:
                assert edge["quote"] in abstract
    string_edges = [edge for edge in snapshot.edges if edge["source"] == "string"]
    assert string_edges
    assert all(
        {
            "nscore",
            "fscore",
            "pscore",
            "ascore",
            "escore",
            "dscore",
            "tscore",
            "string_mech",
        }
        <= edge["properties"].keys()
        for edge in string_edges
    )
    go_edges = [edge for edge in snapshot.edges if edge["source"] == "go"]
    assert go_edges
    assert all(edge["evidence_class"] == "curated" for edge in go_edges)
    assert all(edge["object"].startswith("GO:") for edge in go_edges)
    assert all(
        edge["source_record"].startswith("GO:")
        and len(edge["source_record"].split("|")) == 3
        for edge in go_edges
    )


def test_committed_snapshot_contains_the_full_gene_slice() -> None:
    snapshot = Snapshot()
    slice_config = yaml.safe_load((DATA / "slice.yaml").read_text(encoding="utf-8"))
    expected = set(slice_config["genes"])
    found = {
        node.get("properties", {}).get("symbol")
        for node in snapshot.nodes
        if node.get("type") == "gene"
    }
    assert len(expected) == 40
    assert found == expected


def test_snapshot_properties_do_not_contain_email_addresses() -> None:
    snapshot = Snapshot()
    email = re.compile(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        re.IGNORECASE,
    )

    def strings(value: Any) -> list[str]:
        if isinstance(value, dict):
            return [text for child in value.values() for text in strings(child)]
        if isinstance(value, list):
            return [text for child in value for text in strings(child)]
        return [value] if isinstance(value, str) else []

    for record in [*snapshot.nodes, *snapshot.edges]:
        for text in strings(record.get("properties", {})):
            assert email.search(text) is None


def test_affiliation_objects_keep_institution_name_only() -> None:
    affiliation = sanitize_affiliation(
        {
            "org_name": "Children's Hospital of Philadelphia",
            "email": "contact@example.org",
            "phone": "+1 555 0100",
        }
    )
    assert affiliation == "Children's Hospital of Philadelphia"


def test_stxbp1_golden_mechanism_cluster() -> None:
    snapshot = Snapshot()
    supported, _ = _demo_ids()
    cluster = snapshot.cluster_for(supported)
    assert cluster is not None
    assert cluster["stability"] >= 0.8
    symbol_by_disease = {
        node["id"]: node.get("properties", {}).get("gene_symbol")
        for node in snapshot.nodes
        if node["type"] == "disease"
    }
    members = {symbol_by_disease[item] for item in cluster["member_ids"]}
    assert "STXBP1" in members
    assert len(members & {"SNAP25", "STX1B", "VAMP2"}) >= 2
    assert not members & {"GNAO1", "GABRG2", "KCNT1"}
    cluster_response = TestClient(api_module.app).get(
        f"/api/disease/{quote(supported, safe='')}/cluster"
    )
    assert cluster_response.status_code == 200
    payload = cluster_response.json()
    assert payload["counterexample"]["disease"]["gene_symbol"] == "GNAO1"
    assert len(payload["slice_diseases"]) == 40
    assert {row["gene_symbol"] for row in payload["slice_diseases"]} == {
        node.get("properties", {}).get("gene_symbol")
        for node in snapshot.nodes
        if node["type"] == "disease"
    }
    dnm1 = next(
        (
            row
            for row in [*payload["partial_overlaps"], *payload["neighbours"]]
            if row["disease"]["gene_symbol"] == "DNM1"
        ),
        None,
    )
    assert dnm1 is not None
    assert dnm1["shared_pathways"]
    assert dnm1 in payload["partial_overlaps"]
    assert dnm1["excluded_by"] == "M"
    partial_rows = payload["partial_overlaps"]
    assert all(
        partial_rows[index]["M"] >= partial_rows[index + 1]["M"]
        for index in range(len(partial_rows) - 1)
    )
    expected_partial_count = sum(
        1
        for edge in snapshot.edges
        if supported in {edge["subject"], edge["object"]}
        and edge["predicate"] in {"shares_mechanism_with", "phenotypically_similar_to"}
        and (
            0 < float(edge["properties"].get("M", 0)) < 0.25
            or (
                float(edge["properties"].get("M", 0)) >= 0.25
                and float(edge["properties"].get("S", 0)) < 0.45
            )
        )
    )
    assert len(partial_rows) == expected_partial_count


def test_string_only_support_has_a_channel_score_mechanism_label() -> None:
    snapshot = Snapshot()
    diseases = {
        node.get("properties", {}).get("gene_symbol"): node["id"]
        for node in snapshot.nodes
        if node["type"] == "disease"
    }
    pair = {diseases["STXBP1"], diseases["STX1B"]}
    edge = next(
        row
        for row in snapshot.edges
        if {row["subject"], row["object"]} == pair
        and row["predicate"] == "shares_mechanism_with"
    )
    properties = edge["properties"]
    assert properties["shared_pathways"] == []
    assert properties["supported"] is True
    assert round(properties["string_mech"], 2) == 0.96
    assert properties["mechanism_label"] == (
        "physical/curated interaction (STRING exp/db 0.96)"
    )


def test_cluster_label_uses_a_shared_direct_reactome_parent() -> None:
    clusters, _ = cluster_records(
        ["MONDO:A", "MONDO:B"],
        [
            {
                "disease_a": "MONDO:A",
                "disease_b": "MONDO:B",
                "P": 0.5,
                "M": 0.5,
                "V": 0.5,
                "S": 0.5,
                "supported": True,
                "shared_pathways": ["R-HSA-1", "R-HSA-2"],
                "string_mech": 0.0,
            }
        ],
        {"MONDO:A": "Disease A", "MONDO:B": "Disease B"},
        {
            "R-HSA-1": "Pathway one",
            "R-HSA-2": "Pathway two",
            "R-HSA-parent": "Neurotransmitter release cycle",
        },
        {
            "R-HSA-1": {"R-HSA-parent"},
            "R-HSA-2": {"R-HSA-parent"},
        },
        {"R-HSA-1": 0.01, "R-HSA-2": 0.02},
    )
    assert len(clusters) == 1
    assert clusters[0]["label"] == "Neurotransmitter release cycle"


def test_cached_reactome_lowest_pathways_overlap_for_stxbp1_and_snap25() -> None:
    snapshot = Snapshot()
    genes = {
        node["properties"]["symbol"]: node["id"]
        for node in snapshot.nodes
        if node.get("type") == "gene"
    }
    pathway_sets = {
        symbol: {
            edge["object"]
            for edge in snapshot.edges_by_subject.get(gene_id, [])
            if edge["source"] == "reactome" and edge["predicate"] == "participates_in"
        }
        for symbol, gene_id in genes.items()
    }
    parents = {
        node["id"]: set(node.get("properties", {}).get("ancestor_ids", []))
        for node in snapshot.nodes
        if node.get("type") == "mechanism" and node["id"].startswith("R-HSA-")
    }
    lowest = lowest_level_pathways(pathway_sets, parents)
    specific = {
        symbol: {
            pathway_id
            for pathway_id in pathways
            if snapshot.node_by_id[pathway_id]["properties"]["human_gene_count"] <= 500
        }
        for symbol, pathways in lowest.items()
    }
    assert specific["STXBP1"] & specific["SNAP25"]


def test_gap_candidate_matches_source_counts_and_detector() -> None:
    snapshot = Snapshot()
    config = json.loads((DATA / "demo_config.json").read_text(encoding="utf-8"))
    metrics = json.loads((DATA / "source_metrics.json").read_text(encoding="utf-8"))
    gene = config["gap_gene"]
    disease_id = config["gap_disease"]
    assert metrics[f"pubmed:{gene}"]["count"] < 20
    assert metrics[f"ctgov:{gene}"]["count"] == 0
    assert metrics[f"foundation:{gene}"]["count"] == 0
    assert is_gap(snapshot, disease_id)
    frrs1l_disease = next(
        node["id"]
        for node in snapshot.nodes
        if node.get("type") == "disease"
        and node.get("properties", {}).get("gene_symbol") == "FRRS1L"
    )
    if (
        metrics["pubmed:FRRS1L"]["count"] < 20
        and metrics["ctgov:FRRS1L"]["count"] == 0
        and metrics["foundation:FRRS1L"]["count"] == 0
        and is_gap(snapshot, frrs1l_disease)
    ):
        assert gene == "FRRS1L"


def test_seeded_natural_history_asset_serves_stxbp1_and_syngap1() -> None:
    snapshot = Snapshot()
    asset = next(
        node
        for node in snapshot.nodes
        if node.get("type") == "asset"
        and node.get("properties", {}).get("record_id") == "NCT06555965"
    )
    diseases = {
        node.get("properties", {}).get("gene_symbol"): node["id"]
        for node in snapshot.nodes
        if node.get("type") == "disease"
    }
    served = {
        edge["object"]
        for edge in snapshot.edges_by_subject.get(asset["id"], [])
        if edge["predicate"] == "serves"
    }
    assert {diseases["STXBP1"], diseases["SYNGAP1"]} <= served


def test_hpo_information_content_counts_distinct_diseases_through_descendants() -> None:
    ontology = {
        "graphs": [
            {
                "edges": [
                    {"sub": "HP:0000002", "pred": "is_a", "obj": "HP:0000001"},
                    {"sub": "HP:0000003", "pred": "is_a", "obj": "HP:0000002"},
                ],
                "nodes": [
                    {"id": "HP:0000001", "lbl": "root"},
                    {"id": "HP:0000002", "lbl": "parent"},
                    {
                        "id": "HP:0000003",
                        "lbl": "child",
                        "meta": {"synonyms": [{"pred": "hasExactSynonym", "val": "child term"}]},
                    },
                ],
            }
        ]
    }
    hpoa = "\n".join(
        [
            "OMIM:1\tdisease one\t\tHP:0000003",
            "OMIM:2\tdisease two\t\tHP:0000003",
            "OMIM:2\tdisease two\t\tHP:0000003",
            "OMIM:3\tdisease three\t\tHP:0000002",
            "OMIM:4\tdisease four\tNOT\tHP:0000003",
        ]
    )
    information_content, parents, labels, synonyms = parse_hpo_information(hpoa, ontology)
    assert math.isclose(information_content["HP:0000003"], -math.log(2 / 3))
    assert information_content["HP:0000002"] == 0
    assert information_content["HP:0000001"] == 0
    assert parents["HP:0000003"] == ["HP:0000002"]
    assert labels["HP:0000003"] == "child"
    assert synonyms["HP:0000003"] == ["child term"]


def test_hpo_information_fetches_published_annotations_and_ontology() -> None:
    http = Mock()
    http.get.side_effect = [
        {
            "_text": (
                "OMIM:1\tdisease one\t\tHP:0000002\n"
                "OMIM:2\tdisease two\t\tHP:0000001"
            )
        },
        {
            "graphs": [
                {
                    "edges": [
                        {"sub": "HP:0000002", "pred": "is_a", "obj": "HP:0000001"}
                    ],
                    "nodes": [
                        {"id": "HP:0000001", "lbl": "root"},
                        {"id": "HP:0000002", "lbl": "child"},
                    ],
                }
            ]
        },
    ]

    information_content, _, _, _ = hpo_information(http)

    assert information_content["HP:0000002"] > 0
    assert [call.args[0] for call in http.get.call_args_list] == [
        "https://purl.obolibrary.org/obo/hp/hpoa/phenotype.hpoa",
        "https://purl.obolibrary.org/obo/hp.json",
    ]


def test_nct06555965_coverage_and_eligibility_against_dnm1() -> None:
    snapshot = Snapshot()
    asset = next(
        node
        for node in snapshot.nodes
        if node.get("type") == "asset"
        and node.get("properties", {}).get("record_id") == "NCT06555965"
    )
    dnm1 = next(
        node["id"]
        for node in snapshot.nodes
        if node.get("type") == "disease"
        and node.get("properties", {}).get("gene_symbol") == "DNM1"
    )

    coverage = asset_coverage(asset, dnm1, snapshot)
    assert coverage is not None
    assert 0 <= coverage["value"] <= 1
    target_terms = [
        edge["object"]
        for edge in snapshot.edges_by_subject[dnm1]
        if edge["predicate"] == "has_phenotype"
    ]
    weights = {
        snapshot.node_by_id[term]["properties"]["information_content"] for term in target_terms
    }
    assert len(weights) > 1
    assert coverage["matched"]
    assert all(
        item["match_type"] in {"exact", "descendant", "ancestor"}
        for item in coverage["matched"]
    )

    fields = {row["field"]: row for row in eligibility_diff(asset, dnm1, snapshot)}
    assert fields["genotype_requirement"] == {
        "field": "genotype_requirement",
        "asset_value": "requires STXBP1 or SYNGAP1 variant",
        "target_value": "DNM1 variant",
        "status": "differs",
    }
    assert fields["age_window"]["asset_value"] == "any age"
    assert fields["exclusion_other_gene"]["status"] == "differs"
    assert fields["age_window"]["status"] == "needs_expert_review"
    assert all(
        fields[field]["status"] == "matches"
        and fields[field]["target_value"] == "—"
        for field in ("study_type", "overall_status", "enrollment", "n_locations")
    )
    assert all("Inclusion Criteria" not in row["asset_value"] for row in fields.values())


def test_gene_bridge_proofs_touch_both_requested_diseases() -> None:
    snapshot = Snapshot()
    diseases = {
        node.get("properties", {}).get("gene_symbol"): node["id"]
        for node in snapshot.nodes
        if node.get("type") == "disease"
    }
    left, right = diseases["STXBP1"], diseases["DNM1"]
    bridges, _ = find_bridges_with_unverified(snapshot, left, right)
    assert bridges
    ingo = next(bridge for bridge in bridges if bridge["display_name"] == "Ingo Helbig")
    assert "0000000184860558" in ingo["why_same_person"]
    assert "philadelphia" in ingo["why_same_person"].casefold()
    assert len(ingo["why_same_person"]) < 500
    for bridge in bridges:
        proof_diseases = {
            disease_id
            for edge in bridge["proving_edges"]
            for disease_id in edge["disease_ids"]
        }
        assert {left, right} <= proof_diseases
        proof_ids = [edge["edge_id"] for edge in bridge["proving_edges"]]
        assert len(proof_ids) == len(set(proof_ids))
        assert bridge["why_same_person"]


def test_name_only_bridge_is_reported_as_unverified() -> None:
    people, unverified_count = find_bridges_with_unverified(
        _bridge_snapshot(name_a="HELBIG, INGO", name_b="Ingo Helbig, MD"),
        "MONDO:A",
        "MONDO:B",
    )
    assert people == []
    assert unverified_count == 1


def test_surname_first_alias_merges_when_affiliation_corroborates() -> None:
    people, unverified_count = find_bridges_with_unverified(
        _bridge_snapshot(
            "Children's Hospital of Philadelphia",
            "Children's Hospital of Philadelphia",
            name_a="HELBIG, INGO",
            name_b="Ingo Helbig, MD",
        ),
        "MONDO:A",
        "MONDO:B",
    )
    assert unverified_count == 0
    assert len(people) == 1
    assert people[0]["display_name"] == "Ingo Helbig"
    assert "philadelphia" in people[0]["why_same_person"].casefold()


def test_bridge_affiliations_normalize_and_dedupe_institutions() -> None:
    people, unverified_count = find_bridges_with_unverified(
        _bridge_snapshot(
            "Division of Neurology, Children's Hospital of Philadelphia, Philadelphia, PA",
            "CHILDREN'S HOSP OF PHILADELPHIA",
        ),
        "MONDO:A",
        "MONDO:B",
    )
    assert unverified_count == 0
    assert len(people) == 1
    assert people[0]["affiliations"] == ["Children's Hospital of Philadelphia"]
    assert people[0]["additional_affiliations"] == []


def test_middle_initial_aliases_merge_across_records() -> None:
    affiliation = "Children's Hospital of Philadelphia"
    snapshot = _bridge_snapshot(
        affiliation,
        affiliation,
        name_a="Sarah McKeown Ruggiero",
        name_b="Sarah McKeown Ruggiero",
    )

    def add_alias(
        person_id: str,
        record_id: str,
        name: str,
        gene_id: str,
        affiliation_text: str,
    ) -> None:
        person = {
            "id": person_id,
            "type": "person",
            "label": name,
            "properties": {"affiliations": [affiliation_text]},
        }
        record = {
            "id": record_id,
            "type": "paper",
            "label": "Alias paper",
            "properties": {"year": 2024},
        }
        snapshot.nodes.extend([person, record])
        snapshot.node_by_id.update({person_id: person, record_id: record})
        for subject, predicate, obj in (
            (person_id, "authored", record_id),
            (record_id, "mentions_gene", gene_id),
        ):
            edge = {
                "edge_id": f"{subject}|{predicate}|{obj}",
                "subject": subject,
                "subject_label": subject,
                "predicate": predicate,
                "object": obj,
                "object_label": obj,
                "source": "pubmed",
                "source_url": None,
                "properties": {"affiliations": [affiliation_text]},
            }
            snapshot.edges.append(edge)
            snapshot.edges_by_subject[subject].append(edge)
            snapshot.edges_by_object[obj].append(edge)

    add_alias(
        "constellation:person/a-initial",
        "PMID:3",
        "Sarah M Ruggiero",
        "HGNC:A",
        affiliation,
    )
    add_alias(
        "constellation:person/b-initial",
        "PMID:4",
        "Sarah M Ruggiero",
        "HGNC:B",
        affiliation,
    )

    people, unverified_count = find_bridges_with_unverified(
        snapshot,
        "MONDO:A",
        "MONDO:B",
    )
    assert unverified_count == 0
    assert len(people) == 1
    assert people[0]["display_name"] == "Sarah McKeown Ruggiero"
    assert people[0]["n_a"] == 2
    assert people[0]["n_b"] == 2


def test_generic_affiliation_text_does_not_verify_name_only_bridge() -> None:
    people, unverified_count = find_bridges_with_unverified(
        _bridge_snapshot(
            "Authors' affiliations are listed at the end of the article.",
            "Author affiliations are provided at the end of the article.",
        ),
        "MONDO:A",
        "MONDO:B",
    )
    assert people == []
    assert unverified_count == 1


def test_bridge_affiliation_signals_are_concise_for_long_source_text() -> None:
    long_context = " ".join(["clinical"] * 100)
    people, unverified_count = find_bridges_with_unverified(
        _bridge_snapshot(
            f"{long_context}, Children's Hospital of Philadelphia",
            f"{long_context}, CHILDREN'S HOSP OF PHILADELPHIA",
        ),
        "MONDO:A",
        "MONDO:B",
    )

    assert unverified_count == 0
    assert len(people) == 1
    reason = people[0]["why_same_person"]
    assert "philadelphia" in reason.casefold()
    assert len(reason) < 500
    assert long_context not in reason


def test_affiliation_corroborates_bridge_and_proof_edges_are_deduplicated() -> None:
    people, unverified_count = find_bridges_with_unverified(
        _bridge_snapshot(
            "Department of Neurology, University of Pennsylvania",
            "Child Neurology, University of Pennsylvania",
            name_a="K. L. Helbig",
            name_b="Katherine L Helbig",
        ),
        "MONDO:A",
        "MONDO:B",
    )
    assert unverified_count == 0
    assert len(people) == 1
    bridge = people[0]
    assert bridge["display_name"] == "Katherine L Helbig"
    assert "pennsylvania" in bridge["why_same_person"].casefold()
    proof_ids = [edge["edge_id"] for edge in bridge["proving_edges"]]
    assert len(proof_ids) == len(set(proof_ids))
    assert {
        disease_id
        for edge in bridge["proving_edges"]
        for disease_id in edge["disease_ids"]
    } == {
        "MONDO:A",
        "MONDO:B",
    }


def test_similarity_fusion_uses_prescribed_weights_and_gate() -> None:
    rows = fused_similarity(
        ["MONDO:1", "MONDO:2", "MONDO:3"],
        {"MONDO:1": "A", "MONDO:2": "B", "MONDO:3": "C"},
        {"MONDO:1": {"HP:1"}, "MONDO:2": {"HP:1"}, "MONDO:3": set()},
        {"A": {"R-HSA-1"}, "B": {"R-HSA-1"}, "C": set()},
        {"A": "loss_of_function", "B": "loss_of_function", "C": "unknown"},
        {("A", "B"): 0.7},
        {("MONDO:1", "MONDO:2"): 0.8},
    )
    supported = next(row for row in rows if {row["gene_a"], row["gene_b"]} == {"A", "B"})
    assert (supported["P"], supported["M"], supported["V"], supported["S"]) == (
        1.0,
        1.0,
        1.0,
        1.0,
    )
    assert supported["supported"] is True
    assert all(not row["supported"] for row in rows if row is not supported)


def test_mechanism_guard_requires_at_least_025() -> None:
    common = (
        ["MONDO:1", "MONDO:2"],
        {"MONDO:1": "A", "MONDO:2": "B"},
        {"MONDO:1": set(), "MONDO:2": set()},
        {"A": "loss_of_function", "B": "loss_of_function"},
        {},
        {("MONDO:1", "MONDO:2"): 1.0},
    )
    incidental = fused_similarity(
        common[0],
        common[1],
        common[2],
        {"A": {"R-HSA-shared"}, "B": {"R-HSA-shared", *{f"R-HSA-{i}" for i in range(11)}}},
        common[3],
        common[4],
        common[5],
    )[0]
    adequate = fused_similarity(
        common[0],
        common[1],
        common[2],
        {"A": {"R-HSA-shared", "R-HSA-a"}, "B": {"R-HSA-shared", "R-HSA-b", "R-HSA-c"}},
        common[3],
        common[4],
        common[5],
    )[0]
    assert round(incidental["M"], 3) == 0.083
    assert incidental["S"] >= 0.45
    assert incidental["supported"] is False
    assert adequate["M"] == 0.25
    assert adequate["supported"] is True


def test_string_mechanism_ignores_text_mining_channel() -> None:
    common = (
        ["MONDO:1", "MONDO:2"],
        {"MONDO:1": "A", "MONDO:2": "B"},
        {"MONDO:1": set(), "MONDO:2": set()},
        {"A": set(), "B": set()},
        {"A": "loss_of_function", "B": "gain_of_function"},
    )
    text_only = fused_similarity(
        *common,
        {("A", "B"): {"string_score": 0.857, "string_mech": 0.045}},
        {("MONDO:1", "MONDO:2"): 1.0},
    )[0]
    curated_channels = fused_similarity(
        *common,
        {("A", "B"): {"string_score": 0.75, "string_mech": 0.7}},
        {("MONDO:1", "MONDO:2"): 1.0},
    )[0]
    assert text_only["M"] == 0
    assert text_only["supported"] is False
    assert curated_channels["M"] == 1
    assert curated_channels["supported"] is True
    assert abs(string_mechanism_score(0.045, 0.0) - 0.045) < 1e-12


def test_go_jaccard_contributes_to_mechanism_score() -> None:
    row = fused_similarity(
        ["MONDO:1", "MONDO:2"],
        {"MONDO:1": "A", "MONDO:2": "B"},
        {"MONDO:1": set(), "MONDO:2": set()},
        {"A": set(), "B": set()},
        {"A": "loss_of_function", "B": "gain_of_function"},
        {("A", "B"): {"string_score": 0.857, "string_mech": 0.045}},
        {("MONDO:1", "MONDO:2"): 1.0},
        go_terms={"A": {"GO:0099504", "GO:0006810"}, "B": {"GO:0099504"}},
        go_counts={"GO:0099504": 250, "GO:0006810": 400},
    )[0]
    assert row["M_reactome"] == 0
    assert row["M_go"] == 0.5
    assert row["M"] == 0.5


def test_similarity_uses_only_lowest_level_reactome_pathways() -> None:
    pathways = {
        "A": {"R-HSA-parent", "R-HSA-child", "R-HSA-unrelated"},
        "B": {"R-HSA-parent", "R-HSA-child"},
    }
    parents = {
        "R-HSA-child": {"R-HSA-parent"},
        "R-HSA-unrelated": set(),
    }
    assert lowest_level_pathways(pathways, parents) == {
        "A": {"R-HSA-child", "R-HSA-unrelated"},
        "B": {"R-HSA-child"},
    }
    assert lowest_level_pathways(pathways, {}) == {"A": set(), "B": set()}


def test_cached_http_binary_resources_can_be_seeded_from_local_files(tmp_path: Path) -> None:
    source = tmp_path / "source.gz"
    source.write_bytes(b"cached binary resource")
    cache_dir = tmp_path / "cache"
    first = CachedHTTP(cache_dir=cache_dir)
    assert first.get_bytes("https://example.org/resource.gz", local_fallback=source) == (
        b"cached binary resource"
    )
    first.close()

    second = CachedHTTP(cache_dir=cache_dir)
    assert second.get_bytes("https://example.org/resource.gz") == b"cached binary resource"
    second.close()
    assert list(cache_dir.glob("*.bin"))


def test_goa_gaf_counts_distinct_genes_and_uses_experimental_annotations() -> None:
    obo = b"""[Term]
id: GO:0000001
name: biological process root

[Term]
id: GO:0000002
name: child process
is_a: GO:0000001 ! biological process root

[Term]
id: GO:0000003
name: related process
relationship: part_of GO:0000001 ! biological process root
"""
    names, parents = parse_go_basic_obo(obo)

    def row(
        symbol: str,
        go_id: str,
        evidence: str,
        *,
        qualifier: str = "",
        aspect: str = "P",
        protein_id: str = "P00001",
    ) -> str:
        return "\t".join(
            [
                "UniProtKB",
                protein_id,
                symbol,
                qualifier,
                go_id,
                "PMID:1",
                evidence,
                "",
                aspect,
                symbol,
                "",
                "protein",
                "taxon:9606",
                "20250101",
                "TEST",
                "",
                "",
            ]
        )

    gaf_text = "\n".join(
        [
            "!gaf-version: 2.2",
            row("STXBP1", "GO:0000002", "EXP"),
            row("STXBP1", "GO:0000002", "IEA"),
            row("OTHER", "GO:0000002", "IEA", protein_id="P00002"),
            row("DNM1", "GO:0000003", "IDA", protein_id="P00003"),
            row(
                "SNAP25",
                "GO:0000002",
                "EXP",
                qualifier="NOT",
                protein_id="P00004",
            ),
            row("GNAO1", "GO:0000002", "EXP", aspect="F", protein_id="P00005"),
        ]
    )
    data = parse_goa_human_gaf(
        gzip.compress(gaf_text.encode("utf-8")),
        parents,
        {"STXBP1", "DNM1", "SNAP25", "GNAO1"},
    )

    assert names["GO:0000002"] == "child process"
    assert parents["GO:0000003"] == {"GO:0000001"}
    assert data["gene_counts"]["GO:0000002"] == 2
    assert data["gene_counts"]["GO:0000001"] == 3
    assert data["experimental_slice_rows"] == 2
    assert [row["evidence_code"] for row in data["annotations_by_gene"]["STXBP1"]] == ["EXP"]
    assert "SNAP25" not in data["annotations_by_gene"]


def test_goa_jaccards_match_stxbp1_reference_values() -> None:
    snapshot = Snapshot()
    disease_by_symbol = {
        node["properties"]["gene_symbol"]: node["id"]
        for node in snapshot.nodes
        if node["type"] == "disease"
    }
    scores = {
        "SNAP25": 0.172,
        "VAMP2": 0.238,
        "DNM1": 0.026,
        "KCNT2": 0.030,
    }
    for symbol, expected in scores.items():
        disease_a, disease_b = disease_by_symbol["STXBP1"], disease_by_symbol[symbol]
        edge = next(
            row
            for row in snapshot.edges
            if {row["subject"], row["object"]} == {disease_a, disease_b}
            and row["predicate"] in {"shares_mechanism_with", "phenotypically_similar_to"}
        )
        assert round(float(edge["properties"]["M_go"]), 3) == expected
    dnm1_edge = next(
        row
        for row in snapshot.edges
        if {row["subject"], row["object"]}
        == {disease_by_symbol["STXBP1"], disease_by_symbol["DNM1"]}
        and row["predicate"] in {"shares_mechanism_with", "phenotypically_similar_to"}
    )
    shared_terms = [
        snapshot.node_by_id[term_id]
        for term_id in dnm1_edge["properties"]["shared_pathways"]
        if term_id.startswith("GO:")
    ]
    vesicle_organization = next(
        term for term in shared_terms if term["label"].casefold() == "vesicle organization"
    )
    assert vesicle_organization["properties"]["human_gene_count"] == 384


def test_reactome_mapping_counts_distinct_human_genes() -> None:
    mapping = b"""P00001\tR-HSA-1\thttps://reactome.org/R-HSA-1\tPathway one\tIEA\tHomo sapiens
P00002\tR-HSA-1\thttps://reactome.org/R-HSA-1\tPathway one\tIEA\tHomo sapiens
P00003\tR-HSA-1\thttps://reactome.org/R-HSA-1\tPathway one\tIEA\tHomo sapiens
P00004\tR-HSA-1\thttps://reactome.org/R-HSA-1\tPathway one\tIEA\tMus musculus
"""
    data = parse_reactome_all_levels(
        mapping,
        {
            "P00001": "GENE1",
            "P00002": "GENE1",
            "P00003": "GENE2",
        },
    )
    assert data["gene_counts"]["R-HSA-1"] == 2
    assert data["pathways_by_gene"] == {
        "GENE1": {"R-HSA-1"},
        "GENE2": {"R-HSA-1"},
    }


def test_uniprot_accession_uses_ensembl_gene_cross_references() -> None:
    http = Mock()
    http.get.return_value = [
        {"dbname": "Uniprot_gn", "primary_id": "A0A0D9SG72"},
        {"dbname": "Uniprot_gn", "primary_id": "P61764"},
    ]
    entity = {"xref": ["ENSEMBL:ENSG00000136854", "OMIM:602926"]}
    assert uniprot_accession(http, entity) == "P61764"
    assert http.get.call_args.args[0].endswith("/xrefs/id/ENSG00000136854")


def test_pubmed_records_retain_author_orcid() -> None:
    http = Mock()
    http.get.side_effect = [
        {"esearchresult": {"idlist": ["12345"]}},
        {
            "_text": (
                "<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>12345</PMID>"
                "<Article><ArticleTitle>Title</ArticleTitle><AuthorList><Author>"
                "<LastName>Helbig</LastName><ForeName>Katherine</ForeName>"
                '<Identifier Source="ORCID">0000-0002-1825-0097</Identifier>'
                "<AffiliationInfo><Affiliation>University of Pennsylvania</Affiliation>"
                "</AffiliationInfo></Author></AuthorList></Article></MedlineCitation>"
                "</PubmedArticle></PubmedArticleSet>"
            )
        },
    ]
    records = pubmed_records(http, "STXBP1", retmax=1)
    assert records[0]["authors"][0]["orcid"] == "0000-0002-1825-0097"


def test_clinical_trials_search_uses_condition_total_count() -> None:
    http = Mock()
    http.get.return_value = {"totalCount": 11, "studies": [{"id": "NCT00000001"}]}
    studies, count = search_trials_with_count(http, "STXBP1", page_size=100)
    assert count == 11
    assert len(studies) == 1
    assert http.get.call_args.kwargs["params"] == {
        "query.cond": "STXBP1",
        "pageSize": 100,
        "countTotal": True,
        "format": "json",
    }


def test_reporter_search_requests_up_to_one_hundred_projects() -> None:
    http = Mock()
    http.post.return_value = {"results": [{"project_num": str(i)} for i in range(100)]}
    rows = reporter_projects(http, "STXBP1")
    assert len(rows) == 100
    assert http.post.call_args.kwargs["json_body"]["limit"] == 100


def test_monarch_semsim_uses_supported_limit() -> None:
    http = Mock()
    result = [{"id": "MONDO:1", "score": 0.4}]
    http.post.return_value = {"items": result}
    assert monarch_semsim(http, ["HP:0001250"]) == result
    assert http.post.call_args.kwargs["json_body"]["limit"] == 50


def test_monarch_semsim_multicompare_uses_source_candidates() -> None:
    http = Mock()
    result = [{"subject": {"id": "MONDO:2"}, "score": 4.2}]
    object_sets = [{"id": "MONDO:2", "label": "condition", "phenotypes": ["HP:0001250"]}]
    http.post.return_value = result
    assert monarch_semsim_multicompare(http, ["HP:0001250"], object_sets) == result
    assert http.post.call_args.kwargs["json_body"]["object_sets"] == object_sets


def test_offline_dossier_has_cited_required_sections() -> None:
    snapshot = Snapshot()
    supported, gap = _demo_ids()
    supported_dossier = None
    for disease_id in (supported, gap):
        dossier = build_dossier(snapshot, disease_id, "maria", mode="offline")
        if disease_id == supported:
            supported_dossier = dossier
        assert dossier["dropped_sentences"] == 0
        assert set(SECTION_TITLES) <= {section["key"] for section in dossier["sections"]}
        assert all(section["sentences"] for section in dossier["sections"])
        assert all(
            sentence["edge_ids"] and set(sentence["edge_ids"]) <= snapshot.edge_by_id.keys()
            and len(sentence["edge_ids"]) == len(set(sentence["edge_ids"]))
            for section in dossier["sections"]
            for sentence in section["sentences"]
        )
    assert supported_dossier is not None
    dnm1_id = next(
        node["id"]
        for node in snapshot.nodes
        if node.get("type") == "disease"
        and node.get("properties", {}).get("gene_symbol") == "DNM1"
    )
    supported_dossier = build_dossier(
        snapshot,
        supported,
        "maria",
        mode="offline",
        comparator_id=dnm1_id,
    )
    sections = {section["key"]: section for section in supported_dossier["sections"]}
    who_shares = " ".join(sentence["text"] for sentence in sections["who_shares"]["sentences"])
    cluster = snapshot.cluster_for(supported)
    assert cluster is not None
    members = set(cluster["member_ids"])
    member_symbols = {
        snapshot.node_by_id[disease_id].get("properties", {}).get("gene_symbol")
        for disease_id in members
        if disease_id != supported
    }
    outside_symbols = {
        node.get("properties", {}).get("gene_symbol")
        for node in snapshot.nodes
        if node.get("type") == "disease" and node["id"] not in members
    }
    assert all(symbol in who_shares for symbol in member_symbols if symbol)
    assert not any(symbol in who_shares for symbol in outside_symbols if symbol)
    assert "DNM1-related disease" not in who_shares
    what_differs = " ".join(
        sentence["text"] for sentence in sections["what_differs"]["sentences"]
    )
    assert "GNAO1-related disease" in what_differs
    assert "looks similar clinically" in what_differs
    assert "so it is excluded" in what_differs
    member_rows = []
    for member_id in members - {supported}:
        pair_edges = [
            edge
            for edge in snapshot.edges_by_subject.get(supported, [])
            if edge["object"] == member_id and edge["predicate"] == "shares_mechanism_with"
        ] + [
            edge
            for edge in snapshot.edges_by_subject.get(member_id, [])
            if edge["object"] == supported and edge["predicate"] == "shares_mechanism_with"
        ]
        if pair_edges:
            symbol = snapshot.node_by_id[member_id].get("properties", {}).get(
                "gene_symbol", snapshot.node_by_id[member_id]["label"]
            )
            member_rows.append((max(edge["properties"]["S"] for edge in pair_edges), symbol))
    ordered_members = sorted(member_rows, key=lambda row: (-row[0], row[1].casefold()))
    member_positions = [who_shares.index(symbol) for _, symbol in ordered_members]
    assert member_positions == sorted(member_positions)
    what_exists = " ".join(
        sentence["text"] for sentence in sections["what_exists"]["sentences"]
    )
    assert "covers" in what_exists.casefold()
    assert "%" in what_exists
    assert " of " in what_exists and " terms" in what_exists.casefold()
    assert any(character.isdigit() for character in what_exists)
    assert "Exact-match foundation" in what_exists
    what_differs = " ".join(
        sentence["text"] for sentence in sections["what_differs"]["sentences"]
    )
    assert "V=0" in what_differs and "“" in what_differs
    assert "overlaps only weakly" in what_differs
    assert "vesicle organization" in what_differs
    assert "GO:0016050" in what_differs
    assert "DNM1" in what_differs
    variant_class_ids = {
        edge["edge_id"]
        for edge in snapshot.edges
        if edge["predicate"] == "has_variant_class"
    }
    assert any(
        sentence["edge_ids"] and set(sentence["edge_ids"]) & variant_class_ids
        for sentence in sections["what_differs"]["sentences"]
        if "V=0" in sentence["text"] and "“" in sentence["text"]
    )
    dnm1_partial_sentence = next(
        sentence
        for sentence in sections["what_differs"]["sentences"]
        if "DNM1" in sentence["text"]
    )
    assert any(
        snapshot.edge_by_id[edge_id]["source"] in {"go", "reactome"}
        for edge_id in dnm1_partial_sentence["edge_ids"]
    )
    contact_sentences = sections["who_to_contact"]["sentences"]
    assert len(contact_sentences) <= 3
    assert all(len(sentence["edge_ids"]) <= 3 for sentence in contact_sentences)
    assert any("Ingo Helbig" in sentence["text"] for sentence in contact_sentences)
    assert all(
        "signals:" in sentence["text"]
        for sentence in contact_sentences
        if "No corroborated" not in sentence["text"]
    )
    next_step_ids = set(
        edge_id
        for sentence in sections["next_step"]["sentences"]
        for edge_id in sentence["edge_ids"]
    )
    next_step_edges = [snapshot.edge_by_id[edge_id] for edge_id in next_step_ids]
    assert any(
        edge["predicate"] == "serves"
        and snapshot.node_by_id.get(edge["subject"], {}).get("type") == "asset"
        for edge in next_step_edges
    )
    assert any(edge["predicate"] == "has_phenotype" for edge in next_step_edges)
    assert any(
        edge["predicate"] in {"authored", "funds", "investigates"}
        for edge in next_step_edges
    )
    assert build_dossier(snapshot, supported, "maria", mode="offline")["gap_plan"] is None
    assert build_dossier(snapshot, gap, "maria", mode="offline")["gap_plan"] is not None


def test_dossier_personas_change_language_and_order() -> None:
    snapshot = Snapshot()
    supported, _ = _demo_ids()
    devon = build_dossier(snapshot, supported, "devon")
    priya = build_dossier(snapshot, supported, "priya")
    osei = build_dossier(snapshot, supported, "osei")
    devon_text = " ".join(
        sentence["text"]
        for section in devon["sections"]
        for sentence in section["sentences"]
    )
    priya_text = " ".join(
        sentence["text"]
        for section in priya["sections"]
        for sentence in section["sentences"]
    )
    assert "Verify" not in devon_text
    assert "Verify" in " ".join(
        sentence["text"]
        for section in osei["sections"]
        for sentence in section["sentences"]
    )
    assert osei["sections"][0]["key"] == "what_differs"
    assert "GO:" in priya_text or "R-HSA-" in priya_text


def test_committed_offline_dossier_cache_covers_demo_personas() -> None:
    snapshot = Snapshot()
    supported, gap = _demo_ids()
    for disease_id in (supported, gap):
        for persona in ("maria", "devon", "priya", "osei"):
            path = dossier_cache_path(disease_id, persona, snapshot.snapshot_hash, "offline")
            cached = json.loads(path.read_text(encoding="utf-8"))
            assert cached["disease_id"] == disease_id
            assert cached["persona"] == persona
            assert cached["mode"] == "offline"


def test_dossier_validator_drops_uncited_and_unresolvable_sentences() -> None:
    snapshot = Snapshot()
    disease_id, _ = _demo_ids()
    coverage_edge = next(iter(_coverage_ids(snapshot, disease_id)))
    payload = {
        "sections": [
            {
                "key": "who_shares",
                "title": "Who shares",
                "sentences": [
                    {"text": "No citation.", "edge_ids": []},
                    {"text": "Unknown citation.", "edge_ids": ["not-an-edge"]},
                ],
            },
            {
                "key": "what_exists",
                "title": "What exists",
                "sentences": [
                    {
                        "text": "Duplicate reference.",
                        "edge_ids": [coverage_edge, coverage_edge],
                    }
                ],
            }
        ]
    }
    validated = validate_dossier(payload, snapshot, disease_id)
    assert validated["dropped_sentences"] == 2
    coverage_ids = _coverage_ids(snapshot, disease_id)
    assert all(
        sentence["edge_ids"] and set(sentence["edge_ids"]) <= snapshot.edge_by_id.keys()
        and len(sentence["edge_ids"]) == len(set(sentence["edge_ids"]))
        for section in validated["sections"]
        for sentence in section["sentences"]
    )
    assert all(
        edge_id in coverage_ids
        for section in validated["sections"]
        if section["key"] != "what_exists"
        for sentence in section["sentences"]
        for edge_id in sentence["edge_ids"]
    )


def test_api_contract_for_both_demo_diseases() -> None:
    snapshot = Snapshot()
    client = TestClient(api_module.app)
    supported, gap = _demo_ids()
    resolved_edge_ids: set[str] = set()
    health = client.get("/api/health")
    assert health.status_code == 200
    assert {"status", "snapshot_hash", "llm_mode", "counts"} <= health.json().keys()

    for disease_id in {supported, gap}:
        overview_response = client.get(f"/api/disease/{quote(disease_id, safe='')}")
        assert overview_response.status_code == 200
        overview = overview_response.json()
        assert {
            "disease",
            "counts",
            "patient_groups",
            "summary",
            "is_gap",
        } <= overview.keys()
        match_kinds = [group["match_kind"] for group in overview["patient_groups"]]
        assert match_kinds == sorted(match_kinds, key=lambda value: value != "exact")
        for group in overview["patient_groups"]:
            group_edge = snapshot.edge_by_id[group["edge_id"]]
            group_node = snapshot.node_by_id[group["id"]]
            assert group_edge["properties"]["match_kind"] == group["match_kind"]
            assert group_node["properties"]["match_kind"] == group["match_kind"]
        assert {"technical", "plain"} <= overview["summary"].keys()
        assert {
            "phenotypes",
            "pathogenic_variants",
            "trials",
            "awards_active",
            "papers",
            "patient_groups",
        } <= overview["counts"].keys()
        _assert_edges_resolve(overview, snapshot, client, resolved_edge_ids)
        if overview["disease"]["gene_symbol"] == "STXBP1":
            metrics = json.loads((DATA / "source_metrics.json").read_text(encoding="utf-8"))
            assert overview["counts"]["trials"]["n"] == metrics["ctgov:STXBP1"]["count"]
            assert {group["match_kind"] for group in overview["patient_groups"]} == {
                "exact",
                "umbrella",
            }
        first_id = next(iter(_edge_refs(overview)))
        edge_response = client.get(f"/api/edge/{first_id}")
        assert edge_response.status_code == 200
        assert {
            "edge_id",
            "subject",
            "subject_label",
            "predicate",
            "object",
            "object_label",
            "evidence_class",
            "source",
            "source_record",
            "source_url",
            "retrieved_at",
            "confidence",
            "quote",
            "polarity",
            "contradicted_by",
            "method",
            "schema_version",
            "properties",
        } <= edge_response.json().keys()
        assert "contradicting" in edge_response.json()

        cluster_response = client.get(f"/api/disease/{quote(disease_id, safe='')}/cluster")
        assert cluster_response.status_code == 200
        cluster_result = cluster_response.json()
        assert {
            "disease_id",
            "cluster",
            "neighbours",
            "partial_overlaps",
            "slice_diseases",
            "counterexample",
            "nearest_leads",
            "weights",
        } <= cluster_result.keys()
        _assert_edges_resolve(cluster_result, snapshot, client, resolved_edge_ids)
        cluster_id = cluster_result["cluster"]["id"]
        assets_response = client.get(
            f"/api/cluster/{quote(cluster_id, safe='')}/assets",
            params={"for": disease_id},
        )
        assert assets_response.status_code == 200
        _assert_edges_resolve(assets_response.json(), snapshot, client, resolved_edge_ids)
        assert {"cluster_id", "for_disease", "assets"} <= assets_response.json().keys()
        for asset in assets_response.json()["assets"]:
            assert {
                "id",
                "name",
                "asset_type",
                "url",
                "record_id",
                "serves",
                "coverage",
                "eligibility_diff",
                "eligibility_text",
                "edge_ids",
            } <= asset.keys()

        bridge_response = client.get("/api/bridges", params={"a": supported, "b": gap})
        assert bridge_response.status_code == 200
        bridges_payload = bridge_response.json()
        assert {
            "a",
            "b",
            "people",
            "unverified_name_matches",
        } <= bridges_payload.keys()
        assert bridges_payload["a"]["id"] == supported
        assert bridges_payload["b"]["id"] == gap
        _assert_edges_resolve(bridges_payload, snapshot, client, resolved_edge_ids)
        coverage_response = client.get(f"/api/disease/{quote(disease_id, safe='')}/coverage")
        assert coverage_response.status_code == 200
        assert {
            "disease_id",
            "is_gap",
            "reasons",
            "sources",
            "nearest_leads",
            "what_would_change",
        } <= coverage_response.json().keys()
        _assert_edges_resolve(coverage_response.json(), snapshot, client, resolved_edge_ids)

        dossier_response = client.post(
            "/api/dossier",
            json={"disease": disease_id, "persona": "maria"},
        )
        assert dossier_response.status_code == 200
        dossier = dossier_response.json()
        assert {
            "disease_id",
            "persona",
            "mode",
            "snapshot_hash",
            "trace_id",
            "generated_at",
            "sections",
            "dropped_sentences",
            "gap_plan",
            "markdown",
        } <= dossier.keys()
        _assert_edges_resolve(dossier, snapshot, client, resolved_edge_ids)

    for query in ("STXBP1", "Munc18-1", "DEE4", "STXBP1 Foundation"):
        response = client.get("/api/search", params={"q": query})
        assert response.status_code == 200
        assert response.json()["best"]["disease"]["id"] == "MONDO:0012812"
        _assert_edges_resolve(response.json(), snapshot, client, resolved_edge_ids)

    mechanism = client.get("/api/mechanism/search", params={"q": "SNARE"})
    assert mechanism.status_code == 200
    assert {"query", "clusters"} <= mechanism.json().keys()

    export = client.get("/api/export/kgx")
    assert export.status_code == 200
    with zipfile.ZipFile(BytesIO(export.content)) as archive:
        assert {"nodes.tsv", "edges.tsv"} <= set(archive.namelist())


def test_offline_api_constructs_no_outbound_http_clients(monkeypatch: Any) -> None:
    monkeypatch.setenv("LLM_MODE", "offline")
    client = TestClient(api_module.app)

    def reject_outbound(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("offline API constructed an outbound HTTP client")

    monkeypatch.setattr(httpx, "Client", reject_outbound)
    monkeypatch.setattr(httpx, "AsyncClient", reject_outbound)
    monkeypatch.setattr(urllib.request, "urlopen", reject_outbound)

    with client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["llm_mode"] == "offline"
        stxbp1 = "MONDO:0012812"
        cluster_response = client.get(
            f"/api/disease/{quote(stxbp1, safe='')}/cluster"
        )
        assert cluster_response.status_code == 200
        cluster = cluster_response.json()
        dnm1 = next(
            row["disease"]["id"]
            for row in cluster["partial_overlaps"]
            if row["disease"]["gene_symbol"] == "DNM1"
        )
        cluster_id = cluster["cluster"]["id"]
        assert client.get(
            f"/api/cluster/{quote(cluster_id, safe='')}/assets",
            params={"for": dnm1},
        ).status_code == 200
        assert client.get(
            "/api/bridges", params={"a": stxbp1, "b": dnm1}
        ).status_code == 200
        assert client.post(
            "/api/dossier", json={"disease": stxbp1, "persona": "maria"}
        ).status_code == 200


def test_contribution_persists_only_as_proposed(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    snapshot = Snapshot()
    client = TestClient(api_module.app)
    monkeypatch.setattr(api_module, "DATA", tmp_path)
    source_edge = snapshot.edges[0]
    response = client.post(
        "/api/contribute",
        json={
            "url": "https://example.org/evidence",
            "sentence": "A contributor-proposed statement.",
            "edge_id": source_edge["edge_id"],
            "polarity": "supports",
        },
    )
    assert response.status_code == 200
    proposed = response.json()
    assert proposed["evidence_class"] == "proposed"
    assert proposed["edge_id"] not in snapshot.edge_by_id
    assert client.get(f"/api/edge/{proposed['edge_id']}").json()["edge_id"] == proposed["edge_id"]
    persisted = (tmp_path / "proposed.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(persisted[0])["edge_id"] == proposed["edge_id"]


def test_live_extraction_uses_mocked_structured_outputs() -> None:
    abstract = "STXBP1 loss-of-function variants impair synaptic vesicle release."
    expected = ExtractionResult(
        pmid="12345678",
        claims=[
            Claim(
                subject_gene="STXBP1",
                claim_type="variant_effect",
                object_text="loss_of_function",
                polarity="supports",
                quote=abstract,
                confidence=0.9,
                evidence_level="human_case",
            )
        ],
    )

    class FakeResponses:
        called: dict[str, Any] = {}

        def parse(self, **kwargs: Any) -> Any:
            self.called = kwargs
            return type("ParsedResponse", (), {"output_parsed": expected})()

    class FakeClient:
        def __init__(self) -> None:
            self.responses = FakeResponses()

    client = FakeClient()
    result = extract_live(
        {"pmid": "12345678", "title": "Test", "abstract": abstract},
        ["STXBP1"],
        client=client,
    )
    assert result == expected
    assert client.responses.called["text_format"] is ExtractionResult
    assert "STXBP1" in client.responses.called["input"][1]["content"]
