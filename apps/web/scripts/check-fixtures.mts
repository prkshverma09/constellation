import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";

const root = path.resolve("fixtures");
async function listJson(dir: string): Promise<string[]> {
  const entries = await readdir(dir, { withFileTypes: true });
  const files = await Promise.all(entries.map((entry) => {
    const file = path.join(dir, entry.name);
    return entry.isDirectory() ? listJson(file) : entry.name.endsWith(".json") ? [file] : [];
  }));
  return files.flat();
}
const files = await listJson(root);
const docs = await Promise.all(files.map(async (file) => [file, JSON.parse(await readFile(file, "utf8"))] as const));
const safe = (value: string) => value.replaceAll(":", "_").replaceAll("/", "_");
const edgeFiles = new Set(files.filter((file) => file.includes(`${path.sep}edge${path.sep}`)).map((file) => path.basename(file, ".json")));
const referenced = new Set<string>();
const collect = (value: unknown) => {
  if (Array.isArray(value)) { value.forEach(collect); return; }
  if (!value || typeof value !== "object") return;
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    if (key === "edge_id" && typeof item === "string") referenced.add(item);
    else if ((key === "edge_ids" || key === "contradicted_by") && Array.isArray(item)) item.filter((id): id is string => typeof id === "string").forEach((id) => referenced.add(id));
    collect(item);
  }
};
for (const [file, value] of docs) {
  assert.equal((value as Record<string, unknown>)._fixture, true, `${file} must have _fixture: true`);
  collect(value);
  if (file.includes(`${path.sep}dossier${path.sep}`)) {
    const dossier = value as { sections?: { sentences?: { text: string; edge_ids: string[] }[] }[]; gap_plan?: { what_would_change: { edge_ids: string[] }[] } | null };
    for (const section of dossier.sections ?? []) for (const sentence of section.sentences ?? []) assert.ok(sentence.edge_ids.length > 0, `Dossier sentence missing evidence: ${sentence.text}`);
    for (const item of dossier.gap_plan?.what_would_change ?? []) assert.ok(item.edge_ids.length > 0, "Gap plan sentence missing evidence");
  }
  if (file.includes(`${path.sep}disease${path.sep}`)) {
    const disease = value as { counts: Record<string, { edge_ids: string[] }> };
    for (const [name, count] of Object.entries(disease.counts)) assert.ok(count.edge_ids.length <= 50, `${name} exceeds 50 edge ids`);
  }
}
for (const id of referenced) assert.ok(edgeFiles.has(safe(id)), `Missing edge fixture for ${id}`);
type CheckRow = { disease: { gene_symbol: string }; P: number; M: number; V: number; S: number; supported: boolean };
type CheckCluster = { weights: { P: number; M: number; V: number; threshold: number }; neighbours: CheckRow[]; counterexample: (CheckRow & { excluded_by: string }) | null; nearest_leads: CheckRow[] };
const checkCluster = (cluster: CheckCluster, file: string) => {
  const rows = [...cluster.neighbours, ...(cluster.counterexample ? [cluster.counterexample] : []), ...cluster.nearest_leads];
  for (const row of rows) {
    const expected = 0.5 * row.P + 0.3 * row.M + 0.2 * row.V;
    assert.ok(Math.abs(expected - row.S) <= 0.005, `${file}: ${row.disease.gene_symbol} fused score mismatch: ${expected} != ${row.S}`);
    assert.equal(row.supported, row.S >= cluster.weights.threshold, `${file}: supported flag disagrees with S for ${row.disease.gene_symbol}`);
  }
  for (let index = 1; index < cluster.neighbours.length; index += 1) assert.ok(cluster.neighbours[index - 1].S >= cluster.neighbours[index].S, `${file}: neighbours not sorted by S`);
  if (cluster.counterexample) {
    assert.equal(cluster.counterexample.excluded_by, "M", `${file}: counterexample should be excluded by M`);
    assert.equal(cluster.counterexample.M, 0, `${file}: counterexample M must be zero`);
    assert.ok(cluster.counterexample.S < cluster.weights.threshold, `${file}: counterexample must be under threshold`);
  }
  for (const lead of cluster.nearest_leads) assert.ok(lead.S < cluster.weights.threshold, `${file}: nearest lead must be unsupported`);
};
for (const [file, value] of docs) if (file.includes(`${path.sep}cluster${path.sep}`)) checkCluster(value, file);
console.log(`Fixture check passed: ${files.length} files, ${edgeFiles.size} edge fixtures, ${referenced.size} referenced edge IDs.`);
