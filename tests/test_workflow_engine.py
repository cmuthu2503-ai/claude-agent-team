"""Tests for the workflow engine — loader, runner, dependency resolution."""

import pytest

from src.config.loader import ConfigLoader
from src.workflows.loader import (
    ParallelStage,
    StageDefinition,
    WorkflowLoader,
)
from src.workflows.runner import WorkflowRunner


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
    assert "prd_specialist" in req_stage.agents


def test_quality_gates_parsed(workflow_loader):
    wf = workflow_loader.get_workflow("feature_development")
    review_stage = wf.stages["review"]
    assert isinstance(review_stage, StageDefinition)
    assert len(review_stage.quality_gates) == 2
    gate_names = [g.gate for g in review_stage.quality_gates]
    assert "coverage_check" in gate_names


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

    def __init__(self):
        self.calls = []

    async def execute_agent(self, agent_id, request_id, inputs):
        self.calls.append({"agent_id": agent_id, "request_id": request_id})
        return {"outputs": {agent_id: "done"}, "artifacts": [f"{agent_id}_output"]}


@pytest.fixture
def mock_executor():
    return MockExecutor()


async def test_runner_executes_all_stages(workflow_loader, mock_executor):
    wf = workflow_loader.get_workflow("documentation_update")
    runner = WorkflowRunner(executor=mock_executor)
    result = await runner.run(wf, "REQ-TEST", {"description": "test"})
    agents_called = [c["agent_id"] for c in mock_executor.calls]
    assert "prd_specialist" in agents_called
    assert "user_story_author" in agents_called


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
    # PRD must come before user story author
    prd_idx = agents.index("prd_specialist")
    us_idx = agents.index("user_story_author")
    assert prd_idx < us_idx


def test_dependency_resolver(workflow_loader):
    wf = workflow_loader.get_workflow("feature_development")
    runner = WorkflowRunner(executor=MockExecutor())
    order = runner._resolve_execution_order(wf)
    assert order.index("requirements") < order.index("story_creation")
    assert order.index("story_creation") < order.index("development")
    assert order.index("review") < order.index("testing")
