"""Reasoning agent — runs multi-hop Cypher and scores confidence."""
from __future__ import annotations

from dataclasses import dataclass

from graph_service import GraphService


@dataclass
class AnalysisResult:
    hop_path: str
    hops: int
    confidence: float
    findings: list[dict]


def run(incident_id: str, max_hops: int = 3) -> AnalysisResult:
    gs = GraphService()
    try:
        rca = gs.rca_three_hop(incident_id)
        blast = gs.blast_radius(incident_id, max_hops=max_hops)
    finally:
        gs.close()

    if not rca:
        return AnalysisResult(hop_path="(no path)", hops=0, confidence=0.0, findings=[])

    r = rca[0]
    hop_path = (f"{r['incident']} →[AFFECTS]→ {r['affected']} "
                f"→[DEPENDS_ON]→ {r['depends_on']} →[CAUSED_BY]→ {r['root_cause']}")

    # simple heuristic: confidence scales with blast-radius coverage
    confidence = min(0.99, 0.6 + 0.05 * len(blast))
    return AnalysisResult(hop_path=hop_path, hops=3, confidence=confidence,
                          findings=blast)
