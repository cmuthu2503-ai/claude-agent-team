"""Configuration for agent-team-mcp (HAI-05).

All settings come from the environment so the same image runs in any env. The
backend URL + service token (used by the HAI-07 REST adapter) are read here so
later tasks don't re-plumb env access.
"""

from __future__ import annotations

import os


class Settings:
    # Where the MCP server itself listens (Hermes connects here over HTTP).
    MCP_HOST: str = os.getenv("MCP_HOST", "0.0.0.0")
    MCP_PORT: int = int(os.getenv("MCP_PORT", "9000"))

    # The Claude Agent Team backend the adapter (HAI-07) will call, and the
    # long-lived service token (HAI-01..03) it authenticates with.
    BACKEND_URL: str = os.getenv("AGENT_TEAM_BACKEND_URL", "http://backend:8000")
    SERVICE_TOKEN: str = os.getenv("AGENT_TEAM_SERVICE_TOKEN", "")

    @classmethod
    def summary(cls) -> dict[str, object]:
        """Non-secret config snapshot for the startup banner / health."""
        return {
            "mcp_host": cls.MCP_HOST,
            "mcp_port": cls.MCP_PORT,
            "backend_url": cls.BACKEND_URL,
            "service_token_configured": bool(cls.SERVICE_TOKEN),
            "transport": "streamable-http",
        }


settings = Settings()
