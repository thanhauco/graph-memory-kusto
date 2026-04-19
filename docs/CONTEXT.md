# GraphRAG + MemMachine Hybrid System — Dev Handoff Context

> Generated from claude.ai conversation — April 18, 2026
> Canonical spec for this repo.

---

## 1. What Was Built

Three interactive panels demoing a hybrid memory architecture for Microsoft IcM combining episodic memory, graph semantic understanding, and Kusto telemetry.

### Part I — Core System
- **Architecture** — animated 7-layer SVG flow
- **Live Query** — 6-stage pipeline
- **Graph Memory** — IcM knowledge graph, animated multi-hop traversal
- **Episodic** — 4 stored episodes w/ confidence bars
- **Kusto Pipeline** — 4-stage ingestion flow

### Part II — Extended
- **Reasoning Chain Builder**
- **NL → Cypher** translator
- **IcM Service Map** (18 services, 3 tiers)
- **Memory Lifecycle** (6-phase ENC→STR→RET→RSN→UPD→FGT)
- **Audit Timeline**

### Part III — Advanced
- **Agent Chat** (Claude-powered IcM assistant, `claude-sonnet-4-6`)
- **SQL Impossible** (6 Cypher vs SQL comparisons)
- **Schema Explorer**
- **Guardrails** (9 MITRE ATLAS controls)
- **Performance**

---

## 2. Architecture

```
                   ┌──────────────────────┐
                   │        User          │
                   └──────────┬───────────┘
                              ▼
                    ┌───────────────────┐
                    │   Next.js UI      │
                    └────────┬──────────┘
                             ▼
                   ┌────────────────────┐
                   │ Memory Orchestrator│  ←── Kusto (Azure Data Explorer)
                   │ (.NET / LangGraph) │
                   └────────┬───────────┘
                            │
         ┌──────────────────┼──────────────────┐
         ▼                  ▼                  ▼
 ┌──────────────┐  ┌──────────────┐  ┌────────────────┐
 │ Episodic     │  │ Graph Memory │  │ Vector Memory  │
 │ (MemMachine) │  │ (Neo4j/GDS)  │  │ (pgvector)     │
 └──────┬───────┘  └──────┬───────┘  └────────┬───────┘
        └──────────────────┴───────────────────┘
                           ▼
                ┌─────────────────────────┐
                │  LLM Layer              │
                │  (Azure OpenAI / GPT-4) │
                └──────────┬──────────────┘
                           ▼
                    ┌──────────────┐
                    │ Memory Writer│  → encode + index
                    └──────┬───────┘
                           ▼
                    Persistent Storage
                    (PostgreSQL / CosmosDB / Blob)
```

---

## 3. Knowledge Graph Schema

### Entity Types (6)

| Entity | Key Properties | Index |
|---|---|---|
| `Incident` | id, title, severity (1-4), status, createdDate, ttm | id, createdDate, severity |
| `Service` | name, tier (API\|Platform\|Data), health, sla, poolSize, circuitBreaker | name, tier |
| `RootCause` | type, description, frequency, autoRemediation | type |
| `Team` | name, oncallRotation, slackChannel, escalationPolicy | name |
| `Alert` | alertId, rule, threshold, firedAt, source (Kusto\|Prometheus\|AzureMonitor) | alertId |
| `Deployment` | version, service, deployedAt, deployedBy, rollbackVersion, status | version, service |

### Relationship Types (subset of 43 total)
- `AFFECTS` — Incident → Service
- `DEPENDS_ON` — Service → Service
- `CAUSED_BY` — Service → RootCause
- `OWNS` — Team → Service
- `TRIGGERS` — Alert → Incident
- `INTRODUCED_BY` — Incident → Deployment

### Production Stats
- 18 service nodes · 43 relationship types · 127 edges
- 400 incidents ingested from Kusto
- 1.2M vectors in pgvector (HNSW index, p99 query: 38ms)

---

## 4. IcM Incident Chains (Episodic Memory)

```json
[
  { "episode_id":"ep-001","incident":"INC-456",
    "query":"Why did service A fail?",
    "hop_path":"INC-456 →[AFFECTS]→ ServiceA →[DEPENDS_ON]→ ServiceB →[CAUSED_BY]→ HighCPU",
    "hops":3,
    "outcome":"Restart ServiceB. Set CPU autoscale threshold 80%. Deploy circuit breaker.",
    "confidence":0.94 },
  { "episode_id":"ep-002","incident":"INC-789",
    "query":"API timeout cascade root cause",
    "hop_path":"ApiGateway →[ROUTES_TO]→ ServiceC →[DEPENDS_ON]→ DbService →[CAUSED_BY]→ ConnPoolExhaustion",
    "hops":4,
    "outcome":"Increase connection pool size 10→50.",
    "confidence":0.87 },
  { "episode_id":"ep-003","incident":"INC-234",
    "query":"DB connection pool exhaustion",
    "hop_path":"Multiple services → DbService → ConnPool saturation during overnight batch",
    "hops":5,
    "outcome":"pool_size=50 + circuit breaker on all downstream edges.",
    "confidence":0.91 },
  { "episode_id":"ep-004","incident":"INC-901",
    "query":"Deployment regression memory leak",
    "hop_path":"AuthService →[INTRODUCED_BY]→ v2.3.1 →[CONTAINS]→ MemLeak",
    "hops":2,
    "outcome":"Rollback to v2.3.0.",
    "confidence":0.78 }
]
```

---

## 5. 18-Service Topology

- **API tier:** ApiGateway, AuthService, BillingAPI, NotifyAPI
- **Platform tier:** ServiceA, ServiceB, ServiceC, PaymentProc, MsgBroker, SearchSvc
- **Data tier:** DbService, CacheLayer, BlobStore, IndexStore, QueueStore, VectorDB, GraphDB (+1 reserved)

Highest blast-radius node: `ServiceA` (3 direct dependents).

---

## 6. Kusto → Graph → Memory Pipeline

### KQL Source
```kql
Incidents
| where CreatedDate > ago(7d)
| project IncidentId, Title, AffectedService, Severity, CreatedDate
| order by CreatedDate desc
| take 400
```

### Graph Transform (Cypher MERGE)
```cypher
MERGE (i:Incident {id:$IncidentId})
MERGE (s:Service  {name:$AffectedService})
MERGE (i)-[:AFFECTS]->(s)
```

### Stages
1. Kusto Ingest — pull from ADX
2. Schema Transform — entity + relationship extraction
3. Graph Merge — upsert into Neo4j
4. Memory Integration — embeddings + episode + vectors

---

## 7. Multi-Hop Reasoning (Cypher)

### 3-hop root cause
```cypher
MATCH (i:Incident {id:"INC-456"})
      -[:AFFECTS]->(s)-[:DEPENDS_ON]->(d)-[:CAUSED_BY]->(r:RootCause)
RETURN i.id, s.name, d.name, r.type AS root_cause
```

### Variable-length blast radius
```cypher
MATCH path=(i:Incident {id:"INC-456"})-[:AFFECTS*1..5]->(s)
RETURN DISTINCT s.name, length(path) AS hops
ORDER BY hops
```

### Cycle detection
```cypher
MATCH path=(s:Service)-[:DEPENDS_ON*2..8]->(s)
WHERE length(path) > 1
RETURN [n IN nodes(path) | n.name] AS cycle
LIMIT 10
```

### Shortest path
```cypher
MATCH (a:Service {name:"ApiGateway"}), (b:Service {name:"DbService"}),
      path = shortestPath((a)-[:DEPENDS_ON*]-(b))
RETURN [n IN nodes(path) | n.name]
```

---

## 8. MITRE ATLAS Security Controls

| ATLAS ID | Attack | Risk | Status |
|---|---|---|---|
| AML.T0051 | Prompt injection via incident title | High | Mitigated |
| AML.T0054 | Indirect injection via graph node properties | High | Mitigated |
| AML.T0020 | Episodic memory poisoning | Medium | Partial |
| AML.T0031 | RAG poisoning via malicious graph nodes | High | Mitigated |
| AML.T0043 | MCP tool description poisoning | Medium | Mitigated |
| AML.T0025 | Model inversion via RCA enumeration | Medium | Partial |
| AML.T0057 | Cross-team episode exfiltration | Low | Mitigated |
| AML.T0040 | Resource exhaustion via deep traversal | Medium | Mitigated |
| AML.T0048 | Jailbreak via fictional incident framing | Low | Mitigated |

**Controls:** confidence gate ≥0.75, schema validation on MERGE, hash-verified MCP tool descriptions, 6-hop depth limit, 5s query timeout, RBAC via Azure AD.

---

## 9. Performance Baselines

| Query Type | Latency | Notes |
|---|---|---|
| Episodic retrieval (pgvector ANN) | 18 ms | HNSW |
| Graph traversal 3-hop (Neo4j GDS) | 48 ms | p99, ↓22% vs baseline |
| Kusto telemetry query (ADE) | 210 ms | |
| LLM synthesis — cached prefix | 380 ms | KV cache hit |
| LLM synthesis — cold | 1,240 ms | |
| End-to-end — cached | 670 ms | |
| End-to-end — cold | 1,820 ms | |

KV cache: shared prefix (sys prompt + schema + top-10 episodes ≈ 4,200 tokens), hit rate 71% → ≈0.54× token cost.

---

## 10. Repo Structure

```
graph-memory-kusto/
├── frontend-nextjs/          # React/Next.js UI (all 5 panel components)
├── backend-dotnet/           # Memory Orchestrator API
├── memory-orchestrator/      # LangGraph / SK agent pipeline
│   ├── agents/
│   │   ├── ingestor.py
│   │   ├── summarizer.py
│   │   ├── analyst.py
│   │   ├── writer.py
│   │   └── reviewer.py
│   └── orchestrator.py
├── graph-service/            # Neo4j GDS wrapper + Cypher templates
├── vector-service/           # pgvector + embedding pipeline
├── episodic-memory/          # MemMachine-inspired store
│   ├── schema.py
│   ├── store.py
│   └── retrieval.py
├── kusto-ingestion/          # KQL → graph pipeline
│   ├── kusto_to_neo4j.py
│   └── kql_queries/
├── agents/                   # IcM GraphRAG agent
├── infrastructure/
│   ├── docker-compose.yml
│   └── k8s/
└── docs/
    └── CONTEXT.md            # ← this file
```

---

## 11. Model Note

- `claude-opus-4-6` — deep coding
- `claude-sonnet-4-6` — faster iteration, agent chat in Part III

---

*Context generated by Claude Sonnet 4.6 · GraphRAG + MemMachine session · April 18, 2026*
