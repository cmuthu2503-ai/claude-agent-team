"""AE-1 tool smoke tests — health_probe (AET-26) + anomaly_detect (AET-27)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from src.models.base import DeployHealthProbe
from src.tools.anomaly_detect import (
    MIN_BASELINE_SAMPLES,
    SIGMA_THRESHOLD,
    AnomalyDetectTool,
)
from src.tools.health_probe import HealthProbeTool


# ── health_probe (AET-26) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_probe_requires_url():
    r = await HealthProbeTool().execute({})
    assert r["ok"] is False
    assert r["http_status"] == 0
    assert "url" in (r["error"] or "").lower()


@pytest.mark.asyncio
async def test_health_probe_unreachable_url_is_status_zero():
    """An unreachable URL must return status=0 + error string, not crash.
    Uses a port that is not listening locally — 1 is reserved and never
    bound on any sane machine."""
    r = await HealthProbeTool().execute({
        "url": "http://127.0.0.1:1/nope",
        "timeout_seconds": 1.0,
    })
    assert r["ok"] is False
    assert r["http_status"] == 0
    assert r["error"]  # non-empty diagnostic
    assert r["response_time_ms"] >= 0


@pytest.mark.asyncio
async def test_health_probe_timeout_is_capped():
    """timeout_seconds is capped at 30s on input and floors at 0.5s."""
    # Negative/zero floors to 0.5; we just verify the call returns
    # quickly without hanging on a bogus timeout value.
    r = await HealthProbeTool().execute({
        "url": "http://127.0.0.1:1/nope",
        "timeout_seconds": 0,
    })
    assert r["http_status"] == 0
    assert r["response_time_ms"] < 5000  # didn't hang


@pytest.mark.asyncio
async def test_health_probe_against_live_backend():
    """Live backend on :8000 inside the container. Confirms the
    success-path return shape end-to-end."""
    r = await HealthProbeTool().execute({
        "url": "http://localhost:8000/api/v1/health",
        "timeout_seconds": 3.0,
    })
    assert r["ok"] is True
    assert r["http_status"] == 200
    assert r["response_time_ms"] >= 0
    assert r["error"] is None


# ── anomaly_detect (AET-27) ───────────────────────────────────────────────


class _StubState:
    def __init__(self, probes: list[DeployHealthProbe]) -> None:
        self._probes = probes

    async def list_deploy_health_probes(
        self, env: str | None = None, deploy_id: str | None = None,
        since: datetime | None = None, limit: int = 500,
    ) -> list[DeployHealthProbe]:
        rows = [
            p for p in self._probes
            if (env is None or p.env == env)
            and (deploy_id is None or p.deploy_id == deploy_id)
            and (since is None or p.recorded_at >= since)
        ]
        rows.sort(key=lambda p: p.recorded_at, reverse=True)
        return rows[:limit]


def _baseline_probe(env: str, ago_s: int, rt_ms: int = 100) -> DeployHealthProbe:
    """A probe in the baseline window (older than 5min)."""
    return DeployHealthProbe(
        probe_id=f"P-base-{ago_s}",
        deploy_id="D",
        env=env,
        recorded_at=datetime.utcnow() - timedelta(seconds=ago_s),
        response_time_ms=rt_ms,
        error_rate_5m=0.0,
        http_status=200,
    )


def _current_probe(env: str, ago_s: int, rt_ms: int) -> DeployHealthProbe:
    """A probe inside the current 5min window."""
    return DeployHealthProbe(
        probe_id=f"P-curr-{ago_s}",
        deploy_id="D",
        env=env,
        recorded_at=datetime.utcnow() - timedelta(seconds=ago_s),
        response_time_ms=rt_ms,
        error_rate_5m=0.0,
        http_status=200,
    )


@pytest.mark.asyncio
async def test_anomaly_detect_insufficient_data_on_cold_start():
    """Fewer than MIN_BASELINE_SAMPLES baseline probes must yield
    INSUFFICIENT_DATA — never trigger anomaly from cold state."""
    state = _StubState([_baseline_probe("e", 60 * (i + 6)) for i in range(3)])
    r = await AnomalyDetectTool(state=state).execute({"env": "e"})
    assert r["verdict"] == "INSUFFICIENT_DATA"
    assert r["alerts"] == []


@pytest.mark.asyncio
async def test_anomaly_detect_ok_when_current_matches_baseline():
    """20 baseline probes at 100ms + 3 current probes at 100ms → OK."""
    baseline = [
        _baseline_probe("e", 60 * (i + 6), rt_ms=100)
        for i in range(MIN_BASELINE_SAMPLES + 10)
    ]
    current = [_current_probe("e", 30 * i, rt_ms=100) for i in range(3)]
    state = _StubState(baseline + current)
    r = await AnomalyDetectTool(state=state).execute({"env": "e"})
    assert r["verdict"] == "OK"
    assert r["alerts"] == []


@pytest.mark.asyncio
async def test_anomaly_detect_alerts_when_current_spikes():
    """20 baseline probes ~100ms + 3 current at 2000ms → ANOMALY on
    response_time_ms with deviation >> 2σ."""
    baseline = [
        _baseline_probe("e", 60 * (i + 6), rt_ms=100 + (i % 5))
        for i in range(MIN_BASELINE_SAMPLES + 10)
    ]
    current = [_current_probe("e", 30 * i, rt_ms=2000) for i in range(3)]
    state = _StubState(baseline + current)
    r = await AnomalyDetectTool(state=state).execute({"env": "e"})
    assert r["verdict"] == "ANOMALY"
    rt_alert = next(a for a in r["alerts"] if a["metric"] == "response_time_ms")
    assert rt_alert["current_value"] >= 1500
    assert rt_alert["baseline"] < 200
    assert rt_alert["deviation_sigmas"] >= SIGMA_THRESHOLD
    assert rt_alert["threshold"] == SIGMA_THRESHOLD


@pytest.mark.asyncio
async def test_anomaly_detect_excludes_current_from_baseline():
    """If we included the current window in the baseline, a sustained
    anomaly would slowly poison its own baseline and self-extinguish.
    Verify: 20 baseline at 100ms + 5 current at 500ms. Without the
    exclusion guard the baseline would drift up toward 180ms and the
    deviation would shrink. With the guard the baseline stays at
    ~100ms and deviation is large."""
    baseline = [
        _baseline_probe("e", 60 * (i + 6), rt_ms=100)
        for i in range(MIN_BASELINE_SAMPLES + 10)
    ]
    current = [_current_probe("e", 30 * i, rt_ms=500) for i in range(5)]
    state = _StubState(baseline + current)
    r = await AnomalyDetectTool(state=state).execute({"env": "e"})
    rt_alert = next(
        (a for a in r["alerts"] if a["metric"] == "response_time_ms"),
        None,
    )
    assert rt_alert is not None, "expected response_time_ms alert"
    # Baseline must be ~100, NOT contaminated by the current 500ms probes.
    assert rt_alert["baseline"] <= 110, (
        f"baseline {rt_alert['baseline']} was contaminated by current window"
    )


@pytest.mark.asyncio
async def test_anomaly_detect_min_sigma_floor_prevents_false_alerts():
    """When the baseline is perfectly flat (sigma → 0), the MIN_SIGMA
    floor keeps a tiny wobble in the current window from alerting at
    infinite-σ. Without the floor, baseline 100±0 vs current 101 →
    deviation ∞ → false alert."""
    baseline = [
        _baseline_probe("e", 60 * (i + 6), rt_ms=100)  # all identical
        for i in range(MIN_BASELINE_SAMPLES + 5)
    ]
    current = [_current_probe("e", 30 * i, rt_ms=101) for i in range(3)]
    state = _StubState(baseline + current)
    r = await AnomalyDetectTool(state=state).execute({"env": "e"})
    # 1ms wobble below the MIN_SIGMA=1.0 floor → deviation = 1.0σ < 2.0
    # threshold → no alert.
    rt_alerts = [a for a in r["alerts"] if a["metric"] == "response_time_ms"]
    assert not rt_alerts, (
        f"flat baseline + tiny wobble should NOT alert, got: {rt_alerts}"
    )


@pytest.mark.asyncio
async def test_anomaly_detect_error_when_no_state():
    r = await AnomalyDetectTool(state=None).execute({"env": "e"})
    assert r["verdict"] == "ERROR"


@pytest.mark.asyncio
async def test_anomaly_detect_error_when_env_missing():
    r = await AnomalyDetectTool(state=_StubState([])).execute({})
    assert r["verdict"] == "ERROR"
