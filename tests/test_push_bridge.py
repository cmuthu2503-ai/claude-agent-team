"""HAI-17/18/54 — outbound push bridge (FR-070..072, FR-074, FR-075, NFR-002)."""

import asyncio
import json

import httpx
import pytest

from src.core.push_bridge import (
    DEFAULT_PUSH_EVENTS,
    _build_payload,
    _summarize,
    PushBridge,
)


def _capturing_transport(captured: list, status: int = 200) -> httpx.MockTransport:
    def h(req: httpx.Request) -> httpx.Response:
        captured.append({"url": str(req.url), "content": req.content, "headers": dict(req.headers)})
        return httpx.Response(status)

    return httpx.MockTransport(h)


def _counting_fail_then_ok(counter: dict, fail_first: int) -> httpx.MockTransport:
    def h(req: httpx.Request) -> httpx.Response:
        counter["n"] = counter.get("n", 0) + 1
        if counter["n"] <= fail_first:
            raise httpx.ConnectError("refused")
        return httpx.Response(200)

    return httpx.MockTransport(h)


# ── handler: enqueue-only, filtered ──────────────────────────────────────────

async def test_handler_enqueues_curated_event_only():
    bridge = PushBridge("http://h/w")
    await bridge.handler("request.failed", {"request_id": "REQ-1", "error": "boom"})
    await bridge.handler("agent.started", {"request_id": "REQ-1"})  # not curated → ignored
    assert bridge._queue.qsize() == 1


async def test_handler_never_does_io_or_blocks_when_buffer_full():
    # max_buffer=1, fill it, then a second curated event must be dropped (not raise).
    bridge = PushBridge("http://h/w", max_buffer=1)
    await bridge.handler("request.failed", {"request_id": "A"})
    await bridge.handler("request.failed", {"request_id": "B"})  # buffer full → dropped, no raise
    assert bridge._queue.qsize() == 1


# ── delivery + bounded retry ─────────────────────────────────────────────────

async def test_deliver_success_single_attempt():
    counter: dict = {}
    bridge = PushBridge("http://h/w", retry_backoff=0, transport=_counting_fail_then_ok(counter, 0))
    assert await bridge._deliver_with_retry({"request_id": "R"}) is True
    assert counter["n"] == 1


async def test_deliver_retries_then_succeeds():
    counter: dict = {}
    bridge = PushBridge("http://h/w", max_retries=2, retry_backoff=0, transport=_counting_fail_then_ok(counter, 2))
    assert await bridge._deliver_with_retry({"request_id": "R"}) is True
    assert counter["n"] == 3   # 2 failures + 1 success


async def test_deliver_drops_after_exhausting_retries():
    counter: dict = {}
    # Always fail → max_retries=2 means 3 attempts, then drop (soft-fail, returns False).
    bridge = PushBridge("http://h/w", max_retries=2, retry_backoff=0, transport=_counting_fail_then_ok(counter, 99))
    assert await bridge._deliver_with_retry({"request_id": "R"}) is False
    assert counter["n"] == 3


async def test_4xx_is_retried_then_dropped():
    captured: list = []
    bridge = PushBridge("http://h/w", max_retries=1, retry_backoff=0, transport=_capturing_transport(captured, status=500))
    assert await bridge._deliver_with_retry({"request_id": "R"}) is False
    assert len(captured) == 2   # original + 1 retry


# ── payload + headers ────────────────────────────────────────────────────────

async def test_payload_links_id_and_summary_and_secret_header():
    captured: list = []
    bridge = PushBridge("http://hermes/webhook", secret="s3cret", retry_backoff=0, transport=_capturing_transport(captured))
    await bridge._deliver_with_retry({"request_id": "REQ-9", "event": "request.failed",
                                      "summary": "Request REQ-9 FAILED: boom", "source": "agent-team"})
    body = json.loads(captured[0]["content"])
    assert body["request_id"] == "REQ-9"
    assert "FAILED" in body["summary"]
    assert captured[0]["headers"].get("x-push-secret") == "s3cret"


# ── end-to-end through the worker ────────────────────────────────────────────

async def test_end_to_end_worker_delivers():
    captured: list = []
    bridge = PushBridge("http://h/w", retry_backoff=0, transport=_capturing_transport(captured))
    bridge.start()
    try:
        await bridge.handler("request.failed", {"request_id": "REQ-E2E", "error": "x"})
        await asyncio.wait_for(bridge._queue.join(), timeout=2.0)
    finally:
        await bridge.stop()
    assert len(captured) == 1
    assert json.loads(captured[0]["content"])["request_id"] == "REQ-E2E"


# ── config scope (FR-075) ────────────────────────────────────────────────────

def test_default_events_include_request_and_proposal_lifecycle():
    # The three real request/deploy events …
    assert {"request.failed", "request.completed", "deploy_health.anomaly_detected"} <= DEFAULT_PUSH_EVENTS
    assert "request.deployed" not in DEFAULT_PUSH_EVENTS   # never emitted
    # … plus the full proposal.* lifecycle (HAI-61).
    assert {
        "proposal.created", "proposal.confirmed", "proposal.executed",
        "proposal.failed", "proposal.rejected", "proposal.expired",
    } <= DEFAULT_PUSH_EVENTS


# ── HAI-61 — proposal.* forwarding + token redaction ─────────────────────────

async def test_proposal_created_is_forwarded():
    bridge = PushBridge("http://h/w")
    await bridge.handler("proposal.created", {"proposal_id": "prop-1", "action_type": "deploy"})
    assert bridge._queue.qsize() == 1


def test_proposal_created_payload_redacts_approval_token():
    """THE security test: the raw one-time token must never reach Hermes (the
    proposer), or it could self-approve. It rides the event but is stripped here."""
    payload = _build_payload(
        "proposal.created",
        {
            "proposal_id": "prop-1",
            "action_type": "deploy",
            "target_ref": "proj-9",
            "proposed_by": "service:hermes",
            "approval_token": "SUPER-SECRET-RAW-TOKEN",
        },
    )
    assert payload["proposal_id"] == "prop-1"
    assert "Approval needed" in payload["summary"]
    # token gone from detail and nowhere in the serialized payload
    assert "approval_token" not in payload["detail"]
    assert "SUPER-SECRET-RAW-TOKEN" not in json.dumps(payload)


def test_proposal_summaries_are_human_readable():
    assert "FAILED" in _summarize("proposal.failed", {"proposal_id": "p1", "error": "RuntimeError: boom"})
    assert "executed" in _summarize("proposal.executed", {"proposal_id": "p1", "result_ref": "REQ-7"})
    assert "expired" in _summarize("proposal.expired", {"proposal_id": "p1"})
    assert "rejected" in _summarize("proposal.rejected", {"proposal_id": "p1", "reason": "nope"})
    assert "confirmed" in _summarize("proposal.confirmed", {"proposal_id": "p1", "decided_by": "alice"})


async def test_proposal_created_flows_end_to_end_without_token():
    captured: list = []
    bridge = PushBridge("http://hermes/webhook", retry_backoff=0, transport=_capturing_transport(captured))
    bridge.start()
    try:
        await bridge.handler(
            "proposal.created",
            {"proposal_id": "prop-9", "action_type": "deploy", "approval_token": "RAW"},
        )
        await asyncio.wait_for(bridge._queue.join(), timeout=2.0)
    finally:
        await bridge.stop()
    assert len(captured) == 1
    body = json.loads(captured[0]["content"])
    assert body["proposal_id"] == "prop-9"
    assert "RAW" not in captured[0]["content"].decode()   # token never on the wire


async def test_custom_event_set_overrides_default():
    bridge = PushBridge("http://h/w", events={"deploy_health.anomaly_detected"})
    await bridge.handler("request.failed", {"request_id": "R"})       # not in custom set
    assert bridge._queue.qsize() == 0
    await bridge.handler("deploy_health.anomaly_detected", {"environment": "prod"})
    assert bridge._queue.qsize() == 1


# ── HAI-20 — EventEmitter → PushBridge integration (the real emit path) ───────

async def test_emit_flows_through_bridge_to_webhook():
    from src.core.events import EventEmitter

    captured: list = []
    bridge = PushBridge("http://hermes/webhook", retry_backoff=0, transport=_capturing_transport(captured))
    bridge.start()
    emitter = EventEmitter()
    emitter.on(bridge.handler)
    try:
        # Simulate a real failure being broadcast — the operator-visible "alert".
        await emitter.emit("request.failed", {"request_id": "REQ-INT", "error": "boom"})
        await asyncio.wait_for(bridge._queue.join(), timeout=2.0)
    finally:
        await bridge.stop()

    assert len(captured) == 1
    body = json.loads(captured[0]["content"])
    assert body["request_id"] == "REQ-INT" and "FAILED" in body["summary"]


# ── HAI-19 — pull-only baseline: the platform runs fine with push OFF ─────────

async def test_pull_only_baseline_no_bridge_registered():
    from src.core.events import EventEmitter

    emitter = EventEmitter()
    seen: list = []

    async def other_handler(event_type: str, data: dict) -> None:
        seen.append(event_type)

    emitter.on(other_handler)  # a non-push handler; NO PushBridge registered
    # Emit works cleanly with no push wired up — Hermes would reconcile via pull.
    await emitter.emit("request.failed", {"request_id": "R"})
    await emitter.emit("request.completed", {"request_id": "R"})
    assert seen == ["request.failed", "request.completed"]
