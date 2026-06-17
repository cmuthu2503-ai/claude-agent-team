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

    # ── HAI-43 — lifecycle read companions (Monitor-tier). Let Hermes REVIEW the
    # artifact produced by each gated step (PRD, API spec, build plan, tasks)
    # before proposing/confirming the next one. Reads only — never mutating.

    async def project_get_prd(project_id: str) -> Any:
        """The project's current PRD (latest version) for review between gated steps."""
        return await client.get(f"/api/v1/projects/{project_id}/prd")

    async def project_get_apispec(project_id: str) -> Any:
        """The project's current API specification (latest version)."""
        return await client.get(f"/api/v1/projects/{project_id}/api-spec")

    async def project_get_buildplan(project_id: str) -> Any:
        """The project's build-plan rollup: epics → features → tasks structure + counts."""
        return await client.get(f"/api/v1/projects/{project_id}/build-plan/rollup")

    async def project_get_tasks(project_id: str) -> Any:
        """The project's generated task list (the unit auto-dispatch acts on)."""
        return await client.get(f"/api/v1/projects/{project_id}/tasks")

    # ── HAI-60 — proposal queue read companions (viewer-tier) ──────────────────

    async def monitor_list_proposals(
        status: str | None = None,
        action_type: str | None = None,
        proposed_by: str | None = None,
        limit: int = 100,
    ) -> Any:
        """List governed proposals, newest first.

        Filters (all optional):
          status:      pending | confirmed | rejected | expired
          action_type: e.g. 'project.create', 'request.submit'
          proposed_by: actor string, e.g. 'service:hermes'
          limit:       max rows (default 100)
        """
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        if action_type:
            params["action_type"] = action_type
        if proposed_by:
            params["proposed_by"] = proposed_by
        return await client.get("/api/v1/proposals", params=params)

    async def monitor_get_proposal(proposal_id: str) -> Any:
        """Full detail + current status of one proposal."""
        return await client.get(f"/api/v1/proposals/{proposal_id}")

    # ── HAI-61/65 — gated ACTION tools (developer-tier), named for natural
    # language so a local model routes "create a project" → create_project. Each
    # files a governed proposal (POST /api/v1/proposals); whether it then runs
    # immediately or waits for a human in /approvals is the backend's auto-approve
    # policy (PROPOSAL_REQUIRE_APPROVAL). Hermes never mutates state directly.

    async def _resolve_project_id(project: str | None) -> str | None:
        """Accept a project id OR name and return the id, so a user/model can say
        "testProject" instead of an opaque id. Names match case-insensitively. If
        it's already a known id, or no unique name match is found, it's returned
        unchanged (the backend then reports if it's missing). Best-effort: a lookup
        error never blocks an explicit id."""
        if not project:
            return project
        try:
            projects = await client.get("/api/v1/projects")
        except Exception:  # noqa: BLE001 — don't let a lookup hiccup block the action
            return project
        if not isinstance(projects, list):
            return project
        ids = {p.get("project_id") for p in projects if isinstance(p, dict)}
        if project in ids:
            return project
        matches = [
            p for p in projects
            if isinstance(p, dict) and (p.get("name") or "").strip().lower() == project.strip().lower()
        ]
        if len(matches) == 1:
            return matches[0]["project_id"]
        return project  # 0 or >1 name matches → pass through; backend reports if missing

    async def _propose(action_type: str, target_ref: str | None = None,
                       payload: dict[str, Any] | None = None) -> Any:
        body: dict[str, Any] = {"action_type": action_type}
        if target_ref is not None:
            body["target_ref"] = await _resolve_project_id(target_ref)
        if payload:
            if payload.get("project_id"):
                payload = {**payload, "project_id": await _resolve_project_id(payload["project_id"])}
            body["payload"] = payload
        return await client.post("/api/v1/proposals", json=body)

    async def create_project(name: str, description: str | None = None) -> Any:
        """Create a new project (governed). Args: name (required), description
        (optional). Returns the proposal — auto-approved or pending per policy."""
        payload: dict[str, Any] = {"name": name}
        if description:
            payload["description"] = description
        return await _propose("project.create", payload=payload)

    async def set_project_brief(project_id: str, content: str) -> Any:
        """Set or update a project's brief (its description/summary). Args:
        project_id, content."""
        return await _propose("project.brief.set", target_ref=project_id,
                              payload={"content": content})

    async def generate_prd(project_id: str) -> Any:
        """Generate (create) the project's PRD. Arg: project_id."""
        return await _propose("prd.generate", target_ref=project_id)

    async def generate_api_spec(project_id: str) -> Any:
        """Generate (create) the project's API specification. Arg: project_id."""
        return await _propose("apispec.generate", target_ref=project_id)

    async def generate_epics(project_id: str) -> Any:
        """Generate the project's epics. Arg: project_id."""
        return await _propose("epics.generate", target_ref=project_id)

    async def generate_features(project_id: str, epic_id: str | None = None) -> Any:
        """Generate features for the project (optionally scoped to one epic).
        Args: project_id, epic_id (optional)."""
        payload = {"epic_id": epic_id} if epic_id else None
        return await _propose("features.generate", target_ref=project_id, payload=payload)

    async def generate_tasks(project_id: str) -> Any:
        """Generate the project's task list. Arg: project_id."""
        return await _propose("tasks.generate", target_ref=project_id)

    async def generate_build_plan(project_id: str) -> Any:
        """Generate the project's build plan (epics → features → tasks rollup).
        Arg: project_id."""
        return await _propose("buildplan.generate", target_ref=project_id)

    async def dispatch_tasks(project_id: str, task_ids: list[str] | None = None) -> Any:
        """Dispatch the project's tasks to the agents to start building. Args:
        project_id; task_ids (optional — omit to dispatch all dispatchable tasks)."""
        payload = {"task_ids": task_ids} if task_ids else None
        return await _propose("task.dispatch", target_ref=project_id, payload=payload)

    async def submit_request(
        description: str,
        task_type: str = "feature_request",
        priority: str = "medium",
        project_id: str | None = None,
    ) -> Any:
        """Submit a work request to the agent team (governed). Args: description
        (required); task_type (feature_request default, bug_fix, research, …);
        priority (low|medium|high); project_id (optional → Unassigned)."""
        payload: dict[str, Any] = {
            "description": description,
            "task_type": task_type,
            "priority": priority,
        }
        if project_id:
            payload["project_id"] = project_id
        return await _propose("request.submit", target_ref=project_id, payload=payload)

    # ── HAI-66 — finalize actions (save artifact to disk + GitHub) ─────────────

    async def finalize_prd(project_id: str) -> Any:
        """Finalize the project's PRD and save it to the local machine. Arg: project_id."""
        return await _propose("prd.finalize", target_ref=project_id)

    async def finalize_api_spec(project_id: str) -> Any:
        """Finalize the project's API spec and save it to the local machine. Arg: project_id."""
        return await _propose("apispec.finalize", target_ref=project_id)

    async def finalize_tasks(project_id: str) -> Any:
        """Finalize the project's task list and save it to the local machine. Arg: project_id."""
        return await _propose("tasks.finalize", target_ref=project_id)

    async def finalize_epics(project_id: str) -> Any:
        """Finalize all of the project's epics (cascades to features + tasks). Arg: project_id."""
        return await _propose("epics.finalize", target_ref=project_id)

    async def finalize_features(project_id: str) -> Any:
        """Finalize all of the project's features (cascades to tasks). Arg: project_id."""
        return await _propose("features.finalize", target_ref=project_id)

    # ── HAI-66 — startable-task reads (viewer-tier) ────────────────────────────

    async def get_startable_tasks(project_id: str) -> Any:
        """The first set of tasks that can be started now (all deps satisfied).
        Arg accepts the project name or id."""
        pid = await _resolve_project_id(project_id)
        return await client.get(f"/api/v1/projects/{pid}/tasks/startable")

    async def get_next_wave_tasks(project_id: str) -> Any:
        """The next set of tasks that become startable after the current set finishes.
        Arg accepts the project name or id."""
        pid = await _resolve_project_id(project_id)
        return await client.get(f"/api/v1/projects/{pid}/tasks/next-wave")

    async def get_epics(project_id: str) -> Any:
        """List the project's epics (id, title, ordinal) — find an epic_id to pass to
        generate_features. Arg accepts the project name or id."""
        pid = await _resolve_project_id(project_id)
        return await client.get(f"/api/v1/projects/{pid}/epics")

    async def get_features(project_id: str) -> Any:
        """List the project's features (flat; each carries its epic_id). Arg accepts
        the project name or id."""
        pid = await _resolve_project_id(project_id)
        return await client.get(f"/api/v1/projects/{pid}/features")

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
        "project_get_prd": project_get_prd,
        "project_get_apispec": project_get_apispec,
        "project_get_buildplan": project_get_buildplan,
        "project_get_tasks": project_get_tasks,
        "monitor_list_proposals": monitor_list_proposals,
        "monitor_get_proposal": monitor_get_proposal,
        # HAI-65 — natural-named gated action tools
        "create_project": create_project,
        "set_project_brief": set_project_brief,
        # alias for natural 'update/revise the description' phrasing → same action
        "update_project_description": set_project_brief,
        "generate_prd": generate_prd,
        "generate_api_spec": generate_api_spec,
        "generate_epics": generate_epics,
        "generate_features": generate_features,
        "generate_tasks": generate_tasks,
        "generate_build_plan": generate_build_plan,
        "dispatch_tasks": dispatch_tasks,
        "submit_request": submit_request,
        # HAI-66 — finalize actions + startable-task reads
        "finalize_prd": finalize_prd,
        "finalize_api_spec": finalize_api_spec,
        "finalize_tasks": finalize_tasks,
        "finalize_epics": finalize_epics,
        "finalize_features": finalize_features,
        "get_startable_tasks": get_startable_tasks,
        "get_next_wave_tasks": get_next_wave_tasks,
        # BUG-7 — epic/feature enumeration
        "get_epics": get_epics,
        "get_features": get_features,
    }
