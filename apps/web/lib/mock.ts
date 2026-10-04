import { ApiError, type AssetsResponse, type BridgesResponse, type ClusterResponse, type ContributeRequest, type ContributeResponse, type CoverageResponse, type DiseaseResponse, type Dossier, type EdgeResponse, type HealthResponse, type MechanismSearchResponse, type Persona, type SearchHit, type SearchResponse } from "./api";

type Fixture = { default: Record<string, unknown> };
const fixtures: Record<string, () => Promise<Fixture>> = {
  health: () => import("../fixtures/health.json"),
  "search:MONDO:0012812": () => import("../fixtures/search/MONDO_0012812.json"),
  "search:MONDO:0014859": () => import("../fixtures/search/MONDO_0014859.json"),
  "disease:MONDO:0012812": () => import("../fixtures/disease/MONDO_0012812.json"),
  "disease:MONDO:0014859": () => import("../fixtures/disease/MONDO_0014859.json"),
  "cluster:MONDO:0012812": () => import("../fixtures/cluster/MONDO_0012812.json"),
  "cluster:MONDO:0014859": () => import("../fixtures/cluster/MONDO_0014859.json"),
  "coverage:MONDO:0014859": () => import("../fixtures/coverage/MONDO_0014859.json"),
  "coverage:MONDO:0012812": () => import("../fixtures/coverage/MONDO_0012812.json"),
  "assets:constellation:cluster/1:MONDO:9900001": () => import("../fixtures/assets/constellation_cluster_1__MONDO_9900001.json"),
  "bridges:MONDO:0012812:MONDO:9900001": () => import("../fixtures/bridges/MONDO_0012812__MONDO_9900001.json"),
  "dossier:MONDO:0012812:maria": () => import("../fixtures/dossier/MONDO_0012812__maria.json"),
  "dossier:MONDO:0012812:devon": () => import("../fixtures/dossier/MONDO_0012812__devon.json"),
  "dossier:MONDO:0012812:priya": () => import("../fixtures/dossier/MONDO_0012812__priya.json"),
  "dossier:MONDO:0012812:osei": () => import("../fixtures/dossier/MONDO_0012812__osei.json"),
  "dossier:MONDO:0014859:maria": () => import("../fixtures/dossier/MONDO_0014859__maria.json"),
  "dossier:MONDO:0014859:devon": () => import("../fixtures/dossier/MONDO_0014859__devon.json"),
  "dossier:MONDO:0014859:priya": () => import("../fixtures/dossier/MONDO_0014859__priya.json"),
  "dossier:MONDO:0014859:osei": () => import("../fixtures/dossier/MONDO_0014859__osei.json"),
  mechanism: () => import("../fixtures/mechanism-search.json"),
  "edge:edge_hp_stxbp1-0001": () => import("../fixtures/edge/edge_hp_stxbp1-0001.json"),
  "edge:edge_hp_stxbp1-0002": () => import("../fixtures/edge/edge_hp_stxbp1-0002.json"),
  "edge:edge_variant_stxbp1-0001": () => import("../fixtures/edge/edge_variant_stxbp1-0001.json"),
  "edge:edge_trial_nct06555965": () => import("../fixtures/edge/edge_trial_nct06555965.json"),
  "edge:edge_org_stxbp1-foundation": () => import("../fixtures/edge/edge_org_stxbp1-foundation.json"),
  "edge:edge_smw_stxbp1-dnm1": () => import("../fixtures/edge/edge_smw_stxbp1-dnm1.json"),
  "edge:edge_claim_dnm1-class-contradiction": () => import("../fixtures/edge/edge_claim_dnm1-class-contradiction.json"),
  "edge:edge_smw_stxbp1-snap25": () => import("../fixtures/edge/edge_smw_stxbp1-snap25.json"),
  "edge:edge_smw_stxbp1-stx1b": () => import("../fixtures/edge/edge_smw_stxbp1-stx1b.json"),
  "edge:edge_smw_stxbp1-vamp2": () => import("../fixtures/edge/edge_smw_stxbp1-vamp2.json"),
  "edge:edge_counter_stxbp1-gnao1": () => import("../fixtures/edge/edge_counter_stxbp1-gnao1.json"),
  "edge:edge_hp_frrs1l-0001": () => import("../fixtures/edge/edge_hp_frrs1l-0001.json"),
  "edge:edge_variant_frrs1l-0001": () => import("../fixtures/edge/edge_variant_frrs1l-0001.json"),
  "edge:edge_mechanism_frrs1l-0001": () => import("../fixtures/edge/edge_mechanism_frrs1l-0001.json"),
  "edge:edge_lead_frrs1l-dnm1": () => import("../fixtures/edge/edge_lead_frrs1l-dnm1.json"),
  "edge:edge_lead_frrs1l-snap25": () => import("../fixtures/edge/edge_lead_frrs1l-snap25.json"),
  "edge:edge_lead_frrs1l-stx1b": () => import("../fixtures/edge/edge_lead_frrs1l-stx1b.json"),
};
const delay = (ms = 150) => new Promise((resolve) => setTimeout(resolve, ms));
async function load<T>(key: string): Promise<T> {
  const importer = fixtures[key];
  if (!importer) throw new ApiError("Not found in snapshot", 404);
  await delay();
  const loaded = await importer();
  return loaded.default as T;
}
const stxId = "MONDO:0012812";
const gapId = "MONDO:0014859";
export async function mockHealth() { return load<HealthResponse>("health"); }
export async function mockSearch(q: string): Promise<SearchResponse> {
  const trimmed = q.trim();
  if (!trimmed) return { query: q, best: null, results: [] };
  const candidates = [await load<{ results: SearchHit[] }>(`search:${stxId}`), await load<{ results: SearchHit[] }>(`search:${gapId}`)]
    .flatMap((x) => x.results);
  const lower = trimmed.toLocaleLowerCase();
  const results = candidates.filter((hit) => hit.matched_text.toLocaleLowerCase().includes(lower) || hit.label.toLocaleLowerCase().includes(lower));
  const best = results.find((hit) => hit.matched_text.toLocaleLowerCase() === lower) ?? results[0] ?? null;
  return { query: trimmed, best, results };
}
export async function mockDisease(id: string) {
  if (id !== stxId && id !== gapId) throw new ApiError("Not found in snapshot", 404);
  return load<DiseaseResponse>(`disease:${id}`);
}
export async function mockCluster(id: string) {
  if (id !== stxId && id !== gapId) throw new ApiError("Not found in snapshot", 404);
  return load<ClusterResponse>(`cluster:${id}`);
}
export async function mockEdge(id: string) {
  const safe = id.replaceAll(":", "_").replaceAll("/", "_");
  return load<EdgeResponse>(`edge:${safe}`);
}
export async function mockAssets(clusterId: string, diseaseId: string) {
  const fixture = await load<AssetsResponse>("assets:constellation:cluster/1:MONDO:9900001");
  const cluster = await mockCluster(stxId);
  return {
    ...fixture,
    cluster_id: clusterId,
    for_disease: cluster.slice_diseases.find((disease) => disease.id === diseaseId) ?? fixture.for_disease,
  };
}
export async function mockBridges(a: string, b: string) {
  const fixture = await load<BridgesResponse>("bridges:MONDO:0012812:MONDO:9900001");
  const cluster = await mockCluster(stxId);
  return {
    ...fixture,
    a: cluster.slice_diseases.find((disease) => disease.id === a) ?? fixture.a,
    b: cluster.slice_diseases.find((disease) => disease.id === b) ?? fixture.b,
    people: fixture.people.map((person) => ({
      ...person,
      proving_edges: person.proving_edges.map((proof) => ({
        ...proof,
        disease_id: proof.disease_id === stxId ? a : b,
        disease_ids: [a, b],
      })),
    })),
  };
}
export async function mockCoverage(id: string) {
  if (id !== gapId && id !== stxId) throw new ApiError("Not found in snapshot", 404);
  return load<CoverageResponse>(`coverage:${id}`);
}
export async function mockDossier(disease: string, persona: Persona) {
  if (disease !== stxId && disease !== gapId) throw new ApiError("Not found in snapshot", 404);
  return load<Dossier>(`dossier:${disease}:${persona}`);
}
export async function mockMechanismSearch(q: string) {
  const fixture = await load<MechanismSearchResponse>("mechanism");
  return { ...fixture, query: q };
}
let proposedCount = 0;
const pending = new Map<string, ContributeResponse[]>();
export async function mockContribute(body: ContributeRequest): Promise<ContributeResponse> {
  await delay();
  proposedCount += 1;
  const today = new Date().toISOString().slice(0, 10);
  const row: ContributeResponse = {
    edge_id: `edge:proposed:${proposedCount}`, subject: body.edge_id, subject_label: body.edge_id,
    predicate: body.polarity, object: body.url, object_label: body.sentence, evidence_class: "proposed",
    source: "user_contribution", source_record: body.url, source_url: body.url, retrieved_at: today,
    confidence: 0.5, quote: body.sentence, polarity: body.polarity, contradicted_by: [], method: null,
    schema_version: 1, properties: {},
  };
  const rows = pending.get(body.edge_id) ?? [];
  pending.set(body.edge_id, [...rows, row]);
  return row;
}
export function getPendingContributions(edgeId: string) { return pending.get(edgeId) ?? []; }
