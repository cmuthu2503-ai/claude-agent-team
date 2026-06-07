# Product Requirements Document (PRD)
# Agentic Knowledge Base & Memory System

---

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 1.1 |
| Created Date | 2026-06-02 |
| Last Updated | 2026-06-02 |
| Status | Draft — approved for Phase 1 build |
| Product Owner | Chandramouli |
| Engineering Design | `docs/knowledge-base-design.md` (the *how*; this PRD is the *what* + *why*) |
| Frozen UI design | `docs/mockups/kb-buckets-mockup.html` v1.0 (upload · tagging · buckets · ground-a-task) |
| Phase 1 Scope | Platform KB + **Knowledge Buckets** + **document upload**, consumed by the existing agent team |
| Locked Decisions | Datastore **Postgres 16 + pgvector**; embeddings **local fastembed (ONNX)** — no third-party key/cost (KB-13a; originally Voyage); async **deferred**; **upload in Phase 1**; bucket binding **per-request**; doc→bucket **many-to-many**. |
| v1.1 changes | Added Knowledge Buckets (FR-020..024), document upload locked into Phase 1, frozen UI mockup wired into §7 (4 screens). |

---

## 1. Executive Summary

### 1.1 Product Vision

Give the agent team a **governed, queryable memory** so the platform stops working from a blank slate on every task. Agents retrieve relevant organizational knowledge before acting, **ground every substantive claim in a cited source**, and leave behind a **traceable record of why they concluded what they concluded**. Users can feed documents in, watch the team use them, and click any output back to the exact source that justified it.

Phase 1 delivers this for the **platform's own knowledge** — architecture, conventions, design decisions, and accumulated lessons — so the existing team immediately works smarter. Later phases extend the same machinery to per-application grounding and long-term memory.

### 1.2 Problem Statement

Today the agent team is effectively **stateless and ungrounded**:

- The only knowledge any agent consumes is `agent-lessons-learned.md`, **force-injected wholesale** (up to 50 KB, unranked) into code agents. Every agent sees every lesson regardless of relevance, burning context budget and diluting the signal.
- No agent can look up the platform's architecture, conventions, prior designs, or past research. That knowledge exists in `docs/` but is invisible to the team.
- Agent output carries **no provenance**. When research_specialist asserts something, there is no way to see *what it read* or *why it concluded that* — the reasoning is discarded the moment the task returns.
- There is no way for a user to **feed a document into the team's awareness** short of pasting it into a request.

The result: repeated mistakes, generic output, zero auditability, and a growing `docs/` corpus the team cannot use.

### 1.3 Target Users

| Persona | Need | How the KB serves them |
|---|---|---|
| **Admin / Platform owner** | Control what the team knows; trust its output | Feeds/curates documents; audits reasoning; resets/retires stale knowledge |
| **Developer** | Submit requests and get grounded, traceable results | Sees citations and a "why" trail on every agent output |
| **Viewer / Stakeholder** | Understand and trust what the team produced | Reads the grounding report; clicks claims back to sources |
| **The agent team itself** (machine consumer) | Relevant knowledge at the moment of work | Retrieves via tools; grounds claims; records decisions |

### 1.4 What success looks like

A developer submits a request. Before the agents act, they pull the relevant slice of platform knowledge. The output comes back with footnoted citations. The developer clicks a footnote, sees the exact source paragraph and the document it came from, and opens a "Why" panel showing what the agent searched, what it found, what it used, and anything it couldn't ground. An admin, reviewing later, can reconstruct the entire reasoning chain months after the fact.

---

## 2. Goals & Success Metrics

### 2.1 Goals

1. The agent team retrieves **relevant** platform knowledge at the point of work (not a wholesale dump).
2. Users can **feed documents** into the team's knowledge with a clear, governed flow.
3. Every grounded claim is **cited**; every agent decision is **traceable** to its sources.
4. Stale knowledge can be **retired**; the system does not rot.
5. Build on the existing stack and conventions (FastAPI, Postgres, the tool-registry permission model, the React/Zustand frontend) — additive, not a rewrite.

### 2.2 Success Metrics

| Metric | Baseline (today) | Phase 1 target |
|---|---|---|
| Agent prompt grounding | 50 KB wholesale lessons dump | Top-K relevant chunks (≤ ~4 KB), relevance-ranked |
| Citations per research/content output | 0 internal | ≥ 80% of substantive claims cited or explicitly flagged |
| Knowledge coverage | 0 platform docs queryable | 100% of `docs/*.md` + lessons + CLAUDE.md indexed |
| Reasoning traceability | None | 100% of KB-using runs have a retrieval audit + citation trail |
| Retrieval quality (eval harness) | n/a | recall@5 ≥ agreed threshold on gold queries; CI-gated |
| Token cost of grounding per code-agent call | ~13K tokens (lessons dump) | Reduced via ranked retrieval (measured) |

---

## 3. Personas & Permissions

| Capability | Viewer | Developer | Admin / Curator |
|---|---|---|---|
| See citations + grounding report on outputs | ✅ | ✅ | ✅ |
| Open the "Why" reasoning trail | ✅ | ✅ | ✅ |
| Submit requests that consume the KB | — | ✅ | ✅ |
| Upload / feed documents | — | — | ✅ |
| Approve documents (curation) | — | — | ✅ |
| Reindex / retire / supersede documents | — | — | ✅ |
| Configure per-agent retrieval mode | — | — | ✅ |

Enforced via the existing role model (`viewer → developer → admin`) and `tools.yaml` grants.

---

## 4. Usage Plan (the core of this PRD)

This section describes, end to end, how knowledge **gets in**, how it **gets used**, and how users **make sense of it**.

### 4.1 Feeding documents into the application

There are three ingestion paths, in order of Phase 1 priority:

**(A) Automatic platform-corpus ingestion (Phase 1, primary).**
On deploy and on demand, the platform indexes its own knowledge into `kb_platform`:
- `docs/*.md` (architecture, cross-cutting concerns, design docs, PRDs, playbooks)
- `agent-lessons-learned.md` (folded in — replaces wholesale injection)
- `CLAUDE.md` (conventions)
- Selected `docs/research/*` published outputs

An admin triggers a (re)index from the **Knowledge** admin screen or via `POST /api/v1/knowledge/reindex`. Ingestion is **idempotent** — re-running on unchanged content is a no-op; a changed document creates a new version and supersedes the old.

**(B) Document upload (Phase 1 — LOCKED).**
A user opens **Knowledge → Add documents**, drags files into the dropzone (matching the frozen mock `docs/mockups/kb-buckets-mockup.html` Screen 01), assigns them to bucket(s), and the system:
1. Detects type (Markdown, text, PDF, DOCX, source code) and parses it.
2. Chunks it (structure-aware: headings for prose, symbols for code).
3. Runs a **PII scan** (flags sensitive content).
4. Embeds and indexes it as **`status: pending`**, tagged into the selected bucket(s).
5. Holds it for **curator approval** before it becomes retrievable.

**(C) Connect a source (later phase).**
Pull from external systems (Confluence, Notion, a repo) on a schedule. Out of scope for Phase 1; the ingestion interface is built so this is an added loader, not a redesign.

**What the user sees during ingestion** — a per-document status lifecycle:
```
uploaded → parsing → chunking → embedding → pending-review → approved (live)
                                                  └→ rejected
```
Each document row shows: title, source type, chunk count, status, last-indexed timestamp, sensitivity tag, and who approved it.

**Supported formats (Phase 1):** Markdown, plain text, source code, PDF, DOCX.

### 4.2 How the agent team uses the knowledge base

Retrieval is **per agent, by mode**, configured in each agent's YAML:

| Mode | Behavior | Phase 1 agents |
|---|---|---|
| `forced` | Top-K relevant chunks pre-injected into the system prompt before the agent starts. Replaces the wholesale lessons dump. | Code agents (backend, frontend, reviewer, tester, devops, security) |
| `hybrid` | Forced pre-injection **plus** a `knowledge_search` tool for self-directed follow-up | research_specialist, architecture_reviewer, prd_specialist |
| `agentic` | Agent decides entirely when/what to retrieve, multiple times | (available; used where reasoning-heavy lookup is needed) |
| `none` | No retrieval | agents that don't benefit |

**The user-visible flow** (e.g., a research request):
1. User submits a request as today.
2. Before/while the agent works, it retrieves from `kb_platform` (the user sees a "retrieving knowledge" activity state on the Team/Request view — reuses the existing live-activity surface).
3. The agent produces output with **inline citations** on substantive claims.
4. If the agent cannot ground a claim, it **flags** it rather than asserting (citation-or-flag).
5. The output, its citations, and the reasoning trail are persisted and shown on the Request Detail page.

**The immediate behavioral change Phase 1 ships:** code agents stop receiving the entire lessons file and instead receive the *relevant* lessons + standards for the task at hand — faster, cheaper, sharper.

### 4.3 Making sense of reasoning & tracing back (explainability)

This is a first-class product surface, not an afterthought. Three connected views:

**(1) Inline citations.**
Every grounded claim in an agent's output carries a footnote marker, e.g. `…the supervisor runs on the host, not in Docker [KB-12]`. Clicking `[KB-12]`:
- Opens a **source drawer** showing the exact chunk text.
- Links to the full source document and the **version as of** the time it was used.
- Shows provenance: source type, ingested date, approved-by.

**(2) The "Why" reasoning trail (per agent run).**
A panel on Request Detail / Story Board, reconstructed from the retrieval audit + decision ledger:
- **Searched:** the queries the agent issued (`"supervisor deployment flow"`, …).
- **Retrieved:** which chunks came back, with relevance scores.
- **Used:** which chunks it actually cited.
- **Flagged:** claims it could not ground ("no source supports X").
- **Concluded:** the decision summary linking the conclusion to the cited chunks.

This answers, months later, *"why did the agent say this?"* — the data is immutable and append-only.

**(3) The Request-level Grounding Report.**
For a completed research/content task, a summary card:
- N substantive claims · M grounded · K flagged
- Sources used (distinct documents) + coverage
- A "fully grounded / partially grounded / ungrounded" badge

**Traceability chain (the full back-track):**
```
output claim → citation → chunk → document → original source (file/upload/URL)
            → ingested-when · version · approved-by · sensitivity
```
A user can start at any agent statement and walk all the way back to the original file and the moment it entered the system.

### 4.4 Curation & governance workflow

- **Curator (admin)** approves uploaded documents before they go live; nothing user-uploaded is retrievable until approved.
- **Supersession:** when a newer document replaces an older one, the old version is marked `superseded` and drops out of default retrieval (still reachable for "as-of" historical queries).
- **Retirement:** an admin can retire a document; it leaves retrieval and is audit-logged.
- **The platform corpus (auto-ingested docs)** is trusted by default (it's the team's own repo); user uploads require explicit approval.

### 4.5 Privacy, retention & forgetting

- Documents are sensitivity-tagged (`normal | confidential | pii`); PII-flagged content gets stricter handling.
- A **purge** operation removes a document and all its chunks + vector entries + keyword entries, recorded in audit (supports right-to-be-forgotten).
- Phase 1 corpus is internal platform docs (low sensitivity); the privacy machinery is built but lightly exercised until per-application data (Phase 2) arrives.

### 4.6 Knowledge Buckets (the grounding unit)

A **bucket** is a user-created, named collection of documents — the boundary an agent task is grounded to. This is the user-facing answer to "ground this work only in *this* set of documents."

**Lifecycle:**
```
1. CREATE   user makes a bucket ("Acme Corp Brand", "Healthcare Compliance")
2. UPLOAD   user uploads docs and tags them into one or more buckets
            (many-to-many — a shared style guide can live in several buckets)
3. APPROVE  uploaded docs are PII-scanned, held pending, curator-approved → live
4. GROUND   on request submit, the user selects bucket(s) to ground the task in
5. ISOLATE  the executor injects those bucket ids into retrieval; agents are
            hard-scoped to them — a task grounded in bucket A structurally
            cannot retrieve bucket B's documents (FR-023). Citation-or-flag
            still applies: a claim with no source in the selected bucket(s)
            is flagged, not asserted.
```

**Binding model (locked):** per-request selection — the user chooses the bucket(s) when submitting each request, so the same agent can work different buckets across tasks. Empty selection grounds the task in platform knowledge only (the system "Platform" bucket).

**Why buckets (vs. rigid project namespaces):** the bucket *is* the user-controlled grounding scope. A bucket can represent one application's knowledge, a domain corpus, a client's materials — whatever the user curates. It generalizes the per-application grounding goal and makes it available in Phase 1; Phase 2 adds project-*owned* buckets that a project's requests auto-select (FR-018).

**Isolation guarantee:** identical to the per-project working-tree property — the agent receives the bucket scope from the Request and cannot widen it. Proven by the bucket-isolation test (KB-11): a task grounded in bucket A never returns bucket B chunks, and concurrent A/B tasks never cross-pollinate.

---

## 5. Functional Requirements

| ID | Requirement | Phase |
|---|---|---|
| FR-001 | The platform indexes its own corpus (`docs/*.md`, lessons, CLAUDE.md, selected research) into `kb_platform`. | 1 |
| FR-002 | Ingestion is idempotent (content-hash); changed docs create a new version and supersede the old. | 1 |
| FR-003 | Retrieval is hybrid (vector + keyword) followed by a reranker, returning chunks + citation pointers. | 1 |
| FR-004 | Agents retrieve per a YAML `retrieval` mode (`forced | hybrid | agentic | none`). | 1 |
| FR-005 | The wholesale lessons-doc injection is replaced by ranked retrieval; agents still receive critical lessons (parity-tested). | 1 |
| FR-006 | Substantive claims in agent output carry inline citations to KB chunks. | 1 |
| FR-007 | Citation-or-flag: an agent that cannot ground a claim flags it rather than asserting. | 1 |
| FR-008 | Every retrieval is recorded (queries, returned chunks, cited chunks) — the retrieval audit. | 1 |
| FR-009 | Every agent decision point records a summary linking the conclusion to the chunks that justified it (decision ledger). | 1 |
| FR-010 | A user can open a "Why" reasoning trail for any KB-using agent run. | 1 |
| FR-011 | A user can click a citation and see the source chunk, the full document, and its provenance. | 1 |
| FR-012 | An admin can (re)index the platform corpus on demand. | 1 |
| FR-013 | A user can upload documents (md/txt/pdf/docx/code); each is PII-scanned, held `pending`, and requires approval before retrieval. | **1** |
| FR-014 | An admin can retire/supersede a document; it leaves default retrieval and is audited. | 1 |
| FR-015 | An admin can purge a document and all derived chunks/indexes (right-to-be-forgotten). | 1 |
| FR-016 | A Request-level Grounding Report summarizes claims grounded/flagged + sources used. | 1 |
| FR-017 | An eval harness scores retrieval quality against gold queries and runs in CI. | 1 |
| **FR-020** | **Knowledge Buckets:** a user can create/rename/delete named buckets (collections of documents). | **1** |
| **FR-021** | **A document can be tagged into one or more buckets (many-to-many); a user can add/remove a doc's bucket tags.** | **1** |
| **FR-022** | **On request submit, a user selects which bucket(s) to ground the task in; empty selection = platform knowledge only.** | **1** |
| **FR-023** | **Agent retrieval is hard-scoped to the request's selected bucket(s) — an agent cannot retrieve documents outside them (structural, not advisory). A task grounded in bucket A never returns bucket B chunks.** | **1** |
| **FR-024** | **Auto-ingested platform docs live in a system "Platform" bucket; uploaded docs go to the bucket(s) the uploader assigns.** | **1** |
| FR-018 | Per-application (project-owned) buckets + project-derived auto-selection. | 2 |
| FR-019 | Episodic memory + time-aware recall ("a discussion months ago"). | 4 |

---

## 6. Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-001 | Backend boots healthy even if Postgres/pgvector is unavailable — agents degrade to no-retrieval with a logged warning. |
| NFR-002 | Retrieval p95 latency ≤ 1.5s for a single query (excluding the agent's own LLM time). |
| NFR-003 | Storage engine is abstracted behind `VectorStore`/`KeywordStore`/`Embedder` interfaces; swapping pgvector → Qdrant is a config change, not a rewrite. |
| NFR-004 | Ingestion is restartable and idempotent. |
| NFR-005 | Retrieval audit and decision ledger are append-only / tamper-evident. |
| NFR-006 | All KB state goes through a `KnowledgeStore`; routes never touch the DB directly (existing convention). |
| NFR-007 | Embedding provider is swappable (local fastembed ↔ hosted) behind one interface. |
| NFR-008 | The KB adds exactly one new infra service in Phase 1 (Postgres); no Redis until ingestion volume justifies async workers. |

---

## 7. UX / Surface Specifications

Built on the existing React 19 + Zustand + TanStack frontend (same patterns as the PAM `ModelSelector`/`useModelsStore` work).

> **FROZEN design (2026-06-02):** the upload / tagging / buckets / ground-a-task UI is locked to **`docs/mockups/kb-buckets-mockup.html` v1.0** — 4 screens, futuristic/neon aesthetic. Build the React UI to match it; do **not** redesign without owner approval (same freeze status as the Story Board Kanban). KB-05 builds Screen 01; KB-10 builds Screens 02–04.

### 7.1 Upload (FROZEN Screen 01)
- Drag-drop dropzone (md/txt/pdf/docx/code), per-file **ingestion pipeline** shown as stage chips: `uploaded ▸ parsed ▸ chunked·N ▸ embedding ▸ pending`, animated progress, rejected unsupported types.
- **Bucket-assignment chips** above the queue — files are tagged into the selected bucket(s) on upload.

### 7.2 Tag & Bucket (FROZEN Screen 02)
- Document card with **PII-scan-clean** badge; **many-to-many** bucket tagging via a toggle picker grid; "+ New bucket" inline create-and-assign.

### 7.3 Buckets management (FROZEN Screen 03)
- Card grid of buckets with docs/chunks/tasks stats; the system **"Platform"** bucket (auto-ingested corpus, SYSTEM tag) + user buckets; "+ New bucket" card. CRUD: create / rename / delete.

### 7.4 Ground a Task (FROZEN Screen 04, on Command Center submit)
- 🔒 **bucket selector** on the request composer; a **retrieval-scope visual** where selected buckets glow "grounded" and unselected ones are greyed/blocked — the hard-isolation guarantee made visible. Empty selection = platform knowledge only.

### 7.5 Knowledge admin screen (`/knowledge`, admin-only)
- **Document table:** title · source type · status · chunks · last-indexed · sensitivity · approved-by.
- **Actions:** Reindex platform corpus · Add documents (upload) · Approve/Reject (pending) · Retire · Purge.
- **Test-retrieval box:** type a query, see ranked results + scores — lets a curator sanity-check what agents will get.

### 7.6 Citations on agent output (Request Detail)
- Footnote markers on claims; click → **source drawer** (chunk text + doc link + provenance).

### 7.7 "Why" reasoning trail (Request Detail / Story Board)
- Collapsible panel: Searched · Retrieved · Used · Flagged · Concluded.

### 7.8 Grounding Report card (completed research/content requests)
- Claims grounded/flagged counts · sources used · grounding badge.

### 7.9 Live activity
- Reuse the existing Team Status / Active Agents feed to show a "retrieving knowledge" state while an agent searches.

---

## 8. Reasoning & Traceability Model (deep dive)

The traceability guarantee rests on three persisted, linked records:

| Record | Captures | Mutability | Surfaces as |
|---|---|---|---|
| **Retrieval audit** | Every query + returned chunks + cited chunks per agent run | Append-only | "Searched / Retrieved / Used" in the Why panel |
| **Decision ledger** | The conclusion + the chunk IDs + memory IDs that justified it + an inputs digest | Append-only, tamper-evident | "Concluded" in the Why panel; the audit trail |
| **Citation links** | Claim ↔ chunk ↔ document ↔ source | Immutable pointers | Inline footnotes + source drawer |

**The back-track invariant:** from any agent statement, a user can resolve `claim → chunk → document → original source → ingestion metadata (when, version, approved-by)`. Nothing an agent asserts as fact is unattributable; anything it could not attribute is explicitly flagged.

**Why this matters for trust:** an admin reviewing a research report three months later can answer "what did the agent know, what did it use, and why did it conclude this" without rerunning anything — the reasoning is data, not a transient prompt.

---

## 9. Architecture Summary

Full detail in `docs/knowledge-base-design.md`. In brief:

- **Pattern:** Agentic RAG (retrieval as a tool in the existing ReAct loop) over a Modular hybrid-retrieval pipeline, with per-agent forced/agentic mode.
- **Stores (Phase 1):** `kb_documents`, `kb_chunks`, `kb_retrieval_audit`, `decision_ledger` in **Postgres**; vectors in **pgvector**; keyword in **Postgres FTS**.
- **Embeddings:** local fastembed (`bge-small-en-v1.5`, ONNX, in-process), swappable; no reranker in Phase 1 (RRF fusion only).
- **Namespace:** `kb_platform` only in Phase 1; `kb_project_<id>` isolation is Phase 2.
- **Tools:** `knowledge_search`, `knowledge_get` (granted via `tools.yaml`).
- **Reuses:** tool-registry permissions, `EventEmitter` hooks, the AET-13 review-gate pattern (for upload approval), `TokenTracker` cost attribution, kwarg-threading (PAM-06 pattern) for namespace injection.

---

## 10. Data & Privacy Considerations

- Phase 1 corpus is the platform's own repo docs — low sensitivity, no customer data.
- PII scanning (Presidio) runs on every ingest; flagged content is tagged and gets stricter handling.
- Purge cascades to vector + keyword indexes and is audited.
- Embeddings are computed **locally** (fastembed ONNX, in-process) — docs never leave the host for embedding. A hosted embedder remains swappable behind the same interface if scale demands it (NFR-007).

---

## 11. Cost Model

- **Embedding cost:** **$0** — embeddings run locally (fastembed ONNX, CPU, in-process). No per-token or per-call charge; only the one-time ~130 MB model download.
- **Token savings:** replacing the 50 KB lessons dump with top-K retrieval reduces per-code-agent input tokens materially; measured against baseline.
- **PAM synergy:** retrieval-heavy agents (research) can be assigned a cheaper model to offset multiplied retrieval calls.
- **Retrieval budget:** per-Request cap on `knowledge_search` calls prevents runaway cost.

---

## 12. Phasing & Rollout

### Phase 1 — Platform KB (this PRD's build scope)

| ID | Task | Effort |
|---|---|---|
| KB-01 | Postgres + pgvector compose service + config/secret + deps (`psycopg`, `pgvector`, `fastembed`) | M |
| KB-02 | `Embedder` / `VectorStore` / `KeywordStore` interfaces + pgvector/FTS/local-fastembed impls; soft-fail if Postgres down | M |
| KB-03 | KB schema (`kb_documents`, `kb_chunks`, `kb_retrieval_audit`, `decision_ledger`) via Alembic + `KnowledgeStore` CRUD | M |
| KB-04 | Structure-aware chunker (markdown + code via tree-sitter; plaintext fallback) | M |
| KB-05 | Ingestion pipeline (loader → chunk → hash-dedup → PII scan → embed → index), idempotent, synchronous | L |
| KB-06 | Platform-corpus ingest: management command + admin reindex endpoint | M |
| KB-07 | Retrieval pipeline (hybrid → RRF fuse → [optional rerank] → top-K + citations + audit write) | L |
| KB-08 | Tools `knowledge_search` + `knowledge_get`; grant in `tools.yaml` (`kb_platform`) | M |
| KB-09 | Agent integration: YAML `retrieval` config + forced/hybrid pre-injection; replace wholesale lessons dump | M |
| KB-10 | Admin API + Knowledge screen (list/search/reindex; approve/retire/purge); citations + "Why" panel + Grounding Report | L |
| KB-11 | Tests: ingest idempotency, hybrid retrieval, lessons-replacement parity, audit/ledger written, purge cascade | M |
| KB-12 | Eval harness (gold queries, recall@k/MRR) in CI; baseline recorded | M |

### Later phases (design doc §20)
- **Phase 2:** Per-application KB + hard scope isolation; full upload/connect UX.
- **Phase 3:** Decision ledger depth; broader agent wiring.
- **Phase 4:** Episodic memory + time-aware recall; consolidation job.
- **Phase 5:** Curator role + promotion gate; retention automation; feedback-driven reranking; scale-out.

---

## 13. Verification / Acceptance Criteria

1. `make dev` starts the platform + a `postgres` service; backend healthy even with Postgres down.
2. Reindex ingests the platform corpus into `kb_platform`; re-run is a no-op.
3. `knowledge_search("how does the supervisor deploy")` returns ranked, cited chunks from the real docs.
4. A code agent's prompt contains *relevant* retrieved lessons, not the wholesale dump (parity test confirms critical lessons still reach it).
5. A research output shows inline citations; clicking one opens the source chunk + document + provenance.
6. The "Why" panel reconstructs Searched/Retrieved/Used/Flagged/Concluded for a run.
7. A claim with no supporting source is flagged, not asserted.
8. Purge removes a document + all chunks/indexes; audit records it.
9. Eval harness passes the recall@k threshold and gates CI.

---

## 14. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| New infra service (Postgres) breaks the "two-container" simplicity | One service only; soft-fail boot; documented in deploy playbook |
| Retrieval quality is poor → agents grounded in noise | Hybrid + rerank; eval harness gates CI; test-retrieval box for curators |
| Lessons-replacement regresses agent behavior | Parity test asserting critical lessons still reach code agents; ability to fall back to forced-all if needed |
| Embedding cost / latency | Small Phase 1 corpus; query cache; retrieval budget; cheap-model assignment via PAM |
| Cloud embedding raises a privacy concern | Resolved (KB-13a): embeddings run **locally** (fastembed ONNX) — docs never leave the host; hosted embedder still swappable (NFR-007) |
| Ingestion blocks the request path (synchronous) | Runs via BackgroundTasks; admin-triggered, not on the hot path; async workers added in a later phase if volume grows |

---

## 15. Out of Scope (Phase 1)

- Per-application grounding / isolation (Phase 2).
- Episodic memory and time-aware recall (Phase 4).
- External source connectors (Confluence/Notion/etc.).
- GraphRAG, self-editing memory.
- Async ingestion workers (Redis/arq).
- Migrating the platform's *existing* state off SQLite (separate effort; KB uses Postgres independently).

---

## 16. Open Decisions

| ID | Decision | Status |
|---|---|---|
| D-DATASTORE | Postgres + pgvector from day one | **LOCKED** |
| D-EMBED | Local fastembed (ONNX) embeddings, swappable — no third-party key/cost | **LOCKED — revised to local (KB-13a, 2026-06-03)** |
| D-ASYNC | Synchronous ingestion in Phase 1; defer Redis/arq | **LOCKED** |
| D-UPLOAD | Document upload in Phase 1 | **LOCKED — in Phase 1** (2026-06-02) |
| D-BUCKET-BIND | How tasks bind to buckets | **LOCKED — per-request selection** (2026-06-02) |
| D-BUCKET-CARD | Doc → bucket cardinality | **LOCKED — many-to-many tags** (2026-06-02) |
| D-RERANK | Cross-encoder rerank vs. RRF-only | **REVISED — RRF-only in Phase 1** (KB-13a; optional local reranker later) |
| D-PLATFORM-STATE | When to migrate existing platform state SQLite → Postgres | OPEN — separate effort, not blocking |

---

## 17. Appendix

### 17.1 Glossary
| Term | Meaning |
|---|---|
| `kb_platform` | The namespace holding the platform's own knowledge (Phase 1 scope) |
| Chunk | A retrievable unit of a document |
| Hybrid retrieval | Vector (semantic) + keyword (lexical) fused then reranked |
| Citation-or-flag | An agent cites a source for a claim or explicitly flags it as ungroundable |
| Retrieval audit | Append-only record of what was searched/returned/cited |
| Decision ledger | Append-only record of why a conclusion was reached |
| Grounding Report | Request-level summary of claims grounded vs. flagged + sources used |
| Supersession | Marking an old document version stale when a new one replaces it |

### 17.2 Relationship to existing platform mechanisms
| New concept | Reuses / generalizes |
|---|---|
| Tool grants for `knowledge_search` | `config/tools.yaml` `available_to` |
| Upload approval | Self-learning pending-review gate (AET-13) |
| Decision ledger | `agent_traces` (extended with provenance) |
| Namespace kwarg threading | `project_root` / model-kwarg threading (PAM-06) |
| Cost attribution | `TokenTracker` catalog pricing (PAM-15) |
| Admin screen patterns | Team Status `ModelSelector` / `useModelsStore` (PAM-17/18/19) |

### 17.3 Prior art
A platform-generated research report exists at `docs/research/REQ-4262D7-project-knowledge-base-setup/` covering *human* team knowledge bases (Confluence/Notion). Its one transferable insight — *a named curator + task-organized structure beats tooling; content decay is the top failure mode* — is reflected in this PRD's curation (§4.4) and supersession design. Its tooling recommendations (Confluence/Notion) are not applicable to this agent-consumed RAG layer.
```
