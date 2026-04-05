"""Integration tests for the orchestrator — end-to-end mock request flow."""

import pytest

from src.config.loader import ConfigLoader
from src.core.events import EventEmitter
from src.core.orchestrator import Orchestrator
from src.models.base import RequestStatus
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def setup(tmp_path):
    config = ConfigLoader()
    config.load_all()
    state = SQLiteStateStore(db_path=str(tmp_path / "test.db"))
    await state.initialize()
    events = EventEmitter()
    orchestrator = Orchestrator(config=config, state=state, events=events)
    yield orchestrator, state, events
    await state.close()


async def test_submit_request_creates_in_state(setup):
    orchestrator, state, events = setup
    request = await orchestrator.submit(
        description="Build a login page",
        task_type="feature_request",
        created_by="testuser",
    )
    assert request.request_id.startswith("REQ-")
    fetched = await state.get_request(request.request_id)
    assert fetched is not None


async def test_submit_creates_subtasks(setup):
    orchestrator, state, events = setup
    request = await orchestrator.submit(
        description="Build a login page",
        task_type="feature_request",
        created_by="testuser",
    )
    subtasks = await state.get_subtasks_for_request(request.request_id)
    assert len(subtasks) > 0
    agent_ids = [s.agent_id for s in subtasks]
    assert "prd_specialist" in agent_ids


async def test_submit_completes_with_mock_executor(setup):
    orchestrator, state, events = setup
    request = await orchestrator.submit(
        description="Fix a typo in README",
        task_type="doc_request",
        created_by="testuser",
    )
    fetched = await state.get_request(request.request_id)
    assert fetched.status == RequestStatus.COMPLETED


async def test_events_emitted(setup):
    orchestrator, state, events = setup
    queue = events.subscribe()
    request = await orchestrator.submit(
        description="Test events",
        task_type="doc_request",
        created_by="testuser",
    )
    collected = []
    while not queue.empty():
        collected.append(await queue.get())
    event_types = [e["type"] for e in collected]
    assert "request.created" in event_types
    assert "request.completed" in event_types


async def test_unknown_trigger_fails(setup):
    """Test that an invalid task_type is rejected by the Pydantic model."""
    orchestrator, state, events = setup
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        await orchestrator.submit(
            description="Unknown type",
            task_type="nonexistent_trigger",
            created_by="testuser",
        )


async def test_doc_workflow_agents_called_in_order(setup):
    orchestrator, state, events = setup
    queue = events.subscribe()
    request = await orchestrator.submit(
        description="Write docs",
        task_type="doc_request",
        created_by="testuser",
    )
    collected = []
    while not queue.empty():
        collected.append(await queue.get())
    agent_starts = [
        e["data"]["agent_id"]
        for e in collected
        if e["type"] == "agent.started"
    ]
    assert agent_starts.index("prd_specialist") < agent_starts.index("user_story_author")
