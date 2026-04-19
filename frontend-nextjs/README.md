# frontend-nextjs

Next.js 14 (App Router) scaffold. During migration the full 15-panel UI lives
as a self-contained React + Tailwind (CDN) demo at `public/demo.html` and is
embedded on the landing page. Ports into TSX components can land incrementally
under `src/app/` and `src/components/`.

## Run

```bash
npm install
npm run dev   # http://localhost:3000
```

`/api/*` is proxied to the .NET backend (`BACKEND_URL`, default `http://localhost:5000`).

## Port checklist

- [ ] Architecture panel
- [ ] Live Query pipeline
- [ ] Graph Memory (SVG traversal)
- [ ] Episodic list (wired to `/api/episodes`)
- [ ] Kusto ingestion status
- [ ] Reasoning Chain Builder
- [ ] NL → Cypher
- [ ] Service Map
- [ ] Lifecycle
- [ ] Audit Timeline
- [ ] Agent Chat (`/api/chat`)
- [ ] SQL Impossible
- [ ] Schema Explorer
- [ ] Guardrails
- [ ] Performance
