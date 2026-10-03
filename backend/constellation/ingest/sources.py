from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Iterable
from typing import Any

from constellation.ingest.cache import CachedHTTP

MONARCH = "https://api.monarchinitiative.org/v3/api"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
CTGOV = "https://clinicaltrials.gov/api/v2"


def monarch_search(http: CachedHTTP, query: str, category: str) -> list[dict[str, Any]]:
    payload = http.get(f"{MONARCH}/search", params={"q": query, "category": category, "limit": 20})
    return payload.get("items", [])


def monarch_entity(http: CachedHTTP, identifier: str) -> dict[str, Any] | None:
    try:
        return http.get(f"{MONARCH}/entity/{identifier}")
    except Exception:
        return None


def monarch_associations(
    http: CachedHTTP,
    subject: str,
    category: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"subject": subject, "limit": 100}
    if category:
        params["category"] = category
    try:
        return http.get(f"{MONARCH}/association", params=params).get("items", [])
    except Exception:
        return []


def resolve_gene(http: CachedHTTP, symbol: str) -> dict[str, Any] | None:
    results = monarch_search(http, symbol, "biolink:Gene")
    exact = [
        row
        for row in results
        if (row.get("symbol") or "").casefold() == symbol.casefold()
        or (row.get("name") or "").casefold() == symbol.casefold()
    ]
    if not exact:
        return None
    return monarch_entity(http, exact[0]["id"]) or exact[0]


def pubmed_records(http: CachedHTTP, symbol: str, retmax: int = 60) -> list[dict[str, Any]]:
    term = (
        f'("{symbol}"[Title/Abstract]) AND '
        "(epilepsy OR encephalopathy OR seizure OR neurodevelopment)"
    )
    search = http.get(
        f"{EUTILS}/esearch.fcgi",
        params={
            "db": "pubmed",
            "term": term,
            "retmax": retmax,
            "retmode": "json",
            "sort": "relevance",
        },
        ncbi=True,
    )
    ids = search.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    payload = http.get(
        f"{EUTILS}/efetch.fcgi",
        params={"db": "pubmed", "id": ",".join(ids), "retmode": "xml"},
        ncbi=True,
    )
    xml = payload.get("_text", "")
    root = ET.fromstring(xml)
    records: list[dict[str, Any]] = []
    for article in root.findall(".//PubmedArticle"):
        pmid = article.findtext(".//PMID")
        article_title = article.find(".//ArticleTitle")
        title = (
            " ".join("".join(article_title.itertext()).split()) if article_title is not None else ""
        )
        abstract = " ".join(
            " ".join("".join(node.itertext()).split())
            for node in article.findall(".//Abstract/AbstractText")
        )
        if not pmid:
            continue
        year = (
            article.findtext(".//PubDate/Year")
            or article.findtext(".//PubDate/MedlineDate", "")[:4]
        )
        journal = article.findtext(".//Journal/Title") or ""
        doi = next(
            (
                item.text
                for item in article.findall(".//ArticleId")
                if item.attrib.get("IdType") == "doi" and item.text
            ),
            None,
        )
        authors = []
        for author in article.findall(".//AuthorList/Author"):
            last = author.findtext("LastName", "")
            fore = author.findtext("ForeName", "")
            collective = author.findtext("CollectiveName", "")
            name = collective or " ".join(part for part in (fore, last) if part)
            affiliations = [
                " ".join("".join(node.itertext()).split())
                for node in author.findall(".//Affiliation")
                if "".join(node.itertext()).strip()
            ]
            if name:
                authors.append({"name": name, "affiliations": affiliations})
        records.append(
            {
                "id": f"PMID:{pmid}",
                "pmid": pmid,
                "title": title,
                "abstract": abstract,
                "year": year,
                "journal": journal,
                "doi": doi,
                "authors": authors,
            }
        )
    return records


def pubmed_count(http: CachedHTTP, symbol: str) -> int:
    term = (
        f'("{symbol}"[Title/Abstract]) AND '
        "(epilepsy OR encephalopathy OR seizure OR neurodevelopment)"
    )
    search = http.get(
        f"{EUTILS}/esearch.fcgi",
        params={"db": "pubmed", "term": term, "retmax": 0, "retmode": "json"},
        ncbi=True,
    )
    return int(search.get("esearchresult", {}).get("count", 0))


def clinvar_pathogenic_count(http: CachedHTTP, symbol: str) -> tuple[int, str | None]:
    payload = http.get(
        f"{EUTILS}/esearch.fcgi",
        params={
            "db": "clinvar",
            "term": (
                f"{symbol}[gene] AND "
                "(pathogenic[clinical significance] OR likely pathogenic[clinical significance])"
            ),
            "retmax": 1,
            "retmode": "json",
        },
        ncbi=True,
    )
    result = payload.get("esearchresult", {})
    ids = result.get("idlist", [])
    return int(result.get("count", 0)), ids[0] if ids else None


def trial_by_nct(http: CachedHTTP, nct_id: str) -> dict[str, Any] | None:
    try:
        return http.get(f"{CTGOV}/studies/{nct_id}")
    except Exception:
        return None


def search_trials(http: CachedHTTP, query: str, page_size: int = 100) -> list[dict[str, Any]]:
    return search_trials_with_count(http, query, page_size)[0]


def search_trials_with_count(
    http: CachedHTTP, query: str, page_size: int = 100
) -> tuple[list[dict[str, Any]], int]:
    try:
        payload = http.get(
            f"{CTGOV}/studies",
            params={"query.cond": query, "pageSize": page_size, "format": "json"},
        )
        studies = payload.get("studies", [])
        return studies, int(payload.get("totalCount", len(studies)))
    except Exception:
        return [], 0


def string_network(http: CachedHTTP, symbols: Iterable[str]) -> list[dict[str, Any]]:
    identifiers = "\r".join(symbols)
    try:
        result = http.get(
            "https://string-db.org/api/json/network",
            params={"identifiers": identifiers, "species": 9606, "required_score": 700},
        )
        return result if isinstance(result, list) else []
    except Exception:
        return []


def reactome_pathway_parents(http: CachedHTTP) -> dict[str, set[str]]:
    try:
        hierarchy = http.get("https://reactome.org/ContentService/data/eventsHierarchy/9606")
    except Exception:
        return {}
    parents: dict[str, set[str]] = {}

    def visit(node: dict[str, Any], parent_id: str | None = None) -> None:
        pathway_id = node.get("stId")
        if pathway_id and parent_id:
            parents.setdefault(pathway_id, set()).add(parent_id)
        for child in node.get("children", []):
            visit(child, pathway_id)

    for root in hierarchy if isinstance(hierarchy, list) else []:
        visit(root)
    return parents


def reactome_pathways(http: CachedHTTP, uniprot_id: str) -> list[dict[str, Any]]:
    try:
        data = http.get(
            f"https://reactome.org/ContentService/data/mapping/UniProt/{uniprot_id}/pathways",
            params={"species": "Homo sapiens"},
        )
        return data if isinstance(data, list) else []
    except Exception:
        return []


def uniprot_accession(http: CachedHTTP, symbol: str) -> str | None:
    try:
        payload = http.get(
            "https://rest.uniprot.org/uniprotkb/search",
            params={
                "query": f"gene_exact:{symbol} AND organism_id:9606",
                "fields": "accession,gene_names",
                "format": "json",
                "size": 1,
            },
        )
        results = payload.get("results", [])
        return results[0]["primaryAccession"] if results else None
    except Exception:
        return None


def reporter_projects(http: CachedHTTP, symbol: str) -> list[dict[str, Any]]:
    try:
        payload = http.post(
            "https://api.reporter.nih.gov/v2/projects/search",
            json_body={
                "criteria": {
                    "advanced_text_search": {
                        "operator": "AND",
                        "search_field": "all",
                        "search_text": symbol,
                    }
                },
                "include_fields": [
                    "ProjectNum",
                    "ProjectTitle",
                    "FiscalYear",
                    "ContactPiName",
                    "Organization",
                    "ProjectStartDate",
                    "ProjectEndDate",
                    "AwardAmount",
                    "Terms",
                ],
                "offset": 0,
                "limit": 50,
            },
        )
        return payload.get("results", [])
    except Exception:
        return []


def reporter_publications(http: CachedHTTP, project_num: str) -> list[dict[str, Any]]:
    try:
        payload = http.post(
            "https://api.reporter.nih.gov/v2/publications/search",
            json_body={
                "criteria": {"project_nums": [project_num]},
                "include_fields": [
                    "ProjectNum",
                    "PMID",
                    "PublicationTitle",
                    "JournalTitle",
                    "Year",
                ],
                "offset": 0,
                "limit": 50,
            },
        )
        return payload.get("results", [])
    except Exception:
        return []


def monarch_semsim(http: CachedHTTP, phenotype_ids: list[str]) -> list[dict[str, Any]]:
    if not phenotype_ids:
        return []
    try:
        result = http.post(
            f"{MONARCH}/semsim/search",
            json_body={
                "termset": phenotype_ids,
                "group": "Human Diseases",
                "metric": "ancestor_information_content",
                "limit": 50,
            },
        )
        if isinstance(result, list):
            return result
        return result.get("items", []) if isinstance(result, dict) else []
    except Exception:
        return []


def monarch_semsim_multicompare(
    http: CachedHTTP, phenotype_ids: list[str], object_sets: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not phenotype_ids or not object_sets:
        return []
    try:
        result = http.post(
            f"{MONARCH}/semsim/multicompare",
            json_body={
                "subjects": phenotype_ids,
                "object_sets": object_sets,
                "metric": "ancestor_information_content",
            },
        )
        return result if isinstance(result, list) else []
    except Exception:
        return []


def hpo_information(http: CachedHTTP) -> tuple[dict[str, float], dict[str, list[str]]]:
    information_content: dict[str, float] = {}
    parents: dict[str, list[str]] = {}
    try:
        hpoa = http.get(
            "https://raw.githubusercontent.com/obophenotype/human-phenotype-ontology/master/src/ontology/phenotype.hpoa"
        ).get("_text", "")
        counts: Counter[str] = Counter()
        diseases: set[str] = set()
        for line in hpoa.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            columns = line.split("\t")
            if len(columns) < 4 or not columns[3].startswith("HP:"):
                continue
            diseases.add(columns[0])
            counts[columns[3]] += 1
        total = max(len(diseases), 1)
        information_content = {
            term_id: -math.log2(count / total) for term_id, count in counts.items() if count > 0
        }
    except Exception:
        pass
    try:
        ontology = http.get(
            "https://raw.githubusercontent.com/obophenotype/human-phenotype-ontology/master/src/ontology/hp.json"
        )
        graph = ontology.get("graphs", [{}])[0]
        for edge in graph.get("edges", []):
            if edge.get("pred") == "is_a":
                parents.setdefault(edge.get("sub", ""), []).append(edge.get("obj", ""))
        for node in graph.get("nodes", []):
            if node.get("id", "").startswith("HP:"):
                parents.setdefault(node["id"], [])
                parents[node["id"]].extend(
                    value for value in node.get("is_a", []) if value.startswith("HP:")
                )
    except Exception:
        pass
    return information_content, parents


def extract_uniprot(entity: dict[str, Any]) -> str | None:
    values: list[Any] = []
    for key in ("xref", "equivalent_identifiers", "same_as"):
        item = entity.get(key)
        if isinstance(item, list):
            values.extend(item)
        elif item:
            values.append(item)
    for value in values:
        match = re.search(
            r"(?:UniProtKB:)?([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z0-9]{3}[0-9]){1,2})",
            str(value),
        )
        if match:
            return match.group(1)
    return None
