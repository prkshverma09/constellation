from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

from constellation.config import RAW


class CachedHTTP:
    def __init__(self, cache_dir: Path = RAW, timeout: float = 35.0) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "ConstellationPrototype/0.1 (public research data)"},
        )
        self._last_ncbi = 0.0

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        ncbi: bool = False,
    ) -> Any:
        request_key = json.dumps(
            [method.upper(), url, params or {}, json_body or {}],
            sort_keys=True,
            separators=(",", ":"),
        )
        cache_path = self.cache_dir / (hashlib.sha256(request_key.encode()).hexdigest() + ".json")
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
        if ncbi:
            wait = 1 / 3 - (time.monotonic() - self._last_ncbi)
            if wait > 0:
                time.sleep(wait)
        response = self.client.request(method, url, params=params, json=json_body)
        if ncbi:
            self._last_ncbi = time.monotonic()
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "json" in content_type or response.text.lstrip().startswith(("{", "[")):
            value = response.json()
        else:
            value = {"_text": response.text}
        cache_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return value

    def get(self, url: str, *, params: dict[str, Any] | None = None, ncbi: bool = False) -> Any:
        return self.request("GET", url, params=params, ncbi=ncbi)

    def post(
        self, url: str, *, json_body: dict[str, Any], params: dict[str, Any] | None = None
    ) -> Any:
        return self.request("POST", url, params=params, json_body=json_body)

    def close(self) -> None:
        self.client.close()
