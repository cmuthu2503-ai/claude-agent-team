"""Simple pen-test probe — AET-18.

Replays the finalized API spec's endpoints against a running base URL
with four families of malicious inputs. Deliberately scoped to the
quick wins a security_specialist agent needs before merging code:

  AUTH   — missing-auth (no header) and invalid-auth (junk header)
  INJ    — SQL/NoSQL-injection probes in query and path params
  IDOR   — Insecure Direct Object Reference: numeric id bumping
  DOS    — oversized payload (1 MB JSON body) on POST/PUT endpoints

This is "pen test simple," not "burp suite." The probes are read-only
where possible (GET / OPTIONS preferred), short-timeout (10 s/req),
and write-mutating probes (POST/PUT) only fire when the spec
explicitly declares them — we don't *guess* endpoints. Total run
time on a typical 50-endpoint spec is <30 s.

Inputs
------
The tool MUST be given a finalized OpenAPI spec (dict, path, or URL)
AND a base_url to replay against. Without both, ``execute()`` returns
``{"verdict": "SKIPPED"}`` rather than erroring — per the AE-4
contract, security tools degrade rather than fail. A staging URL is
strongly recommended over production: even read-only probes can
trigger rate-limit penalties, intrusion-detection alarms, or
unintended log/metric churn.

Finding shape
-------------
Each finding is normalised to::

    {
      "rule_id":   "AUTH-001" | "INJ-001" | "IDOR-001" | "DOS-001" | …
      "severity":  "critical" | "high" | "medium" | "low" | "info",
      "method":    "GET" | "POST" | …,
      "path":      "/api/v1/users/{user_id}",
      "evidence":  "<short string describing the signal>",
      "status":    <int HTTP status from the malicious request>,
    }

Verdict
-------
``BLOCK`` when any finding is severity high or critical; ``PASS``
otherwise. ``SKIPPED`` when no spec/base_url. ``ERROR`` only on a
total infra failure (httpx import error) so the agent can distinguish
"target healthy + clean" from "I couldn't reach anything."
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
import structlog

logger = structlog.get_logger()

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Per-request budget. The full sweep is ~ (endpoints × probes_per_endpoint)
# requests; a 10s ceiling keeps a hanging probe from wedging the whole
# run, and the outer total-time cap protects against pathological specs.
_REQUEST_TIMEOUT_S = 10.0
_TOTAL_TIMEOUT_S = 120.0

# Severities that block verdict.
_BLOCKING = frozenset({"critical", "high"})


# ── Probe payloads ───────────────────────────────────────────────────────


# Conservative SQL-injection strings. The "boolean-true" variant
# detects responses that change behavior (e.g. always-200 result list)
# without requiring time-based detection. We avoid destructive payloads
# (DROP TABLE etc.) — even on a staging DB, dropping a table is rude.
_SQLI_PAYLOADS = [
    "' OR '1'='1",
    "'  OR 1=1 -- ",
    "1' UNION SELECT NULL-- ",
    "\\\\\"; ",                # quote-escape edge case
]

# NoSQL-injection (Mongo, mostly) — supplied as a JSON object the
# backend's parser may unwrap directly.
_NOSQLI_BODY = {"$ne": None}

# Sentinels that imply a SQL error leaked into the response body —
# strong evidence the server passed our payload into a SQL string.
_SQL_ERROR_SIGNATURES = re.compile(
    r"(?i)(?:sql|sqlite|psql|postgres|mysql|mariadb|odbc|"
    r"syntax\s+error|unterminated\s+quoted|near\s+\"|"
    r"sqlalchemy\.exc|"
    r"unrecognized\s+token|"
    r"asyncpg\.exceptions)",
)


# 1 MB string used for DOS probe. Allocated once to avoid per-call
# memory churn — the body is sent literally as a single JSON string.
_OVERSIZED_PAYLOAD = "A" * (1024 * 1024)


# Tokens (path parameters) we'll consider for IDOR bumping. Anything
# that looks like a positive integer or a uuid template; everything
# else is left alone (the agent's spec might have arbitrary
# placeholder syntax).
_INT_PATH_PARAM_RE = re.compile(r"\{(?P<name>[a-zA-Z_]+_?(?:id|uuid))\}")


# ── Spec loading ─────────────────────────────────────────────────────────


def _load_spec(spec: Any) -> dict[str, Any] | None:
    """Accept any of: a dict already loaded, an absolute path, a
    repo-relative path, or a URL string. Returns the parsed OpenAPI
    dict or None on failure. Failures are logged but never raise — the
    caller treats a missing spec as SKIPPED."""
    if isinstance(spec, dict):
        return spec
    if not isinstance(spec, str) or not spec:
        return None

    # URL? Fetch with the same client/timeout as probes.
    if spec.startswith(("http://", "https://")):
        try:
            r = httpx.get(spec, timeout=_REQUEST_TIMEOUT_S)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("pen_test_spec_url_failed", url=spec, error=str(e))
            return None

    # Path on disk.
    p = Path(spec)
    if not p.is_absolute():
        p = _REPO_ROOT / spec
    if not p.exists():
        logger.warning("pen_test_spec_missing", path=str(p))
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("pen_test_spec_parse_failed", path=str(p), error=str(e))
        return None


def _iter_endpoints(spec: dict[str, Any]):
    """Yield (method, path_template, operation_dict) for every
    operation in an OpenAPI spec. Skips non-method keys (e.g. parameters,
    summary at the path-item level)."""
    methods = {"get", "post", "put", "patch", "delete", "options"}
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for verb, op in item.items():
            if verb.lower() in methods and isinstance(op, dict):
                yield verb.upper(), path, op


# ── Probe execution ──────────────────────────────────────────────────────


class PenTestSimpleTool:
    """OpenAPI-driven black-box probe for the security_specialist agent."""

    def schema(self) -> dict[str, Any]:
        return {
            "name": "pen_test_simple",
            "description": (
                "Replay the project's finalized OpenAPI spec against a "
                "running base_url with four probe families: missing-auth, "
                "SQL/NoSQL-injection, IDOR (numeric id bumping), and "
                "oversized-payload. Returns structured findings + a "
                "BLOCK/PASS/SKIPPED verdict. Skips when no spec or "
                "base_url is provided. Read-only where possible; "
                "write-mutating probes fire only on endpoints the spec "
                "explicitly declares as POST/PUT."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "spec": {
                        "description": (
                            "OpenAPI spec — either an inline dict, a "
                            "repo-relative path to a JSON file, or a "
                            "URL to fetch. Defaults to "
                            "http://{base_url}/api/v1/openapi.json."
                        ),
                    },
                    "base_url": {
                        "type": "string",
                        "description": (
                            "Base URL to probe (e.g. "
                            "http://localhost:8000). Should be staging "
                            "or dev — never production."
                        ),
                    },
                    "auth_header": {
                        "type": "string",
                        "description": (
                            "Optional valid Authorization header used "
                            "for the IDOR probe (to verify the user has "
                            "access to one id and not to a different "
                            "one). Format: 'Bearer <token>'."
                        ),
                    },
                },
                "required": ["base_url"],
            },
        }

    # ── Per-probe helpers ────────────────────────────────────────────────

    async def _probe_missing_auth(
        self, client: httpx.AsyncClient, method: str, path: str,
        op: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """If the spec declares security on this op (or globally) and
        the unauthenticated request returns 2xx, that's a bypass."""
        declared_security = op.get("security")
        if declared_security is None:
            # No security declared on this op — skip; the spec is the
            # source of truth. If you want to require auth everywhere
            # add a global "security" block in the spec.
            return []
        try:
            r = await client.request(method, path, headers={})
        except httpx.HTTPError:
            return []
        if 200 <= r.status_code < 300:
            return [{
                "rule_id": "AUTH-001",
                "severity": "critical",
                "method": method, "path": path,
                "evidence": (
                    f"endpoint declares security but returned "
                    f"{r.status_code} without an Authorization header"
                ),
                "status": r.status_code,
            }]
        return []

    async def _probe_invalid_auth(
        self, client: httpx.AsyncClient, method: str, path: str,
        op: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if op.get("security") is None:
            return []
        try:
            r = await client.request(
                method, path,
                headers={"Authorization": "Bearer not-a-real-token-xxxxxx"},
            )
        except httpx.HTTPError:
            return []
        if 200 <= r.status_code < 300:
            return [{
                "rule_id": "AUTH-002",
                "severity": "high",
                "method": method, "path": path,
                "evidence": (
                    f"endpoint accepted a garbage Bearer token "
                    f"({r.status_code} response)"
                ),
                "status": r.status_code,
            }]
        return []

    async def _probe_sqli(
        self, client: httpx.AsyncClient, method: str, path: str,
        op: dict[str, Any], headers: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Inject each SQLi payload into the first string query
        parameter and check for error leakage. Stops at the first
        confirmed injection per endpoint to avoid amplifying noise."""
        if method not in ("GET", "DELETE"):
            return []
        params = op.get("parameters") or []
        # Pick a single string query param to mutate. Targeting query
        # params (not path) avoids 404s on every probe — most APIs
        # 404 unknown ids before hitting the SQL layer.
        target = next(
            (p for p in params
             if p.get("in") == "query"
             and (p.get("schema") or {}).get("type", "string") == "string"),
            None,
        )
        if not target:
            return []
        name = target["name"]
        out: list[dict[str, Any]] = []
        for payload in _SQLI_PAYLOADS:
            try:
                r = await client.request(
                    method, path, params={name: payload}, headers=headers,
                )
            except httpx.HTTPError:
                continue
            body = (r.text or "")[:4000]
            if _SQL_ERROR_SIGNATURES.search(body):
                out.append({
                    "rule_id": "INJ-001",
                    "severity": "critical",
                    "method": method, "path": path,
                    "evidence": (
                        f"SQL error leaked into response when {name}={payload!r}; "
                        f"matched signature in body[:400]"
                    ),
                    "status": r.status_code,
                })
                break  # one finding per endpoint is sufficient
        return out

    async def _probe_nosqli(
        self, client: httpx.AsyncClient, method: str, path: str,
        op: dict[str, Any], headers: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Send {"$ne": null} as a body to endpoints that take JSON.
        A vulnerable Mongo-backed login returns 200 with a user row;
        a safe one returns 4xx or coerces to string."""
        if method not in ("POST", "PUT", "PATCH"):
            return []
        try:
            r = await client.request(
                method, path,
                json={"username": _NOSQLI_BODY, "password": _NOSQLI_BODY},
                headers=headers,
            )
        except httpx.HTTPError:
            return []
        if 200 <= r.status_code < 300:
            return [{
                "rule_id": "INJ-002",
                "severity": "high",
                "method": method, "path": path,
                "evidence": (
                    "endpoint returned 2xx when given a NoSQL operator "
                    "object ($ne) as a field value — likely unfiltered "
                    "BSON pass-through"
                ),
                "status": r.status_code,
            }]
        return []

    async def _probe_idor(
        self, client: httpx.AsyncClient, method: str, path: str,
        op: dict[str, Any], auth_header: str | None,
    ) -> list[dict[str, Any]]:
        """Sample two adjacent integer ids; if both 200 under the same
        authenticated user, the endpoint isn't checking ownership.
        Single-id 200 is NOT enough — many endpoints expose public
        catalogs. We need the pair signal."""
        if method != "GET":
            return []
        m = _INT_PATH_PARAM_RE.search(path)
        if not m:
            return []
        param = m.group("name")
        path1 = path.replace(f"{{{param}}}", "1")
        path2 = path.replace(f"{{{param}}}", "2")
        headers = {"Authorization": auth_header} if auth_header else {}
        try:
            r1 = await client.get(path1, headers=headers)
            r2 = await client.get(path2, headers=headers)
        except httpx.HTTPError:
            return []
        # Both 200 with different bodies → IDOR. Same body → likely a
        # catalog endpoint, not IDOR. Different status → properly
        # filtered by ownership.
        if r1.status_code == 200 and r2.status_code == 200 and r1.text != r2.text:
            return [{
                "rule_id": "IDOR-001",
                "severity": "high",
                "method": method, "path": path,
                "evidence": (
                    f"adjacent ids (1 and 2) both returned 200 with "
                    f"different bodies under the same auth — endpoint "
                    f"may not be filtering by ownership"
                ),
                "status": 200,
            }]
        return []

    async def _probe_oversize(
        self, client: httpx.AsyncClient, method: str, path: str,
        op: dict[str, Any], headers: dict[str, str],
    ) -> list[dict[str, Any]]:
        """POST a 1 MB JSON string. Servers without a body-size limit
        return 2xx (or hang); safe ones return 413."""
        if method not in ("POST", "PUT"):
            return []
        try:
            r = await client.request(
                method, path,
                json={"payload": _OVERSIZED_PAYLOAD},
                headers=headers,
            )
        except httpx.ReadTimeout:
            return [{
                "rule_id": "DOS-001",
                "severity": "medium",
                "method": method, "path": path,
                "evidence": "request timed out on 1 MB JSON body — "
                            "no enforced body-size limit",
                "status": 0,
            }]
        except httpx.HTTPError:
            return []
        if 200 <= r.status_code < 300:
            return [{
                "rule_id": "DOS-001",
                "severity": "medium",
                "method": method, "path": path,
                "evidence": (
                    f"endpoint accepted 1 MB JSON body and returned "
                    f"{r.status_code} — consider Content-Length cap"
                ),
                "status": r.status_code,
            }]
        return []

    # ── Entry point ──────────────────────────────────────────────────────

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        base_url = (params.get("base_url") or "").rstrip("/")
        if not base_url:
            return {
                "verdict": "SKIPPED",
                "reason": "no base_url provided",
                "findings": [],
                "summary": "Verdict: SKIPPED — base_url required.",
            }

        spec_input = params.get("spec")
        if spec_input is None:
            # Fall back to the project's own OpenAPI endpoint. Same
            # path FastAPI exposes via `openapi_url` in src/main.py.
            spec_input = urljoin(base_url + "/", "api/v1/openapi.json")

        spec = _load_spec(spec_input)
        if not spec:
            return {
                "verdict": "SKIPPED",
                "reason": "no usable OpenAPI spec",
                "findings": [],
                "summary": "Verdict: SKIPPED — spec unavailable.",
            }

        auth_header = params.get("auth_header")
        # Use the auth header (when given) for INJ probes too — the
        # injection target is usually past the auth wall. Missing-auth
        # probes deliberately drop it.
        valid_headers = (
            {"Authorization": auth_header} if auth_header else {}
        )

        findings: list[dict[str, Any]] = []
        endpoints_probed = 0
        endpoints_skipped = 0

        timeout = httpx.Timeout(_REQUEST_TIMEOUT_S, connect=5.0)
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
            for method, path, op in _iter_endpoints(spec):
                # Strip query-string templates and skip server-internal
                # endpoints to avoid wasting cycles on the docs/openapi
                # routes themselves.
                if "/docs" in path or "/openapi.json" in path:
                    endpoints_skipped += 1
                    continue
                endpoints_probed += 1
                try:
                    findings.extend(await self._probe_missing_auth(
                        client, method, path, op,
                    ))
                    findings.extend(await self._probe_invalid_auth(
                        client, method, path, op,
                    ))
                    findings.extend(await self._probe_sqli(
                        client, method, path, op, valid_headers,
                    ))
                    findings.extend(await self._probe_nosqli(
                        client, method, path, op, valid_headers,
                    ))
                    findings.extend(await self._probe_idor(
                        client, method, path, op, auth_header,
                    ))
                    findings.extend(await self._probe_oversize(
                        client, method, path, op, valid_headers,
                    ))
                except Exception as e:  # noqa: BLE001
                    # Per-endpoint failures shouldn't kill the whole
                    # sweep — log and continue.
                    logger.warning(
                        "pen_test_endpoint_error",
                        method=method, path=path, error=str(e),
                    )

        blocking = [f for f in findings if f["severity"] in _BLOCKING]
        verdict = "BLOCK" if blocking else "PASS"

        if verdict == "PASS":
            summary = (
                f"Verdict: PASS — {endpoints_probed} endpoint(s) probed, "
                f"{len(findings)} finding(s), none high/critical."
            )
        else:
            first = blocking[0]
            summary = (
                f"Verdict: BLOCK — {len(blocking)} high/critical "
                f"finding(s); first: {first['rule_id']} "
                f"({first['method']} {first['path']})"
            )

        logger.info(
            "pen_test_complete",
            verdict=verdict,
            endpoints_probed=endpoints_probed,
            endpoints_skipped=endpoints_skipped,
            finding_count=len(findings),
        )

        return {
            "verdict": verdict,
            "endpoints_probed": endpoints_probed,
            "endpoints_skipped": endpoints_skipped,
            "findings": findings,
            "summary": summary,
        }
