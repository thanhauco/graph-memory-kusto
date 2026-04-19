"""Deterministic mock IcM dataset — 400 incidents covering all 6 entities
from the schema (Incident · Service · RootCause · Team · Alert · Deployment).

Usage
-----
    from kusto_ingestion.mock_data import generate
    data = generate(seed=42)
    # data["incidents"]   400 rows
    # data["services"]    18 rows
    # data["root_causes"] 8 rows
    # data["teams"]       6 rows
    # data["alerts"]      ~520 rows (≈1.3 alerts per incident)
    # data["deployments"] ~60 rows
    # data["edges"]       typed relationships ready for MERGE

All relationships match the ER diagram in the app:
    Incident -[AFFECTS]-> Service
    Service  -[DEPENDS_ON]-> Service (DAG within each tier + cross-tier)
    Service  -[CAUSED_BY]-> RootCause
    Team     -[OWNS]-> Service
    Alert    -[TRIGGERS]-> Incident
    Incident -[INTRODUCED_BY]-> Deployment
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any


# ---------------------------------------------------------------------------
# Static taxonomy — matches CONTEXT.md §5 topology
# ---------------------------------------------------------------------------
SERVICES = {
    "API":      ["ApiGateway", "AuthService", "BillingAPI", "NotifyAPI"],
    "Platform": ["ServiceA", "ServiceB", "ServiceC",
                 "PaymentProc", "MsgBroker", "SearchSvc"],
    "Data":     ["DbService", "CacheLayer", "BlobStore",
                 "IndexStore", "QueueStore", "VectorDB", "GraphDB"],
}
ALL_SERVICES = [(tier, n) for tier, names in SERVICES.items() for n in names]

ROOT_CAUSES = [
    ("HighCPU",            "CPU saturation on service pod",        0.22, True),
    ("ConnPoolExhaustion", "DB connection pool exhausted",          0.18, True),
    ("MemLeak",            "Heap/RSS growing unboundedly",          0.10, False),
    ("DiskPressure",       "Node disk > 85% utilization",           0.08, True),
    ("DNSFailure",         "Upstream DNS resolver failure",         0.06, False),
    ("NetworkPartition",   "Cross-AZ partition or throttling",      0.07, False),
    ("ColdStart",          "Function/pod cold start saturation",    0.12, True),
    ("OvernightBatch",     "Overnight batch job starving OLTP",     0.17, False),
]

TEAMS = [
    ("Platform-Core",  "weekly",  "#plat-core",   "severity2-escalate"),
    ("Data-Infra",     "biweekly","#data-infra",  "severity2-escalate"),
    ("Billing-Eng",    "weekly",  "#billing-eng", "severity1-escalate"),
    ("Auth-Sec",       "weekly",  "#auth-sec",    "severity1-escalate"),
    ("Search-Team",    "weekly",  "#search",      "severity3-escalate"),
    ("Messaging-Plat", "biweekly","#msg-plat",    "severity2-escalate"),
]

# Which team owns which services
TEAM_OWNS = {
    "Platform-Core":  ["ServiceA", "ServiceB", "ServiceC", "ApiGateway"],
    "Data-Infra":     ["DbService", "CacheLayer", "BlobStore",
                       "IndexStore", "QueueStore", "VectorDB", "GraphDB"],
    "Billing-Eng":    ["BillingAPI", "PaymentProc"],
    "Auth-Sec":       ["AuthService"],
    "Search-Team":    ["SearchSvc", "NotifyAPI"],
    "Messaging-Plat": ["MsgBroker"],
}

# Hand-curated dependency graph (no cycles across tiers except intentional one)
DEPENDENCIES = [
    ("ApiGateway", "ServiceA"),    ("ApiGateway", "ServiceC"),
    ("AuthService", "ServiceA"),   ("BillingAPI", "PaymentProc"),
    ("NotifyAPI", "MsgBroker"),
    ("ServiceA", "ServiceB"),      ("ServiceA", "DbService"),
    ("ServiceA", "CacheLayer"),    ("ServiceA", "BlobStore"),
    ("ServiceB", "DbService"),     ("ServiceB", "VectorDB"),
    ("ServiceC", "DbService"),     ("ServiceC", "SearchSvc"),
    ("ServiceC", "GraphDB"),
    ("PaymentProc", "DbService"),
    ("SearchSvc", "IndexStore"),
    ("MsgBroker", "QueueStore"),
    # Intentional cycle (cycle-detection demo): ServiceB → CacheLayer → ServiceB
    ("ServiceB", "CacheLayer"),    ("CacheLayer", "ServiceB"),
]

# Most-likely root cause per service (for realism in generated data)
SERVICE_CAUSE_HINTS = {
    "ServiceA":    ["HighCPU", "ColdStart"],
    "ServiceB":    ["HighCPU", "MemLeak"],
    "ServiceC":    ["ConnPoolExhaustion", "HighCPU"],
    "DbService":   ["ConnPoolExhaustion", "OvernightBatch"],
    "CacheLayer":  ["MemLeak", "HighCPU"],
    "PaymentProc": ["DNSFailure", "NetworkPartition"],
    "MsgBroker":   ["DiskPressure", "OvernightBatch"],
    "SearchSvc":   ["ColdStart", "HighCPU"],
    "IndexStore":  ["DiskPressure"],
    "QueueStore":  ["DiskPressure"],
    "AuthService": ["MemLeak", "ColdStart"],
    "BillingAPI":  ["ConnPoolExhaustion"],
    "NotifyAPI":   ["ColdStart"],
    "ApiGateway":  ["HighCPU", "NetworkPartition"],
    "BlobStore":   ["NetworkPartition"],
    "VectorDB":    ["MemLeak"],
    "GraphDB":     ["HighCPU"],
}

ALERT_SOURCES = ["Kusto", "Prometheus", "AzureMonitor"]

# ---------------------------------------------------------------------------
def _weighted_choice(rng: random.Random, items: list, weights: list[float]):
    return rng.choices(items, weights=weights, k=1)[0]


def generate(seed: int = 42, n_incidents: int = 400) -> dict[str, Any]:
    """Generate a deterministic mock dataset."""
    rng = random.Random(seed)

    now = datetime.utcnow()
    cause_names  = [c[0] for c in ROOT_CAUSES]
    cause_weights = [c[2] for c in ROOT_CAUSES]

    # ---- Services ----
    services = [
        {"name": n, "tier": t, "health": "healthy",
         "sla": 99.95 if t == "API" else 99.9, "poolSize": 50,
         "circuitBreaker": True}
        for t, n in ALL_SERVICES
    ]

    # ---- Root causes ----
    root_causes = [
        {"type": t, "description": d, "frequency": f, "autoRemediation": ar}
        for t, d, f, ar in ROOT_CAUSES
    ]

    # ---- Teams ----
    teams = [
        {"name": n, "oncallRotation": r,
         "slackChannel": s, "escalationPolicy": e}
        for n, r, s, e in TEAMS
    ]

    # ---- Deployments (≈60) ----
    deployments: list[dict] = []
    deploy_svcs = ["ServiceA", "ServiceB", "ServiceC", "AuthService",
                   "BillingAPI", "PaymentProc", "SearchSvc"]
    for i in range(60):
        svc = rng.choice(deploy_svcs)
        major, minor, patch = rng.randint(1, 3), rng.randint(0, 9), rng.randint(0, 9)
        deployments.append({
            "version": f"v{major}.{minor}.{patch}",
            "service": svc,
            "deployedAt": (now - timedelta(days=rng.randint(0, 30),
                                           hours=rng.randint(0, 23))).isoformat() + "Z",
            "deployedBy": rng.choice(["alice", "bob", "carol", "dan", "eve"]),
            "rollbackVersion": f"v{major}.{minor}.{max(0, patch - 1)}",
            "status": rng.choices(["deployed", "rolled_back", "canary"],
                                  weights=[0.8, 0.08, 0.12])[0],
        })

    # ---- Incidents (400) ----
    incidents: list[dict] = []
    edges_affects: list[tuple[str, str]] = []
    edges_caused_by: list[tuple[str, str]] = []       # Service → RootCause (generic)
    edges_introduced_by: list[tuple[str, str]] = []   # Incident → Deployment version
    titles_pool = [
        "5xx spike", "latency regression", "timeout cascade",
        "memory pressure", "elevated error rate", "connection refused",
        "throttling observed", "checkpoint lag", "replication delay",
        "cold-start storm", "cache miss storm", "DNS resolution failure",
    ]

    for idx in range(n_incidents):
        inc_id = f"INC-{1000 + idx:04d}"
        affected = rng.choice(list(SERVICE_CAUSE_HINTS.keys()))
        # Prefer hinted cause 70% of the time; otherwise weighted random
        if rng.random() < 0.7 and SERVICE_CAUSE_HINTS[affected]:
            cause = rng.choice(SERVICE_CAUSE_HINTS[affected])
        else:
            cause = _weighted_choice(rng, cause_names, cause_weights)
        severity = rng.choices([1, 2, 3, 4], weights=[0.08, 0.27, 0.45, 0.20])[0]
        created = now - timedelta(days=rng.randint(0, 6),
                                  hours=rng.randint(0, 23),
                                  minutes=rng.randint(0, 59))
        inc = {
            "id": inc_id,
            "title": f"{affected} {rng.choice(titles_pool)}",
            "severity": severity,
            "status": rng.choices(["resolved", "mitigated", "investigating"],
                                  weights=[0.75, 0.18, 0.07])[0],
            "createdDate": created.isoformat() + "Z",
            "ttm": rng.randint(5, 240),  # time-to-mitigate in minutes
            "affectedService": affected,
            "rootCause": cause,
        }
        incidents.append(inc)
        edges_affects.append((inc_id, affected))
        edges_caused_by.append((affected, cause))

        # 10% of incidents are regressions tied to a deployment of the same service
        if rng.random() < 0.10:
            matching = [d for d in deployments if d["service"] == affected]
            if matching:
                d = rng.choice(matching)
                edges_introduced_by.append((inc_id, d["version"]))

    # ---- Alerts (≈1.3 per incident) ----
    alerts: list[dict] = []
    edges_triggers: list[tuple[str, str]] = []  # Alert → Incident
    rule_pool = ["5xxRate", "P99Latency", "CPUHot", "MemoryHigh",
                 "DiskPressure", "QueueLag", "ErrorBudgetBurn"]
    for inc in incidents:
        for _ in range(rng.randint(1, 2)):
            a_id = f"ALT-{len(alerts) + 1:05d}"
            alerts.append({
                "alertId": a_id,
                "rule": rng.choice(rule_pool),
                "threshold": round(rng.uniform(0.5, 5.0), 2),
                "firedAt": inc["createdDate"],
                "source": rng.choice(ALERT_SOURCES),
            })
            edges_triggers.append((a_id, inc["id"]))

    return {
        "incidents":  incidents,
        "services":   services,
        "root_causes":root_causes,
        "teams":      teams,
        "deployments":deployments,
        "alerts":     alerts,
        "edges": {
            "AFFECTS":         edges_affects,            # (IncidentId, ServiceName)
            "DEPENDS_ON":      DEPENDENCIES,             # static
            "CAUSED_BY":       list({e for e in edges_caused_by}),
            "OWNS":            [(t, s) for t, ss in TEAM_OWNS.items() for s in ss],
            "TRIGGERS":        edges_triggers,           # (AlertId, IncidentId)
            "INTRODUCED_BY":   edges_introduced_by,      # (IncidentId, DeploymentVersion)
        },
    }


if __name__ == "__main__":
    d = generate()
    print(f"incidents:   {len(d['incidents'])}")
    print(f"services:    {len(d['services'])}")
    print(f"root_causes: {len(d['root_causes'])}")
    print(f"teams:       {len(d['teams'])}")
    print(f"deployments: {len(d['deployments'])}")
    print(f"alerts:      {len(d['alerts'])}")
    for rel, lst in d["edges"].items():
        print(f"  {rel:14s} {len(lst)} edges")
