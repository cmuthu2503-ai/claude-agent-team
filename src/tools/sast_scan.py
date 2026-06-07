"""SAST scan tool — AET-15.

Phase AE-4 splits the legacy monolithic ``security_scan`` tool into
focused, structured tools the ``security_specialist`` agent can compose.
``sast_scan`` is the static-analysis half: it invokes the language-
appropriate linter for each file the agent asks about and returns a
single, normalised finding list.

Back-ends
---------
  bandit  Python SAST (``bandit -r <files> -f json``)
  eslint  JS/TS SAST when the ``security`` plugin is configured
          (``eslint --format json --plugin security <files>``)

Normalised finding shape (one dict per finding)::

    {
      "severity":  "critical" | "high" | "medium" | "low" | "info",
      "file":      "src/api/foo.py",
      "line":      42,
      "rule_id":   "B608" | "security/detect-eval-with-expression",
      "message":   "Possible SQL injection vector through string-based query construction.",
      "snippet":   "<line text from the file or empty if not provided>",
      "tool":      "bandit" | "eslint"
    }

Severity is mapped from each back-end's native scale to the unified
5-step scale above (see ``_BANDIT_SEVERITY`` and ``_ESLINT_SEVERITY``)
so downstream gates can reason about findings without caring which
linter produced them. The pen-test smoke + AET-22 e2e test both rely
on this shape.

Graceful degradation
--------------------
Missing tool → ``{"status": "SKIPPED", "reason": "..."}`` in the
per-backend summary, never an exception. The agent reads the verdict
field; a skip is NOT a fail. AET-22 covers the "bandit installed,
eslint missing" path explicitly.

Return shape
------------
    {
      "verdict":  "PASS" | "BLOCK",        # BLOCK = ≥1 high/critical
      "findings": [<normalised_finding>, …],
      "by_tool":  {"bandit": {"status": ..., "raw_count": N}, ...},
      "summary":  "<one-line human-readable verdict>",
    }
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

# Subprocess hard time-out — bandit on a small repo is ~3s, eslint ~5s;
# 60s is the same cap the legacy security_scan uses so behaviour is
# consistent across tools (L20 single source of truth for limits).
_SCAN_TIMEOUT_S = 60

# bandit reports issue_severity ∈ {LOW, MEDIUM, HIGH}. We collapse to
# the unified scale: HIGH → high, MEDIUM → medium, LOW → low. There is
# no "critical" in bandit; the unified scale keeps the slot reserved
# for eslint's "error" + manual escalations from secret_scan (AET-17).
_BANDIT_SEVERITY = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}

# eslint reports severity as an int: 0 off, 1 warning, 2 error. The
# `security` plugin rules default to warn but can be promoted to error
# in the project's eslint config. Map error→high, warning→medium so a
# CI tightening of "error" automatically blocks the gate.
_ESLINT_SEVERITY = {2: "high", 1: "medium"}

# Severity levels that cause verdict=BLOCK. Anything below this is
# reported but doesn't block the workflow (the agent or a human can
# still decide to fix them).
_BLOCKING_SEVERITIES = frozenset({"critical", "high"})


class SastScanTool:
    """Run language-aware SAST against a list of files and emit
    normalised findings."""

    # ── Anthropic tool schema ────────────────────────────────────────────

    def schema(self) -> dict[str, Any]:
        return {
            "name": "sast_scan",
            "description": (
                "Run static-analysis security scanners (bandit for Python, "
                "eslint+security plugin for JS/TS) against a list of files. "
                "Returns a unified list of {severity, file, line, rule_id, "
                "message, snippet, tool} findings plus an overall verdict: "
                "BLOCK when any finding is severity=high or critical, PASS "
                "otherwise. Missing back-ends are SKIPPED, not errored."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Paths to scan, relative to the repo root or "
                            "absolute. .py files go to bandit; .js/.jsx/.ts/"
                            ".tsx go to eslint. Other extensions are ignored."
                        ),
                    },
                    "frontend_dir": {
                        "type": "string",
                        "description": (
                            "Working directory for eslint (default: "
                            "'frontend'). eslint needs to run from a "
                            "directory containing its config."
                        ),
                    },
                },
                "required": ["files"],
            },
        }

    # ── Bandit ───────────────────────────────────────────────────────────

    async def run_bandit(self, py_files: list[str]) -> dict[str, Any]:
        """Invoke bandit on Python files. Returns
        ``{"status": ..., "raw": [...]}`` — raw bandit findings, NOT
        normalised. ``execute()`` does the normalisation so all
        finding-shape decisions live in one place."""
        if not shutil.which("bandit"):
            return {
                "status": "SKIPPED",
                "reason": (
                    "bandit not installed (add bandit>=1.7.0 to "
                    "pyproject.toml dependencies and rebuild the image)"
                ),
            }
        if not py_files:
            return {"status": "SKIPPED", "reason": "no Python files in scan list"}

        try:
            result = subprocess.run(
                ["bandit", "-r", *py_files, "-f", "json", "-q"],
                capture_output=True, text=True, timeout=_SCAN_TIMEOUT_S,
            )
            raw = result.stdout.strip()
            if not raw:
                return {"status": "OK", "raw": [], "errors": []}
            data = json.loads(raw)
            return {
                "status": "OK",
                "raw": data.get("results", []),
                "errors": data.get("errors", []),
            }
        except subprocess.TimeoutExpired:
            return {"status": "ERROR", "reason": "bandit timed out"}
        except json.JSONDecodeError as exc:
            return {"status": "ERROR", "reason": f"bandit JSON parse error: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("bandit_invocation_failed", error=str(exc))
            return {"status": "ERROR", "reason": str(exc)}

    # ── ESLint ───────────────────────────────────────────────────────────

    async def run_eslint(
        self, web_files: list[str], frontend_dir: str = "frontend",
    ) -> dict[str, Any]:
        """Invoke eslint with JSON output and return raw findings. The
        project's eslint config must enable the ``security`` plugin
        (``eslint-plugin-security``) — without it eslint still runs but
        none of the SAST rules will fire. The skipped/error branches
        cover the case where eslint isn't on PATH (backend container)
        or the frontend_dir doesn't exist."""
        if not shutil.which("eslint") and not shutil.which("npx"):
            return {
                "status": "SKIPPED",
                "reason": (
                    "eslint not available (backend container is Python-only; "
                    "this scan is intended to run from a node-equipped container)"
                ),
            }
        if not web_files:
            return {"status": "SKIPPED", "reason": "no JS/TS files in scan list"}

        cwd = (_REPO_ROOT / frontend_dir).resolve()
        if not cwd.exists():
            return {
                "status": "SKIPPED",
                "reason": f"frontend_dir '{frontend_dir}' not found at {cwd}",
            }

        # Use the local install via npx so we pick up the project's
        # eslint version + plugins, not whatever global is on PATH.
        cmd = ["npx", "--no-install", "eslint", "--format", "json", *web_files]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                cwd=str(cwd), timeout=_SCAN_TIMEOUT_S,
            )
            raw = result.stdout.strip()
            if not raw:
                return {"status": "OK", "raw": []}
            data = json.loads(raw)
            # eslint returns [{filePath, messages: [...]}, …]
            return {"status": "OK", "raw": data}
        except subprocess.TimeoutExpired:
            return {"status": "ERROR", "reason": "eslint timed out"}
        except json.JSONDecodeError as exc:
            return {"status": "ERROR", "reason": f"eslint JSON parse error: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("eslint_invocation_failed", error=str(exc))
            return {"status": "ERROR", "reason": str(exc)}

    # ── Normalisation helpers ────────────────────────────────────────────

    def _normalise_bandit(self, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for r in raw:
            sev_native = (r.get("issue_severity") or "").upper()
            out.append({
                "severity": _BANDIT_SEVERITY.get(sev_native, "info"),
                "file": r.get("filename") or "",
                "line": r.get("line_number") or 0,
                "rule_id": r.get("test_id") or "",
                "message": r.get("issue_text") or "",
                "snippet": (r.get("code") or "").strip(),
                "tool": "bandit",
            })
        return out

    def _normalise_eslint(self, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for file_block in raw:
            file_path = file_block.get("filePath") or ""
            # Strip the repo prefix so paths line up with bandit's
            # (bandit emits repo-relative or absolute depending on how
            # it was invoked; we keep absolute → relative if it lives
            # under the repo root).
            try:
                rel = str(Path(file_path).resolve().relative_to(_REPO_ROOT))
                file_path = rel
            except (ValueError, OSError):
                pass
            for m in file_block.get("messages", []):
                # Only surface security/* rules — vanilla eslint stylistic
                # warnings aren't this tool's concern (and would dwarf
                # the security signal in a typical project).
                rule_id = m.get("ruleId") or ""
                if not rule_id.startswith("security/"):
                    continue
                sev_native = m.get("severity")
                out.append({
                    "severity": _ESLINT_SEVERITY.get(sev_native, "info"),
                    "file": file_path,
                    "line": m.get("line") or 0,
                    "rule_id": rule_id,
                    "message": m.get("message") or "",
                    "snippet": (m.get("source") or "").strip(),
                    "tool": "eslint",
                })
        return out

    # ── Entry point ──────────────────────────────────────────────────────

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        files: list[str] = params.get("files") or []
        frontend_dir: str = params.get("frontend_dir") or "frontend"

        py_files = [f for f in files if f.endswith(".py")]
        web_files = [f for f in files if f.endswith((".js", ".jsx", ".ts", ".tsx"))]

        logger.info(
            "sast_scan_started",
            py_count=len(py_files), web_count=len(web_files),
        )

        bandit = await self.run_bandit(py_files)
        eslint = await self.run_eslint(web_files, frontend_dir)

        findings: list[dict[str, Any]] = []
        by_tool: dict[str, dict[str, Any]] = {}

        if bandit.get("status") == "OK":
            normalised = self._normalise_bandit(bandit.get("raw", []))
            findings.extend(normalised)
            by_tool["bandit"] = {
                "status": "OK",
                "raw_count": len(bandit.get("raw", [])),
                "errors": bandit.get("errors", []),
            }
        else:
            by_tool["bandit"] = bandit

        if eslint.get("status") == "OK":
            normalised = self._normalise_eslint(eslint.get("raw", []))
            findings.extend(normalised)
            by_tool["eslint"] = {
                "status": "OK",
                "raw_count": sum(
                    len(b.get("messages", [])) for b in eslint.get("raw", [])
                ),
            }
        else:
            by_tool["eslint"] = eslint

        blocking = [f for f in findings if f["severity"] in _BLOCKING_SEVERITIES]
        verdict = "BLOCK" if blocking else "PASS"

        if verdict == "PASS":
            summary = (
                f"Verdict: PASS — {len(findings)} finding(s), "
                f"none high/critical."
            )
        else:
            # Show the rule_id of the first blocker so the gate message
            # is actionable even before the agent reads the full list.
            first = blocking[0]
            summary = (
                f"Verdict: BLOCK — {len(blocking)} high/critical "
                f"finding(s); first: {first['rule_id']} at "
                f"{first['file']}:{first['line']}"
            )

        logger.info(
            "sast_scan_complete",
            verdict=verdict,
            finding_count=len(findings),
            blocking_count=len(blocking),
        )

        return {
            "verdict": verdict,
            "findings": findings,
            "by_tool": by_tool,
            "summary": summary,
        }
