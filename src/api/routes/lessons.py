"""Lessons review endpoints — AET-13 pending review gate.

Auto-generated lessons from the ``self_learning_agent`` land in
``docs/agent-lessons-learned.pending.md`` wrapped in
``<!-- pending-lesson:start … --> … <!-- pending-lesson:end … -->``
markers. These endpoints let a human approve them (promote to the
canonical ``agent-lessons-learned.md``, which every code-writing agent
reads on every invocation) or reject them (drop the block).

Endpoints
---------
GET    /api/v1/lessons/pending                — list queued entries
POST   /api/v1/lessons/{lesson_id}/approve    — promote to canonical
POST   /api/v1/lessons/{lesson_id}/reject     — drop from queue

All write endpoints require the ``admin`` role — auto-generated lessons
become part of the agent system prompt globally, so the approval signal
is privileged.
"""

import re
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from src.auth.service import get_current_user, require_role
from src.tools.lessons_writer import (
    LESSONS_FILE,
    PENDING_LESSONS_FILE,
    _PENDING_BLOCK_RE,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/lessons", tags=["lessons"])


def _envelope(data: Any, meta: dict | None = None) -> dict:
    return {"data": data, "meta": meta, "error": None}


def _read_pending() -> str:
    if not PENDING_LESSONS_FILE.exists():
        return ""
    return PENDING_LESSONS_FILE.read_text(encoding="utf-8")


def _write_pending(text: str) -> None:
    PENDING_LESSONS_FILE.write_text(text, encoding="utf-8")


def _read_canonical() -> str:
    if not LESSONS_FILE.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Canonical lessons file missing at {LESSONS_FILE}",
        )
    return LESSONS_FILE.read_text(encoding="utf-8")


def _write_canonical(text: str) -> None:
    LESSONS_FILE.write_text(text, encoding="utf-8")


def _parse_pending_entries(text: str) -> list[dict[str, Any]]:
    """Return [{lesson_id, request_id, created, body, span_start, span_end}, …]
    one per ``<!-- pending-lesson:start … --> … <!-- pending-lesson:end … -->``
    block found in the pending file. Order matches source order, which
    is also the order in which the agent queued them."""
    out: list[dict[str, Any]] = []
    for m in _PENDING_BLOCK_RE.finditer(text):
        out.append({
            "lesson_id": m.group("id"),
            "request_id": m.group("request_id"),
            "created": m.group("created"),
            "body": m.group("body").strip(),
            "span_start": m.start(),
            "span_end": m.end(),
        })
    return out


def _find_pending(text: str, lesson_id: str) -> dict[str, Any] | None:
    """Locate one pending entry by lesson_id, or return None. lesson_id
    must match the canonical ``L<NN>`` form embedded in the start marker."""
    for entry in _parse_pending_entries(text):
        if entry["lesson_id"] == lesson_id:
            return entry
    return None


def _strip_block_with_neighbors(
    text: str, span_start: int, span_end: int,
) -> str:
    """Cut a block out of *text* and tidy up the surrounding whitespace
    so we don't leave a triple-blank-line scar. Trailing newline after
    the block is consumed too if present."""
    # Consume trailing newline immediately after the block.
    end = span_end
    if end < len(text) and text[end] == "\n":
        end += 1
    return text[:span_start] + text[end:]


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.get("/pending")
async def list_pending_lessons(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """List all lessons currently queued for human review.

    Open to any authenticated user — viewing the queue doesn't change
    the agent system prompt, only approve/reject does.
    """
    text = _read_pending()
    entries = _parse_pending_entries(text)
    # Strip the heavy span fields from the API response — clients only
    # need the rendered body + metadata.
    payload = [
        {
            "lesson_id": e["lesson_id"],
            "request_id": e["request_id"],
            "created": e["created"],
            "body": e["body"],
        }
        for e in entries
    ]
    return _envelope(payload, meta={"count": len(payload)})


@router.post("/{lesson_id}/approve")
async def approve_lesson(
    lesson_id: str,
    request: Request,
    user: dict = Depends(require_role("admin")),
):
    """Promote a pending lesson to the canonical doc.

    Steps:
      1. Locate the block in the pending file (by lesson_id).
      2. Append its body to ``agent-lessons-learned.md``.
      3. Remove the block from the pending file.

    On any failure, the canonical doc is unchanged. The pending file is
    only modified after the canonical write succeeds.
    """
    pending_text = _read_pending()
    entry = _find_pending(pending_text, lesson_id)
    if not entry:
        raise HTTPException(
            status_code=404,
            detail=f"Lesson {lesson_id} not found in pending review queue",
        )

    # Append to canonical FIRST — if that fails, pending is unchanged.
    canonical = _read_canonical()
    separator = "\n\n" if canonical.endswith("\n") else "\n\n\n"
    new_canonical = canonical + separator + entry["body"] + "\n"
    _write_canonical(new_canonical)

    # Now safely strip the block from pending.
    new_pending = _strip_block_with_neighbors(
        pending_text, entry["span_start"], entry["span_end"],
    )
    _write_pending(new_pending)

    # Emit event so UI can refresh the queue + cost dashboard.
    events = request.app.state.events
    await events.emit("lessons.approved", {
        "lesson_id": lesson_id,
        "request_id": entry["request_id"],
        "approver": user.get("username", "unknown"),
    })

    logger.info(
        "lesson_approved",
        lesson_id=lesson_id, request_id=entry["request_id"],
        approver=user.get("username", "unknown"),
    )
    return _envelope({
        "lesson_id": lesson_id,
        "status": "approved",
        "request_id": entry["request_id"],
    })


@router.post("/{lesson_id}/reject")
async def reject_lesson(
    lesson_id: str,
    request: Request,
    user: dict = Depends(require_role("admin")),
):
    """Drop a pending lesson without promoting it to canonical.

    No content is moved — the block is removed from the pending file
    and silently dropped. The original request's failure context is
    still preserved in the database; this only discards the lesson
    text the self_learning_agent generated.
    """
    pending_text = _read_pending()
    entry = _find_pending(pending_text, lesson_id)
    if not entry:
        raise HTTPException(
            status_code=404,
            detail=f"Lesson {lesson_id} not found in pending review queue",
        )

    new_pending = _strip_block_with_neighbors(
        pending_text, entry["span_start"], entry["span_end"],
    )
    _write_pending(new_pending)

    events = request.app.state.events
    await events.emit("lessons.rejected", {
        "lesson_id": lesson_id,
        "request_id": entry["request_id"],
        "rejecter": user.get("username", "unknown"),
    })

    logger.info(
        "lesson_rejected",
        lesson_id=lesson_id, request_id=entry["request_id"],
        rejecter=user.get("username", "unknown"),
    )
    return _envelope({
        "lesson_id": lesson_id,
        "status": "rejected",
        "request_id": entry["request_id"],
    })
