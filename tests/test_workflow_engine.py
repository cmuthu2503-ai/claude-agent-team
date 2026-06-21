"""Tests for the workflow engine — loader, runner, dependency resolution."""

import pytest

from src.config.loader import ConfigLoader
from src.workflows.loader import (
    ParallelStage,
    StageDefinition,
    WorkflowLoader,
)
from src.workflows.runner import WorkflowRunner, _extract_affected_components


@pytest.fixture
def config():
    loader = ConfigLoader()
    loader.load_all()
    return loader


@pytest.fixture
def workflow_loader(config):
    wl = WorkflowLoader(config)
    wl.load_all()
    return wl


# ── Loader Tests ─────────────────────────────────


def test_loads_all_workflows(workflow_loader):
    workflows = workflow_loader._workflows
    assert "feature_development" in workflows
    assert "bug_fix" in workflows
    assert "documentation_update" in workflows
    assert "demo_preparation" in workflows


def test_feature_workflow_stages(workflow_loader):
    wf = workflow_loader.get_workflow("feature_development")
    assert wf is not None
    assert wf.trigger == "feature_request"
    assert "requirements" in wf.stages
    assert "development" in wf.stages
    assert "review" in wf.stages


def test_parallel_stage_parsed(workflow_loader):
    wf = workflow_loader.get_workflow("feature_development")
    dev_stage = wf.stages["development"]
    assert isinstance(dev_stage, ParallelStage)
    assert len(dev_stage.groups) == 2
    group_ids = [g.group_id for g in dev_stage.groups]
    assert "backend" in group_ids
    assert "frontend" in group_ids


def test_sequential_stage_parsed(workflow_loader):
    wf = workflow_loader.get_workflow("feature_development")
    req_stage = wf.stages["requirements"]
    assert isinstance(req_stage, StageDefinition)
    assert "business_analyst" in req_stage.agents


def test_quality_gates_parsed(workflow_loader):
    wf = workflow_loader.get_workflow("feature_development")
    review_stage = wf.stages["review"]
    # review is now a parallel stage (code_review + arch_review + quality_check run in parallel)
    assert isinstance(review_stage, ParallelStage)
    assert len(review_stage.quality_gates) == 4
    gate_names = [g.gate for g in review_stage.quality_gates]
    assert "coverage_check" in gate_names
    assert "review_approval" in gate_names
    assert "arch_review_approval" in gate_names
    assert "quality_guardian_approval" in gate_names
    # verify all three parallel groups are present
    group_ids = [g.group_id for g in review_stage.groups]
    assert "code_review" in group_ids
    assert "arch_review" in group_ids
    assert "quality_check" in group_ids


def test_on_fail_routing(workflow_loader):
    wf = workflow_loader.get_workflow("feature_development")
    review_stage = wf.stages["review"]
    assert review_stage.on_fail == "development"


def test_get_workflow_for_trigger(workflow_loader):
    wf = workflow_loader.get_workflow_for_trigger("bug_report")
    assert wf is not None
    assert wf.workflow_id == "bug_fix"


def test_get_workflow_for_unknown_trigger(workflow_loader):
    wf = workflow_loader.get_workflow_for_trigger("nonexistent")
    assert wf is None


# ── Runner Tests ─────────────────────────────────


class MockExecutor:
    """Mock agent executor that records calls and returns empty results."""

    def __init__(self, agent_text: dict[str, str] | None = None):
        self.calls = []
        # Optional per-agent text payload — lets tests simulate an agent emitting
        # the **Affected Components:** marker.
        self._agent_text = agent_text or {}

    async def execute_agent(self, agent_id, request_id, inputs):
        self.calls.append({"agent_id": agent_id, "request_id": request_id})
        return {
            "outputs": {agent_id: "done"},
            "artifacts": [f"{agent_id}_output"],
            "text": self._agent_text.get(agent_id, ""),
        }


@pytest.fixture
def mock_executor():
    return MockExecutor()


async def test_runner_executes_all_stages(workflow_loader, mock_executor):
    wf = workflow_loader.get_workflow("documentation_update")
    runner = WorkflowRunner(executor=mock_executor)
    result = await runner.run(wf, "REQ-TEST", {"description": "test"})
    agents_called = [c["agent_id"] for c in mock_executor.calls]
    assert "business_analyst" in agents_called
    assert "business_analyst" in agents_called


async def test_runner_parallel_execution(workflow_loader, mock_executor):
    wf = workflow_loader.get_workflow("feature_development")
    runner = WorkflowRunner(executor=mock_executor)
    result = await runner.run(wf, "REQ-PAR", {"description": "parallel test"})
    agents_called = [c["agent_id"] for c in mock_executor.calls]
    assert "backend_specialist" in agents_called
    assert "frontend_specialist" in agents_called


async def test_execution_order_respects_dependencies(workflow_loader, mock_executor):
    wf = workflow_loader.get_workflow("feature_development")
    runner = WorkflowRunner(executor=mock_executor)
    await runner.run(wf, "REQ-ORD", {"description": "order test"})
    agents = [c["agent_id"] for c in mock_executor.calls]
    # Planning (business_analyst — runs both the PRD and story-creation stages)
    # must precede the development specialists.
    assert "business_analyst" in agents
    ba_idx = agents.index("business_analyst")
    assert ba_idx < agents.index("backend_specialist")
    assert ba_idx < agents.index("frontend_specialist")


def test_dependency_resolver(workflow_loader):
    wf = workflow_loader.get_workflow("feature_development")
    runner = WorkflowRunner(executor=MockExecutor())
    order = runner._resolve_execution_order(wf)
    assert order.index("requirements") < order.index("story_creation")
    assert order.index("story_creation") < order.index("development")
    assert order.index("review") < order.index("testing")


# ── affected_components parser ───────────────────


def test_extract_affected_components_bold_frontend_only():
    text = "## PRD: Neon Theme\n\n**Affected Components:** frontend\n\n### 1. Summary"
    assert _extract_affected_components(text) == ["frontend"]


def test_extract_affected_components_both():
    text = "**Affected Components:** frontend, backend\n\n### 1. Summary"
    assert _extract_affected_components(text) == ["frontend", "backend"]


def test_extract_affected_components_no_markdown_bold():
    text = "Affected Components: backend\n"
    assert _extract_affected_components(text) == ["backend"]


def test_extract_affected_components_case_insensitive():
    text = "**affected COMPONENTS:** Frontend, Backend"
    assert _extract_affected_components(text) == ["frontend", "backend"]


def test_extract_affected_components_handles_brackets_and_and():
    # Tolerate model variations that wrap the list in brackets or use "and".
    text = "**Affected Components:** [frontend and backend]"
    assert _extract_affected_components(text) == ["frontend", "backend"]


def test_extract_affected_components_dedupes():
    text = "**Affected Components:** frontend, frontend, backend"
    assert _extract_affected_components(text) == ["frontend", "backend"]


def test_extract_affected_components_filters_unknown_tokens():
    text = "**Affected Components:** mobile, frontend, kubernetes"
    assert _extract_affected_components(text) == ["frontend"]


def test_extract_affected_components_missing_returns_none():
    assert _extract_affected_components("## PRD: foo\n\n### 1. Summary\nstuff") is None


def test_extract_affected_components_empty_returns_none():
    assert _extract_affected_components("") is None


def test_extract_affected_components_only_unknown_returns_none():
    text = "**Affected Components:** mobile, kubernetes"
    assert _extract_affected_components(text) is None


# ── Parallel-stage skip-routing (Option C) ───────


async def test_parallel_skips_backend_when_frontend_only(workflow_loader):
    """If the PRD emits 'frontend', backend_specialist must NOT run in dev stage."""
    mock = MockExecutor(agent_text={
        "business_analyst": "## PRD: Neon Theme\n\n**Affected Components:** frontend\n",
    })
    runner = WorkflowRunner(executor=mock)
    wf = workflow_loader.get_workflow("feature_development")
    await runner.run(wf, "REQ-FE-ONLY", {"description": "Add neon theme"})
    agents = [c["agent_id"] for c in mock.calls]
    assert "frontend_specialist" in agents
    assert "backend_specialist" not in agents


async def test_parallel_skips_frontend_when_backend_only(workflow_loader):
    """Mirror case: backend-only PRD should skip the frontend specialist."""
    mock = MockExecutor(agent_text={
        "business_analyst": "## PRD: New Endpoint\n\n**Affected Components:** backend\n",
    })
    runner = WorkflowRunner(executor=mock)
    wf = workflow_loader.get_workflow("feature_development")
    await runner.run(wf, "REQ-BE-ONLY", {"description": "Add /api/v1/foo"})
    agents = [c["agent_id"] for c in mock.calls]
    assert "backend_specialist" in agents
    assert "frontend_specialist" not in agents


async def test_parallel_runs_both_when_marker_missing(workflow_loader):
    """Missing marker = safe default: every parallel group runs."""
    mock = MockExecutor()  # No agent_text → no marker anywhere
    runner = WorkflowRunner(executor=mock)
    wf = workflow_loader.get_workflow("feature_development")
    await runner.run(wf, "REQ-DEFAULT", {"description": "fullstack feature"})
    agents = [c["agent_id"] for c in mock.calls]
    assert "backend_specialist" in agents
    assert "frontend_specialist" in agents


async def test_parallel_runs_both_when_marker_lists_both(workflow_loader):
    mock = MockExecutor(agent_text={
        "business_analyst": "**Affected Components:** frontend, backend\n",
    })
    runner = WorkflowRunner(executor=mock)
    wf = workflow_loader.get_workflow("feature_development")
    await runner.run(wf, "REQ-BOTH", {"description": "fullstack feature"})
    agents = [c["agent_id"] for c in mock.calls]
    assert "backend_specialist" in agents
    assert "frontend_specialist" in agents


# ── Regression: bug_fix's review_and_test must NOT be skipped by Option C ────


async def test_bug_fix_review_and_test_runs_regardless_of_affected_components(workflow_loader):
    """Critical regression test for the REQ-378ED5 bug.

    bug_fix's review_and_test parallel stage has groups named 'review' and 'test'
    (stage roles, NOT domain names). Even when the PRD lists affected_components
    as ['frontend', 'backend'], BOTH groups must run — otherwise bug fixes ship
    to production with no code review and no testing.
    """
    mock = MockExecutor(agent_text={
        # The triage stage in bug_fix uses business_analyst; this is where
        # affected_components gets extracted.
        "business_analyst": (
            "## Triage: theme bug\n\n"
            "**Affected Components:** frontend, backend\n\n"
            "### Bug analysis\n..."
        ),
    })
    runner = WorkflowRunner(executor=mock)
    wf = workflow_loader.get_workflow("bug_fix")
    await runner.run(wf, "REQ-BUG-REVIEW", {"description": "bug fix smoke"})
    agents = [c["agent_id"] for c in mock.calls]
    # Both quality-gate agents must run despite their groups (review, test)
    # not matching the affected_components vocabulary.
    assert "code_reviewer" in agents, (
        "Regression: bug_fix's review_and_test 'review' group was skipped by "
        "the Option C affected-components filter. Bugs would ship unreviewed."
    )
    assert "tester_specialist" in agents, (
        "Regression: bug_fix's review_and_test 'test' group was skipped by "
        "the Option C affected-components filter. Bugs would ship untested."
    )


async def test_parallel_skip_only_applies_to_domain_groups(workflow_loader):
    """The affected-components skip filter only applies to groups named with
    domain words (frontend, backend). Non-domain group names always run."""
    # Use bug_fix again — has groups named 'review' and 'test'. Neither is a
    # domain name, so neither should ever be skipped.
    mock = MockExecutor(agent_text={
        "business_analyst": "**Affected Components:** frontend\n",  # only frontend
    })
    runner = WorkflowRunner(executor=mock)
    wf = workflow_loader.get_workflow("bug_fix")
    await runner.run(wf, "REQ-BUG-FE-ONLY", {"description": "frontend-only bug"})
    agents = [c["agent_id"] for c in mock.calls]
    # review + test both run regardless of components list
    assert "code_reviewer" in agents
    assert "tester_specialist" in agents
