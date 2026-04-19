"""Hybrid retrieval: vector ANN + graph-structural + tag-filtered episodic.

Combines the three memory tiers into a single ranked result set.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .schema import Episode
from .store import connect


@dataclass
class RetrievalResult:
    episode: Episode
    vector_score: float
    graph_score: float
    score: float  # combined


def vector_top_k(embedding: list[float], k: int = 10,
                 team: Optional[str] = None) -> list[RetrievalResult]:
    """Cosine ANN top-k via pgvector (HNSW)."""
    where, params = "", {"q": embedding, "k": k}
    if team:
        where = "WHERE team = %(team)s"
        params["team"] = team
    sql = f"""
    SELECT *, (embedding <=> %(q)s::vector) AS dist
    FROM episodes
    {where}
    ORDER BY embedding <=> %(q)s::vector
    LIMIT %(k)s;
    """
    with connect() as c, c.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    out: list[RetrievalResult] = []
    for r in rows:
        ep = Episode.model_validate({k_: r[k_] for k_ in r if k_ != "dist"})
        score = 1.0 - float(r["dist"])
        out.append(RetrievalResult(ep, vector_score=score, graph_score=0.0, score=score))
    return out


def hybrid(embedding: list[float], graph_hits: dict[str, float],
           k: int = 10, alpha: float = 0.6) -> list[RetrievalResult]:
    """α·vector + (1-α)·graph. `graph_hits` is {episode_id: graph_score}."""
    vec = vector_top_k(embedding, k=k * 2)
    for r in vec:
        g = graph_hits.get(r.episode.episode_id, 0.0)
        r.graph_score = g
        r.score = alpha * r.vector_score + (1 - alpha) * g
    vec.sort(key=lambda x: x.score, reverse=True)
    return vec[:k]
