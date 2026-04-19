"""Seed Neo4j with the 400-incident mock dataset.

Usage
-----
    python kusto-ingestion/seed_neo4j.py           # full seed
    python kusto-ingestion/seed_neo4j.py --dry-run # print counts only
"""
from __future__ import annotations

import argparse
import sys
import pathlib

# Folder name contains a hyphen, so import mock_data directly.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from mock_data import generate  # type: ignore

try:
    from neo4j import GraphDatabase  # type: ignore
except Exception:  # pragma: no cover
    GraphDatabase = None  # type: ignore

import os

NEO4J_URI  = os.getenv("NEO4J_URI",  "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASS", "neo4jpass")


DDL = [
    "CREATE CONSTRAINT incident_id  IF NOT EXISTS FOR (i:Incident)   REQUIRE i.id IS UNIQUE",
    "CREATE CONSTRAINT service_name IF NOT EXISTS FOR (s:Service)    REQUIRE s.name IS UNIQUE",
    "CREATE CONSTRAINT team_name    IF NOT EXISTS FOR (t:Team)       REQUIRE t.name IS UNIQUE",
    "CREATE CONSTRAINT rc_type      IF NOT EXISTS FOR (r:RootCause)  REQUIRE r.type IS UNIQUE",
    "CREATE CONSTRAINT alert_id     IF NOT EXISTS FOR (a:Alert)      REQUIRE a.alertId IS UNIQUE",
    "CREATE CONSTRAINT deploy_ver   IF NOT EXISTS FOR (d:Deployment) REQUIRE d.version IS UNIQUE",
    "CREATE INDEX inc_created       IF NOT EXISTS FOR (i:Incident)   ON (i.createdDate)",
    "CREATE INDEX inc_severity      IF NOT EXISTS FOR (i:Incident)   ON (i.severity)",
    "CREATE INDEX svc_tier          IF NOT EXISTS FOR (s:Service)    ON (s.tier)",
]


UPSERT_INCIDENT = """
UNWIND $rows AS r
MERGE (i:Incident {id: r.id})
  SET i.title = r.title, i.severity = r.severity, i.status = r.status,
      i.createdDate = datetime(r.createdDate), i.ttm = r.ttm
MERGE (s:Service {name: r.affectedService})
MERGE (i)-[:AFFECTS]->(s)
MERGE (rc:RootCause {type: r.rootCause})
MERGE (s)-[:CAUSED_BY]->(rc)
"""

UPSERT_SERVICE = """
UNWIND $rows AS r
MERGE (s:Service {name: r.name})
  SET s.tier = r.tier, s.health = r.health, s.sla = r.sla,
      s.poolSize = r.poolSize, s.circuitBreaker = r.circuitBreaker
"""

UPSERT_TEAM = """
UNWIND $rows AS r
MERGE (t:Team {name: r.name})
  SET t.oncallRotation = r.oncallRotation,
      t.slackChannel = r.slackChannel,
      t.escalationPolicy = r.escalationPolicy
"""

UPSERT_ROOT_CAUSE = """
UNWIND $rows AS r
MERGE (rc:RootCause {type: r.type})
  SET rc.description = r.description,
      rc.frequency = r.frequency,
      rc.autoRemediation = r.autoRemediation
"""

UPSERT_DEPLOY = """
UNWIND $rows AS r
MERGE (d:Deployment {version: r.version})
  SET d.service = r.service, d.deployedAt = datetime(r.deployedAt),
      d.deployedBy = r.deployedBy, d.rollbackVersion = r.rollbackVersion,
      d.status = r.status
MERGE (s:Service {name: r.service})
"""

UPSERT_ALERT = """
UNWIND $rows AS r
MERGE (a:Alert {alertId: r.alertId})
  SET a.rule = r.rule, a.threshold = r.threshold,
      a.firedAt = datetime(r.firedAt), a.source = r.source
"""

EDGE_DEPENDS_ON = """
UNWIND $rows AS r
MATCH (a:Service {name: r[0]})
MATCH (b:Service {name: r[1]})
MERGE (a)-[:DEPENDS_ON]->(b)
"""

EDGE_OWNS = """
UNWIND $rows AS r
MATCH (t:Team {name: r[0]})
MATCH (s:Service {name: r[1]})
MERGE (t)-[:OWNS]->(s)
"""

EDGE_TRIGGERS = """
UNWIND $rows AS r
MATCH (a:Alert   {alertId: r[0]})
MATCH (i:Incident {id:    r[1]})
MERGE (a)-[:TRIGGERS]->(i)
"""

EDGE_INTRODUCED_BY = """
UNWIND $rows AS r
MATCH (i:Incident   {id: r[0]})
MATCH (d:Deployment {version: r[1]})
MERGE (i)-[:INTRODUCED_BY]->(d)
"""


def seed(dry_run: bool = False) -> dict:
    data = generate()
    summary = {
        "incidents":   len(data["incidents"]),
        "services":    len(data["services"]),
        "root_causes": len(data["root_causes"]),
        "teams":       len(data["teams"]),
        "deployments": len(data["deployments"]),
        "alerts":      len(data["alerts"]),
        **{f"rel_{k}": len(v) for k, v in data["edges"].items()},
    }
    if dry_run:
        return summary

    if GraphDatabase is None:
        raise RuntimeError("neo4j package not installed. pip install neo4j")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    try:
        with driver.session() as s:
            for stmt in DDL:
                s.run(stmt)
            s.run(UPSERT_SERVICE,     rows=data["services"])
            s.run(UPSERT_TEAM,        rows=data["teams"])
            s.run(UPSERT_ROOT_CAUSE,  rows=data["root_causes"])
            s.run(UPSERT_DEPLOY,      rows=data["deployments"])
            s.run(UPSERT_ALERT,       rows=data["alerts"])
            s.run(UPSERT_INCIDENT,    rows=data["incidents"])
            s.run(EDGE_DEPENDS_ON,    rows=data["edges"]["DEPENDS_ON"])
            s.run(EDGE_OWNS,          rows=data["edges"]["OWNS"])
            s.run(EDGE_TRIGGERS,      rows=data["edges"]["TRIGGERS"])
            s.run(EDGE_INTRODUCED_BY, rows=data["edges"]["INTRODUCED_BY"])
    finally:
        driver.close()
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    s = seed(dry_run=args.dry_run)
    print("seed summary:")
    for k, v in s.items():
        print(f"  {k:18s} {v}")
