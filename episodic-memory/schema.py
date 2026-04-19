"""Pydantic schema for an episodic memory entry.

Mirrors CONTEXT.md §4 — IcM Incident Chains.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, confloat, conint


EpisodeTag = Literal["RCA", "RETRIEVAL", "REGRESSION"]


class Episode(BaseModel):
    episode_id: str = Field(default_factory=lambda: f"ep-{uuid4().hex[:8]}")
    incident: str                                  # e.g. "INC-456"
    query: str
    hop_path: str                                  # human-readable traversal
    hops: conint(ge=1, le=8)
    outcome: str
    confidence: confloat(ge=0.0, le=1.0)
    tag: EpisodeTag = "RCA"
    embedding: Optional[list[float]] = None        # set by writer
    created_at: datetime = Field(default_factory=datetime.utcnow)
    team: Optional[str] = None                     # for RBAC / AML.T0057

    # --- confidence gate per §8 (min 0.75) ------------------------------
    @property
    def should_store(self) -> bool:
        return self.confidence >= 0.75
