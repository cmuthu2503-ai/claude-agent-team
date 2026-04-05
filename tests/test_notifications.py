"""Tests for notification system — catalog, service, reports."""

import pytest

from src.notifications.catalog import NotificationCatalog, CATALOG
from src.notifications.service import NotificationService
from src.notifications.reports import WeeklyReportGenerator
from src.core.events import EventEmitter
from src.state.sqlite_store import SQLiteStateStore
from src.models.base import Request


@pytest.fixture
async def setup(tmp_path):
    state = SQLiteStateStore(db_path=str(tmp_path / "test.db"))
    await state.initialize()
    events = EventEmitter()
    yield state, events
    await state.close()


# ── Catalog Tests ────────────────────────────────

def test_catalog_has_25_events():
    assert len(CATALOG) == 25

def test_catalog_event_ids_sequential():
    for i in range(1, 26):
        assert f"N-{i:03d}" in CATALOG

def test_catalog_lookup():
    catalog = NotificationCatalog()
    event = catalog.get("N-001")
    assert event is not None
    assert event.name == "request_received"

def test_catalog_format_title():
    catalog = NotificationCatalog()
    title = catalog.format_title("N-003", {"request_id": "REQ-042"})
    assert "completed" in title.lower()

def test_catalog_format_message():
    catalog = NotificationCatalog()
    msg = catalog.format_message("N-004", {"request_id": "REQ-042", "error": "timeout"})
    assert "REQ-042" in msg
    assert "timeout" in msg

def test_catalog_toast_events():
    catalog = NotificationCatalog()
    toast_events = [e for e in catalog.all_events() if e.show_toast]
    assert len(toast_events) >= 8  # critical + warning events with toast

def test_budget_events_exist():
    catalog = NotificationCatalog()
    assert catalog.get("N-024") is not None  # budget warning
    assert catalog.get("N-025") is not None  # budget exceeded
    assert catalog.get("N-024").severity == "warning"
    assert catalog.get("N-025").severity == "critical"


# ── Service Tests ────────────────────────────────

async def test_notify_creates_notification(setup):
    state, events = setup
    service = NotificationService(state, events)
    notif = await service.notify("N-001", {"request_id": "REQ-001"})
    assert notif is not None
    assert notif.event_id == "N-001"
    stored = await state.get_notifications(limit=1)
    assert len(stored) == 1

async def test_notify_emits_websocket_event(setup):
    state, events = setup
    queue = events.subscribe()
    service = NotificationService(state, events)
    await service.notify("N-003", {"request_id": "REQ-042"})
    collected = []
    while not queue.empty():
        collected.append(await queue.get())
    types = [e["type"] for e in collected]
    assert "notification.new" in types

async def test_notify_emits_toast_for_critical(setup):
    state, events = setup
    queue = events.subscribe()
    service = NotificationService(state, events)
    await service.notify("N-004", {"request_id": "REQ-042", "error": "crash"})
    collected = []
    while not queue.empty():
        collected.append(await queue.get())
    types = [e["type"] for e in collected]
    assert "notification.toast" in types

async def test_notify_unknown_event(setup):
    state, events = setup
    service = NotificationService(state, events)
    result = await service.notify("N-999", {})
    assert result is None


# ── Report Tests ─────────────────────────────────

async def test_report_generates_markdown(setup):
    state, events = setup
    # Add a request
    req = Request(request_id="REQ-R01", description="Test report", created_by="user")
    await state.create_request(req)

    gen = WeeklyReportGenerator(state)
    report = gen.generate()
    report_text = await report
    assert "Weekly Task Report" in report_text
    assert "Summary" in report_text
    assert "Cost" in report_text

async def test_report_saves_to_file(setup, tmp_path):
    state, events = setup
    gen = WeeklyReportGenerator(state)
    report = await gen.generate()
    path = await gen.save(report, str(tmp_path / "reports"))
    assert path.endswith(".md")
    from pathlib import Path
    assert Path(path).exists()
