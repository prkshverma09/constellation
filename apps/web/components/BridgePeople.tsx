"use client";

import type { BridgesResponse, ComparatorOption, Persona } from "@/lib/api";
import { ComparatorSelect } from "./ComparatorSelect";

const roleLabels = { author: "Author", pi: "PI", study_official: "Study official" };
export function BridgePeople({ data, persona, options, selected, onSelect }: {
  data: BridgesResponse;
  persona: Persona;
  options: ComparatorOption[];
  selected: string;
  onSelect: (value: string) => void;
}) {
  return <section id="people" className="section-block">
    <div className="section-eyebrow">Bridge people</div><h2>People connected across both sides</h2>
    <ComparatorSelect label="Compare bridge people against" options={options} value={selected} onChange={onSelect} />
    {data.unverified_name_matches > 0 && <p className="muted small">{data.unverified_name_matches} name-only matches excluded pending corroboration.</p>}
    {data.people.length === 0 && <div className="notice calm">No corroborated bridge people were found for this pair.</div>}
    <div className="people-grid">{data.people.map((person) => <article key={person.id} className="person-card">
      <div className="person-top"><h3>{person.display_name}</h3><span className="person-score">Score {person.score.toFixed(2)}</span></div>
      <p>{person.affiliations.join(" · ")}</p>
      <div className="role-list">{person.roles.map((role) => <span className="role-chip" key={role}>{roleLabels[role]}</span>)}</div>
      <p className="record-count">{person.n_a} records on {data.a.gene_symbol} · {person.n_b} on {data.b.gene_symbol}</p>
      <ul className="proving-edges">{person.proving_edges.map((proof) => <li key={`${proof.edge_id}-${proof.record_id}`}>
        <span className="proof-kind">{proof.kind}</span><a href={proof.url} target="_blank" rel="noreferrer">{proof.record_id} · {proof.title}{proof.year ? ` (${proof.year})` : ""}</a>
        <span className="proof-side">{proof.disease_id === data.a.id ? data.a.gene_symbol : data.b.gene_symbol}</span>
        <a className="inline-footnote" href={`?persona=${persona}&edge=${encodeURIComponent(proof.edge_id)}`} aria-label={`Evidence ${proof.edge_id}`}><sup>1</sup></a>
      </li>)}</ul>
      <details className="why-person"><summary>Why same person</summary><p>{person.why_same_person}</p></details>
    </article>)}</div>
  </section>;
}
