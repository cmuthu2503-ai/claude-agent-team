"""Ops monitoring endpoints — post-deploy health trigger.

Called by the standalone supervisor after a deployment completes. This is an
internal endpoint — it's not exposed in the OpenAPI docs and is protected by
a simple shared secret (``OPS_SECRET`` env var) to prevent external callers
from flooding the ops_heal_agent with spurious health checks.

If ``OPS_SECRET`` is not set, the endpoint accepts any request (for dev
convenience). Set it in production via the .env file or Docker secrets.

POST /api/v1/ops/monitor
    Body: {"request_id": "REQ-XXX", "deployment_id": "dep-YYY"}
    Response: {"status": "accepted", "request_id": "REQ-XXX"}
    The ops_heal_agent runs asynchronously — the HTTP response returns
    immediately; the agent's verdict arrives via WebSocket as ops.* events.
"""

from __future__ import annotations

import asyncio
import os

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/ops", tags=["ops"])

_OPS_SECRET = os.getenv("OPS_SECRET", "").strip()


class OpsMonitorRequest(BaseModel):
    request_id: str
    deployment_id: str
    secret: str = ""


@router.post("/monitor", include_in_schema=False)
async def trigger_ops_monitor(body: OpsMonitorRequest, request: Request) -> dict:
    """Trigger the ops_heal_agent for a completed deployment.

    Called by the host-side supervisor after ``deployment_states.current_step``
    reaches ``completed``. The agent runs in a background task so this endpoint
    always returns 202-style immediately.

    Authentication: optional shared-secret check via ``OPS_SECRET`` env var.
    When not set (default in dev), all callers are accepted.
    """
    # Shared-secret check — soft: only enforce when OPS_SECRET is configured
    if _OPS_SECRET and body.secret != _OPS_SECRET:
        logger.warning(
            "ops_monitor_unauthorized",
            request_id=body.request_id,
            deployment_id=body.deployment_id,
        )
        raise HTTPException(status_code=403, detail="Invalid ops secret")

    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        # Mock/test environment — no orchestrator wired up
        logger.warning("ops_monitor_no_orchestrator", request_id=body.request_id)
        return {"status": "accepted", "request_id": body.request_id, "note": "no orchestrator"}

    logger.info(
        "ops_monitor_accepted",
        request_id=body.request_id,
        deployment_id=body.deployment_id,
    )

    # Fire and forget — the agent does its health checks asynchronously
    asyncio.create_task(
        orchestrator.trigger_ops_monitor(
            request_id=body.request_id,
            deployment_id=body.deployment_id,
        )
    )

    return {"status": "accepted", "request_id": body.request_id}


@router.get("/latest")  # HAI-15 — schema-visible so the MCP contract test can pin it
async def get_latest_ops_event(request: Request) -> dict:
    """Return the most recent ops health verdict from the deployment_states table.

    Used by the SystemHealthPill component to show a persistent health status
    badge on the Command Center without needing a live WebSocket connection.

    Returns the latest completed deployment row with its ops verdict, or
    {"verdict": "unknown"} if no deployment has completed yet.
    """
    state = getattr(request.app.state, "state_store", None)
    if state is None:
        return {"verdict": "unknown"}

    try:
        # Query the most recent completed deployment for a platform request
        # (excludes per-project rows via request_project_id IS NULL guard)
        row = await state.get_latest_deployment()
        if not row:
            return {"verdict": "unknown", "deployment_id": None}

        # The ops verdict isn't stored separately — derive it from the step
        # history or the strategy field until we add an ops_verdict column.
        # For now, "completed" = healthy, "failed"/"rolled_back" = issue.
        step = row.get("current_step", "")
        if step == "completed":
            verdict = "HEALTHY"
        elif step in ("failed", "rolled_back"):
            verdict = "UNHEALTHY"
        else:
            verdict = "unknown"

        return {
            "verdict": verdict,
            "deployment_id": row.get("deployment_id"),
            "request_id": row.get("request_id"),
            "current_step": step,
            "strategy": row.get("strategy"),
            "risk": row.get("risk"),
        }
    except Exception as exc:
        logger.warning("ops_latest_failed", error=str(exc))
        return {"verdict": "unknown", "error": str(exc)}
