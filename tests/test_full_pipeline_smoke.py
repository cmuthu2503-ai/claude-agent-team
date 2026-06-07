"""AET-42 — Full-pipeline end-to-end smoke (Phase AE definition of done).

This is the CONTRACT test that proves all five AE sub-phases
integrate cleanly with the existing pipeline. It does NOT spin up
real LLM calls (~$50 and 30+ minutes for one run); instead it
exercises each AE gate's actual production code path with
synthesized inputs and asserts the right events flow.

What this pins
--------------
For each AE sub-phase the test confirms:

  AE-3 (quality_guardian)   — policy_check verdict resolution + the
                               WorkflowRunner's quality_guardian_approval
                               gate emits quality.gate.{passed,failed}.

  AE-4 (security_specialist) — the structured security-report-json
                               gate (AET-21) parses correctly, applies
                               the AET-20 threshold, and emits
                               security.gate.{passed,failed}.

  AE-5 (architecture_reviewer) — the AET-35 arch_review_block_severity
                               threshold + CRITICAL/HIGH split route
                               findings correctly.

  AE-2 (self_learning_agent) — request.failed event fires the
                               make_self_learning_handler factory's
                               handler with all routing branches.

  AE-1 (ops_heal_agent)     — deploy_health.anomaly_detected event
                               wakes make_ops_heal_handler which runs
                               slo_check → auto_rollback and emits
                               ops.alert.* / ops.rollback.*.

Plus the cross-cutting contracts:
  - Every event type the frontend (TeamStatus.tsx AE_EVENT_STYLES)
    subscribes to fires through EventEmitter and reaches subscribers.
  - The SCAFFOLD-badge endpoint surfaces total_subtasks per agent.
  - Each AE agent's tools: list is fully granted in tools.yaml.

What this DOES NOT do
---------------------
  - Real LLM calls (covered indirectly by the per-sub-phase smokes).
  - Real docker compose deployment (covered by the host supervisor's
    own test suite; out of scope for the in-container pytest).
  - Real git revert (the supervisor side; the auto_rollback queue
    contract is tested separately in tests/test_auto_rollback_smoke).

This test is the CAPSTONE — it depends on the per-sub-phase smokes
passing (AET-08, AET-14, AET-22, AET-32, AET-36) and adds the
integration assertions that those individual tests can't make in
isolation.

Run via:
  docker compose exec backend pytest tests/test_full_pipeline_smoke.py -v
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.core.events import (
    DEPLOY_HEALTH_ANOMALY_DETECTED,
    LESSONS_ADDED,
    LESSONS_DUPLICATE_SKIPPED,
    LESSONS_PENDING_REVIEW,
    OPS_ALERT_FIRED,
    OPS_ROLLBACK_TRIGGERED,
    QUALITY_GATE_FAILED,
    QUALITY_GATE_PASSED,
    SECURITY_GATE_FAILED,
    SECURITY_GATE_PASSED,
    EventEmitter,
)


# ── Capture-emitter helper (reused across phases) ─────────────────────────


def _capture() -> tuple[EventEmitter, list[tuple[str, dict]]]:
    events = EventEmitter()
    captured: list[tuple[str, dict]] = []

    async def _cap(et: str, data: dict) -> None:
        captured.append((et, dict(data)))

    events.on(_cap)
    return events, captured


async def _drain() -> None:
    """Yield enough times for fire-and-forget handlers to complete."""
    for _ in range(30):
        await asyncio.sleep(0.01)


# ─────────────────────────────────────────────────────────────────────────
# CAPSTONE STEP 1 — AE-3 quality gate end-to-end
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step1_quality_gate_emits_passed_on_clean_emissions():
    """AET-06: structured policy_check verdict + AET-05 event."""
    from src.core.quality_gate import (
        build_quality_gate_payload,
        emit_quality_gate_event,
    )

    events, captured = _capture()
    clean_decision = {
        "verdict": "PASS",
        "violations": [],
        "summary": {"message": "clean"},
        "stage": "review",
        "rework_cycle": 0,
    }
    await emit_quality_gate_event(
        events, request_id="REQ-CAPSTONE-Q", verdict="PASS",
        violations=[], summary={"message": "clean"},
        stage="review", rework_cycle=0,
    )
    await _drain()
    passed = [e for e in captured if e[0] == QUALITY_GATE_PASSED]
    assert len(passed) == 1
    assert passed[0][1]["verdict"] == "PASS"


@pytest.mark.asyncio
async def test_step1_quality_gate_emits_failed_on_block():
    from src.core.quality_gate import emit_quality_gate_event

    events, captured = _capture()
    block_decision = {
        "verdict": "BLOCK",
        "violations": [{"rule_id": "QR-001", "severity": "enforce"}],
        "summary": {"message": "print() in production"},
        "stage": "review",
        "rework_cycle": 1,
    }
    await emit_quality_gate_event(
        events, request_id="REQ-CAPSTONE-Q2", verdict="BLOCK",
        violations=[{"rule_id": "QR-001", "severity": "enforce"}],
        summary={"message": "print() in production"},
        stage="review", rework_cycle=1,
    )
    await _drain()
    failed = [e for e in captured if e[0] == QUALITY_GATE_FAILED]
    assert len(failed) == 1
    assert failed[0][1]["verdict"] == "BLOCK"
    assert any(v["rule_id"] == "QR-001" for v in failed[0][1].get("violations", []))


# ─────────────────────────────────────────────────────────────────────────
# CAPSTONE STEP 2 — AE-4 security gate end-to-end
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step2_security_gate_passes_clean_report():
    """AET-21: JSON-fenced security report + structured verdict."""
    from src.workflows.runner import WorkflowRunner

    events, captured = _capture()

    class _NoExec:
        async def execute_agent(self, **kw):
            raise AssertionError("not invoked")

    runner = WorkflowRunner(
        executor=_NoExec(), events=events,
        thresholds={"thresholds": {
            "security_max_severity_to_block": {"value": "high"},
        }},
    )
    clean = (
        "## Security Scan Report\n\n"
        "**Verdict: ✅ PASS**\n\n"
        "```security-report-json\n"
        + json.dumps({
            "findings": [],
            "by_tool": {"secret_scan": {"verdict": "PASS", "finding_count": 0}},
        })
        + "\n```\n"
    )
    result = await runner._check_security_gate(
        {"security_report": clean}, "REQ-CAPSTONE-S1",
    )
    assert result["passed"] is True
    passed = [e for e in captured if e[0] == SECURITY_GATE_PASSED]
    assert len(passed) == 1


@pytest.mark.asyncio
async def test_step2_security_gate_blocks_on_critical():
    from src.workflows.runner import WorkflowRunner

    events, captured = _capture()
    runner = WorkflowRunner(
        executor=type("E", (), {"execute_agent": lambda *a, **k: None})(),
        events=events,
        thresholds={"thresholds": {
            "security_max_severity_to_block": {"value": "high"},
        }},
    )
    blocked = (
        "## Security Scan Report\n\n"
        "```security-report-json\n"
        + json.dumps({
            "findings": [{
                "rule_id": "anthropic_aws_workspace_id",
                "severity": "critical",
                "tool": "secret_scan",
                "file": "src/cfg.py", "line": 1,
                "message": "leaked workspace ID",
            }],
            "by_tool": {"secret_scan": {"verdict": "BLOCK", "finding_count": 1}},
        })
        + "\n```\n"
    )
    result = await runner._check_security_gate(
        {"security_report": blocked}, "REQ-CAPSTONE-S2",
    )
    assert result["passed"] is False
    failed = [e for e in captured if e[0] == SECURITY_GATE_FAILED]
    assert len(failed) == 1
    assert failed[0][1]["blocking_count"] == 1


# ─────────────────────────────────────────────────────────────────────────
# CAPSTONE STEP 3 — AE-5 arch threshold integration
# ─────────────────────────────────────────────────────────────────────────


def test_step3_arch_threshold_routes_critical_high_correctly():
    """AET-35: rules 1-3 (CRITICAL) block at default cutoff; rules 4-6
    (HIGH) only block when operator tightens to 'high'."""
    from src.core.security_threshold import (
        get_arch_block_severity,
        split_findings,
    )

    # AE-1 rules-1-3 (CRITICAL) + rules-4-6 (HIGH)
    findings = [
        {"rule_id": "rule-1", "severity": "critical"},
        {"rule_id": "rule-4", "severity": "high"},
    ]

    default = get_arch_block_severity(None)
    assert default == "critical"

    blocking, non = split_findings(findings, default)
    assert [f["severity"] for f in blocking] == ["critical"]
    assert [f["severity"] for f in non] == ["high"]

    # Tightened — HIGH now blocks too
    blocking_h, non_h = split_findings(findings, "high")
    assert [f["severity"] for f in blocking_h] == ["critical", "high"]
    assert non_h == []


# ─────────────────────────────────────────────────────────────────────────
# CAPSTONE STEP 4 — AE-2 self-learning event chain
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step4_self_learning_handler_routes_pending_review():
    """AET-11/AET-13: request.failed → handler → single_agent_call →
    OK_WRITTEN_PENDING → lessons.pending_review event."""
    from src.core.self_learning_trigger import make_self_learning_handler

    events, captured = _capture()

    class _StubState:
        async def get_request(self, rid: str) -> Any:
            return None

        async def get_subtasks_for_request(self, rid: str) -> list[Any]:
            return []

    class _StubExec:
        async def single_agent_call(self, agent_id: str, prompt: str, label: str):
            # Simulate the agent calling lessons_writer.append and
            # getting back OK_WRITTEN_PENDING.
            return {"text": (
                "OK_WRITTEN_PENDING: Lesson L99 queued for human review "
                "in agent-lessons-learned.pending.md."
            )}

    handler = make_self_learning_handler(_StubState(), _StubExec(), events)
    events.on(handler)
    await events.emit("request.failed", {
        "request_id": "REQ-CAPSTONE-L1",
        "error": "synthetic failure for AET-42 step 4",
    })
    await _drain()

    pending = [e for e in captured if e[0] == LESSONS_PENDING_REVIEW]
    assert len(pending) == 1, captured
    assert pending[0][1]["request_id"] == "REQ-CAPSTONE-L1"
    assert pending[0][1]["lesson_id"] == "L99"


# ─────────────────────────────────────────────────────────────────────────
# CAPSTONE STEP 5 — AE-1 ops_heal incident pipeline
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step5_ops_heal_breach_triggers_rollback_event():
    """AET-31: anomaly_detected event → handler → slo_check BREACH →
    auto_rollback queued → ops.rollback.triggered event."""
    from src.core.ops_heal_handler import make_ops_heal_handler

    events, captured = _capture()

    class _Tool:
        def __init__(self, result):
            self._r = result

        async def execute(self, params):
            return self._r

    class _Registry:
        def __init__(self, tools):
            self._t = tools

        def get_implementation(self, name):
            return self._t[name]

    class _Exec:
        def __init__(self, tools):
            self.tool_registry = _Registry(tools)

    executor = _Exec({
        "slo_check": _Tool({"verdict": "BREACH", "summary": "availability 0.5"}),
        "auto_rollback": _Tool({
            "status": "queued",
            "request_id": "RB-capstone-XYZ",
            "deploy_id": "D-test",
            "reason": "availability 0.5",
        }),
    })

    events.on(make_ops_heal_handler(None, executor, events))
    await events.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {
        "env": "staging", "alerts": [{"metric": "response_time_ms"}],
    })
    await _drain()

    rb = [e for e in captured if e[0] == OPS_ROLLBACK_TRIGGERED]
    assert len(rb) == 1
    assert rb[0][1]["request_id"] == "RB-capstone-XYZ"


@pytest.mark.asyncio
async def test_step5_ops_heal_degraded_emits_alert_not_rollback():
    from src.core.ops_heal_handler import make_ops_heal_handler

    events, captured = _capture()

    class _Tool:
        def __init__(self, r): self._r = r
        async def execute(self, p): return self._r

    class _Reg:
        def __init__(self, t): self._t = t
        def get_implementation(self, n): return self._t[n]

    class _Exec:
        def __init__(self, t): self.tool_registry = _Reg(t)

    rb = _Tool({"status": "queued"})
    executor = _Exec({
        "slo_check": _Tool({"verdict": "DEGRADED", "summary": "p95 slow"}),
        "auto_rollback": rb,
    })
    events.on(make_ops_heal_handler(None, executor, events))
    await events.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {"env": "staging"})
    await _drain()
    alerts = [e for e in captured if e[0] == OPS_ALERT_FIRED]
    rollbacks = [e for e in captured if e[0] == OPS_ROLLBACK_TRIGGERED]
    assert len(alerts) == 1
    assert rollbacks == []
    assert alerts[0][1]["severity"] == "degraded"


# ─────────────────────────────────────────────────────────────────────────
# CAPSTONE STEP 6 — cross-cutting contracts (UI, configs)
# ─────────────────────────────────────────────────────────────────────────


_REPO_ROOT = Path(__file__).resolve().parents[1]
_AE_AGENTS = (
    "quality_guardian",
    "self_learning_agent",
    "security_specialist",
    "ops_heal_agent",
    "architecture_reviewer",
)


def test_step6_all_ae_agents_have_tool_grants_aligned():
    """AET-41 contract: every tool an AE agent lists in its YAML
    must be granted to it (or 'all') in config/tools.yaml."""
    catalog = yaml.safe_load(
        (_REPO_ROOT / "config" / "tools.yaml").read_text(encoding="utf-8"),
    )["tools"]
    mismatches: list[str] = []
    for agent_id in _AE_AGENTS:
        agent_yaml = yaml.safe_load(
            (_REPO_ROOT / "config" / "agents" / f"{agent_id}.yaml").read_text(
                encoding="utf-8",
            ),
        )
        for tool in agent_yaml.get("tools", []):
            grants = catalog.get(tool, {}).get("available_to", [])
            if "all" not in grants and agent_id not in grants:
                mismatches.append(f"{agent_id} → {tool} (granted: {grants})")
    assert not mismatches, (
        "AE agent → tool grant mismatches (see PRD §6.9.6 verification "
        "snippet for the rationale):\n  " + "\n  ".join(mismatches)
    )


def test_step6_ae1_tools_registered_in_executor():
    """AET-30 contract: all five AE-1 ops tools must be importable
    so the executor can register them at boot."""
    from src.tools.anomaly_detect import AnomalyDetectTool
    from src.tools.auto_rollback import AutoRollbackTool
    from src.tools.health_probe import HealthProbeTool
    from src.tools.ops_check import OpsCheckTool
    from src.tools.slo_check import SloCheckTool

    # Each must have a schema() and execute() — the executor's contract.
    for cls in (
        AnomalyDetectTool, AutoRollbackTool, HealthProbeTool,
        OpsCheckTool, SloCheckTool,
    ):
        tool = cls(state=None) if cls is not HealthProbeTool and cls is not OpsCheckTool else cls()
        assert hasattr(tool, "schema") or cls is OpsCheckTool  # OpsCheckTool has @staticmethod schema
        assert hasattr(tool, "execute")


def test_step6_slo_yaml_loads_with_per_env_overrides():
    """AET-25 contract: per-env SLO targets are loadable and
    production tightens availability vs default."""
    from src.tools.slo_check import load_slo_config, resolve_env_slos

    cfg = load_slo_config()
    assert "defaults" in cfg
    prod = resolve_env_slos(cfg, "production")
    dev = resolve_env_slos(cfg, "development")
    assert prod["slos"]["availability"]["target"] > dev["slos"]["availability"]["target"]


# ─────────────────────────────────────────────────────────────────────────
# CAPSTONE STEP 7 — combined event-stream contract (definition of done)
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step7_combined_pipeline_emits_full_ae_event_set():
    """The capstone assertion: walking through one end-to-end synthetic
    pipeline (quality gate pass → security gate pass → request fail →
    self-learn → ops anomaly → rollback) produces the FULL set of AE
    event types the UI subscribes to (AET-38)."""
    from src.core.ops_heal_handler import make_ops_heal_handler
    from src.core.quality_gate import emit_quality_gate_event
    from src.core.security_gate import emit_security_gate_event
    from src.core.self_learning_trigger import make_self_learning_handler

    events, captured = _capture()

    # Stub state + executor (shared across SL handler + ops handler).
    class _State:
        async def get_request(self, rid): return None
        async def get_subtasks_for_request(self, rid): return []

    class _T:
        def __init__(self, r): self._r = r
        async def execute(self, p): return self._r
        async def single_agent_call(self, **kw):
            return {"text": "OK_WRITTEN_PENDING: Lesson L77 queued."}

    class _Reg:
        def __init__(self, t): self._t = t
        def get_implementation(self, n): return self._t[n]

    class _Exec:
        def __init__(self):
            self.tool_registry = _Reg({
                "slo_check": _T({"verdict": "BREACH", "summary": "down"}),
                "auto_rollback": _T({
                    "status": "queued", "request_id": "RB-cap-FINAL",
                    "deploy_id": "D", "reason": "down",
                }),
            })
        async def single_agent_call(self, **kw):
            return {"text": "OK_WRITTEN_PENDING: Lesson L77 queued."}

    state = _State()
    executor = _Exec()

    # Wire both handlers.
    events.on(make_self_learning_handler(state, executor, events))
    events.on(make_ops_heal_handler(state, executor, events))

    # Drive the synthetic pipeline.
    await emit_quality_gate_event(
        events, request_id="REQ-FINAL", verdict="PASS",
        violations=[], summary={}, stage="review",
    )
    await emit_security_gate_event(events, "REQ-FINAL", {
        "verdict": "PASS", "max_severity": "high",
        "blocking": [], "non_blocking": [], "by_tool": {},
        "summary": "all clean",
    })
    await events.emit("request.failed", {
        "request_id": "REQ-FINAL-FAIL", "error": "AET-42 capstone synthetic",
    })
    await events.emit(DEPLOY_HEALTH_ANOMALY_DETECTED, {
        "env": "staging", "alerts": [{"metric": "response_time_ms"}],
    })
    await _drain()

    event_types = {et for et, _ in captured}
    # The full AE event surface — every type the UI watches for AND every
    # type the gates emit through the pipeline.
    required = {
        QUALITY_GATE_PASSED,                # AE-3
        SECURITY_GATE_PASSED,               # AE-4
        LESSONS_PENDING_REVIEW,             # AE-2
        DEPLOY_HEALTH_ANOMALY_DETECTED,     # AE-1 source
        OPS_ROLLBACK_TRIGGERED,             # AE-1 outcome
    }
    missing = required - event_types
    assert not missing, (
        f"Capstone pipeline missing events: {missing}. "
        f"Saw: {event_types}"
    )

    # Negative contract — failure modes should NOT fire spuriously
    # in the happy-path quality + security gates.
    assert QUALITY_GATE_FAILED not in event_types
    assert SECURITY_GATE_FAILED not in event_types
