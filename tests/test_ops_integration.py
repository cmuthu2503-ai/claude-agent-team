"""Integration smoke test — full post-deploy ops monitoring cycle.

Tests the complete chain:
  supervisor calls POST /api/v1/ops/monitor
  → orchestrator.trigger_ops_monitor() runs as background task
  → ops_heal_agent executes (mocked)
  → ops.healthy / ops.issue_detected event emitted
  → event appears in the EventEmitter subscriber queue

This test uses a real FastAPI TestClient + real EventEmitter to validate
the end-to-end wiring without the LLM. The ops_heal_agent execution is
mocked at the executor level so no Anthropic call is made.

TC-INT-01  Healthy verdict propagates ops.healthy event
TC-INT-02  Unhealthy verdict propagates ops.issue_detected event
TC-INT-03  Missing orchestrator returns graceful "no orchestrator" response
TC-INT-04  judge.py evaluate_deployment carries quality_risk through to user message
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.ops import router as ops_router
from src.core.events import (
    OPS_HEALTHY,
    OPS_ISSUE_DETECTED,
    EventEmitter,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_app(orchestrator=None, state_store=None) -> FastAPI:
    app = FastAPI()
    app.include_router(ops_router)
    if orchestrator is not None:
        app.state.orchestrator = orchestrator
    if state_store is not None:
        app.state.state_store = state_store
    return app


# ──────────────────────────────────────────────────────────────────────────────
# TC-INT-01  Healthy verdict propagates ops.healthy event
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_healthy_verdict_emits_ops_healthy_event():
    """trigger_ops_monitor → ops_heal_agent outputs HEALTHY → ops.healthy emitted."""
    emitter = EventEmitter()
    received_events: list[dict] = []

    async def capture(event_type: str, data: dict) -> None:
        received_events.append({"type": event_type, "data": data})

    emitter.on(capture)

    # Build a mock orchestrator that delegates execution to a mock executor
    # and has a real EventEmitter
    from src.core.orchestrator import Orchestrator

    mock_exec = AsyncMock()
    mock_exec.execute_agent = AsyncMock(return_value={
        "ops_heal_agent_output": (
            "## Ops Health Report\n\n"
            "**Overall: ✅ HEALTHY** — all services operational.\n"
        )
    })

    orch = MagicMock(spec=Orchestrator)
    orch.events = emitter
    orch.execute_agent = mock_exec.execute_agent

    # Use the real trigger_ops_monitor (not mocked) — we want to test the
    # orchestrator method's event-emission logic
    from src.core.orchestrator import Orchestrator as RealOrch
    bound_method = RealOrch.trigger_ops_monitor.__get__(orch)
    await bound_method("REQ-INT-01", "dep-int-01")

    assert any(e["type"] == OPS_HEALTHY for e in received_events), (
        f"Expected ops.healthy event, got: {[e['type'] for e in received_events]}"
    )
    healthy_event = next(e for e in received_events if e["type"] == OPS_HEALTHY)
    assert healthy_event["data"]["verdict"] == "HEALTHY"
    assert healthy_event["data"]["request_id"] == "REQ-INT-01"


# ──────────────────────────────────────────────────────────────────────────────
# TC-INT-02  Unhealthy verdict propagates ops.issue_detected event
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unhealthy_verdict_emits_ops_issue_detected_event():
    """trigger_ops_monitor → ops_heal_agent outputs UNHEALTHY → ops.issue_detected."""
    emitter = EventEmitter()
    received: list[dict] = []
    emitter.on(lambda t, d: received.append({"type": t}) or asyncio.coroutine(lambda: None)())

    # Patch with a proper async handler
    async def capture(event_type: str, data: dict) -> None:
        received.append({"type": event_type, "data": data})

    emitter._handlers = [capture]

    mock_exec = AsyncMock()
    mock_exec.execute_agent = AsyncMock(return_value={
        "ops_heal_agent_output": (
            "## Ops Health Report\n\n"
            "**Overall: ❌ UNHEALTHY** — backend not responding.\n"
            "Remediation: restart the backend container.\n"
        )
    })

    from src.core.orchestrator import Orchestrator as RealOrch
    orch = MagicMock()
    orch.events = emitter
    orch.execute_agent = mock_exec.execute_agent

    bound_method = RealOrch.trigger_ops_monitor.__get__(orch)
    await bound_method("REQ-INT-02", "dep-int-02")

    assert any(e["type"] == OPS_ISSUE_DETECTED for e in received), (
        f"Expected ops.issue_detected, got: {[e['type'] for e in received]}"
    )
    issue_event = next(e for e in received if e["type"] == OPS_ISSUE_DETECTED)
    assert issue_event["data"]["verdict"] == "UNHEALTHY"


# ──────────────────────────────────────────────────────────────────────────────
# TC-INT-03  Missing orchestrator returns graceful "no orchestrator" response
# ──────────────────────────────────────────────────────────────────────────────

def test_ops_monitor_no_orchestrator_graceful():
    """POST /api/v1/ops/monitor returns 200 with note when orchestrator is absent."""
    app = _make_app()  # no orchestrator wired
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/ops/monitor",
            json={"request_id": "REQ-NO-ORCH", "deployment_id": "dep-no-orch"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "accepted"
    assert "note" in data or "request_id" in data  # graceful response, not a 500


# ──────────────────────────────────────────────────────────────────────────────
# TC-INT-04  judge.py evaluate_deployment carries quality_risk through user msg
# ──────────────────────────────────────────────────────────────────────────────

def test_judge_quality_risk_appears_in_user_message():
    """evaluate_deployment with quality_risk='high' must include it in the LLM prompt.

    We patch the AnthropicAWS client and inspect the messages payload rather
    than actually calling the API — this validates the wiring without network I/O.
    """
    import sys
    import pathlib
    # Ensure supervisor/ is importable
    project_root = str(pathlib.Path(__file__).resolve().parents[1])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from supervisor.judge import evaluate_deployment

    captured_kwargs: list[dict] = []

    class FakeResponse:
        content = [MagicMock(type="text", text='{"strategy":"deploy_staging_only","risk":"high","reasoning":"Quality Guardian escalated.","rollback_plan":"revert"}')]

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                captured_kwargs.append(kwargs)
                return FakeResponse()

    # Patch out AnthropicAWS (imported locally in evaluate_deployment via
    # `from anthropic import AnthropicAWS`) and provide fake credentials.
    with patch.dict("os.environ", {
        "ANTHROPIC_AWS_API_KEY": "fake-key",
        "ANTHROPIC_AWS_WORKSPACE_ID": "fake-ws",
    }):
        # The local import path is `anthropic.AnthropicAWS`
        with patch("anthropic.AnthropicAWS", return_value=FakeClient()):
            result = evaluate_deployment(
                commit_sha="abc123",
                request_id="REQ-INT-04",
                files_committed=["src/api/routes/users.py"],
                rollback_sha="def456",
                quality_risk="high",
            )

    assert result.from_llm is True
    assert result.strategy == "deploy_staging_only"

    # Verify quality_risk appeared in the user message
    assert len(captured_kwargs) == 1
    user_content = captured_kwargs[0]["messages"][0]["content"]
    assert "quality_risk" in user_content.lower() or "high" in user_content.lower(), (
        f"Expected quality_risk=high in user message, got: {user_content[:300]}"
    )
