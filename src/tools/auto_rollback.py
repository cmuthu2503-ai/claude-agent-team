"""Auto-rollback tool — AET-29.

Queues a rollback request for the supervisor to execute, after
verifying that an SLO breach has been SUSTAINED for at least
``sustain_minutes`` and that no rollback is already in flight for the
target env. The tool itself doesn't run ``git revert`` — it can't
(the backend container has no host-side docker / git access). It
writes a ``rollback_requests`` row; the supervisor host process polls
that table and performs the actual revert + redeploy.

Why this split exists: keeping the destructive action on the host
preserves the L-class boundary (web tier never touches host shell)
AND lets the supervisor reuse its existing
``_rollback_failed_deploy`` flow rather than duplicating it inside
the container.

Sustained-breach check
----------------------
A "sustained breach" means: looking at recent deploy_health probes
for the env, the AVAILABILITY metric must have been below the SLO
target for at least ``sustain_minutes`` of continuous time, measured
from the most recent probe backwards. The default 5min matches the
``error_rate_5m`` SLO sub-window so a transient blip can't trigger
a panic rollback.

This logic is intentionally simple — slo_check (AET-28) does the
rich threshold work; auto_rollback is the dumb-but-safe trigger that
sits on top.

Idempotency
-----------
Before queuing, the tool calls ``state.get_in_flight_rollback_for_env``
and returns ``status='already_in_flight'`` if a pending or in_flight
request already exists. No new row inserted. The supervisor can
crash-recover by marking abandoned in_flight rows as failed; that's
out of scope for this AET (lives with the supervisor side).

Return shape
------------
    {
      "status":      "queued" | "already_in_flight" | "breach_not_sustained"
                     | "insufficient_data" | "error",
      "request_id":  str | None,
      "env":         str,
      "deploy_id":   str | None,
      "reason":      str,
      "summary":     str,
    }
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from typing import Any

import structlog
import yaml

from src.tools.slo_check import (
    load_slo_config,
    resolve_env_slos,
)

logger = structlog.get_logger()


# Sustain window (minutes) — how long the breach must persist before
# we'll queue a rollback. 5min matches the slo_check error_rate_5m
# window so the two tools reason about the same span. Tightenable via
# env for environments that prefer faster response at the cost of
# more false-positive rollbacks.
SUSTAIN_MINUTES = int(os.getenv("AUTO_ROLLBACK_SUSTAIN_MINUTES", "5"))

# Minimum samples in the sustain window before we'll act. Mirrors
# anomaly_detect's MIN_BASELINE_SAMPLES philosophy: never roll back
# off a cold start.
MIN_SAMPLES_IN_WINDOW = int(os.getenv("AUTO_ROLLBACK_MIN_SAMPLES", "5"))


def _availability_target_for(env: str) -> float:
    """Resolve env's availability target from slo.yaml. Falls back to
    0.99 if the SLO config is missing/broken (matches the slo_check
    default). Kept local so auto_rollback doesn't need a separate
    config import."""
    try:
        cfg = resolve_env_slos(load_slo_config(), env)
        avail = (cfg.get("slos") or {}).get("availability") or {}
        target = float(avail.get("target", 0.99))
        return target
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "auto_rollback_slo_load_failed", env=env, error=str(e),
        )
        return 0.99


def _is_breach_sustained(
    probes: list[Any], target: float, sustain_minutes: int,
) -> tuple[bool, dict[str, Any]]:
    """Walk the recent probes (newest-first ordering assumed) and
    decide whether the env's availability has been below *target*
    for at least *sustain_minutes* of continuous time.

    Algorithm:
      - Look at probes in the last sustain_minutes window.
      - Compute availability = fraction of probes with 200 ≤ status < 300.
      - If availability < target AND samples ≥ MIN_SAMPLES_IN_WINDOW,
        the breach is sustained.

    Simpler than per-probe consecutive-failure counting, and matches
    how slo_check (AET-28) computes its BREACH verdict — both tools
    agree on what "breach" means."""
    if not probes:
        return False, {
            "reason": "no probes in sustain window",
            "samples": 0,
            "availability": None,
            "target": target,
        }

    cutoff = datetime.utcnow() - timedelta(minutes=sustain_minutes)
    in_window = [p for p in probes if p.recorded_at >= cutoff]
    samples = len(in_window)
    if samples < MIN_SAMPLES_IN_WINDOW:
        return False, {
            "reason": f"only {samples} probes in {sustain_minutes}m window; need ≥{MIN_SAMPLES_IN_WINDOW}",
            "samples": samples,
            "availability": None,
            "target": target,
        }
    success = sum(
        1 for p in in_window
        if p.http_status and 200 <= p.http_status < 300
    )
    availability = success / samples
    sustained = availability < target
    return sustained, {
        "reason": (
            f"availability {availability:.3f} < target {target:.3f} "
            f"over {sustain_minutes}m / {samples} probes"
            if sustained
            else f"availability {availability:.3f} ≥ target {target:.3f}"
        ),
        "samples": samples,
        "availability": availability,
        "target": target,
    }


class AutoRollbackTool:
    """Queue a rollback for the supervisor; idempotent per env."""

    def schema(self) -> dict[str, Any]:
        return {
            "name": "auto_rollback",
            "description": (
                "Queue a rollback request for the supervisor when an "
                "SLO breach has been SUSTAINED for at least "
                f"{SUSTAIN_MINUTES} minutes (env-tunable via "
                "AUTO_ROLLBACK_SUSTAIN_MINUTES). Idempotent — returns "
                "status='already_in_flight' if a pending or "
                "in_flight rollback already exists for the env. The "
                "tool only WRITES the rollback request; the actual "
                "git revert + redeploy runs on the supervisor host. "
                "Use AFTER slo_check returns BREACH; never call it "
                "blindly on a single bad probe."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "env": {
                        "type": "string",
                        "description": (
                            "Environment to roll back: 'development', "
                            "'staging', 'production', or 'demo'."
                        ),
                    },
                    "deploy_id": {
                        "type": "string",
                        "description": (
                            "Optional — the specific deploy to revert. "
                            "Default: most recent completed deploy "
                            "associated with the env."
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "Caller-supplied rationale (typically the "
                            "slo_check.summary string). Persisted on "
                            "the rollback_requests row for audit."
                        ),
                    },
                    "sustain_minutes": {
                        "type": "integer",
                        "description": (
                            f"Override the default {SUSTAIN_MINUTES}min "
                            "sustain window. Use sparingly — shorter "
                            "windows raise false-positive rollback rate."
                        ),
                    },
                },
                "required": ["env"],
            },
        }

    def __init__(self, state: Any = None) -> None:
        self.state = state

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        env = (params.get("env") or "").strip()
        if not env:
            return {
                "status": "error",
                "request_id": None, "env": "", "deploy_id": None,
                "reason": "env required",
                "summary": "auto_rollback: env required.",
            }
        if self.state is None:
            return {
                "status": "error",
                "request_id": None, "env": env, "deploy_id": None,
                "reason": "no state store wired",
                "summary": "auto_rollback: state store unavailable.",
            }

        sustain = max(1, int(
            params.get("sustain_minutes") or SUSTAIN_MINUTES
        ))
        caller_reason = (params.get("reason") or "").strip()

        # ── 1. Idempotency ────────────────────────────────────────────
        existing = await self.state.get_in_flight_rollback_for_env(env)
        if existing is not None:
            return {
                "status": "already_in_flight",
                "request_id": existing.request_id,
                "env": env,
                "deploy_id": existing.deploy_id,
                "reason": existing.reason,
                "summary": (
                    f"auto_rollback: skipped — request "
                    f"{existing.request_id} for env={env} is already "
                    f"{existing.status} (queued at "
                    f"{existing.requested_at.isoformat()})."
                ),
            }

        # ── 2. Sustained-breach verification ─────────────────────────
        target = _availability_target_for(env)
        probes = await self.state.list_deploy_health_probes(
            env=env, since=datetime.utcnow() - timedelta(minutes=sustain),
            limit=5000,
        )
        sustained, detail = _is_breach_sustained(probes, target, sustain)
        if not sustained:
            verdict = (
                "insufficient_data"
                if detail.get("availability") is None
                else "breach_not_sustained"
            )
            return {
                "status": verdict,
                "request_id": None,
                "env": env,
                "deploy_id": None,
                "reason": detail["reason"],
                "summary": (
                    f"auto_rollback: NOT queued for env={env} — "
                    f"{detail['reason']}."
                ),
                "detail": detail,
            }

        # ── 3. Pick a deploy_id ──────────────────────────────────────
        # Caller may have specified one; otherwise use the latest
        # probe's deploy_id as a stand-in. The supervisor will
        # canonicalise to the actual deploy it's reverting.
        deploy_id = (
            params.get("deploy_id")
            or (probes[0].deploy_id if probes else f"platform-{env}")
        )

        # ── 4. Queue the request ─────────────────────────────────────
        from src.models.base import RollbackRequest

        req = RollbackRequest(
            request_id=f"RB-{env}-{uuid.uuid4().hex[:8].upper()}",
            deploy_id=deploy_id,
            env=env,
            status="pending",
            reason=caller_reason or detail["reason"],
        )
        await self.state.insert_rollback_request(req)

        logger.info(
            "auto_rollback_queued",
            request_id=req.request_id,
            env=env, deploy_id=deploy_id,
            availability=detail["availability"],
            target=detail["target"],
        )

        return {
            "status": "queued",
            "request_id": req.request_id,
            "env": env,
            "deploy_id": deploy_id,
            "reason": req.reason,
            "summary": (
                f"auto_rollback: QUEUED — request {req.request_id} for "
                f"env={env} deploy={deploy_id}; supervisor will pick "
                f"up on the next tick. {detail['reason']}."
            ),
            "detail": detail,
        }
