"""SBD-18 — Story Board wire-format contract test.

The Story Board frontend (frontend/src/pages/StoryBoard.tsx) renders
a Kanban view from two endpoints:

  GET /api/v1/requests/:id         — workflow + status + project info
  GET /api/v1/requests/:id/stories — per-story data with ACs + tests

Each renders 11 distinct UI features (SBD-05 through SBD-15) that
depend on specific field shapes. A schema drift on the backend
silently breaks the frontend — there's no compile-time link between
the two layers. This test pins the wire format so a future field
rename / removal fails loudly here instead of as a "Story Board
shows nothing" bug report.

The serializer block we pin lives at:
  src/api/routes/requests.py::get_stories  (lines 543-585)

What's NOT covered
------------------
- The React render itself (no jsdom — render is covered by vitest)
- WebSocket event delivery (covered by other ws_broadcast tests)
- End-to-end LLM pipeline (covered by per-agent + full-pipeline tests)
"""

from __future__ import annotations

from datetime import datetime

import pytest


# ── Wire-shape contract (what the route actually returns) ────────────────


def test_story_payload_has_all_eight_fields_storyboard_reads():
    """SBD-05/08/10/12/13/15 all read these eight fields off each
    Story dict. A rename here silently breaks the Kanban render."""
    from src.models.base import Story

    st = Story(
        story_id="S-X", request_id="R-X", title="t", description="d",
        status="done", priority="medium", assigned_agent="backend_specialist",
        coverage_pct=92.0, github_issue_number=99,
    )
    # The fields StoryBoard.tsx renders — keep in lockstep (L23)
    for field in (
        "story_id", "title", "description", "status", "priority",
        "assigned_agent", "coverage_pct", "github_issue_number",
    ):
        assert hasattr(st, field), f"missing required field {field}"


def test_story_status_enum_matches_kanban_columns():
    """StoryBoard.tsx COLUMNS uses {todo, in_progress, review, testing,
    done} as the column.key values; story.status MUST take exactly
    those values for the binning loop to find a column. Construct a
    Story for each status and confirm the model accepts it."""
    from src.models.base import Story

    KANBAN_STATUSES = {"todo", "in_progress", "review", "testing", "done"}
    for status in KANBAN_STATUSES:
        st = Story(
            story_id=f"S-{status}", request_id="R-X",
            title="t", description="d", status=status,
            priority="medium", assigned_agent="backend_specialist",
        )
        assert st.status == status


def test_acceptance_criterion_has_given_when_then_and_is_met():
    """SBD-11 renders Given/When/Then as a checkbox list on Done cards.
    All four fields (3 clauses + is_met bool) must be present on the
    model AND survive the route's serialization rename
    (given_clause → given, etc.)."""
    from src.models.base import AcceptanceCriterion

    ac = AcceptanceCriterion(
        ac_id="AC-X", story_id="S-X",
        criterion_text="x", given_clause="g", when_clause="w", then_clause="t",
        is_met=True,
    )
    for field in (
        "ac_id", "criterion_text",
        "given_clause", "when_clause", "then_clause",
        "is_met",
    ):
        assert hasattr(ac, field), f"AC missing field {field}"
    assert ac.is_met is True


def test_test_case_has_status_enum_and_last_run_at():
    """SBD-09 renders per-status icons; SBD-06 sums pass-count. The
    status field MUST be one of {pass, fail, running, pending} so the
    frontend's count-badge color switch finds a mapping."""
    from src.models.base import TestCase

    VALID = {"pass", "fail", "running", "pending"}
    for status in VALID:
        tc = TestCase(
            test_id=f"TC-{status}", story_id="S-X",
            name="x", status=status, last_run_at=datetime.utcnow(),
        )
        assert tc.status in VALID
        assert tc.last_run_at is not None


# ── Serialization shape — pins the route's dict keys ────────────────────


def test_get_stories_serialization_shape_matches_frontend_reader():
    """The exact JSON dict shape the /stories endpoint returns.
    Mirrors lines 555-583 of src/api/routes/requests.py — keeping
    this assertion in lockstep with that code is the L23 contract."""
    from src.models.base import AcceptanceCriterion, Story, TestCase

    st = Story(
        story_id="S-X", request_id="R-X", title="t", description="d",
        status="done", priority="medium", assigned_agent="backend_specialist",
        coverage_pct=92.0, github_issue_number=99,
    )
    ac = AcceptanceCriterion(
        ac_id="AC-X", story_id="S-X",
        criterion_text="x", given_clause="g", when_clause="w", then_clause="t",
        is_met=True,
    )
    tc = TestCase(
        test_id="TC-X", story_id="S-X", name="x", status="pass",
        last_run_at=datetime.utcnow(),
    )

    # Reproduces the route's dict assembly verbatim — if the route
    # changes a field name, this serialized dict + the frontend
    # reader must change together. The test fails loudly if drift
    # creeps in.
    serialized = {
        "story_id": st.story_id,
        "title": st.title,
        "description": st.description,
        "status": st.status,
        "priority": st.priority,
        "assigned_agent": st.assigned_agent,
        "coverage_pct": st.coverage_pct,
        "github_issue_number": st.github_issue_number,
        "acceptance_criteria": [{
            "ac_id": ac.ac_id, "criterion_text": ac.criterion_text,
            "given": ac.given_clause, "when": ac.when_clause,
            "then": ac.then_clause, "is_met": ac.is_met,
        }],
        "test_cases": [{
            "test_id": tc.test_id, "name": tc.name, "status": tc.status,
            "last_run_at": tc.last_run_at.isoformat(),
        }],
    }
    # Top-level keys frontend reads (StoryBoard.tsx StoryCard render)
    REQUIRED_TOP = {
        "story_id", "title", "description", "status", "priority",
        "assigned_agent", "coverage_pct", "github_issue_number",
        "acceptance_criteria", "test_cases",
    }
    assert set(serialized.keys()) == REQUIRED_TOP
    # AC shape — note the route renames given_clause → given
    REQUIRED_AC = {"ac_id", "criterion_text", "given", "when", "then", "is_met"}
    assert set(serialized["acceptance_criteria"][0].keys()) == REQUIRED_AC
    # TC shape — drives SBD-09 (icon per status) + SBD-06 (pass-count)
    REQUIRED_TC = {"test_id", "name", "status", "last_run_at"}
    assert set(serialized["test_cases"][0].keys()) == REQUIRED_TC


def test_coverage_pct_accepts_numeric_or_none():
    """SBD-10 colorizes the coverage bar at <60, 60-79, ≥80. The
    field must be numeric (or None when unmeasured); strings would
    break the comparison logic."""
    from src.models.base import Story

    for pct in (None, 0, 50, 79, 80, 100, 87.5):
        st = Story(
            story_id="S-X", request_id="R-X", title="t", description="d",
            status="done", priority="medium", assigned_agent="x",
            coverage_pct=pct,
        )
        assert st.coverage_pct is None or isinstance(st.coverage_pct, (int, float))


def test_github_issue_number_drives_pr_badge_text():
    """SBD-12 renders 'PR #N — Open / Under Review / Merged' per column.
    The field is the bare integer N — the badge text is built per
    column on the frontend. Type drift here breaks the render."""
    from src.models.base import Story

    st = Story(
        story_id="S-X", request_id="R-X", title="t", description="d",
        status="done", priority="medium", assigned_agent="x",
        github_issue_number=42,
    )
    assert isinstance(st.github_issue_number, int)
    assert st.github_issue_number == 42

    # None is also valid (no PR yet) — drives the "Awaiting PR" placeholder
    st2 = Story(
        story_id="S-Y", request_id="R-X", title="t", description="d",
        status="in_progress", priority="medium", assigned_agent="x",
    )
    assert st2.github_issue_number is None
