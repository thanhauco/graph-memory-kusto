"""Cypher templates for IcM graph reasoning (§7)."""
from __future__ import annotations

# Idempotent upsert from Kusto rows — §6
INGEST_INCIDENT = """
MERGE (i:Incident {id: $IncidentId})
  ON CREATE SET i.title = $Title,
                i.severity = $Severity,
                i.createdDate = datetime($CreatedDate),
                i.status = 'active'
  ON MATCH  SET i.title = $Title,
                i.severity = $Severity
MERGE (s:Service {name: $AffectedService})
MERGE (i)-[:AFFECTS]->(s)
"""

# §7 — 3-hop root cause
THREE_HOP_RCA = """
MATCH (i:Incident {id:$incidentId})
      -[:AFFECTS]->(s:Service)
      -[:DEPENDS_ON]->(d:Service)
      -[:CAUSED_BY]->(r:RootCause)
RETURN i.id AS incident, s.name AS affected, d.name AS depends_on, r.type AS root_cause
"""

# §7 — variable-length blast radius
BLAST_RADIUS = """
MATCH path=(i:Incident {id:$incidentId})-[:AFFECTS*1..$maxHops]->(s)
RETURN DISTINCT s.name AS service, length(path) AS hops
ORDER BY hops
"""

# §7 — cycle detection on DEPENDS_ON
CYCLE_DETECT = """
MATCH path=(s:Service)-[:DEPENDS_ON*2..8]->(s)
WHERE length(path) > 1
RETURN [n IN nodes(path) | n.name] AS cycle_members
LIMIT 10
"""

# §7 — shortest path between two services
SHORTEST_PATH = """
MATCH (a:Service {name:$from}), (b:Service {name:$to}),
      path = shortestPath((a)-[:DEPENDS_ON*]-(b))
RETURN [n IN nodes(path) | n.name] AS path
"""

# Regression attribution
REGRESSION_PATH = """
MATCH (i:Incident)-[:INTRODUCED_BY]->(d:Deployment)
WHERE d.deployedAt > datetime() - duration({days:$days})
RETURN i.id AS incident, d.version, d.deployedBy, d.service
ORDER BY d.deployedAt DESC
"""

# Index DDL — §3
SCHEMA_DDL = [
    "CREATE CONSTRAINT incident_id  IF NOT EXISTS FOR (i:Incident)   REQUIRE i.id IS UNIQUE",
    "CREATE CONSTRAINT service_name IF NOT EXISTS FOR (s:Service)    REQUIRE s.name IS UNIQUE",
    "CREATE CONSTRAINT team_name    IF NOT EXISTS FOR (t:Team)       REQUIRE t.name IS UNIQUE",
    "CREATE CONSTRAINT rc_type      IF NOT EXISTS FOR (r:RootCause)  REQUIRE r.type IS UNIQUE",
    "CREATE INDEX inc_created      IF NOT EXISTS FOR (i:Incident)   ON (i.createdDate)",
    "CREATE INDEX inc_severity     IF NOT EXISTS FOR (i:Incident)   ON (i.severity)",
    "CREATE INDEX svc_tier         IF NOT EXISTS FOR (s:Service)    ON (s.tier)",
]
