from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from constellation.config import SNAPSHOT

JSON_COLUMNS = {"properties", "member_ids", "shared_pathways"}


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    values = []
    for row in rows:
        item = {}
        for key, value in row.items():
            item[key] = (
                json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
            )
        values.append(item)
    pq.write_table(pa.Table.from_pylist(values), path, compression="zstd")


def _read_rows(path: Path) -> list[dict[str, Any]]:
    rows = pq.read_table(path).to_pylist()
    for row in rows:
        for key in JSON_COLUMNS | {
            "contradicted_by",
            "member_ids",
            "shared_pathways",
            "layer_edge_ids",
        }:
            value = row.get(key)
            if isinstance(value, str):
                try:
                    row[key] = json.loads(value)
                except json.JSONDecodeError:
                    pass
    return rows


def write_snapshot(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
) -> None:
    SNAPSHOT.mkdir(parents=True, exist_ok=True)
    _write_rows(SNAPSHOT / "nodes.parquet", nodes)
    _write_rows(SNAPSHOT / "edges.parquet", edges)
    _write_rows(SNAPSHOT / "ledger.parquet", edges)
    _write_rows(SNAPSHOT / "clusters.parquet", clusters)


class Snapshot:
    def __init__(self, snapshot_dir: Path = SNAPSHOT) -> None:
        self.snapshot_dir = snapshot_dir
        self.nodes = _read_rows(snapshot_dir / "nodes.parquet")
        self.edges = _read_rows(snapshot_dir / "edges.parquet")
        self.clusters = _read_rows(snapshot_dir / "clusters.parquet")
        self.node_by_id = {node["id"]: node for node in self.nodes}
        self.edge_by_id = {edge["edge_id"]: edge for edge in self.edges}
        self.edges_by_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.edges_by_object: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in self.edges:
            self.edges_by_subject[edge["subject"]].append(edge)
            self.edges_by_object[edge["object"]].append(edge)
        digest = hashlib.sha256()
        for name in ("nodes.parquet", "edges.parquet", "clusters.parquet"):
            digest.update((snapshot_dir / name).read_bytes())
        self.snapshot_hash = digest.hexdigest()

    def cluster_for(self, disease_id: str) -> dict[str, Any] | None:
        return next(
            (row for row in self.clusters if disease_id in row.get("member_ids", [])),
            None,
        )

    def disease_ref(self, disease_id: str) -> dict[str, Any] | None:
        disease = self.node_by_id.get(disease_id)
        if not disease:
            return None
        props = disease.get("properties", {})
        return {
            "id": disease_id,
            "name": disease["label"],
            "gene_id": props.get("gene_id", ""),
            "gene_symbol": props.get("gene_symbol", ""),
        }
