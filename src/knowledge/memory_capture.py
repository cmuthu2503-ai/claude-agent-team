"""KB-24 — auto-capture episodic memory on task completion (Phase 4).

The platform's agents finish requests all day; that lived experience — what was
attempted, in which app, and how it turned out — is the raw material of
*episodic* memory. This handler captures one episode per completed/failed
Request into the project's ``mem_project_<id>`` namespace so that, later,
``recall_memory`` (KB-25) can answer "what did we try for this app, and what
happened?"

What episodic memory is NOT (§5.1): it is **unvetted** and **never citeable as
fact**. It rides the same embedding path as the KB only so recall can be
semantic + time-aware — but it lives in its own table (``agent_memory``), its
own namespace (``mem_*``), and is surfaced to agents tagged
``[MEMORY · unvetted]``. The one controlled doorway from memory → knowledge is
the promotion review gate (KB-28), never auto-promotion.

Driven by the same ``EventEmitter`` everything else uses:

- ``request.completed`` → capture a ``success`` episode
- ``request.failed``    → capture a ``failed`` episode

Idempotent (dedups on a per-request content hash, so the orchestrator emitting
``request.failed`` from several code paths can't double-write) and soft-fail (a
KB hiccup must never block request finalization or event broadcasting).
Unassigned-project requests are skipped — there is no per-app memory for the
catch-all project.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from src.knowledge.models import AgentMemory
from src.models.base import UNASSIGNED_PROJECT_ID

logger = structlog.get_logger()

# Keep episode text compact — memory is a recall aid, not a full transcript.
_MAX_EPISODE_CHARS = 2_000


def _summarize(request: Any, outcome: str, detail: str) -> str:
    """Render a compact, human-readable episode from a finished Request."""
    task_type = getattr(getattr(request, "task_type", None), "value", "") or str(
        getattr(request, "task_type", "")
    )
    desc = (getattr(request, "description", "") or "").strip()
    lines = [
        f"[{outcome.upper()}] {task_type} request {getattr(request, 'request_id', '?')}",
        f"Goal: {desc}" if desc else "",
        f"Outcome detail: {detail.strip()}" if detail and detail.strip() else "",
    ]
    text = "\n".join(line for line in lines if line)
    return text[:_MAX_EPISODE_CHARS]


async def _embed(subsystem: Any, text: str) -> list[float] | None:
    """Embed the episode (best-effort). Memory is still useful without a
    vector — recency-ordered ``list_memory`` works — so an embedding failure
    degrades to a None embedding rather than dropping the capture."""
    embedder = getattr(subsystem, "embedder", None)
    if embedder is None:
        return None
    try:
        res = await embedder.embed_documents([text])
        if res and res.vectors:
            return list(res.vectors[0])
    except Exception as e:  # noqa: BLE001
        logger.warning("kb_memory_embed_failed", error=str(e))
    return None


async def _capture(
    subsystem: Any, state: Any, request_id: str, outcome: str, detail: str,
) -> None:
    request = await state.get_request(request_id)
    if request is None:
        return
    project_id = getattr(request, "project_id", None)
    if not project_id or project_id == UNASSIGNED_PROJECT_ID:
        return  # no per-app memory for the catch-all project

    namespace = subsystem.settings.memory_namespace(project_id)
    text = _summarize(request, outcome, detail)
    # Per-request idempotency key (kind=episode is the one capture per request).
    content_hash = hashlib.sha256(
        f"{request_id}|episode|{outcome}".encode()
    ).hexdigest()
    embedding = await _embed(subsystem, text)

    mem = AgentMemory(
        memory_id=f"mem-{uuid.uuid4().hex[:12]}",
        namespace=namespace,
        agent_id="orchestrator",
        request_id=request_id,
        project_id=project_id,
        kind="episode",
        text=text,
        outcome=outcome,
        embedding=embedding,
        content_hash=content_hash,
        unvetted=True,
    )
    stored_id = await subsystem.knowledge_store.insert_memory(mem)
    logger.info(
        "kb_memory_captured", request_id=request_id, project_id=project_id,
        namespace=namespace, outcome=outcome, memory_id=stored_id,
    )


def make_memory_capture_handler(
    subsystem: Any, state: Any
) -> Callable[[str, dict[str, Any]], Awaitable[None]]:
    """Return an EventEmitter-compatible ``async handler(event_type, data)``
    that captures one episodic-memory row per completed/failed Request."""

    async def handler(event_type: str, data: dict[str, Any]) -> None:
        if subsystem is None or not getattr(subsystem, "available", False):
            return
        request_id = data.get("request_id")
        if not request_id:
            return
        try:
            if event_type == "request.completed":
                await _capture(
                    subsystem, state, request_id, "success",
                    str(data.get("result") or ""),
                )
            elif event_type == "request.failed":
                await _capture(
                    subsystem, state, request_id, "failed",
                    str(data.get("error") or data.get("escalation_reason") or ""),
                )
        except Exception as e:  # noqa: BLE001 — never block request finalization
            logger.warning(
                "kb_memory_capture_failed", event=event_type,
                request_id=request_id, error=str(e),
            )

    return handler
