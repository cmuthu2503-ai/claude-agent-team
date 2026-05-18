"""Project endpoints (PM-09).

CRUD + list + detail for the project management feature
(docs/prd-projects-feature.md). RBAC per PRD §10:
- any authenticated user: list, view, create, edit name/description/visual fields
- admin only: archive/unarchive, hard-delete, reassign lead to a non-self user

The immutable Unassigned project (PM-14) is blocked from edits/deletes via
`assert_not_unassigned()` from project_validation.
"""

from __future__ import annotations

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
    Project,
    ProjectStatus,
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
