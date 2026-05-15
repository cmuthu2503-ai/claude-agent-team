"""Request endpoints — submit, list, detail, retry, stories, attachments."""

import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.auth.service import get_current_user, require_role
from src.workflows.loader import ParallelStage, WorkflowDefinition

router = APIRouter(prefix="/api/v1/requests", tags=["requests"])

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "data/uploads"))
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".pdf"}


class SubmitRequestBody(BaseModel):
    description: str
    task_type: str = "feature_request"
    priority: str = "medium"
    tags: list[str] = []


def _envelope(data: Any, meta: dict | None = None) -> dict:
    return {"data": data, "meta": meta, "error": None}


def _iso_utc(dt: datetime | None) -> str | None:
    """Serialize a datetime as an ISO string the browser will parse as UTC.

    The backend stores all timestamps as naive UTC (datetime.utcnow). Without
    a timezone marker, JavaScript's `new Date(iso)` parses the string as the
    browser's LOCAL time, which makes "X minutes ago" math wildly wrong for
    anyone not in UTC (e.g., a user in EDT sees "-239 minutes ago" instead
    of "5 minutes ago"). Appending 'Z' tells the browser it's UTC.
    """
    if dt is None:
        return None
    iso = dt.isoformat()
    # Only mark naive timestamps; if it's already aware, leave the offset alone.
    if "+" not in iso and not iso.endswith("Z"):
        iso += "Z"
    return iso


# Friendly labels for workflow stage IDs. Auto-derivation (replace _ with space
# + title-case) handles most cases; this overrides where the auto result is
# wrong or wordy.
_STAGE_LABEL_OVERRIDES: dict[str, str] = {
    "requirements":    "PRD",
    "story_creation":  "Stories",
    "review_and_test": "Review & Test",
    "hotfix_deploy":   "Deploy",
    "code_commit":     "Code Commit",
    "deployment":      "Deploy",
}


def _stage_label(stage_id: str) -> str:
    """Convert a workflow stage_id to a human-readable label."""
    if stage_id in _STAGE_LABEL_OVERRIDES:
        return _STAGE_LABEL_OVERRIDES[stage_id]
    return stage_id.replace("_", " ").title()


def _serialize_workflow(wf: WorkflowDefinition | None) -> dict[str, Any] | None:
    """Serialize a WorkflowDefinition into a JSON-friendly shape the frontend
    Story Board can use to render a workflow-driven pipeline bar.

    Shape:
        {
            "id": str,
            "trigger": str,
            "produces_stories": bool,
            "stages": [
                {
                    "id": str,
                    "label": str,
                    "agents": list[str],   # flattened across parallel groups
                    "parallel": bool,
                    "system": bool,        # True if no agents (handled by orchestrator)
                },
                ...
            ],
        }
    """
    if wf is None:
        return None
    stages = []
    for stage_id, stage in wf.stages.items():
        if isinstance(stage, ParallelStage):
            agents: list[str] = []
            for group in stage.groups:
                agents.extend(group.agents)
            stages.append({
                "id": stage_id,
                "label": _stage_label(stage_id),
                "agents": agents,
                "parallel": True,
                "system": len(agents) == 0,
            })
        else:
            stages.append({
                "id": stage_id,
                "label": _stage_label(stage_id),
                "agents": list(stage.agents),
                "parallel": False,
                "system": len(stage.agents) == 0,
            })
    # A workflow "produces stories" if it has a stage that runs user_story_author.
    produces_stories = any(
        "user_story_author" in s["agents"] for s in stages
    )
    return {
        "id": wf.workflow_id,
        "trigger": wf.trigger,
        "produces_stories": produces_stories,
        "stages": stages,
    }


@router.post("")
async def submit_request(
    request: Request,
    description: str = Form(...),
    task_type: str = Form("feature_request"),
    priority: str = Form("medium"),
    screenshots: list[UploadFile] = File(default=[]),
    user: dict = Depends(require_role("developer", "admin")),
):
    # Save uploaded screenshots
    saved_files: list[dict[str, str]] = []
    for file in screenshots:
        if file.filename:
            ext = Path(file.filename).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise HTTPException(400, f"File type {ext} not allowed. Use: {ALLOWED_EXTENSIONS}")
            content = await file.read()
            if len(content) > MAX_FILE_SIZE:
                raise HTTPException(400, f"File {file.filename} exceeds 10MB limit")
            file_id = uuid.uuid4().hex[:12]
            safe_name = f"{file_id}{ext}"
            upload_path = UPLOAD_DIR / safe_name
            upload_path.parent.mkdir(parents=True, exist_ok=True)
            upload_path.write_bytes(content)
            saved_files.append({
                "file_id": file_id,
                "filename": file.filename,
                "stored_as": safe_name,
                "size": len(content),
                "url": f"/api/v1/requests/attachments/{safe_name}",
            })

    # Build description with attachment references
    full_description = description
    if saved_files:
        full_description += "\n\n**Attachments:**\n"
        for f in saved_files:
            full_description += f"- [{f['filename']}]({f['url']})\n"

    orchestrator = request.app.state.orchestrator
    result = await orchestrator.submit(
        description=full_description,
        task_type=task_type,
        priority=priority,
        created_by=user.get("username", ""),
    )
    return _envelope({
        "request_id": result.request_id,
        "status": result.status,
        "description": result.description,
        "priority": result.priority,
        "created_at": result.created_at.isoformat(),
        "attachments": saved_files,
    })


@router.get("/attachments/{filename}")
async def get_attachment(filename: str):
    """Serve an uploaded attachment file."""
    file_path = UPLOAD_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, "Attachment not found")
    # Prevent path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(400, "Invalid filename")
    return FileResponse(file_path)


@router.get("")
async def list_requests(
    request: Request,
    status: str | None = None,
    page: int = 1,
    per_page: int = 20,
    user: dict = Depends(get_current_user),
):
    state = request.app.state.state_store
    offset = (page - 1) * per_page
    requests = await state.list_requests(status=status, limit=per_page, offset=offset)
    return _envelope(
        [
            {
                "request_id": r.request_id,
                "description": r.description,
                "task_type": r.task_type,
                "priority": r.priority,
                "status": r.status,
                "created_by": r.created_by,
                "created_at": _iso_utc(r.created_at),
                "completed_at": _iso_utc(r.completed_at),
            }
            for r in requests
        ],
        meta={"page": page, "per_page": per_page},
    )


@router.get("/{request_id}")
async def get_request_detail(
    request_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    state = request.app.state.state_store
    req = await state.get_request(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    subtasks = await state.get_subtasks_for_request(request_id)
    stories = await state.get_stories_for_request(request_id)
    token_usage = await state.get_token_usage_for_request(request_id)
    documents = await state.get_documents_for_request(request_id)
    total_cost = sum(u.cost_usd for u in token_usage)

    # Resolve the workflow this request ran through, so the Story Board can
    # render a pipeline bar that matches the ACTUAL stages rather than
    # hardcoding feature_development's 6-stage shape.
    orchestrator = request.app.state.orchestrator
    workflow = orchestrator.workflow_loader.get_workflow_for_trigger(req.task_type)
    workflow_payload = _serialize_workflow(workflow)

    # Pull the supervisor's deployment state if there's one for this request.
    # The Story Board uses this to render the REAL deploy stage state (judge
    # strategy + supervisor step_history) instead of just the agent's subtask.
    deployment_state = await state.get_deployment_state_for_request(request_id)
    deployment_payload = None
    if deployment_state:
        deployment_payload = {
            "deployment_id": deployment_state.deployment_id,
            "current_step": deployment_state.current_step,
            "strategy": deployment_state.strategy,
            "strategy_reasoning": deployment_state.strategy_reasoning,
            "risk": deployment_state.risk,
            "commit_sha": deployment_state.commit_sha,
            "step_history": deployment_state.step_history,
            "files_committed": deployment_state.files_committed,
            "error_message": deployment_state.error_message,
            "started_at": _iso_utc(deployment_state.started_at),
            "completed_at": _iso_utc(deployment_state.completed_at),
        }

    # Build stories with nested acceptance criteria + test cases (async)
    stories_data = []
    for st in stories:
        acs = await state.get_acceptance_criteria_for_story(st.story_id)
        tcs = await state.get_test_cases_for_story(st.story_id)
        stories_data.append({
            "story_id": st.story_id,
            "title": st.title,
            "description": st.description or "",
            "status": st.status,
            "priority": st.priority,
            "assigned_agent": st.assigned_agent,
            "coverage_pct": st.coverage_pct,
            "github_issue_number": st.github_issue_number,
            "acceptance_criteria": [
                {
                    "ac_id": ac.ac_id,
                    "criterion_text": ac.criterion_text,
                    "given": ac.given_clause,
                    "when": ac.when_clause,
                    "then": ac.then_clause,
                    "is_met": ac.is_met,
                }
                for ac in acs
            ],
            "test_cases": [
                {
                    "test_id": tc.test_id,
                    "name": tc.name,
                    "status": tc.status,
                    "last_run_at": _iso_utc(tc.last_run_at),
                }
                for tc in tcs
            ],
        })

    return _envelope({
        "request_id": req.request_id,
        "description": req.description,
        "task_type": req.task_type,
        "priority": req.priority,
        "status": req.status,
        "created_at": _iso_utc(req.created_at),
        "completed_at": _iso_utc(req.completed_at),
        "workflow": workflow_payload,
        "subtasks": [
            {
                "subtask_id": s.subtask_id,
                "agent_id": s.agent_id,
                "display_name": request.app.state.config.agents.get(s.agent_id, {}).get("display_name", s.agent_id),
                "status": s.status,
                "started_at": _iso_utc(s.started_at),
                "completed_at": _iso_utc(s.completed_at),
                "output_text": s.output_text or "",
                "output_artifacts": s.output_artifacts,
                "error_message": s.error_message,
            }
            for s in subtasks
        ],
        "stories": stories_data,
        "deployment": deployment_payload,
        # code_commit failure detail (when present). Lets the Story Board show
        # the code_commit stage's failure reason instead of leaving users to
        # guess why the request stopped between testing and deployment.
        "code_commit_error": req.code_commit_error,
        "total_cost": {"cost_usd": round(total_cost, 4)},
        "artifacts": {
            "documents": [
                {
                    "document_id": d.document_id,
                    "doc_type": d.doc_type,
                    "title": d.title,
                    "agent_id": d.agent_id,
                    "version": d.version,
                    "created_at": _iso_utc(d.created_at),
                }
                for d in documents
            ],
            "published_files": req.published_files or [],
            "commit_sha": req.commit_sha,
            "commit_url": req.commit_url,
        },
    })


@router.post("/{request_id}/retry")
async def retry_request(
    request_id: str,
    request: Request,
    user: dict = Depends(require_role("developer", "admin")),
):
    state = request.app.state.state_store
    req = await state.get_request(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status not in ("failed", "cancelled"):
        raise HTTPException(
            status_code=400,
            detail="Only failed or cancelled requests can be retried",
        )

    orchestrator = request.app.state.orchestrator
    result = await orchestrator.submit(
        description=req.description,
        task_type=req.task_type,
        priority=req.priority,
        created_by=user.get("username", ""),
    )
    return _envelope({"request_id": result.request_id, "status": result.status})


def _assert_owner_or_admin(req: Any, user: dict) -> None:
    """Permission check: the request's creator OR any admin may mutate it.

    Raises HTTPException(403) otherwise. Viewers are blocked by the route's
    require_role dependency before this runs, so we only need to distinguish
    developer-who-owns-it from developer-who-doesnt.
    """
    if user.get("role") == "admin":
        return
    if req.created_by and req.created_by == user.get("username"):
        return
    raise HTTPException(
        status_code=403,
        detail="Only the request owner or an admin can cancel or delete this request",
    )


@router.post("/{request_id}/cancel")
async def cancel_request(
    request_id: str,
    request: Request,
    user: dict = Depends(require_role("developer", "admin")),
):
    """Cancel an in-flight request. Idempotent — terminal requests return unchanged."""
    state = request.app.state.state_store
    req = await state.get_request(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    _assert_owner_or_admin(req, user)

    orchestrator = request.app.state.orchestrator
    updated = await orchestrator.cancel(request_id)
    return _envelope({
        "request_id": updated.request_id,
        "status": updated.status,
        "completed_at": updated.completed_at.isoformat() if updated.completed_at else None,
    })


@router.delete("/{request_id}")
async def delete_request(
    request_id: str,
    request: Request,
    user: dict = Depends(require_role("developer", "admin")),
):
    """Hard-delete a request and all cascaded rows (subtasks, stories, traces, etc.).

    Only allowed when the request is in a terminal state (completed, failed,
    cancelled). Running requests must be cancelled first to avoid deleting
    state that a live background task is still writing to.
    """
    state = request.app.state.state_store
    req = await state.get_request(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    _assert_owner_or_admin(req, user)

    if req.status not in ("completed", "failed", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete a request in '{req.status}' state. "
                "Cancel it first, then delete."
            ),
        )

    await state.delete_request(request_id)
    return _envelope({"request_id": request_id, "deleted": True})


@router.get("/{request_id}/stories")
async def get_stories(
    request_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    state = request.app.state.state_store
    stories = await state.get_stories_for_request(request_id)
    stories_data = []
    for st in stories:
        acs = await state.get_acceptance_criteria_for_story(st.story_id)
        tcs = await state.get_test_cases_for_story(st.story_id)
        stories_data.append({
            "story_id": st.story_id,
            "title": st.title,
            "description": st.description,
            "status": st.status,
            "priority": st.priority,
            "assigned_agent": st.assigned_agent,
            "coverage_pct": st.coverage_pct,
            "github_issue_number": st.github_issue_number,
            "acceptance_criteria": [
                {
                    "ac_id": ac.ac_id,
                    "criterion_text": ac.criterion_text,
                    "given": ac.given_clause,
                    "when": ac.when_clause,
                    "then": ac.then_clause,
                    "is_met": ac.is_met,
                }
                for ac in acs
            ],
            "test_cases": [
                {
                    "test_id": tc.test_id,
                    "name": tc.name,
                    "status": tc.status,
                    "last_run_at": tc.last_run_at.isoformat() if tc.last_run_at else None,
                }
                for tc in tcs
            ],
        })
    return _envelope(stories_data)
