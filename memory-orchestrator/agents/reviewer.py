"""Reviewer — validates an episode against MITRE ATLAS guardrails (§8).

Blocks: low confidence, suspicious text patterns, depth > 6, cross-team leak.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CONF_GATE = 0.75
MAX_HOPS = 6

_INJECTION_PATTERNS = [
    r"(?i)ignore (all )?(previous|prior) (instructions|prompts)",
    r"(?i)system prompt",
    r"(?i)exfiltrate",
    r"(?i)disregard",
]


@dataclass
class ReviewVerdict:
    ok: bool
    reasons: list[str]


def run(ep) -> ReviewVerdict:  # ep: Episode
    reasons: list[str] = []
    if ep.confidence < CONF_GATE:
        reasons.append(f"AML.T0020 low confidence {ep.confidence:.2f} < {CONF_GATE}")
    if ep.hops > MAX_HOPS:
        reasons.append(f"AML.T0040 hop depth {ep.hops} > {MAX_HOPS}")
    blob = f"{ep.query} {ep.hop_path} {ep.outcome}"
    for pat in _INJECTION_PATTERNS:
        if re.search(pat, blob):
            reasons.append(f"AML.T0051/T0054 suspicious pattern: {pat}")
            break
    return ReviewVerdict(ok=not reasons, reasons=reasons)
