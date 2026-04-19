# GraphRAG + MemMachine Hybrid — IcM Memory UI

Single-file demo of the 15-panel hybrid memory system (Core / Extended / Advanced) described in `CONTEXT.md`.

## Run

Just open `index.html` in a browser — no build step. Uses React 18, Babel standalone, and Tailwind from CDN.

```powershell
start .\index.html
```

Or serve locally for a cleaner experience (avoids any CDN/CORS oddities):

```powershell
python -m http.server 8080
# then open http://localhost:8080/
```

## Tabs

**Core** — Architecture · Live Query · Graph Memory · Episodic · Kusto Pipeline
**Extended** — Reasoning Chain · NL → Cypher · Service Map · Lifecycle · Audit Timeline
**Advanced** — Agent Chat · SQL Impossible · Schema Explorer · Guardrails · Performance

## Notes

- All data (episodes, MITRE ATLAS controls, perf baselines, schema) is sourced directly from the handoff context.
- Agent Chat uses canned responses keyed to preset queries; wire a real call at `/api/chat` to go live with `claude-sonnet-4-6`.
- The `graph-memory-kusto` name is preserved; Kusto → Neo4j → pgvector pipeline is visualised but not executed.
