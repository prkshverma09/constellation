from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

import constellation.agents.dossier as dossier_module
import constellation.agents.live as live_module
import constellation.api.app as api_module
from constellation.agents.dossier import SECTION_TITLES, build_dossier, build_live_dossier
from constellation.graph.store import Snapshot


def _demo_disease(snapshot: Snapshot) -> str:
    return next(
        node["id"]
        for node in snapshot.nodes
        if node.get("type") == "disease"
        and node.get("properties", {}).get("gene_symbol") == "STXBP1"
    )


def test_numeric_guard_ignores_numbers_inside_cited_identifiers() -> None:
    assert dossier_module._number_tokens(
        "GO:0005515, HP:0001250, MONDO:0012812, PMID:12345, NCT06555965 and 52%"
    ) == {"52%"}


def test_health_availability_requires_key_and_nonoffline_mode(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(api_module, "get_snapshot", Snapshot)
    monkeypatch.setattr(api_module, "configured_mode", lambda: "cached")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(api_module.app)

    assert client.get("/api/health").json()["llm_available"] is False
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    available = client.get("/api/health").json()
    assert available["llm_available"] is True
    assert "test-only" not in json.dumps(available)
    monkeypatch.setattr(api_module, "configured_mode", lambda: "offline")
    offline = client.get("/api/health").json()
    assert offline["llm_available"] is False
    assert "test-only" not in json.dumps(offline)


def test_live_dossier_drops_outside_citations_and_invented_numbers(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    snapshot = Snapshot()
    disease_id = _demo_disease(snapshot)
    cache_path = tmp_path / "live.json"
    monkeypatch.setattr(
        dossier_module,
        "live_dossier_cache_path",
        lambda *_args: cache_path,
    )

    async def fake_chain(
        _snapshot: Snapshot,
        _disease_id: str,
        _persona: str,
        offline_draft: dict[str, Any],
        _comparator_id: str | None,
        evidence_pack: list[dict[str, Any]],
    ) -> dict[str, Any]:
        assert len(evidence_pack) <= 400
        assert all(
            set(row)
            == {
                "edge_id",
                "subject_label",
                "predicate",
                "object_label",
                "evidence_class",
                "source",
                "source_record",
                "confidence",
                "quote",
            }
            for row in evidence_pack
        )
        allowed = {row["edge_id"] for row in evidence_pack}
        outside = next(edge_id for edge_id in snapshot.edge_by_id if edge_id not in allowed)
        allowed_id = next(iter(allowed))
        sections = json.loads(json.dumps(offline_draft["sections"]))
        sections[0]["sentences"].extend(
            [
                {"text": "This cites an outside edge.", "edge_ids": [outside]},
                {"text": "This includes invented value 987654321.75%.", "edge_ids": [allowed_id]},
            ]
        )
        return {"sections": sections, "dropped_sentences": 0, "markdown": ""}

    monkeypatch.setattr(live_module, "run_agent_chain", fake_chain)
    result = asyncio.run(build_live_dossier(snapshot, disease_id, "maria"))

    sentences = [
        sentence
        for section in result["sections"]
        for sentence in section["sentences"]
    ]
    rendered = " ".join(sentence["text"] for sentence in sentences)
    cited = {edge_id for sentence in sentences for edge_id in sentence["edge_ids"]}
    assert result["mode"] == "live"
    assert result["dropped_sentences"] >= 2
    assert "outside edge" not in rendered
    assert "987654321.75%" not in rendered
    assert cited <= snapshot.edge_by_id.keys()
    assert cache_path.exists()
    assert json.loads(cache_path.read_text(encoding="utf-8"))["mode"] == "live"


def test_live_dossier_entity_guard_drops_unsupported_gene_claim(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    snapshot = Snapshot()
    disease_id = _demo_disease(snapshot)
    monkeypatch.setattr(
        dossier_module,
        "live_dossier_cache_path",
        lambda *_args: tmp_path / "live.json",
    )

    async def fake_chain(
        _snapshot: Snapshot,
        _disease_id: str,
        _persona: str,
        offline_draft: dict[str, Any],
        _comparator_id: str | None,
        _evidence_pack: list[dict[str, Any]],
    ) -> dict[str, Any]:
        sections = json.loads(json.dumps(offline_draft["sections"]))
        next(section for section in sections if section["key"] == "what_differs")[
            "sentences"
        ].append(
            {
                "text": "CACNA1A has gain-of-function evidence",
                "edge_ids": ["87257c69fcfbcdad", "df57b34e24ef5206"],
            }
        )
        return {"sections": sections, "dropped_sentences": 0, "markdown": ""}

    monkeypatch.setattr(live_module, "run_agent_chain", fake_chain)
    result = asyncio.run(build_live_dossier(snapshot, disease_id, "maria"))

    sentences = [
        sentence["text"]
        for section in result["sections"]
        for sentence in section["sentences"]
    ]
    assert "CACNA1A has gain-of-function evidence" not in sentences
    assert result["dropped_sentences"] >= 1


def test_live_dossier_entity_guard_keeps_supported_gene_claim(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    snapshot = Snapshot()
    disease_id = _demo_disease(snapshot)
    monkeypatch.setattr(
        dossier_module,
        "live_dossier_cache_path",
        lambda *_args: tmp_path / "live.json",
    )

    async def fake_chain(
        _snapshot: Snapshot,
        _disease_id: str,
        _persona: str,
        offline_draft: dict[str, Any],
        _comparator_id: str | None,
        _evidence_pack: list[dict[str, Any]],
    ) -> dict[str, Any]:
        sections = json.loads(json.dumps(offline_draft["sections"]))
        next(section for section in sections if section["key"] == "what_differs")[
            "sentences"
        ].append(
            {
                "text": "CACNA1A has gain-of-function evidence",
                "edge_ids": [
                    "87257c69fcfbcdad",
                    "df57b34e24ef5206",
                    "4e69b8ecbf6740e9",
                ],
            }
        )
        return {"sections": sections, "dropped_sentences": 0, "markdown": ""}

    monkeypatch.setattr(live_module, "run_agent_chain", fake_chain)
    result = asyncio.run(build_live_dossier(snapshot, disease_id, "maria"))

    sentences = [
        sentence["text"]
        for section in result["sections"]
        for sentence in section["sentences"]
    ]
    assert "CACNA1A has gain-of-function evidence" in sentences


def test_live_dossier_next_step_drift_falls_back_to_offline(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    snapshot = Snapshot()
    disease_id = _demo_disease(snapshot)
    dnm1_id = next(
        node["id"]
        for node in snapshot.nodes
        if node.get("type") == "disease"
        and node.get("properties", {}).get("gene_symbol") == "DNM1"
    )
    cache_path = tmp_path / "live.json"
    monkeypatch.setattr(
        dossier_module,
        "live_dossier_cache_path",
        lambda *_args: cache_path,
    )
    offline_draft = build_dossier(
        snapshot,
        disease_id,
        "maria",
        mode="live",
        comparator_id=dnm1_id,
    )
    offline_next_step = next(
        section for section in offline_draft["sections"] if section["key"] == "next_step"
    )
    offline_text = " ".join(
        sentence["text"] for sentence in offline_next_step["sentences"]
    )
    assert "NCT06555965" in offline_text
    assert "DNM1" in offline_text
    assert "Ingo Helbig" in offline_text

    async def fake_chain(
        _snapshot: Snapshot,
        _disease_id: str,
        _persona: str,
        draft: dict[str, Any],
        _comparator_id: str | None,
        evidence_pack: list[dict[str, Any]],
    ) -> dict[str, Any]:
        sections = json.loads(json.dumps(draft["sections"]))
        stx1b_edge_id = next(
            row["edge_id"]
            for row in evidence_pack
            if "STX1B" in row["subject_label"] or "STX1B" in row["object_label"]
        )
        next(section for section in sections if section["key"] == "next_step")[
            "sentences"
        ] = [
            {
                "text": "Review NCT05462054 and contact Rikke S Møller about STX1B.",
                "edge_ids": [stx1b_edge_id],
            }
        ]
        return {"sections": sections, "dropped_sentences": 0, "markdown": ""}

    monkeypatch.setattr(live_module, "run_agent_chain", fake_chain)
    result = asyncio.run(
        build_live_dossier(snapshot, disease_id, "maria", comparator_id=dnm1_id)
    )

    next_step = next(
        section for section in result["sections"] if section["key"] == "next_step"
    )
    assert next_step["sentences"] == offline_next_step["sentences"]
    assert "next_step" in result["fallback_sections"]
    assert result["dropped_sentences"] >= 1
    assert result["comparator_id"] == dnm1_id
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached["comparator_id"] == dnm1_id


def test_live_dossier_exception_returns_offline_fallback(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    snapshot = Snapshot()
    disease_id = _demo_disease(snapshot)
    cache_path = tmp_path / "live.json"
    monkeypatch.setattr(
        dossier_module,
        "live_dossier_cache_path",
        lambda *_args: cache_path,
    )

    async def fail_chain(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("mocked writer failure")

    monkeypatch.setattr(live_module, "run_agent_chain", fail_chain)
    result = asyncio.run(build_live_dossier(snapshot, disease_id, "maria"))

    assert result["mode"] == "offline-fallback"
    assert result["fallback_sections"] == list(SECTION_TITLES)
    assert result["sections"] == build_dossier(snapshot, disease_id, "maria")["sections"]
    assert not cache_path.exists()


def test_cached_llm_dossier_is_served_without_regeneration(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    snapshot = Snapshot()
    disease_id = _demo_disease(snapshot)
    cache_path = tmp_path / "dossier-live-cache.json"
    cached = build_dossier(snapshot, disease_id, "maria")
    cached.update(
        {
            "mode": "live",
            "model": "gpt-5",
            "elapsed_ms": 1234,
            "generated_at": "2025-03-01T00:00:00Z",
        }
    )
    cache_path.write_text(json.dumps(cached), encoding="utf-8")
    monkeypatch.setattr(api_module, "get_snapshot", lambda: snapshot)
    monkeypatch.setattr(api_module, "configured_mode", lambda: "cached")
    monkeypatch.setattr(api_module, "live_dossier_cache_path", lambda *_args: cache_path)

    def unexpected_regeneration(*_args: Any) -> None:
        raise AssertionError("unexpected regeneration")

    monkeypatch.setattr(
        api_module,
        "build_live_dossier",
        unexpected_regeneration,
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    client = TestClient(api_module.app)

    response = client.post(
        "/api/dossier",
        json={"disease": disease_id, "persona": "maria"},
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "cached-llm"
    assert response.json()["model"] == "gpt-5"
    assert response.json()["generated_at"] == "2025-03-01T00:00:00Z"
    assert response.json()["elapsed_ms"] == 1234


def test_dossier_regenerate_runs_live_chain_in_cached_mode(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    snapshot = Snapshot()
    disease_id = _demo_disease(snapshot)
    dnm1_id = next(
        node["id"]
        for node in snapshot.nodes
        if node.get("type") == "disease"
        and node.get("properties", {}).get("gene_symbol") == "DNM1"
    )
    called: dict[str, Any] = {}

    async def fake_live(
        _snapshot: Snapshot,
        disease: str,
        persona: str,
        comparator_id: str | None,
    ) -> dict[str, Any]:
        called.update(
            disease=disease,
            persona=persona,
            comparator_id=comparator_id,
        )
        return {"mode": "live", "disease_id": disease, "persona": persona}

    monkeypatch.setattr(api_module, "get_snapshot", lambda: snapshot)
    monkeypatch.setattr(api_module, "configured_mode", lambda: "cached")
    monkeypatch.setattr(
        api_module,
        "live_dossier_cache_path",
        lambda *_args: tmp_path / "missing-cache.json",
    )
    monkeypatch.setattr(api_module, "build_live_dossier", fake_live)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    response = TestClient(api_module.app).post(
        "/api/dossier",
        json={
            "disease": disease_id,
            "persona": "maria",
            "vs": dnm1_id,
            "regenerate": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "live"
    assert response.json()["model"] == api_module.agent_model()
    assert isinstance(response.json()["elapsed_ms"], int)
    assert response.json()["usage"] == {"input_tokens": 0, "output_tokens": 0}
    assert called == {
        "disease": disease_id,
        "persona": "maria",
        "comparator_id": dnm1_id,
    }
