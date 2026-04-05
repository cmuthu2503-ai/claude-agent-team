"""Release/deployment endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request

from src.auth.service import get_current_user, require_role

router = APIRouter(prefix="/api/v1/releases", tags=["releases"])


@router.get("")
async def list_releases(
    request: Request,
    environment: str | None = None,
    limit: int = 20,
    user: dict = Depends(get_current_user),
):
    state = request.app.state.state_store
    deployments = await state.list_deployments(environment=environment, limit=limit)
    return {
        "data": [
            {
                "deploy_id": d.deploy_id,
                "request_id": d.request_id,
                "git_sha": d.git_sha,
                "environment": d.environment,
                "status": d.status,
                "deployed_at": d.deployed_at.isoformat() if d.deployed_at else None,
                "verified_at": d.verified_at.isoformat() if d.verified_at else None,
            }
            for d in deployments
        ],
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
    deployment = await state.get_deployment(deploy_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return {"data": {"deploy_id": deploy_id, "status": "rollback_initiated"}, "meta": None, "error": None}
