"""Tests for GitHub integration — actions, branches, issues, PRs, webhooks."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.github.actions import ActionsManager
from src.github.branches import BranchManager
from src.github.issues import IssueManager
from src.github.pull_requests import PRManager, PR_TEMPLATE
from src.github.repo import RepoManager
from src.github.webhooks import WebhookHandler
from src.models.base import Story, StoryStatus
from src.state.sqlite_store import SQLiteStateStore


# ── Actions Manager Tests ────────────────────────


def test_actions_list_templates():
    mgr = ActionsManager()
    templates = mgr.list_templates()
    assert "lint.yml" in templates
    assert "test.yml" in templates
    assert "coverage.yml" in templates
    assert "security.yml" in templates
    assert "demo-test.yml" in templates
    assert "cleanup.yml" in templates
    assert len(templates) >= 7


def test_actions_generate_workflows(tmp_path):
    mgr = ActionsManager()
    generated = mgr.generate_workflows(tmp_path, tech_stack="python-react")
    assert len(generated) >= 7
    workflows_dir = tmp_path / ".github" / "workflows"
    assert workflows_dir.exists()
    assert (workflows_dir / "lint.yml").exists()
    assert (workflows_dir / "test.yml").exists()


def test_actions_generate_python_only(tmp_path):
    mgr = ActionsManager()
    generated = mgr.generate_workflows(tmp_path, tech_stack="python-only")
    lint_content = (tmp_path / ".github" / "workflows" / "lint.yml").read_text()
    # Frontend jobs should be removed
    assert "lint-frontend:" not in lint_content


# ── Branch Manager Tests ─────────────────────────


def test_branch_name_generation():
    mgr = BranchManager()
    name = mgr.make_branch_name("REQ-042", "login-page-jwt-auth")
    assert name == "feature/REQ-042-login-page-jwt-auth"


def test_branch_name_with_sub_type():
    mgr = BranchManager()
    name = mgr.make_branch_name("REQ-042", "login-page", "backend")
    assert name == "feature/REQ-042-backend"


def test_branch_name_sanitizes_slug():
    mgr = BranchManager()
    name = mgr.make_branch_name("REQ-001", "My Feature! With @Special Chars")
    assert "@" not in name
    assert "!" not in name
    assert name.startswith("feature/REQ-001-")


def test_branch_name_truncates_long_slug():
    mgr = BranchManager()
    long_slug = "a" * 100
    name = mgr.make_branch_name("REQ-001", long_slug)
    # Slug should be truncated to 40 chars
    slug_part = name.split("REQ-001-")[1]
    assert len(slug_part) <= 40


# ── PR Template Tests ────────────────────────────


def test_pr_template_formatting():
    result = PR_TEMPLATE.format(
        summary="Added login feature",
        changes="- New login endpoint\n- JWT middleware",
        issues="Closes #5, Closes #6",
        testing="Unit tests added",
        coverage="87%",
    )
    assert "Added login feature" in result
    assert "Closes #5" in result
    assert "87%" in result


# ── Webhook Handler Tests ────────────────────────


@pytest.fixture
async def webhook_store(tmp_path):
    store = SQLiteStateStore(db_path=str(tmp_path / "test.db"))
    await store.initialize()
    yield store
    await store.close()


async def test_webhook_handles_issue_closed(webhook_store):
    handler = WebhookHandler(webhook_store)
    payload = {"issue": {"number": 5}}
    # Should not raise
    await handler.handle_event("issues.closed", payload)


async def test_webhook_handles_pr_merged(webhook_store):
    handler = WebhookHandler(webhook_store)
    payload = {
        "pull_request": {
            "number": 10,
            "body": "Closes #5\nCloses #6",
            "merged": True,
        }
    }
    await handler.handle_event("pull_request.merged", payload)


async def test_webhook_handles_pr_closed_without_merge(webhook_store):
    handler = WebhookHandler(webhook_store)
    payload = {
        "pull_request": {
            "number": 11,
            "merged": False,
        }
    }
    await handler.handle_event("pull_request.closed", payload)


async def test_webhook_ignores_unknown_events(webhook_store):
    handler = WebhookHandler(webhook_store)
    # Should not raise
    await handler.handle_event("star.created", {"action": "created"})


# ── Issue Manager Tests ──────────────────────────


async def test_issue_manager_init(webhook_store):
    mgr = IssueManager(webhook_store, repo="test-org/test-repo")
    assert mgr.repo == "test-org/test-repo"


# ── Repo Manager Tests ───────────────────────────


def test_repo_manager_init():
    mgr = RepoManager(org="agent-team-projects")
    assert mgr.org == "agent-team-projects"
    assert mgr.default_branch == "main"


# ── PR Manager Tests ─────────────────────────────


def test_pr_manager_init():
    mgr = PRManager(repo="agent-team-projects/test")
    assert mgr.repo == "agent-team-projects/test"
