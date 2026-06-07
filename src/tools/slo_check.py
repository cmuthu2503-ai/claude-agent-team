"""SLO check tool — AET-25.

Reads recent ``deploy_health`` rows (written every 60s by the
supervisor probe loop — AET-24) and evaluates them against the
project's Service Level Objectives. Returns a structured per-SLO
verdict the ``ops_heal_agent`` (AET-30) consumes to decide whether
to call ``auto_rollback`` (AET-27).

Contract distinction vs ``ops_check`` (the legacy live-probe tool):
  - ops_check    = "is the service responding RIGHT NOW?" (single
                    synchronous probe at call time)
  - slo_check    = "is the service meeting its SLOs OVER A WINDOW?"
                    (rolling aggregate over the last N minutes of
                    probe history)

The two complement each other: ops_check answers "is it up?";
slo_check answers "is it healthy enough that we should leave it
alone?".

SLO catalog (the dials operators turn):

  availability         fraction of 200-ish responses ≥ 99.0% over 15m
  p95_latency_ms       95th percentile response time ≤ 500ms
  p99_latency_ms       99th percentile response time ≤ 1000ms
  error_rate_5m        ≤ 1% over rolling 5m
  restart_burst        ≤ 2 restarts in 5m (rules out flapping)

Each SLO produces ``{name, target, observed, passed, samples}``.
The top-level ``verdict`` is ``PASS`` iff ALL SLOs passed,
``DEGRADED`` if any non-availability SLO failed, ``BREACH`` if
availability dipped below target (the auto_rollback trigger).

Why three verdicts not two: a noisy p99 on a single env doesn't
warrant a rollback (that's a perf incident, route to alerting),
but availability dropping below 99% means users are seeing errors
and the rollback policy should kick in. The agent reads the
verdict and decides.

Insufficient-data path: when fewer than ``MIN_SAMPLES`` probes are
available (e.g. cold start, supervisor just booted), the verdict is
``INSUFFICIENT_DATA`` and every SLO reports samples=N. The agent
must not roll back on this — premature rollback on a cold start was
the L19-style trap this guard exists to prevent.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog
import yaml

logger = structlog.get_logger()


# ── SLO config loading (AET-25 — config/slo.yaml) ───────────────────────


_REPO_ROOT = Path(__file__).resolve().parents[2]
SLO_CONFIG_PATH = Path(
    os.getenv("SLO_CONFIG_PATH", str(_REPO_ROOT / "config" / "slo.yaml")),
)

# Hard-coded fallback used when slo.yaml is missing or fails to parse.
# Mirrors the `defaults:` block of the canonical config so behaviour
# stays identical regardless of whether the YAML loaded.
_FALLBACK_DEFAULTS: dict[str, Any] = {
    "window_minutes": 15,
    "min_samples": 3,
    "slos": {
        "availability":   {"target": 0.99,  "comparator": "ge", "severity": "breach"},
        "p95_latency_ms": {"target": 500,   "comparator": "le", "severity": "degraded"},
        "p99_latency_ms": {"target": 1000,  "comparator": "le", "severity": "degraded"},
        "error_rate_5m":  {"target": 0.01,  "comparator": "le", "severity": "degraded"},
        "restart_burst":  {"target": 2,     "comparator": "le", "severity": "degraded"},
    },
}


def load_slo_config() -> dict[str, Any]:
    """Read config/slo.yaml and return its parsed dict. Falls back to
    the hard-coded defaults dict on missing file / parse error so a
    bad config can't disable the SLO gate silently. Logs a WARNING
    on fallback so the operator notices."""
    if not SLO_CONFIG_PATH.exists():
        logger.warning(
            "slo_config_missing",
            path=str(SLO_CONFIG_PATH),
            hint="using hard-coded defaults",
        )
        return {"defaults": _FALLBACK_DEFAULTS}
    try:
        text = SLO_CONFIG_PATH.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("slo.yaml root must be a mapping")
        if "defaults" not in data:
            data["defaults"] = _FALLBACK_DEFAULTS
        return data
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "slo_config_parse_failed",
            path=str(SLO_CONFIG_PATH), error=str(e),
            hint="using hard-coded defaults",
        )
        return {"defaults": _FALLBACK_DEFAULTS}


def resolve_env_slos(config: dict[str, Any], env: str) -> dict[str, Any]:
    """Merge env-specific overrides over the defaults block. Per-SLO
    `target` field overrides apply only to that SLO; everything else
    falls through. Returns
    ``{window_minutes, min_samples, slos: {<name>: {target, comparator, severity}}}``.
    """
    defaults = config.get("defaults") or _FALLBACK_DEFAULTS
    env_block = config.get(env) or {}
    if not isinstance(env_block, dict):
        env_block = {}

    window = env_block.get("window_minutes", defaults.get("window_minutes", 15))
    min_samples = env_block.get("min_samples", defaults.get("min_samples", 3))

    # Deep merge slos: start from defaults, overlay env-specific fields
    # per SLO. This way env can override just `target` without having
    # to restate `comparator` + `severity`.
    merged_slos: dict[str, Any] = {}
    default_slos = defaults.get("slos") or {}
    env_slos = env_block.get("slos") or {}
    for name, default_spec in default_slos.items():
        merged = dict(default_spec)
        override = env_slos.get(name) or {}
        merged.update(override)
        merged_slos[name] = merged

    return {
        "window_minutes": int(window),
        "min_samples": int(min_samples),
        "slos": merged_slos,
    }


def _percentile(values: list[float], pct: float) -> float | None:
    """Return the *pct*-th percentile (0-100) of *values* using
    linear interpolation. None on empty input. Implemented locally
    rather than reaching for numpy to keep the tool dependency-light —
    the supervisor host doesn't have numpy."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (pct / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] + (s[c] - s[f]) * (k - f)


def _evaluate_slo(
    name: str, observed: float | None, target: float,
    comparator: str, samples: int,
) -> dict[str, Any]:
    """Build one SLO result dict. *comparator* is the direction of
    health: 'le' = lower is better (latency, error_rate, restart_burst);
    'ge' = higher is better (availability)."""
    if observed is None:
        return {
            "name": name, "target": target, "observed": None,
            "comparator": comparator, "samples": samples,
            "passed": None,
        }
    if comparator == "ge":
        passed = observed >= target
    else:  # "le"
        passed = observed <= target
    return {
        "name": name, "target": target, "observed": observed,
        "comparator": comparator, "samples": samples, "passed": passed,
    }


class SloCheckTool:
    """Rolling-window SLO evaluator for the ops_heal_agent."""

    def schema(self) -> dict[str, Any]:
        return {
            "name": "slo_check",
            "description": (
                "Evaluate the recent deploy_health history against the "
                "project's SLOs (availability ≥99%, p95 latency ≤500ms, "
                "p99 ≤1s, error_rate_5m ≤1%, restart_burst ≤2/5m) for "
                "a given env and rolling time window. Returns a "
                "structured verdict (PASS / DEGRADED / BREACH / "
                "INSUFFICIENT_DATA) with per-SLO {target, observed, "
                "passed, samples}. BREACH on availability is the signal "
                "auto_rollback uses; DEGRADED on a latency SLO routes "
                "to alerting only."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "env": {
                        "type": "string",
                        "description": (
                            "Environment to evaluate: 'development', "
                            "'staging', 'production', or 'demo'."
                        ),
                    },
                    "window_minutes": {
                        "type": "integer",
                        "description": (
                            f"Rolling window in minutes (default "
                            f"{DEFAULT_WINDOW_M}). Must be ≥1."
                        ),
                    },
                    "deploy_id": {
                        "type": "string",
                        "description": (
                            "Optional — limit to one specific deploy. "
                            "Default: all probes for the env in the "
                            "window."
                        ),
                    },
                },
                "required": ["env"],
            },
        }

    def __init__(self, state: Any = None) -> None:
        """*state* is the StateStore the tool reads probes through.
        Optional so unit tests can stub it; when missing, ``execute()``
        returns ERROR rather than crashing."""
        self.state = state

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        env = (params.get("env") or "").strip()
        if not env:
            return {
                "verdict": "ERROR",
                "reason": "env parameter required",
                "slos": [],
                "summary": "Verdict: ERROR — env required.",
            }
        if self.state is None:
            return {
                "verdict": "ERROR",
                "reason": "no state store wired into slo_check",
                "slos": [],
                "summary": "Verdict: ERROR — state store unavailable.",
            }

        # Load AET-25 slo.yaml on every call so config edits land
        # without a restart. Cheap (single YAML parse on a small file).
        cfg = resolve_env_slos(load_slo_config(), env)
        window_m = max(1, int(params.get("window_minutes") or cfg["window_minutes"]))
        min_samples = cfg["min_samples"]
        deploy_id = params.get("deploy_id") or None
        since = datetime.utcnow() - timedelta(minutes=window_m)

        probes = await self.state.list_deploy_health_probes(
            env=env, deploy_id=deploy_id, since=since, limit=5000,
        )
        samples = len(probes)

        if samples < min_samples:
            return {
                "verdict": "INSUFFICIENT_DATA",
                "env": env,
                "window_minutes": window_m,
                "samples": samples,
                "min_samples": min_samples,
                "slos": [],
                "summary": (
                    f"Verdict: INSUFFICIENT_DATA — {samples} probe(s) "
                    f"in last {window_m}m for env={env}; need "
                    f"≥{min_samples}."
                ),
            }

        # ── Derived metrics ────────────────────────────────────────────
        # All percentile / fraction calculations done locally to avoid
        # numpy. Only consider probes whose http_status was populated;
        # a status of 0 means "did not reach server" and is treated as
        # an unavailability event, not a latency outlier.
        latencies = [
            float(p.response_time_ms or 0) for p in probes
            if p.http_status and 200 <= p.http_status < 300
        ]
        success_count = sum(
            1 for p in probes
            if p.http_status and 200 <= p.http_status < 300
        )
        availability = success_count / samples if samples else 0.0

        # error_rate_5m: the per-probe column already captures the
        # supervisor's bit (1.0 = error, 0.0 = ok). Average those
        # restricted to the rolling 5m sub-window. If 5m < window_m
        # we still average; if more samples landed than expected (probe
        # cadence drift) the mean stays meaningful.
        five_min_cutoff = datetime.utcnow() - timedelta(minutes=5)
        recent_5m = [
            p for p in probes if p.recorded_at >= five_min_cutoff
        ]
        if recent_5m:
            error_rate_5m = sum(
                (p.error_rate_5m or 0.0) for p in recent_5m
            ) / len(recent_5m)
        else:
            error_rate_5m = None

        # restart_burst: sum of restart_count in the 5m sub-window. The
        # supervisor writes restart_count per-probe (currently 0 — will
        # be populated by a follow-up AET); summing across the window
        # gives the burst figure.
        restart_burst = sum(
            (p.restart_count or 0) for p in recent_5m
        )

        p95 = _percentile(latencies, 95)
        p99 = _percentile(latencies, 99)

        # ── SLO evaluation (driven by AET-25 config) ──────────────────
        # The observed-value resolver maps each SLO name to its
        # already-computed metric + sample count. Keeps the YAML schema
        # the source of truth for {name, comparator, severity}.
        observed_by_name: dict[str, tuple[float | None, int]] = {
            "availability":   (availability,           samples),
            "p95_latency_ms": (p95,                    len(latencies)),
            "p99_latency_ms": (p99,                    len(latencies)),
            "error_rate_5m":  (error_rate_5m,          len(recent_5m)),
            "restart_burst":  (float(restart_burst),   len(recent_5m)),
        }
        slos: list[dict[str, Any]] = []
        for name, spec in cfg["slos"].items():
            obs, count = observed_by_name.get(name, (None, 0))
            r = _evaluate_slo(
                name, obs, float(spec["target"]),
                spec.get("comparator", "le"), count,
            )
            # Stamp severity from the config so the verdict resolver
            # below can read it directly. Defaults to 'degraded' for
            # unknown SLO names so they never accidentally trigger
            # rollback.
            r["severity"] = spec.get("severity", "degraded")
            slos.append(r)

        # ── Verdict resolution ────────────────────────────────────────
        # Per-SLO severity from the config decides the routing:
        #   - any failed SLO with severity='breach' → BREACH (rollback)
        #   - else any failed SLO                   → DEGRADED (alert)
        #   - else                                  → PASS
        breach_failed = any(
            s["passed"] is False and s.get("severity") == "breach"
            for s in slos
        )
        any_failed = any(s["passed"] is False for s in slos)
        if breach_failed:
            verdict = "BREACH"
        elif any_failed:
            verdict = "DEGRADED"
        else:
            verdict = "PASS"

        # Surface the worst-offender in the summary so the agent's
        # rework log has actionable detail without parsing the slos list.
        worst = next(
            (s for s in slos if s["passed"] is False),
            None,
        )
        if verdict == "PASS":
            summary = (
                f"Verdict: PASS — env={env}, {samples} probe(s) over "
                f"{window_m}m, all 5 SLOs met."
            )
        else:
            tag = f"{worst['name']} = {worst['observed']!r}" if worst else ""
            summary = (
                f"Verdict: {verdict} — env={env}, {samples} probe(s) "
                f"over {window_m}m; first failing SLO: {tag} "
                f"(target {worst['target'] if worst else '?'} "
                f"{worst['comparator'] if worst else '?'})."
            )

        logger.info(
            "slo_check_complete",
            env=env, verdict=verdict, samples=samples,
            availability=availability,
            p95=p95, p99=p99, error_rate=error_rate_5m,
        )

        return {
            "verdict": verdict,
            "env": env,
            "window_minutes": window_m,
            "samples": samples,
            "slos": slos,
            "summary": summary,
        }
