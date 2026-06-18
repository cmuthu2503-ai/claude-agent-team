# Product Requirements Document (PRD)
# Personal Knowledge Library — External Ingestion & Human Search (KB-PL)

---

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 0.1 (Draft for review) |
| Created Date | 2026-06-18 |
| Product Owner | Chandramouli |
| Engineering Design | extends `docs/knowledge-base-design.md` + `docs/prd-knowledge-base.md` |
| Relationship | **Additive layer** over the existing KB subsystem (KB-01..33). No engine rewrite. |
| Builds on (verified in code) | `IngestionPipeline.ingest_text()` · `Retriever` (hybrid+RRF) · Knowledge Buckets · `WebScrapeTool`/`WebSearchTool` (firecrawl) · `/api/v1/knowledge/*` routes |

---

## 1. Why this PRD exists

The platform already ships a **production-grade agentic RAG memory subsystem**: local fastembed (ONNX, $0, no key) embeddings, pgvector + Postgres-FTS hybrid retrieval with RRF fusion, structure-aware chunking, Knowledge Buckets with hard-scoped grounding, idempotent ingestion, curation/supersession/retention, decision-ledger provenance, and a CI-gated eval harness. ~5,300 lines, ~30 KB test files.

**But it was built for one consumer: the agent team grounding its own app-building work.** It is *not yet usable* as the Product Owner's **personal research library**. Two front doors are missing, and one default fights the solo workflow:

| # | Gap | Evidence in code |
|---|-----|------------------|
| **A** | No way to ingest an **external web article by URL** into the KB | `POST /documents` takes `UploadFile` only; `ingest_text()` exists but has no URL door; `WebScrapeTool` exists but feeds agents, not the KB |
| **B** | No way to ingest **pasted text** (the ToS-safe LinkedIn path) | same — upload-only HTTP surface |
| **C** | No **human-facing search endpoint** that returns ranked results + source links | `knowledge_search` is agent-tool-only (`KbScope` injected by executor); no `POST /knowledge/search` for a person |
| **D** | Every doc lands `pending` until a **curator** approves — friction for a solo user who *is* the curator | `ingest_*` hardcodes `status='pending'`; approval is a separate curator-gated call |

This PRD fills exactly that 20%. It changes **no** retrieval math, **no** embedding, **no** chunking, **no** isolation guarantee.

---

## 2. Product Vision & Target User

**Vision.** Turn the existing agent-grounding KB into a **personal "second brain"**: the user feeds in knowledge articles from the web and LinkedIn; the platform ingests, chunks, embeds, classifies, and links them; and at any later time the user searches a topic in plain language and gets back **ranked, relevant material with the original source links** — even when the query words never appear verbatim in the saved articles.

**Primary user (Phase 1):** solo Product Owner (admin role). Single curator, single consumer. Multi-user sharing is explicitly out of scope (the existing RBAC already supports it later, untouched).

**The north-star use case (acceptance anchor):**
> The user has fed in 40 articles over a month. They search **"Agentic AI Architecture in Banking Industry"**. They get back a ranked list of the most semantically relevant saved articles — including one titled *"Multi-agent orchestration for loan underwriting"* that never contains the phrase "Agentic AI Architecture" — each with its **title, a matched snippet, a relevance score, and a clickable source URL**.

---

## 3. Goals & Non-Goals

### 3.1 Goals
1. Ingest an external article by **URL** with one action — fetch → clean → run the *existing* ingest pipeline.
2. Ingest **pasted text** (LinkedIn-safe) with one action — same pipeline, manual title/source.
3. Give the user a **search endpoint + simple UI** that returns ranked results **with source links** (the "give me references" requirement).
4. Remove curator friction for the solo workflow via an **auto-approve** ingest option (config-gated; default stays `pending` for the team build).
5. Organize the library with **Topic/Domain buckets** (reusing the existing Knowledge Buckets primitive verbatim).

### 3.2 Non-Goals (this phase)
- **No new retrieval/embedding/chunking engine.** Reuse `Retriever`, `FastEmbedEmbedder`, `chunk_document` as-is.
- **No autonomous web discovery / crawling.** The agent finding new articles on its own (the "improve the platform" stretch goal) is deferred to a later phase; this phase is user-fed ingestion + search.
- **No LinkedIn scraping automation.** ToS-hostile and account-risky; the paste-text path is the deliberate, robust substitute.
- **No multi-user sharing/permissions work.** Existing RBAC is left exactly as-is.
- **No second datastore (no Obsidian/Notion backbone).** The existing pgvector KB is the single source of truth. (Optional one-way Markdown export to a vault is a later nice-to-have, never a second brain.)

---

## 4. Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| **PL-001** | `POST /api/v1/knowledge/ingest-url` — accepts `{url, bucket_ids[], title?, auto_approve?}`; fetches the page via the existing `WebScrapeTool` (firecrawl), extracts clean text/markdown, then calls `IngestionPipeline.ingest_text()` with `source_type="web"`, `uri=<url>`. Idempotent via the existing content-hash dedup. | P0 |
| **PL-002** | `POST /api/v1/knowledge/ingest-text` — accepts `{text, title, bucket_ids[], source_url?, auto_approve?}`; the LinkedIn/manual path. Calls `ingest_text()` with `source_type="paste"`, `uri=source_url`. | P0 |
| **PL-003** | `POST /api/v1/knowledge/search` — human search. Accepts `{query, bucket_ids?[], top_k?}`; runs the existing `Retriever`; returns ranked chunks each with `{title, snippet, score, uri (source link), doc_id, bucket_ids}`. Writes a retrieval-audit row like the agent path. | P0 |
| **PL-004** | An **auto-approve** option on ingest (PL-001/002): when enabled, the doc is created and immediately set `approved` (skips the curator gate) so it's instantly searchable. Config default `knowledge_base.personal_auto_approve` = false (team-safe); the solo deployment sets it true. | P0 |
| **PL-005** | A **Topic/Domain bucket** convention: a small set of user-defined buckets (e.g. `Agentic AI`, `Banking`, `Architecture`). Pure reuse of existing bucket CRUD — no new storage. Ingest paths accept `bucket_ids`; many-to-many tagging already supported. | P0 |
| **PL-006** | `source_type` vocabulary extended to include `web` and `paste` so the chunker's kind-detection and the UI can distinguish externally-sourced articles. (Chunker treats them as prose — no code path change.) | P0 |
| **PL-007** | URL ingest **captures lightweight metadata** when available (page title, author/byline, fetched-at) into the existing `kb_chunks.metadata` JSONB / doc title — no schema change. | P1 |
| **PL-008** | A minimal **Personal Library UI**: an "Add by URL" + "Paste text" composer, a search box returning ranked cards with source links, and bucket filter chips. Reuses the existing React/Zustand `/knowledge` surface and `knowledge.ts` store. | P1 |
| **PL-009** | Search results are **de-duplicated to the document level** (best chunk per doc) so the user sees N distinct articles, not N chunks of the same article — with a "more matches in this doc" affordance. | P1 |
| **PL-010** | **Duplicate-URL awareness**: re-ingesting the same URL is a no-op (existing hash dedup) and the API response says `skipped=true` so the UI can say "already in your library." | P1 |
| **PL-011** | A `hermes`/CLI or `make` convenience: `ingest-url <url> --bucket <name>` for fast capture from the terminal without the UI. | P2 |

---

## 5. Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-PL-001 | **Zero engine changes.** Retrieval, embedding, chunking, isolation untouched. New code only adds: 2 ingest endpoints, 1 search endpoint, 1 URL→text adapter, optional auto-approve branch, UI. |
| NFR-PL-002 | **Soft-fail preserved.** New endpoints follow the existing `_require_kb` / `meta.kb_available` posture — 503 on write when KB down, empty payload on read. |
| NFR-PL-003 | **Fully local at rest.** Embeddings + storage stay on-host (Mac Studio). The only outbound call is the URL fetch itself (firecrawl) — content is then stored and embedded locally. (Document this clearly; it's the one network egress.) |
| NFR-PL-004 | **Idempotent ingest** unchanged — re-adding a URL/text is a content-hash no-op. |
| NFR-PL-005 | **Audited search** — human searches write `kb_retrieval_audit` rows exactly like agent searches, so the "Why/history" surfaces keep working. |
| NFR-PL-006 | **RBAC reuse** — endpoints gate on the existing roles; solo deploy runs as admin. No new permission model. |
| NFR-PL-007 | URL-fetch egress is **opt-in & logged**; if firecrawl is unconfigured, `ingest-url` returns a clear error and `ingest-text` (paste) still works fully offline. |

---

## 6. Feature List (build breakdown)

| ID | Feature | Touches | Effort |
|----|---------|---------|--------|
| **PL-F1** | URL→text adapter: wrap existing `WebScrapeTool` to return `(clean_text, metadata)` for ingestion | new `src/knowledge/web_ingest.py` | S |
| **PL-F2** | `POST /knowledge/ingest-url` endpoint | `src/api/routes/knowledge.py` | S |
| **PL-F3** | `POST /knowledge/ingest-text` endpoint | `src/api/routes/knowledge.py` | S |
| **PL-F4** | Auto-approve branch in ingest (config-gated) | `ingest.py` or route-level status set; `settings.py` | S |
| **PL-F5** | `POST /knowledge/search` human search endpoint (wraps `Retriever`, doc-level dedup, source links) | `src/api/routes/knowledge.py` | M |
| **PL-F6** | `source_type` vocab: add `web`, `paste`; chunker prose-path mapping | `loaders.py`/`chunker.py` constants | S |
| **PL-F7** | Topic/Domain bucket seeding helper (optional convenience) | small script / existing bucket CRUD | S |
| **PL-F8** | Personal Library UI: add-by-URL, paste, search-with-links, bucket chips | `frontend/src/pages/KnowledgeBase.tsx`, `stores/knowledge.ts`, `api/` | M |
| **PL-F9** | CLI/`make` capture convenience | `scripts/` + `Makefile` | S |
| **PL-F10** | Tests: URL ingest (mocked fetch), text ingest, search ranking + source-link return, auto-approve, dup-URL no-op | `tests/test_kb_personal_*.py` | M |

---

## 7. Architecture Fit (how it rides existing rails)

```
USER
 │  (A) drop URL              (B) paste text            (C) search topic
 ▼                            ▼                          ▼
POST /knowledge/ingest-url    POST /ingest-text          POST /knowledge/search
 │  WebScrapeTool (firecrawl)  │                          │
 │   → clean text + meta       │                          │
 └──────────┬──────────────────┘                          │
            ▼                                              ▼
   IngestionPipeline.ingest_text()   [UNCHANGED]    Retriever.retrieve()  [UNCHANGED]
   hash-dedup → chunk → PII → embed → write          hybrid (vector+FTS) → RRF → top-K
            │                                              │
   (PL-004) auto_approve? → status=approved        doc-level dedup + source links
            ▼                                              ▼
   pgvector + FTS + buckets   [UNCHANGED STORE]     ranked cards w/ uri  →  USER
```

Everything below the dashed line already exists and is tested. This PRD adds only the three doors at the top and one status branch.

---

## 8. Acceptance Criteria

1. `POST /knowledge/ingest-url` with a real article URL stores a `web` doc, returns `doc_id` + `chunks > 0`; re-posting the same URL returns `skipped=true`.
2. `POST /knowledge/ingest-text` with pasted LinkedIn text + a title stores a `paste` doc, searchable immediately when `auto_approve=true`.
3. `POST /knowledge/search` for the north-star query returns ≥1 relevant doc whose title does **not** contain the query phrase (semantic match proven), each result carrying a non-null `uri` source link.
4. With `personal_auto_approve=true`, an ingested article is retrievable without a separate approve call; with it false, the existing `pending` gate is preserved (team parity).
5. Search results are doc-level de-duplicated (no two cards for the same `doc_id`).
6. All new endpoints soft-fail (503 on write / empty on read) when the KB subsystem is down.
7. New tests pass and the existing KB eval harness floor is unchanged (no regression).

---

## 9. Phasing

- **Phase 1 (this PRD):** PL-F1..F6, PL-F10 — the three doors + auto-approve + tests. Ships the full personal capture+search loop via API. *(P0)*
- **Phase 2:** PL-F8 UI + PL-F7 topic buckets + PL-F9 CLI. *(P1/P2)*
- **Later (separate PRD):** autonomous web *discovery* (agent fills topic gaps via `WebSearchTool`), and optional one-way Markdown/Obsidian export view.

---

## 10. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Firecrawl egress conflicts with "fully local" intent | Document as the single outbound call; paste path is 100% offline; consider a local Trafilatura fallback adapter behind the same `web_ingest` interface in Phase 2 |
| Auto-approve pollutes the team KB if misused | Config-gated, default false; only the solo deployment enables it; namespaced to platform/personal bucket |
| LinkedIn scraping temptation | Explicitly out of scope; paste path is the supported route |
| Web pages chunk poorly (nav/boilerplate) | Rely on firecrawl's markdown extraction; metadata capture (PL-007) keeps title/byline clean |
| Search returns chunk-spam from one big article | Doc-level dedup (PL-009) |

---

## 11. Open Decisions (need owner input)

| ID | Decision | Recommendation |
|----|----------|----------------|
| Q-APPROVE | Auto-approve default for the solo deploy | **Enable** (`personal_auto_approve=true`) — you are your own curator |
| Q-FETCH | Firecrawl vs. local Trafilatura for URL fetch | Start with **existing firecrawl** (already wired); add local fallback in Phase 2 for full-local purity |
| Q-NS | Put personal articles in the existing `kb_platform` namespace under topic buckets, or a dedicated `kb_personal` namespace | **Dedicated `kb_personal` namespace** keeps your library cleanly separate from platform craft docs |
| Q-UI | Build the UI now or run API-first | **API-first (Phase 1), UI in Phase 2** — fastest path to a working loop |
