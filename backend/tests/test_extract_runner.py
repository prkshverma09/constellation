from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

from constellation.extract.cache import extraction_cache_path
from constellation.extract.models import ExtractionResult
from constellation.extract.run import (
    ExtractionJob,
    cached_extraction_jobs,
    run_extractions,
)
from constellation.graph.store import Snapshot


def test_extraction_cache_key_uses_schema_v2(tmp_path: Path) -> None:
    paper = {"pmid": "123", "abstract": "An abstract."}
    assert extraction_cache_path(paper, "STXBP1", tmp_path).name.startswith("extract-")
    assert extraction_cache_path(paper, "STXBP1", tmp_path) != (
        tmp_path / "extract-legacy-v1.json"
    )


def test_cached_extraction_jobs_pair_papers_with_associated_slice_genes() -> None:
    class FakeSnapshot:
        nodes = [
            {"id": "PMID:1", "type": "paper", "label": "Paper title"},
            {"id": "HGNC:1", "type": "gene", "properties": {"symbol": "STXBP1"}},
            {"id": "HGNC:2", "type": "gene", "properties": {"symbol": "DNM1"}},
        ]
        edges_by_subject = {
            "PMID:1": [
                {"predicate": "mentions_gene", "object": "HGNC:1"},
                {"predicate": "mentions_gene", "object": "HGNC:2"},
            ]
        }

    jobs = cached_extraction_jobs(
        cast(Snapshot, FakeSnapshot()),
        {"PMID:1": "Exact abstract."},
        ["STXBP1", "DNM1", "GNAO1"],
    )

    assert [(job.paper["pmid"], job.symbol) for job in jobs] == [
        ("1", "DNM1"),
        ("1", "STXBP1"),
    ]
    assert jobs[1].paper["title"] == "Paper title"
    assert jobs[1].paper["abstract"] == "Exact abstract."
    assert jobs[1].other_genes == ["DNM1", "GNAO1"]


def test_extraction_runner_skips_cache_hits_and_retries_429(tmp_path: Path) -> None:
    paper = {"pmid": "123", "title": "Title", "abstract": "Abstract."}
    cached_paper = {"pmid": "456", "title": "Cached", "abstract": "Cached abstract."}
    cached_path = extraction_cache_path(cached_paper, "STXBP1", tmp_path)
    cached_path.parent.mkdir(parents=True, exist_ok=True)
    cached_path.write_text(ExtractionResult(pmid="456", claims=[]).model_dump_json())
    retry_delays: list[float] = []

    class RateLimit(Exception):
        status_code = 429

    class FakeResponses:
        calls = 0

        def parse(self, **_kwargs: Any) -> Any:
            self.calls += 1
            if self.calls == 1:
                raise RateLimit()
            return type(
                "Response",
                (),
                {
                    "output_parsed": ExtractionResult(pmid="123", claims=[]),
                    "usage": type(
                        "Usage", (), {"input_tokens": 10, "output_tokens": 4}
                    )(),
                },
            )()

    class FakeClient:
        responses = FakeResponses()

    async def no_wait(seconds: float) -> None:
        retry_delays.append(seconds)

    jobs = [
        ExtractionJob(paper=paper, symbol="STXBP1", other_genes=["DNM1"]),
        ExtractionJob(
            paper=cached_paper,
            symbol="STXBP1",
            other_genes=["DNM1"],
        ),
    ]
    stats = asyncio.run(
        run_extractions(
            jobs,
            client=FakeClient(),
            cache_dir=tmp_path,
            progress_every=100,
            sleep=no_wait,
        )
    )

    assert stats.total_items == 2
    assert stats.cached_hits == 1
    assert stats.new_calls == 1
    assert stats.failures == 0
    assert stats.input_tokens == 10
    assert stats.output_tokens == 4
    assert retry_delays == [1.0]
    assert FakeClient.responses.calls == 2
    assert extraction_cache_path(paper, "STXBP1", tmp_path).exists()
