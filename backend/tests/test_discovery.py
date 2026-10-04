from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import constellation.build as build_module
import constellation.discover.web as discovery_module
from constellation.discover.web import (
    MODEL,
    PROMPT_VERSION,
    DiscoveredOrg,
    Discovery,
    discover_gene,
    discovery_cache_path,
    validate_discovered_org,
)
from constellation.ingest.cache import CachedHTTP
from constellation.ledger import validate_edge


class FakeResponses:
    def __init__(self, result: Discovery) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=self.result)


class FakeClient:
    def __init__(self, result: Discovery) -> None:
        self.responses = FakeResponses(result)


def test_discovery_uses_structured_web_search_and_caches_by_version(
    tmp_path: Path,
) -> None:
    result = Discovery(
        orgs=[
            DiscoveredOrg(
                name="STXBP1 families",
                url="https://example.org/stxbp1",
                kind="foundation",
                snippet="Families with STXBP1-related conditions can enroll.",
            )
        ]
    )
    client = FakeClient(result)
    actual, cached = discover_gene(
        "STXBP1",
        "developmental and epileptic encephalopathy 4",
        client=client,
        cache_dir=tmp_path,
    )

    assert not cached
    assert actual == result
    assert discovery_cache_path("STXBP1", tmp_path).name.startswith("discover-")
    assert client.responses.calls[0]["model"] == MODEL == "gpt-5"
    assert client.responses.calls[0]["tools"] == [{"type": "web_search"}]
    assert client.responses.calls[0]["text_format"] is Discovery
    assert client.responses.calls[0]["input"] == (
        "Find patient advocacy organizations, family foundations, patient registries and "
        "research networks dedicated specifically to STXBP1-related disorders "
        "(developmental and epileptic encephalopathy 4). Only include an organization if its "
        "own website names STXBP1. Exclude general epilepsy charities unless they run a "
        "STXBP1-specific program or registry, and exclude commercial genetic-testing companies. "
        "Return at most 5. For each give: name; url of the organization's own page about STXBP1; "
        "kind (foundation, registry or research_network); and snippet, a short verbatim sentence "
        "from that page that mentions STXBP1. If none exist, return an empty list."
    )

    cache = discovery_cache_path("STXBP1", tmp_path).read_text(encoding="utf-8")
    assert PROMPT_VERSION in cache
    cached_result, was_cached = discover_gene(
        "STXBP1",
        "developmental and epileptic encephalopathy 4",
        client=client,
        cache_dir=tmp_path,
    )
    assert was_cached
    assert cached_result == result
    assert len(client.responses.calls) == 1


def test_discovery_validation_accepts_gene_mention_and_rejects_missing_symbol() -> None:
    organization = DiscoveredOrg(
        name="STXBP1 Families",
        url="https://example.org/stxbp1",
        kind="foundation",
        snippet="Families with STXBP1-related conditions can enroll.",
    )
    accepted, reason = validate_discovered_org(
        organization,
        "STXBP1",
        {
            "status_code": 200,
            "url": "https://www.example.org/stxbp1",
            "content_type": "text/html; charset=utf-8",
            "text": (
                "<html><script>STXBP1 ignored</script><p>Families with STXBP1-related conditions "
                "can enroll.</p></html>"
            ),
        },
    )
    assert reason is None
    assert accepted is not None
    assert accepted.domain == "example.org"
    assert accepted.quote == organization.snippet

    rejected, reason = validate_discovered_org(
        organization,
        "STXBP1",
        {
            "status_code": 200,
            "url": "https://example.org/stxbp1",
            "content_type": "text/html",
            "text": "<p>Families with STXBP10-related conditions can enroll.</p>",
        },
    )
    assert rejected is None
    assert reason == "gene_symbol_not_found"


def test_discovery_validation_rejects_non_html_and_non_success_pages() -> None:
    organization = DiscoveredOrg(
        name="STXBP1 Registry",
        url="https://example.org/stxbp1",
        kind="registry",
        snippet="The STXBP1 registry is open.",
    )
    validated, reason = validate_discovered_org(
        organization,
        "STXBP1",
        {
            "status_code": 200,
            "url": organization.url,
            "content_type": "application/json",
            "text": '{"gene":"STXBP1"}',
        },
    )
    assert validated is None
    assert reason == "non_html_content_type"

    validated, reason = validate_discovered_org(
        organization,
        "STXBP1",
        {
            "status_code": 404,
            "url": organization.url,
            "content_type": "text/html",
            "text": "<p>STXBP1</p>",
        },
    )
    assert validated is None
    assert reason == "http_status_404"


def test_build_adds_only_validated_non_seed_organizations(
    monkeypatch: Any,
) -> None:
    organizations = Discovery(
        orgs=[
            DiscoveredOrg(
                name="Seed domain duplicate",
                url="https://sub.seed.org/stxbp1",
                kind="foundation",
                snippet="The STXBP1 program supports families.",
            ),
            DiscoveredOrg(
                name="New STXBP1 Registry",
                url="https://www.new.org/stxbp1",
                kind="registry",
                snippet="The STXBP1 registry supports research.",
            ),
        ]
    )

    class FakeHTTP:
        pages = {
            "https://sub.seed.org/stxbp1": {
                "status_code": 200,
                "url": "https://sub.seed.org/stxbp1",
                "content_type": "text/html",
                "text": "<p>The STXBP1 program supports families.</p>",
            },
            "https://www.new.org/stxbp1": {
                "status_code": 200,
                "url": "https://www.new.org/stxbp1",
                "content_type": "text/html",
                "text": "<p>The STXBP1 registry supports research.</p>",
            },
        }

        def get_html(self, url: str) -> dict[str, Any]:
            return self.pages[url]

    disease_id = "MONDO:0012812"
    nodes = {
        disease_id: {
            "id": disease_id,
            "type": "disease",
            "label": "Developmental and epileptic encephalopathy 4",
            "properties": {},
        }
    }
    edges: list[dict[str, Any]] = []
    source_counts: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(build_module, "load_cached_discovery", lambda _symbol: organizations)

    rejected = build_module._add_web_discovered_orgs(
        cast(CachedHTTP, FakeHTTP()),
        [{"name": "Curated seed", "url": "https://seed.org", "genes": ["STXBP1"]}],
        {"STXBP1": {"disease_id": disease_id}},
        nodes,
        edges,
        source_counts,
    )

    assert len(edges) == 1
    assert edges[0]["source"] == "openai_web_search"
    assert edges[0]["evidence_class"] == "web_discovered"
    assert edges[0]["predicate"] == "serves"
    assert edges[0]["confidence"] == 0.6
    assert edges[0]["source_record"] == "https://www.new.org/stxbp1"
    assert edges[0]["method"] == "gpt-5 web_search discover-v1"
    assert edges[0]["quote"] == "The STXBP1 registry supports research."
    assert validate_edge(edges[0], set(nodes)) is None
    assert source_counts["discover:STXBP1"]["source"] == "OpenAI web search (validated)"
    assert source_counts["discover:STXBP1"]["count"] == 1
    assert source_counts["discover:STXBP1"]["rejected"] == 1
    assert rejected[0][1] == "duplicate_seed_registrable_domain:seed.org"


def test_discovery_runner_limits_concurrency_and_skips_timed_out_gene(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    symbols = ["A", "B", "C", "D", "E"]
    (tmp_path / "slice.yaml").write_text(
        "genes: [A, B, C, D, E]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(discovery_module, "DATA", tmp_path)
    monkeypatch.setattr(
        discovery_module,
        "Snapshot",
        lambda: SimpleNamespace(
            nodes=[
                {
                    "id": f"MONDO:{index}",
                    "type": "disease",
                    "label": symbol,
                    "properties": {"gene_symbol": symbol},
                }
                for index, symbol in enumerate(symbols)
            ]
        ),
    )
    monkeypatch.setattr(
        discovery_module,
        "OpenAI",
        lambda **_kwargs: SimpleNamespace(close=lambda: None),
    )
    monkeypatch.setattr(
        discovery_module,
        "discovery_cache_path",
        lambda symbol: tmp_path / f"{symbol}.json",
    )
    monkeypatch.setattr(discovery_module, "DISCOVERY_TIMEOUT_SECONDS", 0.1)
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_discover_gene(
        symbol: str,
        _label: str,
        *,
        client: Any,
    ) -> tuple[Discovery, bool]:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.2 if symbol == "E" else 0.01)
            return Discovery(orgs=[]), False
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(discovery_module, "discover_gene", fake_discover_gene)
    discovery_module.run_discovery()

    output = capsys.readouterr().out
    assert 2 <= max_active <= discovery_module.MAX_CONCURRENT_REQUESTS
    assert "E: failed (TimeoutError)" in output
    assert "failures=1" in output
