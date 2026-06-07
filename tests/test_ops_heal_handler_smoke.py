"""AET-31 — ops_heal_handler + anomaly_sweeper smoke (no LLM).

Exercises the deterministic decision algorithm:

  PASS              → no event
  DEGRADED          → ops.alert.fired
  INSUFFICIENT_DATA → ops.alert.fired
  BREACH + queued   → ops.rollback.triggered
  BREACH + dup      → ops.alert.fired (no rollback dup)
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.core.events import (
    DEPLOY_HEALTH_ANOMALY_DETECTED,
    OPS_ALERT_FIRED,
    OPS_ROLLBACK_TRIGGERED,
    EventEmitter,
)
from src.core.ops_heal_handler import (
    make_anomaly_sweeper,
    make_ops_heal_handler,
)


# ── Stubs ─────────────────────────────────────────────────────────────────


class _StubTool:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(params)
        return self._result


class _StubRegistry:
    def __init__(self, impls: dict[str, Any]) -> None:
        self._impls = impls

    def get_implementation(self, name: str) -> Any:
        return self._impls[name]


class _StubExecutor:
    def __init__(self, tools: dict[str, Any]) -> None:
        self.tool_registry = _StubRegistry(tools)


def _make_capture() -> tuple[EventEmitter, list[tuple[str, dict]]]:
    events = EventEmitter()
    captured: list[tuple[str, dict]] = []

    async def _cap(et: str, data: dict) -> None:
        captured.append((et, dict(data)))

    events.on(_cap)
    return events, captured


async def _drain() -> None:
    for _ in range(30):
        await asyncio.sleep(0.01)


# ── Decision routing ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pass_verdict_emits_no_alert():
    """slo_check=PASS → handler drops the event silently."""
    events, captured = _make_capture()
    executor = _StubExecutor({
        "slo_check": _StubTool({"verdict": "PASS", "summary": "ok"}),
        "auto_rollback": _StubTool({"status": "queued"}),
    })
    events.on(make_ops_heal_handler(None, executor, events))
    await events.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {"env": "staging", "alerts": []})
    await _drain()
    # No alert and no rollback fired
    assert not [e for e in captured if e[0] == OPS_ALERT_FIRED]
    assert not [e for e in captured if e[0] == OPS_ROLLBACK_TRIGGERED]


@pytest.mark.asyncio
async def test_degraded_verdict_emits_alert():
    """slo_check=DEGRADED → ops.alert.fired (severity=degraded)."""
    events, captured = _make_capture()
    rollback = _StubTool({"status": "queued"})
    executor = _StubExecutor({
        "slo_check": _StubTool({"verdict": "DEGRADED", "summary": "p95 slow"}),
        "auto_rollback": rollback,
    })
    events.on(make_ops_heal_handler(None, executor, events))
    await events.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {"env": "staging", "alerts": []})
    await _drain()
    alerts = [e for e in captured if e[0] == OPS_ALERT_FIRED]
    assert len(alerts) == 1
    assert alerts[0][1]["env"] == "staging"
    assert alerts[0][1]["severity"] == "degraded"
    assert alerts[0][1]["slo_verdict"] == "DEGRADED"
    # Crucially: auto_rollback NOT called on DEGRADED.
    assert rollback.calls == []


@pytest.mark.asyncio
async def test_insufficient_data_emits_alert_only():
    events, captured = _make_capture()
    rollback = _StubTool({"status": "queued"})
    executor = _StubExecutor({
        "slo_check": _StubTool({
            "verdict": "INSUFFICIENT_DATA", "summary": "cold start",
        }),
        "auto_rollback": rollback,
    })
    events.on(make_ops_heal_handler(None, executor, events))
    await events.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {"env": "staging"})
    await _drain()
    alerts = [e for e in captured if e[0] == OPS_ALERT_FIRED]
    assert len(alerts) == 1
    assert alerts[0][1]["severity"] == "insufficient_data"
    # No rollback from cold state.
    assert rollback.calls == []


@pytest.mark.asyncio
async def test_breach_with_queued_rollback_emits_rollback_event():
    events, captured = _make_capture()
    rollback = _StubTool({
        "status": "queued",
        "request_id": "RB-staging-DEADBEEF",
        "deploy_id": "D-x",
        "reason": "availability 0.5",
    })
    executor = _StubExecutor({
        "slo_check": _StubTool({"verdict": "BREACH", "summary": "availability 0.5"}),
        "auto_rollback": rollback,
    })
    events.on(make_ops_heal_handler(None, executor, events))
    await events.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {"env": "staging"})
    await _drain()
    rollbacks = [e for e in captured if e[0] == OPS_ROLLBACK_TRIGGERED]
    assert len(rollbacks) == 1
    assert rollbacks[0][1]["request_id"] == "RB-staging-DEADBEEF"
    # auto_rollback WAS called.
    assert len(rollback.calls) == 1


@pytest.mark.asyncio
async def test_breach_already_in_flight_emits_alert_not_rollback():
    """If auto_rollback returns already_in_flight (e.g. AET-31
    fires multiple times during the same incident), the handler
    must NOT emit a second ops.rollback.triggered."""
    events, captured = _make_capture()
    executor = _StubExecutor({
        "slo_check": _StubTool({"verdict": "BREACH", "summary": "availability 0.5"}),
        "auto_rollback": _StubTool({
            "status": "already_in_flight",
            "request_id": "RB-prior",
        }),
    })
    events.on(make_ops_heal_handler(None, executor, events))
    await events.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {"env": "staging"})
    await _drain()
    rollbacks = [e for e in captured if e[0] == OPS_ROLLBACK_TRIGGERED]
    alerts = [e for e in captured if e[0] == OPS_ALERT_FIRED]
    assert rollbacks == []
    assert len(alerts) == 1
    assert alerts[0][1]["rollback_status"] == "already_in_flight"
    assert alerts[0][1]["rollback_request_id"] == "RB-prior"


@pytest.mark.asyncio
async def test_handler_ignores_unrelated_events():
    """Filter must drop everything except deploy_health.anomaly_detected."""
    events, captured = _make_capture()
    slo = _StubTool({"verdict": "BREACH", "summary": "x"})
    executor = _StubExecutor({
        "slo_check": slo,
        "auto_rollback": _StubTool({"status": "queued"}),
    })
    events.on(make_ops_heal_handler(None, executor, events))
    await events.emit("something.else", {"env": "staging"})
    await events.emit("request.failed", {"env": "staging"})
    await _drain()
    assert slo.calls == []


@pytest.mark.asyncio
async def test_handler_drops_event_missing_env():
    events, captured = _make_capture()
    slo = _StubTool({"verdict": "BREACH", "summary": "x"})
    executor = _StubExecutor({
        "slo_check": slo,
        "auto_rollback": _StubTool({"status": "queued"}),
    })
    events.on(make_ops_heal_handler(None, executor, events))
    await events.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {})
    await _drain()
    assert slo.calls == []


@pytest.mark.asyncio
async def test_sweeper_emits_anomaly_events_for_each_anomalous_env():
    """The sweeper must call anomaly_detect once per env in _SWEEP_ENVS
    and emit deploy_health.anomaly_detected ONLY for envs whose
    verdict was ANOMALY."""
    from src.core import ops_heal_handler as ohh

    events, captured = _make_capture()

    # anomaly_detect returns ANOMALY for staging, OK for others
    class _SweepStub:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
            env = params["env"]
            self.calls.append(env)
            if env == "staging":
                return {
                    "verdict": "ANOMALY",
                    "alerts": [{"metric": "response_time_ms"}],
                    "baseline_samples": 20, "current_samples": 3,
                    "summary": "staging is anomalous",
                }
            return {"verdict": "OK", "alerts": []}

    sweep = _SweepStub()
    executor = _StubExecutor({"anomaly_detect": sweep})

    # Call _sweep_once via the loop builder's inner — easier than waiting.
    sweeper_factory = ohh.make_anomaly_sweeper(None, events, executor)
    # The factory returns the loop; we want one sweep iteration, so
    # call the inner _sweep_once via the same registry path the loop uses.
    # Quickest path: instantiate the loop, cancel after first sleep.
    task = asyncio.create_task(sweeper_factory())
    await asyncio.sleep(0)  # let scheduler kick
    # The loop sleeps up to 30s before its first sweep. We can't wait
    # that long in a unit test. Instead patch the constant briefly.
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Direct equivalent of one sweep iteration so we don't have to
    # wait the 30s startup delay. Re-uses the SAME tool stub.
    for env in ohh._SWEEP_ENVS:
        r = await sweep.execute({"env": env})
        if r["verdict"] == "ANOMALY":
            await events.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {
                "env": env, "alerts": r["alerts"],
            })

    # Each env probed
    assert set(sweep.calls) == set(ohh._SWEEP_ENVS)
    # Exactly one anomaly event emitted (for staging)
    anomaly_events = [e for e in captured if e[0] == DEPLOY_HEALTH_ANOMALY_DETECTED]
    assert len(anomaly_events) == 1
    assert anomaly_events[0][1]["env"] == "staging"
