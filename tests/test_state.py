"""Tests for the state layer — SQLite StateStore CRUD operations."""

import pytest

from src.models.base import (
    Notification,
    NotificationSeverity,
    Request,
    RequestStatus,
    Subtask,
    TokenUsage,
    User,
    UserRole,
)
from src.state.sqlite_store import SQLiteStateStore


@pytest.fixture
async def store(tmp_path):
    db_path = str(tmp_path / "test.db")
    s = SQLiteStateStore(db_path=db_path)
    await s.initialize()
    yield s
    await s.close()


# ── Request CRUD ─────────────────────────────────


async def test_create_and_get_request(store):
    req = Request(request_id="REQ-001", description="Test feature", created_by="user1")
    await store.create_request(req)
    fetched = await store.get_request("REQ-001")
    assert fetched is not None
    assert fetched.request_id == "REQ-001"
    assert fetched.description == "Test feature"
    assert fetched.status == RequestStatus.RECEIVED


async def test_get_nonexistent_request(store):
    result = await store.get_request("NOPE")
    assert result is None


async def test_update_request_status(store):
    req = Request(request_id="REQ-002", description="Test", created_by="user1")
    await store.create_request(req)
    req.status = RequestStatus.COMPLETED
    await store.update_request(req)
    fetched = await store.get_request("REQ-002")
    assert fetched.status == RequestStatus.COMPLETED


async def test_list_requests(store):
    for i in range(5):
        await store.create_request(
            Request(request_id=f"REQ-{i:03d}", description=f"Test {i}", created_by="user1")
        )
    all_reqs = await store.list_requests(limit=10)
    assert len(all_reqs) == 5


async def test_list_requests_with_filter(store):
    r1 = Request(request_id="REQ-A", description="A", created_by="u")
    r2 = Request(request_id="REQ-B", description="B", created_by="u", status=RequestStatus.COMPLETED)
    await store.create_request(r1)
    await store.create_request(r2)
    await store.update_request(r2)
    completed = await store.list_requests(status="completed")
    assert len(completed) == 1
    assert completed[0].request_id == "REQ-B"


# ── Subtask CRUD ─────────────────────────────────


async def test_create_and_get_subtask(store):
    req = Request(request_id="REQ-010", description="Parent", created_by="u")
    await store.create_request(req)
    sub = Subtask(subtask_id="REQ-010-BE", request_id="REQ-010", agent_id="backend_specialist")
    await store.create_subtask(sub)
    fetched = await store.get_subtask("REQ-010-BE")
    assert fetched is not None
    assert fetched.agent_id == "backend_specialist"


async def test_get_subtasks_for_request(store):
    req = Request(request_id="REQ-020", description="Multi", created_by="u")
    await store.create_request(req)
    await store.create_subtask(Subtask(subtask_id="REQ-020-BE", request_id="REQ-020", agent_id="backend_specialist"))
    await store.create_subtask(Subtask(subtask_id="REQ-020-FE", request_id="REQ-020", agent_id="frontend_specialist"))
    subs = await store.get_subtasks_for_request("REQ-020")
    assert len(subs) == 2


# ── User CRUD ────────────────────────────────────


async def test_create_and_get_user(store):
    user = User(user_id="u1", username="testuser", email="test@test.com", role=UserRole.DEVELOPER)
    await store.create_user(user, "hashed_pw")
    result = await store.get_user_by_username("testuser")
    assert result is not None
    fetched_user, pw_hash = result
    assert fetched_user.username == "testuser"
    assert pw_hash == "hashed_pw"


async def test_get_nonexistent_user(store):
    result = await store.get_user_by_username("nobody")
    assert result is None


# ── Token Usage ──────────────────────────────────


async def test_record_and_get_token_usage(store):
    req = Request(request_id="REQ-030", description="Cost test", created_by="u")
    await store.create_request(req)
    usage = TokenUsage(
        usage_id="tu-1", request_id="REQ-030", subtask_id="REQ-030-BE",
        agent_id="backend_specialist", model="claude-sonnet-4-6",
        input_tokens=1000, output_tokens=2000, cost_usd=0.045,
    )
    await store.record_token_usage(usage)
    records = await store.get_token_usage_for_request("REQ-030")
    assert len(records) == 1
    assert records[0].cost_usd == 0.045


async def test_daily_cost(store):
    usage = TokenUsage(
        usage_id="tu-2", request_id="REQ-031", subtask_id="REQ-031-BE",
        agent_id="be", model="sonnet", input_tokens=100, output_tokens=200, cost_usd=1.50,
    )
    await store.record_token_usage(usage)
    cost = await store.get_daily_cost()
    assert cost == 1.50


# ── Notifications ────────────────────────────────


async def test_create_and_list_notifications(store):
    notif = Notification(
        notification_id="n-1", event_id="N-001", severity=NotificationSeverity.INFO,
        title="Test", message="Hello",
    )
    await store.create_notification(notif)
    notifs = await store.get_notifications()
    assert len(notifs) == 1
    assert notifs[0].title == "Test"


async def test_mark_notification_read(store):
    notif = Notification(
        notification_id="n-2", event_id="N-002", severity=NotificationSeverity.WARNING,
        title="Alert", message="Something happened",
    )
    await store.create_notification(notif)
    await store.mark_notification_read("n-2")
    notifs = await store.get_notifications(unread_only=True)
    assert len(notifs) == 0
