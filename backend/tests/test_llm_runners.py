from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import constellation.extract.run as extraction_runner
from constellation.extract.cache import extraction_cache_path
from constellation.extract.models import ExtractionResult
from constellation.extract.run import ExtractionJob


def test_extraction_runner_skips_cache_hit_and_retries_429(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    cached_paper = {"pmid": "100", "title": "Cached", "abstract": "cached abstract"}
    new_paper = {"pmid": "200", "title": "Retry", "abstract": "new abstract"}
    extraction_cache_path(cached_paper, "STXBP1", tmp_path).write_text("{}", encoding="utf-8")
    jobs = [
        ExtractionJob(cached_paper, "STXBP1", ["DNM1"]),
        ExtractionJob(new_paper, "STXBP1", ["DNM1"]),
    ]
    calls = 0
    delays: list[float] = []
    progress: list[tuple[int, int]] = []

    class RateLimited(Exception):
        status_code = 429

    def fake_extract_live(
        paper: dict[str, Any],
        _symbol: str,
        _other_genes: list[str],
        _client: Any,
        usage_callback: Any,
    ) -> ExtractionResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RateLimited()
        usage_callback(11, 22)
        return ExtractionResult(pmid=paper["pmid"], claims=[])

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(extraction_runner, "extract_live", fake_extract_live)
    result = asyncio.run(
        extraction_runner.run_extractions(
            jobs,
            client=object(),
            cache_dir=tmp_path,
            progress_every=1,
            progress=lambda completed, total: progress.append((completed, total)),
            sleep=fake_sleep,
        )
    )

    assert result.total_items == 2
    assert result.cached_hits == 1
    assert result.new_calls == 1
    assert result.failures == 0
    assert result.input_tokens == 11
    assert result.output_tokens == 22
    assert calls == 2
    assert delays == [1]
    assert sorted(progress) == [(1, 2), (2, 2)]
    assert extraction_cache_path(new_paper, "STXBP1", tmp_path).exists()
