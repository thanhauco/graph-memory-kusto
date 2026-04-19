"""Kusto → graph ingestor agent (§2 Memory Orchestrator)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from graph_service import GraphService


@dataclass
class IngestResult:
    ingested: int
    merged_services: int


def run(rows: list[dict[str, Any]]) -> IngestResult:
    gs = GraphService()
    svc_names: set[str] = set()
    try:
        gs.init_schema()
        for r in rows:
            gs.merge_incident(r)
            svc_names.add(r["AffectedService"])
    finally:
        gs.close()
    return IngestResult(ingested=len(rows), merged_services=len(svc_names))
