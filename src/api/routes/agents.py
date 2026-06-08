"""Agent endpoints — list agent statuses + manage per-agent model overrides."""

import time
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from src.auth.service import get_current_user, get_principal, require_role

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


def _resolve_assigned_model(
    agent_id: str,
    yaml_model: str,
    overrides: dict[str, str],
) -> tuple[str, bool]:
    """Return ``(assigned_model, override_active)``.

    The Team Status panel and the cyberpunk overlay both render the
    agent's *currently effective* model. ``assigned_model`` is what
    the resolver will return at dispatch time (modulo the layer-1
    request override, which is per-call and can't be precomputed
    here). ``override_active`` drives the badge that shows "override"
    in the UI vs. the default.
    """
    db_value = overrides.get(agent_id)
    if db_value:
        return db_value, True
    return yaml_model, False


@router.get("")
async def list_agents(
    request: Request,
    user: dict = Depends(get_principal),  # HAI-16 — JWT or service token (team status)
):
    config = request.app.state.config
    state = request.app.state.state_store

    # Source 1: active subtasks — workflow runner path. Each in-flight
    # subtask pins its agent to in_progress with a request_id link.
    active_by_agent: dict[str, str] = {}
    for st in await state.get_active_subtasks():
        active_by_agent.setdefault(st.agent_id, st.request_id)

    # AET-37 — total historical subtask count per agent. Drives the
    # SCAFFOLD badge on Team Status: total_subtasks==0 means the
    # agent is configured but has never actually run in this DB, so
    # the UI flags it as cosmetic-vs-functional rather than letting
    # the page project "all 15 working" when most haven't run yet.
    try:
        subtask_totals: dict[str, int] = await state.count_subtasks_by_agent()
    except Exception:
        subtask_totals = {}

    # Source 2: in-flight single_agent_call invocations — Project-driven
    # Build path (PRD / API spec / brief / epics / features / atomic
    # tasks generation). These don't create subtasks so they were
    # previously invisible here — the Team Status page showed every
    # card as idle even while an agent was actively generating a
    # 60 KB PRD. Now we merge the executor's busy map so the same
    # agents light up "in progress" with a label like "PRD generation"
    # and elapsed-seconds count.
    busy_single: dict[str, dict] = {}
    executor = getattr(request.app.state, "agent_executor", None)
    if executor is not None and hasattr(executor, "get_busy_agents"):
        try:
            busy_single = executor.get_busy_agents()
        except Exception:
            busy_single = {}

    # PAM-13 — surface model resolution + tool count.
    # ``override_map``: agent_id → catalog_id, populated from the
    # agent_model_overrides table. Bulk-loaded once so we don't fan
    # out one query per agent. Soft-fails to {} if the store can't
    # serve the call (keeps the page renderable on a DB hiccup).
    override_map: dict[str, str] = {}
    try:
        for row in await state.list_agent_model_overrides():
            override_map[row["agent_id"]] = row["model_id"]
    except Exception:  # noqa: BLE001
        override_map = {}

    # Tool registry only exists when the executor is wired (mock-mode
    # boots without one). Default to 0 — the UI hides the chip when
    # the count is 0 so a missing registry doesn't break layout.
    tool_registry = getattr(executor, "tool_registry", None)

    now = time.time()
    agents = []
    for agent_id, agent_config in config.agents.items():
        active_request = active_by_agent.get(agent_id)
        single = busy_single.get(agent_id)
        # An agent is in_progress if EITHER source sees it busy. The
        # workflow-runner subtask wins for the current_task link
        # (more useful than a generic label), but the single_agent_call
        # label fills in when there's no subtask.
        if active_request:
            status_value = "in_progress"
            current_task = active_request
            current_label = None
            elapsed_s = None
        elif single is not None:
            status_value = "in_progress"
            current_task = None
            current_label = single.get("label")
            started = single.get("started_at") or now
            elapsed_s = max(0, int(now - started))
        else:
            status_value = "idle"
            current_task = None
            current_label = None
            elapsed_s = None

        yaml_model = agent_config.get("model", "")
        assigned_model, override_active = _resolve_assigned_model(
            agent_id, yaml_model, override_map,
        )

        # tool_count is the size of the schemas the agent would see at
        # dispatch — same surface BaseAgent reads. Cheaper than
        # walking config separately and stays correct even if a tool
        # is grant-revoked at runtime.
        tool_count = 0
        if tool_registry is not None:
            try:
                tool_count = len(tool_registry.get_schemas_for_agent(agent_id))
            except Exception:  # noqa: BLE001
                tool_count = 0
        if tool_count == 0:
            # Config-only fallback so the chip renders even in mock mode.
            tool_count = len(agent_config.get("tools", []) or [])

        agents.append({
            "agent_id": agent_id,
            "display_name": agent_config.get("display_name", agent_id),
            "role": agent_config.get("role", ""),
            "team": agent_config.get("team", ""),
            # ``model`` kept for backward compat with older frontend
            # builds that haven't picked up the new fields yet — it
            # mirrors ``assigned_model`` so the legacy display stays
            # accurate when an override is set.
            "model": assigned_model,
            # PAM-13 — explicit model surface for the override UI.
            "default_model": yaml_model,
            "assigned_model": assigned_model,
            "override_active": override_active,
            "tool_count": tool_count,
            "status": status_value,
            "current_task": current_task,
            "current_label": current_label,
            "elapsed_seconds": elapsed_s,
            # AET-37 — total subtasks ever executed by this agent.
            # 0 → cosmetic scaffold (configured but never invoked).
            "total_subtasks": subtask_totals.get(agent_id, 0),
        })
    return {"data": agents, "meta": None, "error": None}


# ── PAM-13 — model override mutation routes ────────────────────────────
#
# All three are admin-gated. PATCH and DELETE per-agent use the agent_id
# path param; the bulk DELETE wipes every override in one call (Team
# Status "Reset all to defaults" button). PATCH emits the
# ``agent.model_changed`` event so the WebSocket feed and the cost
# dashboard's audit-log surface pick it up live.


@router.patch("/{agent_id}/model")
async def assign_agent_model(
    agent_id: str,
    request: Request,
    body: dict[str, Any] = Body(...),
    user: dict = Depends(require_role("admin")),
):
    """Upsert the model override for *agent_id*. Body: ``{model_id: str}``.

    Validates that ``model_id`` exists in the catalog before writing,
    so a typo never lands in the DB. Returns 400 on a missing/invalid
    body, 404 when the agent is unknown to the config loader, 422 when
    the model id isn't in the catalog. On success returns the row.
    """
    if not isinstance(body, dict) or not body.get("model_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="body must be {model_id: <catalog id>}",
        )
    model_id = str(body["model_id"]).strip()
    if not model_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="model_id is required",
        )

    config = request.app.state.config
    if agent_id not in config.agents:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown agent_id '{agent_id}'",
        )

    # Catalog membership check: the resolver also enforces this, but
    # validating up front gives the operator a clean 422 with the bad
    # id echoed back, instead of a silent fall-through to YAML at
    # dispatch time (which would be the worst kind of fail — looks
    # like it worked but didn't).
    executor = getattr(request.app.state, "agent_executor", None)
    catalog = getattr(executor, "model_catalog", None) if executor else None
    if catalog is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="model catalog not loaded — cannot validate model_id",
        )
    # Accept either a real catalog id OR a legacy provider string —
    # both are honoured by the resolver, so both should be settable.
    canonical = (
        model_id if catalog.has(model_id)
        else catalog.resolve_legacy_provider(model_id)
    )
    if not canonical:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"model_id '{model_id}' is not in the catalog. "
                f"GET /api/v1/models for the list of valid ids."
            ),
        )

    state = request.app.state.state_store
    username = user.get("username") or user.get("sub") or "system"
    await state.set_agent_model_override(agent_id, canonical, updated_by=username)

    # Emit on the EventEmitter so WebSocket subscribers see the change
    # live. Soft-fail: an emitter outage MUST NOT block the write
    # (the override is already persisted; the event is just notification).
    events = getattr(request.app.state, "events", None)
    if events is not None:
        try:
            await events.emit("agent.model_changed", {
                "agent_id": agent_id,
                "model_id": canonical,
                "updated_by": username,
                "action": "assigned",
            })
        except Exception:  # noqa: BLE001
            pass

    return {
        "data": {
            "agent_id": agent_id,
            "model_id": canonical,
            "updated_by": username,
            "override_active": True,
        },
        "meta": None,
        "error": None,
    }


@router.delete("/model-overrides")
async def clear_all_agent_model_overrides(
    request: Request,
    user: dict = Depends(require_role("admin")),
):
    """Wipe every override at once — "Reset to defaults" on Team Status.

    Returned ``cleared`` count is informational; the UI shows a toast
    like "Reset 4 overrides." Emits a single ``agent.model_changed``
    event with ``action: cleared_all`` rather than one per row (the
    UI just refetches the agents list anyway).

    NOTE: This route MUST be declared BEFORE ``/{agent_id}/model``
    on the DELETE verb so FastAPI's path matcher doesn't try to
    treat ``model-overrides`` as an agent_id and 404. The PATCH route
    above is on a different verb so it doesn't conflict.
    """
    state = request.app.state.state_store
    n = await state.clear_all_agent_model_overrides()
    username = user.get("username") or user.get("sub") or "system"

    events = getattr(request.app.state, "events", None)
    if events is not None:
        try:
            await events.emit("agent.model_changed", {
                "agent_id": "*",
                "cleared": n,
                "updated_by": username,
                "action": "cleared_all",
            })
        except Exception:  # noqa: BLE001
            pass

    return {
        "data": {"cleared": n, "updated_by": username},
        "meta": None,
        "error": None,
    }


@router.delete("/{agent_id}/model")
async def clear_agent_model_override(
    agent_id: str,
    request: Request,
    user: dict = Depends(require_role("admin")),
):
    """Remove the override for *agent_id*. 204 on success, 404 when
    there was nothing to clear (gives the UI a clean "already at
    default" indicator instead of silently succeeding)."""
    state = request.app.state.state_store
    removed = await state.delete_agent_model_override(agent_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no override set for '{agent_id}'",
        )
    username = user.get("username") or user.get("sub") or "system"
    events = getattr(request.app.state, "events", None)
    if events is not None:
        try:
            await events.emit("agent.model_changed", {
                "agent_id": agent_id,
                "updated_by": username,
                "action": "cleared",
            })
        except Exception:  # noqa: BLE001
            pass
    return {
        "data": {"agent_id": agent_id, "updated_by": username, "override_active": False},
        "meta": None,
        "error": None,
    }
