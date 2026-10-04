import { mkdir, writeFile, rm } from "node:fs/promises";
import path from "node:path";

const root = path.resolve("fixtures");
const today = "2025-02-14";
const stx = { id: "MONDO:0012812", name: "developmental and epileptic encephalopathy 4", gene_id: "HGNC:00000007", gene_symbol: "STXBP1" };
const gap = { id: "MONDO:0014859", name: "developmental and epileptic encephalopathy 37", gene_id: "HGNC:00000008", gene_symbol: "FRRS1L" };
const genes = [
  ["DNM1", "HGNC:00000001", "developmental and epileptic encephalopathy 31", "MONDO:9900001"],
  ["SNAP25", "HGNC:00000002", "developmental and epileptic encephalopathy 4-like", "MONDO:9900002"],
  ["STX1B", "HGNC:00000003", "developmental and epileptic encephalopathy 26", "MONDO:9900003"],
  ["VAMP2", "HGNC:00000004", "developmental and epileptic encephalopathy 45", "MONDO:9900004"],
  ["GNAO1", "HGNC:00000005", "developmental and epileptic encephalopathy 17", "MONDO:9900005"],
] as const;
const refs = genes.map(([gene_symbol, gene_id, name, id]) => ({ id, name, gene_id, gene_symbol }));
const safe = (value: string) => value.replaceAll(":", "_").replaceAll("/", "_");
type FixtureLedgerRow = { edge_id: string; contradicted_by: string[]; [key: string]: unknown };
type FixtureDisease = { id: string; name: string; gene_id: string; gene_symbol: string };
const allEdges = new Map<string, FixtureLedgerRow>();
const edge = (id: string, subject: string, subject_label: string, predicate: string, object: string, object_label: string, evidence_class: string, source: string, quote: string | null = null, extra: Record<string, unknown> = {}) => {
  const row = {
    edge_id: id, subject, subject_label, predicate, object, object_label, evidence_class,
    source, source_record: object, source_url: source === "ClinicalTrials.gov" ? "https://clinicaltrials.gov/study/NCT06555965" : "https://example.org/evidence",
    retrieved_at: today, confidence: 0.88, quote, polarity: "supports", contradicted_by: [], method: "fixture evidence graph", schema_version: 1, properties: {}, ...extra,
  };
  allEdges.set(id, row);
  return id;
};
const ePhen1 = edge("edge:hp:stxbp1-0001", stx.id, stx.name, "has_phenotype", "HP:99000001", "Early-onset seizures", "observed", "HPO", "Early-onset seizures are recorded in the snapshot.");
const ePhen2 = edge("edge:hp:stxbp1-0002", stx.id, stx.name, "has_phenotype", "HP:99000002", "Developmental delay", "curated", "HPO", "Developmental delay is recorded in the snapshot.");
const eVariant = edge("edge:variant:stxbp1-0001", stx.gene_id, stx.gene_symbol, "has_variant_class", "constellation:variant/class-fixture", "Loss of function", "inferred", "ClinVar", "Illustrative variant classification.");
const eTrial = edge("edge:trial:nct06555965", "NCT06555965", "STXBP1 and SYNGAP1 Related Disorders Natural History Study", "studies", stx.id, stx.name, "observed", "ClinicalTrials.gov", "Natural history study record for STXBP1 and SYNGAP1 related disorders.");
const eOrg = edge("edge:org:stxbp1-foundation", "constellation:org/stxbp1-foundation-fixture", "STXBP1 Foundation", "supports", stx.id, stx.name, "web_discovered", "Web search", "Organization result associated with the disease.");
const contradicted = edge("edge:smw:stxbp1-dnm1", stx.id, stx.name, "shares_mechanism_with", refs[0].id, refs[0].name, "curated", "STRING fixture", "Both disorders implicate presynaptic vesicle cycling.", {
  contradicted_by: ["edge:claim:dnm1-class-contradiction"],
});
edge("edge:claim:dnm1-class-contradiction", refs[0].gene_id, "DNM1", "claims", "constellation:variant/class-conflicting-fixture", "Dominant-negative variant class", "llm_extracted", "Curated literature review", "A limited claim assigns a dominant-negative effect; expert review is needed.", {
  polarity: "contradicts", source_url: "https://example.org/contradiction",
});
const eCluster = edge("edge:smw:stxbp1-snap25", stx.id, stx.name, "shares_mechanism_with", refs[1].id, refs[1].name, "observed", "STRING fixture", "Shared presynaptic vesicle-cycle mechanism.");
const eCluster2 = edge("edge:smw:stxbp1-stx1b", stx.id, stx.name, "shares_mechanism_with", refs[2].id, refs[2].name, "inferred", "Reactome fixture", "Shared SNARE complex assembly pathway.");
const eCluster3 = edge("edge:smw:stxbp1-vamp2", stx.id, stx.name, "shares_mechanism_with", refs[3].id, refs[3].name, "web_discovered", "Reactome fixture", "Shared presynaptic vesicle-cycle pathway.");
const ePartial = edge("edge:partial:stxbp1-dnm1", stx.id, stx.name, "partial_overlap_with", refs[0].id, refs[0].name, "curated", "go", "GO:0016050 vesicle organization is a weak shared term; DNM1 acts at endocytosis, apart from the SNARE exocytosis core.");
const eCounter = edge("edge:counter:stxbp1-gnao1", stx.id, stx.name, "phenotypically_similar_to", refs[4].id, refs[4].name, "inferred", "Phenotype similarity fixture", "Phenotype overlap without the mechanism layer.");
const eGapPhen = edge("edge:hp:frrs1l-0001", gap.id, gap.name, "has_phenotype", "HP:99000003", "Hypotonia", "observed", "HPO", "Illustrative FRRS1L phenotype record.");
const eGapVariant = edge("edge:variant:frrs1l-0001", gap.gene_id, gap.gene_symbol, "has_variant_class", "constellation:variant/frrs1l-uncertain-fixture", "Uncertain functional effect", "curated", "ClinVar", "Illustrative FRRS1L variant-class evidence.");
const eGapMechanism = edge("edge:mechanism:frrs1l-0001", gap.id, gap.name, "candidate_mechanism", "R-HSA-0000001", "Synaptic vesicle cycle", "inferred", "Reactome fixture", "A candidate pathway relationship without replicated mechanism evidence.");
const eGapLead1 = edge("edge:lead:frrs1l-dnm1", gap.id, gap.name, "phenotypically_similar_to", refs[0].id, refs[0].name, "inferred", "Phenotype similarity fixture", "Illustrative phenotype overlap for a nearest lead.");
const eGapLead2 = edge("edge:lead:frrs1l-snap25", gap.id, gap.name, "phenotypically_similar_to", refs[1].id, refs[1].name, "observed", "Phenotype similarity fixture", "Illustrative phenotype overlap for a nearest lead.");
const eGapLead3 = edge("edge:lead:frrs1l-stx1b", gap.id, gap.name, "phenotypically_similar_to", refs[2].id, refs[2].name, "web_discovered", "Phenotype similarity fixture", "Illustrative phenotype overlap for a nearest lead.");
const edgeIds = [ePhen1, ePhen2, eVariant, eTrial, eOrg, contradicted, "edge:claim:dnm1-class-contradiction", eCluster, eCluster2, eCluster3, ePartial, eCounter, eGapPhen, eGapVariant, eGapMechanism, eGapLead1, eGapLead2, eGapLead3];
const sentence = (text: string, ids: string[] = [contradicted]) => ({ text, edge_ids: ids });
const disease = {
  _fixture: true,
  disease: { ...stx, synonyms: ["DEE4", "Munc18-1-related disorder"], omim: null, orphacode: null },
  counts: {
    phenotypes: { n: 48, edge_ids: [ePhen1, ePhen2] }, pathogenic_variants: { n: 26, edge_ids: [eVariant] },
    trials: { n: 1, edge_ids: [eTrial] }, awards_active: { n: 3, edge_ids: [eVariant] },
    papers: { n: 128, edge_ids: [contradicted, eCluster, eCluster2] }, patient_groups: { n: 1, edge_ids: [eOrg] },
  },
  patient_groups: [{ id: "constellation:org/stxbp1-foundation-fixture", name: "STXBP1 Foundation", url: "https://example.org/stxbp1-foundation", edge_id: eOrg, evidence_class: "web_discovered", match_kind: "exact" }],
  summary: {
    technical: [sentence("STXBP1-related disease shares a presynaptic vesicle-cycle mechanism with several candidate disorders.", [contradicted, eCluster2]), sentence("The DNM1 variant-class interpretation remains contested and needs expert review.", ["edge:claim:dnm1-class-contradiction"])],
    plain: [sentence("Several related conditions affect the same tiny communication points between brain cells.", [contradicted, eCluster2]), sentence("One difference needs a closer expert check before it is treated as settled.", ["edge:claim:dnm1-class-contradiction"])],
  },
  is_gap: false,
};
const gapDisease = {
  _fixture: true,
  disease: { ...gap, synonyms: ["DEE37"], omim: null, orphacode: null },
  counts: {
    phenotypes: { n: 6, edge_ids: [eGapPhen] }, pathogenic_variants: { n: 4, edge_ids: [eGapVariant] },
    trials: { n: 0, edge_ids: [] }, awards_active: { n: 0, edge_ids: [] },
    papers: { n: 14, edge_ids: [eGapMechanism] }, patient_groups: { n: 0, edge_ids: [] },
  },
  patient_groups: [],
  summary: { technical: [sentence("The available snapshot contains limited evidence for FRRS1L-related disease.", [eGapPhen])], plain: [sentence("There is not enough connected evidence in this snapshot to suggest a supported path.", [eGapPhen])] },
  is_gap: true,
};
const pathway = { id: "R-HSA-0000001", name: "Synaptic vesicle cycle" };
const pathway2 = { id: "R-HSA-0000002", name: "SNARE complex assembly" };
const neighbor = (i: number, P: number, M: number, V: number, id: string, mechanismEdge: string) => ({
  disease: refs[i], P, M, M_reactome: M, M_go: 0, string_mech: 0.96, V,
  S: Number((0.5 * P + 0.3 * M + 0.2 * V).toFixed(3)), supported: M >= 0.25,
  mechanism_label: "presynaptic vesicle cycle / SNARE", shared_pathways: [pathway, pathway2], string_score: 0.86 - i * 0.06,
  variant_class_a: "loss of function", variant_class_b: i === 0 ? "dominant-negative (contested)" : "loss of function",
  edge_id: mechanismEdge, layer_edge_ids: { P: [ePhen1], M: [mechanismEdge], V: [eVariant] },
});
const n1 = {
  disease: refs[0], P: 0.806, M: 0.026, M_reactome: 0, M_go: 0.026, string_mech: 0.08, V: 0,
  S: 0.411, supported: false, mechanism_label: "vesicle organization (GO:0016050)",
  shared_pathways: [{ id: "GO:0016050", name: "vesicle organization" }], string_score: 0.08,
  variant_class_a: "loss of function", variant_class_b: "uncertain",
  edge_id: ePartial, layer_edge_ids: { P: [ePhen1], M: [ePartial], V: [] }, excluded_by: "M",
};
const n2 = neighbor(1, 0.68, 0.84, 0.73, refs[1].id, eCluster);
const n3 = neighbor(2, 0.63, 0.81, 0.72, refs[2].id, eCluster2);
const n4 = neighbor(3, 0.60, 0.77, 0.69, refs[3].id, eCluster3);
const counter = {
  disease: refs[4], P: 0.72, M: 0, V: 0.30, S: 0.42, supported: false, mechanism_label: "symptom similarity only",
  shared_pathways: [], string_score: null, variant_class_a: "loss of function", variant_class_b: "uncertain",
  edge_id: eCounter, layer_edge_ids: { P: [ePhen1], M: [], V: [eVariant] }, excluded_by: "M",
};
const cluster = {
  _fixture: true, disease_id: stx.id,
  cluster: { id: "constellation:cluster/1", label: "Presynaptic vesicle cycle (SNARE)", resolution: 1, seed: 42, stability: 0.86, member_ids: [stx.id, ...refs.slice(1, 4).map((r) => r.id)] },
  neighbours: [n2, n3, n4], partial_overlaps: [n1], slice_diseases: [stx, ...refs, gap], counterexample: counter, nearest_leads: [],
  weights: { P: 0.5, M: 0.3, V: 0.2, threshold: 0.45 },
};
const gapLayers = [[0.7, 0.16, 0.15], [0.66, 0.13, 0.25], [0.61, 0.21, 0.2]];
const gapLeads = refs.slice(0, 3).map((diseaseRef, i) => ({
  disease: diseaseRef, P: gapLayers[i][0], M: gapLayers[i][1], V: gapLayers[i][2],
  S: Number((0.5 * gapLayers[i][0] + 0.3 * gapLayers[i][1] + 0.2 * gapLayers[i][2]).toFixed(3)), supported: false, mechanism_label: "candidate pathway overlap",
  shared_pathways: [pathway], string_score: null, variant_class_a: "unknown", variant_class_b: "unknown",
  edge_id: [eGapLead1, eGapLead2, eGapLead3][i], layer_edge_ids: { P: [eGapPhen], M: [], V: [eGapVariant] },
}));
const gapCluster = {
  _fixture: true, disease_id: gap.id, cluster: null, neighbours: [], partial_overlaps: [], slice_diseases: [stx, ...refs, gap], counterexample: null,
  nearest_leads: gapLeads, weights: { P: 0.5, M: 0.3, V: 0.2, threshold: 0.45 },
};
const coverage = {
  _fixture: true, disease_id: gap.id, is_gap: true,
  reasons: ["No candidate reaches the mechanism threshold in the committed snapshot.", "The functional variant-effect layer remains sparse."],
  sources: [
    { source: "PubMed", query: "FRRS1L developmental epileptic encephalopathy", count: 14, retrieved_at: today },
    { source: "ClinicalTrials.gov", query: "FRRS1L", count: 0, retrieved_at: today },
    { source: "NIH RePORTER", query: "FRRS1L", count: 0, retrieved_at: today },
    { source: "ClinVar", query: "FRRS1L pathogenic variant", count: 4, retrieved_at: today },
    { source: "HPO", query: "FRRS1L-associated phenotypes", count: 6, retrieved_at: today },
    { source: "Web search", query: "FRRS1L patient group", count: 0, retrieved_at: today },
  ],
  nearest_leads: gapLeads.map((lead, i) => ({ disease: lead.disease, S: lead.S, missing_layer: (["M", "V", "people"] as const)[i] })),
  what_would_change: [
    { layer: "V", text: "A functional study classifying the FRRS1L variant effect (loss of function vs other) would populate the variant-effect layer; source: a reproducible functional assay.", edge_ids: [eGapVariant] },
    { layer: "M", text: "A replicated mechanism study linking FRRS1L to a shared pathway would strengthen the mechanism layer.", edge_ids: [eGapMechanism] },
  ],
};
const supportedCoverage = {
  _fixture: true, disease_id: stx.id, is_gap: false, reasons: [],
  sources: [
    { source: "PubMed", query: "STXBP1 mechanism", count: 128, retrieved_at: today },
    { source: "ClinicalTrials.gov", query: "STXBP1", count: 1, retrieved_at: today },
  ],
  nearest_leads: [], what_would_change: [],
};
const naturalAsset = {
  id: "constellation:asset/nct06555965-fixture", name: "STXBP1 and SYNGAP1 Related Disorders Natural History Study",
  asset_type: "natural_history_study", url: "https://clinicaltrials.gov/study/NCT06555965", record_id: "NCT06555965",
  serves: [stx, { id: "MONDO:9900006", name: "SYNGAP1-related disorder", gene_id: "HGNC:00000006", gene_symbol: "SYNGAP1" }],
  coverage: { value: 0.78, numerator_ic: 41.2, denominator_ic: 52.8, n_matched: 18, n_total: 24,
    matched: Array.from({ length: 18 }, (_, i) => ({ id: `HP:99000${String(i + 1).padStart(3, "0")}`, label: ["Early-onset seizures", "Developmental delay", "Hypotonia", "Speech impairment"][i % 4] })),
    unmatched: Array.from({ length: 6 }, (_, i) => ({ id: `HP:99001${String(i + 1).padStart(3, "0")}`, label: ["Sleep disturbance", "Feeding difficulty", "Movement disorder"][i % 3] })) },
  eligibility_diff: [
    { field: "Age at enrollment", asset_value: "Birth–18 years", target_value: "Birth–18 years", status: "matches" },
    { field: "Molecular diagnosis", asset_value: "STXBP1 or SYNGAP1", target_value: "STXBP1", status: "matches" },
    { field: "Study visit schedule", asset_value: "Annual", target_value: "Flexible", status: "differs" },
    { field: "Prior treatment", asset_value: "Recorded", target_value: "Not specified", status: "needs_expert_review" },
    { field: "Outcome measures", asset_value: "Development and seizure history", target_value: "Development and seizure history", status: "matches" },
    { field: "Geographic access", asset_value: "Multiple sites", target_value: "Local access", status: "needs_expert_review" },
  ], edge_ids: [eTrial],
};
const animalAsset = {
  id: "constellation:asset/model-fixture", name: "Illustrative synaptic vesicle model resource", asset_type: "animal_model",
  url: "https://example.org/model-resource", record_id: "MODEL-0000001", serves: [refs[0]], coverage: null,
  eligibility_diff: [{ field: "Genetic background", asset_value: "Model strain 1", target_value: "Target strain", status: "needs_expert_review" }], edge_ids: [contradicted],
};
const assets = { _fixture: true, cluster_id: "constellation:cluster/1", for_disease: refs[0], assets: [naturalAsset, animalAsset] };
const bridges = {
  _fixture: true, a: stx, b: refs[0], unverified_name_matches: 0,
  people: [
    { id: "constellation:person/investigator-a", display_name: "Investigator A", affiliations: ["Institution 1"], roles: ["author", "pi"], n_a: 3, n_b: 2, score: 0.91,
      proving_edges: [
        { edge_id: contradicted, kind: "paper", record_id: "PMID:00000001", title: "Vesicle cycling across related disorders", year: 2024, url: "https://example.org/paper-a", disease_id: stx.id, disease_ids: [stx.id, refs[0].id] },
        { edge_id: eCluster, kind: "award", record_id: "AWARD-0000001", title: "Shared synaptic mechanisms", year: 2023, url: "https://example.org/award-a", disease_id: refs[0].id, disease_ids: [stx.id, refs[0].id] },
      ], why_same_person: "The same de-identified author record occurs in publications linked to both disease sides." },
    { id: "constellation:person/investigator-b", display_name: "Investigator B", affiliations: ["Institution 2"], roles: ["study_official"], n_a: 2, n_b: 1, score: 0.78,
      proving_edges: [
        { edge_id: eTrial, kind: "study", record_id: "NCT06555965", title: "STXBP1 and SYNGAP1 Related Disorders Natural History Study", year: 2025, url: "https://clinicaltrials.gov/study/NCT06555965", disease_id: stx.id, disease_ids: [stx.id, refs[0].id] },
        { edge_id: contradicted, kind: "paper", record_id: "PMID:00000002", title: "A fixture study of DNM1", year: 2022, url: "https://example.org/paper-b", disease_id: refs[0].id, disease_ids: [stx.id, refs[0].id] },
      ], why_same_person: "A de-identified investigator affiliation recurs across a study and a disease-linked paper." },
  ],
};
const searchHit = (id: string, label: string, matched_text: string, match_kind: string, diseaseRef: FixtureDisease, type = "gene") => ({ id, type, label, matched_text, match_kind, disease: diseaseRef });
const searchStx = { _fixture: true, query: "", best: null, results: [
  searchHit(stx.gene_id, "STXBP1", "STXBP1", "symbol", stx),
  searchHit(stx.id, stx.name, "Munc18-1", "synonym", stx, "disease"),
  searchHit(stx.id, stx.name, "DEE4", "synonym", stx, "disease"),
  searchHit("constellation:org/stxbp1-foundation-fixture", "STXBP1 Foundation", "STXBP1 Foundation", "org", stx, "patient_group"),
] };
const searchGap = { _fixture: true, query: "", best: null, results: [searchHit(gap.gene_id, "FRRS1L", "FRRS1L", "symbol", gap)] };
const health = { _fixture: true, status: "ok", snapshot_hash: "fixture-2025-02-14", llm_mode: "cached", llm_available: false, agent_model: "gpt-5", counts: { nodes: 274, edges: 812 } };
const mechanism = { _fixture: true, query: "", clusters: [{ id: "constellation:cluster/1", label: "Presynaptic vesicle cycle (SNARE)", score: 0.91, member_ids: [stx.id, ...refs.slice(0, 4).map((r) => r.id)], matched_pathways: [pathway, pathway2] }] };
const mkDossier = (persona: string, gapMode = false) => {
  const plain = persona === "devon";
  let sentences = gapMode
    ? [{ key: "coverage", title: "Search coverage", sentences: [sentence("The current snapshot does not support a mechanism-first neighbour for FRRS1L.", [eGapPhen])] }]
    : [
      { key: "who_shares", title: "Who shares our characteristics", sentences: [
        sentence(plain ? "SNAP25, STX1B, and VAMP2 share the same brain-cell communication pathway." : "SNAP25, STX1B, and VAMP2 share the SNARE exocytosis mechanism with STXBP1-related disease.", [eCluster, eCluster2, eCluster3]),
        sentence("GNAO1 is a phenotype-similar counterexample, excluded by the mechanism layer.", [eCounter]),
      ] },
      { key: "what_exists", title: "What already exists", sentences: [
        sentence("The NCT06555965 natural-history study includes STXBP1 and SYNGAP1 and has a measured phenotype coverage score for the selected comparison.", [eTrial, ePhen1]),
        sentence("The STXBP1 Foundation is an exact-match patient community.", [eOrg]),
      ] },
      { key: "what_differs", title: "What differs and must be verified", sentences: [
        sentence("DNM1 is a same process, different step / weak overlap: vesicle organization (GO:0016050) reflects endocytosis, not the cluster's SNARE exocytosis core.", [ePartial]),
        sentence("A dominant-negative DNM1 variant-class claim is contradicted and should be checked with an expert.", ["edge:claim:dnm1-class-contradiction"]),
      ] },
      { key: "who_to_contact", title: "Who to contact", sentences: [sentence("Investigator A appears on evidence linked to both sides; a study or corresponding-author route is a reasonable starting point.", [contradicted, eCluster])] },
      { key: "next_step", title: "Proposed first joint step", sentences: [sentence("Could a shared natural-history protocol compare seizure trajectories using NCT06555965, starting with its phenotype coverage and a verified bridge investigator?", [eTrial, ePhen1, contradicted])] },
      { key: "coverage", title: "Search coverage", sentences: [sentence("This snapshot includes curated, observed, inferred and web-discovered evidence; two low-support sentences were omitted.", [ePhen1])] },
    ];
  if (!gapMode && persona === "priya") {
    sentences = [
      { ...sentences[0], sentences: [
        sentence("Mechanism first: SNAP25, STX1B, and VAMP2 share SNARE exocytosis with STXBP1; see Reactome terms R-HSA-0000001 and R-HSA-0000002.", [eCluster, eCluster2, eCluster3]),
        sentence("GNAO1 is a phenotype-similar counterexample excluded by M=0.", [eCounter]),
      ] },
      sentences[2],
      sentences[1],
      ...sentences.slice(3),
    ];
  } else if (!gapMode && persona === "osei") {
    sentences = [sentences[1], sentences[2], sentences[3], sentences[0], ...sentences.slice(4)]
      .map((section) => ({ ...section, sentences: section.sentences.map((item) => sentence(`Verify: ${item.text} This comparison is hypothesis-generating and does not establish clinical equivalence.`, item.edge_ids)) }));
  } else if (!gapMode && persona === "devon") {
    sentences = sentences.map((section) => ({ ...section, sentences: section.sentences.map((item) => sentence(item.text, item.edge_ids)) }));
  }
  const markdown = sentences.map((section) => `## ${section.title}\n\n${section.sentences.map((s, i) => `${s.text} [^${i + 1}]`).join("\n\n")}`).join("\n\n");
  return { _fixture: true, disease_id: gapMode ? gap.id : stx.id, persona, mode: "cached", snapshot_hash: health.snapshot_hash,
    trace_id: `trace:fixture:${persona}`, generated_at: "2025-02-14T12:00:00Z", sections: sentences, dropped_sentences: gapMode ? 0 : 2,
    gap_plan: gapMode ? { reasons: coverage.reasons, what_would_change: coverage.what_would_change } : null, markdown };
};
const files: Record<string, unknown> = {
  "health.json": health,
  "search/MONDO_0012812.json": searchStx,
  "search/MONDO_0014859.json": searchGap,
  "disease/MONDO_0012812.json": disease,
  "disease/MONDO_0014859.json": gapDisease,
  "cluster/MONDO_0012812.json": cluster,
  "cluster/MONDO_0014859.json": gapCluster,
  "coverage/MONDO_0014859.json": coverage,
  "coverage/MONDO_0012812.json": supportedCoverage,
  "assets/constellation_cluster_1__MONDO_9900001.json": assets,
  "bridges/MONDO_0012812__MONDO_9900001.json": bridges,
  "mechanism-search.json": mechanism,
};
for (const persona of ["maria", "devon", "priya", "osei"]) {
  files[`dossier/MONDO_0012812__${persona}.json`] = mkDossier(persona);
  files[`dossier/MONDO_0014859__${persona}.json`] = mkDossier(persona, true);
}
for (const id of edgeIds) {
  const row = allEdges.get(id);
  files[`edge/${safe(id)}.json`] = { _fixture: true, ...row, contradicting: row.contradicted_by.map((related: string) => allEdges.get(related)).filter(Boolean) };
}
await rm(root, { recursive: true, force: true });
for (const [relative, data] of Object.entries(files)) {
  const target = path.join(root, relative);
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, `${JSON.stringify(data, null, 2)}\n`);
}
console.log(`Generated ${Object.keys(files).length} fixture files (${allEdges.size} ledger rows).`);
