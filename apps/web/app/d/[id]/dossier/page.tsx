"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Dossier } from "@/components/Dossier";
import { ErrorNotice, usePersona, withPersona } from "@/components/Shared";

function DossierContent() {
  const params = useParams<{ id: string }>();
  const id = decodeURIComponent(params.id);
  const persona = usePersona();
  const query = useQuery({ queryKey: ["dossier", id, persona], queryFn: () => api.dossier(id, persona) });
  return <main id="main-content" className="page-shell dossier-shell">
    <div className="page-topline no-print"><Link href={withPersona(`/d/${encodeURIComponent(id)}`, persona)}>← Disease overview</Link></div>
    {query.isPending && <p>Preparing cited dossier…</p>}
    {query.isError && <ErrorNotice message="This dossier is not available in the current snapshot." />}
    {query.data && <Dossier data={query.data} persona={persona} />}
  </main>;
}
export default function DossierPage() {
  return <Suspense fallback={<main id="main-content" className="page-shell"><p>Loading dossier…</p></main>}><DossierContent /></Suspense>;
}
