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


# ── HAI-61/65 — natural-named gated action tools (→ POST /proposals) ─────────

def _capture_body():
    """A MockTransport handler that records the POSTed JSON body into ``seen``."""
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["method"] = req.method
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            201,
            json={"data": {"proposal_id": "stok-new", "status": "pending"}, "meta": None, "error": None},
        )

    return seen, handler


async def test_create_project_posts_proposal():
    seen, handler = _capture_body()
    impls = build_tool_impls(_client(handler))
    out = await impls["create_project"](name="test project", description="a demo")

    assert seen["method"] == "POST"
    assert seen["url"].endswith("/api/v1/proposals")
    assert seen["body"]["action_type"] == "project.create"
    assert seen["body"]["payload"] == {"name": "test project", "description": "a demo"}
    assert "target_ref" not in seen["body"]                  # create has no target
    assert out["proposal_id"] == "stok-new"


async def test_create_project_omits_blank_description():
    seen, handler = _capture_body()
    impls = build_tool_impls(_client(handler))
    await impls["create_project"](name="solo")
    assert seen["body"]["payload"] == {"name": "solo"}        # no description key when not given


async def test_set_project_brief():
    seen, handler = _capture_body()
    impls = build_tool_impls(_client(handler))
    await impls["set_project_brief"](project_id="proj-1", content="A todo app.")
    assert seen["body"]["action_type"] == "project.brief.set"
    assert seen["body"]["target_ref"] == "proj-1"
    assert seen["body"]["payload"] == {"content": "A todo app."}


async def test_generate_tools_target_ref_and_action_type():
    # the five plain project-scoped generators: target_ref=project_id, no payload
    cases = [
        ("generate_prd", "prd.generate"),
        ("generate_api_spec", "apispec.generate"),
        ("generate_epics", "epics.generate"),
        ("generate_tasks", "tasks.generate"),
        ("generate_build_plan", "buildplan.generate"),
    ]
    for tool, action_type in cases:
        seen, handler = _capture_body()
        impls = build_tool_impls(_client(handler))
        await impls[tool]("proj-7")
        assert seen["body"]["action_type"] == action_type, tool
        assert seen["body"]["target_ref"] == "proj-7", tool
        assert "payload" not in seen["body"], tool          # these carry no payload


async def test_generate_features_optional_epic():
    seen, handler = _capture_body()
    impls = build_tool_impls(_client(handler))
    await impls["generate_features"]("proj-1")
    assert seen["body"]["action_type"] == "features.generate"
    assert seen["body"]["target_ref"] == "proj-1"
    assert "payload" not in seen["body"]                     # no epic → no payload

    await impls["generate_features"]("proj-1", epic_id="epic-9")
    assert seen["body"]["payload"] == {"epic_id": "epic-9"}


async def test_dispatch_tasks_optional_task_ids():
    seen, handler = _capture_body()
    impls = build_tool_impls(_client(handler))
    await impls["dispatch_tasks"]("proj-2")
    assert seen["body"]["action_type"] == "task.dispatch"
    assert seen["body"]["target_ref"] == "proj-2"
    assert "payload" not in seen["body"]                     # omit → dispatch all dispatchable

    await impls["dispatch_tasks"]("proj-2", task_ids=["t1", "t2"])
    assert seen["body"]["payload"] == {"task_ids": ["t1", "t2"]}


async def test_submit_request_defaults_and_target_ref():
    seen, handler = _capture_body()
    impls = build_tool_impls(_client(handler))
    # project_id present → rides in target_ref AND payload
    await impls["submit_request"](description="add a dark mode toggle", project_id="proj-3")
    b = seen["body"]
    assert b["action_type"] == "request.submit"
    assert b["target_ref"] == "proj-3"
    assert b["payload"] == {
        "description": "add a dark mode toggle",
        "task_type": "feature_request",     # default
        "priority": "medium",               # default
        "project_id": "proj-3",
    }

    # project_id omitted → no target_ref key, no project_id key in payload
    await impls["submit_request"](description="fix login bug", task_type="bug_fix", priority="high")
    b2 = seen["body"]
    assert "target_ref" not in b2
    assert "project_id" not in b2["payload"]
    assert b2["payload"]["task_type"] == "bug_fix"
    assert b2["payload"]["priority"] == "high"


def test_registry_has_action_and_proposal_read_tools():
    impls = build_tool_impls(_client(lambda req: httpx.Response(200, json={})))
    for name in (
        "monitor_list_proposals", "monitor_get_proposal",
        "create_project", "set_project_brief", "generate_prd", "generate_api_spec",
        "generate_epics", "generate_features", "generate_tasks", "generate_build_plan",
        "dispatch_tasks", "submit_request",
    ):
        assert name in impls


# ── HAI-66 — finalize actions + startable-task reads ─────────────────────────

async def test_finalize_tools_post_proposal_with_target_ref():
    cases = [
        ("finalize_prd", "prd.finalize"),
        ("finalize_api_spec", "apispec.finalize"),
        ("finalize_tasks", "tasks.finalize"),
        ("finalize_epics", "epics.finalize"),
        ("finalize_features", "features.finalize"),
    ]
    for tool, action_type in cases:
        seen, handler = _capture_body()
        impls = build_tool_impls(_client(handler))
        await impls[tool]("proj-9")
        assert seen["method"] == "POST", tool
        assert seen["url"].endswith("/api/v1/proposals"), tool
        assert seen["body"]["action_type"] == action_type, tool
        assert seen["body"]["target_ref"] == "proj-9", tool
        assert "payload" not in seen["body"], tool


async def test_startable_and_next_wave_read_tools():
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(
            200, json={"data": [{"task_id": "t1"}], "meta": {"count": 1}, "error": None}
        )

    impls = build_tool_impls(_client(handler))

    data = await impls["get_startable_tasks"]("proj-2")
    assert data == [{"task_id": "t1"}]
    assert seen["url"].endswith("/api/v1/projects/proj-2/tasks/startable")

    await impls["get_next_wave_tasks"]("proj-2")
    assert seen["url"].endswith("/api/v1/projects/proj-2/tasks/next-wave")


def test_registry_has_finalize_and_wave_tools():
    impls = build_tool_impls(_client(lambda req: httpx.Response(200, json={})))
    for name in (
        "finalize_prd", "finalize_api_spec", "finalize_tasks", "finalize_epics",
        "finalize_features", "get_startable_tasks", "get_next_wave_tasks",
    ):
        assert name in impls
