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
from pathlib import Path
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

logger = structlog.get_logger()

from src.auth.service import get_current_user, get_principal, require_role
from src.core import project_templates as templates_mod
from src.core.github_publisher import (
    GitHubPublishError,
    GitHubPublisher,
    GitHubRepoCreateError,
    extract_owner_repo,
)
from src.core.project_validation import (
    assert_not_unassigned,
    project_repo_slug,
    validate_color,
    validate_description,
    validate_icon,
    validate_name,
    validate_repo_url,
    validate_tags,
    validate_target_date,
)
from src.core.project_workspace import (
    delete_host_file,
    delete_project_root,
    project_root_dir,
    render_build_plan_markdown,
    render_finalized_tasks_flat_markdown,
    render_tasks_markdown,
    write_build_plan,
    write_finalized_api_spec,
    write_finalized_prd,
    write_finalized_tasks,
)
from src.core.deploy_drift import ProjectDrift, compute_drift
from src.core.project_deploy_judge import evaluate_project_deploy
from src.models.base import (
    ArtifactKind,
    ArtifactStatus,
    DeployAction,
    DeployDecision,
    DeployDecisionStatus,
    DeployStatus,
    Epic,
    Feature,
    Project,
    ProjectArtifact,
    ProjectStatus,
    ProjectTask,
    TaskStatus,
    UNASSIGNED_PROJECT_ID,
)

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


# ─────────────────────────────── Reference-format loader ──────────────────
# PRD / Tasks generation prompts include a literal copy of a sample
# document as "here's the format I want". Those samples live in
# docs/reference-formats/ inside the repo so they're version-controlled
# alongside the prompts but editable WITHOUT redeploying — the next
# generate-PRD / generate-tasks call reads the latest file content.

_REFERENCE_FORMATS_DIR = Path(__file__).resolve().parents[3] / "docs" / "reference-formats"


def _load_reference_format(name: str, fallback: str = "") -> str:
    """Read a reference-format markdown file from docs/reference-formats/.

    ``name`` is the file basename (e.g. 'prd-template.md'). Returns the
    file contents as a string, or ``fallback`` if the file is missing /
    unreadable so a single bad file doesn't kill generation."""
    try:
        path = _REFERENCE_FORMATS_DIR / name
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("reference_format.missing path=%s", _REFERENCE_FORMATS_DIR / name)
        return fallback
    except Exception as e:  # noqa: BLE001 — soft-fail; generation should still work
        logger.warning(
            "reference_format.read_failed name=%s error=%s", name, e,
        )
        return fallback


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
    # WS-03 — when True (and repo_url is blank), the server auto-creates a
    # private GitHub repo named after the project slug under the GITHUB_TOKEN
    # user's namespace (or GITHUB_PROJECT_ORG if set). On success, the new
    # repo's HTML URL becomes `project.repo_url`. Defaults to True so a
    # fresh project gets a workspace by default.
    create_repo: bool = True
    # Per-project working tree feature — which scaffold template to
    # materialize into C:/ai-projects/<Name>/ at create time. Default
    # 'web-app' (FastAPI backend + Vite/React frontend). The frontend
    # picker should constrain to these three values, but we also reject
    # unknown values on the server.
    kind: Literal["web-app", "api-service", "frontend-app"] = "web-app"


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
    # BPD-25 — per-project toggle. False (default) means dispatch on
    # request.deployed is manual; True means the post-deploy handler
    # auto-fires every newly-unblocked task. Off by default per BPD §2.3.
    auto_dispatch_on_deploy: bool | None = None


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
        # Per-project working tree + deploy fields (the "every project
        # is its own running app" feature). All optional from the
        # frontend's perspective — the deployment card only renders
        # when `kind` is present.
        "kind": p.kind,
        "deploy_backend_port": p.deploy_backend_port,
        "deploy_frontend_port": p.deploy_frontend_port,
        "deploy_status": p.deploy_status,
        "deploy_url": p.deploy_url,
        "deploy_last_started_at": (
            p.deploy_last_started_at.isoformat() if p.deploy_last_started_at else None
        ),
        "deploy_error": p.deploy_error,
        # AI Deploy Judge fields. Surfaced so the Deploy panel can
        # render the "X commits since last deploy" subtitle without a
        # separate fetch, and so the user's preferences textarea has
        # an initial value.
        "last_deploy_commit_sha": p.last_deploy_commit_sha,
        "deploy_judge_preferences": p.deploy_judge_preferences,
        "deploy_pending_action": p.deploy_pending_action,
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
    user: dict = Depends(get_principal),  # HAI-12 — JWT or service token
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

    # WS-04 — auto-create a GitHub repo for this project if the user
    # didn't paste an existing URL and didn't opt out. We do this BEFORE
    # inserting the project row so a failed repo create doesn't leave an
    # orphan project that points at nothing. Reverse order would require
    # an UPDATE-or-rollback dance that's not worth the complexity in v1.
    final_repo_url = repo_url
    if body.create_repo and not repo_url:
        try:
            slug = project_repo_slug(name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        publisher = GitHubPublisher()
        try:
            created = await publisher.create_repo(
                name=slug,
                description=description or f"Workspace for {name}",
                private=True,
            )
            final_repo_url = created["html_url"]
        except GitHubRepoCreateError as e:
            # WS-05 — map GitHub status codes to actionable HTTPException details.
            sc = e.status_code
            msg = str(e)
            if sc == 401 or sc == 403:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "github_repo_create_unauthorized",
                        "message": msg,
                        "hint": "Check that GITHUB_TOKEN is set and has `repo` scope. "
                                "If you don't want auto-creation, set create_repo=false "
                                "and paste an existing repo URL.",
                    },
                ) from e
            if sc == 422:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "github_repo_name_taken",
                        "message": msg,
                        "hint": f"A repo with the slug {slug!r} already exists in your namespace. "
                                "Pick a different project name.",
                    },
                ) from e
            if sc is None:
                # Token misconfigured / unreachable host. Allow project creation
                # WITHOUT a repo so the user isn't blocked entirely; they can
                # backfill via the Project Detail page later.
                logger.warning("github_skipped_repo_unconfigured", message=msg)
                final_repo_url = ""
            else:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": "github_repo_create_failed",
                        "message": msg,
                        "status": sc,
                    },
                ) from e

    # Per-project working tree feature — allocate ports BEFORE inserting
    # the project row so they can be baked into the scaffolded
    # docker-compose.yml. Sequential MAX+1; partial unique indexes on
    # the columns catch any race. Port allocation can't fail in
    # practice (16-bit headroom under the bases) but propagate any
    # error as 500 since it's a system condition.
    try:
        backend_port, frontend_port = await state.allocate_project_ports()
    except Exception as e:  # noqa: BLE001
        logger.error("port_allocation_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Port allocation failed: {e}")

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
        repo_url=final_repo_url,
        default_team=body.default_team,
        target_date=target_date,
        template_id=body.template_id,
        created_by=user.get("user_id"),
        # Per-project working tree fields. Initial state: ports
        # allocated, scaffold about to be materialized below,
        # deploy_status stays at the model default (stopped) until the
        # user clicks Deploy.
        kind=body.kind,
        deploy_backend_port=backend_port,
        deploy_frontend_port=frontend_port,
    )
    await state.create_project(project)

    # ── Scaffold the per-project working tree ────────────────────────
    # Materialize the chosen template into C:/ai-projects/<Name>/ on the
    # host (via the /host/ai-projects bind mount). The scaffolded tree
    # has docker-compose.yml + Dockerfile(s) + a minimal "hello world"
    # app, so the user can click Deploy right after creating the project
    # and see something running before any agent task fires.
    #
    # Soft-fail: if the scaffold fails (mount missing, disk full,
    # permission) we still return 201 and report the error in `meta` so
    # the UI can offer a retro-scaffold button later. The DB row is
    # already in place, so subsequent attempts are recoverable.
    from src.core.project_scaffolder import ProjectScaffolder
    from src.core.project_workspace import project_root_dir

    scaffolder = ProjectScaffolder()
    scaffold_meta: dict[str, Any] = {"ok": False, "skipped": "not attempted"}
    initial_push_meta: dict[str, Any] = {"ok": False, "skipped": "not attempted"}
    try:
        slug = project_repo_slug(name)
    except ValueError:
        slug = name.lower()  # validate_name ran earlier; this can't fail in practice
    substitutions = {
        "PROJECT_NAME": name,
        "PROJECT_SLUG": slug,
        "BACKEND_PORT": str(backend_port),
        "FRONTEND_PORT": str(frontend_port),
    }
    try:
        target_root = project_root_dir(name)
        scaffold_result = scaffolder.scaffold(
            kind=body.kind,
            project_root=target_root,
            substitutions=substitutions,
        )
        scaffold_meta = scaffold_result.as_dict()
        if not scaffold_result.ok:
            logger.warning(
                "scaffold_failed",
                project_id=project.project_id, error=scaffold_result.error,
            )
    except Exception as e:  # noqa: BLE001 — soft-fail catch-all
        logger.exception("scaffold_exception", project_id=project.project_id)
        scaffold_meta = {"ok": False, "error": str(e)}

    # ── Push the scaffold to the project's GitHub repo as initial commit ──
    # The auto_init=true GitHub repo starts with only a README.md. We
    # overlay the scaffold via Trees API — the README from the template
    # replaces the auto-init one. Skipped (with a clear reason) when:
    #   - No repo was created (user pasted their own URL, or token unset)
    #   - The repo_url isn't parseable as a github.com link
    # Soft-fail like the disk scaffold — the local tree stays valid.
    if final_repo_url and scaffold_meta.get("ok"):
        parsed = extract_owner_repo(final_repo_url)
        if not parsed:
            initial_push_meta = {"ok": False, "skipped": "unparseable_repo_url"}
        else:
            target_repo = f"{parsed[0]}/{parsed[1]}"
            try:
                rendered = scaffolder.render_template_files(
                    kind=body.kind, substitutions=substitutions,
                )
                publisher = GitHubPublisher()
                commit_info = await publisher.commit_files(
                    files=rendered,
                    commit_message=(
                        f"chore: initial scaffold ({body.kind})\n\n"
                        f"Scaffolded by the Agent Team platform at project creation.\n"
                        f"Project: {name} ({project.project_id})\n"
                        f"Kind: {body.kind}\n"
                        f"Backend port: {backend_port}\n"
                        f"Frontend port: {frontend_port}\n"
                    ),
                    repo=target_repo,
                )
                initial_push_meta = {
                    "ok": True,
                    "repo": target_repo,
                    "sha": commit_info.get("sha"),
                    "short_sha": commit_info.get("short_sha"),
                    "url": commit_info.get("url"),
                    "files": len(rendered),
                }
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "initial_scaffold_push_failed",
                    project_id=project.project_id, repo=target_repo, error=str(e),
                )
                initial_push_meta = {
                    "ok": False, "skipped": "publish_error", "error": str(e),
                }
    elif not final_repo_url:
        initial_push_meta = {"ok": False, "skipped": "no_repo_url"}
    elif not scaffold_meta.get("ok"):
        initial_push_meta = {"ok": False, "skipped": "scaffold_failed"}

    # PM-15 — lifecycle event
    events = request.app.state.events
    await events.emit("project.created", {
        "project_id": project.project_id,
        "name": project.name,
        "created_by": project.created_by,
        "repo_url": project.repo_url,
        "kind": project.kind,
        "deploy_backend_port": project.deploy_backend_port,
        "deploy_frontend_port": project.deploy_frontend_port,
    })
    return {
        "data": _serialize(project),
        "meta": {
            "scaffold": scaffold_meta,
            "initial_github_push": initial_push_meta,
        },
        "error": None,
    }


@router.get("/{project_id}")
async def get_project_detail(
    project_id: str,
    request: Request,
    user: dict = Depends(get_principal),  # HAI-12 — JWT or service token
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
        if body.auto_dispatch_on_deploy is not None:
            project.auto_dispatch_on_deploy = bool(body.auto_dispatch_on_deploy)
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


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    request: Request,
    cascade: bool = False,
    admin: dict = Depends(require_role("admin")),
):
    """Hard-delete a project. With ``?cascade=true`` (the UI default), this
    also wipes:

      1. every Request the project owns (and all their subtasks /
         stories / documents / cost rows via :meth:`StateStore.delete_request`),
      2. the project's working tree on disk
         (``C:/ai-projects/<ProjectName>/``).

    GitHub repo cleanup is INTENTIONALLY out of scope — the user
    manages that side by side via the GitHub UI. Earlier versions of
    this route did attempt repo deletion but it hit GitHub permission
    issues that varied per token/account, so the policy is now: leave
    the repo alone, log the URL, surface it in the response so the user
    can click through.

    Without ``cascade=true`` the historical guard applies: the call is
    refused with 409 if the project has any requests, forcing the caller
    to reassign or delete them first.

    Filesystem cleanup is SOFT-FAILURE: if the mount is missing or rmtree
    raises, the DB delete still succeeds and the error surfaces in the
    response body so the UI can hint at it.

    Response (200) body shape::

        {
          "project_id": "...",
          "deleted_requests": 5,
          "filesystem": {"ok": true,  "path": "/host/ai-projects/Foo", "bytes": 42},
          "repo_url":   "https://github.com/.../...",  // null if unset
        }
    """
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "deleted")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))

    project = await state.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found.")

    counts = await state.count_requests_for_project(project_id)

    # PRJ-006 — refuse non-empty unless cascade was explicitly requested.
    if counts["total"] > 0 and not cascade:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "project not empty",
                "request_count": counts["total"],
                "hint": (
                    "Reassign or delete the requests in this project first, "
                    "OR retry the call with ?cascade=true to wipe everything."
                ),
            },
        )

    # ── 1. Cascade-delete every Request the project owns ──────────────
    deleted_requests = 0
    if cascade and counts["total"] > 0:
        requests_to_delete = await state.get_requests_for_project(project_id)
        for r in requests_to_delete:
            try:
                await state.delete_request(r.request_id)
                deleted_requests += 1
            except Exception:
                # One bad row shouldn't block project-level cleanup. Log
                # and continue — the project row itself still gets removed
                # and the leftover requests will become orphans, which the
                # user can spot in History and clean up manually.
                logger.exception(
                    "delete_project: failed to cascade-delete request",
                    project_id=project_id, request_id=r.request_id,
                )

    # ── 2. Delete the project row itself ──────────────────────────────
    await state.delete_project(project_id)
    events = request.app.state.events
    await events.emit("project.deleted", {"project_id": project_id, "name": project.name})

    # ── 3. Remove the working tree on disk (soft-fail) ────────────────
    fs_result = delete_project_root(project.name).as_dict()

    logger.info(
        "project_deleted",
        project_id=project_id,
        name=project.name,
        cascade=cascade,
        deleted_requests=deleted_requests,
        fs_ok=fs_result.get("ok"),
        repo_url=project.repo_url or None,
    )
    return {
        "data": {
            "project_id": project_id,
            "name": project.name,
            "deleted_requests": deleted_requests,
            "filesystem": fs_result,
            "repo_url": project.repo_url or None,
        },
        "meta": None,
        "error": None,
    }


# ─────────────────────── WS-16: backfill repo for existing project ───────────


@router.post("/{project_id}/create_repo")
async def create_repo_for_project(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """WS-16 — create a GitHub repo for an existing project that doesn't
    have one yet. Idempotent in the sense that it 409's instead of silently
    creating a duplicate when repo_url is already set; user must clear the
    URL first (via PATCH) if they really want to rebind."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    project = await state.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found.")
    if project.repo_url:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "repo_url_already_set",
                "current_repo_url": project.repo_url,
                "hint": "This project already points at a repo. Clear repo_url via PATCH first if you want to create a fresh one.",
            },
        )

    try:
        slug = project_repo_slug(project.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    publisher = GitHubPublisher()
    try:
        created = await publisher.create_repo(
            name=slug,
            description=project.description or f"Workspace for {project.name}",
            private=True,
        )
    except GitHubRepoCreateError as e:
        sc = e.status_code
        msg = str(e)
        if sc in (401, 403):
            raise HTTPException(
                status_code=403,
                detail={"error": "github_repo_create_unauthorized", "message": msg,
                        "hint": "GITHUB_TOKEN must have `repo` scope."},
            ) from e
        if sc == 422:
            raise HTTPException(
                status_code=422,
                detail={"error": "github_repo_name_taken", "message": msg,
                        "hint": f"Slug {slug!r} already taken in your namespace. Rename the project first."},
            ) from e
        raise HTTPException(
            status_code=502,
            detail={"error": "github_repo_create_failed", "message": msg, "status": sc},
        ) from e

    project.repo_url = created["html_url"]
    project.updated_at = datetime.utcnow()
    await state.update_project(project)

    events = request.app.state.events
    await events.emit("project.repo_created", {
        "project_id": project_id,
        "repo_url": project.repo_url,
    })
    return {"data": _serialize(project), "meta": None, "error": None}


# ─────────────────────── Per-project Deploy / Stop ───────────────────────────
# These flip the project's `deploy_status` to `pending_deploy` or
# `pending_stop`. The actual `docker compose up/down` runs on the host
# (via the supervisor) because the backend container can't run docker
# from inside without bind-mount path-resolution issues (see CLAUDE.md
# "Supervisor scope"). Endpoints return 202 immediately; UI polls
# `GET /projects/:id` for the eventual `running` or `failed` status.


def _project_deployable(project: Project) -> tuple[bool, str]:
    """Pre-flight checks before flipping a project into pending_deploy.
    Returns (ok, reason). Reason is empty when ok."""
    if not project.deploy_backend_port and not project.deploy_frontend_port:
        return False, (
            "No ports allocated. This project was created before per-project "
            "deploys were available; rename + recreate or use a legacy deploy."
        )
    # Scaffolded compose file must exist on disk. If it doesn't, the
    # scaffold step failed at create time (or someone deleted the
    # files); we can't run docker compose without it.
    try:
        root = project_root_dir(project.name)
    except ValueError as e:
        return False, str(e)
    if not (root / "docker-compose.yml").exists():
        return False, (
            f"No docker-compose.yml at {root}. Scaffold missing — recreate "
            f"the project, or re-run the scaffold via support tools."
        )
    return True, ""


# ─────────────────────── AI Deploy Judge (per-project) ──────────────────────

class _DeployJudgeApplyBody(BaseModel):
    """POST /projects/:id/deploy/judge/apply — opt body. ``decision_id``
    is omitted in the common case (apply the current pending decision)."""
    decision_id: str | None = None


class _DeployJudgeOverrideBody(BaseModel):
    """POST /projects/:id/deploy/judge/override — user picked a different
    action than the judge recommended. Records the override so Phase 8
    can feed it back into future judge calls."""
    action: str  # one of DeployAction values
    decision_id: str | None = None  # optional pinning to a specific decision


class _DeployJudgePreferencesBody(BaseModel):
    """PUT /projects/:id/deploy/judge/preferences — user-authored free-text
    that the judge prompt picks up as additional context."""
    preferences: str


def _serialize_decision(d: "DeployDecision") -> dict[str, Any]:
    """DeployDecision → JSON dict matching the UI panel's expected shape."""
    return {
        "decision_id": d.decision_id,
        "project_id": d.project_id,
        "drift_summary": d.drift_summary,
        "from_commit_sha": d.from_commit_sha,
        "to_commit_sha": d.to_commit_sha,
        "action": d.action,
        "risk": d.risk,
        "confidence": d.confidence,
        "reasoning": d.reasoning,
        "from_llm": d.from_llm,
        "status": d.status,
        "overridden_action": d.overridden_action,
        "created_at": d.created_at.isoformat(),
        "applied_at": d.applied_at.isoformat() if d.applied_at else None,
    }


@router.get("/{project_id}/deploy/judge")
async def get_deploy_judge(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Return the current drift + judge recommendation for the project.

    Lifecycle:
      1. Compute drift via ``compute_drift(state, project)``.
      2. If no drift → return ``{drift: empty, decision: null}`` — UI
         renders State 1 (Up to date).
      3. If a PENDING decision exists AND its to_commit_sha matches
         the current drift's to_commit_sha → return it (cache hit).
         The UI renders the current recommendation; no fresh LLM call.
      4. Otherwise: supersede prior pending decisions, run the judge,
         persist the new decision row, return it.

    All errors fold into a 200 with a safe-default decision so the UI
    is never blocked from rendering something. See
    ``project_deploy_judge.evaluate_project_deploy`` docs.
    """
    state = request.app.state.state_store
    project = await state.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found.")

    drift = await compute_drift(state, project)

    if not drift.has_drift:
        return {
            "data": {
                "drift": _drift_to_dict(drift),
                "decision": None,
            },
            "meta": None,
            "error": None,
        }

    # Cache check — if there's a pending decision against the same
    # to_commit_sha, return it without re-running the judge. Lets the
    # UI poll cheaply.
    existing = await state.get_latest_pending_decision(project_id)
    if existing and existing.to_commit_sha == drift.to_commit_sha:
        return {
            "data": {
                "drift": _drift_to_dict(drift),
                "decision": _serialize_decision(existing),
            },
            "meta": {"cached": True},
            "error": None,
        }

    # Fresh judge call. Supersede any stale pending decision first so
    # the UI's "current recommendation" lookup returns exactly one row.
    await state.supersede_pending_decisions(project_id)

    # Override learning input — surface this project's last N
    # (recommended, overridden) pairs into the judge prompt.
    prior_overrides = await state.list_recent_overrides(project_id, limit=5)

    result = await evaluate_project_deploy(
        project=project,
        drift=drift,
        prior_overrides=prior_overrides,
    )

    decision = DeployDecision(
        decision_id=f"dd-{uuid.uuid4().hex[:12]}",
        project_id=project_id,
        drift_summary=drift.commits,
        from_commit_sha=drift.from_commit_sha,
        to_commit_sha=drift.to_commit_sha,
        action=result.action,
        risk=result.risk,
        confidence=result.confidence,
        reasoning=result.reasoning,
        from_llm=result.from_llm,
        status=DeployDecisionStatus.PENDING,
    )
    await state.create_deploy_decision(decision)

    logger.info(
        "deploy_judge.decided",
        project_id=project_id,
        action=result.action,
        risk=result.risk,
        confidence=result.confidence,
        commits=drift.commit_count,
        from_llm=result.from_llm,
    )
    return {
        "data": {
            "drift": _drift_to_dict(drift),
            "decision": _serialize_decision(decision),
        },
        "meta": {"cached": False},
        "error": None,
    }


def _drift_to_dict(drift: "ProjectDrift") -> dict[str, Any]:
    """ProjectDrift → JSON dict for the UI panel."""
    return {
        "project_id": drift.project_id,
        "has_drift": drift.has_drift,
        "commit_count": drift.commit_count,
        "from_commit_sha": drift.from_commit_sha,
        "to_commit_sha": drift.to_commit_sha,
        "commits": drift.commits,
        "files_touched": drift.files_touched,
        "over_limit": drift.over_limit,
    }


@router.post("/{project_id}/deploy/judge/apply", status_code=202)
async def apply_deploy_judge(
    project_id: str,
    request: Request,
    body: _DeployJudgeApplyBody | None = None,
    user: dict = Depends(get_current_user),
):
    """User clicked Apply on the judge's recommendation.

      1. Resolve the decision row — body.decision_id if given, else the
         current PENDING decision for the project.
      2. Mark it APPLIED.
      3. Special-case ``skip``: advance ``last_deploy_commit_sha`` to
         the decision's to_commit_sha. No docker, no supervisor handoff.
      4. Special-case ``hold``: refuse — Apply doesn't make sense for
         a "do nothing manually" action.
      5. Otherwise: write the chosen action into
         ``projects.deploy_pending_action`` and flip
         ``deploy_status = pending_deploy``. The supervisor's next poll
         picks both up and runs the matching docker invocation (Phase 5).
    """
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    project = await state.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found.")

    decision_id = (body.decision_id if body else None)
    if decision_id:
        # Pin to a specific decision (UI may have stale view)
        existing = await state.get_latest_pending_decision(project_id)
        if not existing or existing.decision_id != decision_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "decision_superseded",
                    "hint": (
                        "This recommendation has been superseded — refresh "
                        "the page to see the current one."
                    ),
                },
            )
        decision = existing
    else:
        decision = await state.get_latest_pending_decision(project_id)
        if not decision:
            raise HTTPException(
                status_code=409,
                detail={"error": "no_pending_decision", "hint": "Compute drift first via GET /deploy/judge."},
            )

    action = decision.action

    if action == str(DeployAction.HOLD):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "cannot_apply_hold",
                "hint": (
                    "Hold means 'do nothing automatically'. Pick a different "
                    "action via Override, or wait for human review."
                ),
            },
        )

    if action == str(DeployAction.SKIP):
        # No docker work — just advance the baseline so the next drift
        # check sees an empty list. Both fields advance together:
        # last_deploy_commit_sha for human-readable audit, and
        # deploy_last_started_at for compute_drift's cutoff query.
        await state.mark_decision_applied(decision.decision_id)
        await state.update_project_deploy(
            project_id,
            last_deploy_commit_sha=decision.to_commit_sha or "",
            deploy_last_started_at=datetime.utcnow(),
        )
        logger.info(
            "deploy_judge.applied.skip",
            project_id=project_id, decision_id=decision.decision_id,
            advanced_to=decision.to_commit_sha,
        )
        # Re-read so response reflects the just-flipped state.
        project = await state.get_project(project_id)
        return {
            "data": _serialize(project) if project else None,
            "meta": {
                "action_taken": action,
                "decision_id": decision.decision_id,
                "hint": "No docker action — baseline advanced.",
            },
            "error": None,
        }

    # Real docker action — flip pending_deploy with the chosen action.
    # Pre-flight: refuse if the project isn't deployable (no scaffold,
    # already running mid-deploy).
    ok, reason = _project_deployable(project)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail={"error": "not_deployable", "hint": reason},
        )
    if project.deploy_status in (
        DeployStatus.DEPLOYING, DeployStatus.PENDING_DEPLOY,
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "already_active",
                "current_status": project.deploy_status,
                "hint": "Wait for the current deploy to finish, then re-apply.",
            },
        )

    await state.mark_decision_applied(decision.decision_id)
    # Advance last_deploy_commit_sha optimistically — the supervisor
    # will leave it intact on success, and if it fails the next judge
    # call will re-evaluate against this SHA which is correct (the
    # FAILED deploy still attempted to ship that commit).
    await state.update_project_deploy(
        project_id,
        deploy_status=DeployStatus.PENDING_DEPLOY,
        deploy_last_started_at=datetime.utcnow(),
        deploy_error="",
        deploy_pending_action=action,
        last_deploy_commit_sha=decision.to_commit_sha or "",
    )
    events = request.app.state.events
    await events.emit("project.deploy_requested", {
        "project_id": project_id,
        "name": project.name,
        "kind": project.kind,
        "action": action,
        "from_judge": True,
        "decision_id": decision.decision_id,
    })
    project = await state.get_project(project_id)
    logger.info(
        "deploy_judge.applied",
        project_id=project_id, decision_id=decision.decision_id, action=action,
    )
    return {
        "data": _serialize(project) if project else None,
        "meta": {
            "action_taken": action,
            "decision_id": decision.decision_id,
            "hint": "Deploy queued. Poll GET /projects/{id} for status.",
        },
        "error": None,
    }


@router.post("/{project_id}/deploy/judge/override", status_code=202)
async def override_deploy_judge(
    project_id: str,
    body: _DeployJudgeOverrideBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """User picked a different action than the judge recommended.

    Records the override (Phase 8 feeds these into future judge prompts
    as learning signal), then applies the user's chosen action through
    the same supervisor handoff as the regular Apply endpoint.
    """
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    project = await state.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found.")

    chosen_action = (body.action or "").strip().lower()
    if chosen_action not in tuple(str(a) for a in DeployAction):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_action",
                "hint": (
                    f"action must be one of: "
                    f"{', '.join(str(a) for a in DeployAction)}"
                ),
            },
        )

    decision_id = body.decision_id
    decision = await state.get_latest_pending_decision(project_id)
    if not decision:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "no_pending_decision",
                "hint": "Compute drift first via GET /deploy/judge.",
            },
        )
    if decision_id and decision.decision_id != decision_id:
        raise HTTPException(
            status_code=409,
            detail={"error": "decision_superseded"},
        )

    await state.mark_decision_overridden(decision.decision_id, chosen_action)

    # ── Then act on the user's choice (mirrors apply_deploy_judge) ──
    if chosen_action == str(DeployAction.HOLD):
        # Hold = explicit "do nothing". No supervisor handoff; the
        # override row is the audit trail.
        logger.info(
            "deploy_judge.overridden.hold",
            project_id=project_id, decision_id=decision.decision_id,
        )
        return {
            "data": _serialize(project),
            "meta": {
                "action_taken": chosen_action,
                "decision_id": decision.decision_id,
                "hint": "Held — no docker action. Re-evaluate when ready.",
            },
            "error": None,
        }

    if chosen_action == str(DeployAction.SKIP):
        # Same baseline-advance as apply-skip — advance BOTH the SHA
        # and the timestamp so compute_drift's cutoff catches the
        # advance.
        await state.update_project_deploy(
            project_id,
            last_deploy_commit_sha=decision.to_commit_sha or "",
            deploy_last_started_at=datetime.utcnow(),
        )
        project = await state.get_project(project_id)
        logger.info(
            "deploy_judge.overridden.skip",
            project_id=project_id, decision_id=decision.decision_id,
        )
        return {
            "data": _serialize(project) if project else None,
            "meta": {
                "action_taken": chosen_action,
                "decision_id": decision.decision_id,
                "hint": "Skipped — baseline advanced.",
            },
            "error": None,
        }

    # Docker action.
    ok, reason = _project_deployable(project)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail={"error": "not_deployable", "hint": reason},
        )
    if project.deploy_status in (
        DeployStatus.DEPLOYING, DeployStatus.PENDING_DEPLOY,
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "already_active",
                "current_status": project.deploy_status,
                "hint": "Wait for the current deploy to finish, then re-override.",
            },
        )

    # Same baseline advance as apply-docker — see comment there.
    await state.update_project_deploy(
        project_id,
        deploy_status=DeployStatus.PENDING_DEPLOY,
        deploy_last_started_at=datetime.utcnow(),
        deploy_error="",
        deploy_pending_action=chosen_action,
        last_deploy_commit_sha=decision.to_commit_sha or "",
    )
    events = request.app.state.events
    await events.emit("project.deploy_requested", {
        "project_id": project_id,
        "name": project.name,
        "kind": project.kind,
        "action": chosen_action,
        "from_judge": False,
        "decision_id": decision.decision_id,
    })
    project = await state.get_project(project_id)
    logger.info(
        "deploy_judge.overridden",
        project_id=project_id, decision_id=decision.decision_id,
        recommended=decision.action, chose=chosen_action,
    )
    return {
        "data": _serialize(project) if project else None,
        "meta": {
            "action_taken": chosen_action,
            "decision_id": decision.decision_id,
            "hint": "Deploy queued. Poll GET /projects/{id} for status.",
        },
        "error": None,
    }


@router.put("/{project_id}/deploy/judge/preferences")
async def put_deploy_judge_preferences(
    project_id: str,
    body: _DeployJudgePreferencesBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Persist the user's free-text preferences for this project's judge.
    Fed into the prompt on every future judge call as additional context.
    Empty string clears them."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    project = await state.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found.")
    prefs = (body.preferences or "").strip()
    if len(prefs) > 2000:
        raise HTTPException(
            status_code=400,
            detail="preferences must be ≤ 2000 characters.",
        )
    await state.update_project_deploy_preferences(project_id, prefs)
    project = await state.get_project(project_id)
    return {
        "data": _serialize(project) if project else None,
        "meta": None,
        "error": None,
    }


# ─────────────────────── /AI Deploy Judge ──────────────────────────────────


@router.post("/{project_id}/deploy", status_code=202)
async def deploy_project(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Mark the project as ``pending_deploy`` so the host-side
    supervisor picks it up and runs ``docker compose -f
    <project>/docker-compose.yml up -d --build`` on the user's host.

    Returns 202 immediately. UI should poll ``GET /projects/{id}`` and
    watch ``deploy_status`` transition through:

        stopped → pending_deploy → deploying → running

    On failure: ``failed`` with ``deploy_error`` populated.

    Refuses (409) when the project isn't deployable (no scaffold on
    disk, no ports allocated) or is already running / deploying.
    """
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    project = await state.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found.")

    # Pre-flight — refuse if there's no scaffold to deploy.
    ok, reason = _project_deployable(project)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail={"error": "not_deployable", "hint": reason},
        )

    # Already running / mid-deploy — surface a 409 so the UI can show
    # a clear "already running" hint instead of silently no-op'ing.
    if project.deploy_status in (
        DeployStatus.RUNNING, DeployStatus.DEPLOYING,
        DeployStatus.PENDING_DEPLOY,
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "already_active",
                "current_status": project.deploy_status,
                "hint": (
                    "Wait for the current operation to finish, or click Stop "
                    "first to redeploy."
                ),
            },
        )

    # Flip the flag. The supervisor's next poll cycle picks it up.
    # `deploy_error` is cleared so a previous failure doesn't keep
    # showing in the UI.
    await state.update_project_deploy(
        project_id,
        deploy_status=DeployStatus.PENDING_DEPLOY,
        deploy_last_started_at=datetime.utcnow(),
        deploy_error="",
    )
    events = request.app.state.events
    await events.emit("project.deploy_requested", {
        "project_id": project_id,
        "name": project.name,
        "kind": project.kind,
        "backend_port": project.deploy_backend_port,
        "frontend_port": project.deploy_frontend_port,
    })
    # Re-read the row so the response reflects the just-flipped state.
    project = await state.get_project(project_id)
    return {
        "data": _serialize(project) if project else None,
        "meta": {"hint": "Deploy queued. Poll GET /projects/{id} for status."},
        "error": None,
    }


@router.post("/{project_id}/stop", status_code=202)
async def stop_project(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Mark the project as ``pending_stop``. Supervisor runs
    ``docker compose -f <project>/docker-compose.yml down`` and flips
    status to ``stopped``."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    project = await state.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found.")

    # Already stopped / mid-stop — 409 so the UI gives feedback.
    if project.deploy_status in (
        DeployStatus.STOPPED, DeployStatus.STOPPING, DeployStatus.PENDING_STOP,
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "not_running",
                "current_status": project.deploy_status,
                "hint": "Project is already stopped or stopping.",
            },
        )

    await state.update_project_deploy(
        project_id,
        deploy_status=DeployStatus.PENDING_STOP,
    )
    events = request.app.state.events
    await events.emit("project.stop_requested", {
        "project_id": project_id,
        "name": project.name,
    })
    project = await state.get_project(project_id)
    return {
        "data": _serialize(project) if project else None,
        "meta": {"hint": "Stop queued. Poll GET /projects/{id} for status."},
        "error": None,
    }


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


class PRDGenerateBody(BaseModel):
    # Optional reviewer feedback to apply to the PREVIOUS PRD version
    # (if any). When empty/missing AND a previous version exists, the agent
    # is still asked to produce a fresh draft from the brief — same as
    # before this feature. When provided, the agent is asked to revise the
    # previous draft to address the comments rather than start fresh.
    review_comments: str | None = None


_BRIEF_MIN = 50
_BRIEF_MAX = 4000
# Cap on PRD markdown size. Raised from 50K to 100K because the
# Atlas-reference-style prompts now produce structured, detailed PRDs
# (Document Information, numbered sections, ID-tagged Functional
# Requirements tables, ASCII UI mockups, ERDs) that legitimately run
# to 60-90 KB. Hitting 100K means the agent went off-spec or duplicated
# content; that's the right time to reject.
_PRD_MAX = 100_000
_REVIEW_COMMENTS_MAX = 2000  # 2000 chars caps the regen prompt overhead


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
        "review_input": art.review_input,
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
    body: PRDGenerateBody | None = None,
    user: dict = Depends(get_current_user),
):
    """Run `prd_author` (single-shot, no workflow) using the project's
    brief + metadata as input. Creates a NEW PRD version each time —
    older versions stay in the table but the UI surfaces only the latest.

    When `body.review_comments` is provided AND a previous PRD version
    exists, the agent is asked to revise that previous draft to address
    the comments. Otherwise (no comments, or no prior version) the agent
    drafts a fresh PRD from the brief.
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

    review_comments = ((body.review_comments if body else None) or "").strip()
    if len(review_comments) > _REVIEW_COMMENTS_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"review_comments must be ≤ {_REVIEW_COMMENTS_MAX} characters.",
        )

    # Pull the most recent PRD before we mint a new row — that's the
    # version the agent will revise against if review comments are given.
    existing = await state.list_artifacts(project_id, ArtifactKind.PRD)
    # `list_artifacts` returns newest-first.
    previous_prd = existing[0] if existing else None
    next_version = (max((a.version for a in existing), default=0)) + 1
    new_art = ProjectArtifact(
        artifact_id=f"art-{uuid.uuid4().hex[:12]}",
        project_id=project_id,
        kind=ArtifactKind.PRD,
        version=next_version,
        status=ArtifactStatus.DRAFT,
        content="",
        created_by=user.get("user_id"),
        review_input=review_comments or None,
    )
    await state.create_artifact(new_art)

    executor = getattr(request.app.state, "agent_executor", None)
    if executor is None:
        raise HTTPException(
            status_code=503,
            detail="Agent executor not configured — set ANTHROPIC_AWS_API_KEY + ANTHROPIC_AWS_WORKSPACE_ID.",
        )

    # Two prompt shapes:
    #   - "fresh draft" — no review comments, OR no prior PRD to revise.
    #     Same prompt as before this feature.
    #   - "revise existing" — comments + prior PRD. Agent is explicitly told
    #     NOT to start from scratch.
    revising = bool(review_comments) and previous_prd is not None and previous_prd.content.strip()
    if revising:
        prompt = (
            f"You are REVISING an existing Product Requirements Document for "
            f"the project {project.name!r}. DO NOT start from scratch — apply "
            f"the reviewer's feedback to the existing draft and keep the rest "
            f"intact.\n\n"
            f"## Project brief\n{brief.content}\n\n"
            f"## Current PRD (revise this)\n{previous_prd.content}\n\n"
            f"## Reviewer comments to address in this revision\n"
            f"{review_comments}\n\n"
            f"Output the FULL revised PRD in markdown — same structure and "
            f"sections as before. Apply the comments precisely; keep "
            f"unaffected sections word-for-word identical to the previous "
            f"draft. No commentary before or after."
        )
    else:
        # The prompt below loads docs/reference-formats/prd-template.md
        # at request time. Anyone can edit that file to refine the
        # PRD format — no code change required. The template lives
        # in the repo so the format is version-controlled alongside
        # the prompt that uses it.
        today_iso = datetime.utcnow().strftime("%Y-%m-%d")
        template_md = _load_reference_format("prd-template.md")
        if template_md:
            template_block = (
                "## Reference format\n"
                "The following document is a complete real-world example PRD.\n"
                "Your output MUST match its structure section-for-section —\n"
                "Document Information table, numbered sections, ID-tagged\n"
                "Functional Requirements tables (e.g. CALL-001), ASCII UI\n"
                "mockups, ERD + per-table column definitions when persistence\n"
                "is in scope, Future Enhancements table, Success Metrics\n"
                "table, Appendix with Glossary and Revision History. Copy\n"
                "the STRUCTURE (section names, table columns, mockup style),\n"
                "not the CONTENT. Your project is different; the layout is\n"
                "the same.\n\n"
                "<reference_prd>\n"
                f"{template_md}\n"
                "</reference_prd>\n\n"
            )
        else:
            # Fallback if the reference file is missing — a tight
            # structural outline so generation still works.
            template_block = (
                "## Required structure\n"
                "Produce sections in order: # Title + tagline · Document\n"
                "Information table · ## 1. Executive Summary (Vision /\n"
                "Problem / Target Users) · ## 2. Goals (G1..Gn) · ## 3.\n"
                "Product Overview (Core Features + ASCII architecture) ·\n"
                "## 4. Detailed Feature Requirements (per feature: Overview\n"
                "+ ID-tagged Functional Requirements table + ASCII UI\n"
                "mockups) · ## 5. Non-Functional Requirements · ## 6. User\n"
                "Flows · ## 7. UI Design · ## 8. Technical Considerations\n"
                "(Tech Stack table + ERD + table definitions + Integration\n"
                "Points + Constraints) · ## 9. Future Enhancements · ## 10.\n"
                "Success Metrics · ## 11. Appendix (Glossary, Open\n"
                "Questions, Revision History).\n\n"
            )
        prompt = (
            f"You are drafting a Product Requirements Document for the project "
            f"named {project.name!r}.\n\n"
            f"Project description: {project.description or '(none provided)'}\n\n"
            f"Project brief from the lead:\n"
            f"---\n{brief.content}\n---\n\n"
            "## Output rules\n"
            "- Write a SINGLE markdown document. No prose before or after\n"
            "  the markdown. No code fences around the document itself.\n"
            "- Be SPECIFIC. Every requirement is actionable and testable.\n"
            "  If a detail is genuinely unknown, write `TBD` and flag it\n"
            "  under Open Questions in the Appendix.\n"
            "- Be DETAILED but CRISP — no filler sentences, no apologies,\n"
            "  no 'this section will cover…' meta-prose. Every paragraph\n"
            "  carries information.\n"
            "- Tone: opinionated, decisive, technical. The reader is a\n"
            "  technical lead who can implement directly from this doc.\n"
            "- Today's date is "
            f"{today_iso} — use it in the Document Information table\n"
            "  (Created Date and Last Updated) and in the Revision History\n"
            "  v1.0 row.\n"
            f"- Use {project.name!r} as the project name in the title.\n\n"
            f"{template_block}"
            "## Style guidance for this specific project\n"
            "- Skip sections 7 (UI Design) and 8.2 (Database Design) if\n"
            "  the project is clearly a backend service / CLI / library\n"
            "  with no user-facing UI or data persistence. State the\n"
            "  skip explicitly:  `## 7. UI Design — Not applicable (CLI\n"
            "  tool).`  so the numbering stays consistent.\n"
            "- The brief is the ground truth for product intent. Don't\n"
            "  invent goals not implied by it; do flesh out the natural\n"
            "  consequences and edge cases.\n"
            "- Functional Requirements tables are the most important\n"
            "  artifact downstream — the task list is built from them.\n"
            "  Make IDs stable and requirements unambiguous.\n\n"
            "Output the markdown only, no commentary before or after."
        )

    try:
        result = await executor.single_agent_call(
            agent_id="prd_specialist",
            prompt=prompt,
            project_artifact_id=new_art.artifact_id,
            project_id=project_id,
            label="Generating PRD",
            # Atlas-style PRDs run ~60 KB / ~15-25K tokens. The default
            # 8192-token cap truncates the output mid-document. 32 000
            # gives healthy headroom on Claude Opus 4.7 without hitting
            # provider-side limits.
            max_tokens=32_000,
        )
    except Exception as e:
        # Generation failed. Roll back the empty draft we created above —
        # otherwise the orphaned empty row becomes the new "latest" PRD
        # and the editor opens blank on every subsequent page load.
        # Falls back to the previous version (or none) as the latest.
        logger.exception(
            "prd_generate_failed",
            project_id=project_id,
            artifact_id=new_art.artifact_id,
            err=str(e),
        )
        await state.delete_artifact_by_id(new_art.artifact_id)
        raise HTTPException(status_code=502, detail=f"PRD generation failed: {e}")

    text = (result.get("text") or "").strip()
    if not text:
        # Same cleanup as the exception path — agent returned nothing
        # usable, so the empty draft would become the new "latest" PRD
        # and the editor would open blank.
        logger.warning(
            "prd_generate_empty",
            project_id=project_id,
            artifact_id=new_art.artifact_id,
        )
        await state.delete_artifact_by_id(new_art.artifact_id)
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
    """Either save-draft (`content`) or finalize (`status='finalized'`).

    On finalize, also writes the markdown to the host filesystem at
    ``C:/ai-projects/<ProjectName>/docs/PRD.md`` (via the
    ``/host/ai-projects`` bind-mount) and pushes it to the project's
    GitHub repo when ``project.repo_url`` is set. Both side effects are
    soft-fail: failures are reported in ``meta`` but do not roll back
    the SQLite finalize (the user can retry via the project actions
    menu)."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    project = await _require_project(state, project_id)

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

    meta: dict[str, Any] = {}

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

        # ── PM-finalize: write to host filesystem + push to GitHub ──
        # Soft-fail: log + report in `meta`, do NOT raise.
        host_result = write_finalized_prd(project.name, art.content)
        meta["host_write"] = host_result.as_dict()
        if not host_result.ok:
            logger.warning(
                "prd_finalize.host_write_failed",
                project_id=project_id, project_name=project.name,
                error=host_result.error,
            )

        # Push the finalized PRD to the project's own GitHub repo (if
        # repo_url is set). Lands at `docs/PRD.md` in the project's
        # repo. Commit message includes the version + actor for
        # auditability. Skipped when no repo_url is configured — the
        # host write still happened, so the user has a local copy.
        meta["github_push"] = await _push_finalized_doc_to_repo(
            project=project,
            repo_path="docs/PRD.md",
            content=art.content,
            commit_subject=f"docs: finalize PRD v{art.version}",
            descriptor=f"PRD v{art.version}",
            actor=user.get("username") or user.get("user_id") or "unknown",
        )
    else:
        art = await state.get_artifact(project_id, ArtifactKind.PRD)

    return {"data": _artifact_to_dict(art), "meta": meta or None, "error": None}


@router.delete("/{project_id}/prd")
async def delete_prd(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Hard-delete the PRD for this project — all versions (drafts,
    finalized, archived). Also removes the host-side
    ``docs/PRD.md`` file. Tasks derived from this PRD are NOT touched
    (they have no foreign key) — they're left for the user to
    delete separately if they want a clean slate.

    Returns ``meta.deleted_versions`` (DB row count) and
    ``meta.host_delete`` (filesystem result). 200 even if there was no
    PRD to delete — caller can treat this as idempotent."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    project = await _require_project(state, project_id)

    deleted = await state.delete_artifacts(project_id, ArtifactKind.PRD)
    host_result = delete_host_file(project.name, "PRD.md")

    events = request.app.state.events
    await events.emit("project.prd_deleted", {
        "project_id": project_id,
        "deleted_versions": deleted,
        "by": user.get("user_id"),
    })

    logger.info(
        "prd.deleted",
        project_id=project_id, project_name=project.name,
        deleted_versions=deleted, host_ok=host_result.ok,
    )
    return {
        "data": None,
        "meta": {
            "deleted_versions": deleted,
            "host_delete": host_result.as_dict(),
        },
        "error": None,
    }


async def _push_finalized_doc_to_repo(
    *,
    project: Project,
    repo_path: str,
    content: str,
    commit_subject: str,
    descriptor: str,
    actor: str,
) -> dict[str, Any]:
    """Mirrors the WS-12 / WS-15 research_publisher pattern: if the
    project has a parseable GitHub repo URL, commit ``content`` to
    ``repo_path`` in that repo via the Trees API.

    ``descriptor`` is a short string included in the commit body for
    traceability (e.g. ``"PRD v3"`` or ``"task list v7 (24 tasks)"``).

    Returns a result dict for inclusion in the response ``meta``.
    Soft-fail — never raises.

    Skipped cases (returned as ``{ok: False, skipped: <reason>}``):
      - No ``repo_url`` set on the project
      - ``repo_url`` isn't a parseable github.com URL
      - GitHub token / publisher not configured
    """
    if not project.repo_url:
        return {"ok": False, "skipped": "no_repo_url"}
    parsed = extract_owner_repo(project.repo_url)
    if not parsed:
        return {"ok": False, "skipped": "unparseable_repo_url"}
    owner, repo = parsed
    target_repo = f"{owner}/{repo}"

    publisher = GitHubPublisher()
    commit_body = (
        f"{commit_subject}\n\n"
        f"Finalized by {actor} via the Agent Team UI.\n"
        f"Project: {project.name} ({project.project_id})\n"
        f"Artifact: {descriptor}\n"
    )
    try:
        result = await publisher.commit_files(
            files={repo_path: content},
            commit_message=commit_body,
            repo=target_repo,
        )
        return {
            "ok": True,
            "repo": target_repo,
            "path": repo_path,
            "sha": result.get("sha"),
            "short_sha": result.get("short_sha"),
            "url": result.get("url"),
        }
    except GitHubPublishError as e:
        logger.warning(
            "finalize.github_push_failed",
            project_id=project.project_id, repo=target_repo,
            path=repo_path, error=str(e),
        )
        return {"ok": False, "skipped": "publish_error", "error": str(e)}
    except Exception as e:  # noqa: BLE001 — soft-fail catch-all
        logger.exception(
            "finalize.github_push_unexpected",
            project_id=project.project_id, repo=target_repo,
            path=repo_path,
        )
        return {"ok": False, "skipped": "unexpected_error", "error": str(e)}


# ─────────────────────── Project-driven Build: API Specification ─────────────
# Generated AFTER the PRD is finalized. The agent (backend_specialist)
# turns the PRD's Functional Requirements + Database Design into an
# enterprise-grade REST API spec following the format in
# docs/reference-formats/api-spec-template.md.
#
# Lifecycle mirrors the PRD: draft → finalize → archive. On finalize
# the markdown is written to C:/ai-projects/<Project>/docs/api-spec.md
# and pushed to the project's GitHub repo.


_API_SPEC_MAX = 200_000  # OpenAPI YAML + narrative — generous, larger than PRD


class APISpecGenerateBody(BaseModel):
    # Optional reviewer feedback to apply to the PREVIOUS version
    # rather than starting from scratch. Same shape as PRDGenerateBody.
    review_comments: str | None = None


class APISpecPatchBody(BaseModel):
    content: str | None = None
    status: Literal["finalized"] | None = None


@router.get("/{project_id}/api-spec")
async def get_api_spec(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Return the latest API spec artifact (any status). 404 if none yet."""
    state = request.app.state.state_store
    await _require_project(state, project_id)
    art = await state.get_artifact(project_id, ArtifactKind.API_SPEC)
    if art is None:
        raise HTTPException(status_code=404, detail="No API spec yet.")
    return {"data": _artifact_to_dict(art), "meta": None, "error": None}


@router.post("/{project_id}/api-spec/generate", status_code=201)
async def generate_api_spec(
    project_id: str,
    request: Request,
    body: APISpecGenerateBody | None = None,
    user: dict = Depends(get_current_user),
):
    """Run `backend_specialist` (single-shot) on the finalized PRD to
    produce an enterprise-grade REST API specification. Creates a NEW
    artifact version each time — previous versions become accessible
    through ``list_artifacts`` but aren't shown in the default view.

    When ``body.review_comments`` is provided AND a previous API spec
    exists, the agent REVISES the previous version using the comments;
    otherwise drafts a fresh spec from the PRD."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    project = await _require_project(state, project_id)

    prd = await state.get_artifact(project_id, ArtifactKind.PRD)
    if prd is None or prd.status != ArtifactStatus.FINALIZED:
        raise HTTPException(
            status_code=409,
            detail="Finalize the PRD before generating an API spec.",
        )

    review_comments = (body.review_comments or "").strip() if body else ""

    # Get the most recent API spec to learn its version (for revisions
    # AND for the version number we mint here).
    existing = await state.list_artifacts(project_id, ArtifactKind.API_SPEC)
    existing.sort(key=lambda a: a.version, reverse=True)
    previous_spec = existing[0] if existing else None
    next_version = (previous_spec.version + 1) if previous_spec else 1

    new_art = ProjectArtifact(
        artifact_id=f"art-{uuid.uuid4().hex[:12]}",
        project_id=project_id,
        kind=ArtifactKind.API_SPEC,
        version=next_version,
        status=ArtifactStatus.DRAFT,
        content="",
        created_by=user.get("user_id"),
        review_input=review_comments or None,
    )
    await state.create_artifact(new_art)

    executor = getattr(request.app.state, "agent_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="Agent executor unavailable.")

    revising = (
        bool(review_comments)
        and previous_spec is not None
        and previous_spec.content.strip()
    )
    if revising:
        prompt = (
            f"You are REVISING an existing API specification for the project "
            f"{project.name!r}. DO NOT start from scratch — apply the "
            f"reviewer's feedback to the existing draft and keep the rest "
            f"intact.\n\n"
            f"## PRD (for reference)\n{prd.content}\n\n"
            f"## Current API spec (revise this)\n{previous_spec.content}\n\n"
            f"## Reviewer comments to address\n{review_comments}\n\n"
            f"Output the FULL revised API spec in markdown — same structure "
            f"and sections as before. Apply the comments precisely; keep "
            f"unaffected sections word-for-word identical. No commentary "
            f"before or after."
        )
    else:
        # Load the enterprise-grade reference template at request time.
        template_md = _load_reference_format("api-spec-template.md")
        if template_md:
            template_block = (
                "## Reference format\n"
                "The following is a complete real-world example API spec.\n"
                "Your output MUST match its structure section-for-section.\n"
                "Copy the STRUCTURE (section names, table columns, response\n"
                "envelope, error format, OpenAPI shape) — not the CONTENT.\n"
                "Your project is different; the layout and conventions are\n"
                "the same.\n\n"
                "<reference_api_spec>\n"
                f"{template_md}\n"
                "</reference_api_spec>\n\n"
            )
        else:
            template_block = (
                "## Required structure\n"
                "1. Document Information table · 2. Overview · 3. Conventions\n"
                "(Base URL, Auth, Headers, Envelope, RFC 7807 errors,\n"
                "Pagination, Rate limits, Idempotency, ETag, Webhooks) ·\n"
                "4. Resources · 5. Endpoints (per endpoint: method+path,\n"
                "auth, params, request body, response 2xx + errors,\n"
                "examples) · 6. Data Models · 7. Status Code Reference ·\n"
                "8. Versioning & Deprecation · 9. Security · 10. OpenAPI\n"
                "3.1 YAML block · 11. Changelog · 12. Appendix.\n\n"
            )
        today_iso = datetime.utcnow().strftime("%Y-%m-%d")
        prompt = (
            f"You are drafting an enterprise-grade REST API specification "
            f"for the project named {project.name!r}.\n\n"
            f"Project description: {project.description or '(none provided)'}\n\n"
            f"## PRD (source of truth for what the API must support)\n"
            f"---\n{prd.content}\n---\n\n"
            "## Output rules\n"
            "- Write a SINGLE markdown document. No prose before or after.\n"
            "  No code fences around the document itself.\n"
            "- Be SPECIFIC. Every endpoint has a path, method, auth\n"
            "  requirement, request schema (where applicable), response\n"
            "  examples for 2xx AND the 4xx/5xx the endpoint actually emits.\n"
            "- Be DETAILED but CRISP. No filler. Every paragraph carries\n"
            "  information.\n"
            "- DO include §9 (OpenAPI Specification) — the YAML block is\n"
            "  the machine-readable contract. Cover at minimum the\n"
            "  schemas, security schemes, and a few paths showing the\n"
            "  full pattern. The narrative §4 (Endpoints) can carry the\n"
            "  rest without exhaustive YAML duplication.\n"
            "- Tone: opinionated, decisive, technical. The reader is a\n"
            "  senior backend engineer who will implement directly.\n"
            "- Today's date is "
            f"{today_iso} — use it in the Document Information table\n"
            "  and the Changelog v1.0 row.\n"
            f"- Use {project.name!r} as the project name.\n\n"
            "## Industry standards to follow\n"
            "- REST resource modeling (nouns + HTTP verbs)\n"
            "- OpenAPI 3.1 for the machine-readable contract\n"
            "- RFC 7807 (`application/problem+json`) for error responses\n"
            "- RFC 8594 (`Deprecation` / `Sunset` headers) for deprecation\n"
            "- Cursor-based pagination (NOT offset/page) for scalability\n"
            "- `X-Request-ID` trace propagation\n"
            "- Per-token rate limits with `X-RateLimit-*` + `Retry-After`\n"
            "- Idempotency-Key for non-idempotent POSTs\n"
            "- ETag + If-None-Match for resource caching\n"
            "- HMAC-signed webhooks with replay protection\n"
            "- Path-prefix versioning (/v1)\n"
            "- HSTS, TLS 1.2+, no PII in logs\n"
            "- OWASP API Top 10 alignment\n\n"
            f"{template_block}"
            "Now produce the full API spec. Output ONLY the markdown — no\n"
            "commentary before or after."
        )

    try:
        result = await executor.single_agent_call(
            agent_id="backend_specialist",
            prompt=prompt,
            project_artifact_id=new_art.artifact_id,
            project_id=project_id,
            label="Generating API spec",
            # API specs run 30-60 KB (OpenAPI YAML + narrative). Same
            # rationale as the PRD path — 8192-token default truncates.
            max_tokens=32_000,
        )
    except Exception as e:
        # Roll back the empty draft (see /prd/generate for the same
        # pattern + rationale) so the editor doesn't open blank on the
        # next page load.
        logger.exception(
            "api_spec_generate_failed",
            project_id=project_id,
            artifact_id=new_art.artifact_id,
            err=str(e),
        )
        await state.delete_artifact_by_id(new_art.artifact_id)
        raise HTTPException(status_code=502, detail=f"API spec generation failed: {e}")

    text = (result.get("text") or "").strip()
    if not text:
        logger.warning(
            "api_spec_generate_empty",
            project_id=project_id,
            artifact_id=new_art.artifact_id,
        )
        await state.delete_artifact_by_id(new_art.artifact_id)
        raise HTTPException(status_code=502, detail="Agent returned an empty API spec.")
    if len(text) > _API_SPEC_MAX:
        text = text[:_API_SPEC_MAX]

    await state.update_artifact_content(new_art.artifact_id, text)
    saved = await state.get_artifact(project_id, ArtifactKind.API_SPEC)

    events = request.app.state.events
    await events.emit("project.api_spec_generated", {
        "project_id": project_id,
        "artifact_id": saved.artifact_id,
        "version": saved.version,
    })
    return {"data": _artifact_to_dict(saved), "meta": None, "error": None}


@router.patch("/{project_id}/api-spec")
async def patch_api_spec(
    project_id: str,
    body: APISpecPatchBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Either save-draft (`content`) or finalize (`status='finalized'`).

    On finalize, also writes the markdown to
    ``C:/ai-projects/<ProjectName>/docs/api-spec.md`` and pushes it to
    the project's GitHub repo. Both side effects are soft-fail."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    project = await _require_project(state, project_id)

    art = await state.get_artifact(project_id, ArtifactKind.API_SPEC)
    if art is None:
        raise HTTPException(status_code=404, detail="No API spec to update.")

    if body.content is not None:
        content = body.content
        if len(content) > _API_SPEC_MAX:
            raise HTTPException(
                status_code=400,
                detail=f"API spec must be at most {_API_SPEC_MAX} characters "
                       f"(got {len(content)}).",
            )
        await state.update_artifact_content(art.artifact_id, content)

    meta: dict[str, Any] = {}

    if body.status == "finalized":
        art = await state.finalize_artifact(
            art.artifact_id, finalized_by=user.get("user_id"),
        )
        events = request.app.state.events
        await events.emit("project.api_spec_finalized", {
            "project_id": project_id,
            "artifact_id": art.artifact_id,
            "version": art.version,
        })

        host_result = write_finalized_api_spec(project.name, art.content)
        meta["host_write"] = host_result.as_dict()
        if not host_result.ok:
            logger.warning(
                "api_spec_finalize.host_write_failed",
                project_id=project_id, project_name=project.name,
                error=host_result.error,
            )

        meta["github_push"] = await _push_finalized_doc_to_repo(
            project=project,
            repo_path="docs/api-spec.md",
            content=art.content,
            commit_subject=f"docs: finalize API spec v{art.version}",
            descriptor=f"API spec v{art.version}",
            actor=user.get("username") or user.get("user_id") or "unknown",
        )
    else:
        art = await state.get_artifact(project_id, ArtifactKind.API_SPEC)

    return {"data": _artifact_to_dict(art), "meta": meta or None, "error": None}


@router.delete("/{project_id}/api-spec")
async def delete_api_spec(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Hard-delete the API spec for this project — all versions. Also
    removes the host-side ``docs/api-spec.md`` file. Tasks already
    generated against this spec are NOT touched (no FK)."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    project = await _require_project(state, project_id)

    deleted = await state.delete_artifacts(project_id, ArtifactKind.API_SPEC)
    host_result = delete_host_file(project.name, "api-spec.md")

    events = request.app.state.events
    await events.emit("project.api_spec_deleted", {
        "project_id": project_id,
        "deleted_versions": deleted,
        "by": user.get("user_id"),
    })

    logger.info(
        "api_spec.deleted",
        project_id=project_id, project_name=project.name,
        deleted_versions=deleted, host_ok=host_result.ok,
    )
    return {
        "data": None,
        "meta": {
            "deleted_versions": deleted,
            "host_delete": host_result.as_dict(),
        },
        "error": None,
    }


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
        "review_input": t.review_input,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        # BPD fields (BPD-003). MUST be included in the serialized row —
        # without them, `/projects/{pid}/tasks` returns tasks with
        # feature_id=undefined to the frontend, and BuildPlanView's
        # `tasksByFeature` grouping silently drops every task. This was
        # the actual root cause of every "tasks list shows empty"
        # report — not auto-refresh, not the cascade NULLing — the API
        # never exposed feature_id in the first place. Surfaced via DB
        # query showing 348/348 tasks with feature_id set but the tree
        # showing 0 BPD tasks: the data was there, the wire format
        # didn't carry it.
        "feature_id": t.feature_id,
        "depends_on": list(t.depends_on or []),
        "primary_file": t.primary_file,
        "expected_loc": t.expected_loc,
        "acceptance_test": t.acceptance_test,
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
        # Generous caps — the Atlas-reference style prompts emit
        # "Phase N: Theme — Task" titles (often 80-130 chars) and
        # multi-line descriptions with `**Rules**:` + `**Sub-tasks:**`
        # blocks that easily run to 1-3 KB. The DB columns are TEXT
        # with no length constraint; these caps just keep a runaway
        # response from blowing up the row.
        out.append({
            "title": title[:300],
            "description": str(item.get("description") or "")[:6000],
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


class TasksGenerateBody(BaseModel):
    # Optional reviewer feedback to apply to the PREVIOUS task list draft
    # (if any). Same shape as PRDGenerateBody.
    review_comments: str | None = None


@router.post("/{project_id}/tasks/generate", status_code=201)
async def generate_tasks(
    project_id: str,
    request: Request,
    body: TasksGenerateBody | None = None,
    user: dict = Depends(get_current_user),
):
    """Run `user_story_author` (single-shot) on the finalized PRD. Replaces
    any existing draft (PRD §4.3 TSK-005). Blocked when a finalized list
    already exists — caller must archive that list first (TSK-006).

    When `body.review_comments` is supplied AND a prior draft (or archived
    list) exists, the agent is asked to revise that list to address the
    comments rather than start fresh from the PRD."""
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
    # Optional but highly useful — when an API spec has been finalized,
    # we include it in the Tasks prompt so the generated sub-tasks
    # reference REAL endpoints (`POST /api/v1/users`, schemas, etc.)
    # instead of inventing them. Tasks generation does NOT require an
    # API spec — projects without one still work.
    api_spec = await state.get_artifact(project_id, ArtifactKind.API_SPEC)
    api_spec_content = (
        api_spec.content
        if api_spec is not None and api_spec.status == ArtifactStatus.FINALIZED
        else ""
    )

    review_comments = ((body.review_comments if body else None) or "").strip()
    if len(review_comments) > _REVIEW_COMMENTS_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"review_comments must be ≤ {_REVIEW_COMMENTS_MAX} characters.",
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

    # Pull the most recent draft (if any) BEFORE we delete it — that's the
    # version the agent will revise against when review comments are given.
    existing_draft = await state.list_tasks_for_project(
        project_id, list_status=ArtifactStatus.DRAFT,
    )
    previous_tasks: list[ProjectTask] = list(existing_draft)
    if existing_draft:
        # All draft tasks share a list_version (they were all generated together).
        await state.delete_task_list_draft(project_id, existing_draft[0].list_version)

    # If no draft exists but archived versions do, fall back to the most
    # recent archived list as the revision base. Lets a user finalize →
    # archive → regenerate-with-comments and still get a revision.
    if not previous_tasks:
        archived = await state.list_tasks_for_project(
            project_id, list_status=ArtifactStatus.ARCHIVED,
        )
        if archived:
            # `list_tasks_for_project` orders by ordinal asc; pick the
            # highest-list_version group.
            latest_ver = max(t.list_version for t in archived)
            previous_tasks = [t for t in archived if t.list_version == latest_ver]

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

    # Two prompt shapes (same pattern as PRD regen).
    revising = bool(review_comments) and bool(previous_tasks)
    if revising:
        # Serialize the previous list as JSON the agent can ingest cleanly.
        import json as _json
        prev_json = _json.dumps(
            [
                {
                    "title": t.title,
                    "description": t.description,
                    "task_type": t.task_type,
                    "priority": t.priority,
                    "estimated_agent": t.estimated_agent,
                }
                for t in sorted(previous_tasks, key=lambda x: x.ordinal)
            ],
            indent=2,
        )
        prompt = (
            "You are REVISING an existing flat task list for an AI agent team "
            "to execute. DO NOT start from scratch — apply the reviewer's "
            "feedback to the existing list and keep the rest intact.\n\n"
            "## PRD (for reference)\n"
            f"{prd.content}\n\n"
            + (
                f"## API Specification (for reference)\n{api_spec_content}\n\n"
                if api_spec_content else ""
            ) +
            "## Current task list (revise this)\n"
            "```json\n"
            f"{prev_json}\n"
            "```\n\n"
            "## Reviewer comments to address\n"
            f"{review_comments}\n\n"
            "## Output format\n"
            "Emit a single fenced ```json``` block containing the REVISED ARRAY "
            "of task objects, with the same keys as the input (title, "
            "description, task_type, priority, estimated_agent). Apply the "
            "comments precisely; keep unaffected tasks word-for-word identical "
            "to the input. No prose before or after."
        )
    else:
        # The prompt below loads docs/reference-formats/tasks-template.md
        # at request time. Anyone can edit that file to refine the
        # task-list format; the next generate-tasks call picks it up.
        template_md = _load_reference_format("tasks-template.md")
        if template_md:
            template_block = (
                "## Reference format\n"
                "The following document is a complete real-world example\n"
                "task list for a different project. Study its structure:\n"
                "phases organized as `### Phase N: <Theme>`; each task\n"
                "rendered as `#### Task N: <Title>` with a Status line,\n"
                "a Rules line, and a Sub-task table.\n\n"
                "Your output is NOT this markdown — it's a flat JSON array.\n"
                "BUT every JSON task must round-trip into the same visual\n"
                "structure when rendered: phase prefix in the title, Rules\n"
                "+ Sub-tasks in the description. Copy the LEVEL OF DETAIL,\n"
                "phase grouping, and sub-task specificity — not the\n"
                "content (your project is different).\n\n"
                "<reference_task_list>\n"
                f"{template_md}\n"
                "</reference_task_list>\n\n"
            )
        else:
            template_block = (
                "## Required level of detail\n"
                "Phases organize tasks (e.g. 'Phase 1: Foundation'). Each\n"
                "task has a Rules reference, a one-paragraph summary, and\n"
                "a Sub-task table. Sub-tasks name concrete files, commands,\n"
                "endpoints. Last sub-task is always a Test step.\n\n"
            )
        # When the project has a finalized API spec, give it to the
        # agent so generated sub-tasks reference concrete endpoints /
        # schemas instead of inventing them.
        api_spec_block = (
            f"## API Specification (use as the source of truth for endpoints)\n"
            f"{api_spec_content}\n\n"
        ) if api_spec_content else ""
        prompt = (
            "You are breaking a finalized PRD into a DETAILED, phase-organized "
            "task list that an AI agent team will execute.\n\n"
            "## PRD\n"
            f"{prd.content}\n\n"
            f"{api_spec_block}"
            f"{template_block}"
            "## Output format\n"
            "Emit a SINGLE fenced ```json``` block containing an ARRAY of "
            "task objects. No prose before or after. Each object has exactly "
            "these keys:\n\n"
            "  - title: string. Format MUST be:\n"
            "       \"Phase <N>: <Phase theme> — <Task title>\"\n"
            "     The phase prefix lets the renderer group tasks. Examples:\n"
            "       \"Phase 1: Foundation — Create project structure\"\n"
            "       \"Phase 1: Foundation — Configure docker-compose\"\n"
            "       \"Phase 2: Database & Core Services — Define Drizzle schema\"\n"
            "     Phases numbered from 1. Use 4-12 phases for a typical\n"
            "     full-stack app; pick coherent themes (Foundation, Auth,\n"
            "     Data Layer, Core Feature A, Core Feature B, Polish, QA).\n\n"
            "  - description: MULTI-LINE markdown string. Required structure:\n\n"
            "       **Rules**: <which rules / coding-standards apply, e.g.\n"
            "         '/rules/ui.md' or 'see docs/style-guide.md', or 'N/A'>\n\n"
            "       <One-paragraph summary of what 'done' looks like.>\n\n"
            "       **Sub-tasks:**\n"
            "       - <Sub-task 1 — specific action, files to touch, expected outcome>\n"
            "       - <Sub-task 2 — …>\n"
            "       - <Sub-task 3 — …>\n"
            "       - **Test**: <what to verify to call the task done>\n\n"
            "     Aim for 4-8 sub-tasks per task. The last one is ALWAYS a\n"
            "     Test sub-task starting with `**Test**:`. Each sub-task is\n"
            "     concrete: name the function / file / command / URL where\n"
            "     possible. Avoid 'implement feature' — say 'create\n"
            "     src/routes/foo.py with GET /foo returning {…}'.\n\n"
            "  - task_type: one of feature_request, bug_report, doc_request,\n"
            "     demo_request, research_request, content_request.\n\n"
            "  - priority: one of low, medium, high. Foundation-layer tasks\n"
            "     are typically high; polish tasks are typically low.\n\n"
            "  - estimated_agent: one of backend_specialist, frontend_specialist,\n"
            "     tester_specialist, code_reviewer, devops_specialist,\n"
            "     content_creator, research_specialist. Pick the best fit, or\n"
            "     null if purely setup / cross-cutting.\n\n"
            "## Scale guidance\n"
            "- Aim for 15-40 tasks total. A trivial CLI tool may have 10;\n"
            "  a full-stack app with auth + multiple features may have 35.\n"
            "- Within a phase, list 2-6 tasks.\n"
            "- Order tasks so earlier ones don't block later ones when possible.\n\n"
            "## Style guidance\n"
            "- Be SPECIFIC. Mention concrete file paths, function names, env\n"
            "  vars, API endpoints, SQL tables — derived from the PRD's\n"
            "  Functional Requirements + Database Design.\n"
            "- Don't paraphrase the PRD as tasks — translate REQ-XXX IDs into\n"
            "  buildable units. Reference REQ-IDs in sub-tasks where helpful.\n"
            "- Use imperative engineering verbs ('create', 'wire', 'register',\n"
            "  'render', 'validate', 'migrate', 'mock', 'instrument').\n"
            "- No placeholder text. If unknown, write `TBD — <one-line reason>`.\n\n"
            "Now produce the full JSON array for the PRD above. Output ONLY\n"
            "the fenced JSON block — no commentary."
        )

    try:
        result = await executor.single_agent_call(
            agent_id="user_story_author",
            prompt=prompt,
            project_artifact_id=None,  # tasks aren't an artifact row
            project_id=project_id,     # but they ARE attributable by project
            label="Generating flat task list",
            # The phased task list with multi-line descriptions runs
            # 25-50 KB (15-40 tasks × 4-8 sub-tasks). Default 8192 cuts
            # off around task 10 — easy to miss because the JSON parser
            # silently truncates.
            max_tokens=32_000,
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

    # Persist rows. `review_input` is the same on every row in this list
    # version — that's why we denormalize rather than introduce a per-list
    # metadata table for v1.
    now = datetime.utcnow()
    saved: list[ProjectTask] = []
    review_input_value = review_comments or None
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
            review_input=review_input_value,
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


@router.delete("/{project_id}/tasks/{task_id}", status_code=204)
async def delete_task(
    project_id: str,
    task_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Hard-delete a single task row. Allowed only when the task is NOT
    actively in flight — i.e. task_status is one of {backlog, cancelled,
    failed, deployed}. For dispatched/in_progress/review/testing rows the
    user must cancel first (via the chat agent or by cancelling the
    underlying Request) before deleting.

    If the task had a linked Request, the request's source_task_id is
    nulled out — the Request continues to exist in History / Story Board,
    it just loses the project-task back-link."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    task = await state.get_task(task_id)
    if task is None or task.project_id != project_id:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found in project.")

    # In-flight states block deletion. Terminal states + backlog are fine.
    blocked_states = {
        TaskStatus.DISPATCHED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.REVIEW,
        TaskStatus.TESTING,
    }
    if task.task_status in blocked_states:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "task in flight",
                "task_status": str(task.task_status),
                "hint": "Cancel the task first (e.g. ask the chat agent to cancel it, or cancel the linked Request) before deleting.",
            },
        )

    await state.delete_task(task_id)
    return None


class BulkDeleteTasksBody(BaseModel):
    task_ids: list[str]


@router.post("/{project_id}/tasks/bulk_delete")
async def bulk_delete_tasks(
    project_id: str,
    body: BulkDeleteTasksBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Hard-delete multiple task rows in one call. Partial-success contract:
    in-flight or unknown task_ids are reported under `skipped` rather than
    failing the whole batch. The frontend's "Delete Selected" action uses
    this so users can sweep a list without worrying about a single in-flight
    row 409'ing the rest.

    Same per-task rule as the single-task endpoint: in-flight states
    (dispatched/in_progress/review/testing) are blocked; backlog +
    terminal states are deletable. Linked Requests have their
    `source_task_id` nulled out.

    Response shape:
      {"data": {"deleted": ["T-abc", ...],
                "skipped": [{"task_id": "T-xyz", "reason": "in_flight",
                             "task_status": "in_progress"}, ...]},
       "meta": {"requested": N, "deleted": M, "skipped": K}, ...}
    """
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    await _require_project(state, project_id)

    if not body.task_ids:
        raise HTTPException(status_code=400, detail="task_ids must contain at least one entry.")

    # Cap at a reasonable batch size to bound the DB work per request.
    # A finalized list rarely exceeds 50 tasks; 200 leaves headroom while
    # protecting against accidental floods.
    if len(body.task_ids) > 200:
        raise HTTPException(
            status_code=400,
            detail=f"bulk_delete capped at 200 task_ids per call (got {len(body.task_ids)}).",
        )

    blocked_states = {
        TaskStatus.DISPATCHED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.REVIEW,
        TaskStatus.TESTING,
    }
    deleted: list[str] = []
    skipped: list[dict[str, Any]] = []

    # De-dupe IDs in case the client sent the same task twice.
    for tid in dict.fromkeys(body.task_ids):
        task = await state.get_task(tid)
        if task is None or task.project_id != project_id:
            skipped.append({"task_id": tid, "reason": "not_found"})
            continue
        if task.task_status in blocked_states:
            skipped.append({
                "task_id": tid,
                "reason": "in_flight",
                "task_status": str(task.task_status),
            })
            continue
        await state.delete_task(tid)
        deleted.append(tid)

    return {
        "data": {"deleted": deleted, "skipped": skipped},
        "meta": {
            "requested": len(body.task_ids),
            "deleted": len(deleted),
            "skipped": len(skipped),
        },
        "error": None,
    }


@router.post("/{project_id}/tasks/finalize")
async def finalize_tasks(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """TSK-008. Flips the current draft list to finalized atomically (any
    previously-finalized version is archived first). Rejects empty drafts.

    On finalize, also renders the task list to markdown and writes it
    to ``C:/ai-projects/<ProjectName>/docs/tasks.md`` on the host, then
    pushes to the project's GitHub repo when ``repo_url`` is set. Both
    side effects are soft-fail (logged + reported in ``meta``)."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    project = await _require_project(state, project_id)

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

    # ── PM-finalize: render markdown + write to host + push to GitHub ──
    # Pull a finalized_at timestamp off any row (they all share the
    # same finalize_task_list transaction, so updated_at is consistent).
    finalized_at_iso = None
    if finalized and finalized[0].updated_at:
        finalized_at_iso = finalized[0].updated_at.isoformat()
    md = render_tasks_markdown(
        project.name, finalized,
        list_version=list_version,
        finalized_at_iso=finalized_at_iso,
    )
    host_result = write_finalized_tasks(project.name, md)
    if not host_result.ok:
        logger.warning(
            "tasks_finalize.host_write_failed",
            project_id=project_id, project_name=project.name,
            error=host_result.error,
        )

    # Push the rendered markdown to the project's GitHub repo (mirrors
    # the PRD push above). Task lists aren't ProjectArtifact rows —
    # they're rows in `project_tasks` — so we pass the rendered
    # markdown directly rather than synthesizing a fake artifact.
    github_result = await _push_finalized_doc_to_repo(
        project=project,
        repo_path="docs/tasks.md",
        content=md,
        commit_subject=f"docs: finalize task list v{list_version}",
        descriptor=f"task list v{list_version} ({len(finalized)} tasks)",
        actor=user.get("username") or user.get("user_id") or "unknown",
    )

    return {
        "data": [_task_to_dict(t) for t in finalized],
        "meta": {
            "list_version": list_version,
            "count": len(finalized),
            "host_write": host_result.as_dict(),
            "github_push": github_result,
        },
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


# In-flight statuses that short-circuit dispatch as no-ops (per BLD-004
# extended for retry — only terminal-but-not-deployed rows are eligible
# for re-dispatch). Module-level so the shared dispatch helper + the
# auto-dispatch event handler agree on the set.
_DISPATCH_IN_FLIGHT_STATUSES = {
    TaskStatus.DISPATCHED, TaskStatus.IN_PROGRESS,
    TaskStatus.REVIEW, TaskStatus.TESTING,
}


async def _dispatch_one_task(
    state, orchestrator, task: ProjectTask, user_id: str,
) -> dict[str, Any]:
    """Internal helper: submit one ProjectTask through the orchestrator
    and stamp the row with the new request_id + DISPATCHED status.
    Caller is responsible for ALL pre-flight checks (list_status,
    depends_on, in-flight short-circuit).

    Returns the same shape as the dispatch endpoint's `dispatched`
    array entries — caller decides whether to use the returned dict
    or aggregate."""
    if (task.task_status in _DISPATCH_IN_FLIGHT_STATUSES
            or task.task_status == TaskStatus.DEPLOYED) and task.request_id:
        return {
            "task_id": task.task_id,
            "request_id": task.request_id,
            "status": "already_dispatched",
        }
    req = await orchestrator.submit(
        description=task.description or task.title,
        task_type=task.task_type,
        priority=task.priority,
        created_by=user_id,
        project_id=task.project_id,
        source_task_id=task.task_id,
    )
    await state.set_task_status(task.task_id, TaskStatus.DISPATCHED, request_id=req.request_id)
    return {
        "task_id": task.task_id,
        "request_id": req.request_id,
        "status": "dispatched",
    }


async def _check_dependencies_unmet(
    state, task: ProjectTask,
) -> list[dict[str, str]] | None:
    """BPD-201 — returns a list of unmet blockers if `task` can't yet
    dispatch (blockers not all `deployed`), else None. Legacy tasks
    with depends_on=[] always return None (no blockers)."""
    if not task.depends_on:
        return None
    blockers = await state.get_task_blockers(task.task_id)
    blocker_by_id = {b.task_id: b for b in blockers}
    unmet: list[dict[str, str]] = []
    for dep_id in task.depends_on:
        b = blocker_by_id.get(dep_id)
        if b is None:
            unmet.append({
                "task_id": dep_id,
                "title": "(missing — dangling depends_on reference)",
                "status": "missing",
            })
            continue
        if b.task_status != TaskStatus.DEPLOYED:
            unmet.append({
                "task_id": dep_id,
                "title": b.title,
                "status": str(b.task_status),
            })
    return unmet or None


@router.post("/{project_id}/build/dispatch")
async def dispatch_tasks(
    project_id: str,
    body: DispatchBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """For each task_id: validate it belongs to this project AND its current
    task_status is 'backlog' AND its depends_on chain is satisfied.

    Returns 409 dependencies_unmet (BPD-201) when ANY task in the batch
    has unmet dependencies, with the blocker list per offending task.
    Otherwise dispatches the batch.

    Already-dispatched tasks are returned as no-ops with the existing
    request_id (idempotent per BLD-004). Archived or unknown task_ids
    fail the dispatch with a 400 listing the offenders."""
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

    # BPD-20: dependency pre-check. For tasks that are NOT already
    # in-flight / deployed (those short-circuit anyway), refuse if
    # their depends_on chain isn't fully satisfied. The check is
    # all-or-nothing per request — if ANY task has unmet deps, the
    # whole batch is refused so the UI gets a consistent picture.
    unmet_per_task: dict[str, list[dict[str, str]]] = {}
    for tid, task in tasks_by_id.items():
        if task.task_status in _DISPATCH_IN_FLIGHT_STATUSES or task.task_status == TaskStatus.DEPLOYED:
            continue
        unmet = await _check_dependencies_unmet(state, task)
        if unmet:
            unmet_per_task[tid] = unmet
    if unmet_per_task:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "dependencies_unmet",
                "blockers": unmet_per_task,
                "hint": (
                    "Dispatch the blockers first, or use "
                    "POST /build/dispatch-all-ready to fire every "
                    "task in the project whose deps are met."
                ),
            },
        )

    orchestrator = request.app.state.orchestrator
    dispatched: list[dict[str, Any]] = []
    for tid, task in tasks_by_id.items():
        try:
            result = await _dispatch_one_task(
                state, orchestrator, task, user.get("user_id") or "",
            )
        except ValueError as e:
            errors.append(f"{tid}: {e}")
            continue
        dispatched.append(result)

    return {
        "data": {"dispatched": dispatched},
        "meta": {
            "count": len(dispatched),
            "errors": errors if errors else None,
        },
        "error": None,
    }


# ── BPD-21/22/23 — Dispatch by scope (feature / epic / all-ready) ────────


async def _dispatch_ready_tasks(
    state, orchestrator, tasks: list[ProjectTask], user_id: str,
) -> dict[str, Any]:
    """Run the same dispatch loop as dispatch_tasks, but filter to
    tasks whose deps are met AND that are backlog / failed / cancelled.
    Returns {dispatched: [...], blocked: [...], skipped: [...]}."""
    dispatched: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for task in tasks:
        if task.list_status != ArtifactStatus.FINALIZED:
            skipped.append({"task_id": task.task_id, "reason": "not_finalized"})
            continue
        if (task.task_status in _DISPATCH_IN_FLIGHT_STATUSES
                or task.task_status == TaskStatus.DEPLOYED):
            skipped.append({
                "task_id": task.task_id,
                "reason": "already_dispatched_or_deployed",
            })
            continue
        unmet = await _check_dependencies_unmet(state, task)
        if unmet:
            blocked.append({"task_id": task.task_id, "blockers": unmet})
            continue
        try:
            result = await _dispatch_one_task(state, orchestrator, task, user_id)
            dispatched.append(result)
        except ValueError as e:
            blocked.append({"task_id": task.task_id, "error": str(e)})
    return {
        "dispatched": dispatched,
        "blocked": blocked,
        "skipped": skipped,
    }


@router.post("/{project_id}/build/dispatch-feature/{feature_id}")
async def dispatch_feature(
    project_id: str,
    feature_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """BPD-21 — dispatch every task under a feature whose deps are met.
    Tasks with unmet deps are returned in the `blocked` list with their
    blocker chain; they'll auto-dispatch as their deps clear (when
    auto_dispatch_on_deploy is on)."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    await _require_project(state, project_id)
    feature = await state.get_feature(feature_id)
    if feature is None or feature.project_id != project_id:
        raise HTTPException(status_code=404, detail="Feature not found in this project.")

    all_tasks = await state.list_tasks_for_project(project_id)
    feature_tasks = [t for t in all_tasks if t.feature_id == feature_id]
    if not feature_tasks:
        raise HTTPException(
            status_code=404,
            detail="Feature has no tasks. Run Pass 3 generation first.",
        )

    orchestrator = request.app.state.orchestrator
    result = await _dispatch_ready_tasks(
        state, orchestrator, feature_tasks, user.get("user_id") or "",
    )
    return {
        "data": result,
        "meta": {
            "feature_id": feature_id,
            "dispatched_count": len(result["dispatched"]),
            "blocked_count": len(result["blocked"]),
            "skipped_count": len(result["skipped"]),
        },
        "error": None,
    }


@router.post("/{project_id}/build/dispatch-epic/{epic_id}")
async def dispatch_epic(
    project_id: str,
    epic_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """BPD-22 — dispatch every task across every feature under an epic
    whose deps are met. Tasks with unmet deps are reported (will auto-
    dispatch if the toggle is on)."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    await _require_project(state, project_id)
    epic = await state.get_epic(epic_id)
    if epic is None or epic.project_id != project_id:
        raise HTTPException(status_code=404, detail="Epic not found in this project.")

    features = await state.list_features_for_epic(epic_id)
    feature_ids = {f.feature_id for f in features}
    all_tasks = await state.list_tasks_for_project(project_id)
    epic_tasks = [t for t in all_tasks if t.feature_id in feature_ids]
    if not epic_tasks:
        raise HTTPException(
            status_code=404,
            detail="Epic has no tasks. Run Pass 3 generation for its features first.",
        )

    orchestrator = request.app.state.orchestrator
    result = await _dispatch_ready_tasks(
        state, orchestrator, epic_tasks, user.get("user_id") or "",
    )
    return {
        "data": result,
        "meta": {
            "epic_id": epic_id,
            "feature_count": len(feature_ids),
            "dispatched_count": len(result["dispatched"]),
            "blocked_count": len(result["blocked"]),
            "skipped_count": len(result["skipped"]),
        },
        "error": None,
    }


@router.post("/{project_id}/build/dispatch-all-ready")
async def dispatch_all_ready(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """BPD-23 — fan out every backlog task in the project whose deps
    are met. Re-evaluates on each `request.deployed` event when
    auto_dispatch_on_deploy is True (BPD-24)."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    await _require_project(state, project_id)

    # Use the dedicated helper which already encodes the same predicate
    # (backlog + finalized + deps satisfied). Then dispatch each.
    ready = await state.get_dispatchable_tasks(project_id)
    orchestrator = request.app.state.orchestrator
    dispatched: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for task in ready:
        try:
            result = await _dispatch_one_task(
                state, orchestrator, task, user.get("user_id") or "",
            )
            dispatched.append(result)
        except ValueError as e:
            errors.append({"task_id": task.task_id, "error": str(e)})

    # Count what's still blocked for reporting
    all_tasks = await state.list_tasks_for_project(project_id)
    blocked_count = sum(
        1 for t in all_tasks
        if t.task_status == TaskStatus.BACKLOG
        and t.list_status == ArtifactStatus.FINALIZED
        and t.depends_on
        and t.task_id not in {d["task_id"] for d in dispatched}
    )
    return {
        "data": {"dispatched": dispatched, "errors": errors or None},
        "meta": {
            "dispatched_count": len(dispatched),
            "blocked_count": blocked_count,
        },
        "error": None,
    }


# ── BPD-27 — Status rollups (feature / epic completion derived) ──────────


@router.get("/{project_id}/features/{feature_id}/status")
async def get_feature_status(
    project_id: str,
    feature_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Rollup: a feature is 'complete' when every task under it is
    `deployed`. Returns counts by task_status + a derived overall
    label ('complete' | 'in_progress' | 'backlog')."""
    state = request.app.state.state_store
    feature = await state.get_feature(feature_id)
    if feature is None or feature.project_id != project_id:
        raise HTTPException(status_code=404, detail="Feature not found.")
    all_tasks = await state.list_tasks_for_project(project_id)
    children = [t for t in all_tasks if t.feature_id == feature_id]
    counts = _count_by_status(children)
    return {
        "data": {
            "feature_id": feature_id,
            "task_count": len(children),
            "counts_by_status": counts,
            "complete": len(children) > 0 and counts.get("deployed", 0) == len(children),
        },
        "meta": None,
        "error": None,
    }


@router.get("/{project_id}/epics/{epic_id}/status")
async def get_epic_status(
    project_id: str,
    epic_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Rollup: an epic is 'complete' when every feature under it is
    complete. Returns per-feature status + aggregate task counts."""
    state = request.app.state.state_store
    epic = await state.get_epic(epic_id)
    if epic is None or epic.project_id != project_id:
        raise HTTPException(status_code=404, detail="Epic not found.")
    features = await state.list_features_for_epic(epic_id)
    feature_ids = {f.feature_id for f in features}
    all_tasks = await state.list_tasks_for_project(project_id)
    epic_tasks = [t for t in all_tasks if t.feature_id in feature_ids]
    per_feature: list[dict[str, Any]] = []
    for f in features:
        ft = [t for t in epic_tasks if t.feature_id == f.feature_id]
        ft_counts = _count_by_status(ft)
        per_feature.append({
            "feature_id": f.feature_id,
            "title": f.title,
            "task_count": len(ft),
            "counts_by_status": ft_counts,
            "complete": len(ft) > 0 and ft_counts.get("deployed", 0) == len(ft),
        })
    epic_counts = _count_by_status(epic_tasks)

    # BPD-33 — rollup stats for the epic-detail popup. Walks every
    # request that ever ran for an epic task and sums:
    #   - cost: actual_cost_usd (falls back to estimated when missing)
    #   - wall_time: completed_at - created_at per request
    #   - commits: unique commit_sha entries (deduped — one feature can
    #     land in multiple requests but ship as one commit)
    # All three degrade gracefully — tasks that never ran (request_id
    # null) contribute 0. The popup is read-only so we accept a small
    # over-fetch here in exchange for one round-trip end-to-end.
    request_ids = [t.request_id for t in epic_tasks if t.request_id]
    total_cost_usd = 0.0
    total_wall_seconds = 0
    commit_shas: set[str] = set()
    for rid in request_ids:
        try:
            req = await state.get_request(rid)
        except Exception:
            req = None
        if req is None:
            continue
        cost = getattr(req, "actual_cost_usd", None)
        if cost is None:
            cost = getattr(req, "estimated_cost_usd", None) or 0.0
        total_cost_usd += float(cost or 0.0)
        started = getattr(req, "created_at", None)
        completed = getattr(req, "completed_at", None)
        if started and completed:
            try:
                total_wall_seconds += int((completed - started).total_seconds())
            except Exception:
                pass
        sha = getattr(req, "commit_sha", None)
        if sha:
            commit_shas.add(sha)

    rollup_stats = {
        "cost_usd": round(total_cost_usd, 4),
        "wall_seconds": total_wall_seconds,
        "commit_count": len(commit_shas),
        "commit_shas": sorted(commit_shas),
        "requests_walked": len(request_ids),
    }

    return {
        "data": {
            "epic_id": epic_id,
            "title": epic.title,
            "feature_count": len(features),
            "features_complete": sum(1 for f in per_feature if f["complete"]),
            "task_count": len(epic_tasks),
            "counts_by_status": epic_counts,
            "complete": (
                len(features) > 0
                and all(f["complete"] for f in per_feature)
            ),
            "features": per_feature,
            "rollup_stats": rollup_stats,
        },
        "meta": None,
        "error": None,
    }


def _count_by_status(tasks: list[ProjectTask]) -> dict[str, int]:
    """Histogram task_status enum values to a {status_str: count} dict
    suitable for JSON serialization. Used by feature/epic rollup
    endpoints."""
    out: dict[str, int] = {}
    for t in tasks:
        key = str(t.task_status)
        out[key] = out.get(key, 0) + 1
    return out


# ── BPD-29-prerequisite — GET endpoints for the UI to read epics / features ─


@router.get("/{project_id}/epics")
async def list_epics(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Return all epics for a project (latest non-archived version).
    Used by the Task List page's epic-level rendering and by the
    project header rollup chip (BPD-34)."""
    state = request.app.state.state_store
    await _require_project(state, project_id)
    epics = await state.list_epics_for_project(project_id)
    return {
        "data": [e.model_dump(mode="json") for e in epics],
        "meta": {"count": len(epics)},
        "error": None,
    }


class BulkDeleteEpicsBody(BaseModel):
    epic_ids: list[str]


@router.post("/{project_id}/epics/bulk_delete")
async def bulk_delete_epics(
    project_id: str,
    body: BulkDeleteEpicsBody,
    request: Request,
    user: dict = Depends(require_role("developer", "admin")),
):
    """Hard-delete multiple epics and cascade their features + task
    back-links in one call.

    Mirrors `requests.bulk_delete` shape so the UI's selection / partial-
    success handling can use the same envelope. Per-row pre-check skips
    epics that don't exist or don't belong to ``project_id`` (defence
    against stale UI selections). The DB-level cascade in
    ``state.delete_epic`` NULLs ``project_tasks.feature_id`` for any
    tasks whose feature lived under the deleted epic, then drops the
    feature rows, then the epic — same behavior as the existing single-
    epic delete path, just batched.

    Returns:
        {data: {deleted: [epic_id, …], skipped: [{epic_id, reason}, …]},
         meta: {requested, deleted_count, skipped_count,
                cascaded_features, cascaded_tasks_unlinked},
         error: None}
    """
    if not body.epic_ids:
        raise HTTPException(
            status_code=400,
            detail="epic_ids must contain at least one entry.",
        )
    # Cap the batch — same 100-row ceiling as bulk_delete_requests so a
    # runaway selection (e.g. "select all" on a 1000-epic project) can't
    # lock the DB for minutes.
    if len(body.epic_ids) > 100:
        raise HTTPException(
            status_code=400,
            detail="epic_ids may contain at most 100 entries per call.",
        )

    state = request.app.state.state_store
    await _require_project(state, project_id)

    deleted: list[str] = []
    skipped: list[dict[str, str]] = []
    cascaded_features = 0
    cascaded_tasks = 0
    seen: set[str] = set()
    for eid in body.epic_ids:
        if eid in seen:
            continue
        seen.add(eid)
        epic = await state.get_epic(eid)
        if epic is None:
            skipped.append({"epic_id": eid, "reason": "not_found"})
            continue
        # Defence-in-depth: don't let a stale UI selection cross-delete
        # an epic from a different project.
        if epic.project_id != project_id:
            skipped.append({"epic_id": eid, "reason": "wrong_project"})
            continue
        # Count features + tasks-about-to-be-unlinked BEFORE the cascade
        # so the response can tell the user the blast radius. Uses direct
        # SQL because there's no `list_tasks_for_feature` helper on the
        # store and we want a single roundtrip per epic, not N+1.
        feats: list = []
        tasks_to_unlink = 0
        try:
            feats = await state.list_features_for_epic(eid)
            feat_ids = [f.feature_id for f in feats]
            if feat_ids:
                db = await state._get_db()
                placeholders = ",".join("?" * len(feat_ids))
                async with db.execute(
                    f"SELECT COUNT(*) AS n FROM project_tasks "
                    f"WHERE feature_id IN ({placeholders})",
                    feat_ids,
                ) as cur:
                    row = await cur.fetchone()
                    tasks_to_unlink = int(row["n"]) if row else 0
        except Exception:
            pass

        try:
            await state.delete_epic(eid)
            deleted.append(eid)
            cascaded_features += len(feats)
            cascaded_tasks += tasks_to_unlink
        except Exception as e:  # noqa: BLE001
            skipped.append({"epic_id": eid, "reason": f"delete_failed: {e!s}"[:200]})

    # Sync build-plan.md + tasks.md so the host files reflect the
    # post-cascade DB state. Without this, both files kept the deleted
    # epics' rows until the next pass touched them — confusing for the
    # user who deleted via the UI and expected the editor view to match.
    project = await state.get_project(project_id)
    sync = await _sync_build_plan_to_disk(
        state, project,
        actor=user.get("username") or user.get("user_id") or "unknown",
    ) if project else None

    return {
        "data": {"deleted": deleted, "skipped": skipped},
        "meta": {
            "requested": len(body.epic_ids),
            "deleted_count": len(deleted),
            "skipped_count": len(skipped),
            "cascaded_features": cascaded_features,
            "cascaded_tasks_unlinked": cascaded_tasks,
            "sync": sync,
        },
        "error": None,
    }


@router.post("/{project_id}/build-plan/reset")
async def reset_build_plan(
    project_id: str,
    request: Request,
    user: dict = Depends(require_role("developer", "admin")),
):
    """Hard-reset the entire BPD hierarchy for a project: every epic,
    every feature, every UNSTARTED backlog task is deleted in one call.

    Started / completed tasks (those with a request_id) survive the
    cascade with `feature_id=NULL` so the user keeps the work history
    they already paid compute for — those become "Legacy" pseudo-epic
    rows. The PRD and API Spec are NOT touched; this is a build-plan
    reset, not a project reset.

    Use when you want a clean slate to re-run the 3-pass generation —
    typically after a bad cascade ran or the user wants to redo the
    plan from scratch with different brief / PRD wording.

    Returns:
        {data: {epics_deleted, features_deleted, tasks_deleted,
                tasks_preserved_as_legacy},
         meta: {project_id, project_name}, error: None}
    """
    state = request.app.state.state_store
    project = await _require_project(state, project_id)
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))

    # Snapshot the counts BEFORE the cascade so the response can tell
    # the user what just happened (and confirm the operation ran).
    db = await state._get_db()
    async with db.execute(
        "SELECT COUNT(*) AS n FROM epics WHERE project_id=?", (project_id,),
    ) as c:
        epics_before = int((await c.fetchone())["n"])
    async with db.execute(
        "SELECT COUNT(*) AS n FROM features WHERE project_id=?", (project_id,),
    ) as c:
        features_before = int((await c.fetchone())["n"])
    async with db.execute(
        "SELECT COUNT(*) AS n FROM project_tasks WHERE project_id=? "
        "AND task_status='backlog' AND (request_id IS NULL OR request_id='')",
        (project_id,),
    ) as c:
        backlog_before = int((await c.fetchone())["n"])
    async with db.execute(
        "SELECT COUNT(*) AS n FROM project_tasks WHERE project_id=? "
        "AND NOT (task_status='backlog' AND (request_id IS NULL OR request_id=''))",
        (project_id,),
    ) as c:
        dispatched_before = int((await c.fetchone())["n"])

    # Walk the epics + cascade-delete via the existing path (which
    # uses the hardened "hard-delete backlog, preserve dispatched"
    # semantics from delete_epic). One iteration per epic so the
    # cascade rules are applied uniformly — no special-case bulk path
    # to maintain.
    epics = await state.list_epics_for_project(project_id)
    for e in epics:
        try:
            await state.delete_epic(e.epic_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "build_plan_reset_epic_delete_failed",
                project_id=project_id, epic_id=e.epic_id, error=str(exc),
            )

    # Safety net: there may be backlog orphans whose epic was already
    # gone before this call ran (e.g. from a partial earlier cascade).
    # Sweep them up so the project_tasks counter goes to a clean
    # "only dispatched tasks remain" state.
    await db.execute(
        "DELETE FROM project_tasks WHERE project_id=? "
        "AND task_status='backlog' AND (request_id IS NULL OR request_id='')",
        (project_id,),
    )
    await db.commit()

    # Sync the now-empty (or legacy-only) plan to docs/build-plan.md so
    # the host file matches the DB. Without this, the user's editor
    # would keep showing the pre-reset plan until they manually
    # refreshed by re-running a pass.
    sync = await _sync_build_plan_to_disk(
        state, project,
        actor=user.get("username") or user.get("user_id") or "unknown",
    )

    return {
        "data": {
            "epics_deleted": epics_before,
            "features_deleted": features_before,
            "tasks_deleted": backlog_before,
            "tasks_preserved_as_legacy": dispatched_before,
            "sync": sync,
        },
        "meta": {
            "project_id": project_id,
            "project_name": getattr(project, "name", None),
        },
        "error": None,
    }


@router.post("/{project_id}/epics/{epic_id}/finalize")
async def finalize_epic_endpoint(
    project_id: str,
    epic_id: str,
    request: Request,
    user: dict = Depends(require_role("developer", "admin")),
):
    """Finalize one epic + every feature under it + every task under
    those features. Per-row alternative to the project-wide finalize.

    Idempotent — already-finalized rows stay finalized, archived rows
    aren't touched. Returns counts of rows actually flipped so the
    user knows what was locked.

    The PRD pattern locks epic/feature/task structure but doesn't
    affect task_status (a backlog task stays backlog after its list_status
    flips to finalized — finalize means "the plan is approved", not
    "the work is done").

    Also rewrites docs/build-plan.md so the host file mirrors the new
    status badges immediately.
    """
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    project = await _require_project(state, project_id)
    epic = await state.get_epic(epic_id)
    if epic is None or epic.project_id != project_id:
        raise HTTPException(status_code=404, detail="Epic not found in this project.")
    if str(epic.list_status) == str(ArtifactStatus.ARCHIVED):
        raise HTTPException(
            status_code=409,
            detail="Cannot finalize an archived epic. Regenerate or restore first.",
        )
    counts = await state.finalize_epic_subtree(epic_id)
    sync = await _sync_build_plan_to_disk(
        state, project,
        actor=user.get("username") or user.get("user_id") or "unknown",
    )
    return {
        "data": {
            "epic_id": epic_id,
            "epic_title": epic.title,
            "epics_finalized": counts["epics"],
            "features_finalized": counts["features"],
            "tasks_finalized": counts["tasks"],
        },
        "meta": {"sync": sync},
        "error": None,
    }


@router.post("/{project_id}/features/{feature_id}/finalize")
async def finalize_feature_endpoint(
    project_id: str,
    feature_id: str,
    request: Request,
    user: dict = Depends(require_role("developer", "admin")),
):
    """Finalize one feature + every task under it.

    Per-feature alternative to ``epics/{eid}/finalize``. The parent
    epic is NOT auto-finalized (a finalized feature inside a still-
    draft epic is a valid state — means "this feature's spec is
    locked but the surrounding epic structure isn't yet").
    """
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    project = await _require_project(state, project_id)
    feature = await state.get_feature(feature_id)
    if feature is None or feature.project_id != project_id:
        raise HTTPException(status_code=404, detail="Feature not found in this project.")
    if str(feature.list_status) == str(ArtifactStatus.ARCHIVED):
        raise HTTPException(
            status_code=409,
            detail="Cannot finalize an archived feature.",
        )
    counts = await state.finalize_feature_subtree(feature_id)
    sync = await _sync_build_plan_to_disk(
        state, project,
        actor=user.get("username") or user.get("user_id") or "unknown",
    )
    return {
        "data": {
            "feature_id": feature_id,
            "feature_title": feature.title,
            "features_finalized": counts["features"],
            "tasks_finalized": counts["tasks"],
        },
        "meta": {"sync": sync},
        "error": None,
    }


@router.post("/{project_id}/epics/{epic_id}/unfinalize")
async def unfinalize_epic_endpoint(
    project_id: str,
    epic_id: str,
    request: Request,
    user: dict = Depends(require_role("developer", "admin")),
):
    """Flip a finalized epic (+ features + tasks) back to draft.

    Inverse of ``finalize`` — same scope, same idempotent semantics.
    Lets the user fix an accidental finalize without going through
    archive + regenerate (which would lose generated content).

    Refuses to operate on archived rows (use the archive-restore path
    if you want one back).
    """
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    project = await _require_project(state, project_id)
    epic = await state.get_epic(epic_id)
    if epic is None or epic.project_id != project_id:
        raise HTTPException(status_code=404, detail="Epic not found in this project.")
    if str(epic.list_status) == str(ArtifactStatus.ARCHIVED):
        raise HTTPException(
            status_code=409,
            detail="Cannot unfinalize an archived epic.",
        )
    counts = await state.unfinalize_epic_subtree(epic_id)
    sync = await _sync_build_plan_to_disk(
        state, project,
        actor=user.get("username") or user.get("user_id") or "unknown",
    )
    return {
        "data": {
            "epic_id": epic_id,
            "epic_title": epic.title,
            "epics_unfinalized": counts["epics"],
            "features_unfinalized": counts["features"],
            "tasks_unfinalized": counts["tasks"],
        },
        "meta": {"sync": sync},
        "error": None,
    }


@router.post("/{project_id}/features/{feature_id}/unfinalize")
async def unfinalize_feature_endpoint(
    project_id: str,
    feature_id: str,
    request: Request,
    user: dict = Depends(require_role("developer", "admin")),
):
    """Flip a finalized feature (+ its tasks) back to draft.
    Doesn't touch the parent epic."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    project = await _require_project(state, project_id)
    feature = await state.get_feature(feature_id)
    if feature is None or feature.project_id != project_id:
        raise HTTPException(status_code=404, detail="Feature not found in this project.")
    if str(feature.list_status) == str(ArtifactStatus.ARCHIVED):
        raise HTTPException(
            status_code=409,
            detail="Cannot unfinalize an archived feature.",
        )
    counts = await state.unfinalize_feature_subtree(feature_id)
    sync = await _sync_build_plan_to_disk(
        state, project,
        actor=user.get("username") or user.get("user_id") or "unknown",
    )
    return {
        "data": {
            "feature_id": feature_id,
            "feature_title": feature.title,
            "features_unfinalized": counts["features"],
            "tasks_unfinalized": counts["tasks"],
        },
        "meta": {"sync": sync},
        "error": None,
    }


@router.get("/{project_id}/features")
async def list_features(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Return all features for a project, scoped to the latest
    non-archived list_version. Returned flat (epic_id on each row);
    UI groups by epic when rendering the 3-level tree."""
    state = request.app.state.state_store
    await _require_project(state, project_id)
    features = await state.list_features_for_project(project_id)
    return {
        "data": [f.model_dump(mode="json") for f in features],
        "meta": {"count": len(features)},
        "error": None,
    }


# ── BPD-34 — project-level rollup chip data ──────────────────────────────


# ── BPD-39 — Legacy decomposition (optional migration tool) ──────────────


@router.post("/{project_id}/build-plan/decompose-legacy", status_code=202)
async def decompose_legacy_tasks(
    project_id: str,
    request: Request,
    body: dict | None = None,
    user: dict = Depends(get_current_user),
):
    """BPD-39 — optional migration tool. Runs the three-pass generation
    against the project's existing PRD and surfaces the proposed
    hierarchy alongside the legacy task count. Does NOT delete legacy
    tasks — they continue to live under the synthetic "Legacy" epic
    until the user explicitly archives them.

    Request body (optional): `{dry_run: bool=true}` — when `dry_run`
    is True (default), only Pass 1 runs and the projected feature/task
    counts are estimated rather than generated. When False, the full
    three-pass cascade runs (BPD-17 orchestrator) and the legacy task
    count is annotated on the response so the UI can show a diff.

    Returns:
        {data: {epics_generated, features_generated, tasks_generated,
                legacy_task_count, list_version}, meta: None, error: None}
    """
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    await _require_project(state, project_id)

    # Refuse if epics already exist for this project — caller must
    # archive the existing epic list first OR use the regular Pass 1
    # endpoint with review_comments.
    existing = await state.list_epics_for_project(project_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "epics_already_exist",
                "hint": (
                    "This project already has an epic list. Archive it first "
                    "or use /epics/generate with review_comments to amend it. "
                    "decompose-legacy is for projects that ONLY have legacy "
                    "(pre-BPD) tasks."
                ),
            },
        )

    all_tasks = await state.list_tasks_for_project(project_id)
    legacy_count = sum(1 for t in all_tasks if not t.feature_id)
    dry_run = bool((body or {}).get("dry_run", True))

    if dry_run:
        # Run only Pass 1 so the user can review epics before
        # committing the full cascade. The response shape signals
        # which mode ran via `features_generated`/`tasks_generated=0`.
        pass1 = await generate_epics(project_id, request, None, user)
        return {
            "data": {
                "epics_generated": pass1["meta"]["count"],
                "features_generated": 0,
                "tasks_generated": 0,
                "legacy_task_count": legacy_count,
                "list_version": pass1["meta"]["list_version"],
                "next_step": (
                    "Review the epics in the Build Plan view. Approve by "
                    "clicking 'Generate Features' on each (or 'Approve "
                    "All & Run All Three Passes' for the full cascade)."
                ),
            },
            "meta": {"dry_run": True},
            "error": None,
        }

    # Full cascade via the orchestrator endpoint
    result = await generate_build_plan(project_id, request, None, user)
    epics_n = result["data"]["epic_count"]
    features_n = sum(result["data"]["feature_counts_by_epic"].values())
    tasks_n = sum(result["data"]["task_counts_by_feature"].values())
    return {
        "data": {
            "epics_generated": epics_n,
            "features_generated": features_n,
            "tasks_generated": tasks_n,
            "legacy_task_count": legacy_count,
            "list_version": result["data"]["list_version"],
            "next_step": (
                "Review the generated hierarchy in the Build Plan view. "
                "Legacy tasks render under a synthetic 'Legacy' epic at "
                "the bottom; archive them explicitly when you're ready."
            ),
        },
        "meta": {"dry_run": False},
        "error": None,
    }


@router.get("/{project_id}/build-plan/rollup")
async def get_build_plan_rollup(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """One-shot fetch for the project header stat chip:
       {epics_done, epics_total, tasks_done, tasks_total, tasks_blocked}.

    `blocked` counts tasks whose depends_on chain isn't satisfied AND
    that aren't yet in flight."""
    state = request.app.state.state_store
    await _require_project(state, project_id)
    epics = await state.list_epics_for_project(project_id)
    features = await state.list_features_for_project(project_id)
    tasks = await state.list_tasks_for_project(project_id)

    # Epic complete = every feature under it is complete = every task
    # under those features is deployed.
    feature_by_id = {f.feature_id: f for f in features}
    feature_complete: dict[str, bool] = {}
    for f in features:
        ft = [t for t in tasks if t.feature_id == f.feature_id]
        feature_complete[f.feature_id] = (
            len(ft) > 0 and all(t.task_status == TaskStatus.DEPLOYED for t in ft)
        )
    epics_done = 0
    for e in epics:
        epic_feature_ids = [f.feature_id for f in features if f.epic_id == e.epic_id]
        if epic_feature_ids and all(
            feature_complete.get(fid, False) for fid in epic_feature_ids
        ):
            epics_done += 1

    tasks_done = sum(1 for t in tasks if t.task_status == TaskStatus.DEPLOYED)
    # Blocked = backlog + has unmet deps. Tasks without deps aren't
    # "blocked" — they're just not dispatched yet.
    dispatchable = await state.get_dispatchable_tasks(project_id)
    dispatchable_ids = {t.task_id for t in dispatchable}
    tasks_blocked = sum(
        1 for t in tasks
        if t.task_status == TaskStatus.BACKLOG
        and t.list_status == ArtifactStatus.FINALIZED
        and t.depends_on
        and t.task_id not in dispatchable_ids
    )
    return {
        "data": {
            "epics_done": epics_done,
            "epics_total": len(epics),
            "tasks_done": tasks_done,
            "tasks_total": len(tasks),
            "tasks_blocked": tasks_blocked,
            "features_total": len(features),
        },
        "meta": None,
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


@router.delete("/{project_id}/build/messages")
async def clear_build_messages(
    project_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Hard-delete the entire Build Chat transcript for this project.
    Backs the "Clear chat" button in the UI. Tasks, artifacts, and
    other project state are NOT touched — only the conversation
    history. Idempotent: 200 even if there were no messages."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    await _require_project(state, project_id)
    deleted = await state.delete_messages_for_project(project_id)
    events = request.app.state.events
    await events.emit("project.chat_cleared", {
        "project_id": project_id,
        "deleted": deleted,
        "by": user.get("user_id"),
    })
    return {
        "data": None,
        "meta": {"deleted": deleted},
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


# ════════════════════════════════════════════════════════════════════════════
# Build Plan Decomposition — Phase B (BPD-10..19)
# ════════════════════════════════════════════════════════════════════════════
# Three-pass generation: PRD → Epics → Features → Atomic Tasks.
# Each pass has its own endpoint + review-comments flow so the user
# can approve / regenerate one level without disturbing the others.
# Pass-3 persistence runs cycle detection (BPD-08) before insert.
# ════════════════════════════════════════════════════════════════════════════


def _extract_json_array(text: str) -> tuple[list[dict[str, Any]] | None, str]:
    """Extract a JSON array of objects from agent output.

    Strategy:
      1. Prefer a ```json fenced block.
      2. Fall back to the FIRST raw `[ ... ]` substring.
      3. Returns (None, mode) where mode ∈ {"empty", "malformed"} on
         failure so the caller can surface a clear error.

    Returns (parsed_list_or_None, mode) where mode is one of:
      "json"      — fenced block parsed cleanly
      "raw"       — raw [...] parsed (no fence)
      "malformed" — found JSON-ish text but couldn't parse
      "empty"     — nothing found
    """
    if not text:
        return None, "empty"
    fenced = re.search(r"```json\s*\n(.+?)\n```", text, re.DOTALL)
    if fenced:
        try:
            data = json.loads(fenced.group(1).strip())
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)], "json"
        except json.JSONDecodeError:
            pass
    # Fall back to first `[...]` substring at top-level
    bracket = re.search(r"\[\s*\{.+?\}\s*\]", text, re.DOTALL)
    if bracket:
        try:
            data = json.loads(bracket.group(0))
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)], "raw"
        except json.JSONDecodeError:
            pass
    if fenced or bracket:
        return None, "malformed"
    return None, "empty"


async def _sync_build_plan_to_disk(
    state: Any,
    project: Project,
    *,
    actor: str = "system",
    push_to_github: bool = True,
) -> dict[str, Any]:
    """Rewrite the project's `docs/build-plan.md` AND `docs/tasks.md` on
    the host filesystem (and optionally push to GitHub) from the live
    DB state.

    Two files always travel together:
      - **build-plan.md** — the full Epic → Feature → Task hierarchy
        including drafts. Always rewritten — even when nothing is
        finalized — so the editor view mirrors what the BuildPlanView
        UI shows.
      - **tasks.md** — flat view of FINALIZED tasks only, grouped by
        Epic → Feature. Always rewritten too — gets a "no finalized
        tasks yet" placeholder when empty. Without this, `tasks.md`
        was the legacy flat-task-list output and went stale the moment
        we replaced that flow with BPD; the user reported the file
        showing 5/21 contents long after they'd finalized BPD epics.

    Called by every BPD generation + finalize endpoint after a
    successful state mutation. Soft-fails: all host writes and GitHub
    pushes are non-fatal — the SQLite mutation succeeded, the user can
    always read the plan in the UI even if file writes fail.

    Returns a dict with two top-level keys per file:
      - ``host_write``: build-plan.md host write result
      - ``github_push``: build-plan.md GitHub push result
      - ``host_write_tasks``: tasks.md host write result
      - ``github_push_tasks``: tasks.md GitHub push result
    """
    # Snapshot the whole project's BPD state. Three short reads — no
    # join overhead, no concurrency hazard (we're a single async task).
    epics = await state.list_epics_for_project(project.project_id)
    features = await state.list_features_for_project(project.project_id)
    tasks = await state.list_tasks_for_project(project.project_id)

    from datetime import datetime as _dt
    now_iso = _dt.utcnow().isoformat() + "Z"

    # ─── 1. build-plan.md (full tree, including drafts) ────────────
    md = render_build_plan_markdown(
        project.name, epics, features, tasks, generated_at_iso=now_iso,
    )
    host_result = write_build_plan(project.name, md)
    if not host_result.ok:
        logger.warning(
            "build_plan.host_write_failed",
            project_id=project.project_id, project_name=project.name,
            error=host_result.error,
        )

    github_result: dict[str, Any] = {"skipped": "disabled_by_caller"}
    if push_to_github:
        try:
            github_result = await _push_finalized_doc_to_repo(
                project=project,
                repo_path="docs/build-plan.md",
                content=md,
                commit_subject=(
                    f"docs: sync build plan "
                    f"({len(epics)} epics, {len(features)} features, "
                    f"{len(tasks)} tasks)"
                ),
                descriptor=(
                    f"build plan ({len(epics)}E/{len(features)}F/{len(tasks)}T)"
                ),
                actor=actor,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "build_plan.github_push_failed",
                project_id=project.project_id, error=str(e),
            )
            github_result = {"ok": False, "error": str(e)}

    # ─── 2. tasks.md (FINALIZED tasks only, flat view) ────────────
    # Rendered from the same snapshot so the two files can never
    # disagree on what's finalized. Empty/placeholder content is fine
    # here — the file is always present so the editor doesn't show
    # a stale legacy version.
    tasks_md = render_finalized_tasks_flat_markdown(
        project.name, epics, features, tasks, generated_at_iso=now_iso,
    )
    finalized_count = sum(1 for t in tasks if str(t.list_status).lower() == "finalized")
    host_result_tasks = write_finalized_tasks(project.name, tasks_md)
    if not host_result_tasks.ok:
        logger.warning(
            "tasks_md.host_write_failed",
            project_id=project.project_id, project_name=project.name,
            error=host_result_tasks.error,
        )

    github_result_tasks: dict[str, Any] = {"skipped": "disabled_by_caller"}
    if push_to_github:
        try:
            github_result_tasks = await _push_finalized_doc_to_repo(
                project=project,
                repo_path="docs/tasks.md",
                content=tasks_md,
                commit_subject=(
                    f"docs: sync finalized tasks "
                    f"({finalized_count} task{'s' if finalized_count != 1 else ''} finalized)"
                ),
                descriptor=f"tasks.md ({finalized_count} finalized)",
                actor=actor,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "tasks_md.github_push_failed",
                project_id=project.project_id, error=str(e),
            )
            github_result_tasks = {"ok": False, "error": str(e)}

    return {
        "host_write": host_result.as_dict(),
        "github_push": github_result,
        "host_write_tasks": host_result_tasks.as_dict(),
        "github_push_tasks": github_result_tasks,
    }


def _check_truncation(result: dict[str, Any]) -> str | None:
    """Return a user-actionable hint when the agent's response was
    cut off at max_tokens (BPD-18). The downstream parser may still
    produce a partial result; the hint tells the caller to scope the
    next regen smaller."""
    if (result.get("stop_reason") or "") == "max_tokens":
        return (
            "The generator's response was truncated at the token cap. "
            "Try one of: regenerate this level with review comments asking "
            "for fewer items; split a large epic into 2; or escalate to "
            "the per-level batch endpoint which generates one parent at a time."
        )
    return None


# ── Pass 1 — PRD → Epics ─────────────────────────────────────────────────


def _build_epic_generation_prompt(
    prd_content: str,
    api_spec_content: str,
    review_comments: str,
    previous_epics: list[Epic] | None,
) -> str:
    """BPD-10. PRD → 5-12 epics. When `review_comments` + `previous_epics`
    are supplied, the agent revises the existing list rather than starting
    fresh — unaffected epics MUST be preserved word-for-word per BPD-108."""
    api_spec_block = (
        f"## API Specification (reference only)\n{api_spec_content}\n\n"
        if api_spec_content else ""
    )
    if review_comments and previous_epics:
        prev_json = json.dumps(
            [
                {
                    "title": e.title,
                    "description": e.description,
                    "acceptance_criteria": e.acceptance_criteria,
                }
                for e in sorted(previous_epics, key=lambda x: x.ordinal)
            ],
            indent=2,
        )
        return (
            "You are REVISING an existing list of EPICS for an AI agent team "
            "to execute. DO NOT start from scratch — apply the reviewer's "
            "feedback and keep unaffected epics word-for-word identical.\n\n"
            "## PRD (reference)\n"
            f"{prd_content}\n\n"
            f"{api_spec_block}"
            "## Current epic list (revise this)\n"
            "```json\n"
            f"{prev_json}\n"
            "```\n\n"
            "## Reviewer comments to address\n"
            f"{review_comments}\n\n"
            "## Output format\n"
            "Emit a single fenced ```json``` block containing the REVISED "
            "array of epic objects, with the same keys (title, description, "
            "acceptance_criteria). Apply the comments precisely; keep "
            "unaffected epics identical to the input. No prose before or after."
        )
    return (
        "You are decomposing a finalized PRD into a list of EPICS that an "
        "AI agent team will execute. Each epic is a coherent user-facing "
        "capability area (e.g. 'Authentication', 'Dashboard', 'Project CRUD') — "
        "NOT an implementation layer like 'Database access' (that's split "
        "across many user-facing epics).\n\n"
        "## PRD\n"
        f"{prd_content}\n\n"
        f"{api_spec_block}"
        "## Output format\n"
        "Emit a SINGLE fenced ```json``` block containing an ARRAY of "
        "5-12 epic objects. Each object has exactly these keys:\n\n"
        "  - title: string, ≤80 chars, no 'Epic N:' prefix (just the theme).\n"
        "    Examples: 'Authentication', 'Dashboard & Analytics', "
        "'Project CRUD & Lifecycle'.\n\n"
        "  - description: string, 1-2 paragraphs. What user-facing value "
        "this epic delivers; which PRD requirements it covers.\n\n"
        "  - acceptance_criteria: string, ONE sentence. When is this epic "
        "'done' from a user's perspective?\n"
        "    Example: 'Authenticated users can sign in, sign out, reset "
        "their password, and see their session persists across browser tabs.'\n\n"
        "## Scale guidance\n"
        "- 5-12 epics for a typical full-stack app. A trivial CLI tool may "
        "have 3 epics; a complex SaaS with auth + billing + multi-tenant "
        "may push toward 12.\n"
        "- 'Foundation' or 'Project Setup' IS allowed as an epic if the "
        "project has substantial setup not specific to one user feature.\n"
        "- Epics are unordered at this level — ordering emerges from "
        "feature/task-level dependencies in later passes.\n\n"
        "No prose before or after the fenced block."
    )


def _normalize_epic_dicts(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Defensive coercion + length caps. Drops rows without a title."""
    out = []
    for item in raw:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "title": title[:200],
            "description": str(item.get("description") or "")[:4000],
            "acceptance_criteria": str(item.get("acceptance_criteria") or "")[:1000],
        })
    return out


@router.post("/{project_id}/epics/generate", status_code=201)
async def generate_epics(
    project_id: str,
    request: Request,
    body: dict | None = None,
    user: dict = Depends(get_current_user),
):
    """BPD-11 — Pass 1 of the three-pass build-plan flow. PRD → 5-12 epics.

    Body (all optional):
      review_comments: str — when set AND a prior draft / archived list
        exists, the agent REVISES that list rather than generating fresh.
    """
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
            detail="Finalize the PRD before generating epics.",
        )
    # BPD-46 — API Spec is now a hard gate (was optional reference).
    # The agent needs concrete endpoint shapes to produce epics that
    # map to a real surface area; without the spec it over-invents
    # endpoints and the downstream features/tasks become work that
    # doesn't line up with what the project actually exposes.
    api_spec = await state.get_artifact(project_id, ArtifactKind.API_SPEC)
    if api_spec is None or api_spec.status != ArtifactStatus.FINALIZED:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "api_spec_not_finalized",
                "hint": "Finalize the API Specification before generating epics. "
                        "Go to the API Specification section, generate it from the "
                        "PRD, then click Finalize.",
            },
        )
    api_spec_content = api_spec.content

    review_comments = ((body or {}).get("review_comments") or "").strip()
    if len(review_comments) > _REVIEW_COMMENTS_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"review_comments must be ≤ {_REVIEW_COMMENTS_MAX} characters.",
        )

    # Refuse if a finalized epic list already exists — caller must archive
    # it first (mirrors the /tasks/generate semantics).
    existing_final = await state.list_epics_for_project(
        project_id, list_status=ArtifactStatus.FINALIZED,
    )
    if existing_final:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "epic list already finalized",
                "hint": "Archive the current epic list first, then regenerate.",
            },
        )

    # Pull existing draft (revision base when review_comments are given)
    existing_draft = await state.list_epics_for_project(
        project_id, list_status=ArtifactStatus.DRAFT,
    )
    previous: list[Epic] = list(existing_draft)
    if existing_draft:
        await state.delete_epic_list_draft(
            project_id, existing_draft[0].list_version,
        )

    # Next version = max(all) + 1
    all_archived = await state.list_epics_for_project(
        project_id, list_status=ArtifactStatus.ARCHIVED,
    )
    versions = {e.list_version for e in all_archived} | {
        e.list_version for e in existing_final
    }
    next_version = max(versions, default=0) + 1

    executor = getattr(request.app.state, "agent_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="Agent executor not configured.")

    prompt = _build_epic_generation_prompt(
        prd.content, api_spec_content, review_comments, previous if review_comments else None,
    )
    try:
        result = await executor.single_agent_call(
            agent_id="user_story_author",
            prompt=prompt,
            project_artifact_id=None,
            project_id=project_id,
            label="BPD Pass 1 · Generating Epics",
            max_tokens=32_000,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Epic generation failed: {e}")

    text = (result.get("text") or "").strip()
    parsed, parse_mode = _extract_json_array(text)
    truncation_hint = _check_truncation(result)
    if not parsed:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "no_epics_parsed",
                "parse_mode": parse_mode,
                "truncation_hint": truncation_hint,
                "raw_output_first_500": text[:500],
            },
        )
    epics = _normalize_epic_dicts(parsed)
    if not epics:
        raise HTTPException(
            status_code=502,
            detail={"error": "epics_empty_after_normalize"},
        )

    now = datetime.utcnow()
    review_input_val = review_comments or None
    saved = []
    for i, raw_epic in enumerate(epics):
        e = Epic(
            epic_id=f"E-{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            list_version=next_version,
            list_status=ArtifactStatus.DRAFT,
            ordinal=i + 1,
            title=raw_epic["title"],
            description=raw_epic["description"],
            acceptance_criteria=raw_epic["acceptance_criteria"],
            review_input=review_input_val,
            created_at=now,
        )
        await state.create_epic(e)
        saved.append(e)
    # Sync the BPD hierarchy to docs/build-plan.md after every successful
    # pass so the host file mirrors the DB. Soft-fail per the helper's
    # contract; failures show in meta.sync but don't fail the request.
    project_obj = await state.get_project(project_id)
    sync = await _sync_build_plan_to_disk(
        state, project_obj,
        actor=user.get("username") or user.get("user_id") or "unknown",
    ) if project_obj else None
    return {
        "data": [e.model_dump(mode="json") for e in saved],
        "meta": {
            "count": len(saved),
            "list_version": next_version,
            "parse_mode": parse_mode,
            "truncated": truncation_hint is not None,
            "truncation_hint": truncation_hint,
            "sync": sync,
        },
        "error": None,
    }


# ── Pass 2 — Epic → Features ─────────────────────────────────────────────


def _extract_relevant_api_endpoints(api_spec_content: str, hint_text: str) -> str:
    """BPD-49 — pull only the API spec endpoints whose path / summary / tags
    mention any of the keywords in ``hint_text`` (the feature or epic title).

    A full API spec can run 30-60 KB. Injecting the whole thing into every
    Pass-2 / Pass-3 prompt blows past the model's context budget when the
    project has 30+ features × 5 tasks. This heuristic keeps the spec
    excerpt focused on the endpoints the current pass actually touches.

    Strategy:
      1. Split the spec into OpenAPI path blocks (each "/path:" + indented body).
      2. Score each block by keyword overlap with the hint (case-insensitive
         word tokens, ignoring short / stoplist words).
      3. Return blocks with score ≥ 1, capped at 12 to bound the prompt size.
      4. If nothing matches, return the first 6 KB of the spec as a fallback
         (better to include something than to ship a context-blind prompt).

    Returns "" only when the spec itself is empty.
    """
    if not api_spec_content.strip():
        return ""

    # Tokenize the hint into useful words (drop stoplist + short tokens)
    stoplist = {
        "the", "and", "for", "with", "from", "into", "this", "that",
        "what", "when", "have", "will", "must", "page", "view", "list",
        "user", "data", "form", "show", "make", "add", "new",
    }
    import re as _re
    hint_tokens = {
        t.lower() for t in _re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", hint_text or "")
        if t.lower() not in stoplist
    }
    if not hint_tokens:
        # Hint had nothing useful — fall back to a short generic excerpt.
        return api_spec_content[:6000] + ("\n…[truncated]" if len(api_spec_content) > 6000 else "")

    # Split into OpenAPI path-level blocks. Heuristic: a line starting at
    # column 2 with "/" is a path key. Indented lines until the next
    # column-2 line belong to that path.
    lines = api_spec_content.splitlines()
    blocks: list[str] = []
    current: list[str] = []
    inside_paths = False
    for line in lines:
        stripped = line.rstrip()
        if stripped == "paths:":
            inside_paths = True
            continue
        if not inside_paths:
            continue
        # New path block: starts at column 2 with /
        if _re.match(r"^  /", line):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current and (line.startswith("    ") or line.startswith("\t") or not stripped):
            current.append(line)
        elif stripped and not line.startswith(" "):
            # Hit a top-level key (components:, security:, etc.) — paths section ended
            if current:
                blocks.append("\n".join(current))
                current = []
            break
    if current:
        blocks.append("\n".join(current))

    if not blocks:
        # Spec wasn't in standard OpenAPI shape (or paths section absent).
        # Fall back to the first 6 KB so the agent at least sees something.
        return api_spec_content[:6000] + ("\n…[truncated]" if len(api_spec_content) > 6000 else "")

    # Score each block by keyword overlap
    scored: list[tuple[int, str]] = []
    for block in blocks:
        block_tokens = {t.lower() for t in _re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", block)}
        score = len(hint_tokens & block_tokens)
        if score > 0:
            scored.append((score, block))
    if not scored:
        return api_spec_content[:6000] + ("\n…[truncated]" if len(api_spec_content) > 6000 else "")
    scored.sort(key=lambda x: x[0], reverse=True)
    keep = scored[:12]
    return "\n".join(b for _, b in keep)


def _build_feature_generation_prompt(
    epic: Epic,
    sibling_epic_titles: list[str],
    prd_excerpt: str,
    api_spec_content: str,
    review_comments: str,
    previous_features: list[Feature] | None,
) -> str:
    """BPD-12 + BPD-48. One epic → 1-8 features. Includes sibling-epic
    titles so the agent doesn't duplicate work that belongs to a
    different epic, PLUS the relevant API spec endpoints so generated
    features map to a real REST surface (not to invented endpoints)."""
    siblings_block = (
        "## Sibling epics in this project (DO NOT duplicate their work)\n"
        + "\n".join(f"- {t}" for t in sibling_epic_titles if t != epic.title)
        + "\n\n"
    ) if sibling_epic_titles else ""
    prd_block = (
        f"## PRD reference (in case the epic description elided detail)\n{prd_excerpt}\n\n"
        if prd_excerpt else ""
    )
    # BPD-48 — inject API spec endpoints relevant to this epic. We scope
    # to relevant endpoints (not the whole spec) so multi-epic projects
    # don't blow the prompt budget; the scoping heuristic is in
    # _extract_relevant_api_endpoints above.
    spec_excerpt = _extract_relevant_api_endpoints(
        api_spec_content,
        f"{epic.title} {epic.description}",
    ) if api_spec_content else ""
    api_spec_block = (
        f"## API Specification (endpoints relevant to this epic)\n"
        f"```yaml\n{spec_excerpt}\n```\n"
        f"Features SHOULD line up with these endpoints. Don't invent new ones.\n\n"
        if spec_excerpt else ""
    )
    if review_comments and previous_features:
        prev_json = json.dumps(
            [
                {
                    "title": f.title,
                    "description": f.description,
                    "acceptance_criteria": f.acceptance_criteria,
                    "depends_on_features": f.depends_on,
                }
                for f in sorted(previous_features, key=lambda x: x.ordinal)
            ],
            indent=2,
        )
        return (
            "You are REVISING the list of FEATURES under this epic. Apply the "
            "reviewer's feedback; keep unaffected features word-for-word identical.\n\n"
            f"## Epic: {epic.title}\n{epic.description}\n\n"
            f"Acceptance: {epic.acceptance_criteria}\n\n"
            f"{siblings_block}"
            f"{prd_block}"
            f"{api_spec_block}"
            "## Current feature list (revise this)\n"
            "```json\n"
            f"{prev_json}\n"
            "```\n\n"
            "## Reviewer comments\n"
            f"{review_comments}\n\n"
            "Emit a SINGLE fenced ```json``` block containing the revised array. "
            "No prose."
        )
    return (
        "You are decomposing one EPIC into the FEATURES needed to ship it. "
        "Each feature is a deliverable capability that's independently testable "
        "(you could demo it in isolation).\n\n"
        f"## Epic: {epic.title}\n\n"
        f"{epic.description}\n\n"
        f"Acceptance criterion: {epic.acceptance_criteria}\n\n"
        f"{siblings_block}"
        f"{prd_block}"
        f"{api_spec_block}"
        "## Output format\n"
        "Emit a SINGLE fenced ```json``` block containing an ARRAY of "
        "1-8 feature objects. Each object has exactly these keys:\n\n"
        "  - title: string, ≤80 chars, imperative form. Examples: "
        "'Login flow', 'Password reset', 'Session management'.\n\n"
        "  - description: string, 1 paragraph. What user-facing behavior "
        "this feature enables.\n\n"
        "  - acceptance_criteria: string, ONE sentence — 'feature done when X'.\n\n"
        "  - depends_on_features: array of strings (other feature TITLES "
        "in this project that must ship first). Empty array if no deps. "
        "Cross-epic deps allowed but rare. Use feature TITLE strings, not "
        "indices — the system maps them to IDs on persist.\n\n"
        "## Constraints\n"
        "- 1-feature epics are valid; don't force a split.\n"
        "- Feature-level deps are RARE — most deps live at the task level. "
        "Only declare a feature-level dep when 'all of Feature A must ship "
        "before any of Feature B starts' is genuinely true.\n"
        "- Titles MUST be unique within the project.\n\n"
        "No prose before or after the fenced block."
    )


def _normalize_feature_dicts(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for item in raw:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        deps_raw = item.get("depends_on_features") or item.get("depends_on") or []
        deps = [str(d).strip() for d in deps_raw if isinstance(d, str)]
        out.append({
            "title": title[:200],
            "description": str(item.get("description") or "")[:4000],
            "acceptance_criteria": str(item.get("acceptance_criteria") or "")[:1000],
            "depends_on_feature_titles": deps,
        })
    return out


@router.post("/{project_id}/epics/{epic_id}/features/generate", status_code=201)
async def generate_features(
    project_id: str,
    epic_id: str,
    request: Request,
    body: dict | None = None,
    user: dict = Depends(get_current_user),
):
    """BPD-13 — Pass 2. One epic → 1-8 features."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    await _require_project(state, project_id)
    epic = await state.get_epic(epic_id)
    if epic is None or epic.project_id != project_id:
        raise HTTPException(status_code=404, detail="Epic not found in this project.")

    # BPD-47 — same PRD + API Spec gate as the epic-generation path.
    # Without these the feature prompt has no concrete surface area to
    # decompose against, and the agent invents endpoints that don't
    # match what the project actually exposes.
    prd = await state.get_artifact(project_id, ArtifactKind.PRD)
    if prd is None or prd.status != ArtifactStatus.FINALIZED:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "prd_not_finalized",
                "hint": "Finalize the PRD before generating features.",
            },
        )
    api_spec = await state.get_artifact(project_id, ArtifactKind.API_SPEC)
    if api_spec is None or api_spec.status != ArtifactStatus.FINALIZED:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "api_spec_not_finalized",
                "hint": "Finalize the API Specification before generating features.",
            },
        )
    api_spec_content = api_spec.content

    review_comments = ((body or {}).get("review_comments") or "").strip()
    if len(review_comments) > _REVIEW_COMMENTS_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"review_comments must be ≤ {_REVIEW_COMMENTS_MAX} chars.",
        )

    # Sibling-epic titles for prompt context
    all_epics = await state.list_epics_for_project(project_id)
    sibling_titles = [e.title for e in all_epics if e.epic_id != epic_id]

    # PRD reference (truncated to fit token budget on a multi-pass run).
    # Pulled above as part of the gate; just compute the excerpt here.
    prd_excerpt = (prd.content[:6000] + "\n…[truncated]"
                   if len(prd.content) > 6000 else prd.content)

    # Pull existing features under this epic for revision flow
    existing = await state.list_features_for_epic(
        epic_id, list_status=ArtifactStatus.DRAFT,
    )
    previous: list[Feature] = list(existing)
    for f in existing:
        await state.delete_feature(f.feature_id)

    next_version = epic.list_version

    executor = getattr(request.app.state, "agent_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="Agent executor not configured.")

    prompt = _build_feature_generation_prompt(
        epic, sibling_titles, prd_excerpt, api_spec_content,
        review_comments, previous if review_comments else None,
    )
    try:
        result = await executor.single_agent_call(
            agent_id="user_story_author",
            prompt=prompt,
            project_artifact_id=None,
            project_id=project_id,
            label=f"BPD Pass 2 · Generating Features for {epic.title[:40]}",
            max_tokens=32_000,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Feature generation failed: {e}")

    text = (result.get("text") or "").strip()
    parsed, parse_mode = _extract_json_array(text)
    truncation_hint = _check_truncation(result)
    if not parsed:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "no_features_parsed",
                "parse_mode": parse_mode,
                "truncation_hint": truncation_hint,
                "raw_output_first_500": text[:500],
            },
        )
    features = _normalize_feature_dicts(parsed)
    if not features:
        raise HTTPException(
            status_code=502, detail={"error": "features_empty_after_normalize"},
        )

    # Build title → feature_id map for cross-feature dep resolution.
    # New features in this batch resolve to their own emitted feature_id;
    # existing features in the same project (different epic) also count.
    title_to_id: dict[str, str] = {}
    for other_epic in all_epics:
        other_features = await state.list_features_for_epic(other_epic.epic_id)
        for of in other_features:
            title_to_id[of.title] = of.feature_id

    now = datetime.utcnow()
    review_input_val = review_comments or None
    saved: list[Feature] = []
    # First pass: create rows with empty depends_on so we can map titles
    for i, raw_f in enumerate(features):
        fid = f"F-{uuid.uuid4().hex[:8]}"
        title_to_id[raw_f["title"]] = fid
        f = Feature(
            feature_id=fid,
            epic_id=epic_id,
            project_id=project_id,
            list_version=next_version,
            list_status=ArtifactStatus.DRAFT,
            ordinal=i + 1,
            title=raw_f["title"],
            description=raw_f["description"],
            acceptance_criteria=raw_f["acceptance_criteria"],
            depends_on=[],
            review_input=review_input_val,
            created_at=now,
        )
        await state.create_feature(f)
        saved.append(f)
    # Second pass: resolve depends_on_feature_titles → feature_ids
    unresolved: list[dict[str, Any]] = []
    for f, raw_f in zip(saved, features):
        if not raw_f.get("depends_on_feature_titles"):
            continue
        resolved_ids: list[str] = []
        for title in raw_f["depends_on_feature_titles"]:
            fid = title_to_id.get(title)
            if fid:
                resolved_ids.append(fid)
            else:
                unresolved.append({"feature_id": f.feature_id, "missing_title": title})
        if resolved_ids:
            await state.update_feature(f.feature_id, {"depends_on": resolved_ids})

    # Same disk-sync as Pass 1 — rewrite docs/build-plan.md from live DB.
    project_obj = await state.get_project(project_id)
    sync = await _sync_build_plan_to_disk(
        state, project_obj,
        actor=user.get("username") or user.get("user_id") or "unknown",
    ) if project_obj else None

    return {
        "data": [f.model_dump(mode="json") for f in saved],
        "meta": {
            "count": len(saved),
            "epic_id": epic_id,
            "parse_mode": parse_mode,
            "truncated": truncation_hint is not None,
            "truncation_hint": truncation_hint,
            "unresolved_deps": unresolved or None,
            "sync": sync,
        },
        "error": None,
    }


# ── Pass 3 — Feature → Atomic Tasks ──────────────────────────────────────


def _build_task_generation_prompt(
    feature: Feature,
    epic: Epic,
    sibling_feature_titles: list[str],
    prd_content: str,
    api_spec_content: str,
    review_comments: str,
    previous_tasks: list[ProjectTask] | None,
) -> str:
    """BPD-14 + BPD-49. One feature → 3-15 atomic tasks. Enforces atomic
    contract (one primary_file, 50-300 LOC, single acceptance_test).

    Pass 3 used to receive ONLY (feature + epic + sibling titles), which
    meant by the time the agent picked a primary_file or wrote an
    acceptance_test it had no product context and no concrete API
    surface. The PRD excerpt + scoped API endpoints close that gap so
    generated tasks reference real paths / endpoints instead of
    plausible-sounding invented ones.
    """
    siblings = "\n".join(f"- {t}" for t in sibling_feature_titles if t != feature.title)
    siblings_block = (
        f"## Sibling features under epic '{epic.title}'\n{siblings}\n\n"
        if siblings else ""
    )
    # PRD excerpt: tasks need product context to write meaningful
    # descriptions + acceptance tests. Cap at 6 KB — Pass 3 makes one
    # call PER feature so the cumulative token cost matters.
    prd_block = ""
    if prd_content:
        prd_excerpt = (prd_content[:6000] + "\n…[truncated]"
                       if len(prd_content) > 6000 else prd_content)
        prd_block = (
            f"## PRD reference (product context for this feature)\n"
            f"{prd_excerpt}\n\n"
        )
    # API spec, scoped to endpoints touching this feature. Tasks should
    # name concrete endpoint paths in their primary_file / acceptance_test
    # fields, so this is where they look them up.
    spec_excerpt = _extract_relevant_api_endpoints(
        api_spec_content,
        f"{feature.title} {feature.description}",
    ) if api_spec_content else ""
    api_spec_block = (
        f"## API Specification (endpoints relevant to this feature)\n"
        f"```yaml\n{spec_excerpt}\n```\n"
        f"primary_file paths and acceptance_test wording MUST reference "
        f"these endpoints (not invented ones).\n\n"
        if spec_excerpt else ""
    )
    if review_comments and previous_tasks:
        prev_json = json.dumps(
            [
                {
                    "title": t.title,
                    "description": t.description,
                    "primary_file": t.primary_file,
                    "expected_loc": t.expected_loc,
                    "acceptance_test": t.acceptance_test,
                    "depends_on_indices": [],  # not round-tripped at this scale
                    "task_type": t.task_type,
                    "priority": t.priority,
                    "estimated_agent": t.estimated_agent,
                }
                for t in sorted(previous_tasks, key=lambda x: x.ordinal)
            ],
            indent=2,
        )
        return (
            f"You are REVISING the atomic tasks under feature '{feature.title}'. "
            "Apply the reviewer's feedback; keep unaffected tasks identical.\n\n"
            f"## Feature: {feature.title}\n{feature.description}\n\n"
            f"Acceptance: {feature.acceptance_criteria}\n\n"
            f"{prd_block}"
            f"{api_spec_block}"
            "## Current tasks (revise this)\n"
            "```json\n"
            f"{prev_json}\n"
            "```\n\n"
            "## Reviewer comments\n"
            f"{review_comments}\n\n"
            "Emit a SINGLE fenced ```json``` block containing the revised array."
        )
    return (
        "You are decomposing one FEATURE into the ATOMIC TASKS an AI agent "
        "team will execute. Each task is BOUNDED:\n"
        "  - exactly ONE primary file (path stated explicitly)\n"
        "  - ≤ 2 additional files touched\n"
        "  - 50-300 expected lines of code\n"
        "  - ONE acceptance criterion (one test, one curl, one render)\n"
        "  - buildable in 2-3 minutes by a backend/frontend specialist agent\n\n"
        f"## Epic: {epic.title}\n{epic.description}\n\n"
        f"## Feature: {feature.title}\n{feature.description}\n\n"
        f"Acceptance criterion: {feature.acceptance_criteria}\n\n"
        f"{siblings_block}"
        f"{prd_block}"
        f"{api_spec_block}"
        "## Output format\n"
        "Emit a SINGLE fenced ```json``` block containing an ARRAY of "
        "3-15 atomic task objects. Each object has exactly these keys:\n\n"
        "  - title: imperative, ≤80 chars. Examples: 'Create POST /auth/login "
        "endpoint', 'Add LoginForm component', 'Add login flow integration test'.\n\n"
        "  - description: ≤4 lines — the prompt-equivalent of what a junior "
        "developer would read before opening the file.\n\n"
        "  - primary_file: ONE file path, e.g. 'backend/app/api/v1/auth.py'.\n\n"
        "  - expected_loc: integer, typical 50-300. Reject yourself if it's "
        "<30 (over-decomposition) or >500 (under-decomposition).\n\n"
        "  - acceptance_test: ONE sentence. Examples: 'POST /auth/login with "
        "valid creds returns 200 with token; invalid creds returns 401'.\n\n"
        "  - depends_on_indices: array of integer INDICES into THIS array "
        "(0-based). Empty = no deps. References to other features use "
        "the special form 'feature:<feature_title>' as a string instead "
        "of an integer.\n\n"
        "  - task_type: one of feature_request, bug_report, doc_request, "
        "demo_request, research_request, content_request.\n\n"
        "  - priority: one of low, medium, high.\n\n"
        "  - estimated_agent: one of backend_specialist, frontend_specialist, "
        "tester_specialist, code_reviewer, devops_specialist, content_creator, "
        "research_specialist. Pick the best fit; null if cross-cutting.\n\n"
        "## Style\n"
        "- Be SPECIFIC. Reference concrete file paths, function names, "
        "endpoints, SQL tables.\n"
        "- The LAST task in most features is a TEST task targeting "
        "tests/<path>/test_<X>.py.\n"
        "- Order tasks so internal deps form a clean DAG (no cycles).\n\n"
        "No prose before or after the fenced block."
    )


def _normalize_task_emission_dicts(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Atomic-task normalization: primary_file required; bounds on
    expected_loc; emit_warnings list per-row when sus."""
    out = []
    for item in raw:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        primary_file = str(item.get("primary_file") or "").strip() or None
        loc_raw = item.get("expected_loc")
        try:
            loc = int(loc_raw) if loc_raw is not None else None
        except (TypeError, ValueError):
            loc = None
        tt = str(item.get("task_type") or "feature_request").strip()
        if tt not in _VALID_TASK_TYPES:
            tt = "feature_request"
        pr = str(item.get("priority") or "medium").lower().strip()
        if pr not in _VALID_PRIORITIES:
            pr = "medium"
        agent = item.get("estimated_agent")
        if agent is not None:
            agent = str(agent)
        deps = item.get("depends_on_indices") or item.get("depends_on") or []
        if not isinstance(deps, list):
            deps = []
        warnings = []
        if not primary_file:
            warnings.append("missing primary_file")
        if loc is not None and loc < 30:
            warnings.append(f"expected_loc={loc} suspiciously small")
        if loc is not None and loc > 500:
            warnings.append(f"expected_loc={loc} suspiciously large")
        out.append({
            "title": title[:200],
            "description": str(item.get("description") or "")[:2000],
            "primary_file": primary_file,
            "expected_loc": loc,
            "acceptance_test": str(item.get("acceptance_test") or "")[:600] or None,
            "depends_on_raw": deps,
            "task_type": tt,
            "priority": pr,
            "estimated_agent": agent,
            "_warnings": warnings,
        })
    return out


@router.post("/{project_id}/features/{feature_id}/tasks/generate", status_code=201)
async def generate_tasks_for_feature(
    project_id: str,
    feature_id: str,
    request: Request,
    body: dict | None = None,
    user: dict = Depends(get_current_user),
):
    """BPD-15 — Pass 3. One feature → 3-15 atomic tasks with
    primary_file + acceptance_test + depends_on. Persists tasks ONLY
    if the resulting graph has no cycles (BPD-005)."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    await _require_project(state, project_id)
    feature = await state.get_feature(feature_id)
    if feature is None or feature.project_id != project_id:
        raise HTTPException(status_code=404, detail="Feature not found in this project.")
    epic = await state.get_epic(feature.epic_id)
    if epic is None:
        raise HTTPException(status_code=404, detail="Parent epic missing.")

    # BPD-47 — same PRD + API Spec gate as Pass 1 and Pass 2. Pass 3
    # is the one that produces actual implementation tasks (primary_file,
    # acceptance_test, expected_loc), so it needs concrete API surface
    # MORE than the earlier passes, not less. Pulling PRD + spec here
    # also feeds the BPD-49 prompt-enrichment block below.
    prd = await state.get_artifact(project_id, ArtifactKind.PRD)
    if prd is None or prd.status != ArtifactStatus.FINALIZED:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "prd_not_finalized",
                "hint": "Finalize the PRD before generating atomic tasks.",
            },
        )
    api_spec = await state.get_artifact(project_id, ArtifactKind.API_SPEC)
    if api_spec is None or api_spec.status != ArtifactStatus.FINALIZED:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "api_spec_not_finalized",
                "hint": "Finalize the API Specification before generating atomic tasks.",
            },
        )
    prd_content = prd.content
    api_spec_content = api_spec.content

    review_comments = ((body or {}).get("review_comments") or "").strip()
    if len(review_comments) > _REVIEW_COMMENTS_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"review_comments must be ≤ {_REVIEW_COMMENTS_MAX} chars.",
        )

    # Sibling features under same epic for prompt context
    siblings = await state.list_features_for_epic(feature.epic_id)
    sibling_titles = [f.title for f in siblings if f.feature_id != feature_id]

    # Existing draft tasks under this feature for revision flow.
    all_tasks = await state.list_tasks_for_project(project_id)
    existing_tasks = [t for t in all_tasks if t.feature_id == feature_id]
    previous = list(existing_tasks)
    # Delete existing draft tasks under this feature (similar to PRD/task list flow)
    for t in existing_tasks:
        if t.list_status == ArtifactStatus.DRAFT:
            await state.delete_task(t.task_id)

    # Safety net: also purge any UNSTARTED orphan tasks (feature_id IS NULL
    # AND task_status='backlog' AND no request_id) from prior re-generation
    # cycles. The cascade fixes in delete_epic / delete_feature should
    # prevent these going forward, but pre-existing orphans (from before
    # the cascade fix shipped) would otherwise pile up forever — they'd
    # show in the Generator's `tasks:N` rollup but never render under
    # any feature in the tree because their feature is gone.
    orphans = [t for t in all_tasks
               if t.feature_id is None
               and t.task_status == TaskStatus.BACKLOG
               and not t.request_id]
    for t in orphans:
        await state.delete_task(t.task_id)

    next_version = max(
        (t.list_version for t in all_tasks if t.list_status != ArtifactStatus.ARCHIVED),
        default=0,
    ) + 1 if not any(t.list_status == ArtifactStatus.DRAFT for t in all_tasks if t.feature_id != feature_id) else max(
        (t.list_version for t in all_tasks if t.list_status == ArtifactStatus.DRAFT),
        default=1,
    )

    executor = getattr(request.app.state, "agent_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="Agent executor not configured.")

    prompt = _build_task_generation_prompt(
        feature, epic, sibling_titles,
        prd_content, api_spec_content,
        review_comments, previous if review_comments else None,
    )
    try:
        result = await executor.single_agent_call(
            agent_id="user_story_author",
            prompt=prompt,
            project_artifact_id=None,
            project_id=project_id,
            label=f"BPD Pass 3 · Generating Atomic Tasks for {feature.title[:40]}",
            max_tokens=32_000,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Task generation failed: {e}")

    text = (result.get("text") or "").strip()
    parsed, parse_mode = _extract_json_array(text)
    truncation_hint = _check_truncation(result)
    if not parsed:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "no_tasks_parsed",
                "parse_mode": parse_mode,
                "truncation_hint": truncation_hint,
                "raw_output_first_500": text[:500],
            },
        )
    tasks = _normalize_task_emission_dicts(parsed)
    if not tasks:
        raise HTTPException(
            status_code=502, detail={"error": "tasks_empty_after_normalize"},
        )

    # Assign task_ids in advance so depends_on_indices can resolve.
    assigned_ids = [f"T-{uuid.uuid4().hex[:8]}" for _ in tasks]

    # Resolve depends_on. Index → task_id (within this batch). String
    # "feature:<feature_title>" → look up that feature's tasks (use the
    # LAST task in that feature as the conservative "feature complete"
    # signal — v1; v2 may add finer-grained targeting).
    feature_title_to_last_task_id: dict[str, str] = {}
    for other_f in siblings + [feature]:
        f_tasks = [t for t in all_tasks if t.feature_id == other_f.feature_id]
        if f_tasks:
            feature_title_to_last_task_id[other_f.title] = f_tasks[-1].task_id

    now = datetime.utcnow()
    review_input_val = review_comments or None
    to_insert: list[ProjectTask] = []
    unresolved_deps: list[dict[str, Any]] = []
    all_warnings: list[dict[str, Any]] = []
    for i, (raw_t, tid) in enumerate(zip(tasks, assigned_ids)):
        resolved_deps: list[str] = []
        for d in raw_t["depends_on_raw"]:
            if isinstance(d, int):
                if 0 <= d < len(assigned_ids) and d != i:
                    resolved_deps.append(assigned_ids[d])
                else:
                    unresolved_deps.append({"task_index": i, "bad_dep": d, "reason": "out_of_range_or_self"})
            elif isinstance(d, str) and d.startswith("feature:"):
                target_title = d[len("feature:"):]
                resolved_id = feature_title_to_last_task_id.get(target_title)
                if resolved_id:
                    resolved_deps.append(resolved_id)
                else:
                    unresolved_deps.append({"task_index": i, "bad_dep": d, "reason": "feature_not_found"})
        t = ProjectTask(
            task_id=tid,
            project_id=project_id,
            list_version=next_version,
            list_status=ArtifactStatus.DRAFT,
            ordinal=i + 1,
            title=raw_t["title"],
            description=raw_t["description"],
            task_type=raw_t["task_type"],
            priority=raw_t["priority"],
            estimated_agent=raw_t["estimated_agent"],
            task_status=TaskStatus.BACKLOG,
            feature_id=feature_id,
            depends_on=resolved_deps,
            primary_file=raw_t["primary_file"],
            expected_loc=raw_t["expected_loc"],
            acceptance_test=raw_t["acceptance_test"],
            review_input=review_input_val,
            created_at=now,
        )
        to_insert.append(t)
        if raw_t["_warnings"]:
            all_warnings.append({"task_id": tid, "warnings": raw_t["_warnings"]})

    # Insert all rows first, then run cycle detection across the
    # whole project's draft graph. If a cycle is found, ROLL BACK
    # this batch's inserts (BPD-005).
    for t in to_insert:
        await state.create_task(t)
    has_cycle, cycle_path = await state.has_task_cycle(project_id, next_version)
    if has_cycle:
        for t in to_insert:
            await state.delete_task(t.task_id)
        raise HTTPException(
            status_code=422,
            detail={
                "error": "dag_cycle_detected",
                "cycle_path": cycle_path,
                "hint": "The generated tasks form a dependency cycle. "
                        "Regenerate this feature (the agent will produce "
                        "a different ordering).",
            },
        )

    # Sync docs/build-plan.md after Pass 3 too — this is the pass that
    # actually moves the "tasks: N" counter, so this is where the user
    # most often expects to see fresh disk state.
    project_obj = await state.get_project(project_id)
    sync = await _sync_build_plan_to_disk(
        state, project_obj,
        actor=user.get("username") or user.get("user_id") or "unknown",
    ) if project_obj else None

    return {
        "data": [t.model_dump(mode="json") for t in to_insert],
        "meta": {
            "count": len(to_insert),
            "feature_id": feature_id,
            "epic_id": feature.epic_id,
            "parse_mode": parse_mode,
            "truncated": truncation_hint is not None,
            "truncation_hint": truncation_hint,
            "unresolved_deps": unresolved_deps or None,
            "row_warnings": all_warnings or None,
            "sync": sync,
        },
        "error": None,
    }


# ── BPD-17 — Build-plan orchestrator (runs all three passes) ─────────────


@router.post("/{project_id}/build-plan/generate", status_code=202)
async def generate_build_plan(
    project_id: str,
    request: Request,
    body: dict | None = None,
    user: dict = Depends(get_current_user),
):
    """BPD-17. Convenience: run Pass 1 → finalize → Pass 2 (per epic) →
    finalize → Pass 3 (per feature) → finalize. Returns a summary of
    the cascade. Intended for the 'I trust the agent, just give me a
    plan' flow.

    SECURITY: this CAN spend significant LLM cost (1 + N_epics +
    N_features calls). Caller is the gate."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    await _require_project(state, project_id)

    # Pass 1 — epics
    pass1 = await generate_epics(project_id, request, body, user)
    epic_dicts = pass1["data"]
    list_version = pass1["meta"]["list_version"]
    await state.finalize_epic_list(project_id, list_version)

    # Pass 2 — features per epic
    feature_counts: dict[str, int] = {}
    for ed in epic_dicts:
        eid = ed["epic_id"]
        try:
            pass2 = await generate_features(project_id, eid, request, None, user)
        except HTTPException as e:
            raise HTTPException(
                status_code=502,
                detail={"phase": "features", "epic_id": eid, "inner": e.detail},
            )
        feature_counts[eid] = pass2["meta"]["count"]

    # Pass 3 — tasks per feature
    task_counts: dict[str, int] = {}
    all_features = await state.list_features_for_project(project_id)
    for f in all_features:
        if f.list_version != list_version:
            continue
        try:
            pass3 = await generate_tasks_for_feature(
                project_id, f.feature_id, request, None, user,
            )
        except HTTPException as e:
            raise HTTPException(
                status_code=502,
                detail={"phase": "tasks", "feature_id": f.feature_id, "inner": e.detail},
            )
        task_counts[f.feature_id] = pass3["meta"]["count"]

    return {
        "data": {
            "list_version": list_version,
            "epic_count": len(epic_dicts),
            "feature_counts_by_epic": feature_counts,
            "task_counts_by_feature": task_counts,
        },
        "meta": None,
        "error": None,
    }


# ── BPD-16 — Batch generators (per-level convenience) ────────────────────


@router.post("/{project_id}/epics/{epic_id}/features/generate-all")
async def generate_features_for_all_epics(
    project_id: str,
    epic_id: str,  # path placeholder kept for URL parallelism; unused
    request: Request,
    user: dict = Depends(get_current_user),
):
    """BPD-16a — run Pass 2 for EVERY epic in the project (sequential).
    The :epic_id path segment is ignored — kept to match URL shape
    with BPD-13. Use this when you've approved an epic list and want
    features generated for all of them without per-epic clicks."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    await _require_project(state, project_id)
    epics = await state.list_epics_for_project(project_id)
    if not epics:
        raise HTTPException(status_code=404, detail="No epics for this project.")
    counts: dict[str, int] = {}
    for e in epics:
        try:
            res = await generate_features(project_id, e.epic_id, request, None, user)
            counts[e.epic_id] = res["meta"]["count"]
        except HTTPException as he:
            # Surface partial progress + the failing epic
            return {
                "data": {"feature_counts": counts},
                "meta": {"failed_at_epic": e.epic_id, "inner": he.detail},
                "error": "partial",
            }
    return {"data": {"feature_counts": counts}, "meta": None, "error": None}


@router.post("/{project_id}/features/{feature_id}/tasks/generate-all")
async def generate_tasks_for_all_features(
    project_id: str,
    feature_id: str,  # ignored, see above
    request: Request,
    user: dict = Depends(get_current_user),
):
    """BPD-16b — run Pass 3 for EVERY feature in the project."""
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    await _require_project(state, project_id)
    features = await state.list_features_for_project(project_id)
    if not features:
        raise HTTPException(status_code=404, detail="No features for this project.")
    counts: dict[str, int] = {}
    for f in features:
        try:
            res = await generate_tasks_for_feature(
                project_id, f.feature_id, request, None, user,
            )
            counts[f.feature_id] = res["meta"]["count"]
        except HTTPException as he:
            return {
                "data": {"task_counts": counts},
                "meta": {"failed_at_feature": f.feature_id, "inner": he.detail},
                "error": "partial",
            }
    return {"data": {"task_counts": counts}, "meta": None, "error": None}


@router.post("/{project_id}/epics/{epic_id}/tasks/generate-all")
async def generate_tasks_for_epic(
    project_id: str,
    epic_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Pass 3 scoped to a single epic — runs `generate_tasks_for_feature`
    for every feature under that epic, sequentially.

    Sits between the per-feature endpoint (one feature, ~30s) and the
    whole-project endpoint (every feature, ~24 min on a project with
    48 features). For a typical epic with 5-7 features this takes
    3-5 minutes — small enough to wait through, large enough to be
    worth batching instead of clicking 7 individual buttons.

    Returns the same `task_counts` shape as `generate_tasks_for_all_features`
    so the frontend can render a uniform summary. Stops on the first
    HTTPException to surface the failure clearly rather than masking
    a bad feature in a partial-success blob.
    """
    state = request.app.state.state_store
    try:
        assert_not_unassigned(project_id, "modified")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    await _require_project(state, project_id)
    epic = await state.get_epic(epic_id)
    if epic is None or epic.project_id != project_id:
        raise HTTPException(status_code=404, detail="Epic not found in this project.")
    features = await state.list_features_for_epic(epic_id)
    if not features:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "no_features_under_epic",
                "hint": f"Generate features under epic '{epic.title}' first.",
            },
        )
    counts: dict[str, int] = {}
    for f in features:
        try:
            res = await generate_tasks_for_feature(
                project_id, f.feature_id, request, None, user,
            )
            counts[f.feature_id] = res["meta"]["count"]
        except HTTPException as he:
            return {
                "data": {"task_counts": counts},
                "meta": {
                    "epic_id": epic_id,
                    "failed_at_feature": f.feature_id,
                    "inner": he.detail,
                },
                "error": "partial",
            }
    return {
        "data": {"task_counts": counts},
        "meta": {
            "epic_id": epic_id,
            "epic_title": epic.title,
            "feature_count": len(features),
            "total_tasks": sum(counts.values()),
        },
        "error": None,
    }
