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
    state = request.app.state.state_store

    active_by_agent: dict[str, str] = {}
    for st in await state.get_active_subtasks():
        active_by_agent.setdefault(st.agent_id, st.request_id)

    agents = []
    for agent_id, agent_config in config.agents.items():
        active_request = active_by_agent.get(agent_id)
        agents.append({
            "agent_id": agent_id,
            "display_name": agent_config.get("display_name", agent_id),
            "role": agent_config.get("role", ""),
            "team": agent_config.get("team", ""),
            "model": agent_config.get("model", ""),
            "status": "in_progress" if active_request else "idle",
            "current_task": active_request,
        })
    return {"data": agents, "meta": None, "error": None}
