"""Request endpoints — submit, list, detail, retry, stories."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.auth.service import get_current_user, require_role

router = APIRouter(prefix="/api/v1/requests", tags=["requests"])


class SubmitRequestBody(BaseModel):
    description: str
    task_type: str = "feature_request"
    priority: str = "medium"
    tags: list[str] = []


def _envelope(data: Any, meta: dict | None = None) -> dict:
    return {"data": data, "meta": meta, "error": None}


@router.post("")
async def submit_request(
    body: SubmitRequestBody,
    request: Request,
    user: dict = Depends(require_role("developer", "admin")),
):
    orchestrator = request.app.state.orchestrator
    result = await orchestrator.submit(
        description=body.description,
        task_type=body.task_type,
        priority=body.priority,
        created_by=user.get("username", ""),
    )
    return _envelope({
        "request_id": result.request_id,
        "status": result.status,
        "description": result.description,
        "priority": result.priority,
        "created_at": result.created_at.isoformat(),
    })


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
                "created_at": r.created_at.isoformat(),
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
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
    total_cost = sum(u.cost_usd for u in token_usage)

    return _envelope({
        "request_id": req.request_id,
        "description": req.description,
        "task_type": req.task_type,
        "priority": req.priority,
        "status": req.status,
        "created_at": req.created_at.isoformat(),
        "completed_at": req.completed_at.isoformat() if req.completed_at else None,
        "subtasks": [
            {
                "subtask_id": s.subtask_id,
                "agent_id": s.agent_id,
                "status": s.status,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                "output_artifacts": s.output_artifacts,
                "error_message": s.error_message,
            }
            for s in subtasks
        ],
        "stories": [
            {
                "story_id": st.story_id,
                "title": st.title,
                "status": st.status,
                "assigned_agent": st.assigned_agent,
                "coverage_pct": st.coverage_pct,
                "github_issue_number": st.github_issue_number,
            }
            for st in stories
        ],
        "total_cost": {"cost_usd": round(total_cost, 4)},
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
    if req.status != "failed":
        raise HTTPException(status_code=400, detail="Only failed requests can be retried")

    orchestrator = request.app.state.orchestrator
    result = await orchestrator.submit(
        description=req.description,
        task_type=req.task_type,
        priority=req.priority,
        created_by=user.get("username", ""),
    )
    return _envelope({"request_id": result.request_id, "status": result.status})


@router.get("/{request_id}/stories")
async def get_stories(
    request_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    state = request.app.state.state_store
    stories = await state.get_stories_for_request(request_id)
    return _envelope([
        {
            "story_id": st.story_id,
            "title": st.title,
            "description": st.description,
            "status": st.status,
            "priority": st.priority,
            "assigned_agent": st.assigned_agent,
            "coverage_pct": st.coverage_pct,
            "github_issue_number": st.github_issue_number,
        }
        for st in stories
    ])
