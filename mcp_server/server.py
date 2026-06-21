"""agent-team-mcp — MCP server entry point (HAI-05/08/09).

A FastMCP server over **streamable HTTP** transport (FR-002), which is the mode
Hermes Agent connects to. At startup it resolves its own role (via the backend's
``GET /api/v1/service-tokens/me``) and registers the manifest tools it's allowed
to expose. Ships ``ping`` + ``healthz`` local tools so ``hermes mcp test``
succeeds even before a service token is configured.

Run locally:  ``python -m mcp_server.server``  (or ``python server.py``)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP

try:  # support both ``python -m mcp_server.server`` and ``python server.py``
    from mcp_server.backend_client import BackendClient, BackendError
    from mcp_server.config import settings
    from mcp_server.manifest import load_manifest, register_tools
    from mcp_server.role_resolver import resolve_role_with_retry
    from mcp_server.tool_impls import build_tool_impls
except ModuleNotFoundError:  # pragma: no cover - container runs flat
    from backend_client import BackendClient, BackendError  # type: ignore[no-redef]
    from config import settings  # type: ignore[no-redef]
    from manifest import load_manifest, register_tools  # type: ignore[no-redef]
    from role_resolver import resolve_role_with_retry  # type: ignore[no-redef]
    from tool_impls import build_tool_impls  # type: ignore[no-redef]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("agent-team-mcp")

mcp = FastMCP("agent-team", host=settings.MCP_HOST, port=settings.MCP_PORT)

_MANIFEST_PATH = Path(__file__).with_name("tools_manifest.yaml")

# Populated at startup; surfaced by healthz so "what role / which tools" is visible.
_runtime = {"role": None, "registered": [], "backend_ok": False}


@mcp.tool()
def ping() -> str:
    """Liveness check for ``hermes mcp test`` — returns ``pong``."""
    return "pong"


@mcp.tool()
async def healthz() -> dict:
    """Health: backend reachability, this server's resolved role, and the tools
    it registered. The first thing to check when wiring Hermes up."""
    ok = False
    try:
        await BackendClient().get("/api/v1/health")
        ok = True
    except BackendError:
        ok = False
    return {
        "mcp": "ok",
        "transport": "streamable-http",
        "backend_url": settings.BACKEND_URL,
        "backend_reachable": ok,
        "service_token_configured": bool(settings.SERVICE_TOKEN),
        "resolved_role": _runtime["role"],
        "registered_tools": _runtime["registered"],
    }


def main() -> None:
    client = BackendClient()
    backend_ok, role = asyncio.run(resolve_role_with_retry(client))
    _runtime["backend_ok"] = backend_ok
    _runtime["role"] = role

    specs = load_manifest(_MANIFEST_PATH)
    impls = build_tool_impls(client)
    registered = register_tools(mcp, specs, role, impls)
    _runtime["registered"] = registered

    log.info(
        "agent-team-mcp ready backend_ok=%s role=%s registered=%s %s",
        backend_ok, role, registered, settings.summary(),
    )
    # Streamable HTTP transport (FR-002) — NOT SSE-only, NOT stdio.
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
