"""Ops-heal event handler + periodic anomaly sweeper — AET-31.

Two server-side pieces that close the AE-1 ops loop:

  1. ``make_anomaly_sweeper(state, events, …)`` — a background
     asyncio task that wakes every ``ANOMALY_SWEEP_INTERVAL_S`` (90s
     by default; ~1.5× the supervisor probe cadence so we never miss
     a window), runs ``anomaly_detect`` against every env we have
     recent probes for, and emits
     ``deploy_health.anomaly_detected`` events when the verdict is
     ``ANOMALY``. This is the SOURCE of the events the ops_heal
     handler consumes.

     Why backend-side and not in the supervisor: the supervisor is a
     sync sqlite/host process with no EventEmitter. The backend
     already has the event bus, the WebSocket fanout, and tool
     instances — putting the sweep here avoids inventing a
     supervisor→backend event channel.

  2. ``make_ops_heal_handler(state, agent_executor, events)`` — an
     EventEmitter handler that listens for
     ``deploy_health.anomaly_detected`` and runs the decision
     algorithm laid out in AET-30's prompt EXECUTION FLOW:

         slo_check  →  PASS              : drop (no UI noise)
                      DEGRADED/INSUFFICIENT_DATA : emit ops.alert.fired
                      BREACH            : auto_rollback;
                                           if queued    → ops.rollback.triggered
                                           if in_flight → ops.alert.fired (no dup)

     We run the decision OURSELVES, not via the LLM, for two
     reasons: (a) it's deterministic and easier to test, (b) it
     completes in <100ms vs ~30s for an LLM call, which matters when
     a real outage is happening. The agent's LLM-driven path is for
     post-deploy NORMAL flow; the event-driven path is for emergencies.

Per-process inflight guard prevents the handler from re-entering for
the same env while it's still running — back-to-back anomaly events
during a sustained incident are common and should NOT all fire
auto_rollback (the tool's own idempotency would catch it, but the
guard avoids the round-trip).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

import structlog

from src.core.events import (
    DEPLOY_HEALTH_ANOMALY_DETECTED,
    OPS_ALERT_FIRED,
    OPS_ROLLBACK_TRIGGERED,
    EventEmitter,
)
from src.core.in_process_gate import submit_gated_action

logger = structlog.get_logger()


# Sweep cadence — 90s ≈ 1.5× the supervisor's 60s probe interval, so
# we always have at least one new sample per env between sweeps.
ANOMALY_SWEEP_INTERVAL_S = int(os.getenv("ANOMALY_SWEEP_INTERVAL_S", "90"))

# Envs to sweep. Mirrors the supervisor's _ENV_PORT_MAP — keep in
# lockstep (L23). If an env has no probes the anomaly tool returns
# INSUFFICIENT_DATA, no event fires, no harm done.
_SWEEP_ENVS = ("development", "staging", "production", "demo")

# Per-process inflight guard keyed by env. Drops an anomaly event
# whose handler is already running for that env.
_INFLIGHT: set[str] = set()


# ── Periodic anomaly sweeper (the EVENT SOURCE) ─────────────────────────


def make_anomaly_sweeper(
    state: Any, events: EventEmitter, agent_executor: Any,
) -> Any:
    """Returns an async background task callable. The lifespan in
    src/main.py wraps it in ``asyncio.create_task`` so it runs for
    the life of the process.

    Pulls the ``anomaly_detect`` tool out of the executor's registry
    rather than instantiating its own copy — keeps a single source of
    truth for the tool's state-store wiring and tuning constants.
    """

    async def _sweep_once() -> None:
        tool = None
        if agent_executor is not None and hasattr(agent_executor, "tool_registry"):
            try:
                tool = agent_executor.tool_registry.get_implementation("anomaly_detect")
            except Exception:
                tool = None
        if tool is None:
            return  # mock-mode boot path — nothing to do

        for env in _SWEEP_ENVS:
            try:
                result = await tool.execute({"env": env})
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "anomaly_sweep_tool_failed", env=env, error=str(e),
                )
                continue
            if result.get("verdict") != "ANOMALY":
                continue
            await events.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {
                "env": env,
                "alerts": result.get("alerts", []),
                "baseline_samples": result.get("baseline_samples"),
                "current_samples": result.get("current_samples"),
                "summary": result.get("summary"),
            })
            logger.info(
                "anomaly_sweep_emitted",
                env=env,
                alert_count=len(result.get("alerts", [])),
            )

    async def _loop() -> None:
        # First sweep after a short delay so the supervisor has time
        # to write at least one probe per env on a cold start.
        await asyncio.sleep(min(30, ANOMALY_SWEEP_INTERVAL_S))
        while True:
            try:
                await _sweep_once()
            except Exception as e:  # noqa: BLE001
                logger.warning("anomaly_sweep_loop_error", error=str(e))
            await asyncio.sleep(ANOMALY_SWEEP_INTERVAL_S)

    return _loop


# ── Event handler (the DECISION) ────────────────────────────────────────


def make_ops_heal_handler(
    state: Any, agent_executor: Any, events: EventEmitter,
    governed: Callable[[], bool] | None = None,
) -> Any:
    """Returns the async EventHandler bound to (state, executor, events).
    Listens for ``deploy_health.anomaly_detected`` ONLY — every other
    event type is dropped cheaply.

    ``governed`` is a live predicate (HAI-47): when True, a BREACH proposes a
    rollback (a human confirms; the HAI-42 handler enqueues it) instead of rolling
    back automatically. Alert-only verdicts (DEGRADED / INSUFFICIENT_DATA) still
    auto-fire. Defaults to legacy (never governed), so existing call sites/tests
    are unchanged.

    Decision algorithm (deterministic, no LLM):
        1. Pull slo_check + auto_rollback tools from the executor.
        2. Run slo_check(env) for the env in the event payload.
        3. Route by verdict (see module docstring for the table).

    On any failure, log and return — never raise into the event
    loop. The handler must never break event broadcast.
    """
    _is_governed: Callable[[], bool] = governed if callable(governed) else (lambda: False)

    async def _handler(event_type: str, data: dict[str, Any]) -> None:
        if event_type != DEPLOY_HEALTH_ANOMALY_DETECTED:
            return
        env = (data.get("env") or "").strip()
        if not env:
            return
        if env in _INFLIGHT:
            logger.debug(
                "ops_heal_handler_skipped_inflight",
                env=env, hint="another anomaly already routing",
            )
            return

        async def _run() -> None:
            _INFLIGHT.add(env)
            try:
                # Tool registry lookup — defer until handler fires so
                # we pick up registry changes (e.g. a tool re-registered
                # during dev hot-reload).
                slo_tool = agent_executor.tool_registry.get_implementation(
                    "slo_check",
                )
                rollback_tool = agent_executor.tool_registry.get_implementation(
                    "auto_rollback",
                )

                slo = await slo_tool.execute({"env": env})
                slo_verdict = slo.get("verdict", "ERROR")

                # ── PASS → drop, no UI noise ───────────────────────
                if slo_verdict == "PASS":
                    logger.info(
                        "ops_heal_anomaly_dismissed",
                        env=env, reason="slo_check verdict=PASS",
                    )
                    return

                # ── DEGRADED / INSUFFICIENT_DATA → alert only ──────
                if slo_verdict in ("DEGRADED", "INSUFFICIENT_DATA"):
                    await events.emit(OPS_ALERT_FIRED, {
                        "env": env,
                        "severity": slo_verdict.lower(),
                        "slo_verdict": slo_verdict,
                        "slo_summary": slo.get("summary"),
                        "anomaly_alerts": data.get("alerts", []),
                    })
                    logger.info(
                        "ops_alert_fired", env=env, slo_verdict=slo_verdict,
                    )
                    return

                # ── BREACH → roll back, GATED by governance (HAI-47) ──
                if slo_verdict == "BREACH":
                    async def _legacy_rollback() -> Any:
                        """The pre-integration behavior: roll back NOW via the tool."""
                        rb = await rollback_tool.execute({
                            "env": env,
                            "reason": slo.get("summary", "SLO breach"),
                        })
                        rb_status = rb.get("status")
                        if rb_status == "queued":
                            await events.emit(OPS_ROLLBACK_TRIGGERED, {
                                "env": env,
                                "request_id": rb.get("request_id"),
                                "deploy_id": rb.get("deploy_id"),
                                "reason": rb.get("reason"),
                                "slo_summary": slo.get("summary"),
                            })
                            logger.info(
                                "ops_rollback_triggered",
                                env=env, request_id=rb.get("request_id"),
                            )
                        else:
                            # already_in_flight / breach_not_sustained / error —
                            # surface as alert, don't claim a new rollback fired.
                            await events.emit(OPS_ALERT_FIRED, {
                                "env": env,
                                "severity": "breach",
                                "slo_verdict": "BREACH",
                                "slo_summary": slo.get("summary"),
                                "rollback_status": rb_status,
                                "rollback_request_id": rb.get("request_id"),
                                "anomaly_alerts": data.get("alerts", []),
                            })
                            logger.info(
                                "ops_alert_fired_no_rollback",
                                env=env, rollback_status=rb_status,
                            )
                        return rb

                    # Governed → propose the rollback (human confirms; the HAI-42
                    # handler enqueues it) + a breach alert. Legacy → roll back now.
                    # Idempotent per env so repeated breaches don't stack proposals.
                    outcome = await submit_gated_action(
                        state, events,
                        governed=_is_governed(),
                        action_type="rollback",
                        proposed_by="system:ops-heal",
                        target_ref=f"env:{env}",
                        payload={
                            "env": env,
                            "deploy_id": data.get("deploy_id", ""),
                            "reason": slo.get("summary", "SLO breach"),
                        },
                        idempotency_key=f"autorollback:{env}",
                        execute=_legacy_rollback,
                    )
                    if outcome.gated:
                        await events.emit(OPS_ALERT_FIRED, {
                            "env": env,
                            "severity": "breach",
                            "slo_verdict": "BREACH",
                            "slo_summary": slo.get("summary"),
                            "rollback_status": "proposed",
                            "rollback_proposal_id": outcome.proposal_id,
                            "anomaly_alerts": data.get("alerts", []),
                        })
                        logger.info(
                            "ops_rollback_proposed",
                            env=env, proposal_id=outcome.proposal_id,
                        )
                    return

                # ERROR / unknown — log and bail. Don't emit a fake
                # alert; that would pollute the UI with noise.
                logger.warning(
                    "ops_heal_unexpected_slo_verdict",
                    env=env, verdict=slo_verdict,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ops_heal_handler_failed", env=env, error=str(exc),
                )
            finally:
                _INFLIGHT.discard(env)

        # Fire-and-forget so the broadcast pipeline returns immediately.
        asyncio.create_task(_run())

    return _handler
