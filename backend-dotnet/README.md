# backend-dotnet

ASP.NET Core minimal-API that fronts Neo4j, pgvector episodic store, and the
Python GraphRAG agent.

## Endpoints

| Verb | Path | Purpose |
|---|---|---|
| GET  | `/health` | liveness |
| GET  | `/incidents/{id}/rca` | 3-hop root cause |
| GET  | `/incidents/{id}/blast?maxHops=3` | blast radius |
| GET  | `/episodes?incident=INC-456` | episodic list |
| POST | `/chat` `{question}` | proxy to IcM GraphRAG agent |

## Run

```bash
dotnet restore
dotnet run --project IcM.MemoryOrchestrator.csproj
```
