"""LangGraph orchestrator wiring the 5 agents into the 6-phase lifecycle.

Encode → Store → Retrieve → Reason → Update → Forget (§CONTEXT.md §10).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.graph import StateGraph, END

from agents import ingestor, summarizer, analyst, writer, reviewer


class State(TypedDict, total=False):
    # input
    kusto_rows: list[dict[str, Any]]
    incident_id: str
    # produced by agents
    ingest_result: Any
    analysis: Any
    summary: str
    episode: Any
    verdict: Any
    errors: list[str]


# ---- node implementations ----------------------------------------------
def _ingest(s: State) -> State:
    s["ingest_result"] = ingestor.run(s["kusto_rows"])
    return s


def _analyze(s: State) -> State:
    s["analysis"] = analyst.run(s["incident_id"])
    return s


def _summarize(s: State) -> State:
    a = s["analysis"]
    s["summary"] = summarizer.run(a.hop_path, context=str(a.findings))
    return s


def _write(s: State) -> State:
    a = s["analysis"]
    s["episode"] = writer.run(
        incident=s["incident_id"],
        query=f"Root cause for {s['incident_id']}",
        hop_path=a.hop_path,
        hops=a.hops,
        outcome=s["summary"],
        confidence=a.confidence,
    )
    return s


def _review(s: State) -> State:
    s["verdict"] = reviewer.run(s["episode"])
    if not s["verdict"].ok:
        s.setdefault("errors", []).extend(s["verdict"].reasons)
    return s


# ---- graph --------------------------------------------------------------
def build_graph():
    g = StateGraph(State)
    g.add_node("ingest",   _ingest)
    g.add_node("analyze",  _analyze)
    g.add_node("summarize",_summarize)
    g.add_node("write",    _write)
    g.add_node("review",   _review)

    g.set_entry_point("ingest")
    g.add_edge("ingest",    "analyze")
    g.add_edge("analyze",   "summarize")
    g.add_edge("summarize", "write")
    g.add_edge("write",     "review")
    g.add_edge("review",    END)
    return g.compile()


if __name__ == "__main__":
    from kusto_ingestion.kusto_to_neo4j import fetch_incidents
    graph = build_graph()
    final = graph.invoke({"kusto_rows": fetch_incidents(), "incident_id": "INC-456"})
    print("episode:", final.get("episode"))
    print("verdict:", final.get("verdict"))
