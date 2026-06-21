"""Cold-boot role-resolution retry (the #1 reliability fix).

The MCP server used to resolve its role ONCE at startup; if the backend wasn't
healthy yet (e.g. `docker compose restart agent-team-mcp`, which bypasses
depends_on) it cached role=null and registered ZERO action tools for the whole
process. These tests pin the new retry behaviour: transient backend failures
self-heal, auth failures don't spin, and a true timeout starts degraded.

Targets `role_resolver` directly (no FastMCP dependency) so it runs in the
standard test image.
"""

import httpx

import role_resolver as rr
from backend_client import BackendClient


def _client(handler) -> BackendClient:
    return BackendClient("http://backend:8000", "svc-tok", transport=httpx.MockTransport(handler))


def _ok_health() -> httpx.Response:
    return httpx.Response(200, json={"status": "healthy"})


def _role(role: str) -> httpx.Response:
    return httpx.Response(200, json={"data": {"role": role}, "meta": None, "error": None})


# ── resolve_role_once — single pass classification ───────────────────────────

async def test_resolve_once_happy_path(monkeypatch):
    monkeypatch.setattr(rr.settings, "SERVICE_TOKEN", "svc-tok")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/health":
            return _ok_health()
        return _role("developer")

    backend_ok, role, terminal = await rr.resolve_role_once(_client(handler))
    assert backend_ok is True and role == "developer" and terminal is False


async def test_resolve_once_backend_down_is_retryable():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("backend not up")

    backend_ok, role, terminal = await rr.resolve_role_once(_client(handler))
    assert backend_ok is False and role is None and terminal is False  # retryable


async def test_resolve_once_bad_token_is_terminal(monkeypatch):
    monkeypatch.setattr(rr.settings, "SERVICE_TOKEN", "svc-tok")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/health":
            return _ok_health()
        return httpx.Response(401, json={"detail": "Invalid service token"})

    backend_ok, role, terminal = await rr.resolve_role_once(_client(handler))
    assert backend_ok is True and role is None and terminal is True  # do NOT retry


# ── resolve_role_with_retry — the loop ───────────────────────────────────────

async def test_retry_self_heals_when_backend_comes_up(monkeypatch):
    monkeypatch.setattr(rr.settings, "SERVICE_TOKEN", "svc-tok")
    monkeypatch.setattr(rr.settings, "STARTUP_PROBE_INTERVAL", 0)
    monkeypatch.setattr(rr.settings, "STARTUP_PROBE_TIMEOUT", 30)
    calls = {"health": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/health":
            calls["health"] += 1
            if calls["health"] < 3:        # backend "not ready" for 2 attempts
                raise httpx.ConnectError("backend not up")
            return _ok_health()
        return _role("developer")

    backend_ok, role = await rr.resolve_role_with_retry(_client(handler))
    assert role == "developer" and backend_ok is True
    assert calls["health"] >= 3            # it actually retried, didn't give up at attempt 1


async def test_retry_stops_immediately_on_bad_token(monkeypatch):
    # A huge timeout proves we return on auth failure rather than waiting it out.
    monkeypatch.setattr(rr.settings, "SERVICE_TOKEN", "svc-tok")
    monkeypatch.setattr(rr.settings, "STARTUP_PROBE_INTERVAL", 0)
    monkeypatch.setattr(rr.settings, "STARTUP_PROBE_TIMEOUT", 9999)
    calls = {"me": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/health":
            return _ok_health()
        calls["me"] += 1
        return httpx.Response(403, json={"detail": "forbidden"})

    backend_ok, role = await rr.resolve_role_with_retry(_client(handler))
    assert role is None and backend_ok is True
    assert calls["me"] == 1                 # exactly one attempt — no spinning


async def test_retry_times_out_degraded(monkeypatch):
    monkeypatch.setattr(rr.settings, "STARTUP_PROBE_INTERVAL", 0)
    monkeypatch.setattr(rr.settings, "STARTUP_PROBE_TIMEOUT", 0)  # give up after first pass

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("backend never came up")

    backend_ok, role = await rr.resolve_role_with_retry(_client(handler))
    assert role is None and backend_ok is False  # degraded start, healthz will surface it
