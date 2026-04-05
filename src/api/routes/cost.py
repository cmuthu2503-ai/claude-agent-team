"""Cost and token usage endpoints."""

from fastapi import APIRouter, Depends, Request

from src.auth.service import get_current_user, require_role

router = APIRouter(prefix="/api/v1/cost", tags=["cost"])


@router.get("/dashboard")
async def cost_dashboard(
    request: Request,
    user: dict = Depends(get_current_user),
):
    state = request.app.state.state_store
    daily = await state.get_daily_cost()
    monthly = await state.get_monthly_cost()
    return {
        "data": {
            "today": {"total_cost_usd": round(daily, 2)},
            "this_month": {"total_cost_usd": round(monthly, 2)},
        },
        "meta": None,
        "error": None,
    }


@router.get("/requests/{request_id}")
async def cost_for_request(
    request_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    state = request.app.state.state_store
    usage = await state.get_token_usage_for_request(request_id)
    return {
        "data": {
            "request_id": request_id,
            "total_cost_usd": round(sum(u.cost_usd for u in usage), 4),
            "by_agent": {},
            "records": [
                {
                    "agent_id": u.agent_id,
                    "model": u.model,
                    "input_tokens": u.input_tokens,
                    "output_tokens": u.output_tokens,
                    "cost_usd": u.cost_usd,
                }
                for u in usage
            ],
        },
        "meta": None,
        "error": None,
    }
