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
python -m http.server 8080 --bind 127.0.0.1
# open http://localhost:8080/
```

### Agent Chat backend (Neo4j-grounded inference)

Start this in a second terminal before using the Advanced -> Agent Chat panel.

```powershell
$env:NEO4J_URI  = 'bolt://localhost:7688'
$env:NEO4J_USER = 'neo4j'
$env:NEO4J_PASS = 'neo4jpass'

python graph-service/chat_server.py
# listens on http://127.0.0.1:8765
```

The chat panel posts questions to /chat and receives graph-grounded answers
from live Cypher queries. If Azure OpenAI env vars are present, answers are
optionally LLM-polished while staying grounded in the evidence rows.

---

## Quickstart — dedicated Neo4j for this project (separate container)

Use this path when port 7687 is already occupied by another demo.

### 1) Run a dedicated Neo4j container

```powershell
# clean up old container name if it exists
docker rm -f gmk-neo4j 2>$null

# start a new isolated Neo4j for this repo
docker run -d --name gmk-neo4j `
    -p 7475:7474 -p 7688:7687 `
    -e NEO4J_AUTH=neo4j/neo4jpass `
    neo4j:5.19
```

### 2) Credentials and connection info

| Field | Value |
|---|---|
| Browser URL | http://localhost:7475 |
| Bolt URI | bolt://localhost:7688 |
| Username | neo4j |
| Password | neo4jpass |

### 3) Seed 400-incident mock graph data

```powershell
$env:NEO4J_URI  = 'bolt://localhost:7688'
$env:NEO4J_USER = 'neo4j'
$env:NEO4J_PASS = 'neo4jpass'

python kusto-ingestion/seed_neo4j.py
```

Expected summary includes:
- incidents 400
- services 17
- root_causes 8
- teams 6
- deployments 60
- alerts 595

### 4) Run all built-in multi-hop query demos

```powershell
$env:NEO4J_URI  = 'bolt://localhost:7688'
$env:NEO4J_USER = 'neo4j'
$env:NEO4J_PASS = 'neo4jpass'

python graph-service/demo_queries.py run-all
```

### 5) Run showcase queries used in presentation

```powershell
$env:NEO4J_URI  = 'bolt://localhost:7688'
$env:NEO4J_USER = 'neo4j'
$env:NEO4J_PASS = 'neo4jpass'

python graph-service/demo_queries.py run Q04
python graph-service/demo_queries.py run Q06
python graph-service/demo_queries.py run Q08
python graph-service/demo_queries.py run Q11
```

### 6) Stop and remove this dedicated Neo4j

```powershell
docker rm -f gmk-neo4j
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

Note: this compose stack maps Neo4j to ports 7474/7687. If those ports are
already used by another local graph demo, use the dedicated container flow
above (7475/7688).

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
