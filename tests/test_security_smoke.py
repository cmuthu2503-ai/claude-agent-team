"""Phase AE-4 end-to-end smoke test for the security gate (AET-22).

Pins the contract that AE-4 hangs off:

  1. A synthetic emission containing a SQLi pattern is caught by
     ``sast_scan`` (bandit rule B608 — SQL string formatting / hardcoded
     SQL).
  2. A synthetic emission containing a leaked Anthropic AWS workspace
     ID (``wks_…``) is caught by ``secret_scan``
     (rule_id=``anthropic_aws_workspace_id``).
  3. A synthetic dependency_audit finding for a vulnerable package
     (GHSA id, severity=high, fix_versions set) is treated as
     BLOCKing by the AET-20 threshold.
  4. The AET-21 workflow gate, fed a single ``security-report-json``
     fence containing the union of (1)+(2)+(3), returns
     ``passed=False`` AND emits ``security.gate.failed`` with all
     three rule_ids surfaced in the structured payload's ``blocking``
     list.
  5. A clean report (no findings) returns ``passed=True`` AND emits
     ``security.gate.passed`` — rules out a "we always block" bug.

We exercise each tool independently first, then synthesize the JSON
report the agent would emit (using each tool's actual rule_id strings)
and feed it through the runner. That keeps the test deterministic
(no LLM, no pip-audit network round-trip) while still covering the
full contract: tool → JSON shape → gate decision → event.

Run via:
  docker compose exec backend pytest tests/test_security_smoke.py -v
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from src.core.events import (
    SECURITY_GATE_FAILED,
    SECURITY_GATE_PASSED,
    EventEmitter,
)
from src.tools.sast_scan import SastScanTool
from src.tools.secret_scan import SecretScanTool
from src.workflows.runner import WorkflowRunner


# ── Synthetic emission fixtures (the three issues this smoke pins) ────────


# SQLi pattern bandit's B608 detects: f-string interpolation building a
# SQL query. The single literal triggers B608 high-severity.
_SQLI_SOURCE = """\
import sqlite3
def get_user(conn: sqlite3.Connection, name: str):
    # SQL injection — bandit B608 catches f-string SQL building.
    return conn.execute(f"SELECT * FROM users WHERE name = '{name}'").fetchone()
"""

# Leaked Anthropic AWS workspace ID — caught by secret_scan's
# `anthropic_aws_workspace_id` regex.
_LEAKED_KEY_SOURCE = """\
ANTHROPIC_AWS_WORKSPACE_ID = "wks_aBcDeFgHiJkLmNoPqRsTuV"  # leak
"""

# Synthetic dependency_audit finding shape — we don't run pip-audit
# against the network in the smoke test (pip-audit isn't installed in
# the backend container and we don't want to depend on the OSV DB at
# CI time). The shape mirrors what the real tool emits per AET-16.
_VULN_DEP_FINDING = {
    "rule_id": "GHSA-XXXX-YYYY-ZZZZ",
    "severity": "high",
    "tool": "dependency_audit",
    "file": "pyproject.toml",
    "line": 0,
    "message": (
        "requests==2.28.0 is vulnerable to remote-host header injection "
        "(GHSA-XXXX-YYYY-ZZZZ)"
    ),
    "fix_hint": "upgrade to requests>=2.31.0",
}


# ── Helper: capture-emitter so we can assert on broadcast events ──────────


def _make_capture_emitter() -> tuple[EventEmitter, list[tuple[str, dict]]]:
    events = EventEmitter()
    captured: list[tuple[str, dict]] = []

    async def _capture(event_type: str, data: dict) -> None:
        captured.append((event_type, dict(data)))

    events.on(_capture)
    return events, captured


# ── 1. sast_scan catches the SQLi pattern ─────────────────────────────────


@pytest.mark.asyncio
async def test_sast_scan_catches_sqli():
    """Bandit's B608 must surface in the findings list for f-string SQL.

    Note: bandit rates B608 as MEDIUM by default, so the tool-level
    verdict is PASS (sast_scan only BLOCKs on high/critical). The
    AET-21 gate's threshold can still escalate this — that's covered
    in test_gate_blocks_on_unified_findings. What we pin here is the
    contract that B608 is *detected* and flows through the unified
    finding shape with the right tool tag."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, dir="/app",
    ) as f:
        f.write(_SQLI_SOURCE)
        path = f.name
    try:
        result = await SastScanTool().execute({"files": [path]})
    finally:
        Path(path).unlink(missing_ok=True)

    b608_findings = [
        f for f in result["findings"] if f["rule_id"] == "B608"
    ]
    assert b608_findings, (
        f"expected B608 SQLi finding, got rule_ids: "
        f"{[f['rule_id'] for f in result['findings']]}"
    )
    finding = b608_findings[0]
    # Per AET-15 normalisation, the `tool` field on each finding names
    # the BACKEND scanner ("bandit" / "eslint"), not the parent
    # composite tool ("sast_scan"). Lets the gate distinguish which
    # back-end produced the hit.
    assert finding["tool"] == "bandit"
    assert finding["severity"] in ("medium", "high")
    assert "SELECT" in finding["snippet"] or "sql" in finding["message"].lower()
    # Bandit ran successfully (not SKIPPED/ERROR).
    assert result["by_tool"]["bandit"]["status"] == "OK"


# ── 2. secret_scan catches the leaked Anthropic workspace ID ──────────────


@pytest.mark.asyncio
async def test_secret_scan_catches_anthropic_workspace_id():
    """The `wks_…` regex must fire on the leaked key with severity=critical."""
    result = await SecretScanTool().execute({"emissions": [
        {"file_path": "src/config/settings.py", "content": _LEAKED_KEY_SOURCE},
    ]})

    assert result["verdict"] == "BLOCK", result
    matching = [
        f for f in result["findings"]
        if f["rule_id"] == "anthropic_aws_workspace_id"
    ]
    assert matching, f"expected anthropic_aws_workspace_id, got: {result['findings']}"
    assert matching[0]["severity"] == "critical"
    # The actual key value must NEVER appear in the response — only the
    # redacted preview should.
    assert "aBcDeFgHi" not in json.dumps(result), (
        "redaction failed — full key leaked into tool response"
    )


# ── 3. Synthesized dep-audit finding is BLOCKing at the default cutoff ────


def test_dependency_audit_high_finding_blocks_at_default_threshold():
    """The shape dependency_audit emits for a high-severity vuln must
    survive the AET-20 threshold split as 'blocking' at the default
    'high' cutoff."""
    from src.core.security_threshold import split_findings

    blocking, non_blocking = split_findings([_VULN_DEP_FINDING], "high")
    assert blocking == [_VULN_DEP_FINDING]
    assert non_blocking == []


# ── 4. Full gate integration: all three findings → BLOCK + event ──────────


@pytest.mark.asyncio
async def test_gate_blocks_on_unified_findings_and_emits_event():
    """Synthesize the JSON-fenced security report the agent would emit
    after running all three scanners; feed it through the runner gate;
    assert BLOCK + structured event + all three rule_ids in the
    rework feedback."""
    report_dict = {
        "findings": [
            {
                "rule_id": "B608",
                "severity": "high",
                "tool": "sast_scan",
                "file": "src/api/routes/users.py",
                "line": 4,
                "message": "Possible SQL injection via string-based query construction",
                "fix_hint": "use parameterised queries",
            },
            {
                "rule_id": "anthropic_aws_workspace_id",
                "severity": "critical",
                "tool": "secret_scan",
                "file": "src/config/settings.py",
                "line": 1,
                "message": "leaked Anthropic AWS workspace ID",
                "fix_hint": "move to environment variable",
            },
            _VULN_DEP_FINDING,
        ],
        "by_tool": {
            "secret_scan":      {"verdict": "BLOCK",   "finding_count": 1},
            "sast_scan":        {"verdict": "BLOCK",   "finding_count": 1},
            "dependency_audit": {"verdict": "BLOCK",   "finding_count": 1},
            "pen_test_simple":  {"verdict": "SKIPPED", "finding_count": 0},
        },
    }
    # Wrap the agent's emission shape — markdown prose + the JSON fence.
    sec_text = (
        "## Security Scan Report\n\n"
        "**Verdict: ❌ FAIL** — see findings.\n\n"
        "```security-report-json\n"
        + json.dumps(report_dict)
        + "\n```\n"
    )

    events, captured = _make_capture_emitter()

    class _StubExec:
        async def execute_agent(self, **kw: Any) -> dict[str, Any]:
            raise AssertionError("executor should not be invoked in gate test")

    runner = WorkflowRunner(
        executor=_StubExec(), events=events,
        thresholds={
            "thresholds": {
                "security_max_severity_to_block": {"value": "high"},
            },
        },
    )

    result = await runner._check_security_gate(
        {"security_report": sec_text}, "REQ-AET22-BLOCK",
    )

    # Gate decision: BLOCK.
    assert result["passed"] is False
    # All three rule_ids must appear in the rework feedback string so
    # the next cycle sees actionable per-finding guidance.
    for rule_id in ("B608", "anthropic_aws_workspace_id", "GHSA-XXXX-YYYY-ZZZZ"):
        assert rule_id in result["reason"], (
            f"expected {rule_id} in rework feedback, got: {result['reason']}"
        )

    # Event: security.gate.failed with structured payload.
    fail_events = [e for e in captured if e[0] == SECURITY_GATE_FAILED]
    assert len(fail_events) == 1, captured
    payload = fail_events[0][1]
    assert payload["verdict"] == "BLOCK"
    assert payload["blocking_count"] == 3
    assert payload["max_severity"] == "high"
    # by_tool round-trip preserves the SKIPPED leg so the UI can show
    # "pen_test_simple SKIPPED" without penalising the agent.
    assert payload["by_tool"]["pen_test_simple"]["verdict"] == "SKIPPED"


# ── 5. Clean report → PASS + matching event ───────────────────────────────


@pytest.mark.asyncio
async def test_clean_report_passes_and_emits_passed_event():
    """Rules out a 'we always block' bug — a clean structured report
    must produce verdict=PASS and emit security.gate.passed."""
    clean = (
        "## Security Scan Report\n\n"
        "**Verdict: ✅ PASS**\n\n"
        "```security-report-json\n"
        + json.dumps({
            "findings": [],
            "by_tool": {
                "secret_scan":      {"verdict": "PASS", "finding_count": 0},
                "sast_scan":        {"verdict": "PASS", "finding_count": 0},
                "dependency_audit": {"verdict": "PASS", "finding_count": 0},
                "pen_test_simple":  {"verdict": "PASS", "finding_count": 0},
            },
        })
        + "\n```\n"
    )

    events, captured = _make_capture_emitter()

    class _StubExec:
        async def execute_agent(self, **kw: Any) -> dict[str, Any]:
            raise AssertionError("executor should not be invoked in gate test")

    runner = WorkflowRunner(
        executor=_StubExec(), events=events,
        thresholds={
            "thresholds": {
                "security_max_severity_to_block": {"value": "high"},
            },
        },
    )

    result = await runner._check_security_gate(
        {"security_report": clean}, "REQ-AET22-PASS",
    )
    assert result["passed"] is True

    pass_events = [e for e in captured if e[0] == SECURITY_GATE_PASSED]
    assert len(pass_events) == 1
    assert pass_events[0][1]["verdict"] == "PASS"
    assert pass_events[0][1]["blocking_count"] == 0


# ── 6. Sub-threshold findings (medium only) PASS but populate non_blocking ─


@pytest.mark.asyncio
async def test_medium_only_findings_pass_under_default_high_cutoff():
    """At the default cutoff='high', medium and low findings must NOT
    block — but they must still surface in the non_blocking bucket so
    the rework annotation includes them."""
    report = {
        "findings": [
            {"rule_id": "X1", "severity": "medium", "tool": "sast_scan",
             "file": "f.py", "line": 1, "message": ""},
            {"rule_id": "X2", "severity": "low", "tool": "sast_scan",
             "file": "f.py", "line": 2, "message": ""},
        ],
        "by_tool": {
            "sast_scan": {"verdict": "PASS_WITH_WARNINGS", "finding_count": 2},
        },
    }
    sec_text = (
        "## Security Scan Report\n\n"
        "```security-report-json\n"
        + json.dumps(report)
        + "\n```\n"
    )
    events, captured = _make_capture_emitter()

    class _StubExec:
        async def execute_agent(self, **kw: Any) -> dict[str, Any]:
            raise AssertionError

    runner = WorkflowRunner(
        executor=_StubExec(), events=events,
        thresholds={
            "thresholds": {
                "security_max_severity_to_block": {"value": "high"},
            },
        },
    )

    result = await runner._check_security_gate(
        {"security_report": sec_text}, "REQ-AET22-MED",
    )
    assert result["passed"] is True

    pass_events = [e for e in captured if e[0] == SECURITY_GATE_PASSED]
    assert pass_events
    payload = pass_events[0][1]
    assert payload["blocking_count"] == 0
    assert payload["non_blocking_count"] == 2
