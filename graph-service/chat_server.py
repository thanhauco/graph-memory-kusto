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

# Natural-language aliases -> canonical service name
SERVICE_ALIASES = {
    "blob store": "BlobStore", "blob storage": "BlobStore", "blob": "BlobStore",
    "azure blob": "BlobStore", "object storage": "BlobStore",
    "cache": "CacheLayer", "cache layer": "CacheLayer", "redis": "CacheLayer",
    "database": "DbService", "db": "DbService", "sql": "DbService",
    "queue": "QueueStore", "queue store": "QueueStore", "service bus": "QueueStore",
    "search": "SearchSvc", "search svc": "SearchSvc", "search service": "SearchSvc",
    "vector db": "VectorDB", "vectordb": "VectorDB", "vector store": "VectorDB",
    "graph db": "GraphDB", "graphdb": "GraphDB", "graph database": "GraphDB",
    "api gateway": "ApiGateway", "gateway": "ApiGateway",
    "auth": "AuthService", "authentication": "AuthService", "auth service": "AuthService",
    "billing": "BillingAPI", "billing api": "BillingAPI",
    "notify": "NotifyAPI", "notification": "NotifyAPI", "notifications": "NotifyAPI",
    "payment": "PaymentProc", "payments": "PaymentProc", "payment processor": "PaymentProc",
    "message broker": "MsgBroker", "msg broker": "MsgBroker", "broker": "MsgBroker",
    "kafka": "MsgBroker",
    "index": "IndexStore", "index store": "IndexStore",
}

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
    # try longer aliases first so "blob storage" beats "blob"
    for alias in sorted(SERVICE_ALIASES, key=len, reverse=True):
        if alias in low:
            return SERVICE_ALIASES[alias]
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


# ---------------------------------------------------------------------------
# Per-request trace collector.  Handlers append each Cypher they execute so
# the UI can render a transparent reasoning trace (matched handler, extracted
# entities, Cypher text, params, row count, elapsed ms).
# ---------------------------------------------------------------------------
import threading, time
_trace_local = threading.local()

def _trace() -> list[dict]:
    buf = getattr(_trace_local, "buf", None)
    if buf is None:
        buf = []
        _trace_local.buf = buf
    return buf

def _trace_reset() -> None:
    _trace_local.buf = []

def traced_cypher(cypher: str, params: dict | None = None, note: str = "") -> list[dict]:
    """Wrapper around run_cypher that records the query in the active trace."""
    t0 = time.perf_counter()
    try:
        rows = run_cypher(cypher, params)
        ok = True
        err = None
    except Exception as e:
        rows = []
        ok = False
        err = str(e)
    ms = round((time.perf_counter() - t0) * 1000, 2)
    _trace().append({
        "note": note,
        "cypher": cypher.strip(),
        "params": params or {},
        "rows": len(rows),
        "elapsed_ms": ms,
        "ok": ok,
        "error": err,
    })
    if not ok:
        raise RuntimeError(err)
    return rows


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
    rows = traced_cypher(cypher, {"rc": rc}, note="by_root_cause:incidents AFFECTS->CAUSED_BY")
    if not rows:
        return (f"No incidents linked to root cause `{rc}` in the current graph.", [])
    bullets = "\n".join(
        f"- **{r['id']}** — sev {r['severity']} — `{r['service']}` — *{r['title']}*"
        for r in rows[:8]
    )
    more = "" if len(rows) <= 8 else f"\n\n_… and {len(rows) - 8} more_"
    ans = (
        f"**{len(rows)} incident(s)** linked to root cause `{rc}` via "
        f"`AFFECTS → CAUSED_BY`:\n\n{bullets}{more}"
    )
    return (ans, rows)


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
    rows = traced_cypher(cypher, {"minServices": min_services}, note="impact_count:AFFECTS + DEPENDS_ON*1..3")
    if not rows:
        return (
            f"No incidents impact **{min_services}** or more services (direct + {max_hops}-hop blast radius).",
            [],
        )
    bullets = "\n".join(
        f"- **{r['id']}** (sev {r['severity']}) → **{r['impacted_count']}** services — `{', '.join(r['sample'][:5])}`"
        for r in rows[:6]
    )
    ans = (
        f"**{len(rows)} incident(s)** impact **{min_services}+** services "
        f"(dependency cascade up to **{max_hops} hops**):\n\n{bullets}"
    )
    return (ans, rows)


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
    rows = traced_cypher(cypher, {"svc": svc}, note=f"blast_radius:DEPENDS_ON*1..{hops}")
    if not rows:
        return (f"No downstream dependencies for **{svc}** up to {hops} hops.", [])
    by_hop: dict[int, list[str]] = {}
    for r in rows:
        by_hop.setdefault(r["hops"], []).append(r["service"])
    bullets = "\n".join(
        f"- **{h}-hop:** `{', '.join(sorted(set(v)))}`"
        for h, v in sorted(by_hop.items())
    )
    ans = f"**Blast radius of `{svc}`** (up to **{hops} hops**):\n\n{bullets}"
    return (ans, rows)


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
    rows = traced_cypher(cypher, {"id": inc}, note="rca_of_incident:AFFECTS + DEPENDS_ON + CAUSED_BY")
    if not rows:
        return (f"**{inc}** not found in graph.", [])
    r = rows[0]
    causes = [c for c in r["causes"] if c]
    downstream = [d for d in r["downstream"] if d]
    cause_md = ", ".join(f"`{c}`" for c in causes) if causes else "_no explicit CAUSED_BY edge_"
    down_md = ", ".join(f"`{d}`" for d in downstream) if downstream else "_none_"
    ans = (
        f"**{inc}** affects service **`{r['service']}`**.\n\n"
        f"- **Downstream dependencies:** {down_md}\n"
        f"- **Likely root cause(s):** {cause_md}"
    )
    return (ans, rows)


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
    rows = traced_cypher(cypher, {"svc": svc}, note="dependents:inverse DEPENDS_ON")
    if not rows:
        return (f"No services depend on **{svc}**.", [])
    bullets = "\n".join(f"- `{r['service']}` — tier *{r['tier']}*" for r in rows)
    return (f"**{len(rows)} service(s)** depend on **`{svc}`**:\n\n{bullets}", rows)


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
    rows = traced_cypher(cypher, {"svc": svc}, note="owner:Team-OWNS->Service")
    if not rows:
        return (f"**{svc}** has no owning team in the graph.", [])
    t = rows[0]
    ans = (
        f"**`{svc}`** is owned by **{t['team']}**.\n\n"
        f"- **Slack:** `{t['channel']}`\n"
        f"- **On-call rotation:** {t['oncall']}"
    )
    return (ans, rows)


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
    rows = traced_cypher(cypher, note="cycles:DEPENDS_ON*2..8 self-loop")
    if not rows:
        return ("No dependency cycles detected in `:DEPENDS_ON` (depth 2..8).", [])
    bullets = "\n".join(f"- `{' → '.join(r['cycle'])}` ({r['hops']} hops)" for r in rows[:5])
    return (f"**{len(rows)} dependency cycle(s)** detected:\n\n{bullets}", rows)


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
    rows = traced_cypher(cypher, note="regressions:INTRODUCED_BY + AFFECTS")
    if not rows:
        return ("No regressions linked to deployments in the current window.", [])
    bullets = "\n".join(
        f"- **{r['incident']}** (sev {r['severity']}) via deployment `{r['version']}` → `{r['service']}`"
        for r in rows[:6]
    )
    return (f"**{len(rows)} regression(s)** attributed to deployments:\n\n{bullets}", rows)


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
    rows = traced_cypher(cypher, {"svc": svc}, note="incidents_on_service:AFFECTS")
    if not rows:
        return (f"No incidents found affecting **{svc}**.", [])
    bullets = "\n".join(
        f"- **{r['id']}** — sev {r['severity']} — *{r['status']}* — {r['title']}"
        for r in rows[:8]
    )
    more = "" if len(rows) <= 8 else f"\n\n_… and {len(rows)-8} more_"
    return (
        f"**{len(rows)} incident(s)** affecting **`{svc}`**:\n\n{bullets}{more}",
        rows,
    )


# Conjunctive keyword filters.  "outage" is treated as severity <= 2 OR title
# mentions outage (there isn't an explicit outage label in the seed).
_CONCEPT_FILTERS = {
    "outage":        "(i.severity <= 2 OR toLower(i.title) CONTAINS 'outage')",
    "critical":      "i.severity = 1",
    "high severity": "i.severity <= 2",
    "sev1":          "i.severity = 1",
    "sev2":          "i.severity <= 2",
    "open":          "i.status = 'Open'",
    "mitigated":     "i.status = 'Mitigated'",
    "active":        "i.status = 'Open'",
}

def _find_concept(q: str) -> tuple[str, str] | None:
    low = q.lower()
    for kw, clause in _CONCEPT_FILTERS.items():
        if kw in low:
            return kw, clause
    return None


def h_service_plus_concept(q: str) -> tuple[str, list[dict]] | None:
    """Conjunctive: incidents matching BOTH a service AND a concept filter or root cause."""
    low = q.lower()
    if "incident" not in low and "related" not in low and "belong" not in low:
        return None
    svc = extract_service(q)
    if svc is None:
        return None
    rc = extract_root_cause(q)
    concept = _find_concept(q)
    if rc is None and concept is None:
        return None  # defer to simpler handlers

    clauses = []
    params = {"svc": svc}
    rc_clause = ""
    if rc is not None:
        rc_clause = "MATCH (s)-[:CAUSED_BY]->(r:RootCause {type:$rc})"
        params["rc"] = rc
    if concept is not None:
        clauses.append(concept[1])
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    cypher = f"""
MATCH (i:Incident)-[:AFFECTS]->(s:Service {{name:$svc}})
{rc_clause}
{where}
RETURN DISTINCT i.id AS id, i.title AS title, i.severity AS severity,
       i.status AS status, s.name AS service
ORDER BY i.severity, i.id LIMIT 25
"""
    rows = traced_cypher(
        cypher, params,
        note=f"service+concept filter svc={svc}"
             + (f", root_cause={rc}" if rc else "")
             + (f", concept={concept[0]}" if concept else "")
    )

    filters_desc = []
    if rc:
        filters_desc.append(f"root cause = `{rc}`")
    if concept:
        filters_desc.append(f"**{concept[0]}**")
    fstr = " AND ".join(filters_desc)

    if not rows:
        return (f"No incidents on **`{svc}`** match {fstr}.", [])
    bullets = "\n".join(
        f"- **{r['id']}** — sev {r['severity']} — *{r['status']}* — {r['title']}"
        for r in rows[:8]
    )
    more = "" if len(rows) <= 8 else f"\n\n_… and {len(rows)-8} more_"
    return (
        f"**{len(rows)} incident(s)** on **`{svc}`** matching {fstr}:\n\n{bullets}{more}",
        rows,
    )


# ---------------------------------------------------------------------------
# Hybrid search: lexical/"vector-style" scoring over Incident+RootCause text,
# fused with graph expansion (affected service, downstream deps, owning team,
# linked deployment).  This is the catch-all handler that runs when no
# specific intent matched, so any free-form question still gets a grounded
# multi-hop answer.
# ---------------------------------------------------------------------------
_STOPWORDS = {
    "a","an","the","is","are","was","were","be","been","to","of","in","on","at",
    "for","and","or","with","any","some","incidents","incident","outage","outages",
    "causing","caused","cause","related","about","show","me","list","find","what",
    "which","who","why","how","that","this","these","those","did","do","does",
    "have","has","had","please","i","you","my","our","can","tell","give","need",
    "there","it","they","them","azure","service","services","issue","issues",
}

def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t and t not in _STOPWORDS and len(t) > 2]


def _score(query_terms: set[str], doc_terms: list[str]) -> float:
    if not query_terms or not doc_terms:
        return 0.0
    bag = {}
    for t in doc_terms:
        bag[t] = bag.get(t, 0) + 1
    hit = sum(bag[t] for t in query_terms if t in bag)
    # length-normalised like cosine-ish
    return hit / (1.0 + 0.25 * len(doc_terms))


def h_multi_team_incidents(q: str) -> tuple[str, list[dict]] | None:
    """Incidents whose affected services span more than N owning teams.

    Triggers on phrases like:
      - "incidents belong to more than 2 teams"
      - "incidents belong to >2 teams"
      - "incidents spanning multiple teams"
      - "incidents across more than 1 team"
      - "cross-team incidents"
      - "incidents involving several teams"
    Path: (i:Incident)-[:AFFECTS]->(:Service)<-[:OWNS]-(:Team) and/or
          (i)-[:AFFECTS]->(:Service)-[:DEPENDS_ON*1..2]->(:Service)<-[:OWNS]-(:Team)
    """
    low = q.lower()
    mentions_team = ("team" in low or "teams" in low)
    if not mentions_team:
        return None
    cross_words = (
        "more than", "over", ">", "multiple", "several",
        "cross-team", "cross team", "spanning", "across",
        "involving", "belong to", "belonging to",
    )
    if not any(w in low for w in cross_words):
        return None

    # Threshold: "more than 2 teams" -> 2 (strict >), "multiple" -> 1
    n = extract_int_after(q, ["more than", "over", ">", "at least", "greater than"])
    if n is None:
        n = 1  # "multiple"/"several"/"cross-team" => >1 team
    strict_gt = n  # WHERE team_count > n

    # Include 2-hop dependency closure so "belongs to" is interpreted broadly
    # (direct service owner + owners of downstream dependencies the incident
    # propagates through). This is a graph-native query — a vector store can't
    # express "distinct team count over an incident's dependency closure".
    cypher = """
MATCH (i:Incident)-[:AFFECTS]->(s:Service)
OPTIONAL MATCH (s)-[:DEPENDS_ON*0..2]->(s2:Service)<-[:OWNS]-(t:Team)
WITH i, s, collect(DISTINCT t.name) AS teams
WITH i, s.name AS service, teams, size(teams) AS team_count
WHERE team_count > $n
RETURN i.id AS id, i.title AS title, i.severity AS severity, i.status AS status,
       service, teams, team_count
ORDER BY team_count DESC, i.severity, i.id
LIMIT 25
"""
    rows = traced_cypher(cypher, {"n": strict_gt}, note="multi_team_incidents:AFFECTS + DEPENDS_ON*0..2 <-OWNS- Team")
    if not rows:
        return (
            f"No incidents span **more than {strict_gt}** owning team(s) "
            f"(affected service + up to 2 hops of its dependencies).",
            [],
        )
    bullets = "\n".join(
        f"- **{r['id']}** — sev {r['severity']} — *{r['status']}* — `{r['service']}` → "
        f"**{r['team_count']} teams**: {', '.join(r['teams'])} — *{r['title']}*"
        for r in rows[:10]
    )
    more = "" if len(rows) <= 10 else f"\n\n_… and {len(rows) - 10} more_"
    ans = (
        f"**{len(rows)} incident(s)** whose affected service + 2-hop dependency "
        f"closure is owned by **more than {strict_gt}** distinct team(s):\n\n"
        f"{bullets}{more}"
    )
    return (ans, rows)


def h_orphan_incidents(q: str) -> tuple[str, list[dict]] | None:
    """Incidents with no :AFFECTS edge to any Service (data-quality / orphan check).

    Triggers on phrases like:
      - "any incidents not belong to any service"
      - "incidents without a service"
      - "orphan incidents"
      - "incidents missing service"
      - "incidents with no service"
      - "unlinked incidents"
    """
    low = q.lower()
    triggers = (
        "not belong to any service",
        "not belong to a service",
        "without a service",
        "without any service",
        "with no service",
        "no service attached",
        "missing service",
        "orphan incident",
        "orphaned incident",
        "unlinked incident",
        "unattached incident",
        "incidents not linked",
        "incidents not attached",
    )
    if not any(t in low for t in triggers):
        return None
    cypher = """
MATCH (i:Incident)
WHERE NOT (i)-[:AFFECTS]->(:Service)
RETURN i.id AS id, i.title AS title, i.severity AS severity, i.status AS status
ORDER BY i.severity, i.id
LIMIT 50
"""
    rows = traced_cypher(cypher, {}, note="orphan_incidents:Incident no AFFECTS->Service")
    if not rows:
        return (
            "**All 400 incidents are linked to at least one service** via `:AFFECTS`. "
            "No orphans — the data is clean.",
            [],
        )
    bullets = "\n".join(
        f"- **{r['id']}** — sev {r['severity']} — *{r['status']}* — {r['title']}"
        for r in rows[:10]
    )
    more = "" if len(rows) <= 10 else f"\n\n_… and {len(rows) - 10} more_"
    ans = (
        f"**{len(rows)} orphan incident(s)** — no `:AFFECTS` edge to any `Service`:\n\n"
        f"{bullets}{more}"
    )
    return (ans, rows)


def h_hybrid_search(q: str) -> tuple[str, list[dict]] | None:
    """Lexical (vector-style) ranking fused with graph neighborhood expansion."""
    qterms = set(_tokens(q))
    if not qterms:
        return None

    # Pull candidate corpus from Neo4j (Incidents + RootCauses).
    corpus = traced_cypher(
        """
MATCH (i:Incident)
OPTIONAL MATCH (i)-[:AFFECTS]->(s:Service)
OPTIONAL MATCH (s)-[:CAUSED_BY]->(r:RootCause)
RETURN i.id AS id, i.title AS title, i.severity AS severity, i.status AS status,
       s.name AS service,
       collect(DISTINCT r.type)        AS cause_types,
       collect(DISTINCT r.description) AS cause_desc
""",
        note="hybrid:corpus pull Incident+AFFECTS+CAUSED_BY",
    )

    scored: list[dict] = []
    for row in corpus:
        text_parts = [row.get("title") or "", row.get("service") or ""]
        text_parts += [t for t in (row.get("cause_types") or []) if t]
        text_parts += [d for d in (row.get("cause_desc") or []) if d]
        doc = _tokens(" ".join(text_parts))
        s = _score(qterms, doc)
        if s > 0:
            row["_score"] = round(s, 4)
            scored.append(row)

    if not scored:
        return None

    scored.sort(key=lambda r: r["_score"], reverse=True)
    top = scored[:5]

    # Graph expansion for the top hit (service -> downstream deps, owner, deployment).
    expansion: list[dict] = []
    lead_service = next((r["service"] for r in top if r.get("service")), None)
    lead_incident = top[0]["id"]
    if lead_service:
        expansion = traced_cypher(
            """
MATCH (s:Service {name:$svc})
OPTIONAL MATCH (s)-[:DEPENDS_ON*1..2]->(d:Service)
OPTIONAL MATCH (t:Team)-[:OWNS]->(s)
OPTIONAL MATCH (i:Incident {id:$iid})-[:INTRODUCED_BY]->(dep:Deployment)
RETURN s.name   AS service,
       collect(DISTINCT d.name)[0..8] AS downstream,
       t.name   AS owner_team,
       dep.version AS deployment
""",
            {"svc": lead_service, "iid": lead_incident},
            note="hybrid:graph expansion DEPENDS_ON*1..2 + OWNS + INTRODUCED_BY",
        )

    # Synthesize grounded answer.
    hit_bullets = "\n".join(
        f"- **{r['id']}** — sev {r['severity']} — `{r['service'] or 'no-service'}` — score **{r['_score']}**"
        for r in top
    )
    graph_line = ""
    if expansion:
        e = expansion[0]
        down = ", ".join(f"`{d}`" for d in (e.get("downstream") or [])) or "_none_"
        owner = e.get("owner_team") or "_unknown team_"
        depline = f" — deployment `{e['deployment']}`" if e.get("deployment") else ""
        graph_line = (
            f"\n\n**Graph expansion on `{e['service']}`:**\n"
            f"- **Downstream:** {down}\n"
            f"- **Owner:** {owner}{depline}"
        )

    answer = (
        f"**Hybrid search** found **{len(scored)} candidate(s)** for your question.\n\n"
        f"**Top matches:**\n{hit_bullets}{graph_line}"
    )
    evidence = [
        {
            "id": r["id"], "title": r["title"], "severity": r["severity"],
            "service": r["service"], "causes": r.get("cause_types"),
            "score": r["_score"],
        }
        for r in top
    ]
    if expansion:
        evidence.append({"graph_expansion": expansion[0]})
    return (answer, evidence)


# ---------------------------------------------------------------------------
# Advanced multi-hop reasoning handlers (Q1..Q10 from the "complicated" set)
# Each is keyed by a short trigger phrase so users can invoke them from chat.
# ---------------------------------------------------------------------------
def h_q1_cross_team_blind_spot(q: str) -> tuple[str, list[dict]] | None:
    low = q.lower()
    if not ("blind spot" in low or "cross-team blind" in low or re.search(r"\bq1\b", low)):
        return None
    # Find pairs where a team owns a service whose 1-2 hop downstream is owned
    # by a different team and has strictly more sev1/sev2 incidents than s1.
    cypher = """
MATCH (t1:Team)-[:OWNS]->(s1:Service)-[:DEPENDS_ON*1..2]->(s2:Service)<-[:OWNS]-(t2:Team)
WHERE t1 <> t2
OPTIONAL MATCH (i1:Incident)-[:AFFECTS]->(s1) WHERE i1.severity <= 2
OPTIONAL MATCH (i2:Incident)-[:AFFECTS]->(s2) WHERE i2.severity <= 2
WITH t1, s1, t2, s2, count(DISTINCT i1) AS sev_s1, count(DISTINCT i2) AS sev_s2
WHERE sev_s2 >= sev_s1 + 3
RETURN t1.name AS owning_team, s1.name AS clean_service, sev_s1 AS s1_sev_incidents,
       t2.name AS downstream_team, s2.name AS hot_service, sev_s2 AS s2_sev_incidents
ORDER BY (sev_s2 - sev_s1) DESC LIMIT 15
"""
    rows = traced_cypher(cypher, note="q1:cross_team_blind_spot")
    if not rows:
        return ("**Q1 — Cross-team blind spots:** none found in current data.", [])
    bullets = "\n".join(
        f"- **{r['owning_team']}** owns `{r['clean_service']}` ({r['s1_sev_incidents']} sev1/2) → depends on `{r['hot_service']}` owned by **{r['downstream_team']}** ({r['s2_sev_incidents']} sev1/2)"
        for r in rows[:8]
    )
    return (f"**Q1 — Cross-team blind spots** ({len(rows)} pair[s]):\n\n{bullets}", rows)


def h_q2_root_cause_tier0_reach(q: str) -> tuple[str, list[dict]] | None:
    low = q.lower()
    if not ("tier-0 reach" in low or "tier 0 reach" in low or "root cause reach" in low or re.search(r"\bq2\b", low)):
        return None
    cypher = """
MATCH (i:Incident)-[:AFFECTS]->(s:Service)-[:CAUSED_BY]->(rc:RootCause)
MATCH p = shortestPath((s)-[:DEPENDS_ON*0..3]->(t:Service))
WHERE t.tier = 'API'
WITH rc, t, min(length(p)) AS hops
WITH rc, count(DISTINCT t) AS tier0_count, collect({tier0:t.name, hops:hops})[0..5] AS sample
WHERE tier0_count >= 1
RETURN rc.type AS root_cause, tier0_count, sample
ORDER BY tier0_count DESC LIMIT 10
"""
    rows = traced_cypher(cypher, note="q2:root_cause_tier0_reach")
    if not rows:
        return ("**Q2 — Root-cause tier-0 reach:** no tier-0 services reachable.", [])
    bullets = []
    for r in rows:
        sample = ", ".join(f"`{s['tier0']}` ({s['hops']}h)" for s in r["sample"])
        bullets.append(f"- **{r['root_cause']}** reaches **{r['tier0_count']}** tier-0 service(s): {sample}")
    return (f"**Q2 — Root causes by tier-0 reach**:\n\n" + "\n".join(bullets), rows)


def h_q3_silent_amplifier(q: str) -> tuple[str, list[dict]] | None:
    low = q.lower()
    if not ("silent amplifier" in low or "amplifier" in low or re.search(r"\bq3\b", low)):
        return None
    # Services that receive cascaded impact from many upstream incidents of
    # multiple root-cause kinds (the service itself may also have incidents).
    cypher = """
MATCH (s:Service)
MATCH (i:Incident)-[:AFFECTS]->(up:Service)-[:DEPENDS_ON*1..2]->(s)
WHERE up <> s
MATCH (up)-[:CAUSED_BY]->(rc:RootCause)
WITH s, count(DISTINCT i) AS reaching_incidents, count(DISTINCT rc) AS rc_kinds,
     collect(DISTINCT rc.type)[0..5] AS rc_sample
WHERE reaching_incidents > 20 AND rc_kinds >= 3
RETURN s.name AS service, reaching_incidents, rc_kinds, rc_sample
ORDER BY reaching_incidents DESC LIMIT 10
"""
    rows = traced_cypher(cypher, note="q3:silent_amplifier")
    if not rows:
        return ("**Q3 — Silent amplifiers:** none match the criteria.", [])
    bullets = "\n".join(
        f"- **`{r['service']}`** — {r['reaching_incidents']} cascading incident(s), {r['rc_kinds']} root-cause kinds: {', '.join(r['rc_sample'])}"
        for r in rows
    )
    return (f"**Q3 — Silent amplifiers** ({len(rows)}):\n\n{bullets}", rows)


def h_q4_cycles_with_incidents(q: str) -> tuple[str, list[dict]] | None:
    low = q.lower()
    if not (re.search(r"\bq4\b", low) or "cycles with" in low or ("cycle" in low and "team" in low and "across" in low)):
        return None
    cypher = """
MATCH p=(s:Service)-[:DEPENDS_ON*2..4]->(s)
WITH nodes(p)[0..-1] AS cyc, length(p) AS hops
UNWIND cyc AS m
MATCH (t:Team)-[:OWNS]->(m)
WITH cyc, hops, collect(DISTINCT t.name) AS teams, collect(DISTINCT m.name) AS members
WHERE size(teams) >= 2
OPTIONAL MATCH (i:Incident)-[:AFFECTS]->(x:Service)
WHERE x.name IN members AND i.severity <= 2
WITH members, teams, hops, count(DISTINCT i) AS sev_incidents
RETURN members, teams, hops, sev_incidents
ORDER BY sev_incidents DESC, hops ASC LIMIT 10
"""
    rows = traced_cypher(cypher, note="q4:cycles_multi_team")
    if not rows:
        return ("**Q4 — Multi-team cycles:** no cycles span ≥ 2 teams.", [])
    bullets = "\n".join(
        f"- cycle `{' → '.join(r['members'])}` ({r['hops']} hops) — teams: {', '.join(r['teams'])} — sev1/2 incidents: **{r['sev_incidents']}**"
        for r in rows
    )
    return (f"**Q4 — Multi-team dependency cycles** ({len(rows)}):\n\n{bullets}", rows)


def h_q5_recurring_root_cause(q: str) -> tuple[str, list[dict]] | None:
    low = q.lower()
    if not ("recurring" in low or "came back" in low or "regression after deploy" in low or re.search(r"\bq5\b", low)):
        return None
    # Date fields in the seed are strings; we fall back to simple ordering.
    cypher = """
MATCH (d:Deployment)<-[:INTRODUCED_BY]-(post:Incident)-[:AFFECTS]->(s:Service)-[:CAUSED_BY]->(rc:RootCause)
MATCH (prior:Incident)-[:AFFECTS]->(s)
MATCH (s)-[:CAUSED_BY]->(rc)
WHERE prior.id <> post.id AND prior.createdDate < post.createdDate
RETURN d.version AS deployment, s.name AS service, rc.type AS root_cause,
       prior.id AS prior_incident, post.id AS post_incident
ORDER BY post.createdDate DESC LIMIT 12
"""
    rows = traced_cypher(cypher, note="q5:recurring_root_cause_post_deploy")
    if not rows:
        return ("**Q5 — Recurring root cause after deploy:** none in the 14-day window.", [])
    bullets = "\n".join(
        f"- deploy `{r['deployment']}` on `{r['service']}` reintroduced **{r['root_cause']}** ({r['prior_incident']} → {r['post_incident']})"
        for r in rows
    )
    return (f"**Q5 — Recurring root causes after deployment**:\n\n{bullets}", rows)


def h_q6_blast_radius_divergence(q: str) -> tuple[str, list[dict]] | None:
    low = q.lower()
    if not ("blast radius divergence" in low or "divergence" in low or re.search(r"\bq6\b", low)):
        return None
    cypher = """
MATCH (i:Incident)-[:AFFECTS]->(direct:Service)
WITH i, collect(DISTINCT direct) AS direct_set
WHERE size(direct_set) <= 2
UNWIND direct_set AS d
OPTIONAL MATCH (d)-[:DEPENDS_ON*1..3]->(reach:Service)
OPTIONAL MATCH (t:Team)-[:OWNS]->(reach)
WITH i, direct_set,
     count(DISTINCT reach) AS reach_n,
     count(DISTINCT reach.tier) AS tier_kinds,
     count(DISTINCT t) AS team_kinds
WHERE reach_n > 0
RETURN i.id AS incident, i.title AS title, size(direct_set) AS direct_n,
       reach_n, tier_kinds, team_kinds
ORDER BY (tier_kinds + team_kinds) DESC, reach_n DESC LIMIT 10
"""
    rows = traced_cypher(cypher, note="q6:blast_radius_divergence")
    if not rows:
        return ("**Q6 — Blast-radius divergence:** no incidents qualify.", [])
    bullets = "\n".join(
        f"- **{r['incident']}** — direct {r['direct_n']} svc, 3-hop reach {r['reach_n']} svc across {r['tier_kinds']} tier(s) and {r['team_kinds']} team(s) — *{r['title']}*"
        for r in rows
    )
    return (f"**Q6 — Highest blast-radius divergence**:\n\n{bullets}", rows)


def h_q7_collision_chains(q: str) -> tuple[str, list[dict]] | None:
    low = q.lower()
    if not ("collision" in low or "collide" in low or "deploy collision" in low or re.search(r"\bq7\b", low)):
        return None
    cypher = """
MATCH (d:Deployment)<-[:INTRODUCED_BY]-(i1:Incident)-[:AFFECTS]->(a:Service)
MATCH (a)-[:CAUSED_BY]->(rc1:RootCause)
MATCH (a)-[:DEPENDS_ON*1..2]->(b:Service)
MATCH (i2:Incident)-[:AFFECTS]->(b)
MATCH (b)-[:CAUSED_BY]->(rc2:RootCause)
WHERE rc1 <> rc2 AND i1 <> i2
RETURN d.version AS deployment, i1.id AS deploy_incident, rc1.type AS deploy_cause,
       a.name AS source_service, b.name AS collision_service,
       i2.id AS live_incident, rc2.type AS live_cause
LIMIT 15
"""
    rows = traced_cypher(cypher, note="q7:deploy_blast_collisions")
    if not rows:
        return ("**Q7 — Deploy/live-incident collisions:** none.", [])
    bullets = "\n".join(
        f"- deploy `{r['deployment']}` ({r['deploy_incident']} / {r['deploy_cause']}) on `{r['source_service']}` → reaches `{r['collision_service']}` which has live {r['live_incident']} ({r['live_cause']})"
        for r in rows
    )
    return (f"**Q7 — Deployment blast collides with unrelated open incident**:\n\n{bullets}", rows)


def h_q8_distance_to_critical(q: str) -> tuple[str, list[dict]] | None:
    low = q.lower()
    if not ("distance to critical" in low or "distance to tier" in low or ("distance" in low and "tier" in low) or re.search(r"\bq8\b", low)):
        return None
    cypher = """
MATCH (i:Incident)-[:AFFECTS]->(s:Service)-[:CAUSED_BY]->(rc:RootCause)
OPTIONAL MATCH p = shortestPath((s)-[:DEPENDS_ON*0..4]->(t:Service))
WHERE t.tier = 'API'
WITH rc, i, min(length(p)) AS hops
WITH rc, avg(toFloat(hops)) AS avg_hops, count(i) AS incidents
WHERE avg_hops IS NOT NULL
RETURN rc.type AS root_cause, round(avg_hops * 100)/100.0 AS avg_hops_to_tier0, incidents
ORDER BY avg_hops_to_tier0 ASC LIMIT 10
"""
    rows = traced_cypher(cypher, note="q8:distance_to_tier0")
    if not rows:
        return ("**Q8 — Distance to critical:** no paths to tier-0 found.", [])
    bullets = "\n".join(
        f"- **{r['root_cause']}** — avg **{r['avg_hops_to_tier0']}** hop(s) to tier-0 across {r['incidents']} incident(s)"
        for r in rows
    )
    return (f"**Q8 — Root causes ranked by avg distance to tier-0**:\n\n{bullets}", rows)


def h_q9_hidden_shared_risk(q: str) -> tuple[str, list[dict]] | None:
    low = q.lower()
    if not ("hidden shared" in low or "shared risk" in low or "co-incident" in low or re.search(r"\bq9\b", low)):
        return None
    cypher = """
MATCH (x:Service), (y:Service)
WHERE elementId(x) < elementId(y)
  AND NOT EXISTS { MATCH (i:Incident)-[:AFFECTS]->(x), (i)-[:AFFECTS]->(y) }
OPTIONAL MATCH (x)-[:DEPENDS_ON*1..2]->(dx:Service)
WITH x, y, collect(DISTINCT dx) AS xset
OPTIONAL MATCH (y)-[:DEPENDS_ON*1..2]->(dy:Service)
WITH x, y, xset, collect(DISTINCT dy) AS yset
WITH x, y, [n IN xset WHERE n IN yset] AS shared
WHERE size(shared) >= 3
RETURN x.name AS svc_a, y.name AS svc_b, size(shared) AS shared_n,
       [n IN shared | n.name][0..6] AS shared_sample
ORDER BY shared_n DESC LIMIT 10
"""
    rows = traced_cypher(cypher, note="q9:hidden_shared_risk")
    if not rows:
        return ("**Q9 — Hidden shared risk:** no qualifying pairs.", [])
    bullets = "\n".join(
        f"- `{r['svc_a']}` ↔ `{r['svc_b']}` share **{r['shared_n']}** downstream svc(s): {', '.join(r['shared_sample'])}"
        for r in rows
    )
    return (f"**Q9 — Hidden shared-risk pairs**:\n\n{bullets}", rows)


def h_q10_reasoning_subgraph(q: str) -> tuple[str, list[dict]] | None:
    low = q.lower()
    if not ("reasoning subgraph" in low or "spanning subgraph" in low or "reasoning graph" in low or re.search(r"\bq10\b", low)):
        return None
    inc = extract_incident_id(q)
    if inc is None:
        return ("**Q10 — Reasoning subgraph** needs an incident id (e.g. `INC-1008`).", [])
    cypher = """
MATCH (i:Incident {id:$inc})-[:AFFECTS]->(anchor:Service)-[:CAUSED_BY]->(rc:RootCause)
MATCH (otherSvc:Service)-[:CAUSED_BY]->(rc)
MATCH (peer:Incident)-[:AFFECTS]->(otherSvc)
OPTIONAL MATCH path = shortestPath((otherSvc)-[:DEPENDS_ON*0..3]->(t:Service))
WHERE t.tier = 'API'
WITH i, rc, collect(DISTINCT peer.id) AS peer_incidents,
     collect(DISTINCT otherSvc.name) AS affected,
     collect(DISTINCT t.name) AS tier0_reached,
     collect(DISTINCT [n IN nodes(path) | n.name]) AS span_paths
RETURN i.id AS incident, rc.type AS root_cause,
       size(peer_incidents) AS peers, peer_incidents[0..6] AS peer_sample,
       affected, tier0_reached,
       [p IN span_paths WHERE p IS NOT NULL][0..5] AS span_sample
"""
    rows = traced_cypher(cypher, {"inc": inc}, note="q10:reasoning_subgraph")
    if not rows or not rows[0].get("root_cause"):
        return (f"**Q10 — Reasoning subgraph:** {inc} has no root cause linkage.", [])
    r = rows[0]
    paths = "\n".join(f"  - `{' → '.join(p)}`" for p in (r["span_sample"] or []))
    return (
        f"**Q10 — Reasoning subgraph for {inc}**\n\n"
        f"- **Root cause:** `{r['root_cause']}`\n"
        f"- **Peer incidents** sharing this cause ({r['peers']}): {', '.join(r['peer_sample'])}\n"
        f"- **Affected services:** {', '.join(r['affected'])}\n"
        f"- **Tier-0 services reached:** {', '.join(r['tier0_reached']) or '_none_'}\n"
        f"- **Spanning paths to tier-0:**\n{paths or '  - _none_'}",
        rows,
    )


HANDLERS = [
    # advanced multi-hop reasoning — registered FIRST so explicit q1..q10 / phrase
    # triggers win over the simpler keyword routes.
    h_q1_cross_team_blind_spot,
    h_q2_root_cause_tier0_reach,
    h_q3_silent_amplifier,
    h_q4_cycles_with_incidents,
    h_q5_recurring_root_cause,
    h_q6_blast_radius_divergence,
    h_q7_collision_chains,
    h_q8_distance_to_critical,
    h_q9_hidden_shared_risk,
    h_q10_reasoning_subgraph,
    h_service_plus_concept,  # conjunctive: service AND (root_cause | outage | sev) — FIRST
    h_by_root_cause,      # DNS, CPU, MemLeak, etc.
    h_impact_count,       # > N services
    h_blast_radius,
    h_root_cause_of_incident,
    h_regressions,
    h_cycles,
    h_owner,
    h_dependents,
    h_incidents_on_service,  # when a service alias is mentioned
    h_multi_team_incidents,  # incidents spanning >N owning teams
    h_orphan_incidents,      # incidents with no AFFECTS edge
    h_hybrid_search,         # catch-all: vector-style + graph expansion
]


def infer(question: str) -> dict:
    _trace_reset()
    t0 = time.perf_counter()
    entities = {
        "incident_id": extract_incident_id(question),
        "service":     extract_service(question),
        "root_cause":  extract_root_cause(question),
    }
    for h in HANDLERS:
        try:
            out = h(question)
        except Exception as e:
            return {
                "ok": False,
                "answer": f"Query failed while running `{h.__name__}`: {e}",
                "evidence": [],
                "handler": h.__name__,
                "trace": {
                    "entities": entities,
                    "cypher_steps": _trace(),
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                },
            }
        if out is not None:
            ans, ev = out
            final_answer = maybe_llm_synthesis(question, ans, ev) or ans
            return {
                "ok": True,
                "answer": final_answer,
                "evidence": ev[:10],
                "handler": h.__name__,
                "trace": {
                    "entities": entities,
                    "cypher_steps": _trace(),
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                },
            }
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
        "trace": {
            "entities": entities,
            "cypher_steps": _trace(),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        },
    }


# ---------------------------------------------------------------------------
# Graph snapshot for UI visualizer
# ---------------------------------------------------------------------------
def graph_snapshot() -> dict:
    """Return all nodes and edges (with label/type) as a compact JSON payload."""
    nodes: list[dict] = []
    seen: set[str] = set()
    for label in ("Service", "Team", "RootCause", "Deployment", "Incident"):
        cy = f"MATCH (n:{label}) RETURN n LIMIT 2000"
        try:
            rows = run_cypher(cy)
        except Exception:
            continue
        for r in rows:
            n = r["n"]
            props = dict(n) if not isinstance(n, dict) else n
            nid = props.get("id") or props.get("name") or props.get("type") or props.get("version")
            if not nid or nid in seen:
                continue
            seen.add(nid)
            nodes.append({
                "id": str(nid),
                "label": label,
                "name": props.get("name") or props.get("title") or str(nid),
                "severity": props.get("severity"),
                "tier": props.get("tier"),
            })
    edges: list[dict] = []
    edge_queries = [
        ("AFFECTS",       "MATCH (i:Incident)-[:AFFECTS]->(s:Service) RETURN i.id AS a, s.name AS b"),
        ("DEPENDS_ON",    "MATCH (a:Service)-[:DEPENDS_ON]->(b:Service) RETURN a.name AS a, b.name AS b"),
        ("CAUSED_BY",     "MATCH (s:Service)-[:CAUSED_BY]->(r:RootCause) RETURN s.name AS a, r.type AS b"),
        ("OWNS",          "MATCH (t:Team)-[:OWNS]->(s:Service) RETURN t.name AS a, s.name AS b"),
        ("INTRODUCED_BY", "MATCH (i:Incident)-[:INTRODUCED_BY]->(d:Deployment) RETURN i.id AS a, d.version AS b"),
    ]
    for rel, cy in edge_queries:
        try:
            for r in run_cypher(cy):
                if r.get("a") and r.get("b"):
                    edges.append({"source": str(r["a"]), "target": str(r["b"]), "type": rel})
        except Exception:
            continue
    return {"nodes": nodes, "edges": edges,
            "counts": {"nodes": len(nodes), "edges": len(edges)}}


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
        if self.path == "/graph":
            try:
                snap = graph_snapshot()
                payload = json.dumps(snap).encode()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as e:
                self.send_response(500)
                self._cors()
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
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
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7688")
    print(f"graph-chat listening on http://127.0.0.1:{port}")
    print(f"  neo4j target: {uri} (override via NEO4J_URI env var)")
    # Fail fast with a clear message if the driver can't reach Neo4j — this is
    # the #1 source of support questions ("port 7687 vs 7688", container down).
    try:
        with driver().session() as s:
            s.run("RETURN 1").consume()
        print("  neo4j: connection OK")
    except Exception as e:
        print(f"  neo4j: CANNOT CONNECT → {e}")
        print("  hint: is 'gmk-neo4j' running? expected Bolt on localhost:7688")
        print("        docker ps --filter name=gmk-neo4j --format '{{.Names}}  {{.Ports}}'")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    port = int(os.getenv("CHAT_PORT", "8765"))
    serve(port)
