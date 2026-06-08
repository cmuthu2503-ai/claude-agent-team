"""HAI-07 — backend REST adapter tests.

Uses httpx.MockTransport, so no live backend is needed. Run inside the
agent-team-mcp service (it carries httpx); the backend's pytest harness does not
mount mcp_server/.
"""

import httpx
import pytest

from backend_client import BackendClient, BackendError, extract_error_message, root_cause


def _client(handler, token: str = "svc-tok") -> BackendClient:
    return BackendClient("http://backend:8000", token, transport=httpx.MockTransport(handler))


async def test_get_unwraps_envelope_and_sends_auth():
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("authorization", "")
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"data": [{"id": "REQ-1"}], "meta": None, "error": None})

    data = await _client(handler).get("/api/v1/requests", params={"status": "failed"})
    assert data == [{"id": "REQ-1"}]
    assert seen["auth"] == "Bearer svc-tok"
    assert "status=failed" in seen["url"]


async def test_non_envelope_body_returned_as_is():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "healthy"})

    assert await _client(handler).get("/api/v1/health") == {"status": "healthy"}


async def test_error_envelope_becomes_backend_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"data": None, "meta": None, "error": {"code": "x", "message": "nope"}}
        )

    with pytest.raises(BackendError) as exc:
        await _client(handler).post("/api/v1/projects", json={})
    assert "403" in str(exc.value)
    assert "nope" in str(exc.value)


async def test_fastapi_detail_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not found"})

    with pytest.raises(BackendError) as exc:
        await _client(handler).get("/api/v1/requests/REQ-x")
    assert "not found" in str(exc.value)


async def test_transport_failure_is_root_caused():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(BackendError) as exc:
        await _client(handler).get("/api/v1/health")
    assert "Cannot reach" in str(exc.value)
    assert "ConnectError" in str(exc.value) or "connection refused" in str(exc.value)


def test_no_token_means_no_auth_header():
    assert BackendClient("http://b", "")._headers == {}
    assert BackendClient("http://b", "t")._headers == {"Authorization": "Bearer t"}


def test_extract_error_message_variants():
    assert extract_error_message({"error": {"message": "m"}}) == "m"
    assert extract_error_message({"error": "e"}) == "e"
    assert extract_error_message({"detail": "d"}) == "d"
    assert extract_error_message(None) is None
    assert extract_error_message("raw") == "raw"


def test_root_cause_walks_to_deepest():
    try:
        try:
            raise ValueError("inner")
        except ValueError as e:
            raise RuntimeError("outer") from e
    except RuntimeError as e:
        rc = root_cause(e)
    assert "ValueError: inner" in rc
