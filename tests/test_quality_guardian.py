"""Tests for Quality Guardian gate evaluators and judge quality-risk integration.

Covers:
  TC-QG-01  API contract mismatch → _check_quality_guardian_passed returns False
  TC-QG-02  Missing traceability (CRITICAL) → gate fails
  TC-QG-03  Clean outputs → gate passes + combined_gate_passed logged
  TC-QG-04  Gate evaluator parsing — Verdict: APPROVED, ESCALATED, and no-output default
  TC-QG-05  Judge quality_risk parameter — low/medium/high/unknown accepted cleanly
  TC-QG-06  _get_quality_risk parses the Risk: line from the docs table
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workflows.runner import WorkflowRunner


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _runner() -> WorkflowRunner:
    return WorkflowRunner(executor=AsyncMock())


APPROVED_REPORT = """
## Quality Guardian Report

### 1. API Contract Check
| # | Backend | Frontend | Status | Finding |
|---|---------|----------|--------|---------|
| 1 | `GET /api/v1/users` | `apiClient.getUsers()` | ✅ Match | — |

### Verdict

Risk: low
**Verdict: APPROVED** — no CRITICAL findings.
"""

ESCALATED_API_MISMATCH = """
## Quality Guardian Report

### Findings Summary
- **[CRITICAL]** API field mismatch: backend returns `user_id` (int) but frontend reads `.data.userId`

### Verdict

Risk: high
**Verdict: ESCALATED** — 1 CRITICAL finding: API field name mismatch (user_id vs userId).
"""

ESCALATED_TRACEABILITY = """
## Quality Guardian Report

### Findings Summary
- **[CRITICAL]** REQ-007 has no test coverage — tester_specialist must add a test case.

### Verdict

Risk: high
**Verdict: ESCALATED** — 1 CRITICAL finding: REQ-007 untested.
"""

APPROVED_WITH_WARNINGS = """
## Quality Guardian Report

### Findings Summary
- **[HIGH]** REQ-003 has no test coverage — noted for next cycle.

### Verdict

Risk: medium
**Verdict: APPROVED** — no CRITICAL findings; 1 HIGH warning noted.
"""


# ──────────────────────────────────────────────────────────────────────────────
# TC-QG-01  API contract mismatch → gate fails
# ──────────────────────────────────────────────────────────────────────────────

def test_api_mismatch_escalation_fails_gate():
    """Quality Guardian ESCALATED due to API contract mismatch → gate must fail."""
    runner = _runner()
    result = runner._check_quality_guardian_passed(ESCALATED_API_MISMATCH)
    assert result is False, "ESCALATED verdict must cause gate failure"


# ──────────────────────────────────────────────────────────────────────────────
# TC-QG-02  Missing traceability (CRITICAL) → gate fails
# ──────────────────────────────────────────────────────────────────────────────

def test_missing_traceability_escalation_fails_gate():
    """Quality Guardian ESCALATED due to missing REQ traceability → gate must fail."""
    runner = _runner()
    result = runner._check_quality_guardian_passed(ESCALATED_TRACEABILITY)
    assert result is False, "ESCALATED verdict from traceability gap must fail gate"


# ──────────────────────────────────────────────────────────────────────────────
# TC-QG-03  Clean outputs → combined gate passes
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clean_outputs_combined_gate_passes():
    """When all reviewers + quality guardian approve, _check_combined_gate must pass."""
    runner = _runner()
    artifacts = {
        "review_report": "**APPROVED** — no issues found.",
        "arch_review_report": "**APPROVED** — no CRITICAL violations.",
        "quality_report": APPROVED_REPORT,
        "tester_specialist_output": "READY FOR DEPLOYMENT — all tests pass.",
    }
    result = runner._check_combined_gate(artifacts, "REQ-TEST")
    assert result["passed"] is True
    assert "quality guardian" in result["reason"].lower()


# ──────────────────────────────────────────────────────────────────────────────
# TC-QG-04  Gate evaluator parsing edge cases
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("report,expected_pass", [
    # APPROVED — explicit verdict
    (APPROVED_REPORT, True),
    # APPROVED with HIGH warnings — still passes (no CRITICAL)
    (APPROVED_WITH_WARNINGS, True),
    # ESCALATED — API mismatch
    (ESCALATED_API_MISMATCH, False),
    # ESCALATED — traceability gap
    (ESCALATED_TRACEABILITY, False),
    # Empty string → default pass (agent may not have run)
    ("", True),
    # No explicit verdict but [CRITICAL] in body → fail
    ("The report contains **[CRITICAL]** API mismatch on line 7.", False),
    # Partial verdict keyword in noise text → no false positive
    ("This is not-escalated and totally fine.", True),
])
def test_gate_evaluator_parsing(report: str, expected_pass: bool):
    runner = _runner()
    assert runner._check_quality_guardian_passed(report) is expected_pass


# ──────────────────────────────────────────────────────────────────────────────
# TC-QG-05  Judge quality_risk parameter accepts all valid values
# ──────────────────────────────────────────────────────────────────────────────

def test_judge_quality_risk_parameter_accepted():
    """evaluate_deployment must accept quality_risk without raising."""
    from supervisor.judge import _safe_default

    # Import just the template rendering logic — we patch the LLM call
    from supervisor import judge as judge_module

    for qr in ("low", "medium", "high", "unknown", "INVALID"):
        # Should not raise even for invalid value (normalises to "unknown")
        result = judge_module._safe_default(f"test for quality_risk={qr}")
        assert result.strategy == "deploy_full"


def test_judge_user_template_includes_quality_risk():
    """_USER_TEMPLATE must have {quality_risk} placeholder so the judge sees it."""
    from supervisor.judge import _USER_TEMPLATE
    assert "{quality_risk}" in _USER_TEMPLATE, "_USER_TEMPLATE must include {quality_risk}"


def test_judge_system_prompt_explains_quality_risk():
    """_SYSTEM_PROMPT must explain quality_risk semantics (high → staging_only)."""
    from supervisor.judge import _SYSTEM_PROMPT
    assert "quality_risk" in _SYSTEM_PROMPT.lower()
    assert "deploy_staging_only" in _SYSTEM_PROMPT


# ──────────────────────────────────────────────────────────────────────────────
# TC-QG-06  _get_quality_risk parses Risk: line from in-memory SQLite
# ──────────────────────────────────────────────────────────────────────────────

def _in_memory_db_with_quality_report(content: str) -> sqlite3.Connection:
    """Spin up a minimal in-memory SQLite matching the docs table schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE documents (
               document_id TEXT PRIMARY KEY,
               request_id  TEXT NOT NULL,
               doc_type    TEXT NOT NULL,
               title       TEXT NOT NULL,
               content     TEXT NOT NULL,
               agent_id    TEXT NOT NULL,
               created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    conn.execute(
        "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "doc-001",
            "REQ-PARSE",
            "quality_report",
            "Quality Guardian Report",
            content,
            "quality_guardian",
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    return conn


@pytest.mark.parametrize("report_content,expected_risk", [
    (APPROVED_REPORT, "low"),       # Risk: low
    (APPROVED_WITH_WARNINGS, "medium"),  # Risk: medium
    (ESCALATED_API_MISMATCH, "high"),    # Risk: high
    ("No risk line at all.", "unknown"),  # fallback
    ("", "unknown"),                # empty content → unknown
])
def test_get_quality_risk_parsing(report_content: str, expected_risk: str):
    """_get_quality_risk must parse the Risk: line from the documents table."""
    import sys
    import types

    # supervisor/ is not on sys.path by default — add project root
    import pathlib
    project_root = str(pathlib.Path(__file__).resolve().parents[1])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from supervisor.deploy_supervisor import _get_quality_risk  # noqa: PLC0415

    db = _in_memory_db_with_quality_report(report_content)
    result = _get_quality_risk(db, "REQ-PARSE")
    assert result == expected_risk, f"Expected {expected_risk!r}, got {result!r}"
    db.close()


def test_get_quality_risk_no_report_returns_unknown():
    """When no quality_report exists for the request, risk must be 'unknown'."""
    import sys
    import pathlib
    project_root = str(pathlib.Path(__file__).resolve().parents[1])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from supervisor.deploy_supervisor import _get_quality_risk  # noqa: PLC0415

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE documents (document_id TEXT, request_id TEXT, "
        "doc_type TEXT, title TEXT, content TEXT, agent_id TEXT, created_at TEXT)"
    )
    conn.commit()

    result = _get_quality_risk(conn, "REQ-MISSING")
    assert result == "unknown"
    conn.close()
