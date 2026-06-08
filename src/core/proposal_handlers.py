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

from typing import Any

import structlog
from fastapi import HTTPException

from src.api.routes import projects as _projects

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
}


def register_all(registry: Any) -> None:
    """Register every available P3 handler into the proposal action registry."""
    for action_type, handler in _HANDLERS.items():
        registry.register(action_type, handler)
    logger.info("proposal_handlers_registered", count=len(_HANDLERS), actions=sorted(_HANDLERS))
