"use client";

import Link from "next/link";
import { Suspense } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type EvidenceClass, type Persona } from "@/lib/api";

export const PERSONAS: { id: Persona; label: string }[] = [
  { id: "maria", label: "Maria" }, { id: "devon", label: "Devon" }, { id: "priya", label: "Priya" }, { id: "osei", label: "Dr. Osei" },
];
export function usePersona(): Persona {
  const value = useSearchParams().get("persona");
  return PERSONAS.some((persona) => persona.id === value) ? value as Persona : "maria";
}
export function withPersona(path: string, persona: Persona) {
  return `${path}?persona=${persona}`;
}
export function setQueryParam(router: ReturnType<typeof useRouter>, pathname: string, params: URLSearchParams) {
  const query = params.toString();
  router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
}

const evidenceColors: Record<EvidenceClass, string> = {
  observed: "#0072B2", curated: "#009E73", inferred: "#CC79A7",
  llm_extracted: "#E69F00", web_discovered: "#56B4E9", proposed: "#9CA3AF",
};
export function EvidenceClassChip({ value }: { value: EvidenceClass }) {
  return <span className="evidence-chip"><span aria-hidden="true" className="evidence-swatch" style={{ backgroundColor: evidenceColors[value] }} />{value}</span>;
}

export function Footer() {
  return <footer className="site-footer">Evidence-backed research navigation, not medical advice. The atlas proposes; clinicians decide.</footer>;
}

export function HealthBadge() {
  const { data } = useQuery({ queryKey: ["health"], queryFn: api.health });
  const mode = data?.llm_mode ?? "offline";
  return <span className="health-badge"><span className={`health-dot ${mode}`} />{mode} · {data?.snapshot_hash?.slice(0, 12) ?? "checking"}</span>;
}

export function PersonaToggle() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const persona = usePersona();
  const params = new URLSearchParams(searchParams.toString());
  const [mechanismQuery, setMechanismQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const results = useQuery({
    queryKey: ["mechanism-search", debounced],
    queryFn: () => api.mechanismSearch(debounced),
    enabled: persona === "priya" && debounced.trim().length > 0,
  });
  const onMechanismInput = (value: string) => {
    setMechanismQuery(value);
    window.clearTimeout((window as Window & { mechanismTimer?: number }).mechanismTimer);
    (window as Window & { mechanismTimer?: number }).mechanismTimer = window.setTimeout(() => setDebounced(value.trim()), 200);
  };
  return <div className="persona-wrap">
    <div className="persona-toggle" role="radiogroup" aria-label="Choose research persona">
      {PERSONAS.map((item) => <button
        key={item.id} type="button" role="radio" aria-checked={persona === item.id}
        className={persona === item.id ? "persona-option selected" : "persona-option"}
        onClick={() => { params.set("persona", item.id); setQueryParam(router, pathname, params); }}
      >{item.label}</button>)}
    </div>
    {persona === "priya" && <div className="mechanism-search">
      <label className="sr-only" htmlFor="mechanism-search">Search mechanisms</label>
      <input id="mechanism-search" value={mechanismQuery} placeholder="Search mechanisms…" onChange={(event) => onMechanismInput(event.target.value)} />
      {results.data && <div className="mechanism-results" role="list">
        {results.data.clusters.map((cluster) => <Link key={cluster.id} role="listitem" href="#cluster">
          <strong>{cluster.label}</strong><span>{cluster.member_ids.length} members · score {cluster.score.toFixed(2)} · {cluster.matched_pathways.map((pathway) => pathway.name).join(" / ")}</span>
        </Link>)}
      </div>}
    </div>}
  </div>;
}

export function Header() {
  const mock = process.env.NEXT_PUBLIC_API_BASE === "mock";
  return <>
    <a className="skip-link" href="#main-content">Skip to content</a>
    <header className="site-header">
      <Link className="brand" href="/"><span className="brand-mark" aria-hidden="true">✳</span>Constellation</Link>
      <div className="header-persona"><Suspense fallback={<div className="persona-toggle" aria-label="Loading persona choices" />}><PersonaToggle /></Suspense></div>
      <HealthBadge />
    </header>
    {mock && <div className="mock-banner" role="status">Mock mode — illustrative fixture data, not real findings.</div>}
  </>;
}

export function SectionNav({ gap = false }: { gap?: boolean }) {
  const items = [{ id: "overview", label: "Overview" }, { id: gap ? "coverage" : "cluster", label: gap ? "Coverage" : "Cluster" }, ...(!gap ? [{ id: "assets", label: "Assets" }, { id: "people", label: "People" }] : [])];
  return <nav className="section-nav" aria-label="Disease page sections">{items.map((item) => <a key={item.id} href={`#${item.id}`}>{item.label}</a>)}</nav>;
}

export function Sentence({ text, edgeIds, start = 1, numberByEdge }: { text: string; edgeIds: string[]; start?: number; numberByEdge?: Map<string, number> }) {
  const search = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const [activeEdge, setActiveEdge] = useState<string | null>(null);
  const persona = usePersona();
  const edgeQuery = useQuery({
    queryKey: ["edge", activeEdge],
    queryFn: () => api.edge(activeEdge!),
    enabled: Boolean(activeEdge),
  });
  const open = (id: string) => {
    const params = new URLSearchParams(search.toString());
    params.set("persona", persona);
    params.set("edge", id);
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  };
  return <span className="sentence" data-testid="sentence">{text}{edgeIds.map((id, index) => {
    const number = numberByEdge?.get(id) ?? start + index;
    return <span className="footnote-wrap" key={`${id}-${number}`}>
      <sup><button type="button" className="footnote-link" data-testid="footnote" aria-label={`Evidence ${id}`} onClick={() => open(id)}
        onMouseEnter={() => { setActiveEdge(id); void queryClient.prefetchQuery({ queryKey: ["edge", id], queryFn: () => api.edge(id) }); }}
        onFocus={() => setActiveEdge(id)} onBlur={() => setActiveEdge(null)}>{number}</button></sup>
      {activeEdge === id && <span className="footnote-popover" role="tooltip">
        {edgeQuery.data ? <><strong>{edgeQuery.data.predicate}</strong><span>{edgeQuery.data.subject_label} → {edgeQuery.data.object_label}</span><small>{edgeQuery.data.source} · {edgeQuery.data.evidence_class}</small></> : <span>Loading evidence preview…</span>}
      </span>}
    </span>;
  })}</span>;
}

export function ErrorNotice({ message }: { message: string }) {
  return <div className="notice calm" role="status">{message}</div>;
}

export function formatMatchKind(kind: string) {
  const names: Record<string, string> = { id: "identifier", label: "label", synonym: "synonym", symbol: "symbol", alias: "alias", org: "organisation" };
  return names[kind] ?? kind;
}
