# Memory Orchestrator

LangGraph pipeline implementing the 6-phase memory lifecycle
(Encode → Store → Retrieve → Reason → Update → Forget).

## Agents (`agents/`)

| Agent | Role |
|---|---|
| `ingestor`   | Kusto rows → Neo4j MERGE |
| `analyst`    | Multi-hop Cypher + confidence score |
| `summarizer` | Compress traversal + findings → <=120 token digest |
| `writer`     | Embed + upsert episode into pgvector/PostgreSQL |
| `reviewer`   | MITRE ATLAS guardrail check (confidence, depth, injection) |

## Run

```bash
pip install -r requirements.txt \
            -r ../graph-service/requirements.txt \
            -r ../vector-service/requirements.txt \
            -r ../episodic-memory/requirements.txt \
            -r ../kusto-ingestion/requirements.txt
python orchestrator.py
```
