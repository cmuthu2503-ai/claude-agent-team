"""Startup role resolution for agent-team-mcp — the cold-boot retry (FR-004).

Kept separate from ``server.py`` so the logic carries NO dependency on the MCP
transport (FastMCP): it only needs the backend client + config, which makes it
unit-testable without standing up a FastMCP server.

The problem this solves: the server resolves its role ONCE at boot (via
``GET /api/v1/service-tokens/me``) and registers tools for it. If the backend
wasn't healthy yet — classically after ``docker compose restart agent-team-mcp``,
which bypasses ``depends_on`` — the role came back null and ZERO action tools
registered for the whole process lifetime. We now poll until the role resolves,
the token is rejected, or we time out.
"""

from __future__ import annotations

import asyncio
import logging

try:  # support both package and flat (container) layouts
    from mcp_server.backend_client import BackendClient, BackendError
    from mcp_server.config import settings
except ModuleNotFoundError:  # pragma: no cover - container runs flat
    from backend_client import BackendClient, BackendError  # type: ignore[no-redef]
    from config import settings  # type: ignore[no-redef]

log = logging.getLogger("agent-team-mcp")


def is_auth_failure(err: BackendError) -> bool:
    """A 401/403 from /service-tokens/me means the token is bad — that won't fix
    itself by waiting, so we stop retrying and start degraded (healthz surfaces
    it). A connection error or 5xx, by contrast, is a transient cold-boot symptom
    worth retrying."""
    msg = str(err)
    return "(401)" in msg or "(403)" in msg


async def resolve_role_once(client: BackendClient) -> tuple[bool, str | None, bool]:
    """One probe pass. Returns ``(backend_ok, role, terminal)``.

    ``terminal=True`` means there is no point retrying — the backend answered but
    rejected the token, or no token is configured at all."""
    try:
        await client.get("/api/v1/health")
    except BackendError as e:
        log.warning("backend health probe failed: %s", e)
        return False, None, False

    if not settings.SERVICE_TOKEN:
        log.warning("AGENT_TEAM_SERVICE_TOKEN not set — no role; backend tools won't register")
        return True, None, True  # nothing to retry — token is simply absent

    try:
        me = await client.get("/api/v1/service-tokens/me")
        return True, (me or {}).get("role"), False
    except BackendError as e:
        if is_auth_failure(e):
            log.error("service-token rejected by backend (will NOT retry): %s", e)
            return True, None, True
        log.warning("role resolution failed (will retry): %s", e)
        return True, None, False


async def resolve_role_with_retry(client: BackendClient) -> tuple[bool, str | None]:
    """Poll the backend until the role resolves, the token is rejected, or we
    time out (``STARTUP_PROBE_TIMEOUT``). Returns ``(backend_ok, role)``."""
    deadline = asyncio.get_event_loop().time() + settings.STARTUP_PROBE_TIMEOUT
    attempt = 0
    backend_ok = False
    while True:
        attempt += 1
        backend_ok, role, terminal = await resolve_role_once(client)
        if role:
            if attempt > 1:
                log.info("role resolved on attempt %d: role=%s", attempt, role)
            return backend_ok, role
        if terminal:
            return backend_ok, None
        if asyncio.get_event_loop().time() >= deadline:
            log.error(
                "role unresolved after %.0fs (%d attempts); starting DEGRADED with no "
                "action tools (backend_reachable=%s). Fix the backend/token and restart "
                "agent-team-mcp.",
                settings.STARTUP_PROBE_TIMEOUT, attempt, backend_ok,
            )
            return backend_ok, None
        log.info(
            "role not resolved yet (attempt %d, backend_ok=%s) — retrying in %.0fs",
            attempt, backend_ok, settings.STARTUP_PROBE_INTERVAL,
        )
        await asyncio.sleep(settings.STARTUP_PROBE_INTERVAL)
