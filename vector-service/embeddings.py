"""Embedding pipeline + pgvector persistence (§3 production stats)."""
from __future__ import annotations

import os
from typing import Iterable

import psycopg
from openai import AzureOpenAI

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1536"))


def _client() -> AzureOpenAI:
    return AzureOpenAI(
        api_key=os.environ["AZURE_OPENAI_KEY"],
        api_version="2024-06-01",
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    )


def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    resp = _client().embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


# --- pgvector search -------------------------------------------------
def _dsn() -> str:
    return os.getenv("EPISODIC_DB_DSN",
                     "postgresql://memuser:mempass@localhost:5432/memdb")


def ann_search(query: str, k: int = 10) -> list[tuple[str, float]]:
    q = embed_one(query)
    sql = """
    SELECT episode_id, 1 - (embedding <=> %s::vector) AS score
    FROM episodes
    ORDER BY embedding <=> %s::vector
    LIMIT %s;
    """
    with psycopg.connect(_dsn()) as c, c.cursor() as cur:
        cur.execute(sql, (q, q, k))
        return [(r[0], float(r[1])) for r in cur.fetchall()]
