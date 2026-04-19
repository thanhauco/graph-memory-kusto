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

The chat panel posts questions to `/chat` and receives graph-grounded answers
from live Cypher queries. If Azure OpenAI env vars are present, answers are
optionally LLM-polished while staying grounded in the evidence rows.

#### How the chat server reasons

The server runs a cascade of intent handlers against live Neo4j. The first one
that matches returns an answer; if none matches, the hybrid search handler
fuses lexical ranking with graph expansion so free-form questions still work.

| Handler                 | Triggers                                      | Graph pattern |
|-------------------------|-----------------------------------------------|---------------|
| `h_by_root_cause`       | "related to DNS", "caused by CPU", aliases    | `(i)-[:AFFECTS]->(s)-[:CAUSED_BY]->(r:RootCause)` |
| `h_impact_count`        | "impact more than N services"                 | `(i)-[:AFFECTS]->(s)-[:DEPENDS_ON*1..3]->(d)`     |
| `h_blast_radius`        | "blast radius of X up to N hops"              | `(:Service {name:X})-[:DEPENDS_ON*1..N]->(s)`     |
| `h_root_cause_of_incident` | "why did INC-#### fail?"                   | `(i {id})-[:AFFECTS]->(s)-[:DEPENDS_ON]->(d)-[:CAUSED_BY]->(r)` |
| `h_regressions`         | "regressions", "introduced by deployment"     | `(i)-[:INTRODUCED_BY]->(d:Deployment)`            |
| `h_cycles`              | "detect cycles", "loops"                      | `(s)-[:DEPENDS_ON*2..8]->(s)` cycle               |
| `h_owner`               | "who owns", "which team"                      | `(t:Team)-[:OWNS]->(s)`                           |
| `h_dependents`          | "depends on", "consumers of"                  | `(s)-[:DEPENDS_ON]->(:Service {name:X})`          |
| `h_incidents_on_service` | "incidents on database", alias resolved      | `(i)-[:AFFECTS]->(:Service {name:X})`             |
| `h_hybrid_search`       | catch-all for free-form NL                    | TF-IDF style ranking + graph expansion (see below)|

NL aliases resolve natural phrasing to canonical services (e.g. "Azure Blob
storage" -> `BlobStore`, "the database" -> `DbService`, "the cache" ->
`CacheLayer`, "payment" -> `PaymentProc`, "notifications" -> `NotifyAPI`,
"kafka" -> `MsgBroker`, etc.).

#### Hybrid search (vector + graph)

The `h_hybrid_search` handler combines a vector-style lexical ranker with a
graph neighborhood expansion step, which is what turns "disk full problems on
the database" or "memory leak in auth" into grounded multi-hop answers:

1. **Corpus pull (graph read).** Pulls every `Incident` with its `AFFECTS`
    service and all linked `:CAUSED_BY` `RootCause` types/descriptions so each
    candidate doc already carries graph context.
2. **Vector-style ranking (lexical).** Tokenises the user question (stop-word
    filtered), scores each candidate with an overlap-over-length function that
    approximates cosine similarity across title + service + root-cause text.
3. **Graph expansion (multi-hop read).** Takes the top-ranked incident's
    service and expands `DEPENDS_ON*1..2` downstream services, the owning
    `Team`, and any linked `Deployment` (INTRODUCED_BY), so the answer cites
    the full blast radius and ownership path, not just the matched rows.
4. **Fusion + synthesis.** Returns the top-5 ranked incidents plus a
    grounded graph-expansion paragraph as a single answer with evidence rows.
    If Azure OpenAI env vars are set, the answer is LLM-polished while
    preserving the evidence.

Try it against the running server:

```powershell
$q = @(
    'disk full problems on the database',
    'memory leak in auth',
    'something about cross-az throttling',
    'what happened with overnight batch',
    'which incidents mention pool'
)
foreach ($x in $q) {
    $b = @{question = $x} | ConvertTo-Json
    (Invoke-RestMethod -Uri http://127.0.0.1:8765/chat -Method POST `
        -ContentType 'application/json' -Body $b).answer
}
```

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
