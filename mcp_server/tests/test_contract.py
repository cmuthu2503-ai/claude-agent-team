"""HAI-53 (FR-084) — MCP ↔ backend contract.

Pins the backend endpoints the MCP adapter/tools depend on against the backend's
live OpenAPI schema, so a backend route rename/removal is caught HERE instead of
at runtime against Hermes. The frontend regenerates types from OpenAPI for the
same reason; the MCP service gets this contract test.

Skips gracefully when the backend isn't reachable, so a unit-only run (no live
backend) doesn't fail.
"""

import os

import httpx
import pytest

BACKEND = os.getenv("AGENT_TEAM_BACKEND_URL", "http://backend:8000")

# The backend endpoints the MCP server depends on. Keep in sync with
# tool_impls.py (tool paths) and server.py (/health, /service-tokens/me).
# HAI-10+ add their monitor/action endpoints here as tools land.
REQUIRED_ENDPOINTS = [
    ("get", "/api/v1/health"),
    ("get", "/api/v1/service-tokens/me"),
    ("get", "/api/v1/requests"),  # HAI-10 monitor_list_requests
]


def _load_openapi() -> dict | None:
    try:
        r = httpx.get(f"{BACKEND}/api/v1/openapi.json", timeout=5.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


@pytest.fixture(scope="module")
def openapi() -> dict:
    spec = _load_openapi()
    if spec is None:
        pytest.skip(f"backend not reachable at {BACKEND}; contract test needs a live backend")
    return spec


@pytest.mark.parametrize("method,path", REQUIRED_ENDPOINTS)
def test_required_endpoint_present(openapi: dict, method: str, path: str):
    paths = openapi.get("paths", {})
    assert path in paths, f"backend OpenAPI is missing {path} — the MCP adapter depends on it"
    methods = {m.lower() for m in paths[path]}
    assert method in methods, f"{path} exists but lacks {method.upper()} (has: {sorted(methods)})"
