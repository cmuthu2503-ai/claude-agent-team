"""Cost and token usage endpoints."""

from fastapi import APIRouter, Depends, Request

from src.auth.service import get_current_user

router = APIRouter(prefix="/api/v1/cost", tags=["cost"])


@router.get("/dashboard")
async def cost_dashboard(
    request: Request,
    user: dict = Depends(get_current_user),
):
    state = request.app.state.state_store
    daily = await state.get_daily_cost()
    monthly = await state.get_monthly_cost()

    # Get all token usage records for breakdowns
    db = await state._get_db()

    # Per-model breakdown
    async with db.execute(
        "SELECT model, SUM(input_tokens) as inp, SUM(output_tokens) as outp, SUM(cost_usd) as cost "
        "FROM token_usage GROUP BY model ORDER BY cost DESC"
    ) as cursor:
        model_rows = await cursor.fetchall()

    by_model = [
        {"model": r["model"], "input_tokens": r["inp"], "output_tokens": r["outp"], "cost_usd": round(r["cost"], 4)}
        for r in model_rows
    ]

    # Per-agent breakdown
    async with db.execute(
        "SELECT agent_id, SUM(input_tokens) as inp, SUM(output_tokens) as outp, SUM(cost_usd) as cost, COUNT(*) as calls "
        "FROM token_usage GROUP BY agent_id ORDER BY cost DESC"
    ) as cursor:
        agent_rows = await cursor.fetchall()

    by_agent = [
        {"agent_id": r["agent_id"], "input_tokens": r["inp"], "output_tokens": r["outp"], "cost_usd": round(r["cost"], 4), "calls": r["calls"]}
        for r in agent_rows
    ]

    # Per-request breakdown (top 10 most expensive)
    async with db.execute(
        "SELECT request_id, SUM(cost_usd) as cost, SUM(input_tokens) as inp, SUM(output_tokens) as outp, COUNT(*) as calls "
        "FROM token_usage GROUP BY request_id ORDER BY cost DESC LIMIT 10"
    ) as cursor:
        request_rows = await cursor.fetchall()

    by_request = [
        {"request_id": r["request_id"], "cost_usd": round(r["cost"], 4), "input_tokens": r["inp"], "output_tokens": r["outp"], "calls": r["calls"]}
        for r in request_rows
    ]

    # Totals
    total_input = sum(r["inp"] for r in agent_rows) if agent_rows else 0
    total_output = sum(r["outp"] for r in agent_rows) if agent_rows else 0
    total_calls = sum(r["calls"] for r in agent_rows) if agent_rows else 0

    return {
        "data": {
            "today": {"total_cost_usd": round(daily, 4)},
            "this_month": {"total_cost_usd": round(monthly, 4)},
            "totals": {
                "total_cost_usd": round(sum(r["cost"] for r in agent_rows), 4) if agent_rows else 0,
                "total_input_tokens": total_input,
                "total_output_tokens": total_output,
                "total_calls": total_calls,
            },
            "by_model": by_model,
            "by_agent": by_agent,
            "by_request": by_request,
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
            "records": [
                {
                    "agent_id": u.agent_id,
                    "model": u.model,
                    "input_tokens": u.input_tokens,
                    "output_tokens": u.output_tokens,
                    "cost_usd": round(u.cost_usd, 4),
                }
                for u in usage
            ],
        },
        "meta": None,
        "error": None,
    }
