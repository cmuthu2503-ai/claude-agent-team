"""Ops-check tool — post-deploy health verification for ops_heal_agent.

The ops_heal_agent calls this tool after each deployment to verify that the
running stack is actually healthy. It wraps four lightweight checks:

  health_check    HTTP GET /api/v1/health on the running backend
                  (also attempts the frontend root if `service` includes it)
  disk_usage      shutil.disk_usage() on the app data directory
  memory          Reads /proc/meminfo (Linux only; skipped on other platforms)
  error_patterns  Scans recent structlog output from /app/logs/ for ERROR/CRITICAL
                  lines (skipped when the log directory is not mounted)

All checks degrade gracefully — SKIPPED when the resource is inaccessible,
ERROR (with message) on unexpected exceptions. The agent reads the combined
report and decides whether remediation is needed.

Schema
------
action:   "health_check" | "disk_check" | "memory_check" | "error_scan" | "full_check"
service:  "backend" | "frontend" | "all"   (default "all"; only used by health_check)
log_lines: int  (default 100; max lines to scan for error_scan)
"""

from __future__ import annotations

import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOG_DIR = Path("/app/logs")

# How many bytes of logs to scan for error patterns (capped to avoid huge reads)
_MAX_LOG_BYTES = 64 * 1024  # 64 KB


class OpsCheckTool:
    """Post-deploy health and resource monitoring tool for ops_heal_agent."""

    @staticmethod
    def schema() -> dict[str, Any]:
        return {
            "name": "ops_check",
            "description": (
                "Post-deploy health check: HTTP health endpoints, disk usage, "
                "memory pressure, and recent error log scan. Use action='full_check' "
                "for a complete picture after a deployment."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "health_check",
                            "disk_check",
                            "memory_check",
                            "error_scan",
                            "full_check",
                        ],
                        "description": (
                            "Which check to run. 'full_check' runs all four and returns "
                            "a combined report with an overall verdict."
                        ),
                    },
                    "service": {
                        "type": "string",
                        "enum": ["backend", "frontend", "all"],
                        "description": (
                            "Which service to health-check. Applies to 'health_check' and "
                            "'full_check' only. Defaults to 'all'."
                        ),
                    },
                    "log_lines": {
                        "type": "integer",
                        "description": (
                            "Maximum number of recent log lines to scan for error patterns "
                            "(applies to 'error_scan' and 'full_check'). Default 100."
                        ),
                    },
                },
                "required": ["action"],
            },
        }

    # ------------------------------------------------------------------
    # Individual check methods
    # ------------------------------------------------------------------

    def run_health_check(self, service: str = "all") -> dict[str, Any]:
        """HTTP GET the /api/v1/health endpoint(s).

        Backend:  http://localhost:8000/api/v1/health
        Frontend: http://localhost:3000  (just a GET — any 2xx is healthy)
        """
        results: dict[str, Any] = {}

        endpoints: list[tuple[str, str]] = []
        if service in ("backend", "all"):
            host = os.getenv("HEALTHCHECK_HOST", "localhost")
            endpoints.append(("backend", f"http://{host}:8000/api/v1/health"))
        if service in ("frontend", "all"):
            host = os.getenv("HEALTHCHECK_HOST", "localhost")
            endpoints.append(("frontend", f"http://{host}:3000"))

        for name, url in endpoints:
            try:
                with urllib.request.urlopen(url, timeout=5) as resp:
                    status_code = resp.status
                    body = resp.read(512).decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                status_code = e.code
                body = ""
            except Exception as exc:
                results[name] = {
                    "status": "UNREACHABLE",
                    "error": str(exc),
                }
                continue

            if 200 <= status_code < 300:
                results[name] = {"status": "HEALTHY", "http_status": status_code, "body": body[:200]}
            else:
                results[name] = {
                    "status": "UNHEALTHY",
                    "http_status": status_code,
                    "body": body[:200],
                }

        overall = (
            "HEALTHY"
            if all(r.get("status") == "HEALTHY" for r in results.values())
            else "UNHEALTHY"
        )
        return {"overall": overall, "services": results}

    def run_disk_check(self) -> dict[str, Any]:
        """Check disk usage on /app/data (the SQLite bind-mount path)."""
        data_dir = Path("/app/data") if Path("/app/data").exists() else _REPO_ROOT / "data"
        try:
            usage = shutil.disk_usage(str(data_dir))
            total_gb = usage.total / (1024 ** 3)
            used_gb = usage.used / (1024 ** 3)
            free_gb = usage.free / (1024 ** 3)
            pct_used = (usage.used / usage.total * 100) if usage.total else 0
            status = (
                "CRITICAL" if pct_used > 90
                else "WARNING" if pct_used > 75
                else "OK"
            )
            return {
                "status": status,
                "path": str(data_dir),
                "total_gb": round(total_gb, 2),
                "used_gb": round(used_gb, 2),
                "free_gb": round(free_gb, 2),
                "pct_used": round(pct_used, 1),
            }
        except Exception as exc:
            return {"status": "ERROR", "error": str(exc)}

    def run_memory_check(self) -> dict[str, Any]:
        """Read /proc/meminfo on Linux. Skipped on other platforms."""
        meminfo_path = Path("/proc/meminfo")
        if not meminfo_path.exists():
            return {"status": "SKIPPED", "reason": "/proc/meminfo not available (non-Linux)"}
        try:
            lines = meminfo_path.read_text().splitlines()
            data: dict[str, int] = {}
            for line in lines:
                parts = line.split()
                if len(parts) >= 2 and parts[0].rstrip(":") in (
                    "MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached"
                ):
                    try:
                        data[parts[0].rstrip(":")] = int(parts[1])  # kB
                    except ValueError:
                        pass
            total_kb = data.get("MemTotal", 0)
            avail_kb = data.get("MemAvailable", 0)
            if total_kb == 0:
                return {"status": "ERROR", "error": "MemTotal not found in /proc/meminfo"}
            pct_used = ((total_kb - avail_kb) / total_kb * 100) if total_kb else 0
            status = (
                "CRITICAL" if pct_used > 90
                else "WARNING" if pct_used > 75
                else "OK"
            )
            return {
                "status": status,
                "total_mb": round(total_kb / 1024, 1),
                "available_mb": round(avail_kb / 1024, 1),
                "pct_used": round(pct_used, 1),
            }
        except Exception as exc:
            return {"status": "ERROR", "error": str(exc)}

    def run_error_scan(self, max_lines: int = 100) -> dict[str, Any]:
        """Scan recent structlog output in /app/logs/ for ERROR/CRITICAL lines.

        The backend container writes structlog to stdout, not to a file by default,
        so this check is SKIPPED when /app/logs/ doesn't exist. When the log
        directory IS mounted (e.g. via a compose bind-mount), it tails recent
        *.log files and returns any ERROR/CRITICAL lines found.
        """
        if not _LOG_DIR.exists():
            return {
                "status": "SKIPPED",
                "reason": (
                    f"{_LOG_DIR} not found. Mount a log directory to enable "
                    "error scanning. Backend ERROR lines are visible via "
                    "`docker compose logs backend`."
                ),
            }
        try:
            log_files = sorted(_LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not log_files:
                return {"status": "SKIPPED", "reason": "No *.log files found in /app/logs"}

            # Read the most recent log file, capped at _MAX_LOG_BYTES from the end
            recent = log_files[0]
            size = recent.stat().st_size
            offset = max(0, size - _MAX_LOG_BYTES)
            with recent.open("rb") as fh:
                fh.seek(offset)
                raw = fh.read(_MAX_LOG_BYTES)
            text = raw.decode("utf-8", errors="replace")
            lines = text.splitlines()[-max_lines:]

            error_lines: list[str] = []
            for line in lines:
                upper = line.upper()
                if '"LEVEL":"ERROR"' in line or '"LEVEL":"CRITICAL"' in line or \
                        "ERROR" in upper or "CRITICAL" in upper:
                    error_lines.append(line.strip())

            status = "ISSUES_FOUND" if error_lines else "CLEAN"
            return {
                "status": status,
                "log_file": str(recent),
                "scanned_lines": len(lines),
                "error_count": len(error_lines),
                "recent_errors": error_lines[-20:],  # cap at 20 for the agent's context
            }
        except Exception as exc:
            return {"status": "ERROR", "error": str(exc)}

    # ------------------------------------------------------------------
    # execute() — tool entry point
    # ------------------------------------------------------------------

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        action = params.get("action", "full_check")
        service = params.get("service", "all")
        log_lines = int(params.get("log_lines", 100))

        logger.info("ops_check_running", action=action, service=service)

        if action == "health_check":
            return self.run_health_check(service)

        if action == "disk_check":
            return self.run_disk_check()

        if action == "memory_check":
            return self.run_memory_check()

        if action == "error_scan":
            return self.run_error_scan(log_lines)

        if action == "full_check":
            health = self.run_health_check(service)
            disk = self.run_disk_check()
            memory = self.run_memory_check()
            errors = self.run_error_scan(log_lines)

            # Overall verdict: HEALTHY only if health is up and no CRITICAL resources
            issues: list[str] = []
            if health.get("overall") != "HEALTHY":
                issues.append("One or more services are not responding to health checks")
            if disk.get("status") == "CRITICAL":
                issues.append(f"Disk usage critical: {disk.get('pct_used')}% used")
            if memory.get("status") == "CRITICAL":
                issues.append(f"Memory pressure critical: {memory.get('pct_used')}% used")
            if errors.get("status") == "ISSUES_FOUND":
                issues.append(
                    f"Recent error log entries: {errors.get('error_count')} found"
                )

            verdict = "UNHEALTHY" if issues else "HEALTHY"
            logger.info("ops_check_completed", verdict=verdict, issue_count=len(issues))

            return {
                "verdict": verdict,
                "issues": issues,
                "health": health,
                "disk": disk,
                "memory": memory,
                "error_scan": errors,
            }

        return {"status": "ERROR", "error": f"Unknown action: {action!r}"}
