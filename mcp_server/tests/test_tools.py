"""HAI-10+ — monitor tool implementations (httpx.MockTransport, no live backend)."""

import httpx

from backend_client import BackendClient
from tool_impls import build_tool_impls


def _client(handler) -> BackendClient:
    return BackendClient("http://backend:8000", "svc-tok", transport=httpx.MockTransport(handler))


async def test_monitor_list_requests_calls_endpoint_with_filters():
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization", "")
        return httpx.Response(
            200, json={"data": [{"request_id": "REQ-1", "status": "failed"}], "meta": None, "error": None}
        )

    impls = build_tool_impls(_client(handler))
    data = await impls["monitor_list_requests"](status="failed", per_page=5)

    assert data == [{"request_id": "REQ-1", "status": "failed"}]
    assert "/api/v1/requests" in seen["url"]
    assert "status=failed" in seen["url"]
    assert "per_page=5" in seen["url"]
    assert seen["auth"] == "Bearer svc-tok"


async def test_monitor_list_requests_no_filters():
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"data": [], "meta": None, "error": None})

    impls = build_tool_impls(_client(handler))
    assert await impls["monitor_list_requests"]() == []
    # Only per_page sent; no status/project_id when not provided.
    assert "per_page=20" in seen["url"]
    assert "status=" not in seen["url"]


def test_registry_has_expected_tools():
    impls = build_tool_impls(_client(lambda req: httpx.Response(200, json={})))
    assert "monitor_backend_health" in impls
    assert "monitor_list_requests" in impls
