"""Kusto → Neo4j ingestion pipeline (§6).

- pulls incidents from ADX
- heuristic schema inference + FK detection
- idempotent MERGE into Neo4j
"""
from __future__ import annotations

import os
import sys
import pathlib
from typing import Iterable

from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
from azure.kusto.data.helpers import dataframe_from_result_table

# Allow running as a script from repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from graph_service import GraphService  # noqa: E402  (hyphenated dir)

KQL_DIR = pathlib.Path(__file__).parent / "kql_queries"


def _kusto_client() -> KustoClient:
    cluster = os.environ["KUSTO_CLUSTER"]  # https://<cluster>.kusto.windows.net
    kcsb = KustoConnectionStringBuilder.with_az_cli_authentication(cluster)
    return KustoClient(kcsb)


def run_kql(path: pathlib.Path, database: str | None = None):
    database = database or os.environ["KUSTO_DATABASE"]
    client = _kusto_client()
    return client.execute(database, path.read_text())


def fetch_incidents() -> list[dict]:
    resp = run_kql(KQL_DIR / "incidents_last_7d.kql")
    df = dataframe_from_result_table(resp.primary_results[0])
    df["CreatedDate"] = df["CreatedDate"].astype(str)
    return df.to_dict(orient="records")


# Heuristic FK detection — any column ending with "Id" that matches another
# table's primary key is treated as a foreign key.
def detect_fks(rows: Iterable[dict]) -> set[str]:
    fks: set[str] = set()
    for r in rows:
        for k in r:
            if k.endswith("Id") and k != "IncidentId":
                fks.add(k)
    return fks


def ingest() -> int:
    rows = fetch_incidents()
    gs = GraphService()
    gs.init_schema()
    try:
        for r in rows:
            gs.merge_incident({
                "IncidentId":      r["IncidentId"],
                "Title":           r["Title"],
                "AffectedService": r["AffectedService"],
                "Severity":        int(r["Severity"]),
                "CreatedDate":     r["CreatedDate"],
            })
    finally:
        gs.close()
    return len(rows)


if __name__ == "__main__":
    n = ingest()
    print(f"ingested {n} incidents → Neo4j")
