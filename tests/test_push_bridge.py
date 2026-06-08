"""HAI-17/18/54 — outbound push bridge (FR-070..072, FR-074, FR-075, NFR-002)."""

import asyncio
import json

import httpx
import pytest

from src.core.push_bridge import DEFAULT_PUSH_EVENTS, PushBridge


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

def test_default_events_are_the_three_real_ones_only():
    assert DEFAULT_PUSH_EVENTS == {"request.failed", "request.completed", "deploy_health.anomaly_detected"}
    assert "request.deployed" not in DEFAULT_PUSH_EVENTS
    assert not any(e.startswith("proposal.") for e in DEFAULT_PUSH_EVENTS)


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
