"""Request endpoints — submit, list, detail, retry, stories, attachments."""

import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.auth.service import get_current_user, require_role

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
                "display_name": request.app.state.config.agents.get(s.agent_id, {}).get("display_name", s.agent_id),
                "status": s.status,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                "output_text": s.output_text or "",
                "output_artifacts": s.output_artifacts,
                "error_message": s.error_message,
            }
            for s in subtasks
        ],
        "stories": [
            {
                "story_id": st.story_id,
                "title": st.title,
                "description": st.description or "",
                "status": st.status,
                "priority": st.priority,
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
