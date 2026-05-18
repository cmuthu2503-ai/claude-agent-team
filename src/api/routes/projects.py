"""Project endpoints (PM-09).

CRUD + list + detail for the project management feature
(docs/prd-projects-feature.md). RBAC per PRD §10:
- any authenticated user: list, view, create, edit name/description/visual fields
- admin only: archive/unarchive, hard-delete, reassign lead to a non-self user

The immutable Unassigned project (PM-14) is blocked from edits/deletes via
`assert_not_unassigned()` from project_validation.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.auth.service import get_current_user, require_role
from src.core import project_templates as templates_mod
from src.core.project_validation import (
    assert_not_unassigned,
    validate_color,
    validate_description,
    validate_icon,
    validate_name,
    validate_repo_url,
    validate_tags,
    validate_target_date,
)
from src.models.base import (
    ArtifactKind,
    ArtifactStatus,
    Project,
    ProjectArtifact,
    ProjectStatus,
    ProjectTask,
    TaskStatus,
    UNASSIGNED_PROJECT_ID,
)

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


# ─────────────────────────────── Request bodies ───────────────────────────

class CreateProjectBody(BaseModel):
    name: str
    description: str | None = None
    color: str | None = None
    icon: str | None = None
    tags: list[str] | None = None
    lead_user_id: str | None = None
    repo_url: str | None = None
    default_team: Literal["engineering", "research", "content"] | None = None
    target_date: str | None = None  # ISO date string
    template_id: str | None = None


class UpdateProjectBody(BaseModel):
    name: str | None = None
    description: str | None = None
    status: Literal["active", "archived"] | None = None
    color: str | None = None
    icon: str | None = None
    tags: list[str] | None = None
    lead_user_id: str | None = None
    repo_url: str | None = None
    default_team: Literal["engineering", "research", "content"] | None = None
    target_date: str | None = None
    template_id: str | None = None


# ─────────────────────────────── Serializers ──────────────────────────────

def _serialize(p: Project, *, with_stats: dict[str, int] | None = None) -> dict[str, Any]:
    """Project → JSON dict. Optional `with_stats` is merged in for the detail
    view; the list view passes counts separately."""
    out: dict[str, Any] = {
        "project_id": p.project_id,
        "name": p.name,
        "description": p.description,
        "status": p.status,
        "color": p.color,
        "icon": p.icon,
        "tags": p.tags,
        "lead_user_id": p.lead_user_id,
        "repo_url": p.repo_url,
        "default_team": p.default_team,
        "target_date": p.target_date.isoformat() if p.target_date else None,
        "template_id": p.template_id,
        "created_by": p.created_by,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }
    if with_stats:
        out["stats"] = with_stats
    return out


# ─────────────────────────────── Routes ───────────────────────────────────

@router.get("/templates")
async def list_templates(user: dict = Depends(get_current_user)):
    """PRJ-016 / PM-08 — return all templates the Create Project form
    can pre-fill into the new project's `template_id`."""
    return {
        "data": [t.to_dict() for t in templates_mod.all_templates()],
        "meta": None,
        "error": None,
    }


@router.get("")
async def list_projects(
    request: Request,
    include_archived: bool = False,
    user: dict = Depends(get_current_user),
):
    state = request.app.state.state_store
    projects = await state.list_projects(include_archived=include_archived)
    # Augment each list-row with quick counts so the front-end doesn't
    # have to N+1-fetch the detail endpoint per row.
    out = []
    for p in projects:
        stats = await state.count_requests_for_project(p.project_id)
        out.append(_serialize(p, with_stats=stats))
    return {"data": out, "meta": None, "error": None}


@router.post("", status_code=201)
async def create_project(
    body: CreateProjectBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    state = request.app.state.state_store
    try:
        name = validate_name(body.name)
        description = validate_description(body.description)
        color = validate_color(body.color)
        icon = validate_icon(body.icon)
        tags = validate_tags(body.tags)
        repo_url = validate_repo_url(body.repo_url)
        target_date = validate_target_date(body.target_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # PRJ-007 — uniqueness against active projects
    existing = await state.find_project_by_name(name, active_only=True)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A project named {name!r} already exists.",
        )

    # PRJ-016 — template_id must reference a real template (or be None)
    if body.template_id and templates_mod.get_template(body.template_id) is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown template_id {body.template_id!r}.",
        )

    project = Project(
        project_id=f"proj-{uuid.uuid4().hex[:8]}",
        name=name,
        description=description,
        status=ProjectStatus.ACTIVE,
        color=color,
        icon=icon,
        tags=tags,
        # Default lead to caller (PRJ-012)
        lead_user_id=body.lead_user_id or user.get("user_id"),
        repo_url=repo_url,
        default_team=body.default_team,
        target_date=target_date,
        template_id=body.template_id,
        created_by=user.get("user_id"),
    )
    await state.create_project(project)

    # PM-15 — lifecycle event
    events = request.app.state.events
    await events.emit("project.created", {
        "project_id": project.project_id,
        "name": project.name,
        "created_by": project.created_by,
    })
    return {"data": _serialize(project), "meta": None, "error": None}


@router.get("/{project_id}")
async def get_project_detail(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    state = request.app.state.state_store
    project = await state.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found.")
    reqs = await state.get_requests_for_project(project_id)
    stats = await state.count_requests_for_project(project_id)
    # Recent documents — pull the latest 10 across all requests in the project.
    docs: list[dict[str, Any]] = []
    for r in reqs[:20]:  # cap the join — don't scan 1000 requests for the recent-docs panel
        for d in await state.get_documents_for_request(r.request_id):
            docs.append({
                "document_id": d.document_id,
                "request_id": d.request_id,
                "doc_type": d.doc_type,
                "title": d.title,
                "agent_id": d.agent_id,
                "version": d.version,
                "created_at": d.created_at.isoformat(),
            })
    docs.sort(key=lambda x: x["created_at"], reverse=True)
    docs = docs[:10]

    # Template starter checklist (PRJ-017)
    template_payload: dict[str, Any] | None = None
    if project.template_id:
        tpl = templates_mod.get_template(project.template_id)
        if tpl:
            template_payload = tpl.to_dict()

    return {
        "data": {
            **_serialize(project, with_stats=stats),
            "requests": [
                {
                    "request_id": r.request_id,
                    "description": r.description,
                    "task_type": r.task_type,
                    "priority": r.priority,
                    "status": r.status,
                    "created_at": r.created_at.isoformat(),
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                }
                for r in reqs
            ],
            "recent_documents": docs,
            "template": template_payload,
        },
        "meta": None,
        "error": None,
    }


@router.patch("/{project_id}")
async def update_project(
    project_id: str,
    body: UpdateProjectBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))

    project = await state.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found.")

    # PRJ-005 — admin gates on status change + lead reassignment to other user
    is_admin = user.get("role") == "admin"
    if body.status and body.status != project.status and not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Only admins can archive or unarchive a project.",
        )
    if (
        body.lead_user_id is not None
        and body.lead_user_id != project.lead_user_id
        and body.lead_user_id != user.get("user_id")
        and not is_admin
    ):
        raise HTTPException(
            status_code=403,
            detail="Only admins can reassign the project lead to a different user.",
        )

    # Apply each field if the body supplied it.
    try:
        if body.name is not None:
            new_name = validate_name(body.name)
            if new_name.lower() != project.name.lower():
                other = await state.find_project_by_name(new_name, active_only=True)
                if other is not None and other.project_id != project_id:
                    raise HTTPException(
                        status_code=409,
                        detail=f"A project named {new_name!r} already exists.",
                    )
            project.name = new_name
        if body.description is not None:
            project.description = validate_description(body.description)
        if body.status is not None:
            project.status = ProjectStatus(body.status)
        if body.color is not None:
            project.color = validate_color(body.color)
        if body.icon is not None:
            project.icon = validate_icon(body.icon)
        if body.tags is not None:
            project.tags = validate_tags(body.tags)
        if body.lead_user_id is not None:
            project.lead_user_id = body.lead_user_id or None
        if body.repo_url is not None:
            project.repo_url = validate_repo_url(body.repo_url)
        if body.default_team is not None:
            project.default_team = body.default_team
        if body.target_date is not None:
            # Skip the future-date check on edit — past dates are allowed
            # so the "Overdue" UI state can render.
            project.target_date = None if not body.target_date else (
                datetime.fromisoformat(body.target_date)
            )
        if body.template_id is not None:
            if body.template_id and templates_mod.get_template(body.template_id) is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown template_id {body.template_id!r}.",
                )
            project.template_id = body.template_id or None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await state.update_project(project)

    # PM-15
    events = request.app.state.events
    event_type = (
        "project.archived"
        if body.status == "archived" and project.status == ProjectStatus.ARCHIVED
        else "project.updated"
    )
    await events.emit(event_type, {
        "project_id": project.project_id,
        "name": project.name,
    })
    return {"data": _serialize(project), "meta": None, "error": None}


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    request: Request,
    admin: dict = Depends(require_role("admin")),
):
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "deleted")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))

    project = await state.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found.")

    # PRJ-006 — refuse if non-empty; force reassign-or-delete-first
    counts = await state.count_requests_for_project(project_id)
    if counts["total"] > 0:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "project not empty",
                "request_count": counts["total"],
                "hint": "Reassign or delete the requests in this project first.",
            },
        )

    await state.delete_project(project_id)
    events = request.app.state.events
    await events.emit("project.deleted", {"project_id": project_id, "name": project.name})
    return None


# ─────────────────────── Project-driven Build: Brief + PRD ───────────────────
# PDB-06 + PDB-07. The brief is plain text (user-authored, 50–4000 chars).
# The PRD is markdown generated by the prd_author agent from the brief +
# project metadata, then editable in the UI. Both are versioned via the
# `project_artifacts` table; only one row per (project_id, kind) is
# `status='finalized'` at any time (enforced inside StateStore.finalize_artifact).


class BriefBody(BaseModel):
    content: str


class PRDPatchBody(BaseModel):
    content: str | None = None
    status: Literal["finalized"] | None = None


_BRIEF_MIN = 50
_BRIEF_MAX = 4000
_PRD_MAX = 50_000


def _artifact_to_dict(art: ProjectArtifact) -> dict[str, Any]:
    return {
        "artifact_id": art.artifact_id,
        "project_id": art.project_id,
        "kind": art.kind,
        "version": art.version,
        "status": art.status,
        "content": art.content,
        "created_by": art.created_by,
        "created_at": art.created_at.isoformat(),
        "updated_at": art.updated_at.isoformat() if art.updated_at else None,
        "finalized_at": art.finalized_at.isoformat() if art.finalized_at else None,
        "finalized_by": art.finalized_by,
    }


async def _require_project(state, project_id: str) -> Project:
    """404 helper used by every artifact route below."""
    p = await state.get_project(project_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found.")
    return p


# ── Brief ────────────────────────────────────────────────────────────────

@router.get("/{project_id}/brief")
async def get_brief(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Return the latest brief for this project, or 404 if none."""
    state = request.app.state.state_store
    await _require_project(state, project_id)
    art = await state.get_artifact(project_id, ArtifactKind.BRIEF)
    if art is None:
        raise HTTPException(status_code=404, detail="No brief yet.")
    return {"data": _artifact_to_dict(art), "meta": None, "error": None}


@router.put("/{project_id}/brief")
async def put_brief(
    project_id: str,
    body: BriefBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Upsert the project's brief. Always keeps at most one row per
    project. Validates length 50–4000 chars (PRD §4.1 PB-002)."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    await _require_project(state, project_id)
    content = (body.content or "").strip()
    if len(content) < _BRIEF_MIN:
        raise HTTPException(
            status_code=400,
            detail=f"Brief must be at least {_BRIEF_MIN} characters (got {len(content)}).",
        )
    if len(content) > _BRIEF_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"Brief must be at most {_BRIEF_MAX} characters (got {len(content)}).",
        )

    existing = await state.get_artifact(project_id, ArtifactKind.BRIEF)
    if existing is None:
        art = ProjectArtifact(
            artifact_id=f"art-{uuid.uuid4().hex[:12]}",
            project_id=project_id,
            kind=ArtifactKind.BRIEF,
            version=1,
            status=ArtifactStatus.DRAFT,
            content=content,
            created_by=user.get("user_id"),
        )
        await state.create_artifact(art)
    else:
        await state.update_artifact_content(existing.artifact_id, content)
        art = await state.get_artifact(project_id, ArtifactKind.BRIEF)

    return {"data": _artifact_to_dict(art), "meta": None, "error": None}


# ── PRD ──────────────────────────────────────────────────────────────────

@router.get("/{project_id}/prd")
async def get_prd(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Return the latest PRD artifact (any status). 404 if none yet."""
    state = request.app.state.state_store
    await _require_project(state, project_id)
    art = await state.get_artifact(project_id, ArtifactKind.PRD)
    if art is None:
        raise HTTPException(status_code=404, detail="No PRD yet.")
    return {"data": _artifact_to_dict(art), "meta": None, "error": None}


@router.post("/{project_id}/prd/generate", status_code=201)
async def generate_prd(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Run `prd_author` (single-shot, no workflow) using the project's
    brief + metadata as input. Creates a NEW PRD version each time —
    older versions stay in the table but the UI surfaces only the latest.
    """
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    project = await _require_project(state, project_id)

    brief = await state.get_artifact(project_id, ArtifactKind.BRIEF)
    if brief is None or len(brief.content.strip()) < _BRIEF_MIN:
        raise HTTPException(
            status_code=400,
            detail="Save a project brief (≥50 chars) before generating a PRD.",
        )

    # Reserve the artifact row up front so the cost-attribution row
    # recorded inside single_agent_call() has a real artifact_id to point at.
    existing = await state.list_artifacts(project_id, ArtifactKind.PRD)
    next_version = (max((a.version for a in existing), default=0)) + 1
    new_art = ProjectArtifact(
        artifact_id=f"art-{uuid.uuid4().hex[:12]}",
        project_id=project_id,
        kind=ArtifactKind.PRD,
        version=next_version,
        status=ArtifactStatus.DRAFT,
        content="",
        created_by=user.get("user_id"),
    )
    await state.create_artifact(new_art)

    executor = getattr(request.app.state, "agent_executor", None)
    if executor is None:
        raise HTTPException(
            status_code=503,
            detail="Agent executor not configured — set ANTHROPIC_AWS_API_KEY + ANTHROPIC_AWS_WORKSPACE_ID.",
        )

    prompt = (
        f"You are drafting a Product Requirements Document for a project named "
        f"{project.name!r}.\n\n"
        f"Project description: {project.description or '(none provided)'}\n\n"
        f"Project brief from the lead:\n"
        f"---\n{brief.content}\n---\n\n"
        f"Write a complete PRD in markdown following the existing project "
        f"convention: top-level title, Document Information table, Table of "
        f"Contents, numbered sections (Context, Executive Summary, Goals & "
        f"Non-Goals, User Stories, Functional Requirements with REQ-XXX IDs, "
        f"Data Model, API Surface, UI Design, Permissions, Edge Cases, Out of "
        f"Scope, Open Questions, Implementation Phases, Revision History). "
        f"Be specific and avoid placeholder text. The user will edit before "
        f"finalizing — so it's OK to lean opinionated. Output the markdown "
        f"only, no commentary before or after."
    )

    try:
        result = await executor.single_agent_call(
            agent_id="prd_specialist",
            prompt=prompt,
            project_artifact_id=new_art.artifact_id,
        )
    except Exception as e:
        # Generation failed — the empty draft row remains, with the failure
        # surfaced to the user. They can hit Regenerate to retry.
        raise HTTPException(status_code=502, detail=f"PRD generation failed: {e}")

    text = (result.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=502, detail="Agent returned an empty PRD.")

    await state.update_artifact_content(new_art.artifact_id, text)
    saved = await state.get_artifact(project_id, ArtifactKind.PRD)

    events = request.app.state.events
    await events.emit("project.prd_generated", {
        "project_id": project_id,
        "artifact_id": saved.artifact_id,
        "version": saved.version,
    })
    return {"data": _artifact_to_dict(saved), "meta": None, "error": None}


@router.patch("/{project_id}/prd")
async def patch_prd(
    project_id: str,
    body: PRDPatchBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Either save-draft (`content`) or finalize (`status='finalized'`)."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    await _require_project(state, project_id)

    art = await state.get_artifact(project_id, ArtifactKind.PRD)
    if art is None:
        raise HTTPException(status_code=404, detail="No PRD to update.")

    if body.content is not None:
        content = body.content
        if len(content) > _PRD_MAX:
            raise HTTPException(
                status_code=400,
                detail=f"PRD must be at most {_PRD_MAX} characters (got {len(content)}).",
            )
        await state.update_artifact_content(art.artifact_id, content)

    if body.status == "finalized":
        # `finalize_artifact` archives any prior finalized row for the same
        # (project_id, kind) atomically and stamps timestamps on this one.
        art = await state.finalize_artifact(art.artifact_id, finalized_by=user.get("user_id"))
        events = request.app.state.events
        await events.emit("project.prd_finalized", {
            "project_id": project_id,
            "artifact_id": art.artifact_id,
            "version": art.version,
        })
    else:
        art = await state.get_artifact(project_id, ArtifactKind.PRD)

    return {"data": _artifact_to_dict(art), "meta": None, "error": None}


# ─────────────────────── Project-driven Build: Task List ─────────────────────
# PDB-16 / PDB-17 / PDB-18. Generates a structured task list from the finalized
# PRD via user_story_author (single-shot, no workflow). Output is parsed into
# `project_tasks` rows. The user can edit each row inline and finalize when
# happy; the dispatcher in Phase C will read these rows to create Requests.


# ── REQ validation constants ────────────────────────────────────────────
# Valid request task_types are the StrEnum members in TaskType (src/models/base.py).
# Pinning them here keeps the route validation cheap and obvious.
_VALID_TASK_TYPES = {
    "feature_request", "bug_report", "doc_request",
    "demo_request", "research_request", "content_request",
}
_VALID_PRIORITIES = {"low", "medium", "high"}
# Known agent IDs the dispatcher will recognize. Soft validation — we still
# accept anything but warn so the UI doesn't blow up on novel agents.
_KNOWN_AGENTS = {
    "backend_specialist", "frontend_specialist", "tester_specialist",
    "code_reviewer", "devops_specialist", "content_creator",
    "research_specialist", "prd_specialist", "user_story_author",
}


class TaskPatchBody(BaseModel):
    title: str | None = None
    description: str | None = None
    task_type: str | None = None
    priority: str | None = None
    estimated_agent: str | None = None
    ordinal: int | None = None


def _task_to_dict(t: ProjectTask) -> dict[str, Any]:
    return {
        "task_id": t.task_id,
        "project_id": t.project_id,
        "list_version": t.list_version,
        "list_status": t.list_status,
        "ordinal": t.ordinal,
        "title": t.title,
        "description": t.description,
        "task_type": t.task_type,
        "priority": t.priority,
        "estimated_agent": t.estimated_agent,
        "task_status": t.task_status,
        "request_id": t.request_id,
        "amended": t.amended,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def _parse_task_list(agent_output: str) -> tuple[list[dict[str, Any]], str]:
    """Extract a list of task dicts from the user_story_author output.

    Strategy (PDB-17):
      1. Prefer a fenced ```json block containing an array of objects. This
         is what the updated prompt is supposed to emit.
      2. Fall back to a markdown-section parser: look for `### N. <title>`
         or `## <title>` headers and treat each as one task.
      3. Returns ([], "no_tasks_found") if nothing usable is found.

    Returns (tasks, parse_mode) where parse_mode is one of
    "json", "markdown", "empty", "json_malformed_used_markdown".
    """
    text = agent_output or ""

    # ── Strategy 1: fenced JSON ────────────────────────────────────────
    json_pattern = re.compile(r"```json\s*\n(.+?)\n```", re.DOTALL)
    json_match = json_pattern.search(text)
    if json_match:
        block = json_match.group(1).strip()
        try:
            parsed = json.loads(block)
            if isinstance(parsed, list) and all(isinstance(x, dict) for x in parsed):
                return _normalize_task_dicts(parsed), "json"
            if isinstance(parsed, dict) and "tasks" in parsed and isinstance(parsed["tasks"], list):
                return _normalize_task_dicts(parsed["tasks"]), "json"
        except json.JSONDecodeError:
            pass  # fall through to markdown

    # ── Strategy 2: markdown headings ──────────────────────────────────
    # Looks for "### 1. Title" / "## Title" / "- Title" patterns at line start.
    md_tasks: list[dict[str, Any]] = []
    heading_pattern = re.compile(
        r"^(?:#{2,4}\s+(?:\d+\.\s+)?|\d+\.\s+|-\s+\*\*)(.+?)(?:\*\*)?\s*$",
        re.MULTILINE,
    )
    titles: list[str] = []
    for m in heading_pattern.finditer(text):
        title = m.group(1).strip()
        # Skip section-style headings that aren't tasks
        if title.lower() in {"user stories", "tasks", "task list", "stories"}:
            continue
        if len(title) < 4 or len(title) > 200:
            continue
        titles.append(title)
    for i, title in enumerate(titles[:50]):  # cap to 50 tasks
        md_tasks.append({
            "title": title,
            "description": "",
            "task_type": "feature_request",
            "priority": "medium",
            "estimated_agent": None,
        })
    if md_tasks:
        return md_tasks, ("json_malformed_used_markdown" if json_match else "markdown")

    return [], "empty"


def _normalize_task_dicts(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coerce parsed dicts into the strict shape we'll store. Drops rows
    that lack a title; defaults unknown fields rather than rejecting."""
    out: list[dict[str, Any]] = []
    for item in raw:
        title = str(item.get("title") or item.get("name") or "").strip()
        if not title:
            continue
        tt = str(item.get("task_type") or "feature_request").strip()
        if tt not in _VALID_TASK_TYPES:
            tt = "feature_request"
        pr = str(item.get("priority") or "medium").lower().strip()
        if pr not in _VALID_PRIORITIES:
            pr = "medium"
        agent = item.get("estimated_agent") or item.get("agent")
        if agent and str(agent) not in _KNOWN_AGENTS:
            # keep the value, just don't validate strictly — see _KNOWN_AGENTS comment
            agent = str(agent)
        out.append({
            "title": title[:200],
            "description": str(item.get("description") or "")[:2000],
            "task_type": tt,
            "priority": pr,
            "estimated_agent": agent,
        })
    return out


# ── Tasks endpoints ──────────────────────────────────────────────────────

@router.get("/{project_id}/tasks")
async def get_tasks(
    project_id: str,
    request: Request,
    version: int | None = None,
    user: dict = Depends(get_current_user),
):
    """List tasks for this project. Default returns the latest non-archived
    version; pass ?version= for a specific generation."""
    state = request.app.state.state_store
    await _require_project(state, project_id)
    tasks = await state.list_tasks_for_project(project_id, list_version=version)
    return {
        "data": [_task_to_dict(t) for t in tasks],
        "meta": {"count": len(tasks)},
        "error": None,
    }


@router.post("/{project_id}/tasks/generate", status_code=201)
async def generate_tasks(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Run `user_story_author` (single-shot) on the finalized PRD. Replaces
    any existing draft (PRD §4.3 TSK-005). Blocked when a finalized list
    already exists — caller must archive that list first (TSK-006)."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    await _require_project(state, project_id)

    prd = await state.get_artifact(project_id, ArtifactKind.PRD)
    if prd is None or prd.status != ArtifactStatus.FINALIZED:
        raise HTTPException(
            status_code=409,
            detail="Finalize the PRD before generating a task list.",
        )

    # TSK-006: refuse if a finalized list already exists.
    existing_final = await state.list_tasks_for_project(
        project_id, list_status=ArtifactStatus.FINALIZED,
    )
    if existing_final:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "task list already finalized",
                "hint": "Archive the current task list first (POST /tasks/archive), then regenerate.",
            },
        )

    # TSK-005: discard any existing draft for this project.
    existing_draft = await state.list_tasks_for_project(
        project_id, list_status=ArtifactStatus.DRAFT,
    )
    if existing_draft:
        # All draft tasks share a list_version (they were all generated together).
        await state.delete_task_list_draft(project_id, existing_draft[0].list_version)

    # Next version = max(all versions) + 1, where archived versions still count.
    all_tasks = await state.list_tasks_for_project(
        project_id, list_status=ArtifactStatus.ARCHIVED,
    )
    archived_versions = {t.list_version for t in all_tasks}
    finalized_tasks = await state.list_tasks_for_project(
        project_id, list_status=ArtifactStatus.FINALIZED,
    )
    finalized_versions = {t.list_version for t in finalized_tasks}
    next_version = max(archived_versions | finalized_versions, default=0) + 1

    executor = getattr(request.app.state, "agent_executor", None)
    if executor is None:
        raise HTTPException(
            status_code=503,
            detail="Agent executor not configured.",
        )

    prompt = (
        "You are breaking a finalized PRD into a flat list of buildable tasks "
        "for an AI agent team to execute.\n\n"
        "## PRD\n"
        f"{prd.content}\n\n"
        "## Output format\n"
        "Emit a single fenced ```json``` block containing an ARRAY of task "
        "objects. No prose before or after. Each object must have these keys:\n"
        "  - title: string (under 100 chars, imperative — \"Build X\", \"Add Y\")\n"
        "  - description: string (1-3 sentences describing what done looks like)\n"
        "  - task_type: one of feature_request, bug_report, doc_request, demo_request, research_request, content_request\n"
        "  - priority: one of low, medium, high\n"
        "  - estimated_agent: one of backend_specialist, frontend_specialist, tester_specialist, code_reviewer, devops_specialist, content_creator, research_specialist (best fit; or null if uncertain)\n\n"
        "Aim for 5-15 tasks. Each task should be independently dispatchable "
        "to a per-task workflow (the user will dispatch them via chat). "
        "Order tasks so an earlier task isn't blocked by a later one when "
        "possible. Do NOT include cross-task dependencies in v1.\n\n"
        "Example:\n"
        "```json\n"
        '[\n'
        '  {"title": "Add user table migration", "description": "Create users table with email, password_hash, role columns.", "task_type": "feature_request", "priority": "high", "estimated_agent": "backend_specialist"},\n'
        '  {"title": "Build login form", "description": "Email + password fields, calls POST /auth/login.", "task_type": "feature_request", "priority": "high", "estimated_agent": "frontend_specialist"}\n'
        ']\n'
        "```"
    )

    try:
        result = await executor.single_agent_call(
            agent_id="user_story_author",
            prompt=prompt,
            project_artifact_id=None,  # tasks aren't an artifact row; cost is unattributed
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Task generation failed: {e}")

    text = (result.get("text") or "").strip()
    parsed, mode = _parse_task_list(text)
    if not parsed:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "no_tasks_parsed",
                "hint": "Agent returned no parseable tasks. Try regenerating after refining the PRD.",
                "raw_output_first_500": text[:500],
            },
        )

    # Persist rows.
    now = datetime.utcnow()
    saved: list[ProjectTask] = []
    for i, raw_task in enumerate(parsed):
        t = ProjectTask(
            task_id=f"T-{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            list_version=next_version,
            list_status=ArtifactStatus.DRAFT,
            ordinal=i + 1,
            title=raw_task["title"],
            description=raw_task["description"],
            task_type=raw_task["task_type"],
            priority=raw_task["priority"],
            estimated_agent=raw_task.get("estimated_agent"),
            task_status=TaskStatus.BACKLOG,
            created_at=now,
        )
        await state.create_task(t)
        saved.append(t)

    return {
        "data": [_task_to_dict(t) for t in saved],
        "meta": {
            "list_version": next_version,
            "parse_mode": mode,
            "count": len(saved),
        },
        "error": None,
    }


@router.patch("/{project_id}/tasks/{task_id}")
async def patch_task(
    project_id: str,
    task_id: str,
    body: TaskPatchBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Inline edit a single task. Only mutates fields the caller supplied.
    Server rejects unknown task_type / priority values."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    task = await state.get_task(task_id)
    if task is None or task.project_id != project_id:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found in project.")

    fields: dict[str, Any] = {}
    if body.title is not None:
        if not body.title.strip():
            raise HTTPException(status_code=400, detail="Title must be non-empty.")
        fields["title"] = body.title.strip()[:200]
    if body.description is not None:
        fields["description"] = body.description[:2000]
    if body.task_type is not None:
        if body.task_type not in _VALID_TASK_TYPES:
            raise HTTPException(status_code=400, detail=f"task_type must be one of {sorted(_VALID_TASK_TYPES)}.")
        fields["task_type"] = body.task_type
    if body.priority is not None:
        if body.priority not in _VALID_PRIORITIES:
            raise HTTPException(status_code=400, detail=f"priority must be one of {sorted(_VALID_PRIORITIES)}.")
        fields["priority"] = body.priority
    if body.estimated_agent is not None:
        fields["estimated_agent"] = body.estimated_agent or None
    if body.ordinal is not None:
        fields["ordinal"] = body.ordinal

    updated = await state.update_task(task_id, fields)
    return {"data": _task_to_dict(updated), "meta": None, "error": None}


@router.post("/{project_id}/tasks/finalize")
async def finalize_tasks(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """TSK-008. Flips the current draft list to finalized atomically (any
    previously-finalized version is archived first). Rejects empty drafts."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    await _require_project(state, project_id)

    draft_rows = await state.list_tasks_for_project(
        project_id, list_status=ArtifactStatus.DRAFT,
    )
    if not draft_rows:
        raise HTTPException(
            status_code=400,
            detail="No draft task list to finalize.",
        )

    # All draft rows share a list_version.
    list_version = draft_rows[0].list_version
    await state.finalize_task_list(project_id, list_version)

    events = request.app.state.events
    await events.emit("project.tasks_finalized", {
        "project_id": project_id,
        "list_version": list_version,
        "task_count": len(draft_rows),
    })

    finalized = await state.list_tasks_for_project(
        project_id, list_version=list_version,
    )
    return {
        "data": [_task_to_dict(t) for t in finalized],
        "meta": {"list_version": list_version, "count": len(finalized)},
        "error": None,
    }


@router.post("/{project_id}/tasks/archive")
async def archive_tasks(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """TSK-006 escape hatch — archive the current finalized list so a new
    generation can proceed. No-op if nothing's currently finalized."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    await _require_project(state, project_id)

    finalized = await state.list_tasks_for_project(
        project_id, list_status=ArtifactStatus.FINALIZED,
    )
    if not finalized:
        return {"data": None, "meta": {"archived": 0}, "error": None}

    list_version = finalized[0].list_version
    await state.archive_task_list(project_id, list_version)
    return {"data": None, "meta": {"archived": len(finalized), "list_version": list_version}, "error": None}


# ─────────────────────── Project-driven Build: Dispatch ──────────────────────
# PDB-24. Takes finalized tasks and feeds them into the existing orchestrator
# submit() path, creating one Request per task with source_task_id back-linked.
# The PDB-25 event handler in main.py then maps subsequent request.* events
# onto project_tasks.task_status so the Story Board (project mode) stays live.


class DispatchBody(BaseModel):
    task_ids: list[str]


@router.post("/{project_id}/build/dispatch")
async def dispatch_tasks(
    project_id: str,
    body: DispatchBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """For each task_id: validate it belongs to this project AND its current
    task_status is 'backlog'. Already-dispatched tasks are returned as no-ops
    with the existing request_id (idempotent per BLD-004). Archived or
    unknown task_ids fail the dispatch with a 400 listing the offenders."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    await _require_project(state, project_id)

    if not body.task_ids:
        raise HTTPException(status_code=400, detail="task_ids must contain at least one entry.")

    # Validate every task up front before mutating any state.
    tasks_by_id: dict[str, ProjectTask] = {}
    errors: list[str] = []
    for tid in body.task_ids:
        t = await state.get_task(tid)
        if t is None or t.project_id != project_id:
            errors.append(f"{tid}: not found in this project")
            continue
        if t.list_status != ArtifactStatus.FINALIZED:
            errors.append(f"{tid}: list_status is {t.list_status!r}, must be 'finalized' to dispatch")
            continue
        tasks_by_id[tid] = t
    if errors:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_task_ids", "issues": errors},
        )

    orchestrator = request.app.state.orchestrator
    dispatched: list[dict[str, Any]] = []
    for tid, task in tasks_by_id.items():
        # BLD-004: idempotent. If already dispatched, just echo the link.
        if task.task_status != TaskStatus.BACKLOG and task.request_id:
            dispatched.append({
                "task_id": tid,
                "request_id": task.request_id,
                "status": "already_dispatched",
            })
            continue

        # Submit through the existing orchestrator path so the workflow
        # selection / project-active validation / event emission all match
        # the one-off Submit Request behavior. `source_task_id` is the new
        # back-link that the PDB-25 handler reads to map status updates.
        try:
            req = await orchestrator.submit(
                description=task.description or task.title,
                task_type=task.task_type,
                priority=task.priority,
                created_by=user.get("user_id") or "",
                project_id=project_id,
                source_task_id=tid,
            )
        except ValueError as e:
            # Project archived between validate-and-act, or workflow lookup
            # failed. Mark this task as failed-to-dispatch but keep going
            # for the rest — the user can retry.
            errors.append(f"{tid}: {e}")
            continue

        # Stamp the task itself with the new request_id + status.
        await state.set_task_status(tid, TaskStatus.DISPATCHED, request_id=req.request_id)
        dispatched.append({
            "task_id": tid,
            "request_id": req.request_id,
            "status": "dispatched",
        })

    return {
        "data": {"dispatched": dispatched},
        "meta": {
            "count": len(dispatched),
            "errors": errors if errors else None,
        },
        "error": None,
    }


# ─────────────────────── Project-driven Build: Chat (PDB-37/38) ─────────────


class ChatBody(BaseModel):
    message: str


@router.get("/{project_id}/build/messages")
async def get_build_messages(
    project_id: str,
    request: Request,
    limit: int = 200,
    before: str | None = None,
    user: dict = Depends(get_current_user),
):
    """Return chronological message history for this project's chat session.
    Default: last 200 messages. Use ?before=<message_id>&limit=... for
    older pages."""
    state = request.app.state.state_store
    await _require_project(state, project_id)
    msgs = await state.list_messages_for_project(
        project_id, limit=max(1, min(limit, 500)), before=before,
    )
    return {
        "data": [
            {
                "message_id": m.message_id,
                "role": m.role,
                "content": m.content,
                "tool_calls": m.tool_calls,
                "created_at": m.created_at.isoformat(),
                "created_by": m.created_by,
            }
            for m in msgs
        ],
        "meta": {"count": len(msgs)},
        "error": None,
    }


@router.post("/{project_id}/build/chat")
async def post_build_chat(
    project_id: str,
    body: ChatBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Run one turn of the project_orchestrator chat. Persists the user
    message, runs the tool-use loop (max 5 iterations), persists the
    assistant turn + tool_calls summaries, returns the final reply."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    await _require_project(state, project_id)

    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message cannot be empty.")
    if len(message) > 4000:
        raise HTTPException(status_code=400, detail="message must be ≤ 4000 chars.")

    executor = getattr(request.app.state, "agent_executor", None)
    orchestrator = getattr(request.app.state, "orchestrator", None)
    events = getattr(request.app.state, "events", None)
    if executor is None or orchestrator is None:
        raise HTTPException(status_code=503, detail="Agent executor not configured.")

    from src.core.build_chat import run_chat_turn
    try:
        result = await run_chat_turn(
            state=state,
            executor=executor,
            orchestrator=orchestrator,
            project_id=project_id,
            user_message=message,
            user_id=user.get("user_id"),
            events=events,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Chat turn failed: {e}")

    return {"data": result, "meta": None, "error": None}
