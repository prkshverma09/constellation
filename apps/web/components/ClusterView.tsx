"use client";

import { useEffect, useMemo, useState } from "react";
import { scaleLinear } from "d3-scale";
import type { ClusterResponse, Neighbour, Persona } from "@/lib/api";
import { Sentence } from "./Shared";

type Props = { data: ClusterResponse; diseaseGene: string; persona: Persona };
const labels: Record<string, string> = { M: "mechanism layer", V: "variant-effect layer", S: "fused score" };
const scoreLength = 1000;
const mapRadius = 102;

function ScoreAxis({ threshold }: { threshold: number }) {
  return <div className="score-axis-row" aria-hidden="true">
    <div className="score-axis-track">
      <span className="axis-zero">0</span>
      <span className="axis-threshold" style={{ left: `${threshold * 100}%` }}>{threshold.toFixed(2)} threshold</span>
      <span className="axis-one">1.0</span>
    </div>
    <span aria-hidden="true" />
  </div>;
}

function scoreBar(row: Neighbour, threshold: number) {
  const scale = scaleLinear().domain([0, 1]).range([0, scoreLength]);
  const values = [
    { name: "P", value: row.P * 0.5, raw: row.P, tone: "#e5e7eb" },
    { name: "M", value: row.M * 0.3, raw: row.M, tone: "#cbd5e1" },
    { name: "V", value: row.V * 0.2, raw: row.V, tone: "#94a3b8" },
  ];
  const thresholdX = scale(threshold);
  let offset = 0;
  return <div className="score-readout">
    <svg className="score-bar" viewBox={`0 0 ${scoreLength} 30`} preserveAspectRatio="none" aria-hidden="true">
      <line x1="0" x2={scoreLength} y1="13" y2="13" stroke="#e5e7eb" />
      {values.map((segment) => {
        const x = scale(offset);
        const width = scale(segment.value);
        offset += segment.value;
        return <rect key={segment.name} x={x} y="4" width={width} height="17" fill={segment.tone} stroke="#64748b" strokeWidth="2" />;
      })}
      <line x1={thresholdX} x2={thresholdX} y1="0" y2="25" stroke="#334155" strokeDasharray="8 5" strokeWidth="2" />
    </svg>
    <strong className="score-total">S {row.S.toFixed(2)}</strong>
    <div className="score-layers">{values.map((segment) => <span key={segment.name}>{segment.name} {segment.raw.toFixed(2)}</span>)}</div>
  </div>;
}

function RadialMap({ data, queryGene }: { data: ClusterResponse; queryGene: string }) {
  const positions = useMemo(() => {
    const threshold = data.weights.threshold;
    const supportedRadius = scaleLinear().domain([threshold, 1]).range([mapRadius * 0.65, mapRadius * 0.2]).clamp(true);
    const counterexampleRadius = scaleLinear().domain([0, threshold]).range([mapRadius * 0.95, mapRadius * 0.8]).clamp(true);
    const supported = data.neighbours.map((row, index) => {
      const angle = data.neighbours.length === 1
        ? -Math.PI / 2
        : -Math.PI * 0.78 + Math.PI * 0.67 * (index / (data.neighbours.length - 1));
      const pointRadius = supportedRadius(row.S);
      return { row, angle, pointRadius, counterexample: false };
    });
    const counterexample = data.counterexample ? [{
      row: data.counterexample,
      angle: Math.PI * 0.8,
      pointRadius: counterexampleRadius(data.counterexample.S),
      counterexample: true,
    }] : [];
    return [...supported, ...counterexample].map((point) => ({
      ...point,
      x: 160 + Math.cos(point.angle) * point.pointRadius,
      y: 130 + Math.sin(point.angle) * point.pointRadius,
      labelX: 160 + Math.cos(point.angle) * (point.pointRadius + 22),
      labelY: 130 + Math.sin(point.angle) * (point.pointRadius + 22),
    }));
  }, [data]);
  return <div className="radial-wrap"><div className="section-eyebrow">Neighbour map · radius reflects fused score</div>
    <svg className="radial-map" viewBox="0 0 320 260" role="img" aria-label={`Radial map centered on ${queryGene}`}>
      <circle cx="160" cy="130" r={mapRadius * 0.65} fill="none" stroke="#64748b" strokeDasharray="4 4" />
      {positions.map(({ row, x, y, labelX, labelY, counterexample }) => {
        const label = counterexample ? `${row.disease.gene_symbol} (counterexample)` : row.disease.gene_symbol;
        return <g key={row.disease.gene_symbol}>
          <line x1="160" y1="130" x2={x} y2={y} stroke="#cbd5e1" />
          <circle cx={x} cy={y} r="4" fill="white" stroke="#475569" />
          <text x={labelX} y={labelY} textAnchor="middle" fontSize="12" fill="#334155">{label}</text>
        </g>;
      })}
      <text x="160" y="130" textAnchor="middle" fontSize="12" fill="#334155" stroke="#f8fafc" strokeWidth="4" paintOrder="stroke">{queryGene}</text>
    </svg>
    <p className="muted small">Dashed ring marks the S ≥ {data.weights.threshold.toFixed(2)} threshold.</p>
  </div>;
}

function NeighbourDetail({ row, persona }: { row: Neighbour; persona: Persona }) {
  const ids = [row.edge_id, ...row.layer_edge_ids.P, ...row.layer_edge_ids.M, ...row.layer_edge_ids.V];
  return <div className="neighbour-detail">
    <p><strong>Shared pathways:</strong> {row.shared_pathways.map((item) => `${item.name} (${item.id})`).join(" · ") || "None in this snapshot"}</p>
    <p><strong>STRING score:</strong> {row.string_score?.toFixed(2) ?? "—"} · <strong>Variant classes:</strong> {row.variant_class_a} / {row.variant_class_b}</p>
    <p><strong>Layer evidence</strong></p><div className="edge-links">{["P", "M", "V"].map((layer) => <span key={layer}>{layer}: {row.layer_edge_ids[layer as "P" | "M" | "V"].length ? row.layer_edge_ids[layer as "P" | "M" | "V"].map((id, i) => <a key={id} href={`?persona=${persona}&edge=${encodeURIComponent(id)}`} aria-label={`Evidence ${id}`}><sup>{i + 1}</sup> {id}</a>) : "—"}</span>)}</div>
    <Sentence text="Neighbour relationship" edgeIds={[...new Set(ids)]} />
  </div>;
}

function rowTitle(row: Neighbour, rank: number) {
  return <span className="neighbour-title"><span className="rank-number">{rank}.</span><strong>{row.disease.gene_symbol}</strong>{"\u00a0· "}{row.disease.name}</span>;
}

export function ClusterView({ data, diseaseGene, persona, selectedVs }: Props & { selectedVs?: string }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showAllPartials, setShowAllPartials] = useState(false);
  useEffect(() => {
    const hidden = data.partial_overlaps.slice(5);
    let hash = "";
    try {
      hash = decodeURIComponent(window.location.hash.slice(1)).replace(/^partial-/, "").toLocaleLowerCase();
    } catch {
      hash = window.location.hash.slice(1).toLocaleLowerCase();
    }
    if (hidden.some((row) =>
      row.disease.id === selectedVs
      || row.disease.gene_symbol.toLocaleLowerCase() === hash
      || row.disease.id.toLocaleLowerCase() === hash
    )) setShowAllPartials(true);
  }, [data.partial_overlaps, selectedVs]);
  const visiblePartials = showAllPartials ? data.partial_overlaps : data.partial_overlaps.slice(0, 5);
  return <section id="cluster" className="section-block">
    <div className="section-eyebrow">Mechanism-first cluster</div>
    {data.cluster ? <>
      <h2>{data.cluster.label}</h2>
      <div className="cluster-meta"><span>Resolution {data.cluster.resolution}</span><span>Seed {data.cluster.seed}</span><span>Stability {data.cluster.stability.toFixed(2)}</span></div>
      <div className="cluster-legend"><span><i className="tone-p" /> P · phenotype</span><span><i className="tone-m" /> M · mechanism</span><span><i className="tone-v" /> V · variant effect</span><span>Fused S = 0.5P + 0.3M + 0.2V</span></div>
      {data.neighbours.length > 0 && <div className="cluster-ranked-list">
        <h3>Supported neighbours</h3>
        <ScoreAxis threshold={data.weights.threshold} />
        {data.neighbours.map((row, index) => <div className="neighbour-card" key={row.disease.id}>
          <button type="button" className="neighbour-button" aria-expanded={expanded === row.disease.id} onClick={() => setExpanded(expanded === row.disease.id ? null : row.disease.id)}>
            {rowTitle(row, index + 1)}
            <span className="neighbour-mechanism">{row.mechanism_label}</span>
            {scoreBar(row, data.weights.threshold)}
          </button>
          {expanded === row.disease.id && <NeighbourDetail row={row} persona={persona} />}
        </div>)}
      </div>}
    </> : <div className="notice calm"><strong>No supported neighbour meets the threshold (S ≥ {data.weights.threshold.toFixed(2)}).</strong><p>Closest unsupported leads are shown below; missing evidence layers remain explicit.</p></div>}
    {data.counterexample && <div className="counterexample-lane"><div className="section-eyebrow">Counterexample</div>
      <ScoreAxis threshold={data.weights.threshold} />
      <div className="neighbour-card"><button type="button" className="neighbour-button" aria-expanded={expanded === data.counterexample.disease.id} onClick={() => setExpanded(expanded === data.counterexample?.disease.id ? null : data.counterexample!.disease.id)}>
        <span className="neighbour-title"><strong>{data.counterexample.disease.gene_symbol}</strong>{"\u00a0· "}{data.counterexample.disease.name}</span>
        <span className="neighbour-mechanism">{data.counterexample.mechanism_label} · excluded by: {labels[data.counterexample.excluded_by]} ({data.counterexample.excluded_by} = {data.counterexample[data.counterexample.excluded_by]})</span>
        {scoreBar(data.counterexample, data.weights.threshold)}
      </button>{expanded === data.counterexample.disease.id && <NeighbourDetail row={data.counterexample} persona={persona} />}</div>
    </div>}
    {data.partial_overlaps.length > 0 && <div className="partial-overlap-lane">
      <div className="section-eyebrow">Partial overlap</div>
      <ScoreAxis threshold={data.weights.threshold} />
      {visiblePartials.map((row, index) => {
        const termNames = row.shared_pathways.map((term) => `${term.name} (${term.id})`).join(" · ");
        const exclusion = row.excluded_by === "M"
          ? "excluded by: mechanism layer (M < 0.25)"
          : "excluded by: fused score (S < 0.45)";
        return <div className="neighbour-card partial-overlap-card" id={`partial-${row.disease.gene_symbol.toLocaleLowerCase()}`} key={row.disease.id}>
          <button type="button" className="neighbour-button" aria-expanded={expanded === row.disease.id} onClick={() => setExpanded(expanded === row.disease.id ? null : row.disease.id)}>
            {rowTitle(row, index + 1)}
            <span className="neighbour-mechanism">{termNames || "No shared term listed"} · {exclusion}</span>
            {scoreBar(row, data.weights.threshold)}
          </button>
          {expanded === row.disease.id && <NeighbourDetail row={row} persona={persona} />}
        </div>;
      })}
      {data.partial_overlaps.length > 5 && <button
        type="button"
        className="text-button partial-disclosure"
        aria-expanded={showAllPartials}
        onClick={() => setShowAllPartials(!showAllPartials)}
      >{showAllPartials ? "Show fewer" : `Show all (${data.partial_overlaps.length})`}</button>}
    </div>}
    {data.nearest_leads.length > 0 && <div className="nearest-leads"><h3>Nearest leads</h3><ScoreAxis threshold={data.weights.threshold} />{data.nearest_leads.map((row, index) => <div className="neighbour-card" key={row.disease.id}>
      <div className="lead-copy">{rowTitle(row, index + 1)}</div>{scoreBar(row, data.weights.threshold)}
    </div>)}</div>}
    <RadialMap data={data} queryGene={diseaseGene} />
    <div className="sr-only"><ul>
      {data.neighbours.map((row, index) => <li key={row.disease.id}>{index + 1}. Supported: {row.disease.gene_symbol} · {row.disease.name}. P {row.P.toFixed(2)}, M {row.M.toFixed(2)}, V {row.V.toFixed(2)}, S {row.S.toFixed(2)}. {row.mechanism_label}</li>)}
      {data.counterexample && <li>Counterexample: {data.counterexample.disease.gene_symbol} · {data.counterexample.disease.name}. P {data.counterexample.P.toFixed(2)}, M {data.counterexample.M.toFixed(2)}, V {data.counterexample.V.toFixed(2)}, S {data.counterexample.S.toFixed(2)}. Excluded by {labels[data.counterexample.excluded_by]}.</li>}
      {data.partial_overlaps.map((row) => <li key={row.disease.id}>Partial overlap: {row.disease.gene_symbol} · {row.disease.name}. Shared terms: {row.shared_pathways.map((term) => `${term.name} (${term.id})`).join(", ") || "none"}; excluded by {row.excluded_by === "M" ? "mechanism layer" : "fused score"}.</li>)}
      {data.nearest_leads.map((row, index) => <li key={row.disease.id}>Nearest lead {index + 1}: {row.disease.gene_symbol} · {row.disease.name}. P {row.P.toFixed(2)}, M {row.M.toFixed(2)}, V {row.V.toFixed(2)}, S {row.S.toFixed(2)}. {row.mechanism_label}</li>)}
    </ul></div>
  </section>;
}
