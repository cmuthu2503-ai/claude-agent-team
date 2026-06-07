"""Knowledge Base row models (KB-03).

Lightweight dataclasses for the rows ``KnowledgeStore`` reads/writes. Kept
plain (not Pydantic) — these are internal data carriers, validated at the
API boundary (KB-10), not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class KbDocument:
    doc_id: str
    namespace: str
    source_type: str
    title: str
    content_hash: str
    uri: str | None = None
    project_id: str | None = None
    sensitivity: str = "normal"
    status: str = "pending"
    superseded_by: str | None = None
    version: int = 1
    curated_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime | None = None
    ttl_days: int | None = None


@dataclass
class KbChunk:
    chunk_id: str
    doc_id: str
    namespace: str
    ordinal: int
    text: str
    embedding: list[float] | None = None
    token_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    bucket_ids: list[str] = field(default_factory=list)


@dataclass
class AgentMemory:
    """One episodic-memory row (KB-24). Owned + unvetted + decaying — the
    counterpart to the vetted, citeable ``KbChunk``. Carries an embedding so
    recall (KB-25) rides the same vector path."""

    memory_id: str
    namespace: str
    agent_id: str
    kind: str  # episode | summary | discussion
    text: str
    request_id: str | None = None
    project_id: str | None = None
    outcome: str | None = None  # success | failed | partial
    embedding: list[float] | None = None
    content_hash: str | None = None
    unvetted: bool = True
    superseded_by: str | None = None
    created_at: datetime | None = None
    ttl_days: int | None = None
    use_count: int = 0
    last_used_at: datetime | None = None


@dataclass
class KbBucket:
    bucket_id: str
    name: str
    slug: str
    description: str = ""
    project_id: str | None = None
    is_system: bool = False
    created_by: str = "system"
    created_at: datetime | None = None
    # Populated by list/get queries that join membership counts.
    doc_count: int = 0
    chunk_count: int = 0
