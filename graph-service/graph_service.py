"""Thin Neo4j driver wrapper with a 5s query timeout (§8 AML.T0040)."""
from __future__ import annotations

import os
from typing import Any

from neo4j import GraphDatabase, Driver

from . import cypher_templates as C

QUERY_TIMEOUT_SECONDS = int(os.getenv("GRAPH_QUERY_TIMEOUT", "5"))
MAX_HOP_DEPTH = int(os.getenv("GRAPH_MAX_HOPS", "6"))


class GraphService:
    def __init__(self, uri: str | None = None, user: str | None = None, password: str | None = None):
        self._driver: Driver = GraphDatabase.driver(
            uri or os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            auth=(user or os.getenv("NEO4J_USER", "neo4j"),
                  password or os.getenv("NEO4J_PASS", "neo4j")),
        )

    def close(self) -> None:
        self._driver.close()

    # ---- DDL ----------------------------------------------------------
    def init_schema(self) -> None:
        with self._driver.session() as s:
            for stmt in C.SCHEMA_DDL:
                s.run(stmt)

    # ---- Ingestion ----------------------------------------------------
    def merge_incident(self, row: dict[str, Any]) -> None:
        with self._driver.session() as s:
            s.run(C.INGEST_INCIDENT, **row)

    # ---- Queries ------------------------------------------------------
    def _run(self, query: str, **params):
        with self._driver.session() as s:
            return list(s.run(query, timeout=QUERY_TIMEOUT_SECONDS, **params))

    def rca_three_hop(self, incident_id: str):
        return [dict(r) for r in self._run(C.THREE_HOP_RCA, incidentId=incident_id)]

    def blast_radius(self, incident_id: str, max_hops: int = 3):
        max_hops = min(max_hops, MAX_HOP_DEPTH)
        # Neo4j requires interpolation for variable-length upper bound
        q = C.BLAST_RADIUS.replace("$maxHops", str(max_hops))
        return [dict(r) for r in self._run(q, incidentId=incident_id)]

    def cycles(self):
        return [dict(r) for r in self._run(C.CYCLE_DETECT)]

    def shortest(self, src: str, dst: str):
        return [dict(r) for r in self._run(C.SHORTEST_PATH, **{"from": src, "to": dst})]

    def regressions(self, days: int = 7):
        return [dict(r) for r in self._run(C.REGRESSION_PATH, days=days)]
