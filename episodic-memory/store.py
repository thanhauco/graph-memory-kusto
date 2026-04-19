"""PostgreSQL-backed episodic memory store.

Writes an Episode to a `episodes` table and mirrors the embedding into
pgvector (see vector-service/). Idempotent upsert on episode_id.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Iterable

import psycopg
from psycopg.rows import dict_row

from .schema import Episode

DDL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS episodes (
    episode_id  TEXT PRIMARY KEY,
    incident    TEXT NOT NULL,
    query       TEXT NOT NULL,
    hop_path    TEXT NOT NULL,
    hops        INT  NOT NULL,
    outcome     TEXT NOT NULL,
    confidence  REAL NOT NULL,
    tag         TEXT NOT NULL,
    team        TEXT,
    embedding   vector(1536),
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_episodes_incident ON episodes(incident);
CREATE INDEX IF NOT EXISTS idx_episodes_tag      ON episodes(tag);
-- HNSW index (§3 production stats)
CREATE INDEX IF NOT EXISTS idx_episodes_vec
    ON episodes USING hnsw (embedding vector_cosine_ops);
"""


def _dsn() -> str:
    return os.getenv("EPISODIC_DB_DSN",
                     "postgresql://memuser:mempass@localhost:5432/memdb")


@contextmanager
def connect():
    with psycopg.connect(_dsn(), row_factory=dict_row) as conn:
        yield conn


def init_schema() -> None:
    with connect() as c, c.cursor() as cur:
        cur.execute(DDL)
        c.commit()


def upsert(ep: Episode) -> None:
    """Store an episode — rejects low-confidence entries (§8 AML.T0020)."""
    if not ep.should_store:
        raise ValueError(
            f"episode {ep.episode_id} below confidence gate (0.75): {ep.confidence}"
        )
    sql = """
    INSERT INTO episodes (episode_id, incident, query, hop_path, hops,
                          outcome, confidence, tag, team, embedding)
    VALUES (%(episode_id)s, %(incident)s, %(query)s, %(hop_path)s, %(hops)s,
            %(outcome)s, %(confidence)s, %(tag)s, %(team)s, %(embedding)s)
    ON CONFLICT (episode_id) DO UPDATE SET
        outcome    = EXCLUDED.outcome,
        confidence = EXCLUDED.confidence,
        embedding  = EXCLUDED.embedding;
    """
    payload = ep.model_dump()
    payload["embedding"] = ep.embedding  # pgvector accepts list[float]
    with connect() as c, c.cursor() as cur:
        cur.execute(sql, payload)
        c.commit()


def prune(threshold: float = 0.75) -> int:
    """Forget phase (§3 lifecycle) — drop below-threshold episodes."""
    with connect() as c, c.cursor() as cur:
        cur.execute("DELETE FROM episodes WHERE confidence < %s", (threshold,))
        c.commit()
        return cur.rowcount


def bulk_upsert(items: Iterable[Episode]) -> int:
    n = 0
    for e in items:
        try:
            upsert(e); n += 1
        except ValueError:
            continue
    return n
