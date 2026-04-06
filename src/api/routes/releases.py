"""Release/deployment endpoints — includes deployment state machine."""

from fastapi import APIRouter, Depends, HTTPException, Request

from src.auth.service import get_current_user, require_role

router = APIRouter(prefix="/api/v1/releases", tags=["releases"])


@router.get("")
async def list_releases(
    request: Request,
    limit: int = 20,
    user: dict = Depends(get_current_user),
):
    state = request.app.state.state_store

    # Get deployment states (Level 3)
    db = await state._get_db()
    async with db.execute(
        "SELECT * FROM deployment_states ORDER BY started_at DESC LIMIT ?", (limit,)
    ) as cursor:
        rows = await cursor.fetchall()

    import json
    deployments = []
    for r in rows:
        deployments.append({
            "deployment_id": r["deployment_id"],
            "request_id": r["request_id"],
            "commit_sha": r["commit_sha"] or "",
            "current_step": r["current_step"],
            "step_history": json.loads(r["step_history"]) if r["step_history"] else [],
            "files_committed": json.loads(r["files_committed"]) if r["files_committed"] else [],
            "started_at": r["started_at"],
            "completed_at": r["completed_at"],
            "error_message": r["error_message"],
        })

    # Also get legacy deployments
    legacy = await state.list_deployments(limit=limit)
    for d in legacy:
        deployments.append({
            "deployment_id": d.deploy_id,
            "request_id": d.request_id,
            "commit_sha": d.git_sha,
            "current_step": d.status,
            "step_history": [],
            "files_committed": [],
            "started_at": d.deployed_at.isoformat() if d.deployed_at else None,
            "completed_at": d.verified_at.isoformat() if d.verified_at else None,
            "error_message": None,
        })

    return {"data": deployments, "meta": None, "error": None}


@router.get("/{deployment_id}")
async def get_release(
    deployment_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    state = request.app.state.state_store
    dep = await state.get_deployment_state(deployment_id)
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")

    import json
    return {
        "data": {
            "deployment_id": dep.deployment_id,
            "request_id": dep.request_id,
            "commit_sha": dep.commit_sha,
            "current_step": dep.current_step,
            "step_history": dep.step_history,
            "files_committed": dep.files_committed,
            "started_at": dep.started_at.isoformat(),
            "updated_at": dep.updated_at.isoformat() if dep.updated_at else None,
            "completed_at": dep.completed_at.isoformat() if dep.completed_at else None,
            "error_message": dep.error_message,
            "rollback_sha": dep.rollback_sha,
        },
        "meta": None,
        "error": None,
    }


@router.post("/{deploy_id}/rollback")
async def rollback_deployment(
    deploy_id: str,
    request: Request,
    user: dict = Depends(require_role("admin")),
):
    state = request.app.state.state_store
    dep = await state.get_deployment_state(deploy_id)
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return {"data": {"deploy_id": deploy_id, "status": "rollback_initiated"}, "meta": None, "error": None}
