"""Knowledge Base API (KB-10).

The HTTP surface for the agentic Knowledge Base: document upload + lifecycle
(approve / retire / purge), bucket CRUD + tagging, platform reindex, and the
per-request Grounding Report. Backs the frozen UI (``docs/mockups/
kb-buckets-mockup.html`` — Upload / Tag & Bucket / Buckets / Ground-a-Task)
plus the citations + reasoning trail on Request detail.

Soft-fail posture (NFR-007): when the KB subsystem is unavailable (embedding
model can't load / Postgres down), **reads** return empty payloads with ``meta.kb_available
= False`` so the UI renders an "offline" state cleanly, and **writes** return
503 — nothing silently no-ops.

RBAC (inherits viewer → developer → admin):
  - viewer    : read (status, list docs, list buckets, grounding report)
  - developer : upload, approve, retire, tag, create/rename buckets
  - admin     : purge documents, delete buckets, trigger platform reindex
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from src.auth.service import get_current_user, is_kb_curator, require_role

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])

# Upload guard — mirrors the frozen mockup ("max 25 MB / file").
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _envelope(data: Any, meta: dict | None = None) -> dict:
    return {"data": data, "meta": meta, "error": None}


def _subsystem(request: Request) -> Any:
    """The KB subsystem from app.state, or None if it never came up."""
    return getattr(request.app.state, "kb_subsystem", None)


def _require_kb(request: Request) -> Any:
    """Return an available subsystem or 503. Use for write paths — we never
    want an ingest/approve to silently no-op."""
    sub = _subsystem(request)
    if sub is None or not getattr(sub, "available", False):
        reason = getattr(sub, "reason", "not initialized") if sub else "not initialized"
        raise HTTPException(
            status_code=503,
            detail=f"Knowledge base unavailable: {reason}",
        )
    return sub


def _namespace(sub: Any) -> str:
    return sub.settings.platform_namespace


def _resolve_ns(sub: Any, project_id: str | None) -> str:
    """KB-16 — the namespace for a (optional) project. None → platform; a real
    project → its isolated ``kb_project_<id>`` namespace."""
    from src.models.base import UNASSIGNED_PROJECT_ID

    if project_id and project_id != UNASSIGNED_PROJECT_ID:
        return str(sub.settings.project_namespace(project_id))
    return _namespace(sub)


async def _ensure_project_kb(sub: Any, project_id: str) -> tuple[str, str]:
    """Provision (idempotently) a project's KB and return (namespace,
    default_bucket_id) — the target for project-scoped uploads."""
    ns = str(sub.settings.project_namespace(project_id))
    bucket = await sub.knowledge_store.provision_project(project_id, ns)
    return ns, bucket.bucket_id


def _curator_scope(project_id: str | None) -> str:
    """KB-29 — the curator scope a curation action falls under: ``platform`` for
    the platform corpus (no project / the catch-all), else the ``project_id``."""
    from src.models.base import UNASSIGNED_PROJECT_ID

    if not project_id or project_id == UNASSIGNED_PROJECT_ID:
        return "platform"
    return str(project_id)


def _require_curator(user: dict[str, Any], project_id: str | None) -> None:
    """Raise 403 unless the user holds the curator capability for this scope
    (KB-29). Admins/developers are implicit curators; scoped viewers qualify
    only for their granted projects/platform."""
    scope = _curator_scope(project_id)
    if not is_kb_curator(user, scope):
        raise HTTPException(
            status_code=403,
            detail=f"kb_curator capability required for scope '{scope}'.",
        )


def _doc_dict(
    doc: Any, bucket_ids: list[str] | None = None, chunk_count: int = 0,
) -> dict[str, Any]:
    return {
        "doc_id": doc.doc_id,
        "namespace": doc.namespace,
        "title": doc.title,
        "uri": doc.uri,
        "source_type": doc.source_type,
        "sensitivity": doc.sensitivity,
        "status": doc.status,
        "version": doc.version,
        "curated_by": doc.curated_by,
        "approved_at": doc.approved_at.isoformat() if doc.approved_at else None,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "bucket_ids": bucket_ids or [],
        "chunk_count": chunk_count,
    }


def _bucket_dict(b: Any) -> dict[str, Any]:
    return {
        "bucket_id": b.bucket_id,
        "name": b.name,
        "slug": b.slug,
        "description": b.description,
        "is_system": b.is_system,
        "created_by": b.created_by,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "doc_count": b.doc_count,
        "chunk_count": b.chunk_count,
    }


# ── Status ─────────────────────────────────────────────────────────────────


@router.get("")
async def kb_status(request: Request, user: dict = Depends(get_current_user)):
    """Subsystem health + a one-line summary. The KB landing page reads this
    to decide between the live UI and the 'offline' banner."""
    sub = _subsystem(request)
    available = bool(sub and getattr(sub, "available", False))
    payload: dict[str, Any] = {
        "available": available,
        "reason": getattr(sub, "reason", "not initialized") if sub else "not initialized",
        "namespace": _namespace(sub) if available else None,
        "embed_model": sub.settings.embed_model if available else None,
        "rerank": sub.settings.rerank_enabled if available else None,
    }
    return _envelope(payload, meta={"kb_available": available})


# ── Documents ────────────────────────────────────────────────────────────


@router.get("/documents")
async def list_documents(
    request: Request,
    status: str | None = None,
    bucket_id: str | None = None,
    project_id: str | None = None,
    limit: int = 200,
    user: dict = Depends(get_current_user),
):
    """List documents in a namespace (platform by default; ``project_id`` →
    that project's isolated namespace, KB-16). Optionally filtered by status or
    a single bucket. Each doc carries its bucket membership for the tagging
    screen."""
    sub = _subsystem(request)
    if sub is None or not getattr(sub, "available", False):
        return _envelope([], meta={"kb_available": False})
    store = sub.knowledge_store
    ns = _resolve_ns(sub, project_id)
    docs = await store.list_documents(ns, status=status, limit=limit)
    doc_ids = [d.doc_id for d in docs]
    membership = await store.get_document_buckets_bulk(doc_ids)
    counts = await store.get_chunk_counts_bulk(doc_ids)
    if bucket_id:
        docs = [d for d in docs if bucket_id in membership.get(d.doc_id, [])]
    payload = [
        _doc_dict(d, membership.get(d.doc_id, []), counts.get(d.doc_id, 0))
        for d in docs
    ]
    return _envelope(payload, meta={"kb_available": True, "count": len(payload)})


@router.post("/documents")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    bucket_ids: str = Form("[]"),
    project_id: str | None = Form(None),
    user: dict = Depends(require_role("developer", "admin")),
):
    """Upload + ingest a single document (parse → chunk → PII → embed → store).
    Lands ``status='pending'`` (curator-approves before retrieval). ``bucket_ids``
    is a JSON array string of bucket ids to tag it into.

    KB-16: with ``project_id`` the doc goes into that project's isolated
    namespace (provisioned on demand) + its default bucket, so app-specific
    refs (brand guide, domain docs) are grounded only to that app."""
    import json

    from src.models.base import UNASSIGNED_PROJECT_ID

    sub = _require_kb(request)
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")
    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
        )
    try:
        parsed = json.loads(bucket_ids) if bucket_ids else []
        selected = [str(b) for b in parsed] if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        selected = []

    # Resolve the target namespace + (for projects) the default bucket.
    if project_id and project_id != UNASSIGNED_PROJECT_ID:
        ns, default_bucket = await _ensure_project_kb(sub, project_id)
        if default_bucket not in selected:
            selected.append(default_bucket)
    else:
        ns, project_id = _namespace(sub), None

    from src.knowledge.loaders import UnsupportedFileTypeError

    try:
        result = await sub.pipeline.ingest_file(
            filename=file.filename,
            data=data,
            namespace=ns,
            bucket_ids=selected,
            created_by=user.get("username", "unknown"),
        )
    except UnsupportedFileTypeError as e:
        raise HTTPException(status_code=415, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.warning("kb_upload_failed", filename=file.filename, error=str(e))
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {e}") from e

    await request.app.state.events.emit("knowledge.document.ingested", {
        "doc_id": result.doc_id, "status": result.status,
        "skipped": result.skipped, "chunks": result.chunks,
        "filename": file.filename, "by": user.get("username", "unknown"),
    })
    logger.info(
        "kb_document_uploaded", doc_id=result.doc_id, status=result.status,
        skipped=result.skipped, chunks=result.chunks, filename=file.filename,
    )
    return _envelope({
        "doc_id": result.doc_id,
        "status": result.status,
        "skipped": result.skipped,
        "chunks": result.chunks,
        "sensitivity": result.sensitivity,
        "pii_findings": result.pii_findings,
        "bucket_ids": selected,
    })


@router.post("/documents/{doc_id}/approve")
async def approve_document(
    request: Request,
    doc_id: str,
    user: dict = Depends(get_current_user),
):
    """Promote a pending document to ``approved`` — only approved docs are
    retrievable by agents. Requires the ``kb_curator`` capability for the
    document's scope (KB-29)."""
    sub = _require_kb(request)
    doc = await sub.knowledge_store.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found.")
    _require_curator(user, getattr(doc, "project_id", None))
    ok = await sub.knowledge_store.set_document_status(
        doc_id, "approved", curated_by=user.get("username", "unknown")
    )
    if not ok:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found.")
    await request.app.state.events.emit("knowledge.document.approved", {
        "doc_id": doc_id, "by": user.get("username", "unknown"),
    })
    return _envelope({"doc_id": doc_id, "status": "approved"})


class RetireBody(BaseModel):
    status: str = "superseded"  # superseded | retired


@router.post("/documents/{doc_id}/retire")
async def retire_document(
    request: Request,
    doc_id: str,
    body: RetireBody | None = None,
    user: dict = Depends(get_current_user),
):
    """Take a document out of retrieval without deleting it (``superseded`` /
    ``retired``). Reversible via re-approve. Requires the ``kb_curator``
    capability for the document's scope (KB-29)."""
    sub = _require_kb(request)
    status = (body.status if body else "superseded") or "superseded"
    if status not in ("superseded", "retired", "pending"):
        raise HTTPException(status_code=400, detail=f"Invalid retire status {status!r}.")
    doc = await sub.knowledge_store.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found.")
    _require_curator(user, getattr(doc, "project_id", None))
    ok = await sub.knowledge_store.set_document_status(doc_id, status)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found.")
    await request.app.state.events.emit("knowledge.document.retired", {
        "doc_id": doc_id, "status": status, "by": user.get("username", "unknown"),
    })
    return _envelope({"doc_id": doc_id, "status": status})


@router.delete("/documents/{doc_id}")
async def purge_document(
    request: Request,
    doc_id: str,
    user: dict = Depends(require_role("admin")),
):
    """Hard-delete a document + its chunks + membership (right-to-be-forgotten,
    FR-015). Admin only — irreversible."""
    sub = _require_kb(request)
    ok = await sub.knowledge_store.purge_document(doc_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found.")
    await request.app.state.events.emit("knowledge.document.purged", {
        "doc_id": doc_id, "by": user.get("username", "unknown"),
    })
    logger.info("kb_document_purged", doc_id=doc_id, by=user.get("username", "unknown"))
    return _envelope({"doc_id": doc_id, "status": "purged"})


class SetBucketsBody(BaseModel):
    bucket_ids: list[str]


@router.put("/documents/{doc_id}/buckets")
async def set_document_buckets(
    request: Request,
    doc_id: str,
    body: SetBucketsBody,
    user: dict = Depends(require_role("developer", "admin")),
):
    """Replace a document's bucket membership (the tagging screen). Keeps the
    denormalized ``kb_chunks.bucket_ids`` retrieval filter in sync."""
    sub = _require_kb(request)
    doc = await sub.knowledge_store.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found.")
    await sub.knowledge_store.set_document_buckets(doc_id, body.bucket_ids)
    await request.app.state.events.emit("knowledge.document.tagged", {
        "doc_id": doc_id, "bucket_ids": body.bucket_ids,
        "by": user.get("username", "unknown"),
    })
    return _envelope({"doc_id": doc_id, "bucket_ids": body.bucket_ids})


# ── Buckets ────────────────────────────────────────────────────────────────


@router.get("/buckets")
async def list_buckets(
    request: Request,
    project_id: str | None = None,
    user: dict = Depends(get_current_user),
):
    """List buckets. ``project_id`` → only that project's buckets (KB-16);
    omitted → all (global + system)."""
    sub = _subsystem(request)
    if sub is None or not getattr(sub, "available", False):
        return _envelope([], meta={"kb_available": False})
    buckets = await sub.knowledge_store.list_buckets(project_id=project_id)
    payload = [_bucket_dict(b) for b in buckets]
    return _envelope(payload, meta={"kb_available": True, "count": len(payload)})


class CreateBucketBody(BaseModel):
    name: str
    description: str = ""


@router.post("/buckets")
async def create_bucket(
    request: Request,
    body: CreateBucketBody,
    user: dict = Depends(require_role("developer", "admin")),
):
    sub = _require_kb(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Bucket name is required.")
    bucket = await sub.knowledge_store.create_bucket(
        name=name, description=body.description, created_by=user.get("username", "unknown"),
    )
    await request.app.state.events.emit("knowledge.bucket.created", {
        "bucket_id": bucket.bucket_id, "name": bucket.name,
        "by": user.get("username", "unknown"),
    })
    return _envelope(_bucket_dict(bucket))


class UpdateBucketBody(BaseModel):
    name: str
    description: str | None = None


@router.patch("/buckets/{bucket_id}")
async def rename_bucket(
    request: Request,
    bucket_id: str,
    body: UpdateBucketBody,
    user: dict = Depends(require_role("developer", "admin")),
):
    sub = _require_kb(request)
    existing = await sub.knowledge_store.get_bucket(bucket_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Bucket {bucket_id} not found.")
    if existing.is_system:
        raise HTTPException(status_code=403, detail="System buckets cannot be renamed.")
    ok = await sub.knowledge_store.rename_bucket(bucket_id, body.name.strip(), body.description)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Bucket {bucket_id} not found.")
    updated = await sub.knowledge_store.get_bucket(bucket_id)
    return _envelope(_bucket_dict(updated))


@router.delete("/buckets/{bucket_id}")
async def delete_bucket(
    request: Request,
    bucket_id: str,
    user: dict = Depends(require_role("admin")),
):
    """Delete a (non-system) bucket. Membership cascades; affected docs' chunks
    are re-synced so the removed bucket id leaves their scope."""
    sub = _require_kb(request)
    existing = await sub.knowledge_store.get_bucket(bucket_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Bucket {bucket_id} not found.")
    if existing.is_system:
        raise HTTPException(status_code=403, detail="System buckets cannot be deleted.")
    await sub.knowledge_store.delete_bucket(bucket_id)
    await request.app.state.events.emit("knowledge.bucket.deleted", {
        "bucket_id": bucket_id, "by": user.get("username", "unknown"),
    })
    return _envelope({"bucket_id": bucket_id, "status": "deleted"})


# ── Reindex ────────────────────────────────────────────────────────────────


@router.post("/reindex")
async def reindex_platform_corpus(
    request: Request,
    user: dict = Depends(require_role("admin")),
):
    """Re-ingest the platform corpus (CLAUDE.md, docs/**) into the system
    Platform bucket. Idempotent (content-hash dedup) — a re-run only ingests
    what changed. Admin only."""
    sub = _require_kb(request)
    from src.knowledge.reindex import reindex_platform

    root = Path("/app") if Path("/app/docs").is_dir() else Path.cwd()
    summary = await reindex_platform(
        pipeline=sub.pipeline,
        store=sub.knowledge_store,
        root=root,
        namespace=_namespace(sub),
    )
    await request.app.state.events.emit("knowledge.reindex.completed", {
        "ingested": summary.ingested, "skipped": summary.skipped,
        "chunks": summary.chunks, "by": user.get("username", "unknown"),
    })
    logger.info(
        "kb_reindex_via_api", ingested=summary.ingested, skipped=summary.skipped,
        by=user.get("username", "unknown"),
    )
    return _envelope(summary.as_dict())


# ── Promotion gate (KB-28): memory → knowledge ───────────────────────────────


@router.get("/promotions")
async def list_promotions(
    request: Request,
    project_id: str | None = None,
    status: str = "pending",
    user: dict = Depends(get_current_user),
):
    """The review queue (FR-013): recurring patterns the consolidation job
    (KB-26) proposed for promotion from episodic memory into the citeable KB.
    Filter by ``project_id`` (→ that app's ``mem_project_<id>``) and status.
    Soft-fails to an empty list when the KB is down."""
    sub = _subsystem(request)
    if sub is None or not getattr(sub, "available", False):
        return _envelope([], meta={"kb_available": False})
    namespace = None
    from src.models.base import UNASSIGNED_PROJECT_ID

    if project_id and project_id != UNASSIGNED_PROJECT_ID:
        namespace = str(sub.settings.memory_namespace(project_id))
    cands = await sub.knowledge_store.list_promotion_candidates(
        namespace=namespace, status=status,
    )
    return _envelope(cands, meta={"kb_available": True, "count": len(cands)})


@router.post("/promotions/{candidate_id}/approve")
async def approve_promotion(
    request: Request,
    candidate_id: str,
    user: dict = Depends(get_current_user),
):
    """The one controlled doorway memory → knowledge (§5.1). On approval the
    candidate's pattern is ingested into the app's ``kb_project_<id>`` KB as an
    **approved** (citeable) document, and the candidate is marked ``promoted``.
    Unvetted episodic memory only becomes citeable fact via this human gate —
    never automatically. Requires the ``kb_curator`` capability for the
    candidate's project (KB-29)."""
    sub = _require_kb(request)
    store = sub.knowledge_store
    cand = await store.get_promotion_candidate(candidate_id)
    if cand is None:
        raise HTTPException(status_code=404, detail=f"Candidate {candidate_id} not found.")
    if cand["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Candidate {candidate_id} already {cand['status']}.",
        )
    project_id = cand.get("project_id")
    if not project_id:
        raise HTTPException(
            status_code=400,
            detail="Candidate has no project_id; cannot resolve a target KB.",
        )
    _require_curator(user, project_id)
    reviewer = user.get("username", "unknown")
    ns, bucket_id = await _ensure_project_kb(sub, project_id)
    res = await sub.pipeline.ingest_text(
        text=cand["summary"], title=f"Promoted lesson ({candidate_id})",
        source_type="memory_promotion", namespace=ns,
        bucket_ids=[bucket_id], project_id=project_id,
    )
    if not res.skipped:
        await store.set_document_status(res.doc_id, "approved", curated_by=reviewer)
    moved = await store.set_promotion_status(candidate_id, "promoted", reviewed_by=reviewer)
    if not moved:  # lost a race — someone else reviewed it first
        raise HTTPException(status_code=409, detail="Candidate was already reviewed.")
    await request.app.state.events.emit("knowledge.promotion.approved", {
        "candidate_id": candidate_id, "doc_id": res.doc_id,
        "project_id": project_id, "by": reviewer,
    })
    return _envelope({
        "candidate_id": candidate_id, "status": "promoted",
        "doc_id": res.doc_id, "namespace": ns,
    })


@router.post("/promotions/{candidate_id}/reject")
async def reject_promotion(
    request: Request,
    candidate_id: str,
    user: dict = Depends(get_current_user),
):
    """Decline a proposed pattern — it stays in episodic memory (unvetted) but
    is taken off the review queue. Reversible only by the job re-proposing it.
    Requires the ``kb_curator`` capability for the candidate's project (KB-29)."""
    sub = _require_kb(request)
    cand = await sub.knowledge_store.get_promotion_candidate(candidate_id)
    if cand is None:
        raise HTTPException(status_code=404, detail=f"Candidate {candidate_id} not found.")
    _require_curator(user, cand.get("project_id"))
    reviewer = user.get("username", "unknown")
    moved = await sub.knowledge_store.set_promotion_status(
        candidate_id, "rejected", reviewed_by=reviewer,
    )
    if not moved:
        raise HTTPException(
            status_code=404,
            detail=f"Candidate {candidate_id} not found or already reviewed.",
        )
    await request.app.state.events.emit("knowledge.promotion.rejected", {
        "candidate_id": candidate_id, "by": reviewer,
    })
    return _envelope({"candidate_id": candidate_id, "status": "rejected"})


# ── Retention + forgetting (KB-30) ───────────────────────────────────────────


class ForgetBody(BaseModel):
    subject: str                       # the data-subject identifier to erase
    project_id: str | None = None      # optional: scope the purge to one app


@router.post("/forget")
async def forget_subject(
    request: Request,
    body: ForgetBody,
    user: dict = Depends(require_role("admin")),
):
    """Right-to-be-forgotten (FR-015): erase every trace of a data subject from
    BOTH stores — episodic memory + KB documents (chunks/membership cascade) —
    optionally scoped to one app's namespace. Admin-only and **audited**
    (``kb_retention_audit``). Irreversible."""
    sub = _require_kb(request)
    subject = (body.subject or "").strip()
    if len(subject) < 3:
        raise HTTPException(
            status_code=400,
            detail="subject must be at least 3 characters (avoid over-broad purges).",
        )
    # Forgetting spans both stores. Memory lives in mem_project_<id>; KB docs in
    # kb_project_<id>. When project-scoped, purge both namespaces; else global.
    actor = user.get("username", "unknown")
    total = {"memory": 0, "documents": 0}
    namespaces: list[str | None]
    if body.project_id:
        from src.models.base import UNASSIGNED_PROJECT_ID

        if body.project_id == UNASSIGNED_PROJECT_ID:
            raise HTTPException(
                status_code=400, detail="Cannot scope purge to the unassigned project."
            )
        namespaces = [
            str(sub.settings.memory_namespace(body.project_id)),
            str(sub.settings.project_namespace(body.project_id)),
        ]
    else:
        namespaces = [None]  # global sweep across all namespaces
    for ns in namespaces:
        counts = await sub.knowledge_store.forget_subject(subject, namespace=ns, actor=actor)
        total["memory"] += counts.get("memory", 0)
        total["documents"] += counts.get("documents", 0)
    await request.app.state.events.emit("knowledge.subject.forgotten", {
        "subject": subject, "project_id": body.project_id, "by": actor, **total,
    })
    return _envelope({"subject": subject, **total})


@router.get("/retention/audit")
async def retention_audit(
    request: Request,
    limit: int = 100,
    user: dict = Depends(require_role("developer", "admin")),
):
    """The forgetting paper trail (KB-30): TTL/prune sweeps + right-to-be-
    forgotten purges, newest first. Soft-fails to empty when the KB is down."""
    sub = _subsystem(request)
    if sub is None or not getattr(sub, "available", False):
        return _envelope([], meta={"kb_available": False})
    rows = await sub.knowledge_store.list_retention_audit(limit=limit)
    return _envelope(rows, meta={"kb_available": True, "count": len(rows)})


# ── Retrieval feedback (KB-31) ───────────────────────────────────────────────


class FeedbackBody(BaseModel):
    chunk_id: str
    vote: str = "up"                   # "up" | "down"
    request_id: str | None = None


@router.post("/feedback")
async def submit_feedback(
    request: Request,
    body: FeedbackBody,
    user: dict = Depends(get_current_user),
):
    """Thumbs up/down on a retrieved chunk (KB-31). Any authenticated user can
    vote; the namespace is resolved from the chunk (so the vote is inherently
    scoped to its app), and the signal feeds a recency-weighted usefulness boost
    into reranking. Re-voting overwrites your prior vote."""
    sub = _require_kb(request)
    vote = 1 if str(body.vote).lower() in ("up", "+1", "1", "true") else -1
    chunks = await sub.knowledge_store.get_chunks_by_ids([body.chunk_id])
    chunk = chunks.get(body.chunk_id)
    if chunk is None:
        raise HTTPException(status_code=404, detail=f"Chunk {body.chunk_id} not found.")
    fid = await sub.knowledge_store.record_feedback(
        chunk_id=body.chunk_id, namespace=chunk.get("namespace", ""), vote=vote,
        request_id=body.request_id, created_by=user.get("username", "unknown"),
    )
    return _envelope({
        "feedback_id": fid, "chunk_id": body.chunk_id,
        "vote": "up" if vote > 0 else "down",
    })


# ── Grounding Report ─────────────────────────────────────────────────────────


@router.get("/grounding/{request_id}")
async def grounding_report(
    request: Request,
    request_id: str,
    user: dict = Depends(get_current_user),
):
    """The traceability view (FR-010/011/016): the full provenance chain for a
    request — the agents' retrievals (Searched/Returned/Used), the decision
    ledger (Concluded → why), and every cited chunk hydrated to its document +
    source + version/approved-by (claim → chunk → document → source)."""
    sub = _subsystem(request)
    if sub is None or not getattr(sub, "available", False):
        return _envelope(
            {"request_id": request_id, "buckets": [], "retrievals": [],
             "citations": [], "decisions": []},
            meta={"kb_available": False},
        )
    store = sub.knowledge_store

    # The request's selected grounding buckets (from SQLite).
    state = request.app.state.state_store
    req = await state.get_request(request_id)
    selected_bucket_ids = list(getattr(req, "bucket_ids", []) or []) if req else []

    audit = await store.list_retrieval_audit(request_id)
    decisions = await store.list_decisions(request_id)

    # Hydrate every cited chunk once for the citation drawer.
    cited_ids: list[str] = []
    for a in audit:
        cited_ids.extend(a.get("cited_chunk_ids") or [])
    cited_ids = list(dict.fromkeys(cited_ids))
    chunks = await store.get_chunks_by_ids(cited_ids) if cited_ids else {}

    # KB-23 — enrich each citation with the FULL provenance of its source
    # document (version / ingested-when / approved-by / status), so the chain
    # is claim → chunk → document → original source. One doc fetch per cited doc.
    doc_ids = {c.get("doc_id") for c in chunks.values() if c.get("doc_id")}
    docs: dict[str, Any] = {}
    for did in doc_ids:
        d = await store.get_document(did)
        if d is not None:
            docs[did] = d
    citations = []
    for cid, c in chunks.items():
        d = docs.get(c.get("doc_id"))
        citations.append({
            "chunk_id": cid,
            "doc_id": c.get("doc_id"),
            "title": c.get("title"),
            "uri": c.get("uri"),
            "snippet": (c.get("text") or "")[:700],
            "source_type": getattr(d, "source_type", None) if d else None,
            "version": getattr(d, "version", None) if d else None,
            "status": getattr(d, "status", None) if d else c.get("status"),
            "approved_by": getattr(d, "curated_by", None) if d else None,
            "approved_at": (
                d.approved_at.isoformat() if d and getattr(d, "approved_at", None) else None
            ),
            "ingested_at": (
                d.created_at.isoformat() if d and getattr(d, "created_at", None) else None
            ),
        })
    return _envelope(
        {
            "request_id": request_id,
            "buckets": selected_bucket_ids,
            "retrievals": audit,
            "citations": citations,
            "decisions": decisions,
        },
        meta={
            "kb_available": True, "retrieval_count": len(audit),
            "decision_count": len(decisions),
        },
    )
