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


async def test_monitor_get_request_builds_path():
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"data": {"request_id": "REQ-9", "status": "completed"}, "meta": None, "error": None})

    impls = build_tool_impls(_client(handler))
    data = await impls["monitor_get_request"]("REQ-9")
    assert data["request_id"] == "REQ-9"
    assert seen["url"].endswith("/api/v1/requests/REQ-9")


async def test_monitor_list_projects():
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"data": [{"project_id": "proj-1"}], "meta": None, "error": None})

    impls = build_tool_impls(_client(handler))
    assert await impls["monitor_list_projects"]() == [{"project_id": "proj-1"}]
    assert seen["url"].endswith("/api/v1/projects")
    # archived flag only sent when requested
    await impls["monitor_list_projects"](include_archived=True)
    assert "include_archived=true" in seen["url"].lower()


async def test_monitor_get_project_and_costs():
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"data": {"ok": True}, "meta": None, "error": None})

    impls = build_tool_impls(_client(handler))
    await impls["monitor_get_project"]("proj-7")
    assert seen["url"].endswith("/api/v1/projects/proj-7")

    await impls["monitor_get_costs"]()
    assert seen["url"].endswith("/api/v1/cost/dashboard")
    await impls["monitor_get_costs"](project_id="proj-7")
    assert "project_id=proj-7" in seen["url"]


def test_registry_has_expected_tools():
    impls = build_tool_impls(_client(lambda req: httpx.Response(200, json={})))
    for name in (
        "monitor_backend_health", "monitor_list_requests", "monitor_get_request",
        "monitor_list_projects", "monitor_get_project", "monitor_get_costs",
    ):
        assert name in impls
