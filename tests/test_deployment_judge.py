"""Tests for the hybrid deployment flow.

Covers:
  - supervisor/judge.py's response parser (offline; no LLM call)
  - supervisor/judge.py's safe-default behaviour when credentials are missing
  - src/tools/deploy_tools.py::WaitForDeploymentTool (offline; uses a fake state store)
"""

import json
import sys
from pathlib import Path

# The judge module lives in supervisor/, not src/, so we manually add it to
# sys.path before importing — same way the supervisor container does at runtime.
_SUPERVISOR_DIR = Path(__file__).resolve().parents[1] / "supervisor"
if str(_SUPERVISOR_DIR) not in sys.path:
    sys.path.insert(0, str(_SUPERVISOR_DIR))

import judge  # noqa: E402

from src.models.base import DeploymentState  # noqa: E402
from src.tools.deploy_tools import WaitForDeploymentTool  # noqa: E402

# ── judge.py response parser ──────────────────────────────────────────────────


def test_judge_parses_clean_json():
    text = json.dumps({
        "strategy": "deploy_full",
        "risk": "low",
        "reasoning": "small change",
        "rollback_plan": "git revert",
    })
    result = judge._parse_response(text)
    assert result.strategy == "deploy_full"
    assert result.risk == "low"
    assert result.reasoning == "small change"
    assert result.rollback_plan == "git revert"
    assert result.from_llm is True


def test_judge_strips_markdown_fences():
    text = '```json\n{"strategy":"skip","risk":"low","reasoning":"docs only"}\n```'
    result = judge._parse_response(text)
    assert result.strategy == "skip"
    assert result.from_llm is True


def test_judge_extracts_json_from_preamble():
    text = (
        "Here is my decision:\n"
        '{"strategy":"deploy_staging_only","risk":"high","reasoning":"new auth code"}'
    )
    result = judge._parse_response(text)
    assert result.strategy == "deploy_staging_only"
    assert result.risk == "high"


def test_judge_coerces_unknown_risk_to_medium():
    text = '{"strategy":"deploy_full","risk":"WILDLY_INVALID","reasoning":"x"}'
    result = judge._parse_response(text)
    assert result.strategy == "deploy_full"
    assert result.risk == "medium"  # coerced
    assert result.from_llm is True


def test_judge_safe_defaults_on_invalid_strategy():
    """Unknown strategy is unsafe to act on — fall back to deploy_full with a clear reason."""
    text = '{"strategy":"yolo_deploy","risk":"low","reasoning":"trust me"}'
    result = judge._parse_response(text)
    assert result.strategy == "deploy_full"
    assert result.from_llm is False
    assert "unknown strategy" in result.reasoning.lower()


def test_judge_safe_defaults_on_malformed_json():
    result = judge._parse_response("this isn't JSON at all")
    assert result.strategy == "deploy_full"
    assert result.from_llm is False


def test_judge_safe_defaults_on_empty_response():
    result = judge._parse_response("")
    assert result.from_llm is False
    assert result.strategy == "deploy_full"


def test_judge_skips_no_credentials(monkeypatch):
    """evaluate_deployment() returns safe-default without ever touching the SDK
    when the credentials env vars aren't set."""
    monkeypatch.delenv("ANTHROPIC_AWS_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AWS_WORKSPACE_ID", raising=False)
    result = judge.evaluate_deployment(
        commit_sha="abc",
        request_id="REQ-XYZ",
        files_committed=["x.py"],
    )
    assert result.strategy == "deploy_full"
    assert result.from_llm is False
    assert "credentials" in result.reasoning.lower()


# ── WaitForDeploymentTool ────────────────────────────────────────────────────


class _FakeState:
    """Test double for the StateStore — only implements what the tool calls."""

    def __init__(self, *, snapshots: list[DeploymentState | None]):
        # Each call to get_deployment_state_for_request consumes one snapshot
        # from this queue. Lets a test simulate "row appears after 2 polls,
        # then transitions through states, then becomes terminal".
        self._snapshots = list(snapshots)

    async def get_deployment_state_for_request(self, request_id: str):
        if self._snapshots:
            return self._snapshots.pop(0)
        # Once snapshots are exhausted, the test should've finished; return None
        # which the tool will treat as "row not appeared yet."
        return None


def _make_state(step: str, **overrides) -> DeploymentState:
    defaults = dict(
        deployment_id="dep-1",
        request_id="REQ-WAIT-TEST",
        commit_sha="abc1234",
        current_step=step,
        step_history=[],
        files_committed=[],
    )
    defaults.update(overrides)
    return DeploymentState(**defaults)


async def test_wait_returns_immediately_on_terminal():
    """If the deployment row is already terminal on the first poll, return at once."""
    tool = WaitForDeploymentTool(state=_FakeState(snapshots=[
        _make_state("completed", strategy="deploy_full", risk="low",
                    strategy_reasoning="all clear"),
    ]))
    result_text = await tool.execute({"request_id": "REQ-WAIT-TEST", "timeout": 10})
    payload = json.loads(result_text)
    assert payload["status"] == "TERMINAL"
    assert payload["current_step"] == "completed"
    assert payload["strategy"] == "deploy_full"
    assert payload["strategy_reasoning"] == "all clear"


async def test_wait_returns_terminal_for_failed():
    """`failed` is terminal too — agent should observe + report, not keep polling."""
    tool = WaitForDeploymentTool(state=_FakeState(snapshots=[
        _make_state("failed", error_message="staging healthcheck blew up"),
    ]))
    result_text = await tool.execute({"request_id": "REQ-WAIT-TEST", "timeout": 10})
    payload = json.loads(result_text)
    assert payload["status"] == "TERMINAL"
    assert payload["current_step"] == "failed"
    assert payload["error_message"] == "staging healthcheck blew up"


async def test_wait_on_hold_is_terminal():
    tool = WaitForDeploymentTool(state=_FakeState(snapshots=[
        _make_state("on_hold", strategy="hold", strategy_reasoning="risky friday"),
    ]))
    result_text = await tool.execute({"request_id": "REQ-WAIT-TEST", "timeout": 10})
    payload = json.loads(result_text)
    assert payload["current_step"] == "on_hold"
    assert payload["strategy"] == "hold"


async def test_wait_returns_indeterminate_when_no_row():
    """If no deployment_states row ever appears, the tool reports INDETERMINATE
    so the agent can write a truthful 'no deploy happened' report."""
    tool = WaitForDeploymentTool(state=_FakeState(snapshots=[None]))
    # Short timeout — we don't want this test to actually wait 10 minutes.
    # The tool will poll every 5s; we need timeout >= 30 (the tool's own floor).
    result_text = await tool.execute({"request_id": "REQ-NOTHING", "timeout": 30})
    payload = json.loads(result_text)
    assert payload["status"] == "INDETERMINATE"
    assert "no deployment_states row" in payload["reason"].lower()


async def test_wait_errors_without_state():
    """Tool without a state store can't observe the deploy — must say so clearly,
    NOT silently return success."""
    tool = WaitForDeploymentTool(state=None)
    result = await tool.execute({"request_id": "REQ-X"})
    assert "ERROR" in result
    assert "state store unavailable" in result.lower()


async def test_wait_errors_without_request_id():
    tool = WaitForDeploymentTool(state=_FakeState(snapshots=[]))
    result = await tool.execute({"request_id": ""})
    assert "ERROR" in result
    assert "request_id" in result.lower()


def test_wait_tool_schema_advertises_request_id():
    """LLM-visible schema must require request_id, otherwise the agent could
    call the tool without context and silently wait on a phantom deploy."""
    tool = WaitForDeploymentTool(state=None)
    sch = tool.schema()
    assert sch["name"] == "wait_for_deployment"
    assert "request_id" in sch["input_schema"]["required"]
