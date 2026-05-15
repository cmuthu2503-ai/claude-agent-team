"""Release/deployment endpoints — canonical shape across both backing tables.

The codebase has two deployment tables for historical reasons:

- ``deployments`` — the legacy Level-2 table written by older flows. Columns
  include ``deploy_id``, ``environment``, ``git_sha``, ``status``,
  ``deployed_at``, ``verified_at``, ``rolled_back_at``.
- ``deployment_states`` — the newer Level-3 state-machine table written by the
  supervisor. Columns include ``deployment_id``, ``commit_sha``,
  ``current_step``, ``step_history``, ``files_committed``, ``started_at``,
  ``completed_at``, ``error_message``, ``rollback_sha``.

Both feed the same UI. This module hides the difference: every list and detail
response is the canonical ``Release`` dict documented at ``_CANONICAL_DOC``.
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.auth.service import get_current_user, require_role

router = APIRouter(prefix="/api/v1/releases", tags=["releases"])


# ────────────────────────────────────────────────────────────────────────
# Canonical Release shape
#
# {
#     "deploy_id":       str,         unique identifier
#     "request_id":      str,         REQ-XXX that produced this release
#     "commit_sha":      str,         short SHA of the deployed commit
#     "environment":     str,         "staging" | "production" | "demo" | "pending"
#     "status":          str,         "deploying" | "verified" | "active"
#                                     | "failed" | "rolled_back" | "rolling_back"
#     "started_at":      str,         ISO timestamp
#     "completed_at":    str | None,
#     "error_message":   str | None,
#
#     # Detail-only extras (populated for state-machine rows; empty for legacy):
#     "current_step":    str | None,  raw state-machine step
#     "step_history":    list[dict],  per-step audit trail
#     "files_committed": list[str],   repo-relative file paths
#     "rollback_sha":    str,         parent commit SHA, for rollback
# }
# ────────────────────────────────────────────────────────────────────────


# Map a state-machine `current_step` value to the canonical (environment, status)
# pair. Order is intentional: when a deployment fails or rolls back we report
# its FAILURE state, not the last successful environment, because that's the
# more actionable signal for the user.
_STEP_TO_STATUS: dict[str, str] = {
    "code_committed":    "deploying",
    "building":          "deploying",
    "staging_deploying": "deploying",
    "staging_healthy":   "deploying",   # staging passed but prod is still pending
    "prod_deploying":    "deploying",
    "prod_healthy":      "verified",
    "completed":         "verified",
    "failed":            "failed",
    "rolling_back":      "rolling_back",
    "rolled_back":       "rolled_back",
}


def _environment_from_step(current_step: str, step_history: list[dict]) -> str:
    """Derive the canonical environment label from the state-machine progress.

    Walks ``step_history`` backwards to find the most recent environment the
    deployment actually touched, so a failed prod rollout still reports
    ``production`` (not ``pending``) — which is what a user wants to see.
    """
    if current_step in {"prod_deploying", "prod_healthy", "completed"}:
        return "production"
    if current_step in {"staging_deploying", "staging_healthy"}:
        return "staging"

    # Failed / rolling_back / rolled_back / building / code_committed:
    # look at history to find the highest environment we got to.
    saw_prod = False
    saw_staging = False
    for entry in step_history:
        step = entry.get("step", "")
        if "prod_" in step:
            saw_prod = True
        elif "staging_" in step:
            saw_staging = True
    if saw_prod:
        return "production"
    if saw_staging:
        return "staging"
    return "pending"


def _row_to_canonical(row: Any) -> dict[str, Any]:
    """Normalize a `deployment_states` row (aiosqlite.Row) into the canonical shape."""
    step_history_raw = row["step_history"] or "[]"
    files_committed_raw = row["files_committed"] or "[]"
    try:
        step_history = json.loads(step_history_raw)
    except json.JSONDecodeError:
        step_history = []
    try:
        files_committed = json.loads(files_committed_raw)
    except json.JSONDecodeError:
        files_committed = []

    current_step = row["current_step"] or ""
    status = _STEP_TO_STATUS.get(current_step, "deploying")
    environment = _environment_from_step(current_step, step_history)

    return {
        "deploy_id":       row["deployment_id"],
        "request_id":      row["request_id"],
        "commit_sha":      row["commit_sha"] or "",
        "environment":     environment,
        "status":          status,
        "started_at":      row["started_at"],
        "completed_at":    row["completed_at"],
        "error_message":   row["error_message"],
        "current_step":    current_step or None,
        "step_history":    step_history,
        "files_committed": files_committed,
        "rollback_sha":    "",  # not in deployment_states; legacy column on the supervisor side
    }


def _legacy_to_canonical(d: Any) -> dict[str, Any]:
    """Normalize a `deployments` (Deployment model) row into the canonical shape."""
    # The legacy `status` enum (deploying|active|verified|rolled_back|failed) is
    # already canonical-compatible — pass through.
    return {
        "deploy_id":       d.deploy_id,
        "request_id":      d.request_id,
        "commit_sha":      d.git_sha,
        "environment":     d.environment,
        "status":          d.status,
        "started_at":      d.deployed_at.isoformat() if d.deployed_at else None,
        "completed_at":    d.verified_at.isoformat() if d.verified_at else None,
        "error_message":   None,
        "current_step":    None,
        "step_history":    [],
        "files_committed": [],
        "rollback_sha":    "",
    }


def _sort_key(rel: dict[str, Any]) -> str:
    """Sort releases by start time, newest first. Missing timestamps sort last."""
    return rel.get("started_at") or ""


@router.get("")
async def list_releases(
    request: Request,
    limit: int = 20,
    user: dict = Depends(get_current_user),
):
    """Return the most recent releases, merged from both tables, sorted newest-first."""
    state = request.app.state.state_store

    # Pull state-machine rows directly (the StateStore doesn't expose a list helper for these)
    db = await state._get_db()
    async with db.execute(
        "SELECT * FROM deployment_states ORDER BY started_at DESC LIMIT ?", (limit,),
    ) as cursor:
        sm_rows = await cursor.fetchall()
    releases = [_row_to_canonical(r) for r in sm_rows]

    # Merge in legacy deployments
    legacy = await state.list_deployments(limit=limit)
    releases.extend(_legacy_to_canonical(d) for d in legacy)

    # Sort by start time DESC, cap at the requested limit
    releases.sort(key=_sort_key, reverse=True)
    releases = releases[:limit]

    return {"data": releases, "meta": None, "error": None}


@router.get("/{deploy_id}")
async def get_release(
    deploy_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Return the canonical Release detail.

    Looks in both tables; state-machine wins on collision.
    """
    state = request.app.state.state_store

    # Try the state-machine table first (it has the richer detail)
    sm = await state.get_deployment_state(deploy_id)
    if sm:
        return {
            "data": {
                "deploy_id":       sm.deployment_id,
                "request_id":      sm.request_id,
                "commit_sha":      sm.commit_sha,
                "environment":     _environment_from_step(sm.current_step, sm.step_history),
                "status":          _STEP_TO_STATUS.get(sm.current_step, "deploying"),
                "started_at":      sm.started_at.isoformat(),
                "completed_at":    sm.completed_at.isoformat() if sm.completed_at else None,
                "error_message":   sm.error_message,
                "current_step":    sm.current_step,
                "step_history":    sm.step_history,
                "files_committed": sm.files_committed,
                "rollback_sha":    sm.rollback_sha,
            },
            "meta": None,
            "error": None,
        }

    # Fall through to the legacy table
    legacy = await state.get_deployment(deploy_id)
    if legacy:
        return {"data": _legacy_to_canonical(legacy), "meta": None, "error": None}

    raise HTTPException(status_code=404, detail="Deployment not found")


@router.post("/{deploy_id}/rollback")
async def rollback_deployment(
    deploy_id: str,
    request: Request,
    user: dict = Depends(require_role("admin")),
):
    """Trigger a rollback. Currently a stub — supervisor handles the real rollback path."""
    state = request.app.state.state_store
    dep = await state.get_deployment_state(deploy_id)
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return {
        "data": {"deploy_id": deploy_id, "status": "rollback_initiated"},
        "meta": None,
        "error": None,
    }
