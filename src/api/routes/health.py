"""Public health and readiness probes (REQ-007 / REQ-008 / REQ-010).

`/health` is a liveness probe: cheap, no dependencies, always 200 if the
process is up. Suitable for load balancer liveness checks.

`/ready` is a readiness probe: verifies the SQLite file is reachable and
that an LLM API key is configured. Returns 503 problem+json on any failure
so orchestrators can pull traffic until the deployment is ready.

Both endpoints are PUBLIC — no auth dependency.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["health"])

API_VERSION = "1.0.0"


def _check_sqlite() -> dict[str, Any]:
    """Open the configured SQLite DB and run SELECT 1."""
    db_path = os.getenv("CREWAI_DB_PATH", "data/crewai.db")
    try:
        # Short timeout: probe must fail FAST if the DB is locked rather
        # than block the orchestrator's poll loop.
        conn = sqlite3.connect(db_path, timeout=2.0)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
        finally:
            conn.close()
        return {"name": "sqlite", "ok": True}
    except Exception as exc:  # noqa: BLE001 — probe must catch every failure
        return {"name": "sqlite", "ok": False, "error": str(exc)}


def _check_llm_env() -> dict[str, Any]:
    """Verify at least one LLM API key is present in the environment."""
    ok = bool(os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY"))
    check: dict[str, Any] = {"name": "llm_env", "ok": ok}
    if not ok:
        check["error"] = "Neither OPENAI_API_KEY nor ANTHROPIC_API_KEY is set"
    return check


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — always 200 if the process is up.

    Returns `{"status": "healthy", "version": "1.0.0"}`. The envelope
    middleware wraps this in `{data, meta, error}` automatically.
    """
    return {"status": "healthy", "version": API_VERSION}


@router.get("/ready")
async def ready() -> dict[str, Any]:
    """Readiness probe — verifies SQLite + LLM env.

    Returns 200 with `{"checks": [...]}` when all checks pass. On any
    failure, raises HTTPException(503) with the checks array in `detail`
    so the problem+json handler can surface the failing component.
    """
    checks = [_check_sqlite(), _check_llm_env()]
    all_ok = all(check["ok"] for check in checks)

    if not all_ok:
        raise HTTPException(
            status_code=503,
            detail={
                "detail": "One or more readiness checks failed",
                "checks": checks,
            },
        )

    return {"checks": checks}
