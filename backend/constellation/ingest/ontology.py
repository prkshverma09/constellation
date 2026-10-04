from __future__ import annotations

import gzip
import math
import re
from collections import Counter, defaultdict
from io import BytesIO
from pathlib import Path
from typing import Any

from constellation.ingest.cache import CachedHTTP

GO_GAF_URL = "https://current.geneontology.org/annotations/goa_human.gaf.gz"
GO_OBO_URL = "https://current.geneontology.org/ontology/go-basic.obo"
REACTOME_ALL_LEVELS_URL = "https://reactome.org/download/current/UniProt2Reactome_All_Levels.txt"
EXPERIMENTAL_GO_EVIDENCE = {
    "EXP",
    "IDA",
    "IPI",
    "IMP",
    "IGI",
    "IEP",
    "HTP",
    "HDA",
    "HMP",
    "HGI",
    "HEP",
}


def parse_hpo_information(
    hpoa_content: str,
    ontology: dict[str, Any],
) -> tuple[dict[str, float], dict[str, list[str]], dict[str, str], dict[str, list[str]]]:
    graphs = ontology.get("graphs", [])
    graph = graphs[0] if graphs and isinstance(graphs[0], dict) else {}
    parents: dict[str, set[str]] = defaultdict(set)
    labels: dict[str, str] = {}
    synonyms: dict[str, set[str]] = defaultdict(set)

    for edge in graph.get("edges", []):
        if edge.get("pred") == "is_a":
            subject = str(edge.get("sub") or "")
            parent = str(edge.get("obj") or "")
            if subject.startswith("HP:") and parent.startswith("HP:"):
                parents[subject].add(parent)

    for node in graph.get("nodes", []):
        term_id = str(node.get("id") or "")
        if not term_id.startswith("HP:"):
            continue
        parents.setdefault(term_id, set())
        parents[term_id].update(
            value
            for value in node.get("is_a", [])
            if isinstance(value, str) and value.startswith("HP:")
        )
        label = node.get("lbl") or node.get("label")
        if label:
            labels[term_id] = str(label)
        meta = node.get("meta", {})
        for item in meta.get("synonyms", []) if isinstance(meta, dict) else []:
            synonym = item.get("val") if isinstance(item, dict) else item
            if synonym:
                synonyms[term_id].add(str(synonym))
        for item in node.get("synonyms", []):
            synonym = item.get("val") if isinstance(item, dict) else item
            if synonym:
                synonyms[term_id].add(str(synonym))

    closure_cache: dict[str, set[str]] = {}

    def ancestors(term_id: str, visiting: frozenset[str] = frozenset()) -> set[str]:
        if term_id in closure_cache:
            return closure_cache[term_id]
        if term_id in visiting:
            return {term_id}
        result = {term_id}
        for parent in parents.get(term_id, set()):
            result.update(ancestors(parent, visiting | {term_id}))
        closure_cache[term_id] = result
        return result

    diseases_by_term: dict[str, set[str]] = defaultdict(set)
    diseases: set[str] = set()
    for line in hpoa_content.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < 4 or not fields[0] or not fields[3].startswith("HP:"):
            continue
        if "NOT" in fields[2].split("|"):
            continue
        disease_id, term_id = fields[0], fields[3]
        diseases.add(disease_id)
        diseases_by_term[term_id].add(disease_id)

    propagated_diseases: dict[str, set[str]] = defaultdict(set)
    for term_id, disease_ids in diseases_by_term.items():
        for ancestor in ancestors(term_id):
            propagated_diseases[ancestor].update(disease_ids)

    total = len(diseases)
    information_content = {
        term_id: -math.log(len(disease_ids) / total)
        for term_id, disease_ids in propagated_diseases.items()
        if total and disease_ids
    }
    return (
        information_content,
        {term_id: sorted(values) for term_id, values in parents.items()},
        labels,
        {term_id: sorted(values) for term_id, values in synonyms.items()},
    )


def parse_go_basic_obo(content: bytes) -> tuple[dict[str, str], dict[str, set[str]]]:
    names: dict[str, str] = {}
    parents: dict[str, set[str]] = defaultdict(set)
    term_id: str | None = None
    for line in content.decode("utf-8").splitlines():
        if line == "[Term]":
            term_id = None
        elif line.startswith("["):
            term_id = None
        elif line.startswith("id: GO:"):
            term_id = line.removeprefix("id: ").strip()
        elif term_id and line.startswith("name: "):
            names[term_id] = line.removeprefix("name: ").strip()
        elif term_id and line.startswith("is_a: GO:"):
            parent = line.removeprefix("is_a: ").split()[0]
            parents[term_id].add(parent)
        elif term_id and line.startswith("relationship: part_of GO:"):
            parent = line.removeprefix("relationship: part_of ").split()[0]
            parents[term_id].add(parent)
    return names, dict(parents)


def _ancestor_closure(
    term_id: str,
    parents: dict[str, set[str]],
    cache: dict[str, set[str]],
    visiting: frozenset[str] = frozenset(),
) -> set[str]:
    if term_id in cache:
        return cache[term_id]
    if term_id in visiting:
        return {term_id}
    ancestors = {term_id}
    for parent in parents.get(term_id, set()):
        ancestors.update(_ancestor_closure(parent, parents, cache, visiting | {term_id}))
    cache[term_id] = ancestors
    return ancestors


def propagate_go_terms(term_ids: set[str], parents: dict[str, set[str]]) -> set[str]:
    cache: dict[str, set[str]] = {}
    propagated: set[str] = set()
    for term_id in term_ids:
        propagated.update(_ancestor_closure(term_id, parents, cache))
    return propagated


def parse_goa_human_gaf(
    compressed_gaf: bytes,
    parents: dict[str, set[str]],
    slice_symbols: set[str],
) -> dict[str, Any]:
    all_terms_by_gene: dict[str, set[str]] = defaultdict(set)
    experimental_annotations_by_gene: dict[str, list[dict[str, str]]] = defaultdict(list)
    uniprot_to_symbol: dict[str, str] = {}
    accepted_rows = 0
    experimental_slice_rows = 0
    ancestor_cache: dict[str, set[str]] = {}

    with gzip.open(BytesIO(compressed_gaf), "rt", encoding="utf-8") as gaf:
        for line in gaf:
            if line.startswith("!") or not line.strip():
                continue
            fields = line.rstrip("\r\n").split("\t")
            if len(fields) < 15:
                continue
            if "taxon:9606" not in fields[12].split("|"):
                continue
            symbol = fields[2].strip()
            if fields[0] == "UniProtKB" and fields[1] and symbol:
                uniprot_to_symbol[fields[1]] = symbol
            if fields[8] != "P" or "NOT" in fields[3].split("|"):
                continue
            go_id = fields[4].strip()
            if not symbol or not re.fullmatch(r"GO:\d{7}", go_id):
                continue
            accepted_rows += 1
            all_terms_by_gene[symbol].add(go_id)
            if symbol not in slice_symbols or fields[6] not in EXPERIMENTAL_GO_EVIDENCE:
                continue
            experimental_slice_rows += 1
            experimental_annotations_by_gene[symbol].append(
                {
                    "go_id": go_id,
                    "reference": fields[5],
                    "evidence_code": fields[6],
                    "protein_id": fields[1],
                    "symbol": symbol,
                    "assigned_by": fields[14],
                }
            )

    gene_counts: Counter[str] = Counter()
    for gene_terms in all_terms_by_gene.values():
        propagated: set[str] = set()
        for term_id in gene_terms:
            propagated.update(_ancestor_closure(term_id, parents, ancestor_cache))
        gene_counts.update(propagated)

    return {
        "annotations_by_gene": dict(experimental_annotations_by_gene),
        "gene_counts": dict(gene_counts),
        "uniprot_to_symbol": uniprot_to_symbol,
        "annotation_rows": accepted_rows,
        "experimental_slice_rows": experimental_slice_rows,
    }


def load_goa_human_data(
    http: CachedHTTP,
    slice_symbols: set[str],
) -> dict[str, Any]:
    gaf = http.get_bytes(
        GO_GAF_URL,
        local_fallback=Path.home() / "go" / "goa_human.gaf.gz",
    )
    obo = http.get_bytes(
        GO_OBO_URL,
        local_fallback=Path.home() / "go" / "go-basic.obo",
    )
    names, parents = parse_go_basic_obo(obo)
    annotations = parse_goa_human_gaf(gaf, parents, slice_symbols)
    return {"term_names": names, "parents": parents, **annotations}


def parse_reactome_all_levels(
    content: bytes,
    uniprot_to_symbol: dict[str, str],
) -> dict[str, Any]:
    pathways_by_gene: dict[str, set[str]] = defaultdict(set)
    members_by_pathway: dict[str, set[str]] = defaultdict(set)
    labels: dict[str, str] = {}
    row_count = 0
    for line in content.decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 6 or not fields[5].casefold().startswith("homo sapiens"):
            continue
        protein_id = fields[0].removeprefix("UniProtKB:").strip()
        pathway_id = fields[1].strip()
        pathway_name = fields[3].strip()
        if not protein_id or not pathway_id.startswith("R-HSA-"):
            continue
        row_count += 1
        primary_accession = protein_id.split("-", 1)[0]
        symbol = uniprot_to_symbol.get(protein_id) or uniprot_to_symbol.get(primary_accession)
        gene_key = symbol or primary_accession
        members_by_pathway[pathway_id].add(gene_key)
        if symbol:
            pathways_by_gene[symbol].add(pathway_id)
        if pathway_name:
            labels[pathway_id] = pathway_name
    return {
        "pathways_by_gene": dict(pathways_by_gene),
        "gene_counts": {
            pathway_id: len(genes) for pathway_id, genes in members_by_pathway.items()
        },
        "term_names": labels,
        "mapping_rows": row_count,
    }


def load_reactome_all_levels(
    http: CachedHTTP,
    uniprot_to_symbol: dict[str, str],
) -> dict[str, Any]:
    content = http.get_bytes(REACTOME_ALL_LEVELS_URL)
    return parse_reactome_all_levels(content, uniprot_to_symbol)
