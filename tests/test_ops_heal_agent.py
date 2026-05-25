"""Tests for OpsCheckTool and the ops_heal_agent trigger chain.

Covers:
  TC-OPS-01  full_check HEALTHY when services respond 200 and resources are OK
  TC-OPS-02  full_check UNHEALTHY when backend health check fails
  TC-OPS-03  full_check UNHEALTHY when disk usage exceeds 90%
  TC-OPS-04  error_scan CLEAN when no log directory exists (SKIPPED gracefully)
  TC-OPS-05  error_scan ISSUES_FOUND when error lines are present
  TC-OPS-06  memory_check SKIPPED on non-Linux (no /proc/meminfo)
  TC-OPS-07  ops.* event constants are exported from events.py
  TC-OPS-08  POST /api/v1/ops/monitor returns 200 accepted (mock orchestrator)
  TC-OPS-09  GET /api/v1/ops/latest returns verdict from state store
  TC-OPS-10  OpsCheckTool.schema() returns a valid Anthropic tool definition
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ──────────────────────────────────────────────────────────────────────────────
# TC-OPS-10  Schema structure
# ──────────────────────────────────────────────────────────────────────────────

def test_ops_check_schema_valid():
    """OpsCheckTool.schema() must match the Anthropic tool-definition contract."""
    from src.tools.ops_check import OpsCheckTool

    schema = OpsCheckTool.schema()
    assert schema["name"] == "ops_check"
    assert "description" in schema
    assert "input_schema" in schema
    assert schema["input_schema"]["type"] == "object"
    assert "action" in schema["input_schema"]["properties"]
    actions = schema["input_schema"]["properties"]["action"]["enum"]
    assert "full_check" in actions
    assert "health_check" in actions
    assert "disk_check" in actions
    assert "error_scan" in actions
    assert "memory_check" in actions


# ──────────────────────────────────────────────────────────────────────────────
# TC-OPS-01  full_check HEALTHY when backend responds 200
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_check_healthy():
    """When all HTTP checks pass and resources are OK, verdict must be HEALTHY."""
    from src.tools.ops_check import OpsCheckTool

    tool = OpsCheckTool()

    # Backend returns 200
    mock_response = MagicMock()
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_response.status = 200
    mock_response.read.return_value = b'{"status":"healthy"}'

    with patch("src.tools.ops_check.urllib.request.urlopen", return_value=mock_response):
        with patch.object(tool, "run_disk_check", return_value={"status": "OK", "pct_used": 42.0}):
            with patch.object(tool, "run_memory_check", return_value={"status": "OK", "pct_used": 38.0}):
                with patch.object(tool, "run_error_scan", return_value={"status": "SKIPPED", "reason": "no log dir"}):
                    result = await tool.execute({"action": "full_check", "service": "backend"})

    assert result["verdict"] == "HEALTHY"
    assert result["issues"] == []


# ──────────────────────────────────────────────────────────────────────────────
# TC-OPS-02  full_check UNHEALTHY when backend health check fails
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_check_unhealthy_on_backend_failure():
    """When the backend health endpoint is unreachable, verdict must be UNHEALTHY."""
    from src.tools.ops_check import OpsCheckTool

    tool = OpsCheckTool()

    import urllib.error

    with patch(
        "src.tools.ops_check.urllib.request.urlopen",
        side_effect=ConnectionRefusedError("Connection refused"),
    ):
        with patch.object(tool, "run_disk_check", return_value={"status": "OK", "pct_used": 20.0}):
            with patch.object(tool, "run_memory_check", return_value={"status": "OK", "pct_used": 20.0}):
                with patch.object(tool, "run_error_scan", return_value={"status": "SKIPPED"}):
                    result = await tool.execute({"action": "full_check", "service": "backend"})

    assert result["verdict"] == "UNHEALTHY"
    assert len(result["issues"]) >= 1
    assert any("health" in issue.lower() for issue in result["issues"])


# ──────────────────────────────────────────────────────────────────────────────
# TC-OPS-03  full_check UNHEALTHY when disk usage exceeds 90%
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_check_unhealthy_on_disk_critical():
    """When disk usage is CRITICAL (>90%), the full_check verdict must be UNHEALTHY."""
    from src.tools.ops_check import OpsCheckTool

    tool = OpsCheckTool()

    mock_response = MagicMock()
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_response.status = 200
    mock_response.read.return_value = b'{"status":"healthy"}'

    with patch("src.tools.ops_check.urllib.request.urlopen", return_value=mock_response):
        with patch.object(
            tool, "run_disk_check",
            return_value={"status": "CRITICAL", "pct_used": 95.2, "free_gb": 0.5},
        ):
            with patch.object(tool, "run_memory_check", return_value={"status": "OK", "pct_used": 30.0}):
                with patch.object(tool, "run_error_scan", return_value={"status": "SKIPPED"}):
                    result = await tool.execute({"action": "full_check", "service": "backend"})

    assert result["verdict"] == "UNHEALTHY"
    assert any("disk" in issue.lower() for issue in result["issues"])


# ──────────────────────────────────────────────────────────────────────────────
# TC-OPS-04  error_scan SKIPPED when no log directory
# ──────────────────────────────────────────────────────────────────────────────

def test_error_scan_skipped_when_no_log_dir():
    """run_error_scan must return SKIPPED when /app/logs does not exist."""
    from src.tools.ops_check import OpsCheckTool, _LOG_DIR

    tool = OpsCheckTool()
    # Patch _LOG_DIR.exists() to False
    with patch.object(Path, "exists", return_value=False):
        result = tool.run_error_scan()

    assert result["status"] == "SKIPPED"
    assert "reason" in result


# ──────────────────────────────────────────────────────────────────────────────
# TC-OPS-05  error_scan ISSUES_FOUND when error lines present
# ──────────────────────────────────────────────────────────────────────────────

def test_error_scan_issues_found(tmp_path: Path):
    """run_error_scan must return ISSUES_FOUND when ERROR lines appear in logs."""
    from src.tools.ops_check import OpsCheckTool

    # Write a fake log file with error lines
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "app.log"
    log_file.write_text(
        'INFO app started\n'
        'INFO request received\n'
        'ERROR database connection failed: timeout\n'
        'INFO request processed\n',
        encoding="utf-8",
    )

    tool = OpsCheckTool()
    with patch("src.tools.ops_check._LOG_DIR", log_dir):
        result = tool.run_error_scan(max_lines=100)

    assert result["status"] == "ISSUES_FOUND"
    assert result["error_count"] >= 1
    assert any("ERROR" in line for line in result["recent_errors"])


# ──────────────────────────────────────────────────────────────────────────────
# TC-OPS-06  memory_check SKIPPED on non-Linux
# ──────────────────────────────────────────────────────────────────────────────

def test_memory_check_skipped_when_no_proc_meminfo():
    """run_memory_check returns SKIPPED when /proc/meminfo is absent (non-Linux)."""
    from src.tools.ops_check import OpsCheckTool

    tool = OpsCheckTool()
    with patch.object(Path, "exists", return_value=False):
        result = tool.run_memory_check()

    assert result["status"] == "SKIPPED"
    assert "reason" in result


# ──────────────────────────────────────────────────────────────────────────────
# TC-OPS-07  ops.* event constants exported from events.py
# ──────────────────────────────────────────────────────────────────────────────

def test_ops_event_constants():
    """OPS_HEALTHY, OPS_ISSUE_DETECTED, OPS_MONITORING_STARTED, OPS_ERROR must be importable."""
    from src.core.events import (
        OPS_ERROR,
        OPS_HEALTHY,
        OPS_ISSUE_DETECTED,
        OPS_MONITORING_STARTED,
    )

    assert OPS_HEALTHY == "ops.healthy"
    assert OPS_ISSUE_DETECTED == "ops.issue_detected"
    assert OPS_MONITORING_STARTED == "ops.monitoring_started"
    assert OPS_ERROR == "ops.error"


# ──────────────────────────────────────────────────────────────────────────────
# TC-OPS-08  POST /api/v1/ops/monitor accepted (mock orchestrator)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ops_monitor_endpoint_accepted():
    """POST /api/v1/ops/monitor must return 200 accepted when orchestrator exists."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.routes.ops import router

    app = FastAPI()
    app.include_router(router)

    # Attach a mock orchestrator
    mock_orch = AsyncMock()
    mock_orch.trigger_ops_monitor = AsyncMock()
    app.state.orchestrator = mock_orch

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/ops/monitor",
            json={"request_id": "REQ-TEST", "deployment_id": "dep-123"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "accepted"
    assert data["request_id"] == "REQ-TEST"


# ──────────────────────────────────────────────────────────────────────────────
# TC-OPS-09  GET /api/v1/ops/latest returns verdict
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ops_latest_endpoint_healthy():
    """GET /api/v1/ops/latest returns HEALTHY when most recent deployment is completed."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.routes.ops import router

    app = FastAPI()
    app.include_router(router)

    mock_state = AsyncMock()
    mock_state.get_latest_deployment = AsyncMock(return_value={
        "deployment_id": "dep-abc",
        "request_id": "REQ-001",
        "current_step": "completed",
        "strategy": "deploy_full",
        "risk": "low",
    })
    app.state.state_store = mock_state

    with TestClient(app) as client:
        resp = client.get("/api/v1/ops/latest")

    assert resp.status_code == 200
    data = resp.json()
    assert data["verdict"] == "HEALTHY"
    assert data["deployment_id"] == "dep-abc"


@pytest.mark.asyncio
async def test_ops_latest_endpoint_unhealthy_on_rollback():
    """GET /api/v1/ops/latest returns UNHEALTHY when most recent deployment is rolled_back."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.routes.ops import router

    app = FastAPI()
    app.include_router(router)

    mock_state = AsyncMock()
    mock_state.get_latest_deployment = AsyncMock(return_value={
        "deployment_id": "dep-xyz",
        "request_id": "REQ-002",
        "current_step": "rolled_back",
        "strategy": "deploy_full",
        "risk": "high",
    })
    app.state.state_store = mock_state

    with TestClient(app) as client:
        resp = client.get("/api/v1/ops/latest")

    assert resp.status_code == 200
    data = resp.json()
    assert data["verdict"] == "UNHEALTHY"
