export type EvidenceClass = "observed" | "curated" | "inferred" | "llm_extracted" | "web_discovered" | "proposed";
export type Polarity = "supports" | "contradicts" | null;
export type Persona = "maria" | "devon" | "priya" | "osei";
export type DiseaseRef = { id: string; name: string; gene_id: string; gene_symbol: string };
export type LedgerRow = {
  edge_id: string;
  subject: string;
  subject_label: string;
  predicate: string;
  object: string;
  object_label: string;
  evidence_class: EvidenceClass;
  source: string;
  source_record: string;
  source_url: string | null;
  retrieved_at: string;
  confidence: number;
  quote: string | null;
  polarity: Polarity;
  contradicted_by: string[];
  method: string | null;
  schema_version: number;
  properties: Record<string, unknown>;
};
export type Sentence = { text: string; edge_ids: string[] };
export type SearchHit = {
  id: string; type: "disease" | "gene" | "phenotype" | "patient_group" | "mechanism";
  label: string; matched_text: string; match_kind: "id" | "label" | "synonym" | "symbol" | "alias" | "org";
  disease: DiseaseRef | null;
};
export type DiseaseResponse = {
  disease: DiseaseRef & { synonyms: string[]; omim: string | null; orphacode: string | null };
  counts: Record<"phenotypes" | "pathogenic_variants" | "trials" | "awards_active" | "papers" | "patient_groups", { n: number; edge_ids: string[] }>;
  patient_groups: { id: string; name: string; url: string; edge_id: string; evidence_class: EvidenceClass; match_kind: "exact" | "umbrella" }[];
  summary: { technical: Sentence[]; plain: Sentence[] };
  is_gap: boolean;
};
export type Neighbour = {
  disease: DiseaseRef; P: number; M: number; M_reactome: number; M_go: number; string_mech: number; V: number; S: number; supported: boolean; mechanism_label: string;
  shared_pathways: { id: string; name: string }[]; string_score: number | null; variant_class_a: string; variant_class_b: string;
  edge_id: string; layer_edge_ids: { P: string[]; M: string[]; V: string[] };
};
export type PartialOverlap = Neighbour & { excluded_by: "M" | "S" };
export type ComparatorOption = {
  disease: DiseaseRef;
  group: "Cluster members" | "Partial overlaps" | "Other slice diseases";
};
export type ClusterResponse = {
  disease_id: string;
  cluster: { id: string; label: string; resolution: number; seed: number; stability: number; member_ids: string[] } | null;
  neighbours: Neighbour[]; partial_overlaps: PartialOverlap[]; slice_diseases: DiseaseRef[];
  counterexample: (Neighbour & { excluded_by: "M" | "V" | "S" }) | null;
  nearest_leads: Neighbour[]; weights: { P: number; M: number; V: number; threshold: number };
};
export type EdgeResponse = LedgerRow & { contradicting: LedgerRow[] };
export type Asset = {
  id: string; name: string; asset_type: "natural_history_study" | "registry" | "biobank" | "animal_model" | "biomarker" | "protocol";
  url: string; record_id: string; serves: DiseaseRef[];
  coverage: { value: number; numerator_ic: number; denominator_ic: number; n_matched: number; n_total: number; matched: { id: string; label: string; match_type: "exact" | "descendant" | "ancestor"; matched_by: { id: string; label: string } }[]; unmatched: { id: string; label: string }[] } | null;
  eligibility_diff: { field: string; asset_value: string; target_value: string; status: "matches" | "differs" | "needs_expert_review" }[];
  eligibility_text: string;
  edge_ids: string[];
};
export type AssetsResponse = { cluster_id: string; for_disease: DiseaseRef; assets: Asset[] };
export type Bridge = {
  id: string; display_name: string; affiliations: string[]; additional_affiliations: string[]; roles: ("author" | "pi" | "study_official")[];
  n_a: number; n_b: number; score: number;
  proving_edges: { edge_id: string; kind: "paper" | "study" | "award"; record_id: string; title: string; year: number | null; url: string; disease_id: string; disease_ids: string[] }[];
  why_same_person: string;
};
export type BridgesResponse = { a: DiseaseRef; b: DiseaseRef; people: Bridge[]; unverified_name_matches: number };
export type CoverageResponse = {
  disease_id: string; is_gap: boolean; reasons: string[];
  sources: { source: string; query: string; count: number; retrieved_at: string }[];
  nearest_leads: { disease: DiseaseRef; S: number; missing_layer: "M" | "V" | "asset" | "people" }[];
  what_would_change: { layer: string; text: string; edge_ids: string[] }[];
};
export type Dossier = {
  disease_id: string; persona: Persona; mode: "live" | "cached" | "offline"; snapshot_hash: string; trace_id: string; generated_at: string;
  sections: { key: "who_shares" | "what_exists" | "what_differs" | "who_to_contact" | "next_step" | "coverage"; title: string; sentences: Sentence[] }[];
  dropped_sentences: number;
  gap_plan: { reasons: string[]; what_would_change: { layer: string; text: string; edge_ids: string[] }[] } | null;
  markdown: string;
};
export type MechanismSearchResponse = { query: string; clusters: { id: string; label: string; score: number; member_ids: string[]; matched_pathways: { id: string; name: string }[] }[] };
export type HealthResponse = { status: "ok"; snapshot_hash: string; llm_mode: "live" | "cached" | "offline"; counts: { nodes: number; edges: number } };
export type ContributeRequest = { url: string; sentence: string; edge_id: string; polarity: "supports" | "contradicts" };
export type ContributeResponse = LedgerRow;
export type SearchResponse = { query: string; best: SearchHit | null; results: SearchHit[] };

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
    this.name = "ApiError";
  }
}

const base = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
  });
  if (!response.ok) throw new ApiError(`Request failed (${response.status})`, response.status);
  return response.json() as Promise<T>;
}

export const api = {
  health: () => base === "mock" ? import("./mock").then((m) => m.mockHealth()) : request<HealthResponse>("/api/health"),
  search: (q: string) => base === "mock" ? import("./mock").then((m) => m.mockSearch(q)) : request<SearchResponse>(`/api/search?q=${encodeURIComponent(q)}`),
  disease: (id: string) => base === "mock" ? import("./mock").then((m) => m.mockDisease(id)) : request<DiseaseResponse>(`/api/disease/${encodeURIComponent(id)}`),
  cluster: (id: string) => base === "mock" ? import("./mock").then((m) => m.mockCluster(id)) : request<ClusterResponse>(`/api/disease/${encodeURIComponent(id)}/cluster`),
  edge: (id: string) => base === "mock" ? import("./mock").then((m) => m.mockEdge(id)) : request<EdgeResponse>(`/api/edge/${encodeURIComponent(id)}`),
  assets: (clusterId: string, diseaseId: string) => base === "mock" ? import("./mock").then((m) => m.mockAssets(clusterId, diseaseId)) : request<AssetsResponse>(`/api/cluster/${encodeURIComponent(clusterId)}/assets?for=${encodeURIComponent(diseaseId)}`),
  bridges: (a: string, b: string) => base === "mock" ? import("./mock").then((m) => m.mockBridges(a, b)) : request<BridgesResponse>(`/api/bridges?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`),
  coverage: (id: string) => base === "mock" ? import("./mock").then((m) => m.mockCoverage(id)) : request<CoverageResponse>(`/api/disease/${encodeURIComponent(id)}/coverage`),
  dossier: (disease: string, persona: Persona, vs?: string) => base === "mock" ? import("./mock").then((m) => m.mockDossier(disease, persona)) : request<Dossier>("/api/dossier", { method: "POST", body: JSON.stringify({ disease, persona, vs }) }),
  mechanismSearch: (q: string) => base === "mock" ? import("./mock").then((m) => m.mockMechanismSearch(q)) : request<MechanismSearchResponse>(`/api/mechanism/search?q=${encodeURIComponent(q)}`),
  contribute: (body: ContributeRequest) => base === "mock" ? import("./mock").then((m) => m.mockContribute(body)) : request<ContributeResponse>("/api/contribute", { method: "POST", body: JSON.stringify(body) }),
  exportKgx: async (): Promise<Blob> => {
    if (base === "mock") throw new ApiError("KGX export is unavailable in fixture mode", 404);
    const response = await fetch(`${base}/api/export/kgx`);
    if (!response.ok) throw new ApiError(`Request failed (${response.status})`, response.status);
    return response.blob();
  },
};
