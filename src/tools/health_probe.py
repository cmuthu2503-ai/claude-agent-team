"""Health probe tool — AET-26.

Single-shot HTTP probe against an arbitrary URL. The same primitive
the supervisor's background loop (AET-24) uses, exposed as a tool
so the ``ops_heal_agent`` can run an ad-hoc probe between scheduled
ticks (e.g. "after I roll back, is staging healthy again *now*?"
without waiting up to 60s for the next supervisor cycle).

Return shape::

    {
      "ok":               bool,             # True iff 200 ≤ status < 300
      "response_time_ms": int,              # urlopen wall-clock
      "http_status":      int,              # 0 on URLError/timeout/etc.
      "error":            str | None,       # short diagnostic, None on success
      "url":              str,              # echoed back for trace clarity
    }

Why a tool (not just an inline urlopen call): the agent's
``ops_check`` reads /proc/meminfo and disk and logs but does NOT
exercise the HTTP layer in a way the agent can trigger on demand
with custom URLs. ``health_probe`` fills that gap with a tight,
single-purpose contract — perfect for "probe staging, then probe
prod, then decide" sequences in the agent's prompt.

Aligned with the supervisor's probe semantics (AET-24): 5s budget
per call, status=0 on unreachable, body capped at 2KB. Keeps any
delta between "what the agent saw" vs "what the supervisor sees"
to actual wall-clock timing, not implementation drift (L21).
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from typing import Any

import structlog

logger = structlog.get_logger()


# Per-call timeout. Matches the supervisor's _probe_one_env value
# (5s) so the supervisor's probe loop and the agent's ad-hoc probe
# can be compared apples-to-apples. Tune via env if a slow deploy
# legitimately needs more headroom.
_DEFAULT_TIMEOUT_S = 5.0

# Body-read cap — /health endpoints are tiny, but a misbehaving
# server could stream gigabytes. We only need enough to confirm the
# response looks JSON-ish for the audit log.
_BODY_READ_CAP_BYTES = 2048


class HealthProbeTool:
    """Single-shot HTTP probe for use by ops_heal_agent."""

    def schema(self) -> dict[str, Any]:
        return {
            "name": "health_probe",
            "description": (
                "Single-shot HTTP GET against a deploy's health "
                "endpoint. Returns {ok, response_time_ms, "
                "http_status, error?}. Use after a rollback or "
                "intervention to confirm recovery without waiting "
                "for the supervisor's next 60s probe tick. Status=0 "
                "means the endpoint was unreachable; that's a real "
                "outcome the agent should treat as 'still down', "
                "not as 'probe failed'."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": (
                            "Full URL to probe, e.g. "
                            "'http://localhost:8010/api/v1/health'."
                        ),
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "description": (
                            f"Per-call timeout (default "
                            f"{_DEFAULT_TIMEOUT_S}). Capped at 30s."
                        ),
                    },
                },
                "required": ["url"],
            },
        }

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        url = (params.get("url") or "").strip()
        if not url:
            return {
                "ok": False,
                "response_time_ms": 0,
                "http_status": 0,
                "error": "url parameter required",
                "url": "",
            }
        timeout = min(30.0, max(0.5, float(
            params.get("timeout_seconds") or _DEFAULT_TIMEOUT_S
        )))

        started = time.monotonic()
        status_code = 0
        error: str | None = None
        body_preview = ""
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                status_code = resp.status
                body_preview = resp.read(_BODY_READ_CAP_BYTES).decode(
                    "utf-8", errors="replace",
                )
        except urllib.error.HTTPError as e:
            status_code = e.code
            error = f"HTTP {e.code}"
        except urllib.error.URLError as e:
            error = f"URLError: {e.reason}"
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
        elapsed_ms = int((time.monotonic() - started) * 1000)

        ok = 200 <= status_code < 300

        logger.info(
            "health_probe_complete",
            url=url, ok=ok, http_status=status_code,
            response_time_ms=elapsed_ms,
        )

        return {
            "ok": ok,
            "response_time_ms": elapsed_ms,
            "http_status": status_code,
            "error": error,
            "url": url,
            "body_preview": body_preview[:200],
        }
