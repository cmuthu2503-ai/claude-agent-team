"""HAI-10+ — monitor tool implementations (httpx.MockTransport, no live backend)."""

import json

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


async def test_monitor_recent_failures_filters_status():
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"data": [{"request_id": "REQ-F", "status": "failed"}], "meta": None, "error": None})

    impls = build_tool_impls(_client(handler))
    data = await impls["monitor_recent_failures"](per_page=10)
    assert data[0]["status"] == "failed"
    assert "/api/v1/requests" in seen["url"]
    assert "status=failed" in seen["url"]
    assert "per_page=10" in seen["url"]


async def test_monitor_deploy_health_and_team_status():
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        if req.url.path.endswith("/ops/latest"):
            return httpx.Response(200, json={"verdict": "HEALTHY", "deployment_id": "d-1"})
        return httpx.Response(200, json={"data": [{"agent_id": "backend_specialist"}], "meta": None, "error": None})

    impls = build_tool_impls(_client(handler))
    health = await impls["monitor_deploy_health"]()
    assert health["verdict"] == "HEALTHY"           # /ops/latest is not enveloped → returned as-is
    assert seen["url"].endswith("/api/v1/ops/latest")

    team = await impls["monitor_team_status"]()
    assert team[0]["agent_id"] == "backend_specialist"
    assert seen["url"].endswith("/api/v1/agents")


def test_registry_has_expected_tools():
    impls = build_tool_impls(_client(lambda req: httpx.Response(200, json={})))
    for name in (
        "monitor_backend_health", "monitor_list_requests", "monitor_get_request",
        "monitor_list_projects", "monitor_get_project", "monitor_get_costs",
        "monitor_recent_failures", "monitor_deploy_health", "monitor_team_status",
    ):
        assert name in impls


# ── HAI-43 — lifecycle read companions ───────────────────────────────────────

async def test_lifecycle_read_companions_build_paths():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"data": {"ok": True}, "meta": None, "error": None})

    impls = build_tool_impls(_client(handler))
    for tool, suffix in [
        ("project_get_prd", "/api/v1/projects/proj-1/prd"),
        ("project_get_apispec", "/api/v1/projects/proj-1/api-spec"),
        ("project_get_buildplan", "/api/v1/projects/proj-1/build-plan/rollup"),
        ("project_get_tasks", "/api/v1/projects/proj-1/tasks"),
    ]:
        out = await impls[tool]("proj-1")
        assert out == {"ok": True}
        assert seen["url"].endswith(suffix), f"{tool} -> {seen['url']}"


def test_companions_are_registered_viewer_tier():
    # All four are present in the impl map (manifest min_role viewer is asserted in
    # test_manifest's role-filter coverage).
    impls = build_tool_impls(_client(lambda r: httpx.Response(200, json={"data": None})))
    assert {"project_get_prd", "project_get_apispec", "project_get_buildplan", "project_get_tasks"} <= set(impls)


# ── HAI-60 — proposal queue read companions ──────────────────────────────────

async def test_monitor_list_proposals_filters():
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(
            200, json={"data": [{"proposal_id": "stok-1", "status": "pending"}], "meta": {"count": 1}, "error": None}
        )

    impls = build_tool_impls(_client(handler))
    data = await impls["monitor_list_proposals"](status="pending", action_type="project.create")
    assert data[0]["proposal_id"] == "stok-1"
    assert "/api/v1/proposals" in seen["url"]
    assert "status=pending" in seen["url"]
    assert "action_type=project.create" in seen["url"]
    assert "limit=100" in seen["url"]


async def test_monitor_get_proposal_builds_path():
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"data": {"proposal_id": "stok-9", "status": "confirmed"}, "meta": None, "error": None})

    impls = build_tool_impls(_client(handler))
    data = await impls["monitor_get_proposal"]("stok-9")
    assert data["status"] == "confirmed"
    assert seen["url"].endswith("/api/v1/proposals/stok-9")


# ── HAI-61 — gated action tools (propose → human-approved) ───────────────────

async def test_propose_create_project_posts_proposal():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["method"] = req.method
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            201, json={"data": {"proposal_id": "stok-new", "status": "pending", "action_type": "project.create"}, "meta": None, "error": None}
        )

    impls = build_tool_impls(_client(handler))
    out = await impls["propose_create_project"](name="test project", description="a demo")

    assert seen["method"] == "POST"
    assert seen["url"].endswith("/api/v1/proposals")
    assert seen["body"]["action_type"] == "project.create"
    assert seen["body"]["payload"] == {"name": "test project", "description": "a demo"}
    assert out["proposal_id"] == "stok-new"
    assert out["status"] == "pending"


async def test_propose_create_project_omits_blank_description():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(201, json={"data": {"proposal_id": "stok-x", "status": "pending"}, "meta": None, "error": None})

    impls = build_tool_impls(_client(handler))
    await impls["propose_create_project"](name="solo")
    assert seen["body"]["payload"] == {"name": "solo"}        # no description key when not given


async def test_propose_submit_request_defaults_and_target_ref():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(201, json={"data": {"proposal_id": "stok-r", "status": "pending"}, "meta": None, "error": None})

    impls = build_tool_impls(_client(handler))
    # project_id present → rides in target_ref AND payload
    await impls["propose_submit_request"](description="add a dark mode toggle", project_id="proj-3")
    b = seen["body"]
    assert b["action_type"] == "request.submit"
    assert b["target_ref"] == "proj-3"
    assert b["payload"] == {
        "description": "add a dark mode toggle",
        "task_type": "feature_request",     # default
        "priority": "medium",               # default
        "project_id": "proj-3",
    }

    # project_id omitted → target_ref is None, no project_id key in payload
    await impls["propose_submit_request"](description="fix login bug", task_type="bug_fix", priority="high")
    b2 = seen["body"]
    assert b2["target_ref"] is None
    assert "project_id" not in b2["payload"]
    assert b2["payload"]["task_type"] == "bug_fix"
    assert b2["payload"]["priority"] == "high"


def test_registry_has_propose_and_proposal_read_tools():
    impls = build_tool_impls(_client(lambda req: httpx.Response(200, json={})))
    for name in (
        "monitor_list_proposals", "monitor_get_proposal",
        "propose_create_project", "propose_submit_request",
    ):
        assert name in impls
