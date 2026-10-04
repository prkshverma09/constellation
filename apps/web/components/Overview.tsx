"use client";

import { useState } from "react";
import { usePersona, EvidenceClassChip, Sentence } from "./Shared";
import type { DiseaseResponse } from "@/lib/api";

const labels: Record<keyof DiseaseResponse["counts"], string> = {
  phenotypes: "Phenotypes", pathogenic_variants: "Pathogenic variants", trials: "Trials",
  awards_active: "Active awards", papers: "Papers", patient_groups: "Patient groups",
};
export function Overview({ disease }: { disease: DiseaseResponse }) {
  const persona = usePersona();
  const [open, setOpen] = useState<string | null>(null);
  const sentences = persona === "devon" ? disease.summary.plain : disease.summary.technical;
  return <section id="overview" className="section-block">
    <div className="section-eyebrow">Disease overview</div>
    <h1>{disease.disease.name}</h1>
    <div className="identifier-line"><span>{disease.disease.id}</span><span>{disease.disease.gene_symbol}</span></div>
    <div className="synonym-row"><strong>Also known as</strong> {disease.disease.synonyms.join(" · ")}
      {disease.disease.omim && <span> · {disease.disease.omim}</span>}{disease.disease.orphacode && <span> · {disease.disease.orphacode}</span>}
    </div>
    <div className="count-grid">
      {(Object.keys(labels) as (keyof DiseaseResponse["counts"])[]).map((key) => <div className="count-card" key={key}>
        <button className="count-trigger" aria-expanded={open === key} onClick={() => setOpen(open === key ? null : key)}>
          <span className="count-value">{disease.counts[key].n}</span><span className="count-label">{labels[key]}</span><span className="count-open">{open === key ? "Hide evidence" : "View evidence"}</span>
        </button>
        {open === key && <div className="count-detail"><strong>Backing edges</strong><p>{disease.counts[key].edge_ids.length} of {disease.counts[key].n} backing edges shown</p>
          {disease.counts[key].edge_ids.map((id, i) => <a key={id} href={`?persona=${persona}&edge=${encodeURIComponent(id)}`} aria-label={`Evidence ${id}`}><sup>{i + 1}</sup> {id}</a>)}</div>}
      </div>)}
    </div>
    {disease.patient_groups.length > 0 && <div className="patient-group-card">
      <div><div className="section-eyebrow">Patient communities</div>{disease.patient_groups.map((group) => <div key={group.id} className="group-line">
        <a href={group.url} target="_blank" rel="noreferrer">{group.name}</a><EvidenceClassChip value={group.evidence_class} />
        <span className="match-kind">{group.match_kind === "exact" ? "Exact match" : "Umbrella network"}</span>
        <a className="inline-footnote" href={`?persona=${persona}&edge=${encodeURIComponent(group.edge_id)}`} aria-label={`Evidence ${group.edge_id}`}><sup>1</sup></a>
      </div>)}</div>
    </div>}
    <div className="summary-card"><div className="section-eyebrow">{persona === "devon" ? "Plain-language summary" : "Mechanism summary"}</div>
      {sentences.map((item, index) => <p key={`${item.text}-${index}`}><Sentence text={item.text} edgeIds={item.edge_ids} start={1 + sentences.slice(0, index).reduce((sum, row) => sum + row.edge_ids.length, 0)} /></p>)}
    </div>
  </section>;
}
