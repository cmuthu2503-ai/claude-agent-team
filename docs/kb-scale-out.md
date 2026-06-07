# Knowledge Base — Async Ingestion & Scale-Out Path (KB-33)

---

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 1.0 |
| Created Date | 2026-06-05 |
| Status | Implemented (async-ingest seam) + documented validation (datastore swaps) |
| Related Docs | `docs/knowledge-base-design.md` (§11 retrieval, §13 NFRs), `docs/prd-knowledge-base.md` |
| Task | KB-33 — Phase 5 capstone |

---

## 1. Purpose

The Knowledge Base ships at low volume on a deliberately simple stack: synchronous
ingestion, **pgvector** for dense retrieval, **Postgres FTS** for sparse retrieval.
NFR-003 requires that none of those choices is load-bearing — each must be
swappable behind an interface when volume justifies it, **without touching call
sites**. This doc records (a) the async-ingestion seam that is now implemented,
and (b) the validated swap path for the two datastores.

---

## 2. Async ingestion (`IngestionDispatcher`)

Ingestion (embed → chunk → write) is the heaviest KB operation. At scale it must
not run on the request/event thread. `src/knowledge/ingest_dispatch.py` provides
the one seam every ingest call routes through:

```
dispatcher.submit(coro)   # coro = the actual ingestion work
```

Mode is config-driven (`knowledge_base.ingest_mode` → `KnowledgeSettings.ingest_mode`,
attached to the subsystem as `ingest_dispatcher`):

| Mode | Behaviour | When |
|------|-----------|------|
| `inline` (default) | `await` the work, return the `doc_id` | today's volume; what every caller historically assumed |
| `background` | `asyncio.create_task` — fire-and-forget, returns `None`; tasks tracked so they aren't GC'd; `drain()` awaits them on shutdown/tests | single-node, bursty ingest that shouldn't block event handlers |
| `queue` | hand to an external worker via an injected `enqueue` callable (Redis + arq); **degrades to `background` if no backend** so a misconfig never drops an ingest | multi-node / sustained high ingest volume |

**Soft-fail by design:** a background/queue ingestion that raises is logged, never
bubbled — it must not crash the loop that scheduled it. The artifact auto-ingest
handler (KB-14) already routes through the dispatcher, so flipping `ingest_mode`
moves *all* project-artifact ingestion off the event path with zero code change.

### Wiring the `queue` backend (Redis + arq) — the next flip

When volume justifies it:

1. Add a `redis` service to `docker-compose.yml` and an `arq` worker process
   (its own container or a host process, mirroring the deploy supervisor).
2. Define an arq task `ingest_task(ctx, payload)` that reconstructs and runs the
   ingestion coroutine against the same `KnowledgeStore` + `IngestionPipeline`.
3. Construct the dispatcher with `enqueue=<arq pool enqueue>` and set
   `ingest_mode: queue`. Call sites (`dispatcher.submit(...)`) do not change.

No interface change is required — `submit` already accepts the work; only where
it runs changes.

---

## 3. Datastore swap path (NFR-003)

Retrieval depends only on the abstract `Embedder`, `VectorStore`, and
`KeywordStore` interfaces (`src/knowledge/interfaces.py`). The `Retriever`
(`src/knowledge/retrieval.py`) never imports a concrete store — it is handed
implementations at construction by the subsystem factory. That is the whole
swap: **write a new impl of the interface, construct the subsystem with it.**

### 3.1 Dense: pgvector → Qdrant

- **Today:** `PgVectorStore` (`store_pgvector.py`) — `vector(N)` column + cosine
  `<=>` over `kb_chunks`, bucket-scoped + approved-only.
- **At scale:** a `QdrantVectorStore` implementing the same
  `VectorStore.search(namespace, query_vec, k, bucket_ids)` contract against a
  Qdrant collection (namespace → collection or payload filter; `bucket_ids` →
  payload filter; `approved` → payload filter). Construct the subsystem with it
  in `subsystem.py`; the embedding dimension is already config-driven.
- **Validated:** `tests/test_kb_scale_out.py` plugs an alternative in-memory
  `VectorStore` impl into the real `Retriever` and proves hybrid retrieval still
  works end-to-end — i.e. the seam holds and a swap needs no `Retriever` change.

### 3.2 Sparse: Postgres FTS → ParadeDB / OpenSearch

- **Today:** `PostgresFtsStore` (`store_pgfts.py`) — `to_tsvector`/`websearch_to_tsquery`
  over `kb_chunks.text`.
- **At scale:** a `ParadeDbKeywordStore` / `OpenSearchKeywordStore` implementing
  `KeywordStore.search(namespace, query, k, bucket_ids)`. ParadeDB (BM25 in
  Postgres) keeps the single-datastore operational story; OpenSearch is the
  fully-decoupled option. Either is a drop-in behind the interface.

### 3.3 What does NOT change on a swap

- The `Retriever` (RRF fusion, rerank, KB-31 feedback boost, KB-32 cache) — it
  operates on interface results, not store internals.
- `KnowledgeStore` (the relational source of truth: documents, chunks, buckets,
  audit, memory, ledger) stays on Postgres regardless; the vector/keyword stores
  are *indexes* over it.
- Agent tools, grounding/audit, RBAC — all above the store layer.

---

## 4. Status

- **Implemented:** `IngestionDispatcher` (inline/background/queue-with-fallback),
  `ingest_mode` config + settings + subsystem wiring, artifact-ingest handler
  routed through it.
- **Validated by test:** the datastore swap seam (`test_kb_scale_out.py`).
- **Deferred until volume justifies (documented above):** standing up Redis+arq
  and the Qdrant/ParadeDB implementations — each is additive and call-site-neutral.
