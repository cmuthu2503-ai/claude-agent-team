"""AET-25 — slo_check tool smoke (no LLM, deterministic)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from src.models.base import DeployHealthProbe
from src.tools.slo_check import SloCheckTool, load_slo_config, resolve_env_slos

MIN_SAMPLES = 3  # mirrors slo.yaml defaults.min_samples


class _StubState:
    """In-memory probe store with the same surface as
    StateStore.list_deploy_health_probes that slo_check calls."""

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


def _probe(env: str, ago_s: int, status: int, rt_ms: int) -> DeployHealthProbe:
    return DeployHealthProbe(
        probe_id=f"P-{env}-{ago_s}",
        deploy_id="D-TEST",
        env=env,
        recorded_at=datetime.utcnow() - timedelta(seconds=ago_s),
        response_time_ms=rt_ms,
        error_rate_5m=(0.0 if 200 <= status < 300 else 1.0),
        http_status=status,
    )


@pytest.mark.asyncio
async def test_insufficient_data_when_fewer_than_min_samples():
    state = _StubState([_probe("e", 0, 200, 100), _probe("e", 60, 200, 100)])
    r = await SloCheckTool(state=state).execute({"env": "e"})
    assert r["verdict"] == "INSUFFICIENT_DATA"
    assert r["samples"] == 2
    assert r["min_samples"] == MIN_SAMPLES
    assert r["slos"] == []


@pytest.mark.asyncio
async def test_pass_when_all_slos_met():
    probes = [_probe("e", i * 30, 200, 100 + i) for i in range(10)]
    r = await SloCheckTool(state=_StubState(probes)).execute({"env": "e"})
    assert r["verdict"] == "PASS"
    by_name = {s["name"]: s for s in r["slos"]}
    assert by_name["availability"]["passed"] is True
    assert by_name["p95_latency_ms"]["passed"] is True
    assert by_name["error_rate_5m"]["passed"] is True


@pytest.mark.asyncio
async def test_degraded_when_latency_breached_but_availability_ok():
    # Half samples slow → p95 will fail; status always 200 → availability OK.
    probes = [
        _probe("e", i * 30, 200, 1500 if i >= 5 else 100)
        for i in range(10)
    ]
    r = await SloCheckTool(state=_StubState(probes)).execute({"env": "e"})
    assert r["verdict"] == "DEGRADED"
    by_name = {s["name"]: s for s in r["slos"]}
    assert by_name["availability"]["passed"] is True
    assert by_name["p95_latency_ms"]["passed"] is False


@pytest.mark.asyncio
async def test_breach_when_availability_drops_below_target():
    # 30% errors → availability 0.70 < 0.99 target → BREACH (rollback trigger).
    probes = [
        _probe("e", i * 30, 500 if i < 3 else 200, 200)
        for i in range(10)
    ]
    r = await SloCheckTool(state=_StubState(probes)).execute({"env": "e"})
    assert r["verdict"] == "BREACH"
    avail = next(s for s in r["slos"] if s["name"] == "availability")
    assert avail["passed"] is False
    assert 0.6 < avail["observed"] < 0.8  # 0.70


@pytest.mark.asyncio
async def test_error_when_env_missing():
    r = await SloCheckTool(state=_StubState([])).execute({})
    assert r["verdict"] == "ERROR"
    assert "env" in r["reason"].lower()


@pytest.mark.asyncio
async def test_error_when_no_state_wired():
    r = await SloCheckTool(state=None).execute({"env": "e"})
    assert r["verdict"] == "ERROR"


def test_slo_config_loads_from_yaml():
    """AET-25 — config/slo.yaml is parsed and the canonical SLO names
    appear in the defaults block."""
    cfg = load_slo_config()
    assert "defaults" in cfg
    slos = (cfg["defaults"].get("slos") or {})
    for name in (
        "availability", "p95_latency_ms", "p99_latency_ms",
        "error_rate_5m", "restart_burst",
    ):
        assert name in slos, f"missing SLO {name} in slo.yaml defaults"


def test_per_env_overrides_inherit_from_defaults():
    """AET-25 — production tightens availability + p95 latency; the
    other SLOs fall through from defaults."""
    cfg = load_slo_config()
    prod = resolve_env_slos(cfg, "production")
    dev = resolve_env_slos(cfg, "development")
    # Production overrides
    assert prod["slos"]["availability"]["target"] == pytest.approx(0.995)
    assert prod["slos"]["p95_latency_ms"]["target"] == pytest.approx(300)
    # Defaults still apply for the unspecified SLOs
    assert prod["slos"]["restart_burst"]["target"] == pytest.approx(2)
    # Dev loosens p95 + availability
    assert dev["slos"]["p95_latency_ms"]["target"] >= 1000
    assert dev["slos"]["availability"]["target"] < 0.99
    # Comparator + severity preserved from defaults across overrides
    assert prod["slos"]["availability"]["comparator"] == "ge"
    assert prod["slos"]["availability"]["severity"] == "breach"


@pytest.mark.asyncio
async def test_per_env_threshold_actually_used_in_verdict():
    """Production's tighter availability target (0.995) means a 99%
    availability run BREACHes prod but PASSes the default 99% env."""
    # 99 / 100 successful → availability ≈ 0.99
    probes = [
        _probe("development", i * 30, 200 if i > 0 else 500, 100)
        for i in range(100)
    ]
    # Same probe shape but tag as prod
    prod_probes = [
        DeployHealthProbe(
            probe_id=f"P-prod-{i}", deploy_id="D", env="production",
            recorded_at=p.recorded_at, response_time_ms=p.response_time_ms,
            error_rate_5m=p.error_rate_5m, http_status=p.http_status,
        )
        for i, p in enumerate(probes)
    ]
    state = _StubState(probes + prod_probes)
    dev_r = await SloCheckTool(state=state).execute({"env": "development"})
    prod_r = await SloCheckTool(state=state).execute({"env": "production"})
    # dev cutoff = 0.90, observed 0.99 → PASS on availability
    dev_avail = next(s for s in dev_r["slos"] if s["name"] == "availability")
    assert dev_avail["passed"] is True
    # prod cutoff = 0.995, observed 0.99 → BREACH
    prod_avail = next(s for s in prod_r["slos"] if s["name"] == "availability")
    assert prod_avail["passed"] is False
    assert prod_r["verdict"] == "BREACH"


@pytest.mark.asyncio
async def test_deploy_id_filter_narrows_window():
    """deploy_id parameter must narrow the probe set; mixing two deploys'
    probes should not let one masquerade as the other's history."""
    bad_deploy_probes = [
        DeployHealthProbe(
            probe_id=f"P-bad-{i}", deploy_id="BAD", env="e",
            recorded_at=datetime.utcnow() - timedelta(seconds=i*30),
            response_time_ms=2000, error_rate_5m=1.0, http_status=500,
        )
        for i in range(10)
    ]
    good_deploy_probes = [_probe("e", i * 30, 200, 100) for i in range(10)]
    for p in good_deploy_probes:
        p.deploy_id = "GOOD"
    state = _StubState(bad_deploy_probes + good_deploy_probes)

    r_good = await SloCheckTool(state=state).execute({
        "env": "e", "deploy_id": "GOOD",
    })
    assert r_good["verdict"] == "PASS"

    r_bad = await SloCheckTool(state=state).execute({
        "env": "e", "deploy_id": "BAD",
    })
    assert r_bad["verdict"] == "BREACH"
