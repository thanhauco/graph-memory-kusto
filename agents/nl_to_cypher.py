"""Natural-language → Cypher translator (CONTEXT.md §7).

A small deterministic matcher using the 5 template queries as training cases.
For a production system swap `translate()` with an LLM call; the templates
below stay as few-shot examples + golden tests.

Public API:
    translate(nl) -> TranslationResult
    TEMPLATES: list[Template]  (the 5 §7 queries, act as test cases)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Template:
    name: str
    example_nl: str
    cypher: str
    pattern: re.Pattern                    # matcher on the NL question
    fill: Callable[[re.Match], dict]       # extract params


@dataclass
class TranslationResult:
    matched: str
    cypher: str
    params: dict


# ---------------------------------------------------------------------------
# Templates — keep in sync with CONTEXT.md §7 and the UI panel (index.html).
# ---------------------------------------------------------------------------

_T_INCIDENTS_ON_SERVICE = Template(
    name="incidents_on_service_recent",
    example_nl="show all incidents that affected ServiceA in the last week",
    cypher=(
        "MATCH (i:Incident)-[:AFFECTS]->(s:Service {name:$service})\n"
        "WHERE i.createdDate > datetime() - duration({days:$days})\n"
        "RETURN i.id, i.title, i.severity\n"
        "ORDER BY i.createdDate DESC"
    ),
    pattern=re.compile(
        r"incidents?.*(?:affected|on)\s+([A-Za-z]+).*(?:last\s+(\d+)\s*(day|days|week|weeks))?",
        re.IGNORECASE),
    fill=lambda m: {
        "service": m.group(1),
        "days": (int(m.group(2)) * (7 if m.group(3) and m.group(3).startswith("week") else 1))
                 if m.group(2) else 7,
    },
)

_T_DEPENDENTS = Template(
    name="services_depending_on",
    example_nl="what services depend on DbService?",
    cypher=(
        "MATCH (s:Service)-[:DEPENDS_ON]->(:Service {name:$target})\n"
        "RETURN s.name, s.tier"
    ),
    pattern=re.compile(r"services?\s+depend\s+on\s+([A-Za-z]+)", re.IGNORECASE),
    fill=lambda m: {"target": m.group(1)},
)

_T_ROOT_CAUSE = Template(
    name="root_cause_of_incident",
    example_nl="find root cause of INC-456",
    cypher=(
        "MATCH (i:Incident {id:$id})-[:AFFECTS]->(s)-[:DEPENDS_ON]->(d)-[:CAUSED_BY]->(r:RootCause)\n"
        "RETURN r.type, r.description"
    ),
    pattern=re.compile(r"root\s+cause.*\b(INC-\d+)\b", re.IGNORECASE),
    fill=lambda m: {"id": m.group(1).upper()},
)

_T_TEAM_OWNER = Template(
    name="team_owner",
    example_nl="which team owns ServiceA?",
    cypher=(
        "MATCH (t:Team)-[:OWNS]->(:Service {name:$service})\n"
        "RETURN t.name, t.slackChannel, t.oncallRotation"
    ),
    pattern=re.compile(r"(?:which|what)\s+team\s+owns?\s+([A-Za-z]+)", re.IGNORECASE),
    fill=lambda m: {"service": m.group(1)},
)

_T_BLAST_RADIUS = Template(
    name="blast_radius_hops",
    example_nl="blast radius of ServiceA up to 3 hops",
    cypher=(
        "MATCH p=(:Service {name:$service})-[:DEPENDS_ON*1..$hops]->(s)\n"
        "RETURN DISTINCT s.name, length(p) AS hops ORDER BY hops"
    ),
    pattern=re.compile(
        r"blast\s+radius\s+of\s+([A-Za-z]+)(?:.*up\s+to\s+(\d+)\s*hops?)?", re.IGNORECASE),
    fill=lambda m: {"service": m.group(1), "hops": int(m.group(2) or 3)},
)


TEMPLATES: list[Template] = [
    _T_INCIDENTS_ON_SERVICE, _T_DEPENDENTS, _T_ROOT_CAUSE,
    _T_TEAM_OWNER, _T_BLAST_RADIUS,
]


def translate(nl: str) -> Optional[TranslationResult]:
    """Return the first template whose pattern matches `nl`."""
    # Order matters: more specific templates first
    order = [_T_ROOT_CAUSE, _T_BLAST_RADIUS, _T_DEPENDENTS,
             _T_TEAM_OWNER, _T_INCIDENTS_ON_SERVICE]
    for t in order:
        m = t.pattern.search(nl)
        if m:
            try:
                params = t.fill(m)
            except Exception:
                continue
            cy = t.cypher
            # Neo4j variable-length upper bound must be literal
            if "$hops" in cy and "hops" in params:
                cy = cy.replace("$hops", str(params["hops"]))
            if "$days" in cy and "days" in params:
                cy = cy.replace("$days", str(params["days"]))
            return TranslationResult(matched=t.name, cypher=cy, params=params)
    return None


# ---------------------------------------------------------------------------
# Self-check — runs the 5 §7 example queries through the translator.
# Invoke with: python agents/nl_to_cypher.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    failed = 0
    for t in TEMPLATES:
        r = translate(t.example_nl)
        ok = r is not None and r.matched == t.name
        print(f"[{'PASS' if ok else 'FAIL'}] {t.name:35s}  '{t.example_nl}'")
        if not ok:
            failed += 1
        else:
            print(f"         → {r.cypher.splitlines()[0]} …  params={r.params}")
    print()
    if failed:
        raise SystemExit(f"{failed}/{len(TEMPLATES)} templates failed")
    print(f"{len(TEMPLATES)}/{len(TEMPLATES)} templates matched ✓")
