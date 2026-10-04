from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date
from typing import Any

import httpx
import yaml

from constellation.analytics.clusters import cluster_records
from constellation.analytics.gaps import gap_reasons, is_gap
from constellation.analytics.similarity import (
    fused_similarity,
    lowest_level_pathways,
    pathway_depths,
    string_mechanism_score,
)
from constellation.config import CACHE, DATA, RAW, configured_mode
from constellation.extract.cache import extract_claims
from constellation.extract.heuristic import extract_heuristic
from constellation.graph.store import Snapshot, write_snapshot
from constellation.ingest.cache import CachedHTTP
from constellation.ingest.ontology import (
    load_goa_human_data,
    load_reactome_all_levels,
    propagate_go_terms,
)
from constellation.ingest.sources import (
    clinvar_pathogenic_count,
    hpo_information,
    monarch_associations,
    monarch_entity,
    monarch_semsim_multicompare,
    pubmed_count,
    pubmed_records,
    reactome_pathway_parents,
    reporter_projects,
    reporter_publications,
    resolve_gene,
    search_trials_with_count,
    string_network,
    trial_by_nct,
    uniprot_accession,
)
from constellation.ledger import (
    make_edge,
    person_curie,
    reject_edge,
    sanitize_affiliation,
    sanitize_affiliations,
    sanitize_snapshot_record,
    validate_edge,
)

TRIAL_SEED = "NCT06555965"
MECHANISM_CLASS_LABELS = {
    "haploinsufficiency": "loss_of_function",
    "loss_of_function": "loss_of_function",
    "dominant_negative": "dominant_negative",
    "gain_of_function": "gain_of_function",
    "SNARE_complex": "unknown",
    "synaptic_vesicle_cycle": "unknown",
    "presynaptic_release": "unknown",
    "missense": "unknown",
}


def add_node(
    nodes: dict[str, dict[str, Any]],
    identifier: str,
    node_type: str,
    label: str,
    properties: dict[str, Any] | None = None,
) -> None:
    if identifier not in nodes:
        nodes[identifier] = {
            "id": identifier,
            "type": node_type,
            "label": label,
            "properties": properties or {},
        }
    elif properties:
        nodes[identifier]["properties"].update(properties)


def _xrefs(entity: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for key in ("xref", "same_as", "equivalent_identifiers"):
        value = entity.get(key)
        if isinstance(value, list):
            result.extend(str(item) for item in value)
        elif value:
            result.append(str(value))
    return result


def _clinical_modules(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("protocolSection", {})


def _study_to_asset(
    study: dict[str, Any],
    genes_by_symbol: dict[str, dict[str, Any]],
    nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    hpo_labels: dict[str, str],
    hpo_synonyms: dict[str, list[str]],
    information_content: dict[str, float],
    phenotype_parents: dict[str, list[str]],
) -> None:
    modules = _clinical_modules(study)
    ident = modules.get("identificationModule", {})
    design = modules.get("designModule", {})
    eligibility = modules.get("eligibilityModule", {})
    outcomes = modules.get("outcomesModule", {})
    nct_id = ident.get("nctId")
    if not nct_id:
        return
    title = ident.get("briefTitle") or ident.get("officialTitle") or nct_id
    study_id = f"NCT:{nct_id}"
    add_node(
        nodes,
        study_id,
        "study",
        title,
        {
            "nct_id": nct_id,
            "study_type": design.get("studyType", ""),
            "status": modules.get("statusModule", {}).get("overallStatus", ""),
            "conditions": modules.get("conditionsModule", {}).get("conditions", []),
            "eligibility": eligibility.get("eligibilityCriteria", ""),
            "minimum_age": eligibility.get("minimumAge", ""),
            "maximum_age": eligibility.get("maximumAge", ""),
            "outcome_measures": [
                item.get("measure", "")
                for item in outcomes.get("primaryOutcomes", [])
                + outcomes.get("secondaryOutcomes", [])
            ],
        },
    )
    details = modules.get("contactsLocationsModule", {})
    officials = details.get("overallOfficials", [])
    for official in officials:
        name = official.get("name", "").strip()
        if not name:
            continue
        affiliation = sanitize_affiliation(official.get("affiliation", ""))
        identifier = person_curie(name, affiliation)
        orcid = official.get("orcid")
        add_node(
            nodes,
            identifier,
            "person",
            name,
            {
                "affiliations": [affiliation] if affiliation else [],
                "roles": ["study_official"],
                **({"orcid": orcid} if orcid else {}),
            },
        )
        edges.append(
            make_edge(
                identifier,
                name,
                "investigates",
                study_id,
                title,
                evidence_class="observed",
                source="ctgov",
                source_record=nct_id,
                source_url=f"https://clinicaltrials.gov/study/{nct_id}",
                properties={
                    "role": official.get("role", "study_official"),
                    "affiliations": [affiliation] if affiliation else [],
                    **({"orcid": orcid} if orcid else {}),
                },
            )
        )
    genenames = re.findall(r"\b[A-Z][A-Z0-9]{1,8}\b", title)
    related_diseases: set[str] = set()
    for symbol in genenames:
        gene = genes_by_symbol.get(symbol)
        if not gene:
            continue
        edges.append(
            make_edge(
                study_id,
                title,
                "studies_gene",
                gene["id"],
                symbol,
                evidence_class="observed",
                source="ctgov",
                source_record=nct_id,
                source_url=f"https://clinicaltrials.gov/study/{nct_id}",
            )
        )
        related_diseases.add(gene["disease_id"])
    is_asset = design.get("studyType") == "OBSERVATIONAL" and bool(
        re.search(r"natural history|registry|observational", title, re.I)
    )
    if not is_asset:
        return
    asset_id = f"constellation:asset/{nct_id.lower()}"
    outcome_measures = nodes[study_id]["properties"]["outcome_measures"]
    outcome_text = " ".join(outcome_measures).casefold()
    hpo_names: dict[str, set[str]] = defaultdict(set)
    for term_id, label in hpo_labels.items():
        hpo_names[term_id].add(label)
    for term_id, terms in hpo_synonyms.items():
        hpo_names[term_id].update(terms)
    for term_id, node in nodes.items():
        if node.get("type") == "phenotype" and node.get("label"):
            hpo_names[term_id].add(node["label"])
            hpo_names[term_id].update(node.get("properties", {}).get("synonyms", []))
    outcome_phenotypes = sorted(
        term_id
        for term_id, names in hpo_names.items()
        if any(
            re.search(rf"(?<!\w){re.escape(name.casefold())}(?!\w)", outcome_text)
            for name in names
            if name.strip()
        )
    )
    for phenotype_id in outcome_phenotypes:
        if phenotype_id not in nodes:
            add_node(
                nodes,
                phenotype_id,
                "phenotype",
                hpo_labels.get(phenotype_id, phenotype_id),
                {
                    "information_content": information_content.get(phenotype_id, 0.0),
                    "parents": phenotype_parents.get(phenotype_id, []),
                    "synonyms": hpo_synonyms.get(phenotype_id, []),
                },
            )
    served_phenotypes = {
        edge["object"]
        for edge in edges
        if edge["subject"] in related_diseases and edge["predicate"] == "has_phenotype"
    }
    phenotype_profile = sorted(served_phenotypes | set(outcome_phenotypes))
    enrollment_info = design.get("enrollmentInfo") or {}
    add_node(
        nodes,
        asset_id,
        "asset",
        title,
        {
            "asset_type": "natural_history_study"
            if re.search(r"natural history", title, re.I)
            else "registry",
            "record_id": nct_id,
            "url": f"https://clinicaltrials.gov/study/{nct_id}",
            "outcome_phenotypes": outcome_phenotypes,
            "phenotype_profile": phenotype_profile,
            "outcome_measures": outcome_measures,
            "eligibility": eligibility.get("eligibilityCriteria", ""),
            "minimum_age": eligibility.get("minimumAge", ""),
            "maximum_age": eligibility.get("maximumAge", ""),
            "study_type": design.get("studyType", ""),
            "overall_status": modules.get("statusModule", {}).get("overallStatus", ""),
            "enrollment": enrollment_info.get("count"),
            "n_locations": len(details.get("locations") or []),
            "variant_class": "unknown",
        },
    )
    edges.append(
        make_edge(
            asset_id,
            title,
            "derived_from",
            study_id,
            title,
            evidence_class="observed",
            source="ctgov",
            source_record=nct_id,
            source_url=f"https://clinicaltrials.gov/study/{nct_id}",
        )
    )
    for phenotype_id in outcome_phenotypes:
        edges.append(
            make_edge(
                asset_id,
                title,
                "measures_phenotype",
                phenotype_id,
                nodes[phenotype_id]["label"],
                evidence_class="inferred",
                source="ctgov",
                source_record=nct_id,
                source_url=f"https://clinicaltrials.gov/study/{nct_id}",
                method="exact HPO label/synonym match in registered outcome text",
            )
        )
    for disease_id in related_diseases:
        disease_label = nodes[disease_id]["label"]
        edges.append(
            make_edge(
                asset_id,
                title,
                "serves",
                disease_id,
                disease_label,
                evidence_class="observed",
                source="ctgov",
                source_record=nct_id,
                source_url=f"https://clinicaltrials.gov/study/{nct_id}",
            )
        )


def _add_seed_foundations(
    http: httpx.Client,
    seeds: list[dict[str, Any]],
    genes: dict[str, dict[str, Any]],
    nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[dict[str, str]]:
    verified: list[dict[str, str]] = []
    for seed in seeds:
        url = seed["url"]
        match_kind = seed.get("match_kind", "exact")
        if match_kind not in {"exact", "umbrella"}:
            raise ValueError(f"Unknown patient-group match_kind for {seed['name']}: {match_kind}")
        try:
            response = http.get(url, timeout=20)
            if response.status_code < 200 or response.status_code >= 400:
                continue
        except httpx.HTTPError:
            continue
        patient_group_id = (
            f"constellation:org/{re.sub(r'[^a-z0-9]+', '-', seed['name'].lower()).strip('-')}"
        )
        add_node(
            nodes,
            patient_group_id,
            "patient_group",
            seed["name"],
            {
                "url": str(response.url),
                "verified_on": date.today().isoformat(),
                "match_kind": match_kind,
            },
        )
        for symbol in seed.get("genes", []):
            gene = genes.get(symbol)
            if not gene:
                continue
            disease_id = gene["disease_id"]
            edges.append(
                make_edge(
                    patient_group_id,
                    seed["name"],
                    "serves",
                    disease_id,
                    nodes[disease_id]["label"],
                    evidence_class="curated",
                    source="seed_list",
                    source_record=url,
                    source_url=str(response.url),
                    properties={
                        "verified_on": date.today().isoformat(),
                        "match_kind": match_kind,
                    },
                )
            )
        verified.append({"name": seed["name"], "url": str(response.url)})
    return verified


def _author_edges(
    paper: dict[str, Any],
    genes: list[dict[str, Any]],
    nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    paper_id = paper["id"]
    for gene in genes:
        edges.append(
            make_edge(
                paper_id,
                paper["title"],
                "mentions_gene",
                gene["id"],
                gene["symbol"],
                evidence_class="observed",
                source="pubmed",
                source_record=paper["pmid"],
                source_url=f"https://pubmed.ncbi.nlm.nih.gov/{paper['pmid']}/",
            )
        )
    for author in paper.get("authors", []):
        name = author["name"]
        affiliations = sanitize_affiliations(author.get("affiliations", []))
        affiliation = affiliations[0] if affiliations else ""
        person_id = person_curie(name, affiliation)
        orcid = author.get("orcid")
        properties = {
            "affiliations": affiliations,
            "name_variants": [name],
            "roles": ["author"],
            **({"orcid": orcid} if orcid else {}),
        }
        add_node(nodes, person_id, "person", name, properties)
        edges.append(
            make_edge(
                person_id,
                name,
                "authored",
                paper_id,
                paper["title"],
                evidence_class="observed",
                source="pubmed",
                source_record=paper["pmid"],
                source_url=f"https://pubmed.ncbi.nlm.nih.gov/{paper['pmid']}/",
                properties={
                    "affiliations": affiliations,
                    **({"orcid": orcid} if orcid else {}),
                },
            )
        )


def _write_cache_abstracts(papers: dict[str, dict[str, Any]]) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "pubmed_abstracts.json").write_text(
        json.dumps(
            {key: row["abstract"] for key, row in papers.items() if row.get("abstract")},
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def build() -> dict[str, Any]:
    DATA.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    with (DATA / "slice.yaml").open(encoding="utf-8") as stream:
        slice_config = yaml.safe_load(stream)
    symbols: list[str] = slice_config["genes"]
    llm_mode = configured_mode() or ("cached" if any(CACHE.glob("extract-*.json")) else "offline")
    http = CachedHTTP()
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    genes: dict[str, dict[str, Any]] = {}
    diseases: dict[str, dict[str, Any]] = {}
    unresolved: list[str] = []
    phenotype_sets: dict[str, set[str]] = defaultdict(set)
    pathway_sets: dict[str, set[str]] = defaultdict(set)
    pathway_labels: dict[str, str] = {}
    uniprot_by_symbol: dict[str, str] = {}
    go_terms_by_gene: dict[str, set[str]] = defaultdict(set)
    go_term_counts: dict[str, int] = {}
    source_counts: dict[str, dict[str, Any]] = {}
    papers: dict[str, dict[str, Any]] = {}
    pubmed_full_counts: dict[str, int] = {}
    clinvar_counts: dict[str, int] = {}
    clinvar_records: dict[str, str | None] = {}
    trial_counts: dict[str, int] = {}
    trial_ids: set[str] = {TRIAL_SEED}
    trial_gene_hits: dict[str, set[str]] = defaultdict(set)
    (
        information_content,
        phenotype_parents,
        phenotype_labels,
        phenotype_synonyms,
    ) = hpo_information(http)

    for symbol in symbols:
        entity = resolve_gene(http, symbol)
        if not entity or not entity.get("id"):
            unresolved.append(f"{symbol}: Monarch gene lookup")
            continue
        gene_id = entity["id"]
        actual_symbol = entity.get("symbol") or symbol
        add_node(
            nodes,
            gene_id,
            "gene",
            actual_symbol,
            {
                "symbol": actual_symbol,
                "synonyms": entity.get("synonym") or [],
                "aliases": entity.get("exact_synonym") or [],
                "xref": _xrefs(entity),
                "source_url": f"https://api.monarchinitiative.org/v3/api/entity/{gene_id}",
            },
        )
        associations = monarch_associations(
            http,
            gene_id,
            category="biolink:CausalGeneToDiseaseAssociation",
        )
        source_association = next(
            (row for row in associations if str(row.get("object", "")).startswith("MONDO:")),
            None,
        )
        if not source_association:
            unresolved.append(f"{symbol}: Monarch DEE disease association")
            continue
        disease_id = source_association["object"]
        disease_entity = monarch_entity(http, disease_id) or {
            "id": disease_id,
            "name": source_association.get("object_label", disease_id),
            "synonym": [],
            "xref": [],
        }
        disease_label = (
            disease_entity.get("name") or source_association.get("object_label") or disease_id
        )
        disease_props = {
            "gene_id": gene_id,
            "gene_symbol": actual_symbol,
            "synonyms": disease_entity.get("synonym") or [],
            "omim": next(
                (x.split(":", 1)[1] for x in _xrefs(disease_entity) if x.startswith("OMIM:")), None
            ),
            "orphacode": next(
                (x.split(":", 1)[1] for x in _xrefs(disease_entity) if x.startswith("ORPHA:")), None
            ),
        }
        add_node(nodes, disease_id, "disease", disease_label, disease_props)
        gene_info = {
            "id": gene_id,
            "symbol": actual_symbol,
            "label": actual_symbol,
            "disease_id": disease_id,
            "disease_label": disease_label,
            "entity": entity,
        }
        genes[actual_symbol] = gene_info
        diseases[disease_id] = gene_info
        edges.append(
            make_edge(
                gene_id,
                actual_symbol,
                "causes",
                disease_id,
                disease_label,
                evidence_class="curated",
                source="monarch",
                source_record=str(source_association.get("id") or "monarch-association"),
                source_url=f"https://api.monarchinitiative.org/v3/api/entity/{disease_id}",
                properties={
                    "provided_by": source_association.get("provided_by"),
                    "publications": source_association.get("publications", []),
                },
            )
        )
        hpo_associations = monarch_associations(
            http,
            disease_id,
            category="biolink:DiseaseToPhenotypicFeatureAssociation",
        )
        for association in hpo_associations:
            phenotype_id = association.get("object", "")
            if not phenotype_id.startswith("HP:"):
                continue
            label = association.get("object_label") or phenotype_id
            add_node(
                nodes,
                phenotype_id,
                "phenotype",
                label,
                {
                    "information_content": information_content.get(phenotype_id, 0.0),
                    "parents": phenotype_parents.get(phenotype_id, []),
                    "synonyms": phenotype_synonyms.get(phenotype_id, []),
                },
            )
            phenotype_sets[disease_id].add(phenotype_id)
            edges.append(
                make_edge(
                    disease_id,
                    disease_label,
                    "has_phenotype",
                    phenotype_id,
                    label,
                    evidence_class="curated",
                    source="monarch",
                    source_record=str(association.get("id") or "monarch-phenotype-association"),
                    source_url=f"https://api.monarchinitiative.org/v3/api/entity/{disease_id}",
                    properties={
                        "frequency": association.get("frequency"),
                        "evidence": association.get("evidence"),
                        "provided_by": association.get("provided_by"),
                        "publications": association.get("publications", []),
                    },
                )
            )
    if unresolved:
        raise RuntimeError("Unresolved DEE slice entries: " + "; ".join(unresolved))

    go_data = load_goa_human_data(http, set(genes))
    source_counts["go_gaf"] = {
        "source": "GOA human GAF",
        "query": "goa_human.gaf.gz; aspect P; skip NOT; all evidence for specificity counts",
        "count": go_data["annotation_rows"],
        "retrieved_at": date.today().isoformat(),
    }

    for symbol, gene in genes.items():
        papers_for_gene = pubmed_records(http, symbol, retmax=60)
        pubmed_full_counts[symbol] = pubmed_count(http, symbol)
        for paper in papers_for_gene:
            existing = papers.setdefault(paper["id"], paper)
            if symbol not in existing.setdefault("gene_symbols", []):
                existing["gene_symbols"].append(symbol)
        count, clinvar_id = clinvar_pathogenic_count(http, symbol)
        clinvar_counts[symbol] = count
        clinvar_records[symbol] = clinvar_id
        source_counts[f"pubmed:{symbol}"] = {
            "source": "PubMed",
            "query": (
                f'"{symbol}"[Title/Abstract] AND '
                "(epilepsy OR encephalopathy OR seizure OR neurodevelopment)"
            ),
            "count": pubmed_full_counts[symbol],
            "retrieved_at": date.today().isoformat(),
        }
        source_counts[f"clinvar:{symbol}"] = {
            "source": "ClinVar",
            "query": f"{symbol} pathogenic or likely pathogenic",
            "count": count,
            "retrieved_at": date.today().isoformat(),
        }
        uniprot = uniprot_accession(http, gene["entity"])
        if uniprot:
            nodes[gene["id"]]["properties"]["uniprot_id"] = uniprot
            uniprot_by_symbol[symbol] = uniprot
        trial_results, trial_count = search_trials_with_count(http, symbol, page_size=100)
        trial_counts[actual_symbol] = trial_count
        for study in trial_results:
            nct = study.get("protocolSection", {}).get("identificationModule", {}).get("nctId")
            if nct:
                trial_ids.add(nct)
                trial_gene_hits[str(nct)].add(actual_symbol)
        source_counts[f"ctgov:{symbol}"] = {
            "source": "ClinicalTrials.gov",
            "query": symbol,
            "count": trial_count,
            "retrieved_at": date.today().isoformat(),
        }

    reactome_uniprot_to_symbol = dict(go_data["uniprot_to_symbol"])
    reactome_uniprot_to_symbol.update(
        {accession: symbol for symbol, accession in uniprot_by_symbol.items()}
    )
    reactome_data = load_reactome_all_levels(http, reactome_uniprot_to_symbol)
    pathway_sets = defaultdict(
        set,
        {
            symbol: set(pathways)
            for symbol, pathways in reactome_data["pathways_by_gene"].items()
            if symbol in genes
        },
    )
    pathway_labels.update(reactome_data["term_names"])
    source_counts["reactome_all_levels"] = {
        "source": "Reactome UniProt2Reactome",
        "query": "UniProt2Reactome_All_Levels.txt; Homo sapiens",
        "count": reactome_data["mapping_rows"],
        "retrieved_at": date.today().isoformat(),
    }
    for symbol, pathway_ids in pathway_sets.items():
        for pathway_id in sorted(pathway_ids):
            pathway_name = pathway_labels.get(pathway_id, pathway_id)
            gene_count = reactome_data["gene_counts"].get(pathway_id, 0)
            add_node(
                nodes,
                pathway_id,
                "mechanism",
                pathway_name,
                {
                    "level": "Reactome pathway",
                    "human_gene_count": gene_count,
                    "member_genes": [],
                },
            )
            nodes[pathway_id]["properties"]["member_genes"] = sorted(
                set(nodes[pathway_id]["properties"].get("member_genes", [])) | {symbol}
            )
            edges.append(
                make_edge(
                    genes[symbol]["id"],
                    symbol,
                    "participates_in",
                    pathway_id,
                    pathway_name,
                    evidence_class="curated",
                    source="reactome",
                    source_record=pathway_id,
                    source_url=f"https://reactome.org/content/detail/{pathway_id}",
                    properties={"human_gene_count": gene_count},
                )
            )

    go_term_counts = go_data["gene_counts"]
    go_term_labels = go_data["term_names"]
    go_annotations_by_gene = go_data["annotations_by_gene"]
    source_counts["go_specificity_counts"] = {
        "source": "GOA human GAF + go-basic OBO",
        "query": "distinct genes propagated through is_a/part_of using all evidence codes",
        "count": len(go_term_counts),
        "retrieved_at": date.today().isoformat(),
    }
    for symbol in genes:
        annotations = go_annotations_by_gene.get(symbol, [])
        source_counts[f"go:{symbol}"] = {
            "source": "GOA human GAF",
            "query": f"{symbol}; aspect P; experimental evidence codes",
            "count": len(annotations),
            "retrieved_at": date.today().isoformat(),
        }
    experimental_terms = {
        annotation["go_id"]
        for annotations in go_annotations_by_gene.values()
        for annotation in annotations
    }
    go_ancestor_sets = {
        term_id: propagate_go_terms({term_id}, go_data["parents"])
        for term_id in experimental_terms
    }
    go_evidence_by_gene_term: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for symbol, gene_annotations in go_annotations_by_gene.items():
        for annotation in gene_annotations:
            for term_id in go_ancestor_sets[annotation["go_id"]]:
                gene_count = go_term_counts.get(term_id, 0)
                if gene_count > 500:
                    continue
                go_terms_by_gene[symbol].add(term_id)
                go_evidence_by_gene_term[symbol].setdefault(term_id, annotation)
    for symbol, terms in go_terms_by_gene.items():
        for term_id in sorted(terms):
            annotation = go_evidence_by_gene_term[symbol][term_id]
            label = go_term_labels.get(term_id, term_id)
            add_node(
                nodes,
                term_id,
                "mechanism",
                label,
                {
                    "level": "GO biological process",
                    "human_gene_count": go_term_counts[term_id],
                    "member_genes": [],
                },
            )
            nodes[term_id]["properties"]["member_genes"] = sorted(
                set(nodes[term_id]["properties"].get("member_genes", [])) | {symbol}
            )
            pathway_labels[term_id] = label
            source_record = (
                f"{annotation['go_id']}|{annotation['reference']}|"
                f"{annotation['evidence_code']}"
            )
            edges.append(
                make_edge(
                    genes[symbol]["id"],
                    symbol,
                    "annotated_to",
                    term_id,
                    label,
                    evidence_class="curated",
                    source="go",
                    source_record=source_record,
                    source_url=f"https://amigo.geneontology.org/amigo/term/{term_id}",
                    properties={
                        "direct_go_id": annotation["go_id"],
                        "evidence_code": annotation["evidence_code"],
                        "reference": annotation["reference"],
                        "assigned_by": annotation["assigned_by"],
                        "human_gene_count": go_term_counts[term_id],
                        "propagated": term_id != annotation["go_id"],
                    },
                )
            )

    for paper in papers.values():
        add_node(
            nodes,
            paper["id"],
            "paper",
            paper["title"] or paper["id"],
            {
                "year": paper.get("year"),
                "journal": paper.get("journal"),
                "doi": paper.get("doi"),
            },
        )
        associated_genes = [genes[symbol] for symbol in paper["gene_symbols"] if symbol in genes]
        _author_edges(paper, associated_genes, nodes, edges)
        extraction_rows = []
        for gene in associated_genes:
            try:
                extraction, evidence_class = extract_claims(
                    paper, gene["symbol"], list(genes), llm_mode
                )
            except Exception:
                extraction = extract_heuristic(paper, gene["symbol"])
                evidence_class = "inferred"
            extraction_rows.extend(
                (gene, claim, evidence_class)
                for claim in extraction.claims
                if claim.subject_gene.casefold() == gene["symbol"].casefold()
                and claim.quote in paper.get("abstract", "")
            )
        for gene, claim, evidence_class in extraction_rows:
            mechanism_slug = re.sub(r"[^a-z0-9]+", "-", claim.object_text.lower()).strip("-")
            mechanism_id = f"constellation:mechanism/{mechanism_slug}"
            add_node(nodes, mechanism_id, "mechanism", claim.object_text.replace("_", " "))
            class_name = MECHANISM_CLASS_LABELS.get(claim.object_text, "unknown")
            class_id = f"constellation:vc/{gene['symbol'].lower()}/{class_name}"
            add_node(
                nodes,
                class_id,
                "variant_class",
                class_name,
                {"class": class_name, "support_pmids": [paper["id"]]},
            )
            is_variant_claim = claim.claim_type == "variant_effect"
            edges.append(
                make_edge(
                    paper["id"],
                    paper["title"] or paper["id"],
                    "claims",
                    class_id if is_variant_claim else mechanism_id,
                    class_name if is_variant_claim else nodes[mechanism_id]["label"],
                    evidence_class=evidence_class,
                    source="pubmed",
                    source_record=paper["pmid"],
                    source_url=f"https://pubmed.ncbi.nlm.nih.gov/{paper['pmid']}/",
                    confidence=claim.confidence,
                    quote=claim.quote,
                    polarity=claim.polarity,
                    method=(
                        "OpenAI Structured Outputs"
                        if evidence_class == "llm_extracted"
                        else "heuristic-extract v1"
                    ),
                    properties={
                        "gene_id": gene["id"],
                        "claim_type": claim.claim_type,
                        "evidence_level": claim.evidence_level,
                    },
                )
            )
        _write_cache_abstracts(papers)

    for symbol, count in clinvar_counts.items():
        gene = genes[symbol]
        class_edges = [
            edge
            for edge in edges
            if edge["predicate"] == "claims"
            and edge["properties"].get("gene_id") == gene["id"]
            and edge["object"].startswith("constellation:vc/")
        ]
        classes = [edge["object"].rsplit("/", 1)[-1] for edge in class_edges]
        observed_class = Counter(classes).most_common(1)[0][0] if classes else "unknown"
        class_id = f"constellation:vc/{symbol.lower()}/{observed_class}"
        add_node(
            nodes,
            class_id,
            "variant_class",
            observed_class,
            {
                "class": observed_class,
                "n_pathogenic": count,
                "support_pmids": sorted({edge["source_record"] for edge in class_edges}),
            },
        )
        if count and clinvar_records[symbol]:
            edges.append(
                make_edge(
                    gene["id"],
                    symbol,
                    "has_variant_class",
                    class_id,
                    observed_class,
                    evidence_class="observed",
                    source="clinvar",
                    source_record=clinvar_records[symbol] or "",
                    source_url=f"https://www.ncbi.nlm.nih.gov/clinvar/variation/{clinvar_records[symbol]}/",
                    confidence=1.0,
                    method="ClinVar pathogenic/likely pathogenic result",
                    properties={"n_pathogenic": count},
                )
            )
        elif class_edges:
            supporting_claim = class_edges[0]
            edges.append(
                make_edge(
                    gene["id"],
                    symbol,
                    "has_variant_class",
                    class_id,
                    observed_class,
                    evidence_class="inferred",
                    source="pubmed",
                    source_record=supporting_claim["source_record"],
                    source_url=supporting_claim["source_url"],
                    confidence=supporting_claim["confidence"],
                    quote=supporting_claim["quote"],
                    method="literature variant-effect majority",
                    properties={"support_pmids": [row["source_record"] for row in class_edges]},
                )
            )
        gene["variant_class"] = observed_class
        nodes[gene["disease_id"]]["properties"]["variant_class"] = observed_class

    projects: dict[str, dict[str, Any]] = {}
    project_gene_hits: dict[str, set[str]] = defaultdict(set)
    for symbol in genes:
        project_rows = reporter_projects(http, symbol)[:100]
        source_counts[f"reporter:{symbol}"] = {
            "source": "NIH RePORTER",
            "query": symbol,
            "count": len(project_rows),
            "retrieved_at": date.today().isoformat(),
        }
        for row in project_rows:
            project_num = str(row.get("project_num") or "")
            if not project_num:
                continue
            projects[project_num] = row
            project_gene_hits[project_num].add(symbol)
    for project in projects.values():
        project_num = str(project["project_num"])
        project_id = f"NIH:{project_num}"
        title = project.get("project_title", project_num)
        active_flag = str(project.get("is_active", "")).casefold() in {
            "true",
            "1",
            "yes",
        }
        end_date = str(project.get("project_end_date") or "")[:10]
        add_node(
            nodes,
            project_id,
            "award",
            title,
            {
                "fiscal_year": project.get("fiscal_year"),
                "organization": project.get("organization"),
                "amount": project.get("award_amount"),
                "terms": project.get("terms") or "",
                "active": active_flag or end_date >= date.today().isoformat(),
            },
        )
        for symbol in sorted(project_gene_hits[project_num]):
            gene = genes.get(symbol)
            if gene:
                edges.append(
                    make_edge(
                        project_id,
                        title,
                        "studies_gene",
                        gene["id"],
                        symbol,
                        evidence_class="observed",
                        source="reporter",
                        source_record=project_num,
                        source_url=f"https://reporter.nih.gov/project-details/{project_num}",
                    )
                )
        pi_names = project.get("contact_pi_name") or []
        if isinstance(pi_names, (str, dict)):
            pi_names = [pi_names]
        for pi in pi_names:
            name = pi if isinstance(pi, str) else pi.get("full_name", "")
            if not name:
                continue
            affiliation = sanitize_affiliation(project.get("organization", ""))
            orcid = pi.get("orcid") if isinstance(pi, dict) else None
            person_id = person_curie(name, affiliation)
            add_node(
                nodes,
                person_id,
                "person",
                name,
                {
                    "affiliations": [affiliation] if affiliation else [],
                    "roles": ["pi"],
                    **({"orcid": orcid} if orcid else {}),
                },
            )
            edges.append(
                make_edge(
                    person_id,
                    name,
                    "funds",
                    project_id,
                    title,
                    evidence_class="observed",
                    source="reporter",
                    source_record=project_num,
                    source_url=f"https://reporter.nih.gov/project-details/{project_num}",
                    properties={
                        "role": "pi",
                        "affiliations": [affiliation] if affiliation else [],
                        **({"orcid": orcid} if orcid else {}),
                    },
                )
            )
        for publication in reporter_publications(http, project_num):
            pmid = str(publication.get("pmid") or publication.get("PMID") or "")
            if not pmid.isdigit():
                continue
            paper_id = f"PMID:{pmid}"
            paper_title = (
                publication.get("publication_title")
                or publication.get("PublicationTitle")
                or paper_id
            )
            add_node(
                nodes,
                paper_id,
                "paper",
                paper_title,
                {
                    "year": publication.get("year") or publication.get("Year"),
                    "journal": publication.get("journal_title") or publication.get("JournalTitle"),
                },
            )
            edges.append(
                make_edge(
                    project_id,
                    title,
                    "has_publication",
                    paper_id,
                    paper_title,
                    evidence_class="observed",
                    source="reporter",
                    source_record=project_num,
                    source_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    properties={"pmid": pmid},
                )
            )

    trial_cache: dict[str, dict[str, Any]] = {}
    for nct_id in sorted(trial_ids):
        study = trial_by_nct(http, nct_id)
        if not study:
            continue
        trial_cache[nct_id] = study
        _study_to_asset(
            study,
            genes,
            nodes,
            edges,
            phenotype_labels,
            phenotype_synonyms,
            information_content,
            phenotype_parents,
        )
        study_id = f"NCT:{nct_id}"
        if study_id not in nodes:
            continue
        existing_gene_links = {
            edge["object"]
            for edge in edges
            if edge["subject"] == study_id and edge["predicate"] == "studies_gene"
        }
        for symbol in sorted(trial_gene_hits.get(nct_id, set())):
            gene = genes[symbol]
            if gene["id"] in existing_gene_links:
                continue
            edges.append(
                make_edge(
                    study_id,
                    nodes[study_id]["label"],
                    "studies_gene",
                    gene["id"],
                    symbol,
                    evidence_class="observed",
                    source="ctgov",
                    source_record=nct_id,
                    source_url=f"https://clinicaltrials.gov/study/{nct_id}",
                )
            )
            existing_gene_links.add(gene["id"])
    source_counts["NCT06555965"] = {
        "source": "ClinicalTrials.gov",
        "query": TRIAL_SEED,
        "count": 1 if TRIAL_SEED in trial_cache else 0,
        "retrieved_at": date.today().isoformat(),
    }

    with httpx.Client(follow_redirects=True, timeout=20) as verifier:
        verified_groups = _add_seed_foundations(
            verifier, slice_config["foundations"], genes, nodes, edges
        )
    source_counts["patient_groups"] = {
        "source": "verified foundation seed list",
        "query": "15 curated DEE patient groups",
        "count": len(verified_groups),
        "retrieved_at": date.today().isoformat(),
    }
    for symbol, gene in genes.items():
        source_counts[f"foundation:{symbol}"] = {
            "source": "verified foundation seed list",
            "query": symbol,
            "count": sum(
                1
                for edge in edges
                if edge["predicate"] == "serves"
                and edge["object"] == gene["disease_id"]
                and edge["source"] == "seed_list"
            ),
            "retrieved_at": date.today().isoformat(),
        }

    string_scores: dict[tuple[str, str], dict[str, float]] = {}
    string_rows = string_network(http, genes)
    for row in string_rows:
        left, right = row.get("preferredName_A"), row.get("preferredName_B")
        if left in genes and right in genes:
            channel_names = ("nscore", "fscore", "pscore", "ascore", "escore", "dscore", "tscore")
            channel_scores = {
                name: float(row.get(name) or 0.0)
                for name in channel_names
            }
            combined_raw = float(row.get("score") or 0.0)
            score_scale = 1000.0 if max([combined_raw, *channel_scores.values()]) > 1 else 1.0
            combined_score = combined_raw / score_scale
            escore = channel_scores["escore"] / score_scale
            dscore = channel_scores["dscore"] / score_scale
            string_mech = string_mechanism_score(escore, dscore)
            pair = tuple(sorted((left, right)))
            previous = string_scores.get(pair)
            if previous is None or combined_score > previous["string_score"]:
                string_scores[pair] = {
                    "string_score": combined_score,
                    "string_mech": string_mech,
                }
            pair_digest = hashlib.sha1("-".join(pair).encode()).hexdigest()[:12]
            mechanism_id = f"constellation:mechanism/string/{pair_digest}"
            add_node(
                nodes,
                mechanism_id,
                "mechanism",
                f"STRING-linked group: {left} / {right}",
                {"member_genes": list(pair)},
            )
            for symbol in pair:
                edges.append(
                    make_edge(
                        genes[symbol]["id"],
                        symbol,
                        "participates_in",
                        mechanism_id,
                        nodes[mechanism_id]["label"],
                        evidence_class="curated",
                        source="string",
                        source_record=f"{left}|{right}",
                        source_url="https://string-db.org",
                        confidence=combined_score,
                        properties={
                            **channel_scores,
                            "score": combined_raw,
                            "string_score": combined_score,
                            "string_mech": string_mech,
                            "channel_score_scale": score_scale,
                            "required_score": 0.7,
                        },
                    )
                )

    semsim_scores: dict[tuple[str, str], float] = {}
    for disease_id in diseases:
        object_sets = [
            {
                "id": other_id,
                "label": nodes[other_id]["label"],
                "phenotypes": sorted(phenotypes),
            }
            for other_id in diseases
            if other_id != disease_id and (phenotypes := phenotype_sets.get(other_id, set()))
        ]
        results = monarch_semsim_multicompare(
            http, sorted(phenotype_sets.get(disease_id, set())), object_sets
        )
        for row in results:
            subject = row.get("subject")
            if isinstance(subject, dict):
                result_id = row.get("id") or subject.get("id")
            else:
                result_id = row.get("id") or subject
            if result_id not in diseases:
                continue
            score = (
                row.get("score") or row.get("similarity") or row.get("ancestor_information_content")
            )
            if score is None:
                continue
            try:
                semsim_scores[(disease_id, result_id)] = float(score)
            except (TypeError, ValueError):
                continue

    for node in nodes.values():
        if node["type"] == "mechanism" and str(node["id"]).startswith("R-HSA-"):
            node["properties"]["level"] = "Reactome pathway"

    reactome_parents = reactome_pathway_parents(http)
    source_counts["reactome_hierarchy"] = {
        "source": "Reactome",
        "query": "eventsHierarchy/9606",
        "count": len(reactome_parents),
        "retrieved_at": date.today().isoformat(),
    }
    for pathway_id, parent_ids in reactome_parents.items():
        if pathway_id in nodes and nodes[pathway_id]["type"] == "mechanism":
            nodes[pathway_id]["properties"]["parents"] = sorted(parent_ids)
            ancestors: set[str] = set()
            pending = list(parent_ids)
            while pending:
                parent_id = pending.pop()
                if parent_id in ancestors:
                    continue
                ancestors.add(parent_id)
                pending.extend(reactome_parents.get(parent_id, set()))
            nodes[pathway_id]["properties"]["ancestor_ids"] = sorted(ancestors)
    lowest_pathway_sets = lowest_level_pathways(dict(pathway_sets), reactome_parents)
    pathway_sets = defaultdict(
        set,
        {
            symbol: {
                pathway_id
                for pathway_id in pathway_ids
                if 0 < reactome_data["gene_counts"].get(pathway_id, 0) <= 500
            }
            for symbol, pathway_ids in lowest_pathway_sets.items()
        },
    )
    disease_ids = list(diseases)
    genes_by_id = {gene["id"]: symbol for symbol, gene in genes.items()}
    disease_gene = {
        disease_id: genes_by_id[nodes[disease_id]["properties"]["gene_id"]]
        for disease_id in disease_ids
    }
    scores = fused_similarity(
        disease_ids,
        disease_gene,
        phenotype_sets,
        pathway_sets,
        {symbol: gene.get("variant_class", "unknown") for symbol, gene in genes.items()},
        string_scores,
        semsim_scores,
        go_terms=go_terms_by_gene,
        go_counts=go_term_counts,
        reactome_depths=pathway_depths(reactome_parents),
        reactome_counts=reactome_data["gene_counts"],
    )
    for score in scores:
        shared_terms = score["shared_pathways"]
        score["mechanism_label"] = (
            "; ".join(
                pathway_labels.get(term) or str(term) for term in shared_terms[:2]
            )
            if shared_terms
            else (
                "physical/curated interaction "
                f"(STRING exp/db {score['string_mech']:.2f})"
            )
            if score["supported"] and score["string_mech"] >= 0.7
            else ""
        )
    term_gene_counts = {**reactome_data["gene_counts"], **go_term_counts}
    pathway_specificity = {
        term: 1.0 / count
        for term, count in term_gene_counts.items()
        if count > 0
    }
    clusters, _cluster_for_disease = cluster_records(
        disease_ids,
        scores,
        {disease_id: nodes[disease_id]["label"] for disease_id in disease_ids},
        pathway_labels,
        reactome_parents,
        pathway_specificity,
    )
    for cluster in clusters:
        add_node(
            nodes,
            cluster["id"],
            "cluster",
            cluster["label"],
            {
                "member_ids": cluster["member_ids"],
                "resolution": cluster["resolution"],
                "seed": cluster["seed"],
                "stability": cluster["stability"],
                "counterexample_id": cluster["counterexample_id"],
                "excluded_by": cluster["excluded_by"],
            },
        )
        for disease_id in cluster["member_ids"]:
            edges.append(
                make_edge(
                    disease_id,
                    nodes[disease_id]["label"],
                    "member_of",
                    cluster["id"],
                    cluster["label"],
                    evidence_class="inferred",
                    source="analytics",
                    source_record=f"{cluster['id']}:{cluster['resolution']}",
                    confidence=cluster["stability"],
                    method=f"leiden r={cluster['resolution']} seed={cluster['seed']}",
                    properties={"stability": cluster["stability"]},
                )
            )
    for score in scores:
        a, b = score["disease_a"], score["disease_b"]
        predicate = "shares_mechanism_with" if score["supported"] else "phenotypically_similar_to"
        edges.append(
            make_edge(
                a,
                nodes[a]["label"],
                predicate,
                b,
                nodes[b]["label"],
                evidence_class="inferred",
                source="analytics",
                source_record=f"{a}|{b}",
                confidence=score["S"],
                method="P/M/V fusion: S=0.5P+0.3M+0.2V",
                properties={
                    **{
                        key: score[key]
                        for key in (
                            "P",
                            "M",
                            "M_reactome",
                            "M_go",
                            "string_mech",
                            "V",
                            "S",
                            "supported",
                            "shared_pathways",
                            "mechanism_label",
                            "string_score",
                            "variant_class_a",
                            "variant_class_b",
                        )
                    },
                    "weights": {"P": 0.5, "M": 0.3, "V": 0.2},
                },
            )
        )

    for node in nodes.values():
        sanitize_snapshot_record(node)
    for edge in edges:
        sanitize_snapshot_record(edge)

    known_ids = set(nodes)
    valid_edges: list[dict[str, Any]] = []
    rejected_path = DATA / "rejected_edges.jsonl"
    if rejected_path.exists():
        rejected_path.unlink()
    for edge in edges:
        reason = validate_edge(edge, known_ids)
        if reason:
            reject_edge(edge, reason)
        else:
            valid_edges.append(edge)
    _write_cache_abstracts(papers)
    write_snapshot(list(nodes.values()), valid_edges, clusters)
    (DATA / "source_metrics.json").write_text(json.dumps(source_counts, indent=2), encoding="utf-8")

    candidates: list[tuple[int, int, int, str]] = []
    for symbol, gene in genes.items():
        count = pubmed_full_counts.get(symbol, 0)
        trials_for_gene = trial_counts.get(symbol, 0)
        has_group = any(
            edge["predicate"] == "serves"
            and edge["subject"].startswith("constellation:org/")
            and edge["object"] == gene["disease_id"]
            for edge in valid_edges
        )
        if count < 20 and trials_for_gene == 0 and not has_group:
            candidates.append((count, trials_for_gene, int(has_group), symbol))
    snapshot_for_gap = Snapshot()
    preferred_gap = next(
        (
            row
            for row in candidates
            if row[-1] == "FRRS1L"
            if is_gap(snapshot_for_gap, genes[row[-1]]["disease_id"])
        ),
        None,
    )
    gap_candidate = preferred_gap or next(
        (
            row
            for row in candidates
            if row[-1] != "FRRS1L" and is_gap(snapshot_for_gap, genes[row[-1]]["disease_id"])
        ),
        None,
    )
    if gap_candidate is None:
        raise RuntimeError(
            "No disease meets the PubMed/trial/foundation source-count rule and gap detector."
        )
    gap_symbol = gap_candidate[-1]
    gap_disease = genes[gap_symbol]["disease_id"]
    foundation_count = sum(
        1
        for edge in valid_edges
        if edge["predicate"] == "serves"
        and edge["object"] == gap_disease
        and edge["subject"].startswith("constellation:org/")
    )
    decisions = (
        "# Build decisions\n\n"
        f"Generated from the source responses on {date.today().isoformat()}.\n\n"
        f"- Honest-gap disease: `{gap_symbol}` / `{gap_disease}`. "
        f"Live PubMed hits: {pubmed_full_counts.get(gap_symbol, 0)}; ClinicalTrials.gov "
        f"records: {source_counts.get(f'ctgov:{gap_symbol}', {}).get('count', 0)}; "
        f"verified foundation links: {foundation_count}.\n"
        f"- Gap detector fired: {'; '.join(gap_reasons(snapshot_for_gap, gap_disease))}.\n"
        "- Similarity layers: source-derived Monarch semantic-similarity results, "
        "lowest-level specific Reactome pathway sets, specific GO biological-process terms, "
        "filtered STRING mechanism scores, and literature-derived variant-class labels. "
        "No gene-name exceptions are used in the analytics.\n"
        "- STRING mechanism uses `1 - (1-escore)*(1-dscore)`; text-mining is excluded because "
        "co-mention can inflate mechanism evidence. STRING supports M only when this score is "
        "at least 0.7; every channel score is retained in edge properties.\n"
        "- Reactome pathways come from the human UniProt2Reactome all-level mapping. "
        "Specificity counts distinct human genes per pathway; only lowest-level pathways "
        "with counts ≤500 contribute to `M_reactome`.\n"
        "- GO biological-process annotations use `goa_human.gaf.gz` and `go-basic.obo`, "
        "aspect P, excluding NOT qualifiers, propagated over `is_a`/`part_of`. Specificity "
        "counts distinct human genes assigned to each term or its descendants across all "
        "evidence codes; ≤500 is the GSEA default maximum gene-set size. Per-gene scoring "
        "uses experimental codes only, and `M_go` is the Jaccard of specific propagated terms.\n"
        "- HPO information content uses `phenotype.hpoa` and `hp.json` `is_a` ancestry. "
        "Each term counts distinct HPOA diseases annotated to it or a descendant, independent "
        "of the slice. Asset profiles union served-disease phenotypes and HPO terms matched "
        "by exact outcome label/synonym; coverage accepts exact terms, descendants, and "
        "ancestors whose IC is at least half the target term IC, weighted by target IC.\n"
        "- Mechanism support is the maximum of Reactome lowest-level Jaccard, specific GO "
        "Jaccard, and the thresholded STRING indicator. The support guard is "
        "`S >= 0.45 AND M >= 0.25`; weights and the 0.45 S threshold are unchanged. "
        "The previous `M > 0` guard admitted tiny incidental overlaps such as SLC2A1's "
        "single insulin-secretion pathway (M=0.083).\n"
        "- Golden expectation revised: DNM1 removed from required members after evidence "
        "review (experimental GO / Reactome / physical STRING show no specific shared "
        "mechanism; DNM1 acts at vesicle endocytosis, the cluster core at SNARE exocytosis); "
        "it is surfaced as a partial overlap instead.\n"
        "- Monarch pairwise scores use `/v3/api/semsim/multicompare` for the in-slice "
        "phenotype sets. `/v3/api/semsim/search` caps responses at 50 and omitted DNM1 from "
        "STXBP1's result set; multicompare provides a complete slice matrix without changing "
        "the weights.\n"
        "- Animal-model assets were omitted unless a source-validated MGI allele record and live "
        "URL could be verified.\n"
    )
    (DATA.parent / "docs" / "DECISIONS.md").write_text(decisions, encoding="utf-8")
    (DATA / "demo_config.json").write_text(
        json.dumps(
            {
                "supported_disease": "MONDO:0012812",
                "gap_disease": gap_disease,
                "gap_gene": gap_symbol,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    # Dossiers are written by the same deterministic generator used by the API after loading
    # the immutable snapshot and its source-backed graph tools.
    from constellation.agents.dossier import build_dossier, dossier_cache_path

    snapshot = Snapshot()
    current_dossier_paths = set()
    for disease_id in ("MONDO:0012812", gap_disease):
        if disease_id not in snapshot.node_by_id:
            continue
        for persona in ("maria", "devon", "priya", "osei"):
            dossier = build_dossier(snapshot, disease_id, persona, mode="offline")
            cache_path = dossier_cache_path(disease_id, persona, snapshot.snapshot_hash, "offline")
            current_dossier_paths.add(cache_path)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(dossier, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    for cache_path in CACHE.glob("*.json"):
        if cache_path in current_dossier_paths:
            continue
        try:
            cache_path.unlink()
        except OSError:
            continue
    http.close()
    return {
        "nodes": len(nodes),
        "edges": len(valid_edges),
        "clusters": len(clusters),
        "papers": len(papers),
        "gap_gene": gap_symbol,
        "gap_disease": gap_disease,
        "rejected": sum(1 for _ in rejected_path.open(encoding="utf-8"))
        if rejected_path.exists()
        else 0,
    }


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
