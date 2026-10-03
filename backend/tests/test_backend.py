from __future__ import annotations

import importlib
import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from urllib.parse import quote

import yaml
from fastapi.testclient import TestClient

from constellation.agents.dossier import (
    SECTION_TITLES,
    build_dossier,
    dossier_cache_path,
    validate_dossier,
)
from constellation.analytics.bridges import find_bridges
from constellation.analytics.gaps import is_gap
from constellation.analytics.similarity import fused_similarity, lowest_level_pathways
from constellation.config import DATA
from constellation.extract.live import extract_live
from constellation.extract.models import Claim, ExtractionResult
from constellation.graph.store import Snapshot
from constellation.ingest.sources import monarch_semsim, monarch_semsim_multicompare
from constellation.ledger import edge_id, validate_curie

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


def test_stxbp1_golden_mechanism_cluster() -> None:
    snapshot = Snapshot()
    supported, _ = _demo_ids()
    cluster = snapshot.cluster_for(supported)
    assert cluster is not None
    symbol_by_disease = {
        node["id"]: node.get("properties", {}).get("gene_symbol")
        for node in snapshot.nodes
        if node["type"] == "disease"
    }
    members = {symbol_by_disease[item] for item in cluster["member_ids"]}
    assert "STXBP1" in members
    assert "DNM1" in members
    assert len(members & {"SNAP25", "STX1B", "VAMP2"}) >= 2
    assert not members & {"GNAO1", "GABRG2", "KCNT1"}


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


def test_gene_bridge_proofs_touch_both_requested_diseases() -> None:
    snapshot = Snapshot()
    diseases = {
        node.get("properties", {}).get("gene_symbol"): node["id"]
        for node in snapshot.nodes
        if node.get("type") == "disease"
    }
    left, right = diseases["STXBP1"], diseases["DNM1"]
    bridges = find_bridges(snapshot, left, right)
    assert bridges
    for bridge in bridges:
        proof_diseases = {edge["disease_id"] for edge in bridge["proving_edges"]}
        assert {left, right} <= proof_diseases


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
    for disease_id in (supported, gap):
        dossier = build_dossier(snapshot, disease_id, "maria", mode="offline")
        assert dossier["dropped_sentences"] == 0
        assert set(SECTION_TITLES) <= {section["key"] for section in dossier["sections"]}
        assert all(section["sentences"] for section in dossier["sections"])
        assert all(
            sentence["edge_ids"] and set(sentence["edge_ids"]) <= snapshot.edge_by_id.keys()
            for section in dossier["sections"]
            for sentence in section["sentences"]
        )
    assert build_dossier(snapshot, supported, "maria", mode="offline")["gap_plan"] is None
    assert build_dossier(snapshot, gap, "maria", mode="offline")["gap_plan"] is not None


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
    payload = {
        "sections": [
            {
                "key": "who_shares",
                "title": "Who shares",
                "sentences": [
                    {"text": "No citation.", "edge_ids": []},
                    {"text": "Unknown citation.", "edge_ids": ["not-an-edge"]},
                ],
            }
        ]
    }
    validated = validate_dossier(payload, snapshot, disease_id)
    assert validated["dropped_sentences"] == 2
    assert all(
        sentence["edge_ids"] and set(sentence["edge_ids"]) <= snapshot.edge_by_id.keys()
        for section in validated["sections"]
        for sentence in section["sentences"]
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
                "edge_ids",
            } <= asset.keys()

        bridge_response = client.get("/api/bridges", params={"a": supported, "b": gap})
        assert bridge_response.status_code == 200
        assert {"a", "b", "people"} <= bridge_response.json().keys()
        _assert_edges_resolve(bridge_response.json(), snapshot, client, resolved_edge_ids)
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
