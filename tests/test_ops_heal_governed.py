"""HAI-47 (FR-061) — ops-heal auto-rollback routes through the gate when governed."""

import asyncio
from typing import Any

import pytest

from src.core.events import (
    DEPLOY_HEALTH_ANOMALY_DETECTED,
    OPS_ALERT_FIRED,
    OPS_ROLLBACK_TRIGGERED,
    EventEmitter,
)
from src.core.ops_heal_handler import make_ops_heal_handler
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteStateStore(db_path=str(tmp_path / "oh.db"))
    await s.initialize()
    yield s
    await s.close()


class _StubTool:
    def __init__(self, result):
        self._result = result
        self.calls: list[Any] = []

    async def execute(self, params):
        self.calls.append(params)
        return self._result


class _Executor:
    def __init__(self, tools):
        self.tool_registry = type("R", (), {"get_implementation": lambda _self, n: tools[n]})()


def _capture():
    em = EventEmitter()
    cap: list = []

    async def h(et, d):
        cap.append((et, dict(d)))

    em.on(h)
    return em, cap


async def _drain():
    for _ in range(30):
        await asyncio.sleep(0.01)


async def test_breach_governed_proposes_rollback_not_executes(store):
    rollback = _StubTool({"status": "queued", "request_id": "RB-x"})
    ex = _Executor({
        "slo_check": _StubTool({"verdict": "BREACH", "summary": "availability 0.5"}),
        "auto_rollback": rollback,
    })
    em, cap = _capture()
    em.on(make_ops_heal_handler(store, ex, em, governed=lambda: True))

    await em.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {"env": "staging", "deploy_id": "D-1"})
    await _drain()

    # the rollback tool was NOT executed — a proposal was created instead
    assert rollback.calls == []
    proposals = await store.list_proposals(action_type="rollback")
    assert len(proposals) == 1
    assert proposals[0].payload["env"] == "staging"
    assert proposals[0].proposed_by == "system:ops-heal"
    # a breach alert fired flagged as proposed (NOT a triggered rollback)
    assert not [e for e in cap if e[0] == OPS_ROLLBACK_TRIGGERED]
    alert = [d for et, d in cap if et == OPS_ALERT_FIRED][-1]
    assert alert["rollback_status"] == "proposed" and alert["rollback_proposal_id"]


async def test_breach_legacy_rolls_back_directly(store):
    rollback = _StubTool({"status": "queued", "request_id": "RB-x", "deploy_id": "D", "reason": "r"})
    ex = _Executor({
        "slo_check": _StubTool({"verdict": "BREACH", "summary": "availability 0.5"}),
        "auto_rollback": rollback,
    })
    em, cap = _capture()
    em.on(make_ops_heal_handler(store, ex, em))      # default → legacy

    await em.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {"env": "staging"})
    await _drain()

    assert len(rollback.calls) == 1                  # rolled back NOW
    assert [e for e in cap if e[0] == OPS_ROLLBACK_TRIGGERED]
    assert await store.list_proposals(action_type="rollback") == []   # no proposal


async def test_degraded_still_auto_alerts_even_when_governed(store):
    """Pure alerts (DEGRADED) must STILL auto-fire under governance — only the
    state-changing BREACH->rollback action is gated."""
    rollback = _StubTool({"status": "queued"})
    ex = _Executor({
        "slo_check": _StubTool({"verdict": "DEGRADED", "summary": "p95 slow"}),
        "auto_rollback": rollback,
    })
    em, cap = _capture()
    em.on(make_ops_heal_handler(store, ex, em, governed=lambda: True))

    await em.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {"env": "staging"})
    await _drain()

    alerts = [d for et, d in cap if et == OPS_ALERT_FIRED]
    assert len(alerts) == 1 and alerts[0]["severity"] == "degraded"
    assert rollback.calls == []
    assert await store.list_proposals(action_type="rollback") == []   # not gated, not proposed


async def test_repeated_breach_governed_is_idempotent_per_env(store):
    ex = _Executor({
        "slo_check": _StubTool({"verdict": "BREACH", "summary": "breach"}),
        "auto_rollback": _StubTool({"status": "queued"}),
    })
    em, _ = _capture()
    handler = make_ops_heal_handler(store, ex, em, governed=lambda: True)
    em.on(handler)

    await em.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {"env": "staging"})
    await _drain()
    await em.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {"env": "staging"})
    await _drain()
    # one proposal per env even across repeated breaches
    assert len(await store.list_proposals(action_type="rollback")) == 1
