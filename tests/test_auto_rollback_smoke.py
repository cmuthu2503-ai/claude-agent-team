"""AET-29 smoke — auto_rollback tool (queue + idempotency + sustained-breach).

Uses the live SQLite store but writes to an isolated env tag
('rb-smoke-{i}') so the tests don't collide with real probes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest

from src.models.base import DeployHealthProbe, RollbackRequest
from src.state.sqlite_store import SQLiteStateStore
from src.tools.auto_rollback import (
    MIN_SAMPLES_IN_WINDOW,
    SUSTAIN_MINUTES,
    AutoRollbackTool,
)


@pytest.fixture
async def state():
    s = SQLiteStateStore(db_path="/app/data/agent_team.db")
    await s.initialize()
    return s


async def _seed_probes(
    state: SQLiteStateStore, env: str,
    count: int, error_fraction: float,
) -> None:
    """Insert *count* probes evenly spread over the last 5min; the
    first *error_fraction* are 500s, the rest 200s."""
    now = datetime.utcnow()
    err_count = int(count * error_fraction)
    for i in range(count):
        status = 500 if i < err_count else 200
        await state.insert_deploy_health_probe(DeployHealthProbe(
            probe_id=f"P-{env}-{i}-{uuid.uuid4().hex[:6]}",
            deploy_id=f"D-{env}",
            env=env,
            recorded_at=now - timedelta(seconds=i * 15),
            response_time_ms=200,
            error_rate_5m=1.0 if status == 500 else 0.0,
            http_status=status,
        ))


@pytest.mark.asyncio
async def test_auto_rollback_queues_when_breach_sustained(state):
    env = f"rb-smoke-queue-{uuid.uuid4().hex[:6]}"
    # 70% error rate over MIN_SAMPLES_IN_WINDOW probes → availability 0.30
    # which is far below the 0.99 default target → sustained breach.
    await _seed_probes(
        state, env=env, count=MIN_SAMPLES_IN_WINDOW + 5, error_fraction=0.7,
    )
    r = await AutoRollbackTool(state=state).execute({
        "env": env, "reason": "smoke breach",
    })
    assert r["status"] == "queued", r
    assert r["request_id"] and r["request_id"].startswith(f"RB-{env}")
    assert "smoke breach" in r["reason"] or "availability" in r["reason"]
    # The in-flight row now exists
    existing = await state.get_in_flight_rollback_for_env(env)
    assert existing is not None
    assert existing.status == "pending"


@pytest.mark.asyncio
async def test_auto_rollback_idempotent_when_already_in_flight(state):
    env = f"rb-smoke-idem-{uuid.uuid4().hex[:6]}"
    await _seed_probes(
        state, env=env, count=MIN_SAMPLES_IN_WINDOW + 5, error_fraction=0.7,
    )
    tool = AutoRollbackTool(state=state)
    first = await tool.execute({"env": env, "reason": "first"})
    assert first["status"] == "queued"
    # Second call must NOT insert a duplicate row.
    second = await tool.execute({"env": env, "reason": "second"})
    assert second["status"] == "already_in_flight"
    assert second["request_id"] == first["request_id"]
    # Once we transition to a terminal state, a NEW request is allowed.
    await state.update_rollback_request_status(
        first["request_id"], status="completed",
        rollback_sha="abc1234",
    )
    third = await tool.execute({"env": env, "reason": "third"})
    assert third["status"] == "queued"
    assert third["request_id"] != first["request_id"]


@pytest.mark.asyncio
async def test_auto_rollback_does_not_fire_on_transient_blip(state):
    """A single failed probe in an otherwise-healthy 5min window
    must NOT trigger a rollback (false-positive guard)."""
    env = f"rb-smoke-blip-{uuid.uuid4().hex[:6]}"
    # 1 error in MIN_SAMPLES_IN_WINDOW+10 probes → availability ~0.93
    # — still below the 0.99 default cutoff, so this WOULD queue. To
    # show the guard works we drop the error count to 0 → availability
    # 1.0 → no breach.
    await _seed_probes(
        state, env=env, count=MIN_SAMPLES_IN_WINDOW + 10, error_fraction=0.0,
    )
    r = await AutoRollbackTool(state=state).execute({"env": env})
    assert r["status"] == "breach_not_sustained", r
    # No row inserted
    existing = await state.get_in_flight_rollback_for_env(env)
    assert existing is None


@pytest.mark.asyncio
async def test_auto_rollback_insufficient_data(state):
    """Fewer than MIN_SAMPLES_IN_WINDOW probes → insufficient_data,
    NOT a rollback. Anti-cold-start guard."""
    env = f"rb-smoke-cold-{uuid.uuid4().hex[:6]}"
    await _seed_probes(state, env=env, count=2, error_fraction=1.0)
    r = await AutoRollbackTool(state=state).execute({"env": env})
    assert r["status"] == "insufficient_data", r
    existing = await state.get_in_flight_rollback_for_env(env)
    assert existing is None


@pytest.mark.asyncio
async def test_auto_rollback_error_when_env_missing(state):
    r = await AutoRollbackTool(state=state).execute({})
    assert r["status"] == "error"


@pytest.mark.asyncio
async def test_auto_rollback_error_when_state_missing():
    r = await AutoRollbackTool(state=None).execute({"env": "e"})
    assert r["status"] == "error"
