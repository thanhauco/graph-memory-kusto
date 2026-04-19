"""LangGraph orchestrator — 6-phase memory lifecycle (CONTEXT.md §10).

    Encode → Store → Retrieve → Reason → Update → Forget

Phase mapping to agent modules:
    Encode    → embed_one + summarizer     (compress + vectorize inputs)
    Store     → writer                      (upsert Episode into pgvector)
    Retrieve  → episodic_memory.vector_top_k (hybrid ANN)
    Reason    → analyst                     (multi-hop Cypher + confidence)
    Update    → reviewer                    (ATLAS guardrail gate)
    Forget    → episodic_memory.prune       (drop below 0.75 gate)
"""
from __future__ import annotations

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from typing import Any, TypedDict

from langgraph.graph import StateGraph, END

from agents import ingestor, summarizer, analyst, writer, reviewer

try:
    from episodic_memory import prune, vector_top_k  # type: ignore
    from vector_service import embed_one              # type: ignore
except Exception:  # optional heavy deps — allows dry-run without DB/AOAI
    prune = lambda *_a, **_kw: 0                      # type: ignore
    vector_top_k = lambda *_a, **_kw: []              # type: ignore
    embed_one = lambda _t: [0.0] * 1536                # type: ignore


class State(TypedDict, total=False):
    kusto_rows: list[dict[str, Any]]
    incident_id: str
    query: str
    ingest_result: Any
    encoded_query: list[float]
    retrieved: list[Any]
    analysis: Any
    summary: str
    episode: Any
    verdict: Any
    forgotten: int
    errors: list[str]


# ---- phase nodes --------------------------------------------------------
def _ingest(s: State) -> State:
    s["ingest_result"] = ingestor.run(s.get("kusto_rows", []))
    return s


def _encode(s: State) -> State:
    q = s.get("query") or f"Root cause for {s['incident_id']}"
    s["encoded_query"] = embed_one(q)
    return s


def _retrieve(s: State) -> State:
    s["retrieved"] = vector_top_k(s["encoded_query"], k=5)
    return s


def _reason(s: State) -> State:
    s["analysis"] = analyst.run(s["incident_id"])
    return s


def _store(s: State) -> State:
    a = s["analysis"]
    s["summary"] = summarizer.run(a.hop_path, context=str(a.findings))
    s["episode"] = writer.run(
        incident=s["incident_id"],
        query=s.get("query", f"Root cause for {s['incident_id']}"),
        hop_path=a.hop_path,
        hops=a.hops,
        outcome=s["summary"],
        confidence=a.confidence,
    )
    return s


def _update(s: State) -> State:
    s["verdict"] = reviewer.run(s["episode"])
    if not s["verdict"].ok:
        s.setdefault("errors", []).extend(s["verdict"].reasons)
    return s


def _forget(s: State) -> State:
    s["forgotten"] = prune(threshold=0.75)
    return s


# ---- graph --------------------------------------------------------------
def build_graph():
    g = StateGraph(State)
    g.add_node("ingest",   _ingest)
    g.add_node("encode",   _encode)
    g.add_node("retrieve", _retrieve)
    g.add_node("reason",   _reason)
    g.add_node("store",    _store)
    g.add_node("update",   _update)
    g.add_node("forget",   _forget)

    g.set_entry_point("ingest")
    g.add_edge("ingest",   "encode")
    g.add_edge("encode",   "retrieve")
    g.add_edge("retrieve", "reason")
    g.add_edge("reason",   "store")
    g.add_edge("store",    "update")
    g.add_edge("update",   "forget")
    g.add_edge("forget",   END)
    return g.compile()


PHASES = ["Encode", "Store", "Retrieve", "Reason", "Update", "Forget"]


if __name__ == "__main__":
    graph = build_graph()
    try:
        from kusto_ingestion.kusto_to_neo4j import fetch_incidents
        rows = fetch_incidents()
    except Exception:
        rows = []
    final = graph.invoke({
        "kusto_rows": rows,
        "incident_id": "INC-456",
        "query": "Why did ServiceA fail?",
    })
    print("phases executed:", PHASES)
    print("verdict:", final.get("verdict"))
    print("forgotten:", final.get("forgotten"))
