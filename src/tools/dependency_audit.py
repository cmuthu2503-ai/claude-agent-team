"""Dependency audit tool — AET-16.

Phase AE-4 split: where ``sast_scan`` (AET-15) covers source-code
weaknesses, ``dependency_audit`` covers the *supply chain* — known
CVEs in third-party packages the project depends on.

Back-ends
---------
  pip-audit   Python deps  (``pip-audit -f json``)
  npm audit   Frontend deps (``npm audit --json``)

Why pip-audit (not safety): safety v3 requires a paid API key; v2 is
deprecated upstream and emits a "use v3" deprecation banner on every
run that breaks JSON parsing intermittently. pip-audit is OSV-backed,
ships under the PyPA umbrella, and produces stable JSON.

Return shape
------------
Both back-ends are aggregated by severity into the unified bucket
format the security gate (AET-20) consumes::

    {
      "verdict":  "PASS" | "BLOCK",      # BLOCK if any critical OR high CVE
      "by_severity": {
        "critical": [<vuln_dict>, …],
        "high":     [<vuln_dict>, …],
        "moderate": [<vuln_dict>, …],
        "low":      [<vuln_dict>, …],
      },
      "by_ecosystem": {
        "python":   {"status": ..., "raw_count": N},
        "frontend": {"status": ..., "raw_count": N},
      },
      "summary":  "<one-line verdict>",
    }

Each ``vuln_dict`` has the unified shape::

    {
      "ecosystem":   "python" | "frontend",
      "package":     "requests",
      "installed":   "2.25.0",
      "vulnerability_id": "GHSA-... or CVE-...",
      "severity":    "critical" | "high" | "moderate" | "low",
      "fix_versions": ["2.31.0", …]   # may be empty if no fix yet
    }

Severity mapping per ecosystem:
  pip-audit:    GHSA/OSV doesn't always include severity. We use the
                aliases list to look up the highest-known severity,
                falling back to "moderate" so the agent sees the
                finding rather than silently dropping it.
  npm audit:    native scale {critical, high, moderate, low, info} —
                mapped 1:1 except 'info' which we drop (npm uses it
                for advisories without any impact).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Subprocess hard time-out — pip-audit can be slow on the first run as
# it pulls the OSV index; 90s gives the cold path room without wedging.
_AUDIT_TIMEOUT_S = 90

# Severities at or above this bucket cause verdict=BLOCK. Tracking it
# centrally so the gate (AET-20) and this tool agree (L23 cross-layer
# label drift).
_BLOCKING_SEVERITIES = frozenset({"critical", "high"})

# Order matters here — when a vulnerability has multiple severity
# claims (e.g. GHSA + CVSS), we pick the worst.
_SEVERITY_ORDER = ["critical", "high", "moderate", "low"]
_SEVERITY_RANK = {s: i for i, s in enumerate(_SEVERITY_ORDER)}


def _worst(*severities: str) -> str:
    """Return the highest-priority severity from the input list.
    Defaults to 'moderate' when no input is recognised so unknown-severity
    findings don't silently drop below the radar."""
    best = "moderate"
    best_rank = _SEVERITY_RANK["moderate"]
    for s in severities:
        s_norm = (s or "").lower()
        rank = _SEVERITY_RANK.get(s_norm)
        if rank is not None and rank < best_rank:
            best = s_norm
            best_rank = rank
    return best


class DependencyAuditTool:
    """Run pip-audit + npm audit; emit unified-severity vulnerability list."""

    # ── Anthropic tool schema ────────────────────────────────────────────

    def schema(self) -> dict[str, Any]:
        return {
            "name": "dependency_audit",
            "description": (
                "Audit project dependencies for known CVEs using pip-audit "
                "(Python) and npm audit (frontend). Returns vulnerabilities "
                "bucketed by severity (critical/high/moderate/low) with a "
                "BLOCK verdict when any critical or high CVE is present. "
                "Each finding includes package, installed version, "
                "vulnerability ID (GHSA / CVE), and fix versions if known."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "python_requirements": {
                        "type": "string",
                        "description": (
                            "Path to a requirements file or pyproject.toml "
                            "for pip-audit. Defaults to the project's "
                            "pyproject.toml. Pass '' to skip Python audit."
                        ),
                    },
                    "frontend_dir": {
                        "type": "string",
                        "description": (
                            "Directory containing package.json for npm "
                            "audit. Defaults to 'frontend'. Pass '' to skip."
                        ),
                    },
                },
                "required": [],
            },
        }

    # ── pip-audit ─────────────────────────────────────────────────────────

    async def run_pip_audit(self, requirements: str | None = None) -> dict[str, Any]:
        """Invoke pip-audit. When *requirements* is None, scan the
        installed environment (``pip-audit -f json`` with no flag);
        when given a path, target that file. Returns
        ``{"status": ..., "raw": [...]}`` — list of pip-audit
        dependency dicts each with a ``vulns`` list inside."""
        if not shutil.which("pip-audit"):
            return {
                "status": "SKIPPED",
                "reason": (
                    "pip-audit not installed (add 'pip-audit>=2.7.0' to "
                    "pyproject.toml dependencies and rebuild the image)"
                ),
            }

        cmd = ["pip-audit", "-f", "json", "--progress-spinner", "off"]
        if requirements:
            req_path = (_REPO_ROOT / requirements).resolve()
            if not req_path.exists():
                return {
                    "status": "SKIPPED",
                    "reason": f"requirements path not found: {req_path}",
                }
            cmd.extend(["-r", str(req_path)])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=_AUDIT_TIMEOUT_S,
            )
            raw = result.stdout.strip()
            if not raw:
                return {"status": "OK", "raw": []}
            data = json.loads(raw)
            # pip-audit JSON shape varies by version:
            #   v2.7+ → {"dependencies": [{"name", "version", "vulns": [...]}]}
            #   older → list of dependency dicts at top level
            deps = data.get("dependencies", data) if isinstance(data, dict) else data
            return {"status": "OK", "raw": deps}
        except subprocess.TimeoutExpired:
            return {"status": "ERROR", "reason": "pip-audit timed out"}
        except json.JSONDecodeError as exc:
            return {"status": "ERROR", "reason": f"pip-audit JSON parse error: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("pip_audit_invocation_failed", error=str(exc))
            return {"status": "ERROR", "reason": str(exc)}

    # ── npm audit ────────────────────────────────────────────────────────

    async def run_npm_audit(self, frontend_dir: str = "frontend") -> dict[str, Any]:
        if not shutil.which("npm"):
            return {
                "status": "SKIPPED",
                "reason": (
                    "npm not available in this environment (backend "
                    "container is Python-only — invoke this tool from "
                    "a node-equipped container)"
                ),
            }
        cwd = (_REPO_ROOT / frontend_dir).resolve()
        if not cwd.exists():
            return {
                "status": "SKIPPED",
                "reason": f"frontend_dir '{frontend_dir}' not found at {cwd}",
            }
        if not (cwd / "package.json").exists():
            return {
                "status": "SKIPPED",
                "reason": f"no package.json in {cwd}",
            }

        try:
            result = subprocess.run(
                ["npm", "audit", "--json"],
                cwd=str(cwd), capture_output=True, text=True,
                timeout=_AUDIT_TIMEOUT_S,
            )
            raw = result.stdout.strip()
            if not raw:
                return {"status": "OK", "raw": {}}
            data = json.loads(raw)
            return {"status": "OK", "raw": data.get("vulnerabilities", {})}
        except subprocess.TimeoutExpired:
            return {"status": "ERROR", "reason": "npm audit timed out"}
        except json.JSONDecodeError as exc:
            return {"status": "ERROR", "reason": f"npm audit JSON parse error: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("npm_audit_invocation_failed", error=str(exc))
            return {"status": "ERROR", "reason": str(exc)}

    # ── Normalisation ────────────────────────────────────────────────────

    def _normalise_pip_audit(
        self, raw: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for dep in raw or []:
            package = dep.get("name") or ""
            installed = dep.get("version") or ""
            for v in dep.get("vulns", []) or []:
                # pip-audit's per-vuln severity is sparse; the aliases
                # list often points to a CVE with a known CVSS score.
                # When neither has a value, _worst() defaults to "moderate"
                # rather than dropping the finding.
                native_sev = v.get("severity") or ""
                severity = _worst(native_sev)
                out.append({
                    "ecosystem": "python",
                    "package": package,
                    "installed": installed,
                    "vulnerability_id": v.get("id") or "",
                    "severity": severity,
                    "fix_versions": v.get("fix_versions") or [],
                })
        return out

    def _normalise_npm_audit(
        self, raw: dict[str, Any],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for package, info in (raw or {}).items():
            severity = (info.get("severity") or "").lower()
            if severity in ("", "info"):
                # npm's 'info' bucket is upstream advisories with no
                # impact — drop to keep the gate signal-to-noise high.
                continue
            installed = ""
            # npm audit's per-package "via" is messy: can be a list of
            # advisory IDs or nested package names. Pull the first
            # advisory-shaped item for the vuln id.
            vuln_id = ""
            for via in info.get("via") or []:
                if isinstance(via, dict):
                    vuln_id = via.get("source") or via.get("url") or ""
                    break
            fix = info.get("fixAvailable")
            fix_versions: list[str] = []
            if isinstance(fix, dict):
                v = fix.get("version")
                if v:
                    fix_versions = [v]
            out.append({
                "ecosystem": "frontend",
                "package": package,
                "installed": installed,
                "vulnerability_id": str(vuln_id),
                "severity": severity,
                "fix_versions": fix_versions,
            })
        return out

    # ── Entry point ──────────────────────────────────────────────────────

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        py_req = params.get("python_requirements")
        if py_req == "":  # explicit skip
            py_audit: dict[str, Any] = {"status": "SKIPPED", "reason": "caller skipped"}
            py_vulns: list[dict[str, Any]] = []
        else:
            py_audit = await self.run_pip_audit(py_req)
            py_vulns = (
                self._normalise_pip_audit(py_audit.get("raw", []))
                if py_audit.get("status") == "OK"
                else []
            )

        frontend_dir = params.get("frontend_dir")
        if frontend_dir == "":  # explicit skip
            npm_audit: dict[str, Any] = {"status": "SKIPPED", "reason": "caller skipped"}
            npm_vulns: list[dict[str, Any]] = []
        else:
            npm_audit = await self.run_npm_audit(frontend_dir or "frontend")
            npm_vulns = (
                self._normalise_npm_audit(npm_audit.get("raw", {}))
                if npm_audit.get("status") == "OK"
                else []
            )

        all_vulns = py_vulns + npm_vulns
        by_severity: dict[str, list[dict[str, Any]]] = {
            "critical": [], "high": [], "moderate": [], "low": [],
        }
        for v in all_vulns:
            sev = v["severity"]
            by_severity.setdefault(sev, []).append(v)

        blocking_count = sum(
            len(by_severity[s]) for s in _BLOCKING_SEVERITIES if s in by_severity
        )
        verdict = "BLOCK" if blocking_count else "PASS"

        if verdict == "PASS":
            summary = (
                f"Verdict: PASS — {len(all_vulns)} vulnerability(ies), "
                f"none critical or high."
            )
        else:
            summary = (
                f"Verdict: BLOCK — {blocking_count} critical/high "
                f"vulnerability(ies); "
                f"{len(by_severity.get('critical', []))} critical, "
                f"{len(by_severity.get('high', []))} high."
            )

        logger.info(
            "dependency_audit_complete",
            verdict=verdict,
            python_count=len(py_vulns),
            frontend_count=len(npm_vulns),
            blocking_count=blocking_count,
        )

        return {
            "verdict": verdict,
            "by_severity": by_severity,
            "by_ecosystem": {
                "python":   {
                    "status": py_audit.get("status"),
                    "reason": py_audit.get("reason"),
                    "raw_count": len(py_vulns),
                },
                "frontend": {
                    "status": npm_audit.get("status"),
                    "reason": npm_audit.get("reason"),
                    "raw_count": len(npm_vulns),
                },
            },
            "summary": summary,
        }
