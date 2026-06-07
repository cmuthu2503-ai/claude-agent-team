"""Knowledge Base schema DDL (KB-03).

Idempotent ``CREATE TABLE IF NOT EXISTS`` DDL applied at
``KnowledgeStore.initialize()`` — the same pattern ``SQLiteStateStore`` uses
for its ``SCHEMA_SQL``. Postgres flavour (pgvector, UUID[], JSONB, GIN).

Why not Alembic (yet): the platform has no Alembic harness wired, and a
greenfield Phase-1 schema doesn't need versioned migrations — idempotent
DDL is the proven pattern here. When the first schema *change* lands,
introduce Alembic for that delta; the table definitions below become the
baseline migration.

Tables
------
- ``kb_documents``        — one logical source document + lifecycle/provenance
- ``kb_chunks``           — retrievable units; embedding + denormalized bucket_ids
- ``kb_buckets``          — user-defined grounding collections
- ``kb_document_buckets`` — many-to-many doc ↔ bucket membership
- ``kb_retrieval_audit``  — append-only record of what was searched/returned/cited
- ``decision_ledger``     — append-only provenance of why a conclusion was reached
- ``agent_memory``        — episodic memory: owned, unvetted, decaying experience
- ``kb_promotion_candidates`` — recurring patterns proposed (not auto-promoted)
                            from episodic memory into the KB, pending review
- ``kb_retention_audit``  — append-only log of TTL expiry / relevance pruning /
                            right-to-be-forgotten purges (KB-30)
- ``kb_feedback``         — thumbs up/down on retrieved chunks; feeds a
                            recency-weighted usefulness boost into rerank (KB-31)
"""

from __future__ import annotations

# ``gen_random_uuid()`` is built into Postgres 13+ core (no pgcrypto needed).
# ``vector`` extension is ensured by the pool opener (src/knowledge/pg.py).
KNOWLEDGE_SCHEMA_SQL = """
-- ── Documents ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS kb_documents (
    doc_id        TEXT PRIMARY KEY,
    namespace     TEXT NOT NULL,
    project_id    TEXT,
    source_type   TEXT NOT NULL,  -- upload | repo_doc | prd | code | research_output | web_cache
    title         TEXT NOT NULL,
    uri           TEXT,                           -- citation pointer to the original
    content_hash  TEXT NOT NULL,                  -- idempotent ingest / dedup
    sensitivity   TEXT NOT NULL DEFAULT 'normal', -- normal | confidential | pii
    status        TEXT NOT NULL DEFAULT 'pending',-- pending | approved | superseded | purged
    superseded_by TEXT,
    version       INTEGER NOT NULL DEFAULT 1,
    curated_by    TEXT,
    approved_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    ttl_days      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_kb_documents_ns ON kb_documents(namespace, status);
CREATE INDEX IF NOT EXISTS idx_kb_documents_hash ON kb_documents(content_hash);

-- ── Chunks ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS kb_chunks (
    chunk_id    TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL REFERENCES kb_documents(doc_id) ON DELETE CASCADE,
    namespace   TEXT NOT NULL,                    -- tenancy partition
    bucket_ids  UUID[] NOT NULL DEFAULT '{}',     -- denormalized grounding scope
    ordinal     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    embedding   vector(%(DIM)s),
    token_count INTEGER NOT NULL DEFAULT 0,
    metadata    JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_doc ON kb_chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_ns ON kb_chunks(namespace);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_buckets ON kb_chunks USING GIN (bucket_ids);

-- ── Buckets (the grounding unit) ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS kb_buckets (
    bucket_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    project_id  TEXT,                              -- NULL = global (Phase 1)
    is_system   BOOLEAN NOT NULL DEFAULT FALSE,    -- e.g. the auto "Platform" bucket
    created_by  TEXT NOT NULL DEFAULT 'system',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_buckets_slug ON kb_buckets(slug);

-- ── Doc ↔ bucket membership (many-to-many) ──────────────────────────────
CREATE TABLE IF NOT EXISTS kb_document_buckets (
    doc_id    TEXT NOT NULL REFERENCES kb_documents(doc_id)  ON DELETE CASCADE,
    bucket_id UUID NOT NULL REFERENCES kb_buckets(bucket_id) ON DELETE CASCADE,
    PRIMARY KEY (doc_id, bucket_id)
);
CREATE INDEX IF NOT EXISTS idx_kb_docbuckets_bucket ON kb_document_buckets(bucket_id);

-- ── Retrieval audit (append-only) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS kb_retrieval_audit (
    audit_id           TEXT PRIMARY KEY,
    request_id         TEXT,
    agent_id           TEXT NOT NULL,
    namespace          TEXT NOT NULL,
    query              TEXT NOT NULL,
    bucket_ids         UUID[] NOT NULL DEFAULT '{}',
    returned_chunk_ids JSONB NOT NULL DEFAULT '[]',
    cited_chunk_ids    JSONB NOT NULL DEFAULT '[]',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kb_audit_req ON kb_retrieval_audit(request_id);

-- ── Decision ledger (append-only provenance) ────────────────────────────
CREATE TABLE IF NOT EXISTS decision_ledger (
    decision_id         TEXT PRIMARY KEY,
    request_id          TEXT NOT NULL,
    agent_id            TEXT NOT NULL,
    project_id          TEXT,
    summary             TEXT NOT NULL,
    retrieved_chunk_ids JSONB NOT NULL DEFAULT '[]',
    recalled_memory_ids JSONB NOT NULL DEFAULT '[]',
    inputs_digest       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_decision_ledger_req ON decision_ledger(request_id);

-- ── Episodic memory (KB-24): time-stamped, owned, decaying experience ────
-- Distinct from the KB (kb_documents/kb_chunks): memory is UNVETTED and
-- NEVER citeable as fact (§5.1). It carries an embedding so recall_memory
-- (KB-25) can do semantic, time-aware lookup over the same vector path.
-- ``superseded_by`` + ``content_hash`` columns exist now so KB-27 (as-of
-- chains) and KB-30 (decay/forget) need no migration later.
CREATE TABLE IF NOT EXISTS agent_memory (
    memory_id     TEXT PRIMARY KEY,
    namespace     TEXT NOT NULL,          -- mem_project_<id> | mem_agent_<id>
    agent_id      TEXT NOT NULL,
    request_id    TEXT,
    project_id    TEXT,
    kind          TEXT NOT NULL,          -- episode | summary | discussion
    text          TEXT NOT NULL,
    outcome       TEXT,                   -- success | failed | partial
    embedding     vector(%(DIM)s),
    content_hash  TEXT,                   -- idempotent capture / dedup
    unvetted      INTEGER NOT NULL DEFAULT 1,
    superseded_by TEXT,                   -- KB-27 as-of chains
    superseded_at TIMESTAMPTZ,            -- KB-27 when supersession happened
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    ttl_days      INTEGER,                -- decay/forget control (KB-30)
    use_count     INTEGER NOT NULL DEFAULT 0,    -- reinforcement signal
    last_used_at  TIMESTAMPTZ
);
-- KB-27: idempotent add for tables created before superseded_at existed.
ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_agent_memory_ns ON agent_memory(namespace, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_memory_hash ON agent_memory(namespace, content_hash);

-- ── Promotion candidates (KB-26): memory → knowledge proposals ───────────
-- The consolidation job detects recurring patterns across episodes and
-- PROPOSES them here (status='pending'). It NEVER auto-promotes — the single
-- controlled doorway from unvetted memory into the citeable KB is the review
-- gate (KB-28), which reuses the AET-13 pending-review posture. ``content_hash``
-- makes proposal creation idempotent so a re-run of the job doesn't pile up
-- duplicates of the same recurring pattern.
CREATE TABLE IF NOT EXISTS kb_promotion_candidates (
    candidate_id  TEXT PRIMARY KEY,
    namespace     TEXT NOT NULL,          -- mem_project_<id> the pattern came from
    project_id    TEXT,
    kind          TEXT NOT NULL DEFAULT 'pattern',  -- pattern | summary
    summary       TEXT NOT NULL,          -- the proposed (candidate) knowledge
    evidence_ids  JSONB NOT NULL DEFAULT '[]',      -- memory_ids backing it
    occurrences   INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending | promoted | rejected
    content_hash  TEXT,                   -- idempotent proposal key
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_by   TEXT,
    reviewed_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_kb_promo_ns ON kb_promotion_candidates(namespace, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_promo_hash
    ON kb_promotion_candidates(namespace, content_hash);

-- ── Retention audit (KB-30): the forgetting paper trail ──────────────────
-- Memory is a lifecycle: write → … → forget. Every deletion the platform does
-- on its own (TTL expiry, relevance pruning of unused episodes) or on request
-- (right-to-be-forgotten purge of a data subject) is logged here, append-only,
-- so "what was forgotten, when, by whom, and how much" is always answerable —
-- the compliance counterpart to the decision ledger.
CREATE TABLE IF NOT EXISTS kb_retention_audit (
    audit_id    TEXT PRIMARY KEY,
    action      TEXT NOT NULL,          -- ttl_expire | prune_unused | forget_subject
    scope       TEXT,                   -- namespace, or the subject term purged
    actor       TEXT NOT NULL DEFAULT 'system',
    counts      JSONB NOT NULL DEFAULT '{}',   -- {memory: N, documents: N, ...}
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kb_retention_audit_created
    ON kb_retention_audit(created_at DESC);

-- ── Retrieval feedback (KB-31): quality improves with use ────────────────
-- A thumbs up/down on a chunk an agent retrieved. Aggregated into a
-- recency-weighted usefulness boost the reranker folds into the final order,
-- so chunks people found useful surface higher over time (and disliked ones
-- sink). One vote per (chunk, user) — re-voting replaces the prior vote.
CREATE TABLE IF NOT EXISTS kb_feedback (
    feedback_id  TEXT PRIMARY KEY,
    chunk_id     TEXT NOT NULL,
    namespace    TEXT NOT NULL,
    request_id   TEXT,
    created_by   TEXT NOT NULL DEFAULT 'unknown',
    vote         SMALLINT NOT NULL,      -- +1 up | -1 down
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_feedback_chunk_user
    ON kb_feedback(chunk_id, created_by);
CREATE INDEX IF NOT EXISTS idx_kb_feedback_chunk ON kb_feedback(chunk_id);
"""
