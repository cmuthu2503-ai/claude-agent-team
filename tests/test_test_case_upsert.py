"""Regression tests for the test_case UPSERT + per-row isolation fix.

Pins the fix that closed T-b4954195 and T-3e1303b3's failure class:
the tester agent emits deterministic TC-XXX IDs across rework cycles,
so cycle 2's parser tried to re-INSERT rows with the same primary
keys. The plain INSERT raised UNIQUE; the broad `except Exception`
in `_parse_and_save_test_cases` aborted the whole batch; the combined
gate flipped test_passed=False; out of cycles → REQUEST FAILED.

Two fixes:
  1. `create_test_case` is now UPSERT (ON CONFLICT(test_id) DO UPDATE).
  2. The orchestrator's parse loop now isolates per-row failures so
     one bad row doesn't bail the whole batch.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.models.base import (
    Project, ProjectStatus, Request, Story, TestCase,
)
from src.state.sqlite_store import SQLiteStateStore


async def _make_store(tmp_path: Path) -> SQLiteStateStore:
    """Build a fresh store with a parent project + request + story
    seeded so test_cases can satisfy their FK to stories."""
    db = SQLiteStateStore(str(tmp_path / "t.db"))
    await db.initialize()
    # Seed parent rows so test_case INSERTs don't fail on FK.
    await db.create_project(Project(
        project_id="proj-tc", name="x", status=ProjectStatus.ACTIVE,
    ))
    await db.create_request(Request(
        request_id="REQ-TC", description="x",
        task_type="feature_request", priority="medium",
        status="completed", project_id="proj-tc",
    ))
    for sid in ("US-001", "US-002"):
        await db.create_story(Story(
            story_id=sid, request_id="REQ-TC",
            title=sid, description="", status="todo",
            priority="medium", assigned_agent="backend_specialist",
            coverage_pct=0.0,
        ))
    return db


def _tc(test_id: str, story_id: str = "US-001", **overrides) -> TestCase:
    defaults: dict = dict(
        test_id=test_id,
        story_id=story_id,
        name=f"test {test_id}",
        status="pass",
        last_run_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return TestCase(**defaults)


# ── UPSERT behaviour ────────────────────────────────────────────────────────


async def test_upsert_overwrites_existing_test_case(tmp_path: Path) -> None:
    """Cycle 1 inserts TC-001 with status=pass. Cycle 2 re-emits TC-001
    with status=fail. The new status must win — no UNIQUE constraint."""
    store = await _make_store(tmp_path)
    await store.create_test_case(_tc("TC-001", name="initial", status="pass"))
    # Cycle 2 — same ID, different status + name (legitimate update)
    await store.create_test_case(_tc("TC-001", name="updated", status="fail"))
    rows = await store.get_test_cases_for_story("US-001")
    assert len(rows) == 1, "should still be one row (UPSERT, not duplicate)"
    assert rows[0].status == "fail", "newer status wins"
    assert rows[0].name == "updated", "newer name wins"


async def test_upsert_can_relink_story_id(tmp_path: Path) -> None:
    """If the tester re-classifies which story a TC covers, the UPSERT
    moves it. (Edge case but the underlying schema allows it.)"""
    store = await _make_store(tmp_path)
    await store.create_test_case(_tc("TC-005", story_id="US-001"))
    await store.create_test_case(_tc("TC-005", story_id="US-002"))
    us1 = await store.get_test_cases_for_story("US-001")
    us2 = await store.get_test_cases_for_story("US-002")
    assert us1 == []
    assert len(us2) == 1


async def test_repeated_inserts_dont_grow_table(tmp_path: Path) -> None:
    """The bug we're fixing: cycle 1+2+3 of the same task all emit
    TC-001…TC-010. The table should hold 10 rows total, not 30."""
    store = await _make_store(tmp_path)
    for cycle in range(3):
        last_status = "pass" if cycle == 2 else "fail"
        for i in range(1, 11):
            await store.create_test_case(_tc(f"TC-{i:03d}", status=last_status))
    rows = await store.get_test_cases_for_story("US-001")
    assert len(rows) == 10, f"expected 10 rows after 3 cycles, got {len(rows)}"
    # Cycle 2 (last) had status=pass — verify the UPSERT wrote the LATEST.
    assert all(r.status == "pass" for r in rows), \
        "last-cycle status should win for every row"


# ── Per-row isolation in orchestrator parsing ───────────────────────────────


async def test_orchestrator_parse_isolates_per_row_failures(
    tmp_path: Path, monkeypatch,
) -> None:
    """If a single test_case fails to persist (e.g. unanticipated
    constraint), the others must still be saved and story coverage
    must still update. Previously one bad row aborted the whole batch
    via the orchestrator's broad except clause."""
    from src.core.orchestrator import Orchestrator

    store = await _make_store(tmp_path)

    # Build a fake orchestrator with just the parse method exercised
    orch = Orchestrator.__new__(Orchestrator)
    orch.state = store  # type: ignore[attr-defined]

    # Monkey-patch create_test_case so the FIRST call raises but the
    # rest succeed — proves per-row isolation.
    real_create = store.create_test_case
    call_count = {"n": 0}
    async def flaky_create(tc):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated unanticipated constraint failure")
        return await real_create(tc)
    monkeypatch.setattr(store, "create_test_case", flaky_create)

    # 3 test_cases. First will raise; rest must persist.
    # Markdown table format matched by orchestrator's _extract_test_cases.
    output_text = (
        "| TC-001 | first | US-001 AC-1 | unit | pass | n/a |\n"
        "| TC-002 | second | US-001 AC-2 | unit | pass | n/a |\n"
        "| TC-003 | third | US-001 AC-3 | unit | pass | n/a |\n"
    )
    await orch._parse_and_save_test_cases("REQ-TC", output_text)

    # Restore real create_test_case for verification reads
    monkeypatch.setattr(store, "create_test_case", real_create)
    rows = await store.get_test_cases_for_story("US-001")
    # 2 of 3 rows persisted — the first failed, the rest survived
    assert len(rows) == 2
    test_ids = {r.test_id for r in rows}
    assert test_ids == {"TC-002", "TC-003"}
    # Coverage updated based on survivors (2/2 passed → 100%)
    s = (await store.get_stories_for_request("REQ-TC"))[0]
    assert s.coverage_pct == 100.0
