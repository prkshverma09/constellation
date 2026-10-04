"use client";

import { Suspense, useEffect, useMemo } from "react";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api } from "@/lib/api";
import { AssetCompare } from "@/components/AssetCompare";
import { BridgePeople } from "@/components/BridgePeople";
import { ClusterView } from "@/components/ClusterView";
import { CoverageReport } from "@/components/CoverageReport";
import { EdgeInspector } from "@/components/EdgeInspector";
import { Overview } from "@/components/Overview";
import { ErrorNotice, SectionNav, usePersona, withPersona } from "@/components/Shared";
import type { DiseaseRef } from "@/lib/api";

function DiseaseContent() {
  const params = useParams<{ id: string }>();
  const search = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const persona = usePersona();
  const id = decodeURIComponent(params.id);
  const disease = useQuery({ queryKey: ["disease", id], queryFn: () => api.disease(id) });
  const cluster = useQuery({ queryKey: ["cluster", id], queryFn: () => api.cluster(id), enabled: disease.isSuccess });
  const gap = disease.data?.is_gap ?? false;
  const clusterId = cluster.data?.cluster?.id;
  const comparatorOptions = useMemo(() => {
    if (!cluster.data) return [];
    const members = new Set(cluster.data.cluster?.member_ids ?? []);
    const partials = new Set(cluster.data.partial_overlaps.map((row) => row.disease.id));
    const scores = new Map(cluster.data.neighbours.map((row) => [row.disease.id, row.S]));
    const orderBySymbol = (a: DiseaseRef, b: DiseaseRef) =>
      a.gene_symbol.localeCompare(b.gene_symbol);
    const memberRows = cluster.data.slice_diseases
      .filter((row) => row.id !== id && members.has(row.id))
      .sort((a, b) => (scores.get(b.id) ?? -1) - (scores.get(a.id) ?? -1) || orderBySymbol(a, b))
      .map((disease) => ({ disease, group: "Cluster members" as const }));
    const partialRows = cluster.data.partial_overlaps
      .filter((row) => row.disease.id !== id)
      .map((row) => ({ disease: row.disease, group: "Partial overlaps" as const }));
    const otherRows = cluster.data.slice_diseases
      .filter((row) => row.id !== id && !members.has(row.id) && !partials.has(row.id))
      .sort(orderBySymbol)
      .map((disease) => ({ disease, group: "Other slice diseases" as const }));
    return [...memberRows, ...partialRows, ...otherRows];
  }, [cluster.data, id]);
  const selectedParam = search.get("vs");
  const defaultComparator = comparatorOptions.find((option) => option.group === "Cluster members")?.disease.id
    ?? comparatorOptions[0]?.disease.id
    ?? "";
  const selectedComparator = comparatorOptions.some((option) => option.disease.id === selectedParam)
    ? selectedParam!
    : defaultComparator;
  useEffect(() => {
    if (!selectedComparator || selectedParam === selectedComparator) return;
    const query = new URLSearchParams(search.toString());
    query.set("vs", selectedComparator);
    router.replace(`${pathname}?${query.toString()}`, { scroll: false });
  }, [pathname, router, search, selectedComparator, selectedParam]);
  const dossierHref = `${withPersona(`/d/${encodeURIComponent(id)}/dossier`, persona)}${selectedComparator ? `&vs=${encodeURIComponent(selectedComparator)}` : ""}`;
  const selectComparator = (value: string) => {
    const query = new URLSearchParams(search.toString());
    query.set("vs", value);
    router.replace(`${pathname}?${query.toString()}`, { scroll: false });
  };
  const assets = useQuery({
    queryKey: ["assets", clusterId, selectedComparator],
    queryFn: () => api.assets(clusterId!, selectedComparator),
    enabled: Boolean(disease.data && !gap && clusterId && selectedComparator),
  });
  const bridges = useQuery({
    queryKey: ["bridges", id, selectedComparator],
    queryFn: () => api.bridges(id, selectedComparator),
    enabled: Boolean(disease.data && !gap && selectedComparator),
  });
  const coverage = useQuery({ queryKey: ["coverage", id], queryFn: () => api.coverage(id), enabled: Boolean(disease.data?.is_gap) });
  const edgeId = search.get("edge");
  if (disease.isPending) return <main id="main-content" className="page-shell"><p>Loading disease snapshot…</p></main>;
  if (disease.isError || !disease.data) return <main id="main-content" className="page-shell"><ErrorNotice message="This disease was not found in the current snapshot." /><Link href={withPersona("/", persona)}>Return to search</Link></main>;
  return <main id="main-content" className="page-shell">
    <div className="page-topline"><Link href={withPersona("/", persona)}>← Search</Link><span>Snapshot research view</span></div>
    <SectionNav gap={gap} />
    <Overview disease={disease.data} />
    {gap ? <>
      {coverage.isPending && <section id="coverage" className="section-block loading-card">Loading coverage report…</section>}
      {coverage.data && <CoverageReport data={coverage.data} />}
      {coverage.isError && <ErrorNotice message="Coverage details are not available in this snapshot." />}
      <section className="section-block dossier-callout"><div><div className="section-eyebrow">Next step</div><h2>What evidence would change this?</h2><p>Open the evidence plan and coverage notes for this disease.</p></div>
        <Link className="button primary" href={dossierHref}>Open Shared Path Dossier</Link></section>
    </> : <>
      {cluster.isPending && <section id="cluster" className="section-block loading-card">Resolving mechanism cluster…</section>}
      {cluster.data && <ClusterView data={cluster.data} diseaseGene={disease.data.disease.gene_symbol} persona={persona} selectedVs={selectedComparator} />}
      {cluster.isError && <ErrorNotice message="Cluster evidence is not available in this snapshot." />}
      <div className="progressive-section">{assets.isPending && <section id="assets" className="section-block loading-card">Loading reusable assets…</section>}
        {assets.data && <AssetCompare assets={assets.data.assets} persona={persona} options={comparatorOptions} selected={selectedComparator} onSelect={selectComparator} />}{assets.isError && <ErrorNotice message="Asset comparison is unavailable." />}</div>
      <div className="progressive-section">{bridges.isPending && <section id="people" className="section-block loading-card">Loading bridge people…</section>}
        {bridges.data && <BridgePeople data={bridges.data} persona={persona} options={comparatorOptions} selected={selectedComparator} onSelect={selectComparator} />}{bridges.isError && <ErrorNotice message="Bridge people are unavailable." />}</div>
      <section className="section-block dossier-callout"><div><div className="section-eyebrow">Synthesis</div><h2>Carry the shared path forward</h2><p>A cited summary of mechanism, reusable assets and evidence gaps.</p></div>
        <Link className="button primary" href={dossierHref}>Open Shared Path Dossier</Link></section>
    </>}
    {edgeId && <EdgeInspector key={edgeId} edgeId={edgeId} />}
  </main>;
}
export default function DiseasePage() {
  return <Suspense fallback={<main id="main-content" className="page-shell"><p>Loading disease…</p></main>}><DiseaseContent /></Suspense>;
}
