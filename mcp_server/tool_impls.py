"""Tool implementations (HAI-08).

Maps manifest tool names → async callables that call the backend through the
HAI-07 adapter. The manifest (tools_manifest.yaml) controls WHICH tools are
exposed and their min_role; the implementations live here. Monitor tools
(HAI-10..16) and action tools (HAI-33+) add their callables to
``build_tool_impls`` as they're built.
"""

from __future__ import annotations

from typing import Any, Callable

try:  # package vs flat (container) layout
    from mcp_server.backend_client import BackendClient
except ModuleNotFoundError:  # pragma: no cover - container runs flat
    from backend_client import BackendClient  # type: ignore[no-redef]


def build_tool_impls(client: BackendClient) -> dict[str, Callable]:
    """Build the name → async-callable map, closing over the backend client."""

    async def monitor_backend_health() -> Any:
        """Agent Team backend health + agent execution mode (real_llm vs mock)."""
        return await client.get("/api/v1/health")

    async def monitor_list_requests(
        status: str | None = None,
        project_id: str | None = None,
        per_page: int = 20,
    ) -> Any:
        """List Agent Team requests, newest first.

        Filters (all optional):
          status:     e.g. 'failed', 'completed', 'in_progress', 'analyzing'
          project_id: restrict to one project
          per_page:   page size (default 20)
        """
        params: dict[str, Any] = {"per_page": per_page}
        if status:
            params["status"] = status
        if project_id:
            params["project_id"] = project_id
        return await client.get("/api/v1/requests", params=params)

    async def monitor_get_request(request_id: str) -> Any:
        """Full detail for one request: status, subtasks/stories, project, timings."""
        return await client.get(f"/api/v1/requests/{request_id}")

    async def monitor_list_projects(include_archived: bool = False) -> Any:
        """List projects. Set include_archived=true to include archived ones."""
        params = {"include_archived": include_archived} if include_archived else None
        return await client.get("/api/v1/projects", params=params)

    async def monitor_get_project(project_id: str) -> Any:
        """Project detail: status, build-plan rollup, settings."""
        return await client.get(f"/api/v1/projects/{project_id}")

    async def monitor_get_costs(project_id: str | None = None) -> Any:
        """Token/cost rollups across the platform, or scoped to one project."""
        params = {"project_id": project_id} if project_id else None
        return await client.get("/api/v1/cost/dashboard", params=params)

    async def monitor_recent_failures(per_page: int = 20) -> Any:
        """Recently failed requests (status=failed), newest first."""
        return await client.get("/api/v1/requests", params={"status": "failed", "per_page": per_page})

    async def monitor_deploy_health() -> Any:
        """Latest deploy health verdict (HEALTHY / UNHEALTHY / unknown) + deployment id/step."""
        return await client.get("/api/v1/ops/latest")

    async def monitor_team_status() -> Any:
        """The agents with their resolved/assigned model, override state, tool count, and busy state."""
        return await client.get("/api/v1/agents")

    return {
        "monitor_backend_health": monitor_backend_health,
        "monitor_list_requests": monitor_list_requests,
        "monitor_get_request": monitor_get_request,
        "monitor_list_projects": monitor_list_projects,
        "monitor_get_project": monitor_get_project,
        "monitor_get_costs": monitor_get_costs,
        "monitor_recent_failures": monitor_recent_failures,
        "monitor_deploy_health": monitor_deploy_health,
        "monitor_team_status": monitor_team_status,
    }
