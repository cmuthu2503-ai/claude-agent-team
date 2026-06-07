"""KB-14 — auto-ingest a project's approved artifacts into its KB (Phase 2).

When the platform produces an *approved* artifact for a Project, fold it into
that project's isolated knowledge namespace (``kb_project_<id>``) so the agents
working that app retrieve grounded, app-specific context. Driven by the same
``EventEmitter`` the rest of the platform uses.

The Q-ING approval gate: we only ingest artifacts that have crossed a human/
pipeline approval bar — a **finalized** PRD / API spec / tasks list, a
**successful** code commit, or a **published** research output. Those are
trusted (like the platform corpus), so they're auto-approved into the project
KB rather than held ``pending``.

Sources & events
----------------
- ``project.prd_finalized``        → the finalized PRD markdown
- ``project.api_spec_finalized``   → the finalized API spec markdown
- ``project.tasks_finalized``      → the finalized task list (rendered)
- ``research_publish.completed``   → the published research markdown (on disk)
- ``code_commit.completed``        → a commit manifest + best-effort file bodies

Every path is idempotent (the ingestion pipeline dedups on content hash) and
soft-fails — a KB hiccup must never block the artifact pipeline. Unassigned-
project artifacts are skipped (no per-app KB for the catch-all project).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import structlog

from src.models.base import UNASSIGNED_PROJECT_ID, ArtifactKind

logger = structlog.get_logger()

# Best-effort code-body ingestion caps (keep the KB lean + the handler fast).
_MAX_COMMIT_FILES = 40
_MAX_FILE_BYTES = 200_000
_CODE_ROOT = Path("/app")  # platform-repo layout; per-workspace roots: KB-19
_RESEARCH_ROOT = Path("/app/docs/research")
_TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".txt", ".yaml", ".yml",
    ".json", ".toml", ".css", ".html", ".sql", ".sh",
}


async def _ensure_kb(subsystem: Any, project_id: str) -> tuple[str, str]:
    """Provision (idempotently) the project's KB and return (namespace,
    default_bucket_id)."""
    ns = subsystem.settings.project_namespace(project_id)
    bucket = await subsystem.knowledge_store.provision_project(project_id, ns)
    return ns, bucket.bucket_id


async def _ingest(
    subsystem: Any, ns: str, bucket_id: str, project_id: str, *,
    text: str, title: str, source_type: str,
) -> str | None:
    """Ingest one text artifact into the project KB, auto-approved (trusted).
    Returns the doc_id, or None on empty/failed."""
    if not text or not text.strip():
        return None
    res = await subsystem.pipeline.ingest_text(
        text=text, title=title, source_type=source_type, namespace=ns,
        bucket_ids=[bucket_id], project_id=project_id,
    )
    # Auto-approve (only approved docs are retrievable). A dedup skip means it
    # already exists — leave its status untouched.
    if not res.skipped:
        await subsystem.knowledge_store.set_document_status(
            res.doc_id, "approved", curated_by="auto-ingest"
        )
    logger.info(
        "kb_artifact_ingested", project_id=project_id, title=title,
        source_type=source_type, skipped=res.skipped, chunks=res.chunks,
    )
    return str(res.doc_id)


def _render_tasks(tasks: list[Any]) -> str:
    lines = ["# Project Task List", ""]
    for t in tasks:
        lines.append(
            f"## {getattr(t, 'ordinal', '?')}. {getattr(t, 'title', 'Untitled')} "
            f"[{getattr(t, 'task_type', '')}/{getattr(t, 'priority', '')}]"
        )
        desc = getattr(t, "description", "") or ""
        if desc:
            lines.append(desc)
        lines.append("")
    return "\n".join(lines)


async def _ingest_artifact(
    subsystem: Any, state: Any, project_id: str, kind: ArtifactKind, title: str,
) -> None:
    art = await state.get_artifact(project_id, kind)
    if art is None or not getattr(art, "content", None):
        return
    ns, bucket_id = await _ensure_kb(subsystem, project_id)
    await _ingest(
        subsystem, ns, bucket_id, project_id,
        text=art.content, title=title, source_type=kind.value,
    )


async def _ingest_tasks(subsystem: Any, state: Any, project_id: str) -> None:
    from src.models.base import ArtifactStatus

    tasks = await state.list_tasks_for_project(
        project_id, list_status=ArtifactStatus.FINALIZED
    )
    if not tasks:
        return
    ns, bucket_id = await _ensure_kb(subsystem, project_id)
    await _ingest(
        subsystem, ns, bucket_id, project_id,
        text=_render_tasks(tasks), title="Task List", source_type="tasks",
    )


async def _ingest_research(subsystem: Any, state: Any, data: dict[str, Any]) -> None:
    request_id = data.get("request_id")
    if not request_id:
        return
    req = await state.get_request(request_id)
    project_id = getattr(req, "project_id", None) if req else None
    if not project_id or project_id == UNASSIGNED_PROJECT_ID:
        return
    ns, bucket_id = await _ensure_kb(subsystem, project_id)
    # Published research lands under docs/research/<REQ-ID>-<slug>/*.md.
    folders = sorted(_RESEARCH_ROOT.glob(f"{request_id}*")) if _RESEARCH_ROOT.is_dir() else []
    ingested = 0
    for folder in folders:
        for md in sorted(folder.glob("*.md")):
            try:
                text = md.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
            await _ingest(
                subsystem, ns, bucket_id, project_id,
                text=text, title=f"Research: {md.name}", source_type="research_output",
            )
            ingested += 1
    if ingested == 0:
        # Nothing readable on disk — record a pointer doc so agents know
        # research exists + where (commit_url).
        files = data.get("files") or []
        ptr = (
            f"# Research published for {request_id}\n\n"
            f"Commit: {data.get('commit_url') or data.get('commit_sha') or 'n/a'}\n\n"
            "Files:\n" + "\n".join(f"- {f}" for f in files)
        )
        await _ingest(
            subsystem, ns, bucket_id, project_id,
            text=ptr, title=f"Research index: {request_id}", source_type="research_output",
        )


async def _ingest_commit(subsystem: Any, state: Any, data: dict[str, Any]) -> None:
    request_id = data.get("request_id")
    if not request_id:
        return
    req = await state.get_request(request_id)
    project_id = getattr(req, "project_id", None) if req else None
    if not project_id or project_id == UNASSIGNED_PROJECT_ID:
        return
    files = list(data.get("files") or [])
    ns, bucket_id = await _ensure_kb(subsystem, project_id)
    # Always ingest a manifest (what was built, where) — cheap + always works.
    manifest = (
        f"# Code commit {data.get('commit_sha') or ''} ({request_id})\n\n"
        f"{len(files)} file(s) changed:\n" + "\n".join(f"- {f}" for f in files)
    )
    await _ingest(
        subsystem, ns, bucket_id, project_id,
        text=manifest, title=f"Commit manifest: {request_id}", source_type="code",
    )
    # Best-effort: ingest the bodies of changed text files that resolve under
    # the platform-repo root. Per-workspace roots come in KB-19; for now this
    # covers platform-layout projects and soft-skips the rest.
    for rel in files[:_MAX_COMMIT_FILES]:
        p = (_CODE_ROOT / rel)
        if p.suffix not in _TEXT_SUFFIXES or not p.is_file():
            continue
        try:
            if p.stat().st_size > _MAX_FILE_BYTES:
                continue
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        await _ingest(
            subsystem, ns, bucket_id, project_id,
            text=text, title=str(rel), source_type="code",
        )


def make_kb_artifact_ingest_handler(
    subsystem: Any, state: Any
) -> Callable[[str, dict[str, Any]], Awaitable[None]]:
    """Return an EventEmitter-compatible ``async handler(event_type, data)``
    that auto-ingests approved project artifacts into the project KB."""

    async def handler(event_type: str, data: dict[str, Any]) -> None:
        if subsystem is None or not getattr(subsystem, "available", False):
            return

        async def _work() -> None:
            if event_type == "project.prd_finalized":
                await _ingest_artifact(subsystem, state, data["project_id"],
                                       ArtifactKind.PRD, "PRD")
            elif event_type == "project.api_spec_finalized":
                await _ingest_artifact(subsystem, state, data["project_id"],
                                       ArtifactKind.API_SPEC, "API Spec")
            elif event_type == "project.tasks_finalized":
                await _ingest_tasks(subsystem, state, data["project_id"])
            elif event_type == "research_publish.completed":
                await _ingest_research(subsystem, state, data)
            elif event_type == "code_commit.completed":
                await _ingest_commit(subsystem, state, data)

        # KB-33 — route through the ingestion dispatcher: inline by default,
        # off the event path (background/queue) at scale. Soft-fail either way.
        try:
            dispatcher = getattr(subsystem, "ingest_dispatcher", None)
            if dispatcher is not None:
                await dispatcher.submit(_work())
            else:
                await _work()
        except Exception as e:  # noqa: BLE001 — never block the artifact pipeline
            logger.warning(
                "kb_artifact_ingest_failed", event=event_type, error=str(e),
            )

    return handler
