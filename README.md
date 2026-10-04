# Constellation

Constellation is a mechanism-first rare-disease knowledge graph for a focused
40-gene slice of developmental and epileptic encephalopathies (DEE). It serves a
versioned, source-backed DuckDB/Parquet snapshot through FastAPI and a Next.js
research-navigation interface. It is an evidence exploration prototype, not a
diagnostic or treatment tool.

## Two research journeys

- **Follow a mechanism.** Start with STXBP1, inspect its stable SNARE-exocytosis
  cluster, compare supported neighbours, and distinguish the GNAO1
  phenotype-similar counterexample from DNM1's weak, different-step
  “vesicle organization” overlap.
- **Find an honest gap.** Start with FRRS1L (`MONDO:0014859`), inspect source
  coverage and nearest leads, and open the dossier to see which missing evidence
  would change the current conclusion.

The Assets and People sections share a comparator. It defaults to the
highest-scoring cluster member; the URL's `vs=` parameter makes a comparison
reproducible and linkable.

## Quickstart

Install the Python and Node dependencies once (`uv sync --project backend`,
then `cd apps/web && pnpm install`). The committed snapshot and cached dossiers
let the demo run without external API access:

```sh
make demo
```

Open <http://localhost:3000>. The API is at <http://localhost:8000>. `make demo`
starts the API with `LLM_MODE=offline`; it refuses to reuse an API already
running in a different LLM mode rather than silently making an online request.
For separate foreground processes, use `make api` and `make web`.

To refresh the source snapshot (requires access to the configured source APIs):

```sh
make data
```

Then restart the API so it loads the rebuilt snapshot. Useful gates:

```sh
make test
make lint                 # Ruff and Pyright
cd apps/web
pnpm typecheck
pnpm lint
pnpm build
cd ../..
make e2e                  # Playwright against the real API on :8000
```

The fixture-backed frontend client remains available with
`NEXT_PUBLIC_API_BASE=mock pnpm dev` for isolated interface work. `make e2e`
defaults to the real API, not fixtures.

## Architecture

- **Ingestion and normalization** (`backend/constellation/ingest/`) fetch and
  cache source records, normalize disease/gene identifiers, and parse HPO,
  Reactome, STRING, and GO annotations. The GO layer reads the human GAF and
  `go-basic.obo`, propagates biological-process annotations over `is_a` and
  `part_of`, and counts distinct human genes for specificity.
- **Analytics** (`backend/constellation/analytics/`) constructs phenotype,
  mechanism, and variant-effect evidence; computes guarded pairwise similarity;
  detects clusters, partial overlaps, coverage gaps, and corroborated bridge
  people.
- **Build and storage** write the graph snapshot as Parquet with DuckDB-backed
  access. The build also regenerates the offline dossier caches for the demo
  diseases/personas and removes caches keyed to old snapshot hashes.
- **API** (`backend/constellation/api/`) exposes the contract documented in
  [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md). `apps/web/openapi.json` is the
  frontend's exported API schema.
- **Frontend** (`apps/web/`) provides disease and mechanism search, cluster and
  partial-overlap lanes, the asset/people comparator, source coverage, a cited
  dossier, and edge-level provenance inspection.

The fused similarity remains `S = 0.5P + 0.3M + 0.2V`; support requires both
`S ≥ 0.45` and `M ≥ 0.25`. Reactome and experimental GO biological-process
Jaccards are filtered to terms/pathways with no more than 500 distinct human
genes. STRING mechanism support combines experimental and database channels;
text-mining does not contribute. Partial overlaps are returned without an API
cap, sorted by mechanism overlap; the interface initially shows five and can
expand the lane.

## Evidence ledger

Every assertion exposed by the API is tied to ledger edge IDs. Ledger records
retain subject/object identifiers and labels, predicate, source and source
record, source URL, retrieval time, evidence class, confidence, quote or
method, polarity, contradiction references, and schema version. The edge
inspector resolves those IDs to their full provenance. Curated annotations
preserve their source record identifiers (including GO IDs, references, and
evidence codes); evidence edges are not silently converted into uncited prose.

Bridge people are merged only when normalized names have corroborating
identity signals (for example ORCID, affiliation, shared co-authors, or a
linked study/award role). Name-only candidates are reported separately and do
not appear as verified bridges.

## LLM modes and offline behavior

`LLM_MODE` supports `live`, `cached`, and `offline`. Claim extraction uses
`CONSTELLATION_EXTRACT_MODEL` (default `gpt-5-mini`) and the versioned
`extraction-schema-v2` cache. `make extract` makes explicit live calls; the
normal build reads validated cache entries in cached mode. Patient-organization
discovery uses `gpt-5` web search and `discover-v1`; the build accepts only
cached results whose own HTML page is available and names the gene. It runs at
most four gene requests concurrently with a 120-second timeout and records
failures without blocking other genes. Live dossiers use
`CONSTELLATION_AGENT_MODEL` (default `gpt-5`) and `writer-v1`.
Their writer receives a compact evidence pack, and post-validation drops
citations outside that pack and unsupported numbers. Successful dossiers are
cached by disease, persona, comparator, snapshot, model, and prompt version.
The explicit live workflows are `make extract`, `make discover`, and
`make dossiers-live`; they report API-reported token usage for cost estimates.
The measured partial live run estimated about **$17.06**: 2,162 extraction
jobs, 18 of 40 discovery genes, and nine dossiers, using standard token rates
and one web-search call per discovered gene. It excludes cached-input discounts.

In cached mode, `/api/dossier` serves a matching live dossier cache when
available and otherwise uses the offline dossier. With a configured key,
`Regenerate with GPT-5` explicitly refreshes the live dossier. `/api/health`
reports availability without exposing the key. `make demo` explicitly selects
offline mode; its API routes use the local snapshot and cache and do not
construct an outbound HTTP client. `make data` is a separate refresh operation
and may contact external data providers.

## Known limitations

- The graph is a curated 40-gene DEE slice, not an exhaustive rare-disease
  knowledge graph. Source refreshes can change counts and cluster membership.
- Mechanism overlap is a transparent heuristic, not proof of causality.
  Experimental GO annotations and Reactome membership are incomplete, and the
  500-gene specificity cutoff is a deliberate gene-set-size filter.
- Phenotype similarity, asset phenotype coverage, and shared investigators
  are navigation aids. They do not establish clinical equivalence, trial
  eligibility, or a recommendation to contact a person.
- Offline dossiers are generated for the configured demo diseases/personas;
  live generation requires a valid configured key and may return different
  text. The application makes no clinical claims or treatment
  recommendations.
