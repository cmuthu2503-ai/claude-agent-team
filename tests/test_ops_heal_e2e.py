"""Phase AE-1 end-to-end smoke for the ops_heal pipeline (AET-32).

Pins the entire AE-1 incident-response chain end-to-end using REAL
tools + REAL state + REAL handler (no LLM, no host supervisor — those
two are exercised separately). The seven contract steps from the
AET-32 spec, mapped to what this test verifies:

  (a) supervisor probe captures the error_rate spike
      → simulated by inserting 5xx-heavy probes into deploy_health
        directly. The real supervisor's _probe_one_env writes the
        same shape (AET-24); see test_supervisor_probe_smoke in
        the supervisor side for that contract.

  (b) anomaly_detect flags >2σ deviation
      → ASSERTED — calling the real tool against the seeded data
        must return verdict=ANOMALY with response_time_ms in alerts.

  (c) ops_heal_agent fires
      → ASSERTED — the periodic sweep emits
        deploy_health.anomaly_detected and the registered handler
        runs the deterministic decision tree.

  (d) sustained breach triggers auto_rollback
      → ASSERTED — slo_check returns BREACH, auto_rollback returns
        'queued', a rollback_requests row is persisted, and
        ops.rollback.triggered fires.

  (e) supervisor reverts (mock-only here)
      → ASSERTED via the rollback_requests row transition:
        the test manually flips status pending → in_flight →
        completed to model what the supervisor host process does.
        The real supervisor consumes the queue in a follow-up
        AET that lives in supervisor/deploy_supervisor.py.

  (f) health probes recover
      → ASSERTED — after marking the rollback completed, we seed
        fresh healthy probes and re-run slo_check; verdict must
        flip back to PASS.

  (g) ops.alert.cleared event fires (IF defined)
      → SKIPPED for this iteration — the ops.alert.cleared event
        belongs to AE-1 follow-up work. The test asserts the
        rollback_requests row reached terminal status as the
        functional equivalent.

Crucially also pins the idempotency contract:
  - A second anomaly event during the in-flight rollback must NOT
    queue a duplicate rollback. The handler emits ops.alert.fired
    with rollback_status='already_in_flight' instead.

Run via:
  docker compose exec backend pytest tests/test_ops_heal_e2e.py -v
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest

from src.core.events import (
    DEPLOY_HEALTH_ANOMALY_DETECTED,
    OPS_ALERT_FIRED,
    OPS_ROLLBACK_TRIGGERED,
    EventEmitter,
)
from src.core.ops_heal_handler import make_ops_heal_handler
from src.models.base import DeployHealthProbe
from src.state.sqlite_store import SQLiteStateStore
from src.tools.anomaly_detect import AnomalyDetectTool
from src.tools.auto_rollback import (
    MIN_SAMPLES_IN_WINDOW,
    AutoRollbackTool,
)
from src.tools.slo_check import SloCheckTool


# ── Real tool registry + executor stub ────────────────────────────────────


class _RealToolRegistry:
    """Thin registry that returns the real tools wired to the live state."""

    def __init__(self, tools: dict[str, Any]) -> None:
        self._tools = tools

    def get_implementation(self, name: str) -> Any:
        return self._tools[name]


class _RealExecutor:
    def __init__(self, state: Any) -> None:
        self.tool_registry = _RealToolRegistry({
            "anomaly_detect": AnomalyDetectTool(state=state),
            "slo_check":      SloCheckTool(state=state),
            "auto_rollback":  AutoRollbackTool(state=state),
        })


# ── Probe seeding helpers ─────────────────────────────────────────────────


async def _seed_baseline(
    state: SQLiteStateStore, env: str,
    samples: int = 30, rt_ms: int = 100,
) -> None:
    """Seed a healthy baseline window OLDER than the current 5min so
    anomaly_detect's exclusion guard treats them as baseline-only."""
    now = datetime.utcnow()
    for i in range(samples):
        # Spread across the 10-60 minute window (older than current
        # 5min slot but inside anomaly_detect's 60min baseline).
        ago_s = 10 * 60 + i * 30  # starts at 10min back, every 30s
        await state.insert_deploy_health_probe(DeployHealthProbe(
            probe_id=f"P-base-{env}-{uuid.uuid4().hex[:6]}",
            deploy_id=f"D-{env}",
            env=env,
            recorded_at=now - timedelta(seconds=ago_s),
            response_time_ms=rt_ms,
            error_rate_5m=0.0,
            http_status=200,
        ))


async def _seed_5xx_storm(
    state: SQLiteStateStore, env: str,
    samples: int = MIN_SAMPLES_IN_WINDOW + 5,
    error_fraction: float = 0.8,
    rt_ms: int = 2000,
) -> None:
    """Seed the CURRENT 5min window with a 5xx storm — high error
    rate AND inflated response time, both of which trip anomaly_detect
    and slo_check."""
    now = datetime.utcnow()
    err_count = int(samples * error_fraction)
    for i in range(samples):
        is_err = i < err_count
        ago_s = i * 15  # newest at 0s, oldest at samples*15s back
        await state.insert_deploy_health_probe(DeployHealthProbe(
            probe_id=f"P-storm-{env}-{uuid.uuid4().hex[:6]}",
            deploy_id=f"D-{env}",
            env=env,
            recorded_at=now - timedelta(seconds=ago_s),
            response_time_ms=rt_ms if is_err else rt_ms // 4,
            error_rate_5m=1.0 if is_err else 0.0,
            http_status=500 if is_err else 200,
        ))


async def _seed_recovery(
    state: SQLiteStateStore, env: str,
    samples: int = MIN_SAMPLES_IN_WINDOW + 10,
) -> None:
    """After rollback: replace the storm with fresh healthy probes
    in the current window. The handler / slo_check should flip back
    to PASS."""
    now = datetime.utcnow()
    for i in range(samples):
        ago_s = i * 15
        await state.insert_deploy_health_probe(DeployHealthProbe(
            probe_id=f"P-recov-{env}-{uuid.uuid4().hex[:6]}",
            deploy_id=f"D-{env}",
            env=env,
            recorded_at=now - timedelta(seconds=ago_s),
            response_time_ms=100,
            error_rate_5m=0.0,
            http_status=200,
        ))


async def _drain_events() -> None:
    """Yield long enough for fire-and-forget _run() tasks to finish.
    Each handler does state I/O + a couple of tool calls, so 30 ticks
    at 10ms is well within the wall-clock budget."""
    for _ in range(30):
        await asyncio.sleep(0.01)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
async def state() -> SQLiteStateStore:
    s = SQLiteStateStore(db_path="/app/data/agent_team.db")
    await s.initialize()
    return s


@pytest.fixture
async def isolated_env(state: SQLiteStateStore) -> str:
    """Unique env tag per test so we never collide with real probes
    or with each other under pytest -j."""
    env = f"ae1-e2e-{uuid.uuid4().hex[:8]}"
    yield env
    # Cleanup — leave the rollback row for audit but drop probes.
    db = await state._get_db()
    await db.execute("DELETE FROM deploy_health WHERE env = ?", (env,))
    await db.execute("DELETE FROM rollback_requests WHERE env = ?", (env,))
    await db.commit()


def _make_capture() -> tuple[EventEmitter, list[tuple[str, dict]]]:
    events = EventEmitter()
    captured: list[tuple[str, dict]] = []

    async def _cap(event_type: str, data: dict) -> None:
        captured.append((event_type, dict(data)))

    events.on(_cap)
    return events, captured


# ── The headline test ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_incident_pipeline_anomaly_to_rollback(
    state: SQLiteStateStore, isolated_env: str,
):
    """The full chain: probe storm → anomaly_detect ANOMALY →
    sweeper emits → handler runs slo_check BREACH → auto_rollback
    queues → ops.rollback.triggered fires. One assertion per
    contract step (a)-(d)."""
    env = isolated_env

    # ── (a) Simulate supervisor probe storm ─────────────────────────
    await _seed_baseline(state, env)
    await _seed_5xx_storm(state, env)

    # ── (b) anomaly_detect ANOMALY ─────────────────────────────────
    anomaly_tool = AnomalyDetectTool(state=state)
    anomaly_result = await anomaly_tool.execute({"env": env})
    assert anomaly_result["verdict"] == "ANOMALY", anomaly_result
    metrics_flagged = {a["metric"] for a in anomaly_result["alerts"]}
    # response_time_ms is the strongest signal in our storm shape
    assert "response_time_ms" in metrics_flagged

    # ── (c) Handler wired + anomaly event fired ────────────────────
    events, captured = _make_capture()
    executor = _RealExecutor(state)
    events.on(make_ops_heal_handler(state, executor, events))

    # Direct emit (simulating what the periodic sweeper would do).
    await events.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {
        "env": env,
        "alerts": anomaly_result["alerts"],
        "summary": anomaly_result["summary"],
    })
    await _drain_events()

    # ── (d) Handler ran slo_check BREACH + auto_rollback queued ────
    rollback_events = [e for e in captured if e[0] == OPS_ROLLBACK_TRIGGERED]
    assert len(rollback_events) == 1, (
        f"expected exactly one ops.rollback.triggered, got: "
        f"{[e[0] for e in captured]}"
    )
    payload = rollback_events[0][1]
    assert payload["env"] == env
    assert payload["request_id"] and payload["request_id"].startswith(f"RB-{env}")
    assert "availability" in (payload.get("reason") or "").lower() or \
           "breach" in (payload.get("slo_summary") or "").lower()

    # rollback_requests row exists in pending state.
    pending = await state.get_in_flight_rollback_for_env(env)
    assert pending is not None
    assert pending.status == "pending"
    assert pending.request_id == payload["request_id"]


# ── Idempotency contract ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_repeated_anomaly_during_inflight_does_not_duplicate_rollback(
    state: SQLiteStateStore, isolated_env: str,
):
    """During an active incident the sweeper will keep firing
    deploy_health.anomaly_detected as long as the metrics are still
    bad. The handler MUST NOT queue a second auto_rollback for the
    same env — instead it emits ops.alert.fired with
    rollback_status='already_in_flight'."""
    env = isolated_env
    await _seed_baseline(state, env)
    await _seed_5xx_storm(state, env)

    events, captured = _make_capture()
    executor = _RealExecutor(state)
    events.on(make_ops_heal_handler(state, executor, events))

    # First anomaly → triggers rollback
    await events.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {"env": env, "alerts": []})
    await _drain_events()
    triggers_first = [e for e in captured if e[0] == OPS_ROLLBACK_TRIGGERED]
    assert len(triggers_first) == 1

    # Second anomaly while the rollback is still pending
    captured.clear()
    await events.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {"env": env, "alerts": []})
    await _drain_events()
    triggers_second = [e for e in captured if e[0] == OPS_ROLLBACK_TRIGGERED]
    alerts_second = [e for e in captured if e[0] == OPS_ALERT_FIRED]
    # NO second rollback event
    assert triggers_second == []
    # Exactly one alert with the in-flight annotation
    assert len(alerts_second) == 1
    assert alerts_second[0][1]["rollback_status"] == "already_in_flight"
    assert alerts_second[0][1]["rollback_request_id"] == triggers_first[0][1]["request_id"]


# ── Recovery contract ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_rollback_recovery_flips_slo_back_to_pass(
    state: SQLiteStateStore, isolated_env: str,
):
    """After the supervisor completes the rollback (modelled by
    transitioning the request to status='completed') AND fresh
    healthy probes land, slo_check must flip back to PASS — proving
    the system isn't stuck in a perpetual-alert state."""
    env = isolated_env
    await _seed_baseline(state, env)
    await _seed_5xx_storm(state, env)

    # Storm phase: slo_check BREACH
    slo_tool = SloCheckTool(state=state)
    storm_slo = await slo_tool.execute({"env": env})
    assert storm_slo["verdict"] == "BREACH"

    # Queue a rollback (as the handler would)
    rb_tool = AutoRollbackTool(state=state)
    rb = await rb_tool.execute({"env": env, "reason": "e2e test"})
    assert rb["status"] == "queued"

    # ── (e) Supervisor revert path — modelled inline ───────────────
    # In production the supervisor host process polls
    # rollback_requests, runs git revert, then transitions the row.
    # We mimic the terminal state transition here.
    await state.update_rollback_request_status(
        rb["request_id"], status="in_flight",
    )
    await state.update_rollback_request_status(
        rb["request_id"], status="completed",
        rollback_sha="recovered-sha-abc1234",
    )
    in_flight = await state.get_in_flight_rollback_for_env(env)
    # No pending/in_flight rollback after the transition.
    assert in_flight is None

    # ── (f) Fresh healthy probes land (post-revert) ────────────────
    # Clear the storm probes so the rolling window sees the recovery.
    db = await state._get_db()
    await db.execute("DELETE FROM deploy_health WHERE env = ?", (env,))
    await db.commit()
    await _seed_recovery(state, env)

    # slo_check must now return PASS (recovery confirmed)
    recovery_slo = await slo_tool.execute({"env": env})
    assert recovery_slo["verdict"] == "PASS", recovery_slo

    # New anomaly events would now route to PASS path (no event emit)
    events, captured = _make_capture()
    executor = _RealExecutor(state)
    events.on(make_ops_heal_handler(state, executor, events))
    await events.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {"env": env, "alerts": []})
    await _drain_events()
    # Healthy system → handler emits NEITHER alert nor rollback
    assert not [e for e in captured if e[0] == OPS_ROLLBACK_TRIGGERED]
    assert not [e for e in captured if e[0] == OPS_ALERT_FIRED]


# ── Negative contract: healthy env stays silent end-to-end ───────────────


@pytest.mark.asyncio
async def test_healthy_env_produces_no_alerts_end_to_end(
    state: SQLiteStateStore, isolated_env: str,
):
    """A healthy env with no anomalous data must NOT generate any
    ops.alert.fired or ops.rollback.triggered events even if the
    sweeper fires a spurious anomaly event (defence-in-depth: the
    handler re-checks via slo_check)."""
    env = isolated_env
    # Healthy baseline + healthy current
    await _seed_baseline(state, env)
    await _seed_recovery(state, env)  # healthy current window

    events, captured = _make_capture()
    executor = _RealExecutor(state)
    events.on(make_ops_heal_handler(state, executor, events))

    # Spurious anomaly event — should be dismissed by slo_check=PASS
    await events.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {
        "env": env, "alerts": [{"metric": "fake"}],
    })
    await _drain_events()
    assert not [e for e in captured if e[0] == OPS_ALERT_FIRED]
    assert not [e for e in captured if e[0] == OPS_ROLLBACK_TRIGGERED]
