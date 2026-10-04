"use client";

import { useCallback, useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { api, type ContributeRequest, type EdgeResponse, type LedgerRow } from "@/lib/api";
import { EvidenceClassChip } from "./Shared";

function LedgerFields({ edge }: { edge: EdgeResponse }) {
  const sourceRecord = edge.source_url ? <a href={edge.source_url} target="_blank" rel="noreferrer">{edge.source_record || edge.source_url}</a> : edge.source_record || "—";
  return <dl className="ledger-fields">
    <dt>edge_id</dt><dd>{edge.edge_id}</dd>
    <dt>subject</dt><dd>{edge.subject} <span>({edge.subject_label})</span></dd>
    <dt>predicate</dt><dd>{edge.predicate}</dd>
    <dt>object</dt><dd>{edge.object} <span>({edge.object_label})</span></dd>
    <dt>evidence_class</dt><dd><EvidenceClassChip value={edge.evidence_class} /></dd>
    <dt>source</dt><dd>{edge.source}</dd>
    <dt>source_record</dt><dd>{sourceRecord}</dd>
    <dt>retrieved_at</dt><dd>{edge.retrieved_at}</dd>
    <dt>confidence</dt><dd>{edge.confidence}</dd>
    <dt>quote</dt><dd>{edge.quote ? <mark>{edge.quote}</mark> : "—"}</dd>
    <dt>polarity</dt><dd>{edge.polarity ?? "—"}</dd>
    <dt>contradicted_by</dt><dd>{edge.contradicted_by.length ? edge.contradicted_by.join(", ") : "—"}</dd>
    <dt>method</dt><dd>{edge.method ?? "—"}</dd>
    <dt>schema_version</dt><dd>{edge.schema_version}</dd>
    <dt>properties</dt><dd><code className="json-value">{JSON.stringify(edge.properties)}</code></dd>
  </dl>;
}
function ContributeEvidence({ edgeId }: { edgeId: string }) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (body: ContributeRequest) => api.contribute(body),
    onSuccess: (row) => queryClient.setQueryData(["pending-contributions", edgeId], (rows: LedgerRow[] | undefined) => [...(rows ?? []), row]),
  });
  return <div className="contribute-box">
    <h3>Contribute evidence</h3><p className="muted small">Suggestions are proposed and do not alter snapshot analytics.</p>
    <form onSubmit={(event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      mutation.mutate({ url: String(form.get("url")), sentence: String(form.get("sentence")), edge_id: edgeId, polarity: String(form.get("polarity")) as "supports" | "contradicts" });
    }}>
      <input type="hidden" name="edge_id" value={edgeId} />
      <label>Source URL<input required type="url" name="url" placeholder="https://example.org/source" /></label>
      <label>One sentence<input required name="sentence" maxLength={500} placeholder="Describe the evidence in one sentence" /></label>
      <fieldset><legend>This evidence</legend><label><input type="radio" name="polarity" value="supports" defaultChecked /> supports</label><label><input type="radio" name="polarity" value="contradicts" /> contradicts</label></fieldset>
      <button className="button primary" type="submit" disabled={mutation.isPending}>{mutation.isPending ? "Submitting…" : "Submit proposal"}</button>
      {mutation.isError && <p role="alert">The proposal could not be saved.</p>}
    </form>
    {mutation.isSuccess && <p className="notice calm" role="status">Pending — proposed</p>}
  </div>;
}
export function EdgeInspector({ edgeId }: { edgeId: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const dialogRef = useRef<HTMLElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const query = useQuery({ queryKey: ["edge", edgeId], queryFn: () => api.edge(edgeId) });
  const pending = useQuery<LedgerRow[]>({ queryKey: ["pending-contributions", edgeId], queryFn: async () => [], initialData: [] });
  const close = useCallback(() => {
    const params = new URLSearchParams(search.toString());
    params.delete("edge");
    router.replace(params.size ? `${pathname}?${params.toString()}` : pathname, { scroll: false });
  }, [pathname, router, search]);
  useEffect(() => {
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialogRef.current?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); close(); return; }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const items = [...dialogRef.current.querySelectorAll<HTMLElement>('button, a[href], input:not([type="hidden"]), [tabindex]:not([tabindex="-1"])')].filter((item) => !item.hasAttribute("disabled"));
      const first = items[0], last = items.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", keydown);
    return () => { document.removeEventListener("keydown", keydown); returnFocus.current?.focus(); };
  }, [close]);
  return <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
    <aside ref={dialogRef} className="edge-drawer" role="dialog" aria-modal="true" aria-labelledby="edge-inspector-title" tabIndex={-1}>
      <header className="drawer-header"><div><div className="section-eyebrow">Evidence ledger</div><h2 id="edge-inspector-title">Edge inspector</h2></div><button className="icon-button" aria-label="Close edge inspector" onClick={close}>×</button></header>
      {query.isPending && <p>Loading edge…</p>}
      {query.isError && <div className="notice calm">This edge is not available in the current snapshot.</div>}
      {query.data && <><LedgerFields edge={query.data} />
        {query.data.source_url && <p><a href={query.data.source_url} target="_blank" rel="noreferrer">Open source record ↗</a></p>}
        <section className="contradictions"><h3>Contradicting edges</h3>{query.data.contradicting.length === 0 ? <p className="muted">No contradicting edges in this snapshot.</p> : query.data.contradicting.map((row) => <article className="contradiction-row" key={row.edge_id}>
          <strong>{row.edge_id}</strong><p>{row.source} · {row.source_record}</p><EvidenceClassChip value={row.evidence_class} /><p>{row.quote ? <mark>{row.quote}</mark> : "—"} · {row.polarity ?? "—"}</p>
          {row.source_url && <a href={row.source_url} target="_blank" rel="noreferrer">Open source record ↗</a>}
        </article>)}</section>
        {pending.data.map((row) => <article className="pending-row" key={row.edge_id}><h3>Pending — proposed</h3><EvidenceClassChip value="proposed" /><p>{row.quote}</p><small>{row.edge_id}</small></article>)}
        <ContributeEvidence edgeId={edgeId} />
      </>}
    </aside>
  </div>;
}
