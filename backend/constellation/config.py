from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"
SNAPSHOT = DATA / "snapshot"
CACHE = DATA / "llm_cache"
SCHEMA_VERSION = 1


def configured_mode() -> str:
    mode = os.getenv("LLM_MODE", "").lower()
    return mode if mode in {"live", "cached", "offline"} else ""


def extraction_model() -> str:
    return os.getenv("CONSTELLATION_EXTRACT_MODEL", "gpt-5-mini")


def agent_model() -> str:
    return os.getenv("CONSTELLATION_AGENT_MODEL", "gpt-5")
