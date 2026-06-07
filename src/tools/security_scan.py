"""Security scan tool — SAST, dependency audit, and secrets detection.

Wraps four scanning back-ends into a single callable tool for the
``security_specialist`` agent:

  bandit          Python SAST (high/medium/low severity findings)
  safety          Python dependency CVE check (requires safety<3.0;
                  v3+ requires an API key which we don't have in CI)
  npm audit       Frontend dependency audit  (skipped when npm is absent,
                  e.g. inside the pure-Python backend container)
  detect-secrets  Yelp's secrets-pattern scanner

Every back-end degrades gracefully:
  - Tool binary not on PATH  → ``{"status": "SKIPPED", "reason": "..."}``
  - Subprocess crash / parse error → ``{"status": "ERROR", "error": "..."}``
  - Clean run (even with findings) → structured findings dict

``execute()`` aggregates all four results and sets:
  verdict = "FAIL"  if any scanner reports CRITICAL or HIGH findings, or
                    if any secrets are detected
  verdict = "PASS"  otherwise (MEDIUM/LOW findings are logged, not blocking)
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Subprocess hard time-out so a hung scanner can't wedge a workflow stage.
_SCAN_TIMEOUT_S = 60


class SecurityScanTool:
    """Run SAST, dependency audit, and secrets detection against project files."""

    # ── Anthropic tool schema ────────────────────────────────────────────────

    def schema(self) -> dict[str, Any]:
        return {
            "name": "security_scan",
            "description": (
                "Run automated security scanning against the generated code files. "
                "Executes up to four scanners in parallel: "
                "bandit (Python SAST), safety (Python CVEs), "
                "npm audit (frontend deps), detect-secrets (hard-coded credentials). "
                "Returns a structured report with verdict=PASS or verdict=FAIL. "
                "FAIL means at least one CRITICAL or HIGH finding was detected, "
                "or a secret/credential was found in the code."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "List of file paths to scan (relative to the project root "
                            "or absolute). Python files go to bandit + detect-secrets. "
                            "If empty or omitted, the scanner defaults to src/ for "
                            "Python SAST and frontend/ for npm audit."
                        ),
                    },
                    "project_dir": {
                        "type": "string",
                        "description": (
                            "Directory passed to npm audit (default: 'frontend'). "
                            "Only relevant when npm is available in the environment."
                        ),
                    },
                },
                "required": [],
            },
        }

    # ── Individual scanners ──────────────────────────────────────────────────

    async def run_bandit(self, file_paths: list[str]) -> dict[str, Any]:
        """SAST scan of Python files using bandit."""
        if not shutil.which("bandit"):
            return {
                "tool": "bandit",
                "status": "SKIPPED",
                "reason": "bandit not installed (add bandit>=1.7.0 to pyproject.toml dependencies and rebuild)",
            }
        if not file_paths:
            return {"tool": "bandit", "status": "SKIPPED", "reason": "no files provided"}

        # Resolve paths; skip non-Python files so the agent can safely pass
        # mixed lists (e.g. including .tsx files from a combined file list).
        py_files = [p for p in file_paths if p.endswith(".py")]
        if not py_files:
            return {"tool": "bandit", "status": "SKIPPED", "reason": "no Python files in list"}

        try:
            result = subprocess.run(
                ["bandit", "-r", *py_files, "-f", "json", "-q"],
                capture_output=True,
                text=True,
                timeout=_SCAN_TIMEOUT_S,
            )
            # bandit exits non-zero when findings exist — that's expected.
            raw = result.stdout.strip()
            if not raw:
                return {"tool": "bandit", "findings": [], "high_count": 0, "error_count": 0}

            data = json.loads(raw)
            results = data.get("results", [])
            high_count = sum(
                1 for r in results
                if r.get("issue_severity", "").upper() in ("HIGH", "CRITICAL")
            )
            return {
                "tool": "bandit",
                "findings": results,
                "high_count": high_count,
                "error_count": len(data.get("errors", [])),
            }
        except subprocess.TimeoutExpired:
            return {"tool": "bandit", "status": "ERROR", "error": "scan timed out"}
        except json.JSONDecodeError as exc:
            return {"tool": "bandit", "status": "ERROR", "error": f"JSON parse error: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("bandit_scan_failed", error=str(exc))
            return {"tool": "bandit", "status": "ERROR", "error": str(exc)}

    async def run_npm_audit(self, project_dir: str = "frontend") -> dict[str, Any]:
        """Dependency vulnerability scan for the frontend via npm audit."""
        if not shutil.which("npm"):
            return {
                "tool": "npm_audit",
                "status": "SKIPPED",
                "reason": "npm not available in this environment (backend container is Python-only)",
            }

        audit_dir = _REPO_ROOT / project_dir
        if not audit_dir.exists():
            return {
                "tool": "npm_audit",
                "status": "SKIPPED",
                "reason": f"project_dir '{project_dir}' not found at {audit_dir}",
            }

        try:
            result = subprocess.run(
                ["npm", "audit", "--json"],
                cwd=str(audit_dir),
                capture_output=True,
                text=True,
                timeout=_SCAN_TIMEOUT_S,
            )
            data = json.loads(result.stdout)
            vulns = data.get("vulnerabilities", {})
            high_count = sum(
                1 for v in vulns.values()
                if v.get("severity", "") in ("high", "critical")
            )
            critical_count = sum(
                1 for v in vulns.values()
                if v.get("severity", "") == "critical"
            )
            return {
                "tool": "npm_audit",
                "vulnerabilities": list(vulns.values()),
                "high_count": high_count,
                "critical_count": critical_count,
            }
        except subprocess.TimeoutExpired:
            return {"tool": "npm_audit", "status": "ERROR", "error": "audit timed out"}
        except json.JSONDecodeError as exc:
            return {"tool": "npm_audit", "status": "ERROR", "error": f"JSON parse error: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("npm_audit_failed", error=str(exc))
            return {"tool": "npm_audit", "status": "ERROR", "error": str(exc)}

    async def run_safety_check(self) -> dict[str, Any]:
        """Python dependency CVE scan via safety check."""
        if not shutil.which("safety"):
            return {
                "tool": "safety",
                "status": "SKIPPED",
                "reason": "safety not installed (add 'safety>=2.3.0,<3.0' to pyproject.toml dependencies and rebuild)",
            }

        try:
            result = subprocess.run(
                ["safety", "check", "--json"],
                capture_output=True,
                text=True,
                timeout=_SCAN_TIMEOUT_S,
            )
            # safety exits 255 when vulnerabilities are found — parse stdout
            raw = result.stdout.strip()
            if not raw:
                return {"tool": "safety", "vulnerabilities": [], "critical_cve_count": 0}

            data = json.loads(raw)
            # safety v2 output: list of [package, installed, affected, id, description]
            vulns = data if isinstance(data, list) else data.get("vulnerabilities", [])
            return {
                "tool": "safety",
                "vulnerabilities": vulns,
                "critical_cve_count": len(vulns),  # safety v2 doesn't distinguish severity
            }
        except subprocess.TimeoutExpired:
            return {"tool": "safety", "status": "ERROR", "error": "check timed out"}
        except json.JSONDecodeError as exc:
            return {"tool": "safety", "status": "ERROR", "error": f"JSON parse error: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("safety_check_failed", error=str(exc))
            return {"tool": "safety", "status": "ERROR", "error": str(exc)}

    async def run_detect_secrets(self, file_paths: list[str]) -> dict[str, Any]:
        """Scan files for hard-coded secrets using detect-secrets."""
        if not shutil.which("detect-secrets"):
            return {
                "tool": "detect_secrets",
                "status": "SKIPPED",
                "reason": "detect-secrets not installed (add 'detect-secrets>=1.4.0' to pyproject.toml dependencies and rebuild)",
            }
        if not file_paths:
            return {"tool": "detect_secrets", "status": "SKIPPED", "reason": "no files provided"}

        try:
            result = subprocess.run(
                ["detect-secrets", "scan", *file_paths],
                capture_output=True,
                text=True,
                timeout=_SCAN_TIMEOUT_S,
            )
            data = json.loads(result.stdout)
            results_by_file: dict = data.get("results", {})
            all_findings = [
                {"file": f, **secret}
                for f, secrets in results_by_file.items()
                for secret in secrets
            ]
            return {
                "tool": "detect_secrets",
                "secrets_found": len(all_findings),
                "details": all_findings,
            }
        except subprocess.TimeoutExpired:
            return {"tool": "detect_secrets", "status": "ERROR", "error": "scan timed out"}
        except json.JSONDecodeError as exc:
            return {"tool": "detect_secrets", "status": "ERROR", "error": f"JSON parse error: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("detect_secrets_failed", error=str(exc))
            return {"tool": "detect_secrets", "status": "ERROR", "error": str(exc)}

    # ── Main entry point ─────────────────────────────────────────────────────

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run all four scanners and return an aggregated security report.

        Returns a dict with:
          verdict      "PASS" | "FAIL"
          scan_results list of per-scanner result dicts
          summary      one-line human-readable verdict string
        """
        file_paths: list[str] = params.get("files", [])
        project_dir: str = params.get("project_dir", "frontend")

        # Default Python scan target: src/ when no files given
        effective_py_files = file_paths or [str(_REPO_ROOT / "src")]

        logger.info(
            "security_scan_started",
            file_count=len(file_paths),
            project_dir=project_dir,
        )

        bandit_result = await self.run_bandit(effective_py_files)
        npm_result = await self.run_npm_audit(project_dir)
        safety_result = await self.run_safety_check()
        secrets_result = await self.run_detect_secrets(effective_py_files)

        scan_results = [bandit_result, npm_result, safety_result, secrets_result]

        # ── Verdict logic ────────────────────────────────────────────────────
        fail_reasons: list[str] = []

        # bandit: any HIGH/CRITICAL finding → FAIL
        if bandit_result.get("status") not in ("SKIPPED", "ERROR"):
            if bandit_result.get("high_count", 0) > 0:
                fail_reasons.append(
                    f"bandit: {bandit_result['high_count']} HIGH/CRITICAL Python SAST finding(s)"
                )

        # npm audit: any critical/high vulnerability → FAIL
        if npm_result.get("status") not in ("SKIPPED", "ERROR"):
            if npm_result.get("critical_count", 0) > 0:
                fail_reasons.append(
                    f"npm audit: {npm_result['critical_count']} critical vulnerability(ies)"
                )
            elif npm_result.get("high_count", 0) > 0:
                fail_reasons.append(
                    f"npm audit: {npm_result['high_count']} high-severity vulnerability(ies)"
                )

        # safety: any CVE → FAIL
        if safety_result.get("status") not in ("SKIPPED", "ERROR"):
            if safety_result.get("critical_cve_count", 0) > 0:
                fail_reasons.append(
                    f"safety: {safety_result['critical_cve_count']} CVE(s) in Python dependencies"
                )

        # detect-secrets: any secret → FAIL
        if secrets_result.get("status") not in ("SKIPPED", "ERROR"):
            if secrets_result.get("secrets_found", 0) > 0:
                fail_reasons.append(
                    f"detect-secrets: {secrets_result['secrets_found']} secret(s) detected"
                )

        verdict = "FAIL" if fail_reasons else "PASS"
        if verdict == "PASS":
            summary = "Verdict: ✅ PASS — no CRITICAL/HIGH findings, no secrets detected."
        else:
            summary = "Verdict: ❌ FAIL — " + "; ".join(fail_reasons)

        logger.info(
            "security_scan_complete",
            verdict=verdict,
            fail_reasons=fail_reasons,
        )

        return {
            "verdict": verdict,
            "scan_results": scan_results,
            "summary": summary,
        }
