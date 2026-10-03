# Constellation API contract (v1) — authoritative; frontend and backend both build to this

All endpoints under `/api`, JSON, read-only over the committed snapshot except POST /dossier and POST /contribute.
Every factual item the UI shows carries `edge_ids` that resolve via GET /api/edge/{edge_id}.
CURIEs everywhere (MONDO:, HGNC:, HP:, PMID:, NCT:, R-HSA-, constellation:...). URL path params are URL-encoded CURIEs.

## Shared types
LedgerRow = {edge_id, subject, subject_label, predicate, object, object_label, evidence_class: "observed"|"curated"|"inferred"|"llm_extracted"|"web_discovered"|"proposed",
             source, source_record, source_url|null, retrieved_at (ISO date), confidence (0..1), quote|null, polarity: "supports"|"contradicts"|null,
             contradicted_by: [edge_id], method|null, schema_version: int, properties: {}}
DiseaseRef = {id, name, gene_id, gene_symbol}

## Endpoints
GET  /api/health -> {status:"ok", snapshot_hash, llm_mode: "live"|"cached"|"offline", counts:{nodes, edges}}
GET  /api/search?q= -> {query, best: SearchHit|null, results:[SearchHit]}
     SearchHit = {id, type:"disease"|"gene"|"phenotype"|"patient_group"|"mechanism", label, matched_text, match_kind:"id"|"label"|"synonym"|"symbol"|"alias"|"org", disease: DiseaseRef|null}
     ("STXBP1", "Munc18-1", "DEE4", "STXBP1 Foundation" all -> best.disease.id == "MONDO:0012812")
GET  /api/disease/{mondo} -> {disease: DiseaseRef & {synonyms, omim|null, orphacode|null},
     counts: {phenotypes, pathogenic_variants, trials, awards_active, papers, patient_groups}  each = {n:int, edge_ids:[edge_id] (<=50)},
     patient_groups:[{id, name, url, edge_id, evidence_class}], summary:{technical:[Sentence], plain:[Sentence]}, is_gap: bool}
     Sentence = {text, edge_ids:[edge_id]}
GET  /api/disease/{mondo}/cluster -> {disease_id, cluster: {id, label, resolution, seed, stability, member_ids:[mondo]}|null,
     neighbours:[Neighbour] (supported, sorted by S desc), counterexample: Neighbour & {excluded_by:"M"|"V"|"S"}|null,
     nearest_leads:[Neighbour] (top 3 unsupported by S, for gap state), weights:{P:0.5,M:0.3,V:0.2,threshold:0.45}}
     Neighbour = {disease: DiseaseRef, P, M, V, S, supported: bool, mechanism_label, shared_pathways:[{id,name}], string_score|null,
                  variant_class_a, variant_class_b, edge_id (shares_mechanism_with or phenotypically_similar_to), layer_edge_ids:{P:[..],M:[..],V:[..]}}
GET  /api/edge/{edge_id} -> LedgerRow & {contradicting: [LedgerRow]}
GET  /api/cluster/{cluster_id}/assets?for={mondo} -> {cluster_id, for_disease, assets:[Asset]}
     Asset = {id, name, asset_type:"natural_history_study"|"registry"|"biobank"|"animal_model"|"biomarker"|"protocol", url, record_id (e.g. NCT06555965),
              serves:[DiseaseRef], coverage:{value 0..1, numerator_ic, denominator_ic, n_matched, n_total, matched:[{id,label}], unmatched:[{id,label}]}|null,
              eligibility_diff:[{field, asset_value, target_value, status:"matches"|"differs"|"needs_expert_review"}], edge_ids:[edge_id]}
GET  /api/bridges?a={mondo}&b={mondo} -> {a, b, people:[Bridge]}
     Bridge = {id, display_name, affiliations:[str], roles:["author"|"pi"|"study_official"], n_a, n_b, score,
               proving_edges:[{edge_id, kind:"paper"|"study"|"award", record_id, title, year|null, url, disease_id}], why_same_person: str}
GET  /api/disease/{mondo}/coverage -> {disease_id, is_gap, reasons:[str], sources:[{source, query, count, retrieved_at}],
     nearest_leads:[{disease: DiseaseRef, S, missing_layer:"M"|"V"|"asset"|"people"}], what_would_change:[{layer, text, edge_ids}]}
POST /api/dossier {disease, persona:"maria"|"devon"|"priya"|"osei"} -> Dossier
     Dossier = {disease_id, persona, mode:"live"|"cached"|"offline", snapshot_hash, trace_id, generated_at,
                sections:[{key:"who_shares"|"what_exists"|"what_differs"|"who_to_contact"|"next_step"|"coverage", title, sentences:[Sentence]}],
                dropped_sentences:int, gap_plan: {reasons, what_would_change:[{layer,text,edge_ids}]}|null, markdown: str}
     Every sentence must have >=1 edge_id that exists in the snapshot (cite-or-drop validator in code).
GET  /api/mechanism/search?q= -> {query, clusters:[{id, label, score, member_ids, matched_pathways:[{id,name}]}]}
POST /api/contribute {url, sentence, edge_id, polarity} -> LedgerRow (evidence_class "proposed"; stored in data/proposed.jsonl; never enters analytics)
GET  /api/export/kgx -> application/zip with nodes.tsv, edges.tsv (KGX)
