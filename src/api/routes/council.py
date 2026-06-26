"""Council endpoints — ad-hoc one-shot reviews outside the workflow pipeline.

POST   /api/v1/council         — submit a review (paste code or document)
POST   /api/v1/council/upload  — submit a review (upload a file)
GET    /api/v1/council         — list past reviews
GET    /api/v1/council/{id}    — full review detail
DELETE /api/v1/council/{id}    — delete a review (developer/admin)
"""

from __future__ import annotations

import json as _json
import os
import uuid
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from src.auth.service import get_current_user, require_role
from src.knowledge.loaders import (
    LoaderUnavailableError,
    UnsupportedFileTypeError,
    load_text,
)
from src.models.base import Document

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/council", tags=["council"])

# ── Constants ──────────────────────────────────────────────────────

ALLOWED_AGENT_TYPES: frozenset[str] = frozenset({"code_reviewer", "document_reviewer"})
MAX_CONTENT_CHARS: int = int(os.getenv("COUNCIL_MAX_CONTENT_CHARS", "100000"))
COUNCIL_MAX_TOKENS: int = int(os.getenv("COUNCIL_MAX_TOKENS", "8192"))
COUNCIL_MAX_UPLOAD_BYTES: int = int(
    os.getenv("COUNCIL_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))
)

DOC_TYPE_BY_AGENT: dict[str, str] = {
    "code_reviewer": "council_code_review",
    "document_reviewer": "council_doc_review",
}

COUNCIL_DOC_TYPES: frozenset[str] = frozenset(DOC_TYPE_BY_AGENT.values())
COUNCIL_TAG = "council"


# ── Request / Response models ──────────────────────────────────────


class CouncilRequest(BaseModel):
    agent_type: str
    content: str
    language: str | None = None
    document_type: str | None = None
    focus_areas: list[str] | None = None


# ── Helpers ────────────────────────────────────────────────────────


def _envelope(data: Any, meta: dict | None = None) -> dict:
    return {"data": data, "meta": meta, "error": None}


def _iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    iso = dt.isoformat()
    if "+" not in iso and not iso.endswith("Z"):
        iso += "Z"
    return iso


def _get_executor(request: Request) -> Any:
    """Return the AgentSystemExecutor or raise 503 if not initialized."""
    executor = getattr(request.app.state, "agent_executor", None)
    if not executor:
        raise HTTPException(
            status_code=503,
            detail="Agent executor not initialized — LLM client unavailable",
        )
    return executor


def _parse_focus(raw: str | None) -> list[str]:
    """Parse a JSON-array Form string into a focus_areas list.

    Tolerates empty / malformed → [].  Used by both paste (pydantic list)
    and upload (multipart Form string) paths.
    """
    if not raw:
        return []
    try:
        parsed = _json.loads(raw)
        if isinstance(parsed, list):
            return [str(f) for f in parsed]
    except (ValueError, TypeError):
        pass
    return []


def _build_prompt_text(
    agent_type: str,
    content: str,
    *,
    language: str | None = None,
    document_type: str | None = None,
    focus_areas: list[str] | None = None,
) -> str:
    """Build the review prompt from individual fields."""
    context_lines: list[str] = []

    if agent_type == "code_reviewer":
        context_lines.append(
            "You are performing an AD-HOC, one-shot code review. You have NO "
            "tools and NO repository access — review ONLY the code pasted below. "
            "Do not assume files you cannot see. If context is missing, state "
            "the assumption explicitly."
        )
        if language:
            context_lines.append(f"Language/framework: {language}")
        if not focus_areas:
            focus_areas = ["All"]
    else:
        context_lines.append(
            "You are performing an AD-HOC, one-shot document review. You have "
            "NO tools — review ONLY the document text provided below."
        )
        if document_type:
            context_lines.append(f"Document type: {document_type}")
        if not focus_areas:
            focus_areas = ["All"]

    context_lines.append(f"Focus areas: {', '.join(focus_areas)}")
    context_lines.append("\n--- BEGIN CONTENT ---")
    header = "\n".join(context_lines)
    footer = "--- END CONTENT ---"

    return f"{header}\n{content}\n{footer}"


def _build_title_text(
    agent_type: str,
    *,
    language: str | None = None,
    document_type: str | None = None,
) -> str:
    """Build a human-readable title for the review document."""
    ts = datetime.now(tz=None).strftime("%Y-%m-%d %H:%M")
    if agent_type == "code_reviewer":
        lang = language or "Code"
        return f"Code Review — {lang} ({ts})"
    else:
        dtype = document_type or "Document"
        return f"Document Review — {dtype} ({ts})"


def _doc_to_payload(doc: Document) -> dict[str, Any]:
    """Map a persisted Document row to the Council API response shape."""
    tags: list[str] = getattr(doc, "tags", []) or []
    agent_type = (
        "code_reviewer"
        if doc.doc_type == "council_code_review"
        else "document_reviewer"
    )
    focus_areas = [
        t for t in tags
        if t not in (COUNCIL_TAG, agent_type) and t != "All"
    ]
    payload: dict[str, Any] = {
        "council_id": doc.document_id,
        "agent_type": agent_type,
        "title": doc.title,
        "review_report": doc.content,
        "focus_areas": focus_areas if focus_areas else ["All"],
        "created_at": _iso_utc(doc.created_at),
    }
    # Surface source_filename when present (upload path)
    source = next((t for t in tags if t.startswith("source:")), None)
    if source:
        payload["source_filename"] = source[len("source:"):]
    return payload


def _preview(text: str, max_len: int = 200) -> str:
    """First meaningful line(s) of a review report, truncated."""
    if not text:
        return ""
    cleaned = text.lstrip("#").strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[:max_len].rsplit(" ", 1)[0] + "…"


def _validate_agent_type(agent_type: str) -> None:
    if agent_type not in ALLOWED_AGENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid agent_type: {agent_type!r}. "
            f"Allowed: {', '.join(sorted(ALLOWED_AGENT_TYPES))}",
        )


def _validate_content(content: str) -> str:
    """Strip + validate content; return the stripped version."""
    stripped = content.strip()
    if not stripped:
        raise HTTPException(status_code=400, detail="Content must not be empty.")
    if len(stripped) > MAX_CONTENT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Content exceeds {MAX_CONTENT_CHARS} character limit "
            f"({len(stripped)} chars submitted).",
        )
    return stripped


# ── Core review execution (shared by paste + upload) ──────────────


async def _run_review(
    request: Request,
    agent_type: str,
    content: str,
    *,
    language: str | None = None,
    document_type: str | None = None,
    focus_areas: list[str] | None = None,
    source_filename: str | None = None,
) -> dict[str, Any]:
    """Run a Council review end-to-end: prompt → executor → persist.

    Shared by POST /council (paste) and POST /council/upload (file upload).
    """
    focus = focus_areas or []

    # Mock mode — return a labelled placeholder, skip persistence
    agent_mode = getattr(request.app.state, "agent_mode", "unknown")
    if agent_mode == "mock":
        source_note = (
            f"\nSource file: {source_filename}\n"
            if source_filename
            else ""
        )
        mock_report = (
            f"# Mock Review Report\n\n"
            f"**⚠️ MOCK — this is NOT a real review.**\n\n"
            f"The agent system is running in mock mode (ALLOW_MOCK_MODE=true, "
            f"no LLM credentials configured). Agents produce simulated output. "
            f"Do NOT use for real work.\n\n"
            f"Agent: {agent_type}\n"
            f"Content length: {len(content)} chars\n"
            f"Focus: {', '.join(focus or ['All'])}"
            f"{source_note}"
        )
        result: dict[str, Any] = {
            "council_id": f"mock-{uuid.uuid4().hex[:8]}",
            "agent_type": agent_type,
            "title": _build_title_text(
                agent_type, language=language, document_type=document_type,
            ),
            "review_report": mock_report,
            "focus_areas": focus if focus else ["All"],
            "created_at": _iso_utc(datetime.now(tz=None)),
            "mock": True,
        }
        if source_filename:
            result["source_filename"] = source_filename
        return result

    # Resolve the executor
    executor = _get_executor(request)

    # Build the review prompt
    prompt = _build_prompt_text(
        agent_type, content,
        language=language, document_type=document_type,
        focus_areas=focus,
    )

    # Execute the review
    logger.info(
        "council_review_submitted",
        agent_type=agent_type,
        content_len=len(content),
        source=source_filename or "paste",
    )
    llm_result = await executor.single_agent_call(
        agent_id=agent_type,
        prompt=prompt,
        max_tokens=COUNCIL_MAX_TOKENS,
        label=f"council:{agent_type}",
    )

    # Check for agent-not-found
    if llm_result.get("error"):
        err = llm_result["error"]
        logger.error("council_agent_call_failed", agent_type=agent_type, error=err)
        raise HTTPException(
            status_code=502,
            detail=f"Agent {agent_type!r} could not process the review: {err}",
        )

    review_report: str = llm_result.get("text", "")
    if not review_report.strip():
        raise HTTPException(
            status_code=502,
            detail=f"Agent {agent_type!r} returned an empty review.",
        )

    # Persist the review (report only, NOT the submitted content or file)
    state = request.app.state.state_store
    tags = [COUNCIL_TAG, agent_type]
    if language:
        tags.append(language)
    if document_type:
        tags.append(document_type)
    if source_filename:
        tags.append(f"source:{source_filename}")
    for fa in focus:
        tags.append(fa)

    doc = Document(
        document_id=f"doc-{uuid.uuid4().hex[:12]}",
        request_id="",  # no parent Request
        doc_type=DOC_TYPE_BY_AGENT[agent_type],
        title=_build_title_text(
            agent_type, language=language, document_type=document_type,
        ),
        content=review_report,
        agent_id=agent_type,
        tags=tags,
    )
    await state.save_document(doc)
    logger.info("council_review_persisted", council_id=doc.document_id)

    payload: dict[str, Any] = {
        "council_id": doc.document_id,
        "agent_type": agent_type,
        "title": doc.title,
        "review_report": review_report,
        "focus_areas": focus if focus else ["All"],
        "created_at": _iso_utc(doc.created_at),
        "mock": False,
    }
    if source_filename:
        payload["source_filename"] = source_filename
    return payload


# ── Routes ─────────────────────────────────────────────────────────


@router.post("")
async def submit_council_review(
    body: CouncilRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Submit pasted content for a one-shot review by a specialist agent."""
    _validate_agent_type(body.agent_type)
    content = _validate_content(body.content)
    return _envelope(await _run_review(
        request,
        body.agent_type,
        content,
        language=body.language,
        document_type=body.document_type,
        focus_areas=body.focus_areas,
    ))


@router.post("/upload")
async def upload_council_review(
    request: Request,
    file: UploadFile = File(...),
    agent_type: str = Form(...),
    language: str | None = Form(None),
    document_type: str | None = Form(None),
    focus_areas: str = Form("[]"),
    user: dict = Depends(get_current_user),
):
    """Upload a file for a one-shot review by a specialist agent.

    Supported formats: PDF, DOCX, XLSX, Markdown, plain text, and source code.
    The file is extracted to text server-side; the bytes are never persisted.
    """
    _validate_agent_type(agent_type)

    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    data = await file.read()
    if len(data) > COUNCIL_MAX_UPLOAD_BYTES:
        limit_mb = COUNCIL_MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {limit_mb} MB size limit.",
        )

    # Early mock-mode bypass — skip file parsing entirely in mock mode
    agent_mode = getattr(request.app.state, "agent_mode", "unknown")
    if agent_mode == "mock":
        focus = _parse_focus(focus_areas)
        return _envelope(await _run_review(
            request, agent_type, "(mock — file content not extracted)",
            language=language, document_type=document_type,
            focus_areas=focus if focus else None,
            source_filename=file.filename,
        ))

    try:
        text, _src_type = load_text(file.filename, data)
    except UnsupportedFileTypeError as e:
        raise HTTPException(status_code=415, detail=str(e)) from e
    except LoaderUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    content = text.strip() if text else ""
    if not content:
        raise HTTPException(
            status_code=400,
            detail="No extractable text found (scanned or image-only file?).",
        )
    if len(content) > MAX_CONTENT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Extracted text exceeds {MAX_CONTENT_CHARS} character limit "
            f"({len(content)} chars).",
        )

    focus = _parse_focus(focus_areas)

    return _envelope(await _run_review(
        request,
        agent_type,
        content,
        language=language,
        document_type=document_type,
        focus_areas=focus if focus else None,
        source_filename=file.filename,
    ))


@router.get("")
async def list_council_reviews(
    request: Request,
    agent_type: str | None = None,
    limit: int = 20,
    user: dict = Depends(get_current_user),
):
    """List past Council reviews, newest-first.

    Optional ``agent_type`` filter: ``code_reviewer`` or ``document_reviewer``.
    """
    state = request.app.state.state_store

    # Validate agent_type filter if provided
    if agent_type is not None and agent_type not in ALLOWED_AGENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid agent_type filter: {agent_type!r}. "
            f"Allowed: {', '.join(sorted(ALLOWED_AGENT_TYPES))}",
        )

    all_docs: list[Document] = []
    for doc_type_val in COUNCIL_DOC_TYPES:
        docs = await state.search_documents("council", doc_type=doc_type_val, limit=limit)
        all_docs.extend(docs)

    if agent_type:
        target_type = DOC_TYPE_BY_AGENT[agent_type]
        all_docs = [d for d in all_docs if d.doc_type == target_type]

    all_docs.sort(key=lambda d: d.created_at, reverse=True)
    all_docs = all_docs[:limit]

    payloads = []
    for doc in all_docs:
        p = _doc_to_payload(doc)
        p["preview"] = _preview(p["review_report"])
        payloads.append(p)

    return _envelope(payloads)


@router.get("/{council_id}")
async def get_council_review(
    council_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Get a single Council review by its id."""
    state = request.app.state.state_store
    doc = await state.get_document(council_id)
    if doc is None or doc.doc_type not in COUNCIL_DOC_TYPES:
        raise HTTPException(status_code=404, detail="Council review not found.")
    return _envelope(_doc_to_payload(doc))


@router.delete("/{council_id}")
async def delete_council_review(
    council_id: str,
    request: Request,
    user: dict = Depends(require_role("developer", "admin")),
):
    """Delete a Council review. Developer/admin only. Viewer → 403."""
    state = request.app.state.state_store
    doc = await state.get_document(council_id)
    if doc is None or doc.doc_type not in COUNCIL_DOC_TYPES:
        raise HTTPException(status_code=404, detail="Council review not found.")
    deleted = await state.delete_document(council_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Council review not found.")
    return _envelope({"council_id": council_id, "deleted": True})
