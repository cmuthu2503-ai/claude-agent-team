"""KB-15 — namespace scope resolution + isolation (Phase 2).

Decides WHICH knowledge namespace an agent retrieves from for a given Request.
This is the per-application isolation boundary: a Request filed against Project
A resolves to ``kb_project_A`` so the agents working it are scoped to App A's
knowledge and structurally cannot reach App B's (the store filters by namespace
in-query — NFR-003).

The resolution honours a per-agent **scope grant** from the agent's YAML
``retrieval.scope`` so an agent can't widen beyond what it's granted:

  - ``"auto"`` (default) — a real project Request → that project's namespace;
    an unassigned / project-less Request → the platform namespace.
  - ``"project"`` — always the project namespace (falls back to platform when
    the Request has no project, so a project-only agent still functions).
  - ``"platform"`` — always the platform namespace (craft-only agents that must
    never read app-specific facts).

Pure + dependency-light so it unit-tests without a DB.
"""

from __future__ import annotations

from typing import Any

from src.models.base import UNASSIGNED_PROJECT_ID


def _is_real_project(project_id: str | None) -> bool:
    return bool(project_id) and project_id != UNASSIGNED_PROJECT_ID


def resolve_namespace(
    settings: Any, project_id: str | None, scope: str = "auto"
) -> str:
    """Return the KB namespace for a Request, given the agent's scope grant.

    ``settings`` must expose ``platform_namespace`` and
    ``project_namespace(project_id)`` (``KnowledgeSettings``)."""
    scope = (scope or "auto").lower()
    platform: str = str(settings.platform_namespace)
    if scope == "platform":
        return platform
    if scope == "project":
        if _is_real_project(project_id):
            return str(settings.project_namespace(project_id))
        return platform
    # auto
    if _is_real_project(project_id):
        return str(settings.project_namespace(project_id))
    return platform


def resolve_craft_namespace(
    settings: Any, project_id: str | None, scope: str = "auto"
) -> str | None:
    """KB-17 — the secondary **craft** namespace (platform), or None.

    Under ``scope=auto`` a project Request grounds *facts* in the project
    namespace but may draw *craft* (format/method/tone) from the platform KB —
    never as a citeable fact (§5.1). Returns the platform namespace only in that
    case; ``project`` (facts-strict) and ``platform`` scopes get no separate
    craft source. None when the primary namespace already IS the platform."""
    scope = (scope or "auto").lower()
    if scope != "auto":
        return None
    if not _is_real_project(project_id):
        return None  # primary is already platform — no separate craft source
    return str(settings.platform_namespace)
