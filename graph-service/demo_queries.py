"""Library of multi-hop reasoning queries for demo / README.

All queries operate over the exact ER schema shown in the UI:

    Incident -[AFFECTS]-> Service
    Service  -[DEPENDS_ON]-> Service
    Service  -[CAUSED_BY]-> RootCause
    Team     -[OWNS]-> Service
    Alert    -[TRIGGERS]-> Incident
    Incident -[INTRODUCED_BY]-> Deployment


Each entry has:
    id, title, description, cypher (string), params (dict or example)

Run all queries against a live Neo4j:

    python graph-service/demo_queries.py            # list
    python graph-service/demo_queries.py run <id>   # execute one
    python graph-service/demo_queries.py run-all    # execute every query
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class DemoQuery:
    id: str
    title: str
    description: str
    hops: int
    cypher: str
    params: dict[str, Any]


QUERIES: list[DemoQuery] = [

    # ---------- single-hop (baseline) ----------
    DemoQuery(
        id="Q01",
        title="Incidents affecting a service (last 7d)",
        description="Find every Incident that AFFECTS a given service in the past week.",
        hops=1,
        cypher="""
MATCH (i:Incident)-[:AFFECTS]->(s:Service {name:$service})
WHERE i.createdDate > datetime() - duration({days:7})
RETURN i.id, i.title, i.severity, i.status, i.createdDate
ORDER BY i.createdDate DESC LIMIT 50
""".strip(),
        params={"service": "ServiceA"},
    ),

    # ---------- 2-hop ----------
    DemoQuery(
        id="Q02",
        title="Team responsible for an incident",
        description="Incident → Service → Team (who owns the service that broke).",
        hops=2,
        cypher="""
MATCH (i:Incident {id:$incidentId})-[:AFFECTS]->(s:Service)<-[:OWNS]-(t:Team)
RETURN i.id, s.name, t.name AS owner_team, t.slackChannel, t.oncallRotation
""".strip(),
        params={"incidentId": "INC-1000"},
    ),

    DemoQuery(
        id="Q03",
        title="Alerts that triggered an incident on a service",
        description="Alert → Incident → Service (firing rules per affected service).",
        hops=2,
        cypher="""
MATCH (a:Alert)-[:TRIGGERS]->(i:Incident)-[:AFFECTS]->(s:Service {name:$service})
RETURN s.name, a.rule, a.source, count(DISTINCT i) AS incidents
ORDER BY incidents DESC
""".strip(),
        params={"service": "DbService"},
    ),

    # ---------- 3-hop (classic root-cause) ----------
    DemoQuery(
        id="Q04",
        title="3-hop root cause (Incident -> Service -> DependsOn -> RootCause)",
        description="Classic RCA path — incident, the service it hit, what that depends on, and why.",
        hops=3,
        cypher="""
MATCH (i:Incident {id:$incidentId})
      -[:AFFECTS]->(s:Service)
      -[:DEPENDS_ON]->(d:Service)
      -[:CAUSED_BY]->(r:RootCause)
RETURN i.id AS incident, s.name AS affected,
       d.name AS depends_on, r.type AS root_cause, r.description
""".strip(),
        params={"incidentId": "INC-1000"},
    ),

    DemoQuery(
        id="Q05",
        title="Regression attribution - Incident -> Deployment -> Service",
        description="Which deployments introduced which incidents, and on which services.",
        hops=2,
        cypher="""
MATCH (i:Incident)-[:INTRODUCED_BY]->(d:Deployment)
WHERE d.deployedAt > datetime() - duration({days:7})
MATCH (i)-[:AFFECTS]->(s:Service)
RETURN i.id, i.severity, d.version, d.deployedBy, s.name AS service
ORDER BY d.deployedAt DESC
""".strip(),
        params={},
    ),

    # ---------- variable-length ----------
    DemoQuery(
        id="Q06",
        title="Blast radius up to N hops",
        description="Variable-length DEPENDS_ON traversal — everything reachable from a service.",
        hops=3,
        cypher="""
MATCH p=(:Service {name:$service})-[:DEPENDS_ON*1..$maxHops]->(s:Service)
RETURN DISTINCT s.name AS service, s.tier, length(p) AS hops
ORDER BY hops, service
""".strip(),
        params={"service": "ServiceA", "maxHops": 3},
    ),

    DemoQuery(
        id="Q07",
        title="Shortest dependency path between two services",
        description="Useful for answering 'how does A reach Z?' across mixed tiers.",
        hops=0,  # shortestPath — variable
        cypher="""
MATCH (a:Service {name:$from}), (b:Service {name:$to}),
      p = shortestPath((a)-[:DEPENDS_ON*]-(b))
RETURN [n IN nodes(p) | n.name] AS path, length(p) AS hops
""".strip(),
        params={"from": "ApiGateway", "to": "DbService"},
    ),

    # ---------- cycle detection ----------
    DemoQuery(
        id="Q08",
        title="Cycle detection (DEPENDS_ON 2..8)",
        description="Services that eventually depend on themselves — architectural smell.",
        hops=0,
        cypher="""
MATCH p=(s:Service)-[:DEPENDS_ON*2..8]->(s)
WHERE length(p) > 1
RETURN [n IN nodes(p) | n.name] AS cycle, length(p) AS hops
ORDER BY hops LIMIT 10
""".strip(),
        params={},
    ),

    # ---------- 4-hop cross-entity ----------
    DemoQuery(
        id="Q09",
        title="4-hop - Alert -> Incident -> Service -> Team",
        description="Who gets paged when an alert fires? Traces signal to on-call owner.",
        hops=3,
        cypher="""
MATCH (a:Alert)-[:TRIGGERS]->(i:Incident)-[:AFFECTS]->(s:Service)<-[:OWNS]-(t:Team)
WHERE a.source = $source
RETURN t.name AS team, t.slackChannel, count(DISTINCT i) AS incidents,
       collect(DISTINCT s.name)[0..5] AS sample_services
ORDER BY incidents DESC
""".strip(),
        params={"source": "Kusto"},
    ),

    DemoQuery(
        id="Q10",
        title="Top root causes per owning team",
        description="Team -> Service -> CAUSED_BY RootCause - what each team should automate first.",
        hops=2,
        cypher="""
MATCH (t:Team)-[:OWNS]->(s:Service)-[:CAUSED_BY]->(r:RootCause)
MATCH (i:Incident)-[:AFFECTS]->(s)
RETURN t.name AS team, r.type AS root_cause, count(DISTINCT i) AS incidents
ORDER BY team, incidents DESC
""".strip(),
        params={},
    ),

    # ---------- 5-hop regression path ----------
    DemoQuery(
        id="Q11",
        title="5-hop regression - Deployment introduces Incident that cascades",
        description="Deployment -> Incident -> Service -> DependsOn -> RootCause (full regression trail).",
        hops=4,
        cypher="""
MATCH (d:Deployment)<-[:INTRODUCED_BY]-(i:Incident)
      -[:AFFECTS]->(s:Service)
      -[:DEPENDS_ON]->(dep:Service)
      -[:CAUSED_BY]->(r:RootCause)
RETURN d.version, i.id, s.name AS affected,
       dep.name AS downstream, r.type AS root_cause
LIMIT 25
""".strip(),
        params={},
    ),

    # ---------- aggregation demos ----------
    DemoQuery(
        id="Q12",
        title="Highest blast-radius node",
        description="Service with the most distinct downstream dependents (3-hop).",
        hops=3,
        cypher="""
MATCH (s:Service)-[:DEPENDS_ON*1..3]->(t:Service)
WHERE s <> t
RETURN s.name AS service, count(DISTINCT t) AS reach
ORDER BY reach DESC LIMIT 5
""".strip(),
        params={},
    ),
]


# --------------------------------------------------------------------------
# CLI runner
# --------------------------------------------------------------------------
def _fmt(q: DemoQuery) -> str:
    return f"[{q.id}] {q.title}  (hops={q.hops})\n     params={q.params}"


def list_queries() -> None:
    for q in QUERIES:
        print(_fmt(q))


def run_one(query_id: str) -> list[dict]:
    q = next((x for x in QUERIES if x.id == query_id), None)
    if q is None:
        raise SystemExit(f"unknown query id: {query_id}")

    from neo4j import GraphDatabase  # type: ignore
    import os

    uri  = os.getenv("NEO4J_URI",  "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    pw   = os.getenv("NEO4J_PASS", "neo4jpass")

    cypher = q.cypher
    params = dict(q.params)
    # Neo4j requires literal upper bound for variable-length
    if "$maxHops" in cypher:
        cypher = cypher.replace("$maxHops", str(params.pop("maxHops", 3)))

    driver = GraphDatabase.driver(uri, auth=(user, pw))
    try:
        with driver.session() as s:
            rows = [dict(r) for r in s.run(cypher, **params, timeout=5)]
    finally:
        driver.close()
    return rows


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if not args:
        list_queries()
    elif args[0] == "run" and len(args) == 2:
        rows = run_one(args[1])
        print(f"{len(rows)} rows")
        for r in rows[:25]:
            print(r)
    elif args[0] == "run-all":
        for q in QUERIES:
            try:
                rows = run_one(q.id)
                print(f"[{q.id}] OK — {len(rows)} rows")
            except Exception as e:  # noqa: BLE001
                print(f"[{q.id}] FAIL — {e}")
    else:
        list_queries()
