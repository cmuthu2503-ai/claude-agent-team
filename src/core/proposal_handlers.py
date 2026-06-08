"""Gated-action handlers — the P3 lifecycle actions (HAI-33..42 / FR-050..056).

Each handler executes a CONFIRMED proposal's action by invoking the SAME route
function the dashboard uses — so there is exactly ZERO duplication of the (heavy)
create/generate/deploy logic, and a Hermes-proposed action behaves identically to
the human-driven one. The human confirm IS the authorization, so handlers act as a
synthetic system principal.

Registration: ``register_all(registry)`` is called once at startup (src/main.py),
wiring each gated ``action_type`` to its handler. An action_type that is gated but
has no handler here still fails cleanly (the dispatcher returns ``failed``).

Handler contract (from the dispatcher, HAI-25): ``(proposal, ctx) -> {"result_ref"}``
where ``ctx`` is the FastAPI Request of the confirm call (so ``ctx.app.state`` has
the state store, orchestrator, etc.). Route functions are referenced via the
``projects``/``requests`` modules so tests can monkeypatch them.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import HTTPException

from src.api.routes import projects as _projects
from src.models.base import RollbackRequest, TaskStatus

logger = structlog.get_logger()

# Handlers run on behalf of a human-confirmed proposal, so they act with admin
# authority (the gate already enforced human approval). Shaped like a get_current_user
# principal so route functions that read user fields work unchanged.
_SYSTEM_PRINCIPAL: dict[str, Any] = {
    "sub": "system:proposal-executor",
    "user_id": "system:proposal-executor",
    "username": "proposal-executor",
    "role": "admin",
    "is_service_token": False,
}


def _system_principal() -> dict[str, Any]:
    return dict(_SYSTEM_PRINCIPAL)


async def _invoke(coro: Any) -> Any:
    """Await a route coroutine, converting an HTTPException into a plain error so
    the dispatcher records a clean, readable failure reason on the proposal."""
    try:
        return await coro
    except HTTPException as e:
        raise RuntimeError(f"action failed [{e.status_code}]: {e.detail}") from e


def _id_from(resp: Any, *keys: str) -> str | None:
    data = (resp or {}).get("data") if isinstance(resp, dict) else None
    if not isinstance(data, dict):
        return None
    for k in keys:
        v = data.get(k)
        if v:
            return str(v)
    return None


def _payload_for(body_model: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Keep only the keys the route's body model accepts (drops extras Hermes may
    have attached so Pydantic doesn't choke)."""
    return {k: v for k, v in (payload or {}).items() if k in body_model.model_fields}


# ── HAI-36 — project.create ──────────────────────────────────────────────────

async def project_create(proposal: Any, ctx: Any) -> dict[str, Any]:
    payload = _payload_for(_projects.CreateProjectBody, proposal.payload)
    # Default create_repo OFF for proposed projects — a confirm shouldn't trigger a
    # surprise GitHub repo creation unless the proposer explicitly asked for it.
    payload.setdefault("create_repo", False)
    body = _projects.CreateProjectBody(**payload)
    resp = await _invoke(
        _projects.create_project(body=body, request=ctx, user=_system_principal())
    )
    return {"result_ref": _id_from(resp, "project_id", "id")}


# ── HAI-37 — project.brief.set ───────────────────────────────────────────────

async def project_set_brief(proposal: Any, ctx: Any) -> dict[str, Any]:
    body = _projects.BriefBody(content=(proposal.payload or {}).get("content", ""))
    resp = await _invoke(
        _projects.put_brief(
            project_id=proposal.target_ref, body=body, request=ctx, user=_system_principal()
        )
    )
    return {"result_ref": _id_from(resp, "artifact_id", "id")}


# ── HAI-38 — prd.generate ────────────────────────────────────────────────────

async def prd_generate(proposal: Any, ctx: Any) -> dict[str, Any]:
    payload = _payload_for(_projects.PRDGenerateBody, proposal.payload)
    body = _projects.PRDGenerateBody(**payload) if payload else None
    resp = await _invoke(
        _projects.generate_prd(
            project_id=proposal.target_ref, request=ctx, body=body, user=_system_principal()
        )
    )
    return {"result_ref": _id_from(resp, "artifact_id", "document_id", "id", "version")}


# ── HAI-39 — apispec.generate ────────────────────────────────────────────────

async def apispec_generate(proposal: Any, ctx: Any) -> dict[str, Any]:
    payload = _payload_for(_projects.APISpecGenerateBody, proposal.payload)
    body = _projects.APISpecGenerateBody(**payload) if payload else None
    resp = await _invoke(
        _projects.generate_api_spec(
            project_id=proposal.target_ref, request=ctx, body=body, user=_system_principal()
        )
    )
    return {"result_ref": _id_from(resp, "artifact_id", "document_id", "id", "version")}


# ── HAI-40 — build-plan family (epics / features / tasks / buildplan) ────────
# target_ref is the project id for all of these (consistent with the HAI-59 target
# validator); sub-ids like epic_id ride in the payload.

async def epics_generate(proposal: Any, ctx: Any) -> dict[str, Any]:
    resp = await _invoke(
        _projects.generate_epics(
            project_id=proposal.target_ref, request=ctx,
            body=dict(proposal.payload or {}), user=_system_principal(),
        )
    )
    return {"result_ref": _id_from(resp, "artifact_id", "id") or proposal.target_ref}


async def features_generate(proposal: Any, ctx: Any) -> dict[str, Any]:
    payload = proposal.payload or {}
    resp = await _invoke(
        _projects.generate_features(
            project_id=proposal.target_ref, epic_id=payload.get("epic_id"),
            request=ctx, body=dict(payload), user=_system_principal(),
        )
    )
    return {"result_ref": _id_from(resp, "artifact_id", "id") or payload.get("epic_id")}


async def tasks_generate(proposal: Any, ctx: Any) -> dict[str, Any]:
    payload = _payload_for(_projects.TasksGenerateBody, proposal.payload)
    body = _projects.TasksGenerateBody(**payload) if payload else None
    resp = await _invoke(
        _projects.generate_tasks(
            project_id=proposal.target_ref, request=ctx, body=body, user=_system_principal()
        )
    )
    return {"result_ref": _id_from(resp, "artifact_id", "id", "version") or proposal.target_ref}


async def buildplan_generate(proposal: Any, ctx: Any) -> dict[str, Any]:
    resp = await _invoke(
        _projects.generate_build_plan(
            project_id=proposal.target_ref, request=ctx,
            body=dict(proposal.payload or {}), user=_system_principal(),
        )
    )
    return {"result_ref": _id_from(resp, "id", "summary") or proposal.target_ref}


# ── HAI-41 — task.dispatch ───────────────────────────────────────────────────

async def task_dispatch(proposal: Any, ctx: Any) -> dict[str, Any]:
    """Dispatch one or more project tasks (payload: project_id + task_ids/task_id).
    Mirrors auto_dispatch (BPD-24): orchestrator.submit per task, then mark it
    DISPATCHED. This is the handler HAI-46 routes governed auto-dispatch through."""
    state = ctx.app.state.state_store
    orchestrator = ctx.app.state.orchestrator
    payload = proposal.payload or {}
    project_id = proposal.target_ref or payload.get("project_id")
    task_ids = payload.get("task_ids") or ([payload["task_id"]] if payload.get("task_id") else [])

    dispatched: list[str] = []
    for tid in task_ids:
        task = await state.get_task(tid)
        if task is None:
            continue
        new_req = await orchestrator.submit(
            description=task.description or task.title,
            task_type=getattr(task, "task_type", "feature_request"),
            priority=getattr(task, "priority", "medium"),
            created_by="proposal:task.dispatch",
            project_id=project_id,
            source_task_id=task.task_id,
        )
        await state.set_task_status(tid, TaskStatus.DISPATCHED, request_id=new_req.request_id)
        dispatched.append(new_req.request_id)

    if not dispatched:
        raise RuntimeError("task.dispatch: no dispatchable task resolved from payload")
    return {"result_ref": ",".join(dispatched)}


# ── HAI-42 — deploy / rollback (admin scope) ─────────────────────────────────

async def ops_deploy(proposal: Any, ctx: Any) -> dict[str, Any]:
    """Deploy a project (target_ref = project id) via the existing 202 deploy route;
    the host supervisor picks it up."""
    await _invoke(
        _projects.deploy_project(
            project_id=proposal.target_ref, request=ctx, user=_system_principal()
        )
    )
    return {"result_ref": proposal.target_ref}


async def ops_rollback(proposal: Any, ctx: Any) -> dict[str, Any]:
    """Enqueue a RollbackRequest row for the host supervisor (there is no /rollback
    REST endpoint — AET-29 hand-off). Idempotent: if a rollback is already in flight
    for the env, return that one instead of queuing a duplicate. HAI-47 routes
    governed auto-rollback through here."""
    state = ctx.app.state.state_store
    payload = proposal.payload or {}
    env = payload.get("env")
    if not env:
        raise RuntimeError("rollback: 'env' is required in payload")

    existing = await state.get_in_flight_rollback_for_env(env)
    if existing is not None:
        return {"result_ref": existing.request_id}

    req = RollbackRequest(
        request_id=f"rb-{uuid.uuid4().hex[:12]}",
        deploy_id=payload.get("deploy_id", ""),
        env=env,
        reason=payload.get("reason", "operator-confirmed rollback proposal"),
    )
    await state.insert_rollback_request(req)
    return {"result_ref": req.request_id}


# ── HAI-33/34/35 — request.submit (service submits forced through the gate) ──

async def request_submit(proposal: Any, ctx: Any) -> dict[str, Any]:
    """Submit a Request from a CONFIRMED request.submit proposal. A service token
    can't POST /requests directly (the write-block forces it to propose, HAI-35);
    this is the execution side. The proposed project_id + rationale ride in the
    payload (HAI-33); a missing project_id falls through to Unassigned via
    orchestrator.submit's default, so the operator consciously confirmed an
    Unassigned submit (HAI-34). Human dashboard submits are unchanged (still direct).
    """
    orchestrator = ctx.app.state.orchestrator
    payload = proposal.payload or {}
    description = (payload.get("description") or "").strip()
    if not description:
        raise RuntimeError("request.submit: 'description' is required in payload")

    req = await orchestrator.submit(
        description=description,
        task_type=payload.get("task_type", "feature_request"),
        priority=payload.get("priority", "medium"),
        created_by=proposal.proposed_by or "proposal:request.submit",
        project_id=payload.get("project_id") or proposal.target_ref,  # None → Unassigned
    )
    return {"result_ref": req.request_id}


# ── registration ─────────────────────────────────────────────────────────────

# action_type -> handler. Only the P3 actions with a real handler today; the rest
# stay gated-without-handler (dispatcher fails them cleanly) until their task lands.
_HANDLERS = {
    "project.create": project_create,
    "project.brief.set": project_set_brief,
    "prd.generate": prd_generate,
    "apispec.generate": apispec_generate,
    "epics.generate": epics_generate,
    "features.generate": features_generate,
    "tasks.generate": tasks_generate,
    "buildplan.generate": buildplan_generate,
    "task.dispatch": task_dispatch,
    "deploy": ops_deploy,
    "rollback": ops_rollback,
    "request.submit": request_submit,
}


def register_all(registry: Any) -> None:
    """Register every available P3 handler into the proposal action registry."""
    for action_type, handler in _HANDLERS.items():
        registry.register(action_type, handler)
    logger.info("proposal_handlers_registered", count=len(_HANDLERS), actions=sorted(_HANDLERS))
