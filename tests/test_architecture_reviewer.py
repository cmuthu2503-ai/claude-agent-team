"""
Tests for the architecture_reviewer agent's gate-evaluation logic.

We test the WorkflowRunner helper methods that parse the architecture_reviewer's
output text — these are the same methods that decide whether to approve or
block a commit. No LLM calls are made; all tests are purely textual.
"""

import pytest
from unittest.mock import AsyncMock

from src.workflows.runner import WorkflowRunner


# ---------------------------------------------------------------------------
# Fixture: a WorkflowRunner instance with all external dependencies mocked
# ---------------------------------------------------------------------------

@pytest.fixture
def runner():
    executor = AsyncMock()
    return WorkflowRunner(executor=executor)


# ---------------------------------------------------------------------------
# Test 1: Direct DB import in a route file → ARCH_VIOLATION
# ---------------------------------------------------------------------------

DIRECT_DB_IMPORT_REPORT = """
## Architecture Review Report

### Rules Checked

| # | Rule | Status | Finding |
|---|------|--------|---------|
| 1 | Layer boundary (no direct DB in routes) | ❌ CRITICAL | `src/api/routes/widgets.py` line 3: `import aiosqlite` |
| 2 | Endpoint registration (main.py) | ✅ Pass | All handlers registered |
| 3 | Frontend router (App.tsx) | ✅ Pass | All pages routed |
| 4 | Pydantic v2 compliance | ✅ Pass | No v1 patterns |
| 5 | Circular imports | ✅ Pass | No cycles detected |
| 6 | Config-system compliance | ✅ Pass | All references defined |

### Findings Detail

- **[CRITICAL]** `src/api/routes/widgets.py` line 3: `import aiosqlite`

### Verdict
**ARCH_VIOLATION** — 1 CRITICAL finding: replace `import aiosqlite` with StateStore call.
"""


def test_arch_reviewer_flags_direct_db_import(runner):
    """ARCH_VIOLATION keyword in output must fail the gate."""
    assert runner._check_arch_review_passed(DIRECT_DB_IMPORT_REPORT) is False


# ---------------------------------------------------------------------------
# Test 2: Missing include_router in main.py → ARCH_VIOLATION
# ---------------------------------------------------------------------------

MISSING_ROUTER_REPORT = """
## Architecture Review Report

### Rules Checked

| # | Rule | Status | Finding |
|---|------|--------|---------|
| 1 | Layer boundary (no direct DB in routes) | ✅ Pass | No violations |
| 2 | Endpoint registration (main.py) | ❌ CRITICAL | `widgets.router` defined in routes but no `include_router` in src/main.py |
| 3 | Frontend router (App.tsx) | ✅ Pass | All pages routed |
| 4 | Pydantic v2 compliance | ✅ Pass | No v1 patterns |
| 5 | Circular imports | ✅ Pass | No cycles detected |
| 6 | Config-system compliance | ✅ Pass | All references defined |

### Findings Detail

- **[CRITICAL]** `src/api/routes/widgets.py`: router defined but no `include_router` call found in `src/main.py`

### Verdict
**ARCH_VIOLATION** — 1 CRITICAL finding: add `app.include_router(widgets.router, prefix="/api/v1/widgets")` to src/main.py
"""


def test_arch_reviewer_flags_missing_include_router(runner):
    """ARCH_VIOLATION for missing route registration must fail the gate."""
    assert runner._check_arch_review_passed(MISSING_ROUTER_REPORT) is False


# ---------------------------------------------------------------------------
# Test 3: Missing React <Route> entry in App.tsx → ARCH_VIOLATION
# ---------------------------------------------------------------------------

MISSING_PAGE_ROUTE_REPORT = """
## Architecture Review Report

### Rules Checked

| # | Rule | Status | Finding |
|---|------|--------|---------|
| 1 | Layer boundary (no direct DB in routes) | ✅ Pass | No violations |
| 2 | Endpoint registration (main.py) | ✅ Pass | All handlers registered |
| 3 | Frontend router (App.tsx) | ❌ CRITICAL | `frontend/src/pages/NewDashboard.tsx` has no Route entry in App.tsx |
| 4 | Pydantic v2 compliance | ✅ Pass | No v1 patterns |
| 5 | Circular imports | ✅ Pass | No cycles detected |
| 6 | Config-system compliance | ✅ Pass | All references defined |

### Findings Detail

- **[CRITICAL]** `frontend/src/pages/NewDashboard.tsx` exists but no `<Route path="/new-dashboard"` found in `frontend/src/App.tsx`

### Verdict
**ARCH_VIOLATION** — 1 CRITICAL finding: add `<Route path="/new-dashboard" element={<NewDashboard />} />` to App.tsx
"""


def test_arch_reviewer_flags_missing_react_route(runner):
    """ARCH_VIOLATION for unrouted page component must fail the gate."""
    assert runner._check_arch_review_passed(MISSING_PAGE_ROUTE_REPORT) is False


# ---------------------------------------------------------------------------
# Test 4: Pydantic v1 patterns → HIGH (not blocking) → APPROVED
# ---------------------------------------------------------------------------

PYDANTIC_V1_REPORT = """
## Architecture Review Report

### Rules Checked

| # | Rule | Status | Finding |
|---|------|--------|---------|
| 1 | Layer boundary (no direct DB in routes) | ✅ Pass | No violations |
| 2 | Endpoint registration (main.py) | ✅ Pass | All handlers registered |
| 3 | Frontend router (App.tsx) | ✅ Pass | All pages routed |
| 4 | Pydantic v2 compliance | ⚠️ HIGH | `src/models/widget.py` line 12: `@validator` (use `@field_validator`) |
| 5 | Circular imports | ✅ Pass | No cycles detected |
| 6 | Config-system compliance | ✅ Pass | All references defined |

### Findings Detail

- **[HIGH]** `src/models/widget.py` line 12: `@validator` — Fix: replace with `@field_validator`

### Verdict
**APPROVED** — no CRITICAL findings (1 HIGH warning logged for next cycle).
"""


def test_arch_reviewer_approves_with_high_only_findings(runner):
    """HIGH findings are non-blocking — verdict must still be APPROVED."""
    assert runner._check_arch_review_passed(PYDANTIC_V1_REPORT) is True


# ---------------------------------------------------------------------------
# Test 5: Fully compliant code → APPROVED
# ---------------------------------------------------------------------------

CLEAN_CODE_REPORT = """
## Architecture Review Report

### Rules Checked

| # | Rule | Status | Finding |
|---|------|--------|---------|
| 1 | Layer boundary (no direct DB in routes) | ✅ Pass | No violations |
| 2 | Endpoint registration (main.py) | ✅ Pass | All handlers registered |
| 3 | Frontend router (App.tsx) | ✅ Pass | All pages routed |
| 4 | Pydantic v2 compliance | ✅ Pass | No v1 patterns |
| 5 | Circular imports | ✅ Pass | No cycles detected |
| 6 | Config-system compliance | ✅ Pass | All references defined |

### Verdict
**APPROVED** — no CRITICAL findings.
"""


def test_arch_reviewer_approves_clean_code(runner):
    """All rules passing must produce APPROVED and gate passes."""
    assert runner._check_arch_review_passed(CLEAN_CODE_REPORT) is True


# ---------------------------------------------------------------------------
# Test 6: No arch review output (agent not yet run) → pass by default
# ---------------------------------------------------------------------------

def test_arch_reviewer_passes_on_empty_output(runner):
    """Empty / missing output = pass by default (agent may not have run)."""
    assert runner._check_arch_review_passed("") is True
    assert runner._check_arch_review_passed(None) is True  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Test 7: Combined gate includes arch review in failure feedback
# ---------------------------------------------------------------------------

def test_combined_gate_includes_arch_violation_in_feedback(runner):
    """When arch review fails, _check_combined_gate must include the
    ARCHITECTURE VIOLATIONS section in the returned reason string."""
    artifacts = {
        "review_report": "## Code Review Report\n**APPROVED** — looks good.",
        "arch_review_report": MISSING_ROUTER_REPORT,
        "tester_specialist_output": "READY FOR DEPLOYMENT",
    }
    import asyncio
    result = runner._check_combined_gate(artifacts, request_id="REQ-TEST")
    assert result["passed"] is False
    assert "ARCHITECTURE VIOLATIONS" in result["reason"]
    assert "CRITICAL" in result["reason"]


# ---------------------------------------------------------------------------
# Test 8: Combined gate passes when all three reviewers approve
# ---------------------------------------------------------------------------

def test_combined_gate_passes_when_all_reviewers_approve(runner):
    """All three sub-gates (review, arch, test) green → combined gate passes."""
    artifacts = {
        "review_report": "## Code Review Report\n**APPROVED** — no issues.",
        "arch_review_report": CLEAN_CODE_REPORT,
        "tester_specialist_output": "READY FOR DEPLOYMENT",
    }
    result = runner._check_combined_gate(artifacts, request_id="REQ-TEST")
    assert result["passed"] is True
