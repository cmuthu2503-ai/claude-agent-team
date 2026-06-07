"""Tests for SSEHeadersMiddleware — REQ-005.

We don't depend on the real app (which has heavy startup deps). Instead
we mount the middleware on a minimal FastAPI app with two routes:
    - GET /api/v1/events  → returns plain JSON
    - GET /api/v1/health  → returns plain JSON
and assert the headers are set on the first path and absent from the
second. We also exercise a StreamingResponse to confirm headers reach
the wire BEFORE the stream body (the actual SSE production case).
"""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from src.api.middleware.sse_headers import SSEHeadersMiddleware


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(SSEHeadersMiddleware)

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/events")
    async def events() -> dict[str, str]:
        return {"event": "ping"}

    @app.get("/api/v1/requests/abc/events")
    async def request_events() -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            yield b"data: hello\n\n"
            yield b"data: world\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return TestClient(app)


def test_events_path_has_sse_headers(client: TestClient) -> None:
    """REQ-005 ACs 1 & 2 — both headers present on /events responses."""
    resp = client.get("/api/v1/events")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache, no-transform"
    assert resp.headers["x-accel-buffering"] == "no"


def test_non_events_path_omits_anti_buffer_header(client: TestClient) -> None:
    """REQ-005 AC 3 — health (no /events in path) MUST NOT carry the
    nginx anti-buffering header. Cache-Control on non-SSE responses is
    framework-default and not asserted here either way."""
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert "x-accel-buffering" not in {k.lower() for k in resp.headers.keys()}


def test_streaming_response_carries_headers_before_body(client: TestClient) -> None:
    """REQ-005 AC 4 — for a real StreamingResponse the headers are part
    of the HTTP response start (before any event bytes), so a TestClient
    that reads .headers BEFORE .text proves they were flushed first."""
    with client.stream("GET", "/api/v1/requests/abc/events") as resp:
        # Inspect headers BEFORE consuming the body — this would fail
        # if the middleware appended headers only after the stream
        # finished.
        assert resp.headers["cache-control"] == "no-cache, no-transform"
        assert resp.headers["x-accel-buffering"] == "no"
        body = b"".join(resp.iter_bytes())
    assert b"data: hello" in body
    assert b"data: world" in body


def test_substring_match_applies_to_nested_paths(client: TestClient) -> None:
    """The middleware checks substring `/events` in the full path, so
    nested routes like /api/v1/requests/<id>/events also pick up the
    headers without needing per-route opt-in."""
    with client.stream("GET", "/api/v1/requests/abc/events") as resp:
        assert resp.headers["x-accel-buffering"] == "no"
