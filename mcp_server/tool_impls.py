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

    return {
        "monitor_backend_health": monitor_backend_health,
    }
