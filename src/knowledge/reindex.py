"""Platform-corpus reindex (KB-06).

Feeds the platform's own docs through the KB-05 ingestion pipeline into the
system **"Platform"** bucket. Idempotent (the pipeline dedups on content
hash), so it's safe to run repeatedly — a re-run only ingests what changed.

Two entry points:
  - ``reindex_platform(...)`` — the callable the admin reindex endpoint
    (KB-10) and the CLI both use.
  - ``python -m src.knowledge.reindex`` — CLI: builds the subsystem from
    config and runs the reindex, printing a summary. Needs reachable Postgres
    + the local fastembed model (soft-fails with a clear message otherwise).

Platform docs are **auto-approved** (status ``approved``) — they're the
team's own trusted repo, unlike user uploads which await curator review.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from src.knowledge.corpus import platform_corpus_files, relative_uri

logger = structlog.get_logger()


@dataclass
class ReindexSummary:
    bucket_id: str
    namespace: str
    files_seen: int = 0
    ingested: int = 0          # newly embedded + stored
    skipped: int = 0           # dedup hits (unchanged content)
    chunks: int = 0
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "bucket_id": self.bucket_id, "namespace": self.namespace,
            "files_seen": self.files_seen, "ingested": self.ingested,
            "skipped": self.skipped, "chunks": self.chunks,
            "failures": self.failures,
        }


async def reindex_platform(
    *,
    pipeline: Any,            # IngestionPipeline
    store: Any,              # KnowledgeStore
    root: str | Path,
    namespace: str = "kb_platform",
    include_globs: tuple[str, ...] | list[str] | None = None,
    auto_approve: bool = True,
) -> ReindexSummary:
    """Ingest the platform corpus into the system Platform bucket. Returns a
    summary; never raises on a single-file error (records it in ``failures``)."""
    bucket = await store.get_or_create_system_bucket("Platform")
    summary = ReindexSummary(bucket_id=bucket.bucket_id, namespace=namespace)

    files = platform_corpus_files(root, include_globs)
    summary.files_seen = len(files)

    for path in files:
        uri = relative_uri(root, path)
        try:
            data = path.read_bytes()
            res = await pipeline.ingest_file(
                filename=uri, data=data, namespace=namespace,
                bucket_ids=[bucket.bucket_id], title=path.name, uri=uri,
            )
            if res.skipped:
                summary.skipped += 1
            else:
                summary.ingested += 1
                summary.chunks += res.chunks
                if auto_approve:
                    await store.set_document_status(
                        res.doc_id, "approved", curated_by="system"
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning("kb_reindex_file_failed", file=uri, error=str(e))
            summary.failures.append(f"{uri}: {e}")

    logger.info(
        "kb_reindex_complete",
        namespace=namespace, files=summary.files_seen,
        ingested=summary.ingested, skipped=summary.skipped, chunks=summary.chunks,
        failures=len(summary.failures),
    )
    return summary


# ── CLI ──────────────────────────────────────────────────────────────────


async def _cli() -> int:
    from src.config.loader import ConfigLoader
    from src.knowledge.subsystem import build_knowledge_subsystem

    config = ConfigLoader()
    config.load_all()
    sub = await build_knowledge_subsystem(config)
    if not sub.available:
        print(f"❌ Knowledge subsystem unavailable: {sub.reason}")
        print("   (needs reachable Postgres + the local fastembed model to load)")
        return 1

    root = Path("/app") if Path("/app/docs").is_dir() else Path.cwd()
    print(f"▶ Reindexing platform corpus from {root} → {sub.settings.platform_namespace}")
    summary = await reindex_platform(
        pipeline=sub.pipeline, store=sub.knowledge_store,
        root=root, namespace=sub.settings.platform_namespace,
    )
    print(
        f"✓ files={summary.files_seen} ingested={summary.ingested} "
        f"skipped={summary.skipped} chunks={summary.chunks} "
        f"failures={len(summary.failures)}"
    )
    for f in summary.failures:
        print(f"   ⚠ {f}")
    await sub.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_cli()))
