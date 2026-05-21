"""Tests for the per-project AI Deploy Judge.

Covers:
  - src/core/project_deploy_judge.py
      * _parse_response (deterministic; no LLM call)
      * evaluate_project_deploy cost-free shortcuts (no drift, over_limit,
        missing creds) — all return without invoking the SDK
      * _render_user_message structure (truncation, blocks)
  - src/core/deploy_drift.py
      * compute_drift against a real SQLiteStateStore on a temp DB
  - src/state/sqlite_store.py
      * create_deploy_decision / get_latest_pending_decision /
        supersede / mark_applied / mark_overridden / list_recent_overrides
      * update_project_deploy_preferences

All tests are offline (no LLM call) so they run in CI without
ANTHROPIC_AWS_* credentials.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime

import pytest

from src.core.deploy_drift import ProjectDrift, compute_drift
from src.core.project_deploy_judge import (
    _parse_response,
    _render_user_message,
    evaluate_project_deploy,
)
from src.models.base import (
    DeployAction,
    DeployDecision,
    DeployDecisionStatus,
    DeployRiskLevel,
    DeployStatus,
    Project,
    ProjectKind,
    Request,
    RequestStatus,
)
from src.state.sqlite_store import SQLiteStateStore


# ── project_deploy_judge._parse_response ─────────────────────────────────────


def test_judge_parses_clean_json():
    text = json.dumps({
        "action": "restart-backend",
        "risk": "low",
        "confidence": "high",
        "reasoning": "only backend python changed",
    })
    result = _parse_response(text)
    assert result.action == "restart-backend"
    assert result.risk == "low"
    assert result.confidence == "high"
    assert result.reasoning == "only backend python changed"
    assert result.from_llm is True


def test_judge_strips_markdown_fences():
    text = (
        '```json\n'
        '{"action":"skip","risk":"low","confidence":"high",'
        '"reasoning":"docs only"}\n'
        '```'
    )
    result = _parse_response(text)
    assert result.action == "skip"
    assert result.from_llm is True


def test_judge_extracts_json_from_preamble():
    text = (
        "Here is my decision:\n"
        '{"action":"rebuild-frontend","risk":"medium",'
        '"confidence":"medium","reasoning":"package.json changed"}'
    )
    result = _parse_response(text)
    assert result.action == "rebuild-frontend"
    assert result.risk == "medium"


def test_judge_coerces_unknown_risk_to_medium():
    text = (
        '{"action":"rebuild-all","risk":"OFF_THE_SCALE",'
        '"confidence":"high","reasoning":"x"}'
    )
    result = _parse_response(text)
    assert result.action == "rebuild-all"
    assert result.risk == "medium"  # coerced
    assert result.from_llm is True


def test_judge_coerces_unknown_confidence_to_medium():
    text = (
        '{"action":"restart-backend","risk":"low",'
        '"confidence":"banana","reasoning":"x"}'
    )
    result = _parse_response(text)
    assert result.confidence == "medium"
    assert result.from_llm is True


def test_judge_safe_defaults_on_invalid_action():
    """An action the supervisor can't run safely falls back to rebuild-all."""
    text = '{"action":"nuke-from-orbit","risk":"low","confidence":"high","reasoning":"trust me"}'
    result = _parse_response(text)
    assert result.action == "rebuild-all"
    assert result.from_llm is False
    assert "unknown action" in result.reasoning.lower()


def test_judge_safe_defaults_on_malformed_json():
    result = _parse_response("this is not JSON")
    assert result.action == "rebuild-all"
    assert result.from_llm is False


def test_judge_safe_defaults_on_empty_response():
    result = _parse_response("")
    assert result.from_llm is False
    assert result.action == "rebuild-all"


# ── evaluate_project_deploy cost-free shortcuts ──────────────────────────────


async def test_evaluate_skips_when_no_drift(monkeypatch):
    """No commits since last deploy → skip, no LLM call."""
    monkeypatch.setenv("ANTHROPIC_AWS_API_KEY", "stub")
    monkeypatch.setenv("ANTHROPIC_AWS_WORKSPACE_ID", "stub")
    project = Project(
        project_id="proj-test",
        name="Test",
        kind=ProjectKind.WEB_APP,
        deploy_status=DeployStatus.STOPPED,
    )
    drift = ProjectDrift(project_id="proj-test")  # empty
    result = await evaluate_project_deploy(project=project, drift=drift)
    assert result.action == "skip"
    assert result.from_llm is False
    assert "no drift" in result.reasoning.lower()


async def test_evaluate_rebuild_all_when_over_limit(monkeypatch):
    """If drift exceeds the cost cap, fall back to rebuild-all without an LLM call."""
    monkeypatch.setenv("ANTHROPIC_AWS_API_KEY", "stub")
    monkeypatch.setenv("ANTHROPIC_AWS_WORKSPACE_ID", "stub")
    project = Project(project_id="proj-test", name="Test", kind=ProjectKind.WEB_APP)
    drift = ProjectDrift(
        project_id="proj-test",
        commits=[{"commit_sha": f"c{i}", "files": [], "file_count": 0,
                  "description": "x", "completed_at": "2026-05-21T00:00:00",
                  "request_id": f"r{i}"} for i in range(51)],
        over_limit=True,
        to_commit_sha="c50",
    )
    result = await evaluate_project_deploy(project=project, drift=drift)
    assert result.action == "rebuild-all"
    assert result.from_llm is False
    assert "exceeds" in result.reasoning.lower() or "cap" in result.reasoning.lower()


async def test_evaluate_safe_default_without_credentials(monkeypatch):
    """No ANTHROPIC_AWS_* in env → rebuild-all (no LLM call, no exception)."""
    monkeypatch.delenv("ANTHROPIC_AWS_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AWS_WORKSPACE_ID", raising=False)
    project = Project(project_id="proj-test", name="Test", kind=ProjectKind.WEB_APP)
    drift = ProjectDrift(
        project_id="proj-test",
        commits=[{"commit_sha": "abc", "files": ["x.py"], "file_count": 1,
                  "description": "do thing", "completed_at": "2026-05-21T00:00:00",
                  "request_id": "REQ-1"}],
        to_commit_sha="abc",
    )
    result = await evaluate_project_deploy(project=project, drift=drift)
    assert result.action == "rebuild-all"
    assert result.from_llm is False
    assert "credentials" in result.reasoning.lower()


# ── _render_user_message — prompt structure ──────────────────────────────────


def _make_drift(n_files: int) -> ProjectDrift:
    return ProjectDrift(
        project_id="proj-test",
        commits=[{
            "commit_sha": "abcdef12",
            "description": "Add API route + tests",
            "files": [f"src/api/route_{i}.py" for i in range(n_files)],
            "file_count": n_files,
            "completed_at": "2026-05-21T10:00:00",
            "request_id": "REQ-XYZ",
        }],
        from_commit_sha="0000",
        to_commit_sha="abcdef12",
        files_touched=[f"src/api/route_{i}.py" for i in range(n_files)],
    )


def test_render_user_message_includes_project_metadata():
    project = Project(
        project_id="proj-x", name="MyProject",
        kind=ProjectKind.API_SERVICE,
        deploy_judge_preferences="",
    )
    msg = _render_user_message(project=project, drift=_make_drift(3), prior_overrides=None)
    assert "MyProject" in msg
    assert "api-service" in msg
    assert "commits_count:    1" in msg


def test_render_user_message_includes_commit_files():
    project = Project(project_id="proj-x", name="X", kind=ProjectKind.WEB_APP)
    msg = _render_user_message(project=project, drift=_make_drift(3), prior_overrides=None)
    # Files render inside the commits_json block; confirm they're present.
    assert "src/api/route_0.py" in msg
    assert "src/api/route_2.py" in msg


def test_render_user_message_truncates_long_file_lists():
    """Per-commit file lists cap at 20 entries so the prompt stays bounded."""
    project = Project(project_id="proj-x", name="X", kind=ProjectKind.WEB_APP)
    msg = _render_user_message(project=project, drift=_make_drift(50), prior_overrides=None)
    # The 20th entry should be present, but the 30th should NOT be —
    # truncation places an "…" placeholder beyond the cap.
    assert "src/api/route_19.py" in msg
    assert "src/api/route_30.py" not in msg


def test_render_user_message_includes_preferences_when_set():
    project = Project(
        project_id="proj-x", name="X", kind=ProjectKind.WEB_APP,
        deploy_judge_preferences="Treat src/state/ as rebuild-backend.",
    )
    msg = _render_user_message(project=project, drift=_make_drift(2), prior_overrides=None)
    assert "Treat src/state/ as rebuild-backend." in msg
    assert "Project-specific preferences" in msg


def test_render_user_message_omits_preferences_block_when_empty():
    project = Project(
        project_id="proj-x", name="X", kind=ProjectKind.WEB_APP,
        deploy_judge_preferences="",
    )
    msg = _render_user_message(project=project, drift=_make_drift(2), prior_overrides=None)
    assert "Project-specific preferences" not in msg


def test_render_user_message_surfaces_recent_overrides():
    """Prior (recommended, overridden) pairs feed into the prompt."""
    project = Project(project_id="proj-x", name="X", kind=ProjectKind.WEB_APP)
    overrides = [
        DeployDecision(
            decision_id="d1", project_id="proj-x",
            action=DeployAction.REBUILD_ALL, risk=DeployRiskLevel.HIGH,
            confidence=DeployRiskLevel.MEDIUM, reasoning="",
            status=DeployDecisionStatus.OVERRIDDEN,
            overridden_action=DeployAction.RESTART_BACKEND,
        ),
    ]
    msg = _render_user_message(project=project, drift=_make_drift(2), prior_overrides=overrides)
    assert "Recent overrides" in msg
    assert "rebuild-all" in msg
    assert "restart-backend" in msg


# ── compute_drift + SqliteStateStore deploy_decisions methods ────────────────


@pytest.fixture
async def temp_store(tmp_path):
    """Fresh SQLite store on a temp file for each test."""
    db_path = str(tmp_path / "test.db")
    store = SQLiteStateStore(db_path=db_path)
    await store.initialize()
    yield store
    # Connection cleanup handled by the store; tmp_path auto-deletes.


async def test_compute_drift_empty_for_never_deployed_project_without_commits(temp_store):
    """A new project with no committed Requests yields empty drift."""
    project = Project(
        project_id="proj-test", name="Test", kind=ProjectKind.WEB_APP,
        deploy_status=DeployStatus.STOPPED,
    )
    await temp_store.create_project(project)
    drift = await compute_drift(temp_store, project)
    assert drift.has_drift is False
    assert drift.commit_count == 0
    assert drift.to_commit_sha is None


async def test_compute_drift_picks_up_committed_requests(temp_store):
    """Inserting completed Requests with commit_sha shows up as drift."""
    project = Project(
        project_id="proj-test", name="Test", kind=ProjectKind.WEB_APP,
    )
    await temp_store.create_project(project)
    # Insert two committed requests
    for i, sha in enumerate(["aaaaaaaa", "bbbbbbbb"]):
        req = Request(
            request_id=f"REQ-{i:03d}",
            description=f"Task {i}",
            task_type="feature_request",
            priority="medium",
            status=RequestStatus.COMPLETED,
            project_id="proj-test",
            completed_at=datetime(2026, 5, 21, 10 + i, 0, 0),
            commit_sha=sha,
            published_files=["src/api/x.py", "src/api/y.py"],
        )
        await temp_store.create_request(req)
        await temp_store.update_request(req)

    drift = await compute_drift(temp_store, project)
    assert drift.has_drift is True
    assert drift.commit_count == 2
    assert drift.to_commit_sha == "bbbbbbbb"  # newest by completed_at
    assert drift.files_touched == ["src/api/x.py", "src/api/y.py"]  # deduped


async def test_compute_drift_respects_deploy_last_started_cutoff(temp_store):
    """Commits completed BEFORE last deploy are filtered out."""
    project = Project(
        project_id="proj-test", name="Test", kind=ProjectKind.WEB_APP,
        # Cutoff at 10:30 — only commits AFTER this are drift.
        deploy_last_started_at=datetime(2026, 5, 21, 10, 30, 0),
    )
    await temp_store.create_project(project)
    for i, (sha, hr) in enumerate([("OLD", 10), ("NEW", 11)]):  # 10:00 vs 11:00
        req = Request(
            request_id=f"REQ-{i:03d}",
            description=f"Task {i}",
            task_type="feature_request",
            priority="medium",
            status=RequestStatus.COMPLETED,
            project_id="proj-test",
            completed_at=datetime(2026, 5, 21, hr, 0, 0),
            commit_sha=sha,
            published_files=["a.py"],
        )
        await temp_store.create_request(req)
        await temp_store.update_request(req)

    drift = await compute_drift(temp_store, project)
    assert drift.commit_count == 1
    assert drift.to_commit_sha == "NEW"  # the OLD commit is filtered


async def test_deploy_decision_lifecycle(temp_store):
    """Create → fetch latest pending → apply → no longer pending."""
    project = Project(project_id="proj-d", name="D", kind=ProjectKind.WEB_APP)
    await temp_store.create_project(project)

    dec = DeployDecision(
        decision_id="dd-1",
        project_id="proj-d",
        action=DeployAction.RESTART_BACKEND,
        risk=DeployRiskLevel.LOW,
        confidence=DeployRiskLevel.HIGH,
        reasoning="test",
        to_commit_sha="abc",
    )
    await temp_store.create_deploy_decision(dec)

    latest = await temp_store.get_latest_pending_decision("proj-d")
    assert latest is not None
    assert latest.decision_id == "dd-1"
    assert latest.action == "restart-backend"

    await temp_store.mark_decision_applied("dd-1")
    # After apply, no pending decision remains.
    assert await temp_store.get_latest_pending_decision("proj-d") is None


async def test_supersede_pending_decisions_idempotent(temp_store):
    """Calling supersede with no pending rows returns 0 — safe to call eagerly."""
    project = Project(project_id="proj-d2", name="D2", kind=ProjectKind.WEB_APP)
    await temp_store.create_project(project)
    count = await temp_store.supersede_pending_decisions("proj-d2")
    assert count == 0

    # Create one, supersede it.
    await temp_store.create_deploy_decision(DeployDecision(
        decision_id="dd-X", project_id="proj-d2",
        action=DeployAction.SKIP, risk=DeployRiskLevel.LOW,
        confidence=DeployRiskLevel.HIGH, reasoning="x",
    ))
    count = await temp_store.supersede_pending_decisions("proj-d2")
    assert count == 1
    # The superseded one no longer appears as latest pending.
    assert await temp_store.get_latest_pending_decision("proj-d2") is None


async def test_mark_decision_overridden_records_action_choice(temp_store):
    project = Project(project_id="proj-o", name="O", kind=ProjectKind.WEB_APP)
    await temp_store.create_project(project)
    await temp_store.create_deploy_decision(DeployDecision(
        decision_id="dd-O", project_id="proj-o",
        action=DeployAction.RESTART_BACKEND, risk=DeployRiskLevel.LOW,
        confidence=DeployRiskLevel.HIGH, reasoning="x",
    ))
    await temp_store.mark_decision_overridden("dd-O", "rebuild-all")

    overrides = await temp_store.list_recent_overrides("proj-o")
    assert len(overrides) == 1
    assert overrides[0].action == "restart-backend"  # what the judge said
    assert overrides[0].overridden_action == "rebuild-all"  # what the user did
    assert overrides[0].status == "overridden"


async def test_update_project_deploy_preferences_round_trip(temp_store):
    project = Project(project_id="proj-p", name="P", kind=ProjectKind.WEB_APP)
    await temp_store.create_project(project)

    await temp_store.update_project_deploy_preferences(
        "proj-p", "Prefer restart for non-dep changes.",
    )
    refetched = await temp_store.get_project("proj-p")
    assert refetched is not None
    assert refetched.deploy_judge_preferences == "Prefer restart for non-dep changes."

    # Clearing back to empty works too.
    await temp_store.update_project_deploy_preferences("proj-p", "")
    refetched = await temp_store.get_project("proj-p")
    assert refetched.deploy_judge_preferences == ""
