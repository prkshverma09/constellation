# Constellation web

Mechanism-first rare-disease research navigation prototype.

## Run

```sh
pnpm install
make api
# in another shell
make web
```

`make web` runs the Next.js development server on port 3000 and uses the local API.
The fixture-backed client is still available for isolated interface work:

```sh
NEXT_PUBLIC_API_BASE=mock pnpm dev
```

Run the deterministic fixture generator and checks:

```sh
pnpm fixtures:gen
pnpm fixtures:check
pnpm typecheck
pnpm lint
pnpm build
pnpm e2e
```

`pnpm e2e` uses the real API on port 8000 by default. `make demo` starts the API in
offline mode together with the web development server. Screenshots are captured
from the real API with `pnpm screenshots` and saved to `~/shots/`.

## Map

- `app/`: search, disease, and dossier routes.
- `components/`: accessible search/persona controls, overview, D3 score/radial views, asset comparison, bridge people, coverage report, dossier, and edge inspector.
- `lib/api.ts`: contract types and typed endpoint client; `lib/mock.ts`: delayed, fixture-backed client.
- `fixtures/`: committed illustrative response and ledger snapshots; `scripts/`: deterministic generator, checker, screenshots.

## Choices

- Assets and bridge people share a comparator selector, defaulting to the highest-scoring cluster member.
- Hover/focus previews citations; selecting a citation opens the ledger drawer.
- P/M/V contribution bars use neutral greys; colour hues encode evidence class only and are always accompanied by text.
- Non-contract identifiers and source records in mock mode are synthetic fixtures; URLs use `example.org`, except the public NCT06555965 study link.
- Mock search filters fixture hits by case-insensitive matched text or label; exact matched-text hits take priority.
- Tailwind CSS v3.4, a system-font light theme, and no component library or dark mode.
- The cluster is a ranked evidence view with a radial mini-map, never a force-directed graph.
