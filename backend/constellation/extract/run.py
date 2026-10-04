from __future__ import annotations

import asyncio
import json
import sys
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from openai import OpenAI

from constellation.config import CACHE, DATA, extraction_model
from constellation.extract.cache import extraction_cache_path
from constellation.extract.live import extract_live
from constellation.extract.models import ExtractionResult
from constellation.graph.store import Snapshot

CONCURRENCY = 16
MAX_TRIES = 5


@dataclass(frozen=True)
class ExtractionJob:
    paper: dict[str, Any]
    symbol: str
    other_genes: list[str]


@dataclass
class ExtractionStats:
    total_items: int = 0
    cached_hits: int = 0
    new_calls: int = 0
    failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


def cached_extraction_jobs(
    snapshot: Snapshot,
    abstracts: dict[str, str],
    symbols: list[str],
) -> list[ExtractionJob]:
    symbol_by_gene_id = {
        node["id"]: str(node.get("properties", {}).get("symbol", ""))
        for node in snapshot.nodes
        if node.get("type") == "gene"
    }
    jobs = []
    for node in snapshot.nodes:
        if node.get("type") != "paper":
            continue
        paper_id = node["id"]
        pmid = paper_id.removeprefix("PMID:")
        abstract = abstracts.get(paper_id, "")
        if not abstract:
            continue
        associated = sorted(
            {
                symbol_by_gene_id[edge["object"]]
                for edge in snapshot.edges_by_subject.get(paper_id, [])
                if edge["predicate"] == "mentions_gene"
                and edge["object"] in symbol_by_gene_id
            }
        )
        paper = {"pmid": pmid, "title": node["label"], "abstract": abstract}
        for symbol in associated:
            if symbol not in symbols:
                continue
            jobs.append(
                ExtractionJob(
                    paper=paper,
                    symbol=symbol,
                    other_genes=[candidate for candidate in symbols if candidate != symbol],
                )
            )
    return jobs


def _status_code(error: Exception) -> int | None:
    status = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


async def _extract_with_retry(
    job: ExtractionJob,
    client: Any,
    usage_callback: Callable[[int, int], None],
    sleep: Callable[[float], Awaitable[None]],
) -> ExtractionResult:
    for attempt in range(MAX_TRIES):
        try:
            return await asyncio.to_thread(
                extract_live,
                job.paper,
                job.symbol,
                job.other_genes,
                client,
                usage_callback,
            )
        except Exception as error:
            status = _status_code(error)
            if status != 429 and (status is None or not 500 <= status <= 599):
                raise
            if attempt == MAX_TRIES - 1:
                raise
            await sleep(float(2**attempt))
    raise RuntimeError("Unreachable extraction retry state")


async def run_extractions(
    jobs: list[ExtractionJob],
    *,
    client: Any,
    cache_dir: Path = CACHE,
    concurrency: int = CONCURRENCY,
    progress_every: int = 100,
    progress: Callable[[int, int], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> ExtractionStats:
    stats = ExtractionStats(total_items=len(jobs))
    semaphore = asyncio.Semaphore(concurrency)
    usage_lock = threading.Lock()
    completed = 0

    def add_usage(input_tokens: int, output_tokens: int) -> None:
        with usage_lock:
            stats.input_tokens += input_tokens
            stats.output_tokens += output_tokens

    async def process(job: ExtractionJob) -> None:
        nonlocal completed
        cache_path = extraction_cache_path(job.paper, job.symbol, cache_dir)
        if cache_path.exists():
            stats.cached_hits += 1
        else:
            stats.new_calls += 1
            async with semaphore:
                try:
                    result = await _extract_with_retry(job, client, add_usage, sleep)
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
                except Exception as error:
                    stats.failures += 1
                    status = _status_code(error)
                    error_type = type(error).__name__
                    status_text = f"HTTP {status}" if status is not None else error_type
                    print(
                        f"Extraction failed for PMID:{job.paper['pmid']} "
                        f"{job.symbol}: {status_text}",
                        file=sys.stderr,
                    )
        completed += 1
        if progress and completed % progress_every == 0:
            progress(completed, len(jobs))

    await asyncio.gather(*(process(job) for job in jobs))
    return stats


def _load_jobs() -> list[ExtractionJob]:
    with (DATA / "slice.yaml").open(encoding="utf-8") as stream:
        symbols = yaml.safe_load(stream)["genes"]
    abstracts_path = DATA / "raw" / "pubmed_abstracts.json"
    if not abstracts_path.exists():
        raise FileNotFoundError("Cached PubMed abstracts are missing; run `make data` first.")
    abstracts = json.loads(abstracts_path.read_text(encoding="utf-8"))
    return cached_extraction_jobs(Snapshot(), abstracts, symbols)


async def main() -> None:
    jobs = _load_jobs()
    client = OpenAI()

    def show_progress(completed: int, total: int) -> None:
        print(f"Processed {completed}/{total} extraction items.", flush=True)

    try:
        print(f"Extracting {len(jobs)} paper/gene pairs with {extraction_model()}.", flush=True)
        stats = await run_extractions(jobs, client=client, progress=show_progress)
    finally:
        client.close()
    print(
        f"Extraction summary ({extraction_model()}): total items={stats.total_items}, "
        f"cached hits={stats.cached_hits}, new calls={stats.new_calls}, "
        f"failures={stats.failures}, input tokens={stats.input_tokens}, "
        f"output tokens={stats.output_tokens}.",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
