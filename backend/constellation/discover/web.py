from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from openai import OpenAI
from pydantic import BaseModel

from constellation.config import CACHE, DATA
from constellation.graph.store import Snapshot

MODEL = "gpt-5"
PROMPT_VERSION = "discover-v1"
MAX_CONCURRENT_REQUESTS = 4
DISCOVERY_TIMEOUT_SECONDS = 120
MULTI_LABEL_PUBLIC_SUFFIXES = frozenset(
    {
        "ac.uk",
        "co.in",
        "co.id",
        "co.jp",
        "co.nz",
        "co.uk",
        "co.za",
        "com.au",
        "com.ar",
        "com.bd",
        "com.br",
        "com.co",
        "com.cn",
        "com.eg",
        "com.hk",
        "com.mx",
        "com.my",
        "com.ng",
        "com.pe",
        "com.ph",
        "com.pk",
        "com.pl",
        "com.sa",
        "com.sg",
        "com.tr",
        "com.tw",
        "com.ua",
        "com.vn",
        "edu.au",
        "gov.au",
        "gov.uk",
        "net.au",
        "net.br",
        "net.cn",
        "net.in",
        "net.nz",
        "net.uk",
        "or.id",
        "or.kr",
        "or.th",
        "org.au",
        "org.br",
        "org.cn",
        "org.in",
        "org.nz",
        "org.uk",
        "org.za",
        "ac.kr",
        "co.kr",
        "co.th",
        "go.kr",
    }
)


class DiscoveredOrg(BaseModel):
    name: str
    url: str
    kind: Literal["foundation", "registry", "research_network"]
    snippet: str


class Discovery(BaseModel):
    orgs: list[DiscoveredOrg]


@dataclass(frozen=True)
class ValidatedOrgPage:
    domain: str
    page_url: str
    quote: str


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style", "noscript"}:
            self.hidden_tags.append(tag.casefold())

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in self.hidden_tags:
            self.hidden_tags.remove(normalized)

    def handle_data(self, data: str) -> None:
        if not self.hidden_tags:
            self.parts.append(data)


def discovery_cache_path(symbol: str, cache_dir: Path = CACHE) -> Path:
    key = json.dumps(
        [symbol, MODEL, PROMPT_VERSION],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return cache_dir / f"discover-{digest}.json"


def discovery_prompt(symbol: str, disease_label: str) -> str:
    return (
        f"Find patient advocacy organizations, family foundations, patient registries and research "
        f"networks dedicated specifically to {symbol}-related disorders ({disease_label}). Only "
        f"include an organization if its own website names {symbol}. Exclude general epilepsy "
        f"charities unless they run a {symbol}-specific program or registry, and exclude "
        f"commercial genetic-testing companies. Return at most 5. For each give: name; url of the "
        f"organization's own page about {symbol}; kind (foundation, registry or research_network); "
        f"and snippet, a short verbatim sentence from that page that mentions {symbol}. If none "
        f"exist, return an empty list."
    )


def load_cached_discovery(
    symbol: str,
    cache_dir: Path = CACHE,
) -> Discovery | None:
    path = discovery_cache_path(symbol, cache_dir)
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if (
            cached.get("symbol") != symbol
            or cached.get("model") != MODEL
            or cached.get("prompt_version") != PROMPT_VERSION
        ):
            return None
        result = Discovery.model_validate(cached["result"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return Discovery(orgs=result.orgs[:5])


def discover_gene(
    symbol: str,
    disease_label: str,
    *,
    client: Any | None = None,
    cache_dir: Path = CACHE,
) -> tuple[Discovery, bool]:
    cached = load_cached_discovery(symbol, cache_dir)
    if cached is not None:
        return cached, True
    api = client or OpenAI()
    response = api.responses.parse(
        model=MODEL,
        input=discovery_prompt(symbol, disease_label),
        tools=[{"type": "web_search"}],
        text_format=Discovery,
    )
    parsed = getattr(response, "output_parsed", None)
    if isinstance(parsed, Discovery):
        result = parsed
    elif isinstance(parsed, dict):
        result = Discovery.model_validate(parsed)
    else:
        raise ValueError("OpenAI structured discovery returned no parsed result")
    result = Discovery(orgs=result.orgs[:5])
    raw_usage = getattr(response, "usage", None)
    usage = {
        "input_tokens": int(getattr(raw_usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(raw_usage, "output_tokens", 0) or 0),
    }
    path = discovery_cache_path(symbol, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "symbol": symbol,
                "model": MODEL,
                "prompt_version": PROMPT_VERSION,
                "generated_at": datetime.now(UTC).isoformat(),
                "usage": usage,
                "result": result.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return result, False


def registrable_domain(url: str) -> str | None:
    try:
        host = urlsplit(url.strip()).hostname
    except ValueError:
        return None
    if not host:
        return None
    try:
        host = host.casefold().rstrip(".").encode("idna").decode("ascii")
    except UnicodeError:
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) < 2 or any(not label for label in labels):
        return None
    suffix = ".".join(labels[-2:])
    label_count = 3 if suffix in MULTI_LABEL_PUBLIC_SUFFIXES else 2
    if len(labels) < label_count:
        return None
    return ".".join(labels[-label_count:])


def _visible_text(html: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(html)
    parser.close()
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def validate_discovered_org(
    org: DiscoveredOrg | dict[str, Any],
    symbol: str,
    page: dict[str, Any],
) -> tuple[ValidatedOrgPage | None, str | None]:
    try:
        row = org if isinstance(org, DiscoveredOrg) else DiscoveredOrg.model_validate(org)
    except ValueError:
        return None, "invalid_discovery_record"
    if not row.name.strip():
        return None, "missing_organization_name"
    try:
        status_code = int(page.get("status_code", 0))
    except (TypeError, ValueError):
        return None, "missing_http_status"
    if status_code != 200:
        return None, f"http_status_{status_code}"
    content_type = str(page.get("content_type", "")).split(";", 1)[0].strip().casefold()
    if content_type not in {"text/html", "application/xhtml+xml"}:
        return None, "non_html_content_type"
    text = _visible_text(str(page.get("text", "")))
    gene_pattern = re.compile(rf"(?<!\w){re.escape(symbol)}(?!\w)", re.IGNORECASE)
    mention = gene_pattern.search(text)
    if mention is None:
        return None, "gene_symbol_not_found"
    page_url = str(page.get("url") or row.url)
    domain = registrable_domain(page_url)
    if domain is None:
        return None, "invalid_page_url"
    snippet = re.sub(r"\s+", " ", row.snippet).strip()
    if snippet and snippet.casefold() in text.casefold():
        quote = snippet
    else:
        quote = text[max(0, mention.start() - 150) : min(len(text), mention.end() + 150)].strip()
    return ValidatedOrgPage(domain=domain, page_url=page_url, quote=quote), None


def run_discovery() -> None:
    with (DATA / "slice.yaml").open(encoding="utf-8") as stream:
        slice_config = yaml.safe_load(stream)
    snapshot = Snapshot()
    diseases = {
        str(node.get("properties", {}).get("gene_symbol", "")): node
        for node in snapshot.nodes
        if node.get("type") == "disease"
    }
    client = OpenAI(timeout=DISCOVERY_TIMEOUT_SECONDS, max_retries=0)
    total_input_tokens = 0
    total_output_tokens = 0
    failures = 0
    try:
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_REQUESTS) as executor:
            genes = slice_config["genes"]
            for offset in range(0, len(genes), MAX_CONCURRENT_REQUESTS):
                batch = genes[offset : offset + MAX_CONCURRENT_REQUESTS]
                jobs = {}
                for symbol in batch:
                    disease = diseases.get(symbol)
                    if disease is None:
                        print(
                            f"{symbol}: skipped (disease is absent from the snapshot)",
                            flush=True,
                        )
                        continue
                    jobs[symbol] = executor.submit(
                        discover_gene,
                        symbol,
                        disease["label"],
                        client=client,
                    )
                completed, _pending = wait(
                    jobs.values(),
                    timeout=DISCOVERY_TIMEOUT_SECONDS,
                )
                for symbol, future in jobs.items():
                    if future not in completed:
                        future.cancel()
                        failures += 1
                        print(f"{symbol}: failed (TimeoutError)", flush=True)
                        continue
                    try:
                        result, cached = future.result()
                    except Exception as error:
                        failures += 1
                        print(f"{symbol}: failed ({type(error).__name__})", flush=True)
                        continue
                    try:
                        cache = json.loads(
                            discovery_cache_path(symbol).read_text(encoding="utf-8")
                        )
                    except (OSError, json.JSONDecodeError):
                        cache = {}
                    usage = cache.get("usage", {})
                    input_tokens = int(usage.get("input_tokens", 0) or 0)
                    output_tokens = int(usage.get("output_tokens", 0) or 0)
                    total_input_tokens += input_tokens
                    total_output_tokens += output_tokens
                    cache_label = "cached" if cached else "new"
                    print(
                        f"{symbol}: {cache_label}, organizations={len(result.orgs)}, "
                        f"input_tokens={input_tokens}, output_tokens={output_tokens}",
                        flush=True,
                    )
    finally:
        client.close()
    print(
        f"Discovery usage: input_tokens={total_input_tokens}, "
        f"output_tokens={total_output_tokens}; failures={failures}",
        flush=True,
    )


if __name__ == "__main__":
    run_discovery()
