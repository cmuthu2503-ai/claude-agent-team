"""Phase AE-3 end-to-end smoke test for the quality_guardian gate (AET-08).

Pins the contract the rest of AE-3 hangs off:

  1. A clean diff passes the gate AND emits quality.gate.passed
  2. A print() violation in src/ BLOCKs AND emits quality.gate.failed
     with QR-001 in the violations list and its fix_hint in the
     rework feedback string
  3. The same print() in tests/** does NOT trigger QR-001 (exclude_files
     scoping works end-to-end through the gate)
  4. After a BLOCK, fixing the violation lets the gate pass on the
     next cycle (rework loop continues — no sticky-block bug)

Setup: build a `WorkflowRunner` with a real `PolicyCheckTool` pointed
at the production `config/quality-rules.yaml`, a real `EventEmitter`
that captures every emit, and a stub `AgentExecutor` (the gate code
doesn't call the executor — only the surrounding stages do — so a
no-op stub suffices). No SQLite needed.

Run via:
  docker compose exec backend pytest tests/test_quality_guardian_smoke.py -v
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.core.events import EventEmitter
from src.tools.policy_check import PolicyCheckTool
from src.workflows.runner import WorkflowRunner


# ── Helpers ───────────────────────────────────────────────────────────────


class _StubAgentExecutor:
    """No-op executor — the gate evaluator we're testing doesn't call
    execute_agent. Stages above the gate (development, testing) would
    call it but we never run them in these tests."""

    async def execute_agent(
        self, agent_id: str, request_id: str, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        raise AssertionError(
            "executor should not be called during gate-only tests; "
            f"got agent_id={agent_id}"
        )


def _make_runner() -> tuple[WorkflowRunner, EventEmitter, list[dict[str, Any]]]:
    """Build a runner + capture-list pair. Subscribes a queue + a
    background coroutine that drains it into the returned list. Returns
    (runner, emitter, captured_events).

    The caller MUST call `await _drain(events_queue)` (or
    `await asyncio.sleep(0)` a few times) before asserting on
    `captured_events` so the EventEmitter has a chance to push.
    """
    events = EventEmitter()
    tool = PolicyCheckTool()  # loads production config/quality-rules.yaml
    runner = WorkflowRunner(
        executor=_StubAgentExecutor(),
        get_policy_check_tool=lambda: tool,
        events=events,
    )
    captured: list[dict[str, Any]] = []
    queue = events.subscribe()

    async def _drain() -> None:
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=0.05)
                captured.append(ev)
            except asyncio.TimeoutError:
                return

    runner._test_drain = _drain  # type: ignore[attr-defined]
    return runner, events, captured


def _file_block(path: str, content: str) -> str:
    """Render an emission in the `### \\`path\\`` block format the
    runner's extractor expects. Mirrors the materializer's input shape."""
    return f"### `{path}`\n```python\n{content}\n```\n"


# ── Tests ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clean_diff_passes_gate_and_emits_passed_event() -> None:
    """Scenario (1): No violations → gate passes → quality.gate.passed
    event emitted with verdict=PASS and empty violations list."""
    runner, _events, captured = _make_runner()

    artifacts = {
        "backend_code": _file_block(
            "src/api/routes/clean.py",
            "import structlog\n"
            "logger = structlog.get_logger()\n"
            "\n"
            "def hello() -> dict:\n"
            "    logger.info('hello_called')\n"
            "    return {'ok': True}\n",
        ),
        # Agent prose verdicts — also clean.
        "review_report": "**Verdict: APPROVED**\nReview clean.",
        "arch_review_report": "**Verdict: APPROVED**\nArch clean.",
        "quality_report": "**Verdict: APPROVED**\nClean.",
        "tester_specialist_output": "All tests passed. PASS_RATE: 100%",
    }

    result = await runner._check_combined_gate(artifacts, "REQ-CLEAN")

    assert result["passed"] is True, f"Expected pass, got reason: {result['reason']}"
    await runner._test_drain()  # type: ignore[attr-defined]

    gate_events = [
        e for e in captured
        if e["type"] in ("quality.gate.passed", "quality.gate.failed")
    ]
    assert len(gate_events) == 1, f"Expected 1 gate event, got {len(gate_events)}"
    assert gate_events[0]["type"] == "quality.gate.passed"
    assert gate_events[0]["data"]["verdict"] == "PASS"
    # The event may carry info-severity violations (e.g. QR-006 fires
    # on small files as a non-blocking decomposition hint) — those do
    # NOT change the verdict to BLOCK or PASS_WITH_WARNINGS. We only
    # require that no enforce or warn-level rules fired.
    severities = {v["severity"] for v in gate_events[0]["data"]["violations"]}
    assert "enforce" not in severities, (
        f"Unexpected enforce-severity violations on clean diff: {gate_events[0]['data']['violations']}"
    )
    assert "warn" not in severities, (
        f"Unexpected warn-severity violations on clean diff: {gate_events[0]['data']['violations']}"
    )


@pytest.mark.asyncio
async def test_print_violation_blocks_with_qr001_in_feedback() -> None:
    """Scenario (2): print() in src/ → gate BLOCKS → quality.gate.failed
    fires with QR-001 in the violations list and the rule's fix_hint
    appears in the rework feedback so the agent has actionable guidance."""
    runner, _events, captured = _make_runner()

    artifacts = {
        "backend_code": _file_block(
            "src/api/routes/widgets.py",
            "def hello():\n"
            "    print('debug: hello')\n"
            "    return 42\n",
        ),
        # Prose verdicts all approve — only policy_check should fail.
        "review_report": "**Verdict: APPROVED**",
        "arch_review_report": "**Verdict: APPROVED**",
        "quality_report": "**Verdict: APPROVED**",
        "tester_specialist_output": "PASS_RATE: 100%",
    }

    result = await runner._check_combined_gate(artifacts, "REQ-PRINT")

    assert result["passed"] is False, "Expected gate to BLOCK on print()"
    assert "POLICY CHECK BLOCKED" in result["reason"], \
        "Expected POLICY CHECK BLOCKED header in rework feedback"
    assert "QR-001" in result["reason"], \
        f"Expected QR-001 cited in rework feedback, got: {result['reason'][:500]}"
    # The fix_hint should land in the feedback so the next cycle has
    # actionable guidance (this is the L20-style fix: the agent gets
    # the FIX, not just the failure).
    assert "logger.info" in result["reason"], \
        "Expected QR-001's fix_hint (use logger.info) in feedback"

    await runner._test_drain()  # type: ignore[attr-defined]

    failed_events = [e for e in captured if e["type"] == "quality.gate.failed"]
    assert len(failed_events) == 1, \
        f"Expected 1 quality.gate.failed event, got {len(failed_events)}"
    ev = failed_events[0]
    assert ev["data"]["verdict"] == "BLOCK"
    assert ev["data"]["request_id"] == "REQ-PRINT"
    rule_ids = [v["rule_id"] for v in ev["data"]["violations"]]
    assert "QR-001" in rule_ids, f"Expected QR-001 in violations, got {rule_ids}"
    # Each violation carries the documented fields per the AET-05 contract.
    qr001 = next(v for v in ev["data"]["violations"] if v["rule_id"] == "QR-001")
    for required in ("rule_name", "severity", "target_path",
                     "snippet", "rationale", "fix_hint", "lesson_ref"):
        assert required in qr001, f"violation missing field: {required}"
    assert qr001["severity"] == "enforce"
    assert qr001["lesson_ref"] == "L11"


@pytest.mark.asyncio
async def test_print_in_tests_does_not_trigger_qr001() -> None:
    """Scenario (3): The same print() emission in tests/** doesn't fire
    QR-001 (exclude_files: ['tests/**'] in config/quality-rules.yaml).
    End-to-end confirmation that glob exclusion works through the gate."""
    runner, _events, _captured = _make_runner()

    artifacts = {
        # backend_tests goes through the runner's emission extractor
        # with agent_id=tester_specialist; the file path lives under
        # tests/ so QR-001's exclude_files glob should skip it.
        "backend_tests": _file_block(
            "tests/test_widgets.py",
            "def test_widget():\n"
            "    print('debugging the test')\n"
            "    assert True\n",
        ),
        "review_report": "**Verdict: APPROVED**",
        "arch_review_report": "**Verdict: APPROVED**",
        "quality_report": "**Verdict: APPROVED**",
        "tester_specialist_output": "PASS_RATE: 100%",
    }

    result = await runner._check_combined_gate(artifacts, "REQ-TESTPRINT")

    # The print() is in tests/, which is excluded — QR-001 shouldn't
    # fire. policy_check returns PASS or PASS_WITH_WARNINGS (the
    # tests/** file may still trip a warn-level rule like QR-006
    # over-decomposed primary_file, which is fine).
    assert result["passed"] is True, (
        f"Expected pass (print() in tests/** is excluded), "
        f"got reason: {result['reason'][:400]}"
    )


@pytest.mark.asyncio
async def test_rework_loop_passes_after_violation_is_fixed() -> None:
    """Scenario (4): A first cycle BLOCKs on print(). The agent fixes
    it (replaces with logger.info). The next gate evaluation passes
    cleanly — no sticky-block bug, no false positives carrying over."""
    runner, _events, captured = _make_runner()

    # First cycle — print() emission, expect BLOCK.
    bad_artifacts = {
        "backend_code": _file_block(
            "src/api/routes/widgets.py",
            "def hello():\n    print('hi')\n    return 42\n",
        ),
        "review_report": "**Verdict: APPROVED**",
        "arch_review_report": "**Verdict: APPROVED**",
        "quality_report": "**Verdict: APPROVED**",
        "tester_specialist_output": "PASS_RATE: 100%",
    }
    first = await runner._check_combined_gate(bad_artifacts, "REQ-REWORK")
    assert first["passed"] is False
    assert "QR-001" in first["reason"]

    # Second cycle — same file, fixed content. policy_check should now
    # return PASS (or PASS_WITH_WARNINGS — non-blocking). Gate passes.
    fixed_artifacts = {
        "backend_code": _file_block(
            "src/api/routes/widgets.py",
            "import structlog\n"
            "logger = structlog.get_logger()\n"
            "\n"
            "def hello():\n"
            "    logger.info('hello_called')\n"
            "    return 42\n",
        ),
        "review_report": "**Verdict: APPROVED**",
        "arch_review_report": "**Verdict: APPROVED**",
        "quality_report": "**Verdict: APPROVED**",
        "tester_specialist_output": "PASS_RATE: 100%",
    }
    second = await runner._check_combined_gate(fixed_artifacts, "REQ-REWORK")
    assert second["passed"] is True, (
        f"Expected rework cycle 2 to pass after fix, "
        f"got reason: {second['reason'][:400]}"
    )

    await runner._test_drain()  # type: ignore[attr-defined]

    # Both events should have fired, in order.
    gate_events = [
        e for e in captured
        if e["type"] in ("quality.gate.passed", "quality.gate.failed")
    ]
    assert len(gate_events) == 2, \
        f"Expected 2 gate events (BLOCK then PASS), got {len(gate_events)}"
    assert gate_events[0]["type"] == "quality.gate.failed"
    assert gate_events[0]["data"]["verdict"] == "BLOCK"
    assert gate_events[1]["type"] == "quality.gate.passed"
    assert gate_events[1]["data"]["verdict"] in ("PASS", "PASS_WITH_WARNINGS")


@pytest.mark.asyncio
async def test_no_emissions_does_not_block() -> None:
    """Edge case: a workflow that emitted nothing code-y (e.g. a
    research workflow with no `### \\`path\\`` blocks) shouldn't block
    at the gate just because policy_check had nothing to evaluate.
    Defensive against a false-positive sticky-block bug."""
    runner, _events, captured = _make_runner()

    artifacts = {
        # No backend_code / frontend_code — only prose research output.
        "research_report": "Findings: ...",
        "review_report": "**Verdict: APPROVED**",
        "arch_review_report": "**Verdict: APPROVED**",
        "quality_report": "**Verdict: APPROVED**",
        "tester_specialist_output": "PASS_RATE: 100%",
    }
    result = await runner._check_combined_gate(artifacts, "REQ-NORES")
    assert result["passed"] is True, (
        f"Expected no-emissions workflow to pass, got reason: {result['reason']}"
    )


@pytest.mark.asyncio
async def test_runner_without_policy_check_tool_falls_back_to_legacy() -> None:
    """Edge case: when policy_check is unavailable (e.g. rules YAML
    failed to load at boot, AET-04's try/except disabled the tool),
    the gate must fall back to the legacy prose-only behavior rather
    than block the entire workflow. Loud fallback documented in the
    runner's `policy_check_unavailable` log line."""
    events = EventEmitter()
    runner = WorkflowRunner(
        executor=_StubAgentExecutor(),
        get_policy_check_tool=lambda: None,  # tool unavailable
        events=events,
    )
    # The artifacts have a print() — would normally BLOCK, but without
    # policy_check the gate falls back to prose verdicts only.
    artifacts = {
        "backend_code": _file_block(
            "src/api/routes/widgets.py",
            "def hello():\n    print('hi')\n",
        ),
        "review_report": "**Verdict: APPROVED**",
        "arch_review_report": "**Verdict: APPROVED**",
        "quality_report": "**Verdict: APPROVED**",
        "tester_specialist_output": "PASS_RATE: 100%",
    }
    result = await runner._check_combined_gate(artifacts, "REQ-NOTOOL")
    assert result["passed"] is True, (
        "Expected legacy fallback to pass (prose verdicts all APPROVED) "
        "when policy_check is unavailable. "
        f"Got reason: {result['reason']}"
    )
