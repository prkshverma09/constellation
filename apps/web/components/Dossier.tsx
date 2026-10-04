"use client";

import { useMemo, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { api, type Dossier as DossierType, type Persona } from "@/lib/api";
import { Sentence } from "./Shared";

function citedRows(data: DossierType) {
  const ids = new Set<string>();
  if (data.gap_plan) data.gap_plan.what_would_change.forEach((item) => item.edge_ids.forEach((id) => ids.add(id)));
  for (const section of data.sections) for (const sentence of section.sentences) sentence.edge_ids.forEach((id) => ids.add(id));
  return [...ids];
}
export function Dossier({ data, persona }: { data: DossierType; persona: Persona }) {
  const [copied, setCopied] = useState(false);
  const edgeIds = useMemo(() => citedRows(data), [data]);
  const numberByEdge = useMemo(() => new Map(edgeIds.map((id, index) => [id, index + 1])), [edgeIds]);
  const edgeQueries = useQueries({ queries: edgeIds.map((id) => ({ queryKey: ["edge", id], queryFn: () => api.edge(id) })) });
  let sentenceNumber = 1;
  const print = () => window.print();
  const copy = async () => { await navigator.clipboard.writeText(data.markdown); setCopied(true); window.setTimeout(() => setCopied(false), 2000); };
  const download = () => {
    const url = URL.createObjectURL(new Blob([data.markdown], { type: "text/markdown;charset=utf-8" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = "constellation-shared-path-dossier.md"; anchor.click(); URL.revokeObjectURL(url);
  };
  return <article className="dossier-page">
    <div className="dossier-controls no-print"><button className="button secondary" onClick={print}>Export PDF</button>
      <button className="button secondary" onClick={() => void copy()}>Copy Markdown</button>
      <button className="button secondary" onClick={download}>Download .md</button>
      <span className="live-status" aria-live="polite">{copied ? "Copied" : ""}</span>
    </div>
    <header className="dossier-header"><h1>Shared Path Dossier</h1>
      <p className="dossier-disease">{data.disease_id === "MONDO:0012812" ? "developmental and epileptic encephalopathy 4" : "developmental and epileptic encephalopathy 37"}</p>
      <div className="dossier-meta"><span>Persona: {persona}</span><span>Mode: {data.mode}</span><span>Snapshot: {data.snapshot_hash}</span><span>Trace: {data.trace_id}</span><span>Generated: {new Date(data.generated_at).toLocaleDateString()}</span></div>
    </header>
    {data.dropped_sentences > 0 && <div className="notice calm dropped-notice">{data.dropped_sentences} sentences removed for lack of evidence.</div>}
    {data.gap_plan ? <section className="section-block gap-plan">
      <h2>What would change this</h2><ul>{data.gap_plan.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
      {data.gap_plan.what_would_change.map((item, index) => <p key={`${item.layer}-${index}`}><strong>{item.layer} layer.</strong> <Sentence text={item.text} edgeIds={item.edge_ids} start={sentenceNumber++} numberByEdge={numberByEdge} /></p>)}
    </section> : data.sections.filter((section) => section.key !== "coverage").map((section) => <section className="dossier-section" key={section.key}>
      <h2>{section.title}</h2>{section.sentences.map((sentence) => {
        const start = sentenceNumber;
        sentenceNumber += sentence.edge_ids.length;
        return <p key={`${section.key}-${sentence.text}`}><Sentence text={sentence.text} edgeIds={sentence.edge_ids} start={start} numberByEdge={numberByEdge} /></p>;
      })}
    </section>)}
    {data.sections.filter((section) => section.key === "coverage").map((section) => <section className="dossier-section" key={section.key}>
      <h2>{section.title}</h2>{section.sentences.map((sentence) => { const start = sentenceNumber; sentenceNumber += sentence.edge_ids.length; return <p key={sentence.text}><Sentence text={sentence.text} edgeIds={sentence.edge_ids} start={start} numberByEdge={numberByEdge} /></p>; })}
    </section>)}
    <section className="footnote-list"><h2>Evidence</h2><ol>{edgeIds.map((id, index) => {
      const edge = edgeQueries[index]?.data;
      return <li key={id} id={`evidence-${index + 1}`}><strong>{index + 1}. {id}</strong>
        {edge && <span>{edge.subject_label} · {edge.predicate} · {edge.object_label} · {edge.source} · {edge.evidence_class}</span>}
      </li>;
    })}</ol></section>
    <div className="notice subtle print-disclaimer">Evidence-backed research navigation, not medical advice. The atlas proposes; clinicians decide.</div>
  </article>;
}
