"""Tests for the CrewAI API skeleton — health, readiness, request-id,
envelope, and RFC 7807 error shape.

Covers the six acceptance test cases from US-006 plus the cross-cutting
middleware contracts from US-002/US-003/US-004.
"""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


# -------------------------------------------------------------------- /health


def test_health_returns_200_and_envelope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/v1/health → 200 with enveloped status/version body."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    body = response.json()
    # Envelope shape
    assert set(body.keys()) == {"data", "meta", "error"}
    assert body["error"] is None
    assert "request_id" in body["meta"]
    # Payload
    assert body["data"] == {"status": "healthy", "version": "1.0.0"}


def test_health_response_has_request_id_header(client: TestClient) -> None:
    """X-Request-ID header is a valid UUIDv4 and matches meta.request_id."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200

    header_id = response.headers.get("X-Request-ID")
    assert header_id is not None
    assert UUID_RE.match(header_id), f"X-Request-ID not a UUID: {header_id!r}"

    body = response.json()
    assert body["meta"]["request_id"] == header_id


def test_request_id_echoed_when_supplied(client: TestClient) -> None:
    """A client-supplied valid X-Request-ID is echoed verbatim."""
    supplied = str(uuid.uuid4())
    response = client.get(
        "/api/v1/health", headers={"X-Request-ID": supplied}
    )
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == supplied
    assert response.json()["meta"]["request_id"] == supplied


# --------------------------------------------------------------------- /ready


def test_ready_all_checks_pass(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When SQLite is reachable AND an LLM key is set, /ready returns 200."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    response = client.get("/api/v1/ready")
    assert response.status_code == 200

    body = response.json()
    assert "data" in body
    checks = body["data"]["checks"]
    names = {c["name"]: c for c in checks}
    assert names["sqlite"]["ok"] is True
    assert names["llm_env"]["ok"] is True


def test_ready_missing_llm_env_returns_503_problem_json(
    client: TestClient, clean_llm_env: None
) -> None:
    """No LLM key → 503 with application/problem+json body."""
    response = client.get("/api/v1/ready")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith(
        "application/problem+json"
    )

    body = response.json()
    # RFC 7807 required fields
    for key in ("type", "title", "status", "detail", "instance", "request_id"):
        assert key in body, f"missing key {key} in {body!r}"
    assert body["status"] == 503
    assert body["instance"] == "/api/v1/ready"

    # Extension field: checks array preserved
    assert "checks" in body
    llm_check = next(c for c in body["checks"] if c["name"] == "llm_env")
    assert llm_check["ok"] is False


# ----------------------------------------------------------- problem+json 404


def test_unknown_route_returns_problem_json(client: TestClient) -> None:
    """GET unknown path → 404 application/problem+json with all RFC 7807
    fields plus request_id matching X-Request-ID header."""
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(
        "application/problem+json"
    )

    body = response.json()
    for key in ("type", "title", "status", "detail", "instance", "request_id"):
        assert key in body, f"missing key {key} in {body!r}"
    assert body["status"] == 404
    assert body["instance"] == "/api/v1/does-not-exist"

    # request_id in body matches response header
    assert body["request_id"] == response.headers["X-Request-ID"]
