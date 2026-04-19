# infrastructure

## Local dev

```powershell
docker compose -f infrastructure/docker-compose.yml up --build
```

Services:
| URL | Role |
|---|---|
| http://localhost:3000 | Next.js frontend (iframe-embedded 15-panel demo) |
| http://localhost:5000 | .NET API (`/health`, `/incidents/…`, `/chat`) |
| http://localhost:8000 | Python GraphRAG agent sidecar |
| http://localhost:7474 | Neo4j browser (neo4j / neo4jpass) |
| postgres://localhost:5432/memdb | PostgreSQL + pgvector |

Set `AZURE_OPENAI_KEY` and `AZURE_OPENAI_ENDPOINT` in your shell before
starting the stack.

## Kubernetes

Apply manifests under `k8s/` after building & pushing images:

```powershell
kubectl apply -f infrastructure/k8s/
```

Create the AOAI secret first:
```powershell
kubectl -n icm-memory create secret generic aoai `
  --from-literal=key=$env:AZURE_OPENAI_KEY `
  --from-literal=endpoint=$env:AZURE_OPENAI_ENDPOINT
```
