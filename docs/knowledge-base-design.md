# Design: Agentic Knowledge Base & Memory System

---

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 0.2 |
| Created Date | 2026-06-02 |
| Last Updated | 2026-06-02 |
| Status | Draft — Phase 1 approved for build; later-phase decisions in §22 |
| Product Owner | Chandramouli |
| Related Docs | `docs/prd-knowledge-base.md` (PRD / usage plan), `docs/design-per-project-code-tree.md`, `docs/architecture.md` |
| Locked Decisions (2026-06-02; embedder revised 2026-06-03) | **Datastore: PostgreSQL 16 + pgvector from day one** (not SQLite). **Embeddings: local fastembed (ONNX, in-process), swappable — no third-party account/key/cost** (KB-13a; originally Voyage cloud). **Phase 1 = Platform KB (`kb_platform`); async workers (Redis/arq) deferred — synchronous ingestion.** |
| Supersedes | Force-injected `docs/agent-lessons-learned.md` (folded in as one source — see §6.3) |

---

## 1. Problem Statement

The platform is a **meta-application**: it runs a team of specialist agents to build *other* applications (Projects). Today those agents are effectively **stateless across Requests** and have **no curated knowledge to ground their work in**. Memory exists only in fragments:

| Memory fragment | Where | Lifespan | Limitation |
|---|---|---|---|
| Tool-use loop messages | `BaseAgent.process_task` in-process list | One agent invocation | Discarded on return — the agent's reasoning is never persisted |
| Workflow `artifacts` dict | `WorkflowRunner.run` | One Request | Dies when the workflow returns |
| `agent_traces` / `subtasks` / `token_usage` | SQLite | Durable | Observability only — **no agent ever reads them back** |
| `build_session_messages` | SQLite, 20-message window | Per project chat | Sliding window, no long-term recall, no time-aware query |
| `agent-lessons-learned.md` | Flat file, force-injected | Durable | Append-only (no retirement of stale lessons), no relevance ranking, unbounded growth capped by crude 50 KB truncation |
| Self-learning loop (AET-09..14) | Failure → review gate → lessons doc | Durable | Only captures *failures*; the only "learning" path that exists |

### 1.1 The three things the platform cannot do today

1. **Learn over time from experience.** The only learning path is the failure-driven self-learning loop. There is no general mechanism to accumulate, rank, retire, and reuse knowledge. Stale lessons never get retired; good lessons are diluted by being force-injected wholesale.
2. **Recollect across time.** No agent can answer "what did we discuss / decide / research two months ago." There is no long-term episodic store and no time-aware retrieval.
3. **Justify its actions.** When an agent produces output, there is no trail linking the output to the knowledge and reasoning that produced it. `agent_traces` records *that* an agent ran, never *why* it concluded what it concluded.

### 1.2 The critical correctness gap (application grounding)

Because the platform builds *many* applications, the most severe gap is **cross-application contamination**. A research/content task for Application A has no isolated corpus to ground itself in. Without per-application isolation, knowledge from Application B (different domain, different compliance regime, different architecture) can leak into A's output. For a build-many-apps platform this is not a privacy leak — it is **wrong output produced confidently**.

---

## 2. Goals

1. Give the platform a **governed memory lifecycle** across three distinct stores (knowledge, episodic, decision provenance), not a single vector dump.
2. Let critical agents (research_specialist first) **ground their output in a specific application's context**, with a structural guarantee that they cannot reach another application's knowledge.
3. Make the platform **learn over time** — experiences consolidate into knowledge, wrong lessons get retired, learning is measured.
4. Make every agent action **traceable to the sources that justified it**.
5. Reuse existing platform rails (per-project context threading, the `tools.yaml` permission model, the AET-13 review gate, the PAM cost machinery) so the system is additive, not a rewrite.

### 2.1 Non-Goals (this version)

- **GraphRAG / knowledge-graph traversal.** Deferred — see §23. Hybrid retrieval covers the stated use cases.
- **Full self-editing agent memory (MemGPT/Letta paging).** Episodic recall through the same retrieval path gets the value without the operational risk.
- **Replacing the workflow `artifacts` dict** (per-Request working memory). That stays as-is; this system is the durable layer above it.

---

## 3. Architecture Pattern

**Primary pattern: Agentic RAG, over a Modular hybrid-retrieval pipeline, with a per-agent forced/agentic mode switch.** Rationale and trade-offs are recorded here so future maintainers understand *why* this pattern, not just *what*.

### 3.1 Why Agentic RAG

`BaseAgent.process_task()` is already a ReAct loop (reason → call tool → observe → repeat). Retrieval becomes one more tool in that loop. The agent decides **when** to retrieve, **what** to query, **whether to re-query** after seeing results, and **how to reconcile** internal knowledge against the live web. For research — an inherently multi-hop, iterative task — single-shot classic RAG would be crippling.

### 3.2 The layered decomposition

| Layer | Pattern | Implementation |
|---|---|---|
| Orchestration (how the agent uses memory) | **Agentic RAG** | Retrieval exposed as tools in the existing ReAct loop |
| Retrieval (behind the tool) | **Modular RAG** | `query-rewrite → hybrid (BM25 + vector) → rerank → ACL filter → citation assembly` — each stage swappable |
| Cross-Request recall | **Lightweight episodic memory** | Past outputs ingested as a namespace, retrieved through the same path |

### 3.3 Forced vs. agentic retrieval (the per-agent mode switch)

Pure Agentic RAG has a cost: the agent burns iterations and tokens deciding to retrieve, and sometimes fails to retrieve when it should. So retrieval supports **two modes, chosen per agent** in YAML:

- **`agentic`** — agent calls `knowledge_search` itself, multiple times, self-directed. For research_specialist, architecture_reviewer.
- **`forced`** — top-K relevant chunks are pre-injected into the system prompt before the agent starts; no agent deliberation. This is what the lessons doc does today, generalized. For code agents that always need grounding.
- **`hybrid`** — forced pre-injection of a small grounding set **plus** the `knowledge_search` tool for follow-up. Default for content-grounding tasks.

Config (`config/agents/<id>.yaml`):
```yaml
retrieval:
  mode: agentic            # agentic | forced | hybrid | none
  default_scope: project   # project | global | auto | all
  max_searches: 10         # per-Request agentic retrieval budget
  forced_top_k: 5          # for forced/hybrid pre-injection
```

### 3.4 Deliberate trade-off (recorded)

Agentic RAG has **latency and cost variance** — a task may issue 1 retrieval or 8. With the existing 180s per-call timeout and `max_iterations`, retrieval-heavy runs could hit either ceiling. Mitigations are first-class (§16): per-Request retrieval budget, query caching, forced-mode for cheap cases, and PAM cheap-model assignment for memory-heavy agents. This variance is acceptable because research is a "take time, get it right" task, not a latency-critical one.

---

## 4. The Three Memory Systems

The design treats memory as **three separate systems with different governance**, mapped to the cognitive taxonomy used in memory research (working / procedural / episodic / semantic).

| System | Cognitive type | Answers | Trust | Citeable? |
|---|---|---|---|---|
| **Knowledge Repository** | Semantic (+ procedural) | *What is true* | Vetted, curated | **Yes** — source of truth |
| **Agent Memory** | Episodic (+ working) | *What happened / was said, and when* | Unvetted — "this occurred" | No — informs, never grounds |
| **Decision Ledger** | Provenance | *Why an action was taken* | Immutable record | N/A — it *is* the audit trail |

Working memory (the tool-use loop) stays ephemeral and in-context as today.

### 4.1 Why three stores and not one

A single vector store collapses the trust domains and produces **knowledge poisoning**: unvetted episodic notes get cited as authoritative fact; stale episodes outrank corrected truth; there is no governance surface to gate what becomes "truth." The stores share *infrastructure* (one vector engine, one embedding model, one reranker, one retrieval API) but are kept in separate **collections/namespaces**, with separate **metadata schemas**, **write paths**, **read tools**, and **default ACLs** (§9).

### 4.2 The single controlled doorway: promotion

Episodic memory → Knowledge is possible but **only through a review gate** (reusing the AET-13 pending-review pattern). There is no automatic path from "an agent said it" to "it is organizational truth." This generalizes the existing self-learning loop:

```
request.failed → self_learning_agent observes (episodic)
              → dedup → AET-13 review gate (curator)
              → promoted to Knowledge → consumed by future agents
```

---

## 5. Two Levels of Knowledge: Platform vs. Application

The platform-vs-application distinction is the design's load-bearing wall for grounding.

| | **Platform KB** | **Application KB** |
|---|---|---|
| Scope | The Agent Team platform itself | One Project (one app being built) |
| Type | **Procedural** — how agents do their job | **Substantive** — what is true about this app |
| Contains | Engineering standards, lessons-learned, report-writing craft, coding conventions | The app's PRD, code, API specs, domain docs, brand guidelines, prior research for *this* app |
| Used for | *How* to research/build | *What is true* to ground output in |
| Shared? | Yes, across all projects | **No** — isolated per application |
| Namespace | `kb_platform` | `kb_project_<project_id>` |

### 5.1 The grounding rule (non-negotiable)

> **Substantive claims may be grounded ONLY in the Application KB. The Platform KB may contribute craft (format, method, tone) but NEVER a substantive claim.**

This is what lets an agent draw on shared procedural knowledge to write well while keeping every *fact* grounded in the one application it is working on. The report-writer step is instructed accordingly, and only Application-KB chunks may be footnoted as sources (§12.4).

---

## 6. Per-Application Grounding & Isolation

### 6.1 It rides on existing rails

The platform already threads per-project context end to end. KB scoping is the identical pattern applied to a new tool class:

```
Request (project_id)
  → AgentSystemExecutor._resolve_project_root_for_request()      [EXISTS]
  → NEW: _resolve_kb_namespace_for_request() → kb_project_<id>
  → process_task(..., project_root=..., kb_namespace=...)        [extend existing kwarg threading]
  → _execute_tool(..., kb_namespace=...)
  → knowledge_search() hard-scoped to kb_project_<id>
```

### 6.2 The isolation guarantee (structural, not advisory)

The agent **does not receive a scope/project parameter it can choose**. The namespace is injected from the Request's `project_id` by the executor — exactly as `project_root` is injected for filesystem tools today. research_specialist building App A **cannot** query App B's KB because it never sees B's namespace. Grounding is not "the agent stays in scope politely"; it is structurally impossible to escape, identical to the filesystem-isolation property in `docs/design-per-project-code-tree.md`.

### 6.3 The Application-KB lifecycle

```
1. PROVISION    Project created → auto-create namespaces:
                kb_project_<id>, mem_project_<id>, ledger_project_<id>  (empty)

2. AUTO-POPULATE  As the platform BUILDS the app, ingest what it produces:
                · approved PRD, API specs, task lists, architecture docs
                · the app's code (its per-project working tree)
                · build-chat discussions (→ episodic)
                · prior research/content reports for THIS app

3. HUMAN-ADD    Projects UI "Knowledge" tab:
                · upload app-specific refs (brand guide, domain docs,
                  competitor analysis, interview transcripts)
                · connect app-specific sources; mark authoritative

4. QUERY        Research/content task → knowledge_search hard-scoped to
                kb_project_<id> → grounded output → ingested back
                (episodic, promotable to KB via review gate)

5. PURGE        Project archived/deleted → namespaces purged
                (ties into retention/privacy, §15)
```

Step 2 is the elegant property: **the platform generates the app's own grounding corpus as a byproduct of building it.** Research later in a project is grounded in what the platform itself produced earlier.

### 6.4 Grounding guarantee: citation-or-flag

Every substantive claim in generated content must link to a chunk in `kb_project_<id>`. If a claim cannot be grounded in the Application KB, the agent **must not** fall back to training knowledge and assert it — it **flags**: *"No source in this application's knowledge base supports X — flagging for human input or explicit web research."* This makes grounding verifiable, not best-effort, and feeds the Decision Ledger.

### 6.5 Edge cases

- **Cold start (greenfield).** Day one the Application KB is empty. Retrieval degrades gracefully: the agent leans on the (approved) PRD + uploaded refs + explicitly-flagged web, and labels output "grounded in PRD + web; app KB sparse." An empty KB must not block the first research task.
- **Enhancing an existing app.** At project creation, a one-time ingest seeds `kb_project_<id>` from the existing app's codebase + docs.

---

## 7. The Memory Lifecycle

Memory is a **lifecycle, not a store**. The back half (consolidate → decay → invalidate → forget) is what prevents rot and is explicitly designed here.

```
write → consolidate → retrieve → reinforce/decay → invalidate → forget
```

| Stage | Mechanism | Owner |
|---|---|---|
| **Write** | Ingestion pipeline (KB) / auto-capture on task completion (episodic) / decision record (ledger) | §10 |
| **Consolidate** | Background job distills raw episodes into compact summaries; recurring patterns proposed for promotion | §10.4 |
| **Retrieve** | Hybrid + rerank + ACL filter | §11 |
| **Reinforce / decay** | Human feedback (thumbs up/down) boosts ranking; unused memory decays via recency-weighted score | §11.4, §15 |
| **Invalidate** | Supersession chains — a new doc/lesson marks a prior one stale (`superseded_by`); stale items drop out of retrieval | §9.4 |
| **Forget** | TTL + relevance-pruning + explicit purge (right-to-be-forgotten) | §15 |

---

## 8. Component Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ SOURCES                                                        │
│ approved project artifacts · per-project code tree · uploads   │
│ · build-chat · past agent outputs · platform docs · web cache  │
└───────────────┬────────────────────────────────────────────────┘
                │  Ingestion workers (async; chunk · hash-dedup ·
                │   enrich metadata · scrub PII · embed)
                ▼
┌──────────────────────────────────────────────────────────────┐
│ STORAGE (shared substrate, logically separated namespaces)     │
│ · SQLite/Postgres: documents, chunks, audit, decision ledger   │
│ · Vector index (pgvector → Qdrant): kb_*, mem_* collections    │
│ · BM25 (SQLite FTS5 → OpenSearch): keyword index               │
│ · Object store / repo: original blobs (citations link out)     │
└───────────────┬────────────────────────────────────────────────┘
                │  Retrieval API (stateless, horizontally scalable)
                │   hybrid → rerank → ACL filter → citations
                ▼
┌──────────────────────────────────────────────────────────────┐
│ TOOL SURFACE (ToolRegistry; granted via tools.yaml)            │
│ knowledge_search · knowledge_get · knowledge_cite              │
│ recall_memory · record_decision                                │
│ scope injected from Request.project_id — agent cannot override │
└───────────────┬────────────────────────────────────────────────┘
                ▼
┌──────────────────────────────────────────────────────────────┐
│ AGENTS (per-agent retrieval mode: agentic | forced | hybrid)   │
│ research_specialist (priority) · architecture_reviewer ·       │
│ code_reviewer · prd_specialist · content_creator               │
└──────────────────────────────────────────────────────────────┘

┌─ GOVERNANCE (cross-cutting) ───────────────────────────────────┐
│ Curator role · promotion review gate (reuses AET-13) ·         │
│ immutable audit log · retrieval eval harness · supersession ·  │
│ retention/forgetting · cost budgets                            │
└────────────────────────────────────────────────────────────────┘
```

---

## 9. Data Model

### 9.1 Relational schema (append to `SCHEMA_SQL` in `src/state/sqlite_store.py`, `IF NOT EXISTS`)

```sql
-- A logical document (one source artifact). Chunks point back to it.
CREATE TABLE IF NOT EXISTS kb_documents (
    doc_id        TEXT PRIMARY KEY,
    namespace     TEXT NOT NULL,        -- kb_platform | kb_project_<id> | mem_project_<id>
    project_id    TEXT,                 -- NULL for kb_platform
    source_type   TEXT NOT NULL,        -- prd | code | upload | build_chat | research_output | web_cache | lesson
    title         TEXT NOT NULL,
    uri           TEXT,                 -- citation pointer to the original
    content_hash  TEXT NOT NULL,        -- idempotent ingest; dedup
    sensitivity   TEXT NOT NULL DEFAULT 'normal',  -- normal | confidential | pii
    status        TEXT NOT NULL DEFAULT 'pending', -- pending | approved | superseded | purged
    superseded_by TEXT,                 -- doc_id of the replacement, NULL if current
    version       INTEGER NOT NULL DEFAULT 1,
    curated_by    TEXT,                 -- user_id who approved
    approved_at   TIMESTAMP,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ttl_days      INTEGER                -- NULL = no expiry
);
CREATE INDEX IF NOT EXISTS idx_kb_documents_ns ON kb_documents(namespace, status);
CREATE INDEX IF NOT EXISTS idx_kb_documents_hash ON kb_documents(content_hash);

-- Chunk = retrievable unit. Vector + BM25 indexes key off chunk_id.
CREATE TABLE IF NOT EXISTS kb_chunks (
    chunk_id    TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL,
    namespace   TEXT NOT NULL,          -- tenancy partition (kb_platform; kb_project_<id> in P2)
    bucket_ids  UUID[] NOT NULL DEFAULT '{}',  -- KB buckets this chunk belongs to (denormalized
                                               -- from kb_document_buckets); the grounding filter
    ordinal     INTEGER NOT NULL,       -- position within the doc
    text        TEXT NOT NULL,
    embedding   vector(1024),           -- pgvector column (dim from settings)
    token_count INTEGER NOT NULL DEFAULT 0,
    metadata    JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (doc_id) REFERENCES kb_documents(doc_id)
);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_doc ON kb_chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_ns ON kb_chunks(namespace);
-- GIN index makes the bucket-overlap retrieval filter (bucket_ids && ARRAY[...]) fast.
CREATE INDEX IF NOT EXISTS idx_kb_chunks_buckets ON kb_chunks USING GIN (bucket_ids);

-- ── Knowledge Buckets — the user-defined grounding unit ─────────────────
-- A bucket is a named collection of documents. An agent task is grounded in
-- one or more buckets (selected per-request); retrieval is hard-scoped to
-- them via the bucket_ids overlap filter above. Many-to-many: a doc can be
-- tagged into several buckets. project_id is NULL for global/platform-level
-- buckets (Phase 1); Phase 2 adds project-owned buckets.
CREATE TABLE IF NOT EXISTS kb_buckets (
    bucket_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    project_id  TEXT,                   -- NULL = global; set in Phase 2
    is_system   BOOLEAN NOT NULL DEFAULT FALSE,  -- e.g. the auto "Platform" bucket
    created_by  TEXT NOT NULL DEFAULT 'system',
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_buckets_slug ON kb_buckets(slug);

-- Many-to-many doc ↔ bucket membership. Writes here trigger a sync of the
-- denormalized kb_chunks.bucket_ids for the doc's chunks (done in KnowledgeStore).
CREATE TABLE IF NOT EXISTS kb_document_buckets (
    doc_id    TEXT NOT NULL,
    bucket_id UUID NOT NULL,
    PRIMARY KEY (doc_id, bucket_id),
    FOREIGN KEY (doc_id)    REFERENCES kb_documents(doc_id) ON DELETE CASCADE,
    FOREIGN KEY (bucket_id) REFERENCES kb_buckets(bucket_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_kb_docbuckets_bucket ON kb_document_buckets(bucket_id);

-- Episodic memory: time-stamped experience, owned + decaying.
CREATE TABLE IF NOT EXISTS agent_memory (
    memory_id   TEXT PRIMARY KEY,
    namespace   TEXT NOT NULL,          -- mem_project_<id> | mem_agent_<id>
    agent_id    TEXT NOT NULL,
    request_id  TEXT,
    project_id  TEXT,
    kind        TEXT NOT NULL,          -- episode | summary | discussion
    text        TEXT NOT NULL,
    outcome     TEXT,                   -- success | failed | partial
    unvetted    INTEGER NOT NULL DEFAULT 1,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ttl_days    INTEGER,                -- decay/forget control
    use_count   INTEGER NOT NULL DEFAULT 0,   -- reinforcement signal
    last_used_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agent_memory_ns ON agent_memory(namespace, created_at);

-- Decision ledger: immutable provenance — why an action was taken.
CREATE TABLE IF NOT EXISTS decision_ledger (
    decision_id  TEXT PRIMARY KEY,
    request_id   TEXT NOT NULL,
    agent_id     TEXT NOT NULL,
    project_id   TEXT,
    summary      TEXT NOT NULL,         -- what was decided/concluded
    retrieved_chunk_ids TEXT NOT NULL DEFAULT '[]',  -- JSON: KB chunks that justified it
    recalled_memory_ids TEXT NOT NULL DEFAULT '[]',  -- JSON: episodic memory consulted
    inputs_digest TEXT,                 -- hash of the inputs the agent saw
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    -- intentionally NO update path: append-only, tamper-evident
);
CREATE INDEX IF NOT EXISTS idx_decision_ledger_req ON decision_ledger(request_id);

-- Retrieval audit: who queried what, what came back, was it used.
CREATE TABLE IF NOT EXISTS kb_retrieval_audit (
    audit_id    TEXT PRIMARY KEY,
    request_id  TEXT,
    agent_id    TEXT NOT NULL,
    namespace   TEXT NOT NULL,
    query       TEXT NOT NULL,
    scope       TEXT NOT NULL,          -- project | global | auto | all
    returned_chunk_ids TEXT NOT NULL DEFAULT '[]',
    cited_chunk_ids    TEXT NOT NULL DEFAULT '[]',
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Human feedback on retrieval quality (reinforcement).
CREATE TABLE IF NOT EXISTS kb_feedback (
    feedback_id TEXT PRIMARY KEY,
    chunk_id    TEXT NOT NULL,
    request_id  TEXT,
    rating      INTEGER NOT NULL,       -- +1 useful / -1 not
    note        TEXT,
    created_by  TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 9.2 Vector + keyword indexes

- **Vector:** pgvector column on `kb_chunks` (Phase 1). The `VectorStore` interface (KB-02) abstracts the engine so swapping to **Qdrant** at scale is a config change (§19).
- **Keyword:** **Postgres FTS** Phase 1 → ParadeDB/OpenSearch at scale. Over `kb_chunks.text` + `agent_memory.text`.

### 9.3 Two isolation dimensions: namespace (tenancy) + bucket (grounding)

There are two scope filters, applied together, neither ever omitted:

1. **`namespace`** — the *tenancy* partition (`kb_platform` in Phase 1; `kb_project_<id>` in Phase 2; `mem_*` for episodic). System-controlled.
2. **`bucket_ids`** — the *grounding* scope. A **Knowledge Bucket** is a user-created collection of documents; a chunk carries the `bucket_ids` (UUID[]) it belongs to (many-to-many, denormalized from `kb_document_buckets`). An agent task selects bucket(s) **per request**; the executor injects them and retrieval filters `WHERE namespace = %s AND bucket_ids && %s::uuid[]`.

**The grounding guarantee** (FR-023): the agent receives the bucket scope from the Request and **cannot widen it** — same structural property as the per-project working-tree isolation. A task grounded in bucket A never returns bucket B chunks; an empty selection grounds in the system "Platform" bucket only. This is what makes content "completely grounded to the bucket." Verified by the KB-11 bucket-isolation test.

The auto-ingested platform corpus lands in a system `is_system` bucket ("Platform"); uploaded docs go to whatever bucket(s) the uploader tags them into.

### 9.4 Supersession (invalidation)

`kb_documents.superseded_by` forms a chain. Retrieval excludes `status IN ('superseded','purged')` by default. An "as-of `<date>`" query can opt into superseded versions for historical recall (point-in-time truth, §1.1).

---

## 10. Ingestion Pipeline

### 10.1 Flow

```
source → loader → chunk → content-hash → dedup → PII scrub → enrich metadata
       → embed → write kb_documents + kb_chunks → vector + BM25 index
       → (if source requires approval) status='pending' until curated
```

- **Idempotent** via `content_hash`: re-ingesting an unchanged doc is a no-op; a changed doc creates a new `version` and marks the prior `superseded_by`.
- **Async workers** (arq/Celery; Phase 1 may use a FastAPI `BackgroundTasks` + a simple queue table). Horizontally scalable.
- **Chunking**: structure-aware (markdown headings, code by symbol) with overlap; target ~512–1024 tokens.

### 10.2 Auto-ingest triggers (event-driven, reuses `EventEmitter`)

| Event | Action |
|---|---|
| `project.created` | Provision namespaces |
| Artifact **approved** (PRD/spec/tasks) | Ingest into `kb_project_<id>` |
| `code_commit` success | Ingest changed files of the per-project tree |
| `research_publish.completed` | Ingest the published report into `kb_project_<id>` (and episodic) |
| build-chat message | Append to `mem_project_<id>` (episodic) |
| `request.failed` | Existing self-learning path → candidate lesson (promotion) |
| `project.deleted` | Purge namespaces |

> **OPEN — needs owner input (Q-ING):** auto-ingest only **approved** artifacts (recommended — keeps the KB clean) vs. all generated artifacts including rejected drafts.

### 10.3 PII / sensitivity tagging

Ingestion runs a PII scrub/classifier; documents flagged `sensitivity='pii'` get stricter ACL and are first in line for retention purge (§15).

### 10.4 Consolidation job (the anti-rot mechanism)

A scheduled background task (cron via the existing host supervisor or an APScheduler job) periodically:
1. Summarizes raw episodes older than N days into compact `kind='summary'` memory rows, then expires the raw episodes.
2. Detects recurring patterns across episodes and **proposes** them to the promotion review gate (does not auto-promote).

This is what keeps episodic memory from becoming an unsearchable landfill.

---

## 11. Retrieval Pipeline

### 11.1 Hybrid + rerank (Modular RAG)

```
query → (optional) query-rewrite → ┌ BM25 top-N ┐
                                    ├            ├→ fuse (RRF) → rerank (top-K)
                                    └ vector top-N┘                  │
                                                       → ACL/namespace filter
                                                       → assemble chunks + citations
```

- **Hybrid is mandatory.** BM25 nails exact-token lookups (REQ-IDs, function names, error codes, model ids like `claude-opus-4-7`) where embeddings fail; vector catches semantic matches. This is the **L27 lesson** (deterministic signal before LLM judgment) applied to retrieval.
- **Reranker** fuses and orders; keeps top-K small so context stays bounded.

### 11.2 Scope resolution (the grounding enforcement point)

The retrieval API computes the namespace set from `(agent_id, project_id, requested_scope)` — the agent's `requested_scope` is bounded by its YAML `default_scope` and any elevated grant:

```
scope=project → [kb_project_<id>]                         (strict grounding default for content)
scope=global  → [kb_platform]                             (craft only)
scope=auto    → [kb_platform, kb_project_<id>]            (facts from project, craft from platform)
scope=all     → [kb_platform, kb_project_*]               (REQUIRES elevated grant; always audited)
```

> **OPEN — needs owner input (Q-SCOPE):** for content-grounding tasks, **strict** (`scope=project`, facts only from the app — recommended given "completely grounded only to its respective application") vs. **auto** (allow platform craft to mix in). Recommendation: facts strictly per-app; craft from platform allowed but never citeable as fact.

### 11.3 Trust precedence

When KB and memory both match, **KB outranks memory**; memory informs reasoning but is never returned by `knowledge_search` (different tool, §12). The reranker weights by source-trust and recency.

### 11.4 Reinforcement

`kb_feedback` ratings and `agent_memory.use_count` feed a recency- and usefulness-weighted boost into the reranker, so quality improves with use instead of merely existing.

---

## 12. Tool Surface

New tools registered in `ToolRegistry` and granted per-agent via `config/tools.yaml` `available_to` (same mechanism as `web_search`, `github_api`).

### 12.1 `knowledge_search(query, scope?, filters?)`
Queries the **Knowledge** namespaces only. Returns chunks tagged `[KB · citeable · <source>]` with `chunk_id`s. `scope` bounded by agent config; namespace injected from Request — agent cannot widen beyond its grant.

### 12.2 `knowledge_get(doc_id)`
Pulls a full document for deep context after a search hit.

### 12.3 `recall_memory(query, window?)`
Queries the **episodic** namespace for this agent/project. Returns rows tagged `[MEMORY · prior experience · unvetted]`. Supports time-aware `window` ("last 60 days", "as of 2026-03"). **Never citeable as fact.**

### 12.4 `knowledge_cite(chunk_id)`
Records a citation in `kb_retrieval_audit.cited_chunk_ids` and emits the footnote into the agent's output. **Only Knowledge chunks are accepted** — attempting to cite a memory row is rejected. This is the mechanical enforcement of the §5.1 grounding rule.

### 12.5 `record_decision(summary, chunk_ids, memory_ids)`
Appends to the immutable `decision_ledger`. Called by the agent (or auto-derived from the trace) at decision points so "why" is captured. Feeds the justification trail.

---

## 13. Agent Integration

### 13.1 Thread kwargs (extends PAM-06 pattern)

`process_task` already receives `project_root`; add `kb_namespace` + `retrieval_config`, threaded the same way to `_execute_tool` and into the new tools. No instance state — concurrency-safe by the same argument as PAM-06.

### 13.2 Forced/hybrid pre-injection

For `mode in (forced, hybrid)`, before the loop starts, the executor runs a retrieval with the Request's framing, takes `forced_top_k` chunks, and prepends them to the system prompt (replacing today's wholesale `_load_cross_agent_lessons()` dump with ranked, relevant chunks).

### 13.3 Priority rollout order

1. **research_specialist** (`hybrid`, `scope=auto`, facts-strict) — the headline use case
2. **content_creator** (`hybrid`, `scope=project`)
3. **architecture_reviewer**, **code_reviewer** (`agentic`, can pull ADRs/prior reviews)
4. **prd_specialist** (`hybrid` — ground new PRDs in the app's existing knowledge)
5. Code agents (`forced` — relevant lessons replace the wholesale lessons dump)

---

## 14. Governance

| Control | Mechanism | Reuses |
|---|---|---|
| **Curator role** | New `kb_curator` capability; gates promotion + approves uploads | RBAC in `config/project.yaml` |
| **Promotion gate** | Memory→KB and lesson candidates pass human review | **AET-13 pending-review gate** |
| **Immutable audit** | `decision_ledger` (append-only) + `kb_retrieval_audit` | `agent_traces` pattern |
| **Eval harness** | Gold queries → expected chunks, scored on every embedding/ranker change (recall@k, MRR) | new |
| **Supersession** | `superseded_by` chains; stale items leave retrieval | §9.4 |

The **eval harness is non-negotiable for enterprise**: without gold queries you cannot tell whether an embedding-model or ranker change helped or hurt. It runs in CI like the pytest suite.

---

## 15. Privacy, Retention & Forgetting

- **TTL** per document/memory (`ttl_days`); the consolidation job enforces expiry.
- **Right-to-be-forgotten:** a purge operation deletes rows + vector points + BM25 entries by `doc_id`/subject and records the purge in audit. Designed in from day one because retrofitting deletion into a vector store is painful.
- **Sensitivity tiers** (`normal | confidential | pii`) drive ACL and purge priority.
- **Decay:** unused memory (`use_count`, `last_used_at`) loses retrieval weight over time and is pruned by the consolidation job.

> **OPEN — needs owner input (Q-RET):** episodic retention policy — hard TTL (e.g., 180 days) vs. relevance-pruning vs. keep-forever-until-storage-pressure. And: must we support per-subject purge for compliance (GDPR-style)? *This decision shapes the schema and the consolidation job most — answer first.*

---

## 16. Cost Governance

Every write = embedding cost; every agentic retrieval = embedding + LLM tokens. Controls:
- **Per-Request retrieval budget** (`max_searches`) — a confused agent can't loop the model into a rate-limit wall.
- **Query cache** within a Request — identical query returns cached chunks, zero cost.
- **PAM synergy** — assign memory-heavy agents (research) a cheaper model (Haiku) so multiplied call volume costs less; cost is attributed per the existing `TokenTracker` catalog pricing.
- **Forced-mode** for always-grounded agents avoids spending agentic budget on deliberation.

---

## 17. Functional Requirements

| ID | Requirement |
|---|---|
| FR-001 | Each Project auto-provisions isolated `kb_project_<id>`, `mem_project_<id>`, `ledger_project_<id>` namespaces on creation. |
| FR-002 | `knowledge_search` is hard-scoped to the Request's project namespace set; the agent cannot widen scope beyond its YAML grant. |
| FR-003 | Substantive claims may cite only Knowledge chunks; `knowledge_cite` rejects memory rows. |
| FR-004 | Approved project artifacts (PRD, specs, tasks, code, published research) auto-ingest into the app's KB. |
| FR-005 | Humans can upload/connect app-specific sources via a Projects "Knowledge" tab; uploads require curator approval before going live. |
| FR-006 | Retrieval is hybrid (BM25 + vector) followed by a reranker. |
| FR-007 | `recall_memory` supports time-aware queries ("last N days", "as of date") and returns unvetted, non-citeable context. |
| FR-008 | Every retrieval is recorded in `kb_retrieval_audit` (returned + cited chunks). |
| FR-009 | Every agent decision point appends an immutable `decision_ledger` row linking the conclusion to the chunks/memory that justified it. |
| FR-010 | Memory→Knowledge promotion passes the AET-13 review gate; no automatic promotion. |
| FR-011 | Documents support supersession; superseded items are excluded from default retrieval. |
| FR-012 | A background consolidation job summarizes and expires old episodes and proposes recurring patterns for promotion. |
| FR-013 | Per-document/memory TTL and a per-subject purge operation exist; purges cascade to vector + BM25 indexes and are audited. |
| FR-014 | Per-agent retrieval mode (`agentic | forced | hybrid | none`), default scope, search budget, and forced top-K are configured in agent YAML. |
| FR-015 | An eval harness scores retrieval (recall@k, MRR) against gold queries and runs in CI. |
| FR-016 | Citation-or-flag: an agent that cannot ground a claim in the app KB flags it rather than asserting from training knowledge. |
| FR-017 | The lessons-learned doc is ingested as one `kb_platform` source (procedural) and retrieved by relevance, replacing wholesale force-injection. |
| FR-018 | `GET`/admin APIs expose KB contents, retrieval audit, and decision ledger for inspection (Story Board / new Knowledge views). |

---

## 18. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-001 | Retrieval API is stateless and horizontally scalable. |
| NFR-002 | Data layer scales to 1M+ chunks via engine swap (pgvector → Qdrant) with no code change above the retrieval abstraction. |
| NFR-003 | Namespace isolation is structural (enforced in-query), not advisory; verified by an isolation test (App A query never returns App B chunks). |
| NFR-004 | Ingestion is idempotent (content-hash) and re-runnable. |
| NFR-005 | Decision ledger and retrieval audit are append-only / tamper-evident. |
| NFR-006 | All new state goes through `StateStore`; no direct SQLite from routes (existing convention). |
| NFR-007 | Backend boot tolerates an unavailable vector engine (degrade: agents run without retrieval, log a warning) — mirrors the PAM soft-fail posture. |
| NFR-008 | Embedding provider is swappable (cloud ↔ self-hosted) behind one interface. |

---

## 19. Technology Choices

**LOCKED (2026-06-02):** the datastore is **PostgreSQL 16** hosting both relational KB metadata and vectors (via **pgvector**) and keyword search (via **Postgres FTS**). SQLite is **not** the target — it is single-writer, has no production vector search, and cannot scale horizontally; it remains available only as a local-dev implementation behind the storage interfaces. This reverses an earlier "embedded store to match the serverless posture" suggestion, which optimized for consistency with the current setup over the stated enterprise bar.

| Concern | Phase 1 (≤10K chunks) — **LOCKED** | At scale (1M+) — escape hatch | Note |
|---|---|---|---|
| Relational metadata | **PostgreSQL 16** | read replicas / Aurora | KB tables; platform state migration is a separate effort |
| Vector index | **pgvector** (same Postgres) | **Qdrant** | Behind `VectorStore`; swap = config |
| Keyword index | **Postgres FTS** (tsvector + GIN) | ParadeDB `pg_search` → OpenSearch | Behind `KeywordStore` |
| Embeddings | **Local fastembed `bge-small-en-v1.5`** (ONNX, in-process, 384-dim) | hosted (Voyage/OpenAI) for scale | Swappable behind `Embedder`; no account/key/cost (KB-13a) |
| Reranker | **none** (RRF fusion only, Phase 1) | local `bge-reranker` / hosted | Optional cross-encoder drops in behind `Embedder.rerank` |
| Object store (blobs) | **S3** (you're on AWS) | same | MinIO for local dev |
| Ingestion workers | **synchronous via FastAPI BackgroundTasks** | **arq + Redis** | Redis/arq **deferred** until ingestion volume justifies async |
| Scheduler (consolidation) | n/a Phase 1 | arq cron / host supervisor | Consolidation is a later phase |
| Orchestration glue | **hand-rolled** over the interfaces | same | No LangChain/LlamaIndex — fights the isolation/governance requirements |

**Net infra delta for Phase 1: exactly one new service (`postgres` with pgvector).** No Redis yet. Embeddings run **locally** (fastembed ONNX, CPU) — no external API, no GPU.

> **Privacy note (revised KB-13a):** embeddings are now computed **locally** in the backend container — the platform's docs never leave the host for embedding. The earlier Voyage-cloud concern is moot. A hosted embedder can still be swapped in behind the same `Embedder` interface if scale ever demands it.

---

## 20. Phased Rollout

Task IDs use the `KB-` prefix, matching the `docs/task-list.md` post-release subsection format. Effort: S/M/L.

### Phase 1 — Platform KB consumed by the existing team (APPROVED FOR BUILD)

Scope: stand up Postgres+pgvector, ingest the platform's own corpus into `kb_platform`, wire the existing agents to retrieve from it, and ship the citation + reasoning-trail surfaces. Per-application isolation, episodic memory, and the full lifecycle are deferred to later phases. Full product detail in `docs/prd-knowledge-base.md`.

| ID | Task | Effort |
|---|---|---|
| KB-01 | Postgres + pgvector compose service + config/secret + deps (`psycopg`, `pgvector`, `fastembed`) | M |
| KB-02 | `Embedder` / `VectorStore` / `KeywordStore` interfaces + `PgVectorStore` / `PostgresFtsStore` / `FastEmbedEmbedder` (local ONNX) impls; soft-fail if Postgres down (NFR-007) | M |
| KB-03 | KB schema (`kb_documents`, `kb_chunks`, `kb_retrieval_audit`, `decision_ledger`) via Alembic migration + `KnowledgeStore` CRUD | M |
| KB-04 | Structure-aware chunker (markdown by heading, code by symbol via tree-sitter, plaintext fallback) | M |
| KB-05 | Ingestion pipeline (loader → chunk → content-hash dedup → PII scan → embed → index), idempotent, **synchronous** (BackgroundTasks) | L |
| KB-06 | Platform-corpus ingest: management command + admin reindex endpoint (`docs/*.md`, lessons, CLAUDE.md, selected research → `kb_platform`) | M |
| KB-07 | Retrieval pipeline (hybrid pgvector + FTS → RRF fuse → [optional rerank] → top-K + citations + audit write) | L |
| KB-08 | Tools `knowledge_search` + `knowledge_get`; grant via `tools.yaml`; namespace fixed to `kb_platform` | M |
| KB-09 | Agent integration: YAML `retrieval:` config + forced/hybrid pre-injection; **replace wholesale `_load_cross_agent_lessons()` dump** (FR-005) | M |
| KB-10 | Admin API + Knowledge screen (list/search/reindex; approve/retire/purge) + citations + "Why" reasoning trail + Grounding Report (FR-010/011/016) | L |
| KB-11 | Tests: ingest idempotency, hybrid retrieval, lessons-replacement parity, audit/ledger written, purge cascade | M |
| KB-12 | Eval harness (gold queries, recall@k/MRR) in CI; baseline recorded | M |

### Phase 2 — Per-application binding & isolation
| ID | Task | Effort |
|---|---|---|
| KB-13 | `project.created`/`deleted` → provision/purge `kb_project_<id>` namespaces | S |
| KB-14 | Auto-ingest on approved artifact / `code_commit` / `research_publish.completed` (event hooks) | M |
| KB-15 | `_resolve_kb_namespace_for_request` + thread `kb_namespace`; hard scope isolation (NFR-003) | M |
| KB-16 | Projects UI "Knowledge" tab: upload, list, mark authoritative, curator-approve | M |
| KB-17 | Wire research_specialist + content_creator to per-app KB (facts-strict); citation-or-flag; isolation E2E | M |

### Phase 3 — Decision provenance depth & broader wiring
| ID | Task | Effort |
|---|---|---|
| KB-18 | `record_decision` tool + decision ledger at all agent decision points | S |
| KB-19 | Wire architecture_reviewer / code_reviewer / prd_specialist retrieval modes | M |

### Phase 4 — Memory lifecycle + episodic
| ID | Task | Effort |
|---|---|---|
| KB-20 | `agent_memory` store; auto-capture episodes on task completion; build-chat → episodic | M |
| KB-21 | `recall_memory` tool with time-aware queries ("couple months ago") | S |
| KB-22 | Consolidation job (summarize + expire + propose promotions) | M |
| KB-23 | Supersession chains + as-of retrieval | S |
| KB-24 | Promotion gate (memory→KB) reusing AET-13 review UI | M |

### Phase 5 — Governance, privacy, quality, scale
| ID | Task | Effort |
|---|---|---|
| KB-25 | `kb_curator` role + RBAC | S |
| KB-26 | Retention: TTL enforcement + automated relevance-pruning | M |
| KB-27 | Human feedback (thumbs up/down) → reranker reinforcement | S |
| KB-28 | Cost budgets: per-Request retrieval budget + query cache (Redis) | S |
| KB-29 | Async ingestion (Redis + arq) when volume justifies; scale-out path (Qdrant / ParadeDB) | M |

---

## 21. Verification Plan

1. **Isolation (the critical test):** ingest distinct docs into `kb_project_A` and `kb_project_B`; run a research task on A; assert returned + cited chunks are 100% from A; assert a direct attempt to scope to B is rejected.
2. **Grounding guarantee:** task with a claim unsupported by the app KB → agent emits a flag, not an assertion.
3. **Hybrid correctness:** exact-token query (a function name) returns the right chunk via BM25 even when semantically diluted.
4. **Lifecycle:** ingest → supersede → assert old version drops from default retrieval but is reachable via as-of.
5. **Promotion:** an episodic pattern → review gate → appears in KB → retrievable.
6. **Privacy:** purge a subject → rows + vector + BM25 entries gone; audit records the purge.
7. **Eval:** gold-query suite passes recall@k threshold; CI fails on regression after a ranker change.
8. **Concurrency:** two research tasks on different apps concurrently never cross-pollinate (extends the PAM concurrency test).

---

## 22. Open Decisions (need owner input before build)

| ID | Decision | Recommendation |
|---|---|---|
| Q-RET | Episodic retention + per-subject purge for compliance | Hard TTL (180d) + relevance-prune + support per-subject purge. **Answer first — shapes schema.** |
| Q-SCOPE | Content grounding: strict (facts only from app) vs. auto (allow platform craft) | Facts strict per-app; platform craft allowed but never citeable |
| Q-EMB | Embeddings self-hosted vs. cloud API | Cloud embeddings + self-hosted reranker, swappable |
| Q-SCALE | Steady-state corpus size + ingestion cadence | Design for 10K Phase 1, 1M via engine swap |
| Q-ING | Auto-ingest only approved artifacts vs. all | Approved only — keep KB clean |
| Q-XAPP | Cross-app knowledge ever allowed | Forbidden by default; opt-in via explicit, audited project-link grant |
| Q-CURATE | Who owns curation — per-project vs. platform-wide curator | Per-project curator + platform curator for `kb_platform` |
| Q-LEARN | Learning autonomy — auto-apply vs. review-gated | Review-gated (AET-13) until eval proves quality |
| Q-JUSTIFY | Decision ledger depth — full reasoning traces vs. decision summaries | Summaries + source links (cheaper, sufficient audit); full traces opt-in |

---

## 23. Out of Scope (this version)

- **GraphRAG / entity-relationship traversal** — revisit if research questions prove multi-hop/relationship-heavy.
- **Self-editing agent memory** (MemGPT-style paging).
- **Cross-platform federation** (KBs spanning multiple Agent Team instances).
- **Real-time streaming ingestion** (Phase 1 is batch/event-driven).
- **Fine-tuning models on the KB** — retrieval-only grounding.

---

## 24. Appendix

### 24.1 Glossary

| Term | Meaning |
|---|---|
| Namespace | Isolation boundary for a collection (`kb_platform`, `kb_project_<id>`, `mem_*`) |
| Knowledge Repository | Vetted, citeable semantic knowledge |
| Agent Memory | Unvetted, decaying episodic experience |
| Decision Ledger | Immutable provenance of why actions were taken |
| Promotion | Reviewed move from memory → knowledge |
| Grounding | Tying every substantive claim to an Application-KB chunk |
| Hybrid retrieval | BM25 (keyword) + vector (semantic) fused then reranked |
| Consolidation | Background distillation of raw episodes into summaries |

### 24.2 Cognitive taxonomy → platform mapping

| Cognitive memory | Platform realization | Status |
|---|---|---|
| Working | Tool-use loop `messages` | Exists (ephemeral) |
| Procedural | `kb_platform` (lessons, standards) | New (generalizes lessons doc) |
| Episodic | `agent_memory` / `mem_*` | New |
| Semantic | `kb_project_<id>` / `kb_platform` | New |

### 24.3 Relationship to existing platform mechanisms

| New concept | Reuses / generalizes |
|---|---|
| Namespace isolation | Per-project working-tree isolation (`docs/design-per-project-code-tree.md`) |
| Tool grants | `config/tools.yaml` `available_to` |
| Promotion review gate | Self-learning pending-review (AET-13) |
| Decision ledger | `agent_traces` (extended with provenance) |
| Cost attribution | `TokenTracker` catalog pricing (PAM-15) |
| Per-agent retrieval config | Per-agent model config pattern (PAM) |
| Kwarg threading of `kb_namespace` | `project_root` / model kwarg threading (PAM-06) |
```
