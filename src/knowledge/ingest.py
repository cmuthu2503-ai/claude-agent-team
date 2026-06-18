"""Ingestion pipeline (KB-05).

The engine that turns a document into retrievable, bucket-tagged, embedded
chunks. One synchronous flow (Phase 1 — no Redis/arq):

    raw text → content-hash → DEDUP → chunk → PII scan → embed → write
             → assign bucket(s) (syncs kb_chunks.bucket_ids)

**Idempotent:** keyed on the sha256 of the text within a namespace. Re-ingesting
identical content is a no-op that returns the existing doc (``skipped=True``) —
no re-embedding, no duplicate chunks. KB-06 (platform reindex) relies on this.

Documents land ``status='pending'`` (curator-approved before retrieval, per the
frozen upload flow). PII-flagged docs get ``sensitivity='pii'``.

Construction is dependency-injected (store + embedder + chunker config) so the
pipeline is testable with a fake embedder — no model download needed for tests.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

import structlog

from src.knowledge.chunker import chunk_document
from src.knowledge.interfaces import Embedder
from src.knowledge.loaders import load_text
from src.knowledge.models import KbChunk, KbDocument
from src.knowledge.pii import PiiScanner
from src.knowledge.store import KnowledgeStore

logger = structlog.get_logger()


@dataclass
class IngestResult:
    doc_id: str
    status: str            # pending | skipped
    skipped: bool          # True when content already existed (dedup hit)
    chunks: int
    sensitivity: str
    pii_findings: list[str]


class IngestionPipeline:
    def __init__(
        self,
        store: KnowledgeStore,
        embedder: Embedder,
        *,
        max_tokens: int = 512,
        overlap_tokens: int = 64,
        pii: PiiScanner | None = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._max_tokens = max_tokens
        self._overlap = overlap_tokens
        self._pii = pii or PiiScanner()

    # ── Public entry points ─────────────────────────────────────────────

    async def ingest_file(
        self,
        *,
        filename: str,
        data: bytes,
        namespace: str,
        bucket_ids: list[str] | None = None,
        title: str | None = None,
        created_by: str = "system",
        uri: str | None = None,
    ) -> IngestResult:
        """Load an uploaded file → text, then ingest. Raises
        ``UnsupportedFileTypeError`` for bad types (loader backstop)."""
        text, source_type = load_text(filename, data)
        return await self.ingest_text(
            text=text, title=title or filename, source_type=source_type,
            namespace=namespace, bucket_ids=bucket_ids, created_by=created_by,
            uri=uri or filename,
        )

    async def ingest_text(
        self,
        *,
        text: str,
        title: str,
        source_type: str,
        namespace: str,
        bucket_ids: list[str] | None = None,
        uri: str | None = None,
        created_by: str = "system",
        sensitivity: str = "normal",
        project_id: str | None = None,
        auto_approve: bool = False,
    ) -> IngestResult:
        bucket_ids = bucket_ids or []
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        # 1. DEDUP — identical content in this namespace is a no-op.
        existing = await self._store.find_document_by_hash(namespace, content_hash)
        if existing is not None:
            # Still (re)apply bucket tags — the same doc may need new buckets.
            if bucket_ids:
                await self._store.assign_document_to_buckets(existing.doc_id, bucket_ids)
            logger.info("kb_ingest_skipped_dedup", doc_id=existing.doc_id, namespace=namespace)
            return IngestResult(
                doc_id=existing.doc_id, status="skipped", skipped=True,
                chunks=0, sensitivity=existing.sensitivity, pii_findings=[],
            )

        # 2. PII scan → sensitivity.
        pii = self._pii.scan(text)
        if pii.has_pii:
            sensitivity = "pii"

        # 3. Chunk.
        chunks = chunk_document(
            text, source_type=source_type, uri=uri,
            max_tokens=self._max_tokens, overlap_tokens=self._overlap,
        )
        status = "approved" if auto_approve else "pending"
        if not chunks:
            # Empty/whitespace doc — record it but with no chunks.
            doc_id = await self._write_document(
                namespace, source_type, title, uri, content_hash,
                sensitivity, created_by, project_id, status,
            )
            if bucket_ids:
                await self._store.set_document_buckets(doc_id, bucket_ids)
            return IngestResult(doc_id=doc_id, status=status, skipped=False,
                                chunks=0, sensitivity=sensitivity, pii_findings=pii.findings)

        # 4. Embed (batch).
        emb = await self._embedder.embed_documents([c.text for c in chunks])
        vectors = emb.vectors

        # 5. Write document + chunks.
        doc_id = await self._write_document(
            namespace, source_type, title, uri, content_hash,
            sensitivity, created_by, project_id, status,
        )
        kb_chunks = [
            KbChunk(
                chunk_id=f"chk-{uuid.uuid4().hex[:16]}",
                doc_id=doc_id, namespace=namespace, ordinal=c.ordinal,
                text=c.text, embedding=vectors[i] if i < len(vectors) else None,
                token_count=c.token_estimate, metadata=c.metadata,
            )
            for i, c in enumerate(chunks)
        ]
        await self._store.insert_chunks(kb_chunks)

        # 6. Assign buckets (syncs kb_chunks.bucket_ids — the grounding filter).
        if bucket_ids:
            await self._store.set_document_buckets(doc_id, bucket_ids)

        logger.info(
            "kb_ingest_complete", doc_id=doc_id, namespace=namespace,
            chunks=len(kb_chunks), buckets=len(bucket_ids), sensitivity=sensitivity,
            status=status,
        )
        return IngestResult(
            doc_id=doc_id, status=status, skipped=False,
            chunks=len(kb_chunks), sensitivity=sensitivity, pii_findings=pii.findings,
        )

    # ── Internal ────────────────────────────────────────────────────────

    async def _write_document(
        self, namespace: str, source_type: str, title: str, uri: str | None,
        content_hash: str, sensitivity: str, created_by: str, project_id: str | None,
        status: str = "pending",
    ) -> str:
        doc = KbDocument(
            doc_id=f"doc-{uuid.uuid4().hex[:16]}",
            namespace=namespace, source_type=source_type, title=title, uri=uri,
            content_hash=content_hash, sensitivity=sensitivity, status=status,
            curated_by=created_by if status == "approved" else None,
            project_id=project_id,
        )
        await self._store.create_document(doc)
        return doc.doc_id

    def _digest(self, text: str) -> str:  # exposed for tests / callers
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = ["IngestionPipeline", "IngestResult"]
