"""Cost and token usage endpoints."""

from datetime import datetime

from fastapi import APIRouter, Depends, Request

from src.auth.service import get_current_user, get_principal, require_role

router = APIRouter(prefix="/api/v1/cost", tags=["cost"])


@router.get("/dashboard")
async def cost_dashboard(
    request: Request,
    project_id: str | None = None,  # PM-17 — scope all rollups to a single project
    user: dict = Depends(get_principal),  # HAI-13 — JWT or service token
):
    state = request.app.state.state_store

    # Get all token usage records for breakdowns
    db = await state._get_db()

    # PM-17 / PDB-08 / cost-attribution-fix — when scoped to a project,
    # restrict every aggregation by the direct `token_usage.project_id`
    # column. That column is now populated by:
    #   • record_token_usage (which back-derives project_id from
    #     request_id when the caller didn't set it explicitly), and
    #   • single_agent_call's BPD generators (which pass project_id
    #     directly because they have no request_id).
    # Previously this was an OR-of-subqueries over request_id +
    # project_artifact_id, which left BPD generation spend stranded —
    # those rows had request_id='' AND project_artifact_id=NULL, so they
    # appeared in the All Projects total but couldn't be attributed to
    # any project. The old OR clause is kept for the artifact branch
    # only (rows recorded before this migration are backfilled by the
    # migration UPDATE for the request branch but artifact-only rows
    # need the legacy join). New rows match on project_id directly.
    where_proj = ""
    params_proj: tuple = ()
    if project_id:
        where_proj = (
            "WHERE project_id = ? "
            "OR project_artifact_id IN (SELECT artifact_id FROM project_artifacts WHERE project_id = ?)"
        )
        params_proj = (project_id, project_id)

    # PM-17 fix: Daily/monthly totals also need to respect the project filter,
    # otherwise the "Today"/"This Month" cards stay global while the breakdown
    # tables go filtered — confusing. Compute them inline against the same
    # scoped query when project_id is set, fall back to the legacy helpers
    # (which scan the full table once) when unscoped.
    today_iso = datetime.utcnow().date().isoformat()
    first_of_month_iso = datetime.utcnow().replace(day=1).date().isoformat()
    if project_id:
        # Same shape as the where_proj clause above so the daily/monthly
        # cards stay consistent with the breakdown tables — direct
        # project_id column hit plus the legacy artifact-id branch for
        # pre-migration rows that only have project_artifact_id set.
        proj_filter = (
            "(project_id = ? "
            "OR project_artifact_id IN (SELECT artifact_id FROM project_artifacts WHERE project_id = ?))"
        )
        async with db.execute(
            f"SELECT COALESCE(SUM(cost_usd), 0) AS total FROM token_usage "
            f"WHERE {proj_filter} AND recorded_at >= ?",
            (project_id, project_id, today_iso),
        ) as cur:
            daily = float((await cur.fetchone())["total"])
        async with db.execute(
            f"SELECT COALESCE(SUM(cost_usd), 0) AS total FROM token_usage "
            f"WHERE {proj_filter} AND recorded_at >= ?",
            (project_id, project_id, first_of_month_iso),
        ) as cur:
            monthly = float((await cur.fetchone())["total"])
    else:
        daily = await state.get_daily_cost()
        monthly = await state.get_monthly_cost()

    # Today's input/output token totals — the cyberpunk overlay's ticker pairs
    # these with `today.total_cost_usd` so "[COST] today" and "[TOKENS] today"
    # share the same time window. Previously the ticker showed all-time tokens
    # next to today's cost (mixed scopes), which made the bar hard to read.
    where_today = "recorded_at >= ?"
    today_params: tuple = (today_iso,)
    if project_id:
        where_today = (
            "recorded_at >= ? AND "
            "(project_id = ? "
            "OR project_artifact_id IN (SELECT artifact_id FROM project_artifacts WHERE project_id = ?))"
        )
        today_params = (today_iso, project_id, project_id)
    async with db.execute(
        f"SELECT COALESCE(SUM(input_tokens), 0) AS inp, "
        f"COALESCE(SUM(output_tokens), 0) AS outp "
        f"FROM token_usage WHERE {where_today}",
        today_params,
    ) as cur:
        today_tok_row = await cur.fetchone()
    today_input_tokens = int(today_tok_row["inp"]) if today_tok_row else 0
    today_output_tokens = int(today_tok_row["outp"]) if today_tok_row else 0

    # Per-model breakdown
    async with db.execute(
        f"SELECT model, SUM(input_tokens) as inp, SUM(output_tokens) as outp, SUM(cost_usd) as cost "
        f"FROM token_usage {where_proj} GROUP BY model ORDER BY cost DESC",
        params_proj,
    ) as cursor:
        model_rows = await cursor.fetchall()

    by_model = [
        {"model": r["model"], "input_tokens": r["inp"], "output_tokens": r["outp"], "cost_usd": round(r["cost"], 4)}
        for r in model_rows
    ]

    # Per-agent breakdown
    async with db.execute(
        f"SELECT agent_id, SUM(input_tokens) as inp, SUM(output_tokens) as outp, SUM(cost_usd) as cost, COUNT(*) as calls "
        f"FROM token_usage {where_proj} GROUP BY agent_id ORDER BY cost DESC",
        params_proj,
    ) as cursor:
        agent_rows = await cursor.fetchall()

    by_agent = [
        {"agent_id": r["agent_id"], "input_tokens": r["inp"], "output_tokens": r["outp"], "cost_usd": round(r["cost"], 4), "calls": r["calls"]}
        for r in agent_rows
    ]

    # Per-request breakdown (top 10 most expensive)
    async with db.execute(
        f"SELECT request_id, SUM(cost_usd) as cost, SUM(input_tokens) as inp, SUM(output_tokens) as outp, COUNT(*) as calls "
        f"FROM token_usage {where_proj} GROUP BY request_id ORDER BY cost DESC LIMIT 10",
        params_proj,
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
            "today": {
                "total_cost_usd": round(daily, 4),
                "total_input_tokens": today_input_tokens,
                "total_output_tokens": today_output_tokens,
            },
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


@router.get("/orphans")
async def list_orphan_cost(
    request: Request,
    user: dict = Depends(require_role("admin")),
):
    """Return rollup of token_usage rows that have NO project linkage
    (no request_id → project, no project_artifact_id, no project_id).

    These rows came from pre-migration BPD generation calls
    (single_agent_call with all three keys empty/NULL). The dashboard's
    All-Projects total includes them but no per-project filter can
    surface them — they're unattributable spend. Admins can use this
    endpoint to inspect the blast radius before calling the DELETE
    endpoint below to purge them.
    """
    state = request.app.state.state_store
    db = await state._get_db()
    # An "orphan" = no request → project link AND no artifact → project
    # link AND project_id IS NULL. The migration backfilled project_id
    # from request_id where possible, so anything still NULL after
    # migration is genuinely unattributable.
    async with db.execute(
        "SELECT COUNT(*) AS n, "
        "COALESCE(SUM(cost_usd), 0) AS cost, "
        "COALESCE(SUM(input_tokens), 0) AS inp, "
        "COALESCE(SUM(output_tokens), 0) AS outp "
        "FROM token_usage "
        "WHERE project_id IS NULL "
        "AND (request_id = '' OR request_id IS NULL) "
        "AND project_artifact_id IS NULL"
    ) as cur:
        row = await cur.fetchone()
    return {
        "data": {
            "orphan_count": int(row["n"]) if row else 0,
            "orphan_cost_usd": round(float(row["cost"]) if row else 0.0, 4),
            "orphan_input_tokens": int(row["inp"]) if row else 0,
            "orphan_output_tokens": int(row["outp"]) if row else 0,
        },
        "meta": None,
        "error": None,
    }


@router.delete("/orphans")
async def delete_orphan_cost(
    request: Request,
    user: dict = Depends(require_role("admin")),
):
    """Hard-delete unattributable token_usage rows.

    Admin-only. Use after this migration ships to clean up cost rows
    from BPD generation calls that ran BEFORE single_agent_call learned
    to pass project_id. New rows are attributed correctly so this
    endpoint is effectively a one-shot cleanup — running it on a clean
    DB is a no-op.
    """
    state = request.app.state.state_store
    db = await state._get_db()
    async with db.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS cost "
        "FROM token_usage "
        "WHERE project_id IS NULL "
        "AND (request_id = '' OR request_id IS NULL) "
        "AND project_artifact_id IS NULL"
    ) as cur:
        before = await cur.fetchone()
    await db.execute(
        "DELETE FROM token_usage "
        "WHERE project_id IS NULL "
        "AND (request_id = '' OR request_id IS NULL) "
        "AND project_artifact_id IS NULL"
    )
    await db.commit()
    return {
        "data": {
            "deleted_count": int(before["n"]) if before else 0,
            "deleted_cost_usd": round(float(before["cost"]) if before else 0.0, 4),
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
