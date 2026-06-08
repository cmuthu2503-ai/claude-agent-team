"""agent-team-mcp — MCP server entry point (HAI-05).

A FastMCP server over **streamable HTTP** transport (FR-002), which is the mode
Hermes Agent connects to (``mcp_servers.<name>.transport: streamable_http`` in
``~/.hermes/config.yaml``). This scaffold ships a single ``ping`` liveness tool
so ``hermes mcp test`` succeeds; the real tools land in HAI-08/10+.

Run locally:  ``python -m mcp_server.server``  (or ``python server.py``)
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

try:  # support both ``python -m mcp_server.server`` and ``python server.py``
    from mcp_server.config import settings
except ModuleNotFoundError:  # pragma: no cover - container runs flat
    from config import settings  # type: ignore[no-redef]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("agent-team-mcp")

# Name shown to Hermes; host/port are where the streamable-HTTP endpoint binds.
mcp = FastMCP("agent-team", host=settings.MCP_HOST, port=settings.MCP_PORT)


@mcp.tool()
def ping() -> str:
    """Liveness check for ``hermes mcp test`` — returns ``pong``.

    Placeholder tool: the monitor/action tools that wrap the Agent Team REST API
    are added in HAI-08 (manifest) and HAI-10+ (the tools themselves).
    """
    return "pong"


def main() -> None:
    log.info("agent-team-mcp starting %s", settings.summary())
    # Streamable HTTP transport (FR-002) — NOT SSE-only, NOT stdio.
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
