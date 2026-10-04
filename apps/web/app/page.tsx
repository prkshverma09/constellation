"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type SearchHit } from "@/lib/api";
import { formatMatchKind, usePersona } from "@/components/Shared";

function SearchScreen() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const persona = usePersona();
  const [input, setInput] = useState("");
  const [debounced, setDebounced] = useState("");
  const [active, setActive] = useState(-1);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(input.trim()), 200);
    return () => window.clearTimeout(timer);
  }, [input]);
  const search = useQuery({ queryKey: ["search", debounced], queryFn: () => api.search(debounced), enabled: debounced.length > 0 });
  const currentSearch = search.data?.query === input.trim() ? search.data : undefined;
  const best = currentSearch?.best ?? null;
  const choose = (hit: SearchHit | null) => {
    if (!hit?.disease) return;
    router.push(`/d/${encodeURIComponent(hit.disease.id)}?persona=${persona}`);
  };
  const results = currentSearch?.results ?? [];
  const display = (hit: SearchHit) => `${hit.disease?.gene_symbol ?? hit.label} → ${hit.disease?.name ?? hit.label} (${hit.disease?.id ?? hit.id}), matched ${formatMatchKind(hit.match_kind)} '${hit.matched_text}'`;
  return <main id="main-content" className="search-main">
    <div className="search-intro"><div className="section-eyebrow">Rare disease research atlas</div>
      <h1>Find a shared path.</h1><p>Start with a gene, condition, phenotype, organisation or mechanism.</p>
    </div>
    <div className="command-box">
      <label htmlFor="disease-search" className="sr-only">Search diseases and mechanisms</label>
      <span className="search-icon" aria-hidden="true">⌕</span>
      <input id="disease-search" role="combobox" aria-autocomplete="list" aria-expanded={results.length > 0}
        aria-controls={results.length ? "search-results" : undefined} aria-activedescendant={active >= 0 ? `search-option-${active}` : undefined}
        placeholder="Try STXBP1, Munc18-1, DEE4…" value={input}
        onChange={(event) => { setInput(event.target.value); setActive(-1); }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" && results.length) { event.preventDefault(); setActive((n) => (n + 1) % results.length); }
          else if (event.key === "ArrowUp" && results.length) { event.preventDefault(); setActive((n) => n <= 0 ? results.length - 1 : n - 1); }
          else if (event.key === "Enter") {
            event.preventDefault();
            const selected = active >= 0 ? results[active] : best;
            if (selected) choose(selected);
            else if (input.trim()) {
              void queryClient.fetchQuery({
                queryKey: ["search", input.trim()],
                queryFn: () => api.search(input.trim()),
              }).then((response) => choose(response.best));
            }
          }
          else if (event.key === "Escape") { setInput(""); setActive(-1); }
        }} />
      {search.isFetching && <span className="search-loading" aria-label="Searching">…</span>}
    </div>
    {best && <button className="resolved-line" onClick={() => choose(best)}>{display(best)} <span aria-hidden="true">↗</span></button>}
    {results.length > 0 && <div className="search-results" id="search-results" role="listbox" aria-label="Search results">
      {results.map((hit, index) => <button type="button" role="option" id={`search-option-${index}`} aria-selected={active === index}
        className={active === index ? "search-option active" : "search-option"} key={`${hit.id}-${hit.match_kind}-${hit.matched_text}`}
        onMouseEnter={() => setActive(index)} onClick={() => choose(hit)}>
        <strong>{hit.label}</strong>
        <span>{hit.disease?.name ?? hit.label} ({hit.disease?.id ?? hit.id}) · matched {formatMatchKind(hit.match_kind)} “{hit.matched_text}”</span>
      </button>)}
    </div>}
    {debounced && search.isSuccess && !best && <div className="notice calm search-empty">No match in this snapshot. Try STXBP1 or FRRS1L.</div>}
    {search.isError && <div className="notice calm">Search is unavailable. Try again shortly.</div>}
    <div className="search-hints"><span>Suggested</span>{["STXBP1", "FRRS1L"].map((term) => <button key={term} onClick={() => { setInput(term); setDebounced(term); }}>{term}</button>)}</div>
    <div className="search-note">Explore how evidence connects — every path stays linked to its source.</div>
  </main>;
}
export default function SearchPage() {
  return <Suspense fallback={<main id="main-content" className="search-main"><p>Loading search…</p></main>}><SearchScreen /></Suspense>;
}
