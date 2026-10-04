from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any

import igraph as ig

from constellation.graph.store import Snapshot

NAME_PREFIXES = {"dr", "doctor", "prof", "professor"}
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "md", "do", "phd"}
AFFILIATION_STOPWORDS = {
    "address",
    "and",
    "article",
    "author",
    "authors",
    "affiliation",
    "affiliations",
    "center",
    "centre",
    "clinic",
    "college",
    "department",
    "division",
    "faculty",
    "for",
    "hospital",
    "institute",
    "laboratory",
    "lab",
    "listed",
    "mail",
    "medical",
    "medicine",
    "of",
    "provided",
    "research",
    "school",
    "the",
    "university",
}
ROLE_NAMES = {
    "authored": "author",
    "funds": "pi",
    "investigates": "study_official",
}


def _diseases_linked_to_record(snapshot: Snapshot, record_id: str) -> set[str]:
    paper_genes = {
        edge["object"]
        for edge in snapshot.edges_by_subject.get(record_id, [])
        if edge["predicate"] == "mentions_gene"
    }
    study_genes = {
        edge["object"]
        for edge in snapshot.edges_by_subject.get(record_id, [])
        if edge["predicate"] == "studies_gene"
    }
    genes = paper_genes | study_genes
    return {
        edge["object"]
        for gene_id in genes
        for edge in snapshot.edges_by_subject.get(gene_id, [])
        if edge["predicate"] == "causes"
    }


def _name_tokens(name: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    normalized = normalized.casefold()
    tokens = re.findall(r"[a-z0-9]+", normalized)
    if "," in normalized:
        surname, given = normalized.split(",", 1)
        surname_tokens = [
            token for token in re.findall(r"[a-z0-9]+", surname) if token not in {"md", "do", "phd"}
        ]
        given_tokens = [
            token for token in re.findall(r"[a-z0-9]+", given) if token not in {"md", "do", "phd"}
        ]
        if surname_tokens and given_tokens:
            tokens = [*given_tokens, *surname_tokens]
    tokens = [token for token in tokens if token not in {"md", "do", "phd"}]
    while tokens and tokens[0] in NAME_PREFIXES:
        tokens.pop(0)
    while tokens and tokens[-1] in NAME_SUFFIXES:
        tokens.pop()
    return tuple(tokens)


def _display_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    raw_tokens = re.findall(r"[a-z0-9]+", normalized.casefold())
    if "," not in name and not any(token in NAME_SUFFIXES for token in raw_tokens):
        return name.strip()
    return " ".join(
        token.upper() if len(token) == 1 else token.capitalize()
        for token in _name_tokens(name)
    )


def _names_compatible(name_a: str, name_b: str) -> bool:
    tokens_a = _name_tokens(name_a)
    tokens_b = _name_tokens(name_b)
    if len(tokens_a) < 2 or len(tokens_b) < 2 or tokens_a[-1] != tokens_b[-1]:
        return False
    given_a, given_b = tokens_a[:-1], tokens_b[:-1]
    for token_a, token_b in zip(given_a, given_b, strict=False):
        if token_a == token_b:
            continue
        if len(token_a) == 1 and token_a == token_b[0]:
            continue
        if len(token_b) == 1 and token_b == token_a[0]:
            continue
        return False
    return True


def _canonical_name(name_a: str, name_b: str) -> str:
    tokens_a = _name_tokens(name_a)
    tokens_b = _name_tokens(name_b)
    given_a, given_b = tokens_a[:-1], tokens_b[:-1]
    given = [
        token_a
        if len(token_a) >= len(token_b)
        else token_b
        for token_a, token_b in zip(given_a, given_b, strict=False)
    ]
    given.extend(given_a[len(given) :] or given_b[len(given) :])
    return " ".join([*given, tokens_a[-1]])


def _canonical_group_key(name: str) -> str:
    tokens = _name_tokens(name)
    if len(tokens) < 2:
        return " ".join(tokens)
    return " ".join([tokens[0], *(token[0] for token in tokens[1:-1]), tokens[-1]])


def _orcid(value: Any) -> str:
    return re.sub(r"[^0-9Xx]", "", str(value or "")).upper()


def _year(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _affiliations(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _affiliation_tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return {
        token
        for token in re.findall(r"[a-z0-9]+", normalized.casefold())
        if len(token) >= 4 and token not in AFFILIATION_STOPWORDS
    }


def _edge_affiliations(edge: dict[str, Any]) -> list[str]:
    return _affiliations(edge.get("properties", {}).get("affiliations"))


def _side_edges(records: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    unique = {edge["edge_id"]: edge for rows in records.values() for edge in rows}
    return list(unique.values())


def _shared_coauthor_signal(
    authors_by_record: dict[str, list[tuple[str, str]]],
    records_a: set[str],
    records_b: set[str],
    candidate_name: str,
) -> str | None:
    for record_a in sorted(records_a):
        for record_b in sorted(records_b):
            for _, name_a in authors_by_record.get(record_a, []):
                if _names_compatible(name_a, candidate_name):
                    continue
                for _, name_b in authors_by_record.get(record_b, []):
                    if _names_compatible(name_b, candidate_name):
                        continue
                    if _names_compatible(name_a, name_b):
                        return (
                            f"shared co-author {name_a!r} on {record_a} and {record_b}"
                        )
    return None


def _corroborating_signals(
    edges_a: list[dict[str, Any]],
    edges_b: list[dict[str, Any]],
    records_a: set[str],
    records_b: set[str],
    candidate_name: str,
    authors_by_record: dict[str, list[tuple[str, str]]],
) -> list[str]:
    signals: set[str] = set()
    orcids_a = {
        _orcid(edge.get("properties", {}).get("orcid"))
        for edge in edges_a
        if edge.get("properties", {}).get("orcid")
    }
    orcids_b = {
        _orcid(edge.get("properties", {}).get("orcid"))
        for edge in edges_b
        if edge.get("properties", {}).get("orcid")
    }
    for identifier in orcids_a & orcids_b:
        signals.add(f"matching ORCID {identifier}")

    affiliations_a = {
        affiliation for edge in edges_a for affiliation in _edge_affiliations(edge)
    }
    affiliations_b = {
        affiliation for edge in edges_b for affiliation in _edge_affiliations(edge)
    }
    for affiliation_a in affiliations_a:
        for affiliation_b in affiliations_b:
            overlap = _affiliation_tokens(affiliation_a) & _affiliation_tokens(
                affiliation_b
            )
            if overlap:
                tokens = ", ".join(sorted(overlap))
                signals.add(
                    f"overlapping affiliation tokens [{tokens}] in "
                    f"{affiliation_a!r} and {affiliation_b!r}"
                )
                role_edges = [
                    edge
                    for edge in [*edges_a, *edges_b]
                    if edge["predicate"] in {"investigates", "funds"}
                    or edge.get("properties", {}).get("role")
                    in {"pi", "study_official"}
                ]
                if role_edges:
                    sources = sorted(
                        {
                            "CT.gov official"
                            if edge["source"] == "ctgov"
                            else "RePORTER PI"
                            if edge["source"] == "reporter"
                            else "study official/PI"
                            for edge in role_edges
                        }
                    )
                    signals.add(
                        f"{'/'.join(sources)} affiliation match "
                        f"{affiliation_a!r} / {affiliation_b!r}"
                    )
    coauthor = _shared_coauthor_signal(
        authors_by_record, records_a, records_b, candidate_name
    )
    if coauthor:
        signals.add(coauthor)
    return sorted(signals)


def _person_evidence(
    snapshot: Snapshot,
    disease_a: str,
    disease_b: str,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, list[tuple[str, str]]],
    dict[str, set[str]],
]:
    people: dict[str, dict[str, Any]] = {}
    authors_by_record: dict[str, list[tuple[str, str]]] = defaultdict(list)
    disease_cache: dict[str, set[str]] = {}
    for edge in snapshot.edges:
        if edge["predicate"] == "authored":
            person = snapshot.node_by_id.get(edge["subject"], {})
            authors_by_record[edge["object"]].append(
                (edge["subject"], str(person.get("label") or edge["subject_label"]))
            )
        if edge["predicate"] not in {"authored", "investigates", "funds"}:
            continue
        person_id = edge["subject"]
        person = snapshot.node_by_id.get(person_id, {})
        record_id = edge["object"]
        if record_id not in disease_cache:
            disease_cache[record_id] = _diseases_linked_to_record(snapshot, record_id)
        linked_diseases = disease_cache[record_id] & {disease_a, disease_b}
        if not linked_diseases:
            continue
        evidence = people.setdefault(
            person_id,
            {
                "name": str(person.get("label") or edge["subject_label"]),
                "records": {"a": defaultdict(list), "b": defaultdict(list)},
                "affiliations": _affiliations(
                    person.get("properties", {}).get("affiliations")
                ),
            },
        )
        for side, disease_id in (("a", disease_a), ("b", disease_b)):
            if disease_id in linked_diseases:
                evidence["records"][side][record_id].append(edge)
    return people, authors_by_record, disease_cache


def find_bridges_with_unverified(
    snapshot: Snapshot,
    disease_a: str,
    disease_b: str,
) -> tuple[list[dict[str, Any]], int]:
    people_by_id, authors_by_record, disease_cache = _person_evidence(
        snapshot, disease_a, disease_b
    )
    left_ids = sorted(
        person_id
        for person_id, evidence in people_by_id.items()
        if evidence["records"]["a"]
    )
    right_ids = sorted(
        person_id
        for person_id, evidence in people_by_id.items()
        if evidence["records"]["b"]
    )
    grouped: dict[str, dict[str, Any]] = {}
    unverified: set[str] = set()
    for person_a_id in left_ids:
        evidence_a = people_by_id[person_a_id]
        records_a = set(evidence_a["records"]["a"])
        edges_a = _side_edges(evidence_a["records"]["a"])
        for person_b_id in right_ids:
            evidence_b = people_by_id[person_b_id]
            if not _names_compatible(evidence_a["name"], evidence_b["name"]):
                continue
            records_b = set(evidence_b["records"]["b"])
            edges_b = _side_edges(evidence_b["records"]["b"])
            canonical_name = _canonical_name(
                evidence_a["name"], evidence_b["name"]
            )
            canonical_key = _canonical_group_key(canonical_name)
            signals = _corroborating_signals(
                edges_a,
                edges_b,
                records_a,
                records_b,
                canonical_name,
                authors_by_record,
            )
            if not signals:
                unverified.add(canonical_key)
                continue
            bridge = grouped.setdefault(
                canonical_key,
                {
                    "id": person_a_id,
                    "names": set(),
                    "records_a": set(),
                    "records_b": set(),
                    "proofs": {},
                    "affiliations": set(),
                    "roles": set(),
                    "signals": set(),
                    "graph_edges": set(),
                },
            )
            bridge["names"].update({evidence_a["name"], evidence_b["name"]})
            bridge["records_a"].update(records_a)
            bridge["records_b"].update(records_b)
            bridge["affiliations"].update(
                evidence_a["affiliations"] + evidence_b["affiliations"]
            )
            bridge["signals"].update(signals)
            bridge["graph_edges"].update(
                (bridge["id"], record_id) for record_id in records_a | records_b
            )
            for side_edges in (edges_a, edges_b):
                for edge in side_edges:
                    role = ROLE_NAMES.get(edge["predicate"])
                    if role:
                        bridge["roles"].add(role)
                    bridge["affiliations"].update(_edge_affiliations(edge))
                    linked = disease_cache.get(edge["object"], set())
                    proof = bridge["proofs"].setdefault(
                        edge["edge_id"], {"edge": edge, "disease_ids": set()}
                    )
                    proof["disease_ids"].update(
                        linked & {disease_a, disease_b}
                    )
                    bridge["graph_edges"].update(
                        (edge["object"], disease_id)
                        for disease_id in linked & {disease_a, disease_b}
                    )

    rows: list[dict[str, Any]] = []
    graph_edges: set[tuple[str, str]] = set()
    for canonical_name, bridge in grouped.items():
        graph_edges.update(bridge["graph_edges"])
        proof_rows = []
        recency: list[int] = []
        for edge_id, proof in sorted(bridge["proofs"].items()):
            edge = proof["edge"]
            record_id = edge["object"]
            record = snapshot.node_by_id.get(record_id, {})
            properties = record.get("properties", {})
            year = properties.get("year") or properties.get("fiscal_year")
            if year:
                recency.append(_year(year))
            linked = proof["disease_ids"]
            disease_ids = sorted(
                linked, key=lambda identifier: (identifier != disease_a, identifier)
            )
            proof_rows.append(
                {
                    "edge_id": edge_id,
                    "kind": "paper"
                    if record_id.startswith("PMID:")
                    else "award"
                    if record_id.startswith("NIH:")
                    else "study",
                    "record_id": record_id,
                    "title": record.get("label", record_id),
                    "year": year,
                    "url": edge.get("source_url") or "",
                    "disease_id": disease_ids[0] if disease_ids else disease_a,
                    "disease_ids": disease_ids,
                }
            )
        names = sorted(
            bridge["names"],
            key=lambda name: (
                sum(len(token) > 1 for token in _name_tokens(name)),
                len(name),
            ),
            reverse=True,
        )
        display_name = _display_name(names[0]) if names else canonical_name
        roles = sorted(bridge["roles"])
        row = {
            "id": bridge["id"],
            "display_name": display_name,
            "affiliations": sorted(bridge["affiliations"]),
            "roles": roles,
            "n_a": len(bridge["records_a"]),
            "n_b": len(bridge["records_b"]),
            "score": float(min(len(bridge["records_a"]), len(bridge["records_b"]))),
            "proving_edges": proof_rows,
            "why_same_person": "; ".join(sorted(bridge["signals"])),
            "betweenness": 0.0,
            "_recency": max(recency, default=0),
            "_role_priority": int(bool({"study_official", "pi"} & set(roles))),
        }
        rows.append(row)
    vertices = sorted({identifier for edge in graph_edges for identifier in edge})
    if vertices:
        index = {identifier: position for position, identifier in enumerate(vertices)}
        graph = ig.Graph(
            n=len(vertices),
            edges=[(index[a], index[b]) for a, b in graph_edges],
        )
        centrality = dict(zip(vertices, graph.betweenness(directed=False), strict=True))
    else:
        centrality = {}
    for row in rows:
        row["betweenness"] = centrality.get(row["id"], 0.0)
        row.pop("_recency", None)
        row.pop("_role_priority", None)
    rows.sort(
        key=lambda row: (
            min(row["n_a"], row["n_b"]),
            row["betweenness"],
            max(
                (
                    _year(
                        proof["year"]
                        or snapshot.node_by_id.get(proof["record_id"], {})
                        .get("properties", {})
                        .get("fiscal_year")
                    )
                    for proof in row["proving_edges"]
                ),
                default=0,
            ),
            int(bool({"study_official", "pi"} & set(row["roles"]))),
        ),
        reverse=True,
    )
    return rows, len(unverified - grouped.keys())


def find_bridges(snapshot: Snapshot, disease_a: str, disease_b: str) -> list[dict[str, Any]]:
    return find_bridges_with_unverified(snapshot, disease_a, disease_b)[0]
