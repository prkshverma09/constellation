"use client";

import { useState } from "react";
import type { Asset, ComparatorOption, Persona } from "@/lib/api";
import { ComparatorSelect } from "./ComparatorSelect";

const statuses = [
  { key: "matches", label: "Matches", icon: "✓" },
  { key: "differs", label: "Differs", icon: "↔" },
  { key: "needs_expert_review", label: "Needs expert review", icon: "?" },
] as const;
export function AssetCompare({ assets, persona, options, selected, onSelect }: {
  assets: Asset[];
  persona: Persona;
  options: ComparatorOption[];
  selected: string;
  onSelect: (value: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const compared = options.find((option) => option.disease.id === selected)?.disease;
  return <section id="assets" className="section-block">
    <div className="section-eyebrow">Reusable assets</div><h2>Compare evidence assets</h2>
    <ComparatorSelect label="Compare assets against" options={options} value={selected} onChange={onSelect} />
    <p className="muted">Assets are compared against {compared?.gene_symbol ?? "the selected disease"}.</p>
    {assets.map((asset) => <article className="asset-card" key={asset.id}>
      <div className="asset-heading"><div><h3>{asset.name}</h3><span className="asset-type">{asset.asset_type.replaceAll("_", " ")}</span></div>
        <a href={asset.url} target="_blank" rel="noreferrer">{asset.record_id} ↗</a>
      </div>
      <p className="serves-line"><strong>Serves:</strong> {asset.serves.map((disease) => disease.gene_symbol).join(" · ")}</p>
      {asset.coverage ? <div className="coverage-score"><strong>{Math.round(asset.coverage.value * 100)}%</strong>{" "}
        <span>(IC {asset.coverage.numerator_ic} / {asset.coverage.denominator_ic}, {asset.coverage.n_matched} of {asset.coverage.n_total} phenotypes)</span>
      </div> : <div className="notice subtle">Coverage not computed for this asset type</div>}
      {asset.coverage && <div className="phenotype-disclosure">
        <button className="text-button" type="button" aria-expanded={open === asset.id} onClick={() => setOpen(open === asset.id ? null : asset.id)}>
          {open === asset.id ? "Hide" : "Review"} matched and unmatched phenotype terms
        </button>
        {open === asset.id && <div className="phenotype-lists">
          <div><strong>Matched ({asset.coverage.matched.length})</strong><ul>{asset.coverage.matched.map((item) => <li key={item.id}>{item.label} <span>{item.id}</span></li>)}</ul></div>
          <div><strong>Unmatched ({asset.coverage.unmatched.length})</strong><ul>{asset.coverage.unmatched.map((item) => <li key={item.id}>{item.label} <span>{item.id}</span></li>)}</ul></div>
        </div>}
      </div>}
      <div className="eligibility-columns">{statuses.map((status) => <div className="eligibility-col" key={status.key}>
        <h4><span aria-hidden="true">{status.icon}</span> {status.label}</h4>
        <ul>{asset.eligibility_diff.filter((item) => item.status === status.key).map((item) => <li key={item.field}><strong>{item.field}:</strong> {item.asset_value}</li>)}</ul>
      </div>)}</div>
      <details className="diff-details"><summary>Full eligibility difference table</summary><div className="table-scroll"><table><thead><tr><th>Field</th><th>Asset value</th><th>Target value</th><th>Status</th></tr></thead>
        <tbody>{asset.eligibility_diff.map((item) => <tr key={item.field}><td>{item.field}</td><td>{item.asset_value}</td><td>{item.target_value}</td><td>{item.status.replaceAll("_", " ")}</td></tr>)}</tbody></table></div></details>
      <div className="asset-evidence">Evidence {asset.edge_ids.map((id, index) => <a key={id} href={`?persona=${persona}&edge=${encodeURIComponent(id)}`} aria-label={`Evidence ${id}`}><sup>{index + 1}</sup></a>)}</div>
    </article>)}
  </section>;
}
