"""PAM-12 — /api/v1/models route contract tests.

Uses FastAPI's TestClient against a minimal app that wires the real
routes module + a fake `agent_executor` carrying a real ModelCatalog.
Auth is replaced with `dependency_overrides` so we don't need to
bootstrap a user / login round trip.

Pinned contracts:
  - GET / returns the catalog (any authenticated user)
  - GET / returns 503 when catalog isn't loaded (PAM-07 soft-fail path)
  - POST /reload requires admin
  - POST /reload swaps catalog on success
  - POST /reload returns 422 on a broken catalog without touching live state
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import models as models_route
from src.auth.service import get_current_user, require_role
from src.models.catalog import ModelCatalog, default_catalog_path


def _make_app(executor: Any = None) -> FastAPI:
    """Spin up an app with the models router mounted and auth bypassed."""
    app = FastAPI()
    app.include_router(models_route.router)
    app.state.agent_executor = executor

    # Bypass auth: any token (or no token) yields a fake user. Tests
    # that need an admin gate override require_role separately below.
    def _fake_user() -> dict[str, Any]:
        return {"sub": "u1", "username": "tester", "role": "viewer"}

    app.dependency_overrides[get_current_user] = _fake_user
    return app


def _make_executor_with_real_catalog() -> Any:
    """Real ModelCatalog → real GET responses, no mocks of the catalog
    surface itself. We only mock the executor wrapper because building
    AgentSystemExecutor needs AWS creds."""
    catalog = ModelCatalog.load(default_catalog_path())
    executor = MagicMock()
    executor.model_catalog = catalog
    # Build a resolver-shaped object so the reload route's None check
    # passes. Its .catalog is what gets swapped — assert against this.
    resolver = MagicMock()
    resolver.catalog = catalog
    executor.model_resolver = resolver
    return executor


# ── GET /api/v1/models ─────────────────────────────────────────────────


def test_list_models_returns_catalog():
    executor = _make_executor_with_real_catalog()
    app = _make_app(executor)
    client = TestClient(app)

    r = client.get("/api/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is None
    assert body["data"]["default_model"] == "claude-opus-4-7"
    assert body["meta"]["count"] == len(body["data"]["models"])
    assert body["meta"]["count"] >= 5
    # Each model has the full shape — frontend depends on these keys.
    m = body["data"]["models"][0]
    for k in ("id", "provider_type", "model_id", "display_name", "tier",
              "tool_calling_mode", "pricing_per_million"):
        assert k in m, f"missing {k} in {m}"
    assert "input" in m["pricing_per_million"]
    assert "output" in m["pricing_per_million"]
    # Legacy alias map is NOT exposed (internal back-compat detail).
    assert "legacy_provider_aliases" not in body["data"]


def test_list_models_503_when_catalog_missing():
    """Catalog not loaded → 503 (PAM-07 soft-fail surface, not 500)."""
    app = _make_app(executor=None)
    client = TestClient(app)
    r = client.get("/api/v1/models")
    assert r.status_code == 503
    assert "model catalog" in r.json()["detail"].lower()


def test_list_models_503_when_executor_has_no_catalog():
    executor = MagicMock()
    executor.model_catalog = None
    app = _make_app(executor)
    client = TestClient(app)
    r = client.get("/api/v1/models")
    assert r.status_code == 503


# ── POST /api/v1/models/reload ─────────────────────────────────────────


def _override_admin(app: FastAPI) -> None:
    """Replace require_role('admin') so we don't need a real JWT."""
    def _admin_user() -> dict[str, Any]:
        return {"sub": "u1", "username": "admin", "role": "admin"}
    # require_role returns a closure per call — override the factory
    # output by overriding ANY call. The easiest way: override every
    # require_role() instance the route declared at import time.
    # FastAPI keys on the dependable object identity, so we walk the
    # route's dependencies once.
    for route in app.routes:
        for dep in getattr(route, "dependant", MagicMock()).dependencies or []:
            if getattr(dep.call, "__name__", "") == "role_checker":
                app.dependency_overrides[dep.call] = _admin_user


def test_reload_succeeds_and_swaps_catalog():
    executor = _make_executor_with_real_catalog()
    original_catalog = executor.model_catalog
    app = _make_app(executor)
    _override_admin(app)
    client = TestClient(app)

    r = client.post("/api/v1/models/reload")
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["data"]["previous_count"] == len(original_catalog.models)
    assert body["data"]["new_count"] >= 5
    # Catalog was swapped on BOTH the executor and the resolver.
    assert executor.model_catalog is not original_catalog
    assert executor.model_resolver.catalog is executor.model_catalog


def test_reload_503_when_resolver_not_wired():
    executor = MagicMock()
    executor.model_catalog = None
    executor.model_resolver = None
    app = _make_app(executor)
    _override_admin(app)
    client = TestClient(app)
    r = client.post("/api/v1/models/reload")
    assert r.status_code == 503


def test_reload_422_on_broken_yaml(monkeypatch, tmp_path):
    """Bad YAML → 422, live catalog UNCHANGED (best-effort swap)."""
    executor = _make_executor_with_real_catalog()
    original_catalog = executor.model_catalog
    app = _make_app(executor)
    _override_admin(app)
    client = TestClient(app)

    # Force ModelCatalog.load to blow up — simulates a broken YAML.
    from src.models import catalog as cat_module

    def _explode(path):  # noqa: ANN001
        raise ValueError("oops: missing default_model")
    monkeypatch.setattr(cat_module.ModelCatalog, "load", classmethod(lambda cls, p: _explode(p)))

    r = client.post("/api/v1/models/reload")
    assert r.status_code == 422
    assert "oops" in r.json()["detail"]
    # Live state untouched — this is the L25 guarantee.
    assert executor.model_catalog is original_catalog
    assert executor.model_resolver.catalog is original_catalog


def test_reload_requires_admin():
    """Without admin override, the require_role('admin') gate rejects."""
    executor = _make_executor_with_real_catalog()
    app = _make_app(executor)  # only viewer override
    client = TestClient(app)
    # The require_role dependency reads request.app.state.auth_service
    # for token decode; we didn't wire one, so it 401s on missing
    # credentials. Either way, the route is gated — that's the contract.
    r = client.post("/api/v1/models/reload")
    assert r.status_code in (401, 403)
