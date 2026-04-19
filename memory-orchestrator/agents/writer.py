"""Writer agent — encodes, embeds, indexes an Episode (§2 Memory Writer)."""
from __future__ import annotations

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from episodic_memory import Episode, upsert  # type: ignore
from vector_service import embed_one          # type: ignore


def run(incident: str, query: str, hop_path: str, hops: int,
        outcome: str, confidence: float, tag: str = "RCA",
        team: str | None = None) -> Episode:
    ep = Episode(
        incident=incident, query=query, hop_path=hop_path, hops=hops,
        outcome=outcome, confidence=confidence, tag=tag, team=team,
    )
    # Embed on the combined context
    ep.embedding = embed_one(f"{query}\n{hop_path}\n{outcome}")
    upsert(ep)
    return ep
