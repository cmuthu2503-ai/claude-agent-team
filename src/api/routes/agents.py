"""Agent endpoints — list agent statuses."""

from fastapi import APIRouter, Depends, Request

from src.auth.service import get_current_user

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


@router.get("")
async def list_agents(
    request: Request,
    user: dict = Depends(get_current_user),
):
    config = request.app.state.config
    agents = []
    for agent_id, agent_config in config.agents.items():
        agents.append({
            "agent_id": agent_id,
            "display_name": agent_config.get("display_name", agent_id),
            "role": agent_config.get("role", ""),
            "team": agent_config.get("team", ""),
            "model": agent_config.get("model", ""),
            "status": "idle",  # TODO: track real-time status from orchestrator
            "current_task": None,
        })
    return {"data": agents, "meta": None, "error": None}
