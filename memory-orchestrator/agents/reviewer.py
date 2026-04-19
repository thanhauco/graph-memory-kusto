"""Reviewer — validates an episode against MITRE ATLAS guardrails (§8).

Implemented controls:
  AML.T0020 — episodic memory poisoning (confidence gate)
  AML.T0031 — RAG poisoning via malicious graph nodes (allow-list check)
  AML.T0040 — resource exhaustion (hop-depth cap)
  AML.T0048 — jailbreak framings
  AML.T0051 — prompt injection in incident text
  AML.T0054 — indirect injection via node properties
  AML.T0057 — exfiltration patterns
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CONF_GATE = 0.75
MAX_HOPS = 6

# §5 18-service topology
_ALLOWED_SERVICES = {
    "ApiGateway", "AuthService", "BillingAPI", "NotifyAPI",
    "ServiceA", "ServiceB", "ServiceC", "PaymentProc", "MsgBroker", "SearchSvc",
    "DbService", "CacheLayer", "BlobStore", "IndexStore", "QueueStore",
    "VectorDB", "GraphDB",
}
_ALLOWED_CAUSES = {
    "HighCPU", "ConnPoolExhaustion", "OvernightBatch", "MemLeak",
    "ColdStart", "NetworkPartition", "DiskPressure", "DNSFailure", "ConnPool",
}
_ALLOWED_TOKENS = _ALLOWED_SERVICES | _ALLOWED_CAUSES | {
    "AFFECTS", "DEPENDS_ON", "CAUSED_BY", "OWNS", "TRIGGERS", "INTRODUCED_BY",
    "ROUTES_TO", "CONTAINS",
}
# narrative connective words that show up in hop_path strings — ignore
_IGNORE_WORDS = {
    "multiple", "services", "during", "overnight", "batch",
    "connection", "pool", "size", "saturation",
}

_INJECTION_PATTERNS = [
    (r"(?i)ignore (all )?(previous|prior) (instructions|prompts)", "AML.T0051"),
    (r"(?i)(reveal|dump|show).*(system|hidden) prompt",            "AML.T0051"),
    (r"(?i)\bsystem prompt\b",                                     "AML.T0051"),
    (r"(?i)disregard (all|any|previous)",                          "AML.T0054"),
    (r"(?i)exfiltrat",                                             "AML.T0057"),
    (r"(?i)jailbreak|DAN mode|developer mode",                     "AML.T0048"),
]


@dataclass
class ReviewVerdict:
    ok: bool
    reasons: list[str]


def _extract_tokens(hop_path: str) -> list[str]:
    cleaned = re.sub(r"→|\[[^\]]+\]|:|->", " ", hop_path)
    tokens = []
    for t in cleaned.split():
        t = t.strip(".,;")
        if not t:
            continue
        if re.match(r"^INC-\d+$", t):         # incident ids
            continue
        if re.match(r"^v\d[\d.]*$", t):       # versions (v2.3.1)
            continue
        if t.lower() in _IGNORE_WORDS:
            continue
        tokens.append(t)
    return tokens


def run(ep) -> ReviewVerdict:
    reasons: list[str] = []

    # AML.T0020 — confidence gate
    if ep.confidence < CONF_GATE:
        reasons.append(f"AML.T0020 low confidence {ep.confidence:.2f} < {CONF_GATE}")

    # AML.T0040 — hop depth cap
    if ep.hops > MAX_HOPS:
        reasons.append(f"AML.T0040 hop depth {ep.hops} > {MAX_HOPS}")

    # AML.T0031 — RAG poisoning: hop_path nodes must be on allow-list
    unknown = [t for t in _extract_tokens(ep.hop_path) if t not in _ALLOWED_TOKENS]
    if unknown:
        reasons.append(f"AML.T0031 unknown graph nodes in hop_path: {unknown[:5]}")

    # T0051 / T0054 / T0048 / T0057
    blob = f"{ep.query} {ep.hop_path} {ep.outcome}"
    for pat, atlas_id in _INJECTION_PATTERNS:
        if re.search(pat, blob):
            reasons.append(f"{atlas_id} suspicious pattern matched")
            break

    return ReviewVerdict(ok=not reasons, reasons=reasons)
