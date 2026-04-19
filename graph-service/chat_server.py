"""Graph-backed NL chat server for the Advanced -> Agent Chat panel.

Exposes a tiny HTTP endpoint (stdlib only) that accepts a natural-language
question, derives a Cypher query against Neo4j, executes it with a 5s
timeout, and returns a grounded answer plus evidence.

This replaces the previous in-browser canned responder with real multi-hop
graph reasoning on the seeded demo database.

Run
---
    $env:NEO4J_URI  = 'bolt://localhost:7688'
    $env:NEO4J_USER = 'neo4j'
    $env:NEO4J_PASS = 'neo4jpass'
    python graph-service/chat_server.py   # defaults to port 8765
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from neo4j import GraphDatabase  # type: ignore
except Exception:  # pragma: no cover
    GraphDatabase = None  # type: ignore


# ---------------------------------------------------------------------------
# Vocabulary used for NL intent extraction
# ---------------------------------------------------------------------------
SERVICE_NAMES = [
    "ApiGateway", "AuthService", "BillingAPI", "NotifyAPI",
    "ServiceA", "ServiceB", "ServiceC", "PaymentProc", "MsgBroker", "SearchSvc",
    "DbService", "CacheLayer", "BlobStore", "IndexStore", "QueueStore",
    "VectorDB", "GraphDB",
]

ROOT_CAUSE_KEYWORDS = {
    "DNSFailure":         ["dns", "dnsfailure", "resolver"],
    "HighCPU":            ["cpu", "highcpu", "cpu saturation"],
    "ConnPoolExhaustion": ["connection pool", "conn pool", "pool exhaust", "connpool"],
    "MemLeak":            ["memory leak", "memleak", "heap leak"],
    "DiskPressure":       ["disk pressure", "disk full", "disk space"],
    "NetworkPartition":   ["network partition", "cross-az", "throttling"],
    "ColdStart":          ["cold start", "coldstart"],
    "OvernightBatch":     ["overnight batch", "batch job"],
}


def extract_incident_id(text: str) -> str | None:
    m = re.search(r"INC-\d{3,6}", text, re.IGNORECASE)
    return m.group(0).upper() if m else None


def extract_service(text: str) -> str | None:
    low = text.lower()
    for name in SERVICE_NAMES:
        if name.lower() in low:
            return name
    return None


def extract_root_cause(text: str) -> str | None:
    low = text.lower()
    for rc, keys in ROOT_CAUSE_KEYWORDS.items():
        for k in keys:
            if k in low:
                return rc
    return None


def extract_int_after(text: str, markers: list[str]) -> int | None:
    for mk in markers:
        m = re.search(rf"{mk}\s*(\d+)", text, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Neo4j session helper
# ---------------------------------------------------------------------------
_driver = None

def driver():
    global _driver
    if _driver is not None:
        return _driver
    if GraphDatabase is None:
        raise RuntimeError("neo4j driver not installed")
    _driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI",  "bolt://localhost:7688"),
        auth=(os.getenv("NEO4J_USER", "neo4j"),
              os.getenv("NEO4J_PASS", "neo4jpass")),
    )
    return _driver


def run_cypher(cypher: str, params: dict | None = None) -> list[dict]:
    with driver().session() as s:
        rows = s.run(cypher, timeout=5, **(params or {}))
        return [dict(r) for r in rows]


def maybe_llm_synthesis(question: str, grounded_answer: str, evidence: list[dict]) -> str | None:
    """Optionally polish answer with existing GraphRAG agent when AOAI env is set."""
    if not (os.getenv("AZURE_OPENAI_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT")):
        return None
    try:
        repo_root = Path(__file__).resolve().parents[1]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from agents.icm_graphrag import answer as llm_answer  # type: ignore

        ev = json.dumps(evidence[:5], ensure_ascii=True)
        prompt = (
            "You are answering strictly from graph-grounded evidence. "
            "If evidence is insufficient, say so explicitly.\n\n"
            f"User question: {question}\n"
            f"Grounded answer draft: {grounded_answer}\n"
            f"Evidence rows: {ev}\n"
            "Provide a concise exact answer with IDs and entities."
        )
        out = llm_answer(prompt)
        return out.strip() if out else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Intent handlers - each returns (answer, evidence)
# ---------------------------------------------------------------------------
def h_by_root_cause(q: str) -> tuple[str, list[dict]] | None:
    """Any incidents related to X? / causing outage in X?"""
    low = q.lower()
    if not any(w in low for w in ("related to", "caused by", "causing", "due to", "because of", "about")):
        return None
    rc = extract_root_cause(q)
    if rc is None:
        return None
    cypher = """
MATCH (i:Incident)-[:AFFECTS]->(s:Service)-[:CAUSED_BY]->(r:RootCause {type:$rc})
RETURN i.id AS id, i.title AS title, i.severity AS severity, s.name AS service
ORDER BY i.severity, i.id LIMIT 25
"""
    rows = run_cypher(cypher, {"rc": rc})
    if not rows:
        return (f"No incidents linked to root cause `{rc}` in the current graph.", [])
    sample = ", ".join(f"{r['id']} ({r['service']})" for r in rows[:6])
    more = "" if len(rows) <= 6 else f" ... and {len(rows) - 6} more"
    return (
        f"Yes. {len(rows)} incident(s) map to root cause `{rc}` via AFFECTS -> CAUSED_BY: {sample}{more}.",
        rows,
    )


def h_impact_count(q: str) -> tuple[str, list[dict]] | None:
    """Any incidents impact > N services? (via dependency blast radius)"""
    low = q.lower()
    if not (("impact" in low or "affect" in low) and "service" in low):
        return None
    n = extract_int_after(q, ["more than", "over", ">"])
    min_services = (n + 1) if n is not None else 3
    max_hops = 3
    cypher = f"""
MATCH (i:Incident)-[:AFFECTS]->(s:Service)
OPTIONAL MATCH (s)-[:DEPENDS_ON*1..{max_hops}]->(d:Service)
WITH i, s, collect(DISTINCT d.name) + [s.name] AS impacted
WITH i, [x IN impacted WHERE x IS NOT NULL] AS impacted
WITH i, impacted, size(impacted) AS cnt
WHERE cnt >= $minServices
RETURN i.id AS id, i.severity AS severity, cnt AS impacted_count, impacted[0..8] AS sample
ORDER BY cnt DESC LIMIT 12
"""
    rows = run_cypher(cypher, {"minServices": min_services})
    if not rows:
        return (
            f"No incidents impact {min_services} or more services (direct + 3-hop blast radius).",
            [],
        )
    top = rows[0]
    lines = "; ".join(
        f"{r['id']} -> {r['impacted_count']} services ({', '.join(r['sample'][:5])})"
        for r in rows[:6]
    )
    return (
        f"Yes. {len(rows)} incident(s) impact {min_services}+ services (including dependency cascade up to {max_hops} hops). Top: {lines}.",
        rows,
    )


def h_blast_radius(q: str) -> tuple[str, list[dict]] | None:
    low = q.lower()
    if "blast radius" not in low:
        return None
    svc = extract_service(q)
    if svc is None:
        return None
    hops = extract_int_after(q, ["up to", "within"]) or 3
    hops = max(1, min(6, hops))
    cypher = f"""
MATCH p=(:Service {{name:$svc}})-[:DEPENDS_ON*1..{hops}]->(s:Service)
RETURN DISTINCT s.name AS service, s.tier AS tier, length(p) AS hops
ORDER BY hops, service
"""
    rows = run_cypher(cypher, {"svc": svc})
    if not rows:
        return (f"No downstream dependencies for {svc} up to {hops} hops.", [])
    by_hop: dict[int, list[str]] = {}
    for r in rows:
        by_hop.setdefault(r["hops"], []).append(r["service"])
    parts = [f"{h}-hop: {', '.join(sorted(set(v)))}" for h, v in sorted(by_hop.items())]
    return (f"Blast radius of {svc} ({hops} hops): " + " | ".join(parts) + ".", rows)


def h_root_cause_of_incident(q: str) -> tuple[str, list[dict]] | None:
    low = q.lower()
    if "root cause" not in low and "why" not in low and "fail" not in low:
        return None
    inc = extract_incident_id(q)
    if inc is None:
        return None
    cypher = """
MATCH (i:Incident {id:$id})-[:AFFECTS]->(s:Service)
OPTIONAL MATCH (s)-[:DEPENDS_ON]->(d:Service)-[:CAUSED_BY]->(r:RootCause)
OPTIONAL MATCH (s)-[:CAUSED_BY]->(r2:RootCause)
RETURN i.id AS id, s.name AS service,
       collect(DISTINCT d.name)[0..5] AS downstream,
       collect(DISTINCT coalesce(r.type, r2.type))[0..5] AS causes
"""
    rows = run_cypher(cypher, {"id": inc})
    if not rows:
        return (f"{inc} not found in graph.", [])
    r = rows[0]
    causes = [c for c in r["causes"] if c]
    downstream = [d for d in r["downstream"] if d]
    cause_str = ", ".join(causes) if causes else "no explicit CAUSED_BY edge"
    down_str = ", ".join(downstream) if downstream else "(no downstream)"
    return (
        f"{inc} affects {r['service']}. Downstream dependencies: {down_str}. "
        f"Likely root cause(s): {cause_str}.",
        rows,
    )


def h_dependents(q: str) -> tuple[str, list[dict]] | None:
    low = q.lower()
    if not ("depend on" in low or "depends on" in low or "who uses" in low or "consumers" in low):
        return None
    svc = extract_service(q)
    if svc is None:
        return None
    cypher = """
MATCH (s:Service)-[:DEPENDS_ON]->(:Service {name:$svc})
RETURN s.name AS service, s.tier AS tier
ORDER BY tier, service
"""
    rows = run_cypher(cypher, {"svc": svc})
    if not rows:
        return (f"No services depend on {svc}.", [])
    names = ", ".join(r["service"] for r in rows)
    return (f"{len(rows)} service(s) depend on {svc}: {names}.", rows)


def h_owner(q: str) -> tuple[str, list[dict]] | None:
    low = q.lower()
    if not ("owner" in low or "which team" in low or "owns" in low):
        return None
    svc = extract_service(q)
    if svc is None:
        return None
    cypher = """
MATCH (t:Team)-[:OWNS]->(:Service {name:$svc})
RETURN t.name AS team, t.slackChannel AS channel, t.oncallRotation AS oncall
"""
    rows = run_cypher(cypher, {"svc": svc})
    if not rows:
        return (f"{svc} has no owning team in the graph.", [])
    t = rows[0]
    return (
        f"{svc} is owned by {t['team']} (channel {t['channel']}, oncall {t['oncall']}).",
        rows,
    )


def h_cycles(q: str) -> tuple[str, list[dict]] | None:
    low = q.lower()
    if "cycle" not in low and "loop" not in low:
        return None
    cypher = """
MATCH p=(s:Service)-[:DEPENDS_ON*2..8]->(s)
WHERE length(p) > 1
RETURN [n IN nodes(p) | n.name] AS cycle, length(p) AS hops
ORDER BY hops LIMIT 5
"""
    rows = run_cypher(cypher)
    if not rows:
        return ("No dependency cycles detected in :DEPENDS_ON (depth 2..8).", [])
    sample = "; ".join(" -> ".join(r["cycle"]) for r in rows[:3])
    return (f"{len(rows)} dependency cycle(s) detected: {sample}.", rows)


def h_regressions(q: str) -> tuple[str, list[dict]] | None:
    low = q.lower()
    if "regression" not in low and "introduced" not in low and "deployment" not in low:
        return None
    cypher = """
MATCH (i:Incident)-[:INTRODUCED_BY]->(d:Deployment)
MATCH (i)-[:AFFECTS]->(s:Service)
RETURN i.id AS incident, d.version AS version, d.deployedBy AS deployed_by,
       s.name AS service, i.severity AS severity
ORDER BY d.deployedAt DESC LIMIT 15
"""
    rows = run_cypher(cypher)
    if not rows:
        return ("No regressions linked to deployments in the current window.", [])
    sample = "; ".join(
        f"{r['incident']} via {r['version']} -> {r['service']} (sev {r['severity']})"
        for r in rows[:5]
    )
    return (f"{len(rows)} regression(s) attributed to deployments. Recent: {sample}.", rows)


def h_incidents_on_service(q: str) -> tuple[str, list[dict]] | None:
    low = q.lower()
    if "incident" not in low:
        return None
    svc = extract_service(q)
    if svc is None:
        return None
    cypher = """
MATCH (i:Incident)-[:AFFECTS]->(:Service {name:$svc})
RETURN i.id AS id, i.title AS title, i.severity AS severity, i.status AS status
ORDER BY i.createdDate DESC LIMIT 20
"""
    rows = run_cypher(cypher, {"svc": svc})
    if not rows:
        return (f"No incidents found affecting {svc}.", [])
    sample = ", ".join(f"{r['id']} (sev {r['severity']})" for r in rows[:6])
    return (f"{len(rows)} incident(s) affecting {svc}: {sample}.", rows)


HANDLERS = [
    h_by_root_cause,      # DNS, CPU, MemLeak, etc.
    h_impact_count,       # > N services
    h_blast_radius,
    h_root_cause_of_incident,
    h_regressions,
    h_cycles,
    h_owner,
    h_dependents,
    h_incidents_on_service,  # fallback when a service is mentioned
]


def infer(question: str) -> dict:
    for h in HANDLERS:
        try:
            out = h(question)
        except Exception as e:
            return {
                "ok": False,
                "answer": f"Query failed while running `{h.__name__}`: {e}",
                "evidence": [],
                "handler": h.__name__,
            }
        if out is not None:
            ans, ev = out
            final_answer = maybe_llm_synthesis(question, ans, ev) or ans
            return {"ok": True, "answer": final_answer, "evidence": ev[:10], "handler": h.__name__}
    return {
        "ok": True,
        "answer": (
            "I can answer RCA, blast radius, dependents, owners, cycles, "
            "regressions, and incidents filtered by service or root cause. "
            "Examples: \"incidents related to DNS\", \"blast radius of ServiceA up to 3 hops\", "
            "\"why did INC-1008 fail?\", \"which team owns DbService?\", \"detect cycles\"."
        ),
        "evidence": [],
        "handler": "fallback",
    }


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
class ChatHandler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS, GET")

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
            return
        self.send_response(404)
        self._cors()
        self.end_headers()

    def do_POST(self):  # noqa: N802
        if self.path != "/chat":
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body.decode() or "{}")
        except Exception:
            payload = {}
        question = (payload.get("question") or "").strip()
        if not question:
            self.send_response(400)
            self._cors()
            self.end_headers()
            self.wfile.write(b'{"error":"missing question"}')
            return
        try:
            result = infer(question)
        except Exception as e:
            result = {"ok": False, "answer": f"server error: {e}", "evidence": [], "handler": "error"}
        body_out = json.dumps(result).encode()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body_out)))
        self.end_headers()
        self.wfile.write(body_out)

    def log_message(self, fmt, *args):  # quiet log
        sys.stderr.write("[chat] " + (fmt % args) + "\n")


def serve(port: int = 8765):
    srv = ThreadingHTTPServer(("127.0.0.1", port), ChatHandler)
    print(f"graph-chat listening on http://127.0.0.1:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    port = int(os.getenv("CHAT_PORT", "8765"))
    serve(port)
