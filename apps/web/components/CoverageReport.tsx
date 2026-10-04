"use client";

import type { CoverageResponse } from "@/lib/api";
import { Sentence } from "./Shared";

const missingLabels: Record<string, string> = { M: "mechanism layer", V: "variant-effect layer", asset: "reusable asset evidence", people: "bridge-person evidence" };
export function CoverageReport({ data }: { data: CoverageResponse }) {
  return <section id="coverage" className="section-block coverage-report">
    <div className="section-eyebrow">Evidence coverage</div><h2>Honest gap report</h2>
    <div className="notice calm"><strong>No supported neighbour meets the threshold (S ≥ 0.45)</strong><p>The snapshot is sparse here. This is a coverage gap, not a negative finding.</p></div>
    <h3>Why the path is not supported yet</h3><ul className="reason-list">{data.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
    <h3>Sources checked</h3><div className="table-scroll"><table><thead><tr><th>Source</th><th>Query</th><th>Count</th><th>Retrieved</th></tr></thead>
      <tbody>{data.sources.map((source) => <tr key={`${source.source}-${source.query}`}><td>{source.source}</td><td>{source.query}</td><td>{source.count}</td><td>{source.retrieved_at}</td></tr>)}</tbody></table></div>
    <h3>Nearest leads</h3><div className="lead-list">{data.nearest_leads.map((lead) => <article className="lead-card" key={lead.disease.id}>
      <strong>{lead.disease.gene_symbol} · {lead.disease.name}</strong><span>S {lead.S.toFixed(2)}</span><span>missing: {missingLabels[lead.missing_layer]}</span>
    </article>)}</div>
    <h3>What would change this</h3><div className="change-list">{data.what_would_change.map((item, index) => <p key={`${item.layer}-${index}`}><strong>{item.layer} layer.</strong> <Sentence text={item.text} edgeIds={item.edge_ids} start={index + 1} /></p>)}</div>
  </section>;
}
