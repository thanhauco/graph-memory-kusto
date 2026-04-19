# graph-memory-kusto

**GraphRAG + MemMachine Hybrid Memory System for Microsoft IcM.**
Combines episodic memory, graph-based semantic understanding, and Kusto
enterprise telemetry. See [`docs/CONTEXT.md`](docs/CONTEXT.md) for the full spec.

---

## Repo layout (matches `CONTEXT.md` §10)

| Path | Purpose |
|---|---|
| [`frontend-nextjs/`](frontend-nextjs/)         | Next.js 14 App Router UI (15 panels) |
| [`backend-dotnet/`](backend-dotnet/)           | ASP.NET Core Memory Orchestrator API |
| [`memory-orchestrator/`](memory-orchestrator/) | LangGraph pipeline + 5 agents |
| [`graph-service/`](graph-service/)             | Neo4j GDS wrapper + Cypher templates |
| [`vector-service/`](vector-service/)           | Azure OpenAI embeddings + pgvector search |
| [`episodic-memory/`](episodic-memory/)         | MemMachine-style episode store |
| [`kusto-ingestion/`](kusto-ingestion/)         | KQL → Neo4j MERGE pipeline |
| [`agents/`](agents/)                            | IcM GraphRAG agent |
| [`infrastructure/`](infrastructure/)           | `docker-compose.yml` + k8s manifests |
| [`docs/CONTEXT.md`](docs/CONTEXT.md)           | Canonical spec |
| [`index.html`](index.html)                      | Portable 15-panel demo (no build step) |

---

## Quickstart — demo UI

```powershell
python -m http.server 8080
# open http://localhost:8080/
```

## Quickstart — full stack (Docker)

```powershell
$env:AZURE_OPENAI_KEY      = "<key>"
$env:AZURE_OPENAI_ENDPOINT = "https://<your>.openai.azure.com"
docker compose -f infrastructure/docker-compose.yml up --build
```

| URL | What |
|---|---|
| http://localhost:3000  | Next.js frontend |
| http://localhost:5000  | .NET API |
| http://localhost:8000  | Python GraphRAG agent |
| http://localhost:7474  | Neo4j browser |

---

## Data flow (one query)

```
User → Next.js → .NET /chat → GraphRAG agent
                                 ├─ vector_service.ann_search      (pgvector)
                                 ├─ episodic_memory.vector_top_k   (episodes)
                                 ├─ graph_service.rca_three_hop    (Neo4j)
                                 └─ Azure OpenAI synthesis         (KV cached)
→ response (cites ep-### episodes)
```

## Memory lifecycle (LangGraph)

```
ingest → analyze → summarize → write → review
 (Kusto)  (Cypher)  (LLM)      (pgvector) (MITRE ATLAS gate)
```

## Security (MITRE ATLAS — CONTEXT.md §8)

- confidence gate ≥ 0.75 ([episodic-memory/store.py](episodic-memory/store.py), [memory-orchestrator/agents/reviewer.py](memory-orchestrator/agents/reviewer.py))
- 6-hop depth limit & 5s query timeout ([graph-service/graph_service.py](graph-service/graph_service.py))
- injection regex on all user-facing text ([memory-orchestrator/agents/reviewer.py](memory-orchestrator/agents/reviewer.py))

## Models

- `claude-opus-4-6` — deep coding
- `claude-sonnet-4-6` — agent chat
- Azure OpenAI `gpt-4o` + `text-embedding-3-small` — synthesis & embeddings
