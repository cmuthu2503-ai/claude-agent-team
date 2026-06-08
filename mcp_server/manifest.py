"""Tool manifest loader (HAI-08 / FR-005, FR-008, NFR-007).

Reads ``tools_manifest.yaml`` and registers the declared tools onto the MCP
server — but only those at or below the server's own resolved role. WHICH tools
exist and WHAT role each needs is config (the manifest), not code, so widening
or narrowing the exposed surface is a YAML edit, not a redeploy of logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

# viewer ⊂ developer ⊂ admin — a server with role R may expose tools whose
# min_role rank is ≤ R's rank.
_ROLE_RANK = {"viewer": 0, "developer": 1, "admin": 2}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    min_role: str
    method: str
    path: str


def load_manifest(path: str | Path) -> list[ToolSpec]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    specs: list[ToolSpec] = []
    for entry in data.get("tools", []) or []:
        specs.append(
            ToolSpec(
                name=entry["name"],
                description=entry.get("description", ""),
                min_role=str(entry.get("min_role", "admin")),
                method=str(entry.get("method", "GET")).upper(),
                path=entry["path"],
            )
        )
    return specs


def role_allows(server_role: str | None, min_role: str) -> bool:
    """True if a server holding ``server_role`` may expose a tool requiring
    ``min_role``. An unknown/absent server role (rank -1) exposes nothing — so a
    server that couldn't resolve its role registers no backend tools."""
    return _ROLE_RANK.get(server_role or "", -1) >= _ROLE_RANK.get(min_role, 99)


def tools_for_role(specs: list[ToolSpec], server_role: str | None) -> list[ToolSpec]:
    return [s for s in specs if role_allows(server_role, s.min_role)]


def register_tools(
    mcp: Any,
    specs: list[ToolSpec],
    server_role: str | None,
    impls: dict[str, Callable],
) -> list[str]:
    """Register the role-allowed tools that have an implementation onto a
    FastMCP-like object (anything exposing ``add_tool(fn, name=, description=)``).
    Returns the registered tool names. Tools without an impl are skipped (the
    manifest may declare ahead of implementation)."""
    registered: list[str] = []
    for spec in tools_for_role(specs, server_role):
        impl = impls.get(spec.name)
        if impl is None:
            continue
        mcp.add_tool(impl, name=spec.name, description=spec.description)
        registered.append(spec.name)
    return registered
