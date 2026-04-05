"""P8-T05/T06/T07: Integration tests — full workflow flows with mock agents."""

import pytest
from src.config.loader import ConfigLoader
from src.core.events import EventEmitter
from src.core.orchestrator import Orchestrator
from src.models.base import RequestStatus
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def system(tmp_path):
    config = ConfigLoader()
    config.load_all()
    state = SQLiteStateStore(db_path=str(tmp_path / "integration.db"))
    await state.initialize()
    events = EventEmitter()
    orchestrator = Orchestrator(config=config, state=state, events=events)
    yield orchestrator, state, events
    await state.close()


# ── Feature Flow ─────────────────────────────────

async def test_feature_flow_creates_all_subtasks(system):
    """Full feature workflow: PRD → stories → parallel dev → review → test → deploy."""
    orchestrator, state, events = system
    request = await orchestrator.submit(
        description="Build a user profile page with avatar upload",
        task_type="feature_request",
        priority="high",
        created_by="developer",
    )
    subtasks = await state.get_subtasks_for_request(request.request_id)
    agent_ids = {s.agent_id for s in subtasks}

    # Feature workflow should involve: prd, user_story_author, backend, frontend, code_reviewer, tester, devops
    assert "prd_specialist" in agent_ids
    assert "user_story_author" in agent_ids
    assert "backend_specialist" in agent_ids
    assert "frontend_specialist" in agent_ids


async def test_feature_flow_completes(system):
    orchestrator, state, events = system
    request = await orchestrator.submit(
        description="Add search functionality",
        task_type="feature_request",
        created_by="developer",
    )
    fetched = await state.get_request(request.request_id)
    assert fetched.status == RequestStatus.COMPLETED


async def test_feature_flow_events_emitted(system):
    orchestrator, state, events = system
    queue = events.subscribe()
    await orchestrator.submit(
        description="Build notifications",
        task_type="feature_request",
        created_by="developer",
    )
    collected = []
    while not queue.empty():
        collected.append(await queue.get())
    types = {e["type"] for e in collected}
    assert "request.created" in types
    assert "agent.started" in types
    assert "agent.completed" in types
    assert "request.completed" in types


# ── Bug Fix Flow ─────────────────────────────────

async def test_bugfix_flow_completes(system):
    orchestrator, state, events = system
    request = await orchestrator.submit(
        description="Fix login timeout after 5 minutes",
        task_type="bug_report",
        created_by="developer",
    )
    fetched = await state.get_request(request.request_id)
    assert fetched.status == RequestStatus.COMPLETED


async def test_bugfix_flow_shorter_pipeline(system):
    """Bug fix should have fewer stages than feature."""
    orchestrator, state, events = system
    feature = await orchestrator.submit(description="New feature", task_type="feature_request", created_by="u")
    bugfix = await orchestrator.submit(description="Fix bug", task_type="bug_report", created_by="u")

    feature_subtasks = await state.get_subtasks_for_request(feature.request_id)
    bugfix_subtasks = await state.get_subtasks_for_request(bugfix.request_id)
    assert len(bugfix_subtasks) <= len(feature_subtasks)


# ── Doc Flow ─────────────────────────────────────

async def test_doc_flow_completes(system):
    orchestrator, state, events = system
    request = await orchestrator.submit(
        description="Update API documentation",
        task_type="doc_request",
        created_by="developer",
    )
    fetched = await state.get_request(request.request_id)
    assert fetched.status == RequestStatus.COMPLETED
    subtasks = await state.get_subtasks_for_request(request.request_id)
    agents = {s.agent_id for s in subtasks}
    assert "prd_specialist" in agents
    assert "user_story_author" in agents


# ── Demo Flow ────────────────────────────────────

async def test_demo_flow_completes(system):
    orchestrator, state, events = system
    request = await orchestrator.submit(
        description="Prepare demo environment",
        task_type="demo_request",
        created_by="developer",
    )
    fetched = await state.get_request(request.request_id)
    assert fetched.status == RequestStatus.COMPLETED


# ── Failure & Recovery ───────────────────────────

async def test_invalid_task_type_rejected(system):
    orchestrator, state, events = system
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        await orchestrator.submit(
            description="Bad type",
            task_type="invalid",
            created_by="u",
        )


async def test_multiple_concurrent_requests(system):
    """Submit multiple requests and verify all complete."""
    import asyncio
    orchestrator, state, events = system
    tasks = [
        orchestrator.submit(description=f"Task {i}", task_type="doc_request", created_by="u")
        for i in range(3)
    ]
    results = await asyncio.gather(*tasks)
    for r in results:
        fetched = await state.get_request(r.request_id)
        assert fetched.status == RequestStatus.COMPLETED
