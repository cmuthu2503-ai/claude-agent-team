"""KB-12 — retrieval eval harness (gold-query set → recall@k / MRR).

A small, dependency-light evaluation loop that measures how well the
bucket-scoped retriever surfaces the *right* documents for a curated set of
gold queries. Two entry points:

  - ``run_eval(retriever, store, gold, ...)`` — the callable the CI test and
    the CLI share. Ingests the gold corpus into per-bucket scopes, runs each
    query (bucket-scoped), and computes **recall@k** and **MRR**.
  - ``python -m src.knowledge.eval`` — builds the real subsystem (local
    fastembed embedder + Postgres), evaluates, prints a table, and writes the
    baseline to ``docs/kb-eval-baseline.json``.

Metrics are standard IR:
  - **recall@k**  = |relevant ∩ top-k| / |relevant|, averaged over queries.
  - **MRR**       = mean of 1/(rank of first relevant hit), 0 if none in top-k.

The gold set (``tests/data/kb_eval_gold.yaml``) anchors each query on a
distinctive lexical term present in its target doc, so the eval is meaningful
and deterministic even with a non-semantic fake embedder (the Postgres FTS arm
carries the match) — that's what lets it gate CI deterministically.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


# ── Gold set model ─────────────────────────────────────────────────────────


@dataclass
class GoldDoc:
    title: str
    text: str


@dataclass
class GoldQuery:
    query: str
    relevant_titles: list[str]


@dataclass
class GoldBucket:
    name: str
    docs: list[GoldDoc]
    queries: list[GoldQuery]


def load_gold_set(path: str | Path) -> list[GoldBucket]:
    """Parse the gold YAML into buckets. Shape:

        buckets:
          <bucket name>:
            docs:    [{title, text}, ...]
            queries: [{query, relevant: [title, ...]}, ...]
    """
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out: list[GoldBucket] = []
    for name, body in (data.get("buckets") or {}).items():
        docs = [GoldDoc(title=d["title"], text=d["text"]) for d in (body.get("docs") or [])]
        queries = [
            GoldQuery(query=q["query"], relevant_titles=list(q.get("relevant") or []))
            for q in (body.get("queries") or [])
        ]
        out.append(GoldBucket(name=name, docs=docs, queries=queries))
    return out


# ── Metrics (pure) ─────────────────────────────────────────────────────────


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """|relevant ∩ top-k| / |relevant|. Returns 0.0 when nothing is relevant
    (caller should skip empty-relevant queries; guarded here for safety)."""
    if not relevant:
        return 0.0
    top = set(retrieved[:k])
    return len(top & relevant) / len(relevant)


def reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float:
    """1/(rank of first relevant hit), 1-indexed; 0.0 if none retrieved."""
    for i, rid in enumerate(retrieved):
        if rid in relevant:
            return 1.0 / (i + 1)
    return 0.0


@dataclass
class EvalResult:
    k: int
    recall_at_k: float
    mrr: float
    queries: int
    per_query: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "recall_at_k": round(self.recall_at_k, 4),
            "mrr": round(self.mrr, 4),
            "queries": self.queries,
            "per_query": self.per_query,
        }


# ── Harness ────────────────────────────────────────────────────────────────


async def run_eval(
    *,
    retriever: Any,
    store: Any,
    pipeline: Any,
    gold: list[GoldBucket],
    namespace: str | None = None,
    k: int = 5,
    cleanup: bool = True,
) -> EvalResult:
    """Ingest the gold corpus (each bucket isolated), run every gold query
    bucket-scoped, and aggregate recall@k + MRR. Returns an EvalResult.

    Each gold doc is ingested + auto-approved and tagged into its bucket; the
    title→doc_id map turns relevant titles into the ids we score against.
    """
    ns = namespace or f"kb_eval_{uuid.uuid4().hex[:8]}"
    created_docs: list[str] = []
    created_buckets: list[str] = []
    title_to_id: dict[str, str] = {}
    bucket_id_by_name: dict[str, str] = {}

    try:
        # 1. Ingest the corpus, one isolated bucket per gold bucket.
        for gb in gold:
            bucket = await store.create_bucket(f"{gb.name} {uuid.uuid4().hex[:6]}")
            created_buckets.append(bucket.bucket_id)
            bucket_id_by_name[gb.name] = bucket.bucket_id
            for doc in gb.docs:
                res = await pipeline.ingest_text(
                    text=doc.text, title=doc.title, source_type="lesson",
                    namespace=ns, bucket_ids=[bucket.bucket_id],
                )
                await store.set_document_status(res.doc_id, "approved", curated_by="eval")
                created_docs.append(res.doc_id)
                title_to_id[f"{gb.name}::{doc.title}"] = res.doc_id

        # 2. Run each query, scored within its bucket.
        recalls: list[float] = []
        rrs: list[float] = []
        per_query: list[dict[str, Any]] = []
        for gb in gold:
            bid = bucket_id_by_name[gb.name]
            for q in gb.queries:
                relevant = {
                    title_to_id[f"{gb.name}::{t}"]
                    for t in q.relevant_titles
                    if f"{gb.name}::{t}" in title_to_id
                }
                hits = await retriever.retrieve(
                    q.query, ns, bucket_ids=[bid], agent_id="kb_eval", top_k=k,
                )
                # De-dup retrieved doc ids preserving rank order (multiple
                # chunks of one doc collapse to that doc's best rank).
                seen: list[str] = []
                for h in hits:
                    if h.doc_id not in seen:
                        seen.append(h.doc_id)
                r = recall_at_k(seen, relevant, k)
                rr = reciprocal_rank(seen, relevant)
                recalls.append(r)
                rrs.append(rr)
                per_query.append({
                    "bucket": gb.name, "query": q.query,
                    "recall_at_k": round(r, 4), "rr": round(rr, 4),
                    "hit": rr > 0,
                })

        n = len(recalls)
        result = EvalResult(
            k=k,
            recall_at_k=(sum(recalls) / n) if n else 0.0,
            mrr=(sum(rrs) / n) if n else 0.0,
            queries=n,
            per_query=per_query,
        )
        logger.info(
            "kb_eval_complete", k=k, recall_at_k=round(result.recall_at_k, 4),
            mrr=round(result.mrr, 4), queries=n,
        )
        return result
    finally:
        if cleanup:
            import contextlib

            for doc_id in created_docs:
                with contextlib.suppress(Exception):
                    await store.purge_document(doc_id)
            for bid in created_buckets:
                with contextlib.suppress(Exception):
                    await store.delete_bucket(bid)


# ── CLI ────────────────────────────────────────────────────────────────────


DEFAULT_GOLD_PATH = "tests/data/kb_eval_gold.yaml"
DEFAULT_BASELINE_PATH = "docs/kb-eval-baseline.json"


async def _cli() -> int:
    import json

    from src.config.loader import ConfigLoader
    from src.knowledge.subsystem import build_knowledge_subsystem

    config = ConfigLoader()
    config.load_all()
    sub = await build_knowledge_subsystem(config)
    if not sub.available:
        print(f"❌ Knowledge subsystem unavailable: {sub.reason}")
        print("   (needs reachable Postgres + the local fastembed model to load)")
        return 1

    gold = load_gold_set(DEFAULT_GOLD_PATH)
    result = await run_eval(
        retriever=sub.retriever, store=sub.knowledge_store, pipeline=sub.pipeline,
        gold=gold, k=5,
    )
    print(f"▶ KB eval · {result.queries} queries · k={result.k}")
    print(f"  recall@{result.k} = {result.recall_at_k:.3f}")
    print(f"  MRR        = {result.mrr:.3f}")
    for pq in result.per_query:
        mark = "✓" if pq["hit"] else "✗"
        print(f"   {mark} [{pq['bucket']}] {pq['query']!r} · rr={pq['rr']}")

    Path(DEFAULT_BASELINE_PATH).write_text(
        json.dumps(
            {"embed_model": sub.settings.embed_model, **result.as_dict()}, indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"✓ baseline written → {DEFAULT_BASELINE_PATH}")
    await sub.aclose()
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(_cli()))
