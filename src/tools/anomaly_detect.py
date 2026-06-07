"""Anomaly detector — AET-27.

Reads the last hour of ``deploy_health`` probes and flags any metric
whose CURRENT short-term value is more than ``SIGMA_THRESHOLD`` (default
2.0) standard deviations away from its rolling baseline. The agent
(AET-30/31) wires this to the ``deploy_health.anomaly_detected``
event channel so the Ops Console can render live alerts.

Contract distinction vs ``slo_check`` (AET-25):
  - slo_check    = "is the env meeting its FIXED targets?" (absolute
                    thresholds from slo.yaml)
  - anomaly_detect = "is the env behaving DIFFERENTLY than recently?"
                    (relative to its own rolling baseline)

The two are complementary: slo_check catches "we always violate this
target"; anomaly_detect catches "something changed in the last 5min
that doesn't violate any target yet but probably will."

Detection algorithm (per metric)::

    baseline   = mean(metric, recent 1hr EXCLUDING the last 5min)
    sigma      = stddev(metric, same window)
    current    = mean(metric, last 5min)
    deviation  = abs(current - baseline) / max(sigma, MIN_SIGMA)

    alert  iff  deviation >= SIGMA_THRESHOLD  AND  baseline_samples >= MIN_BASELINE

Why exclude the current 5min from the baseline: if we included it, a
sustained anomaly would slowly poison its own baseline and the alert
would self-extinguish — exactly the masking failure mode that makes
naive z-score systems miss long incidents.

MIN_SIGMA floor (default 1.0) prevents division-by-zero alerts when
the baseline was unusually stable. Without it a normally-100ms
response time wobbling 1ms in the current window would alert at
infinite sigma.

Output shape::

    {
      "verdict": "OK" | "ANOMALY" | "INSUFFICIENT_DATA",
      "env": str,
      "alerts": [
        {
          "metric": "response_time_ms",
          "current_value": float,
          "baseline": float,
          "sigma": float,
          "deviation_sigmas": float,
          "threshold": 2.0,
          "samples": {"baseline": N, "current": M},
        }, …
      ],
      "summary": "<one-line verdict>",
    }
"""

from __future__ import annotations

import math
import os
import statistics
from datetime import datetime, timedelta
from typing import Any

import structlog

logger = structlog.get_logger()


# ── Tuning knobs (operator-overridable via env) ─────────────────────────

# Sigma cutoff above which a metric counts as anomalous. 2.0 is the
# classic z-score outlier threshold (~95% of normal samples fall within
# 2σ). Loosen to 3.0 to suppress noise; tighten to 1.5 to catch
# incidents earlier at the cost of false positives.
SIGMA_THRESHOLD = float(os.getenv("ANOMALY_SIGMA_THRESHOLD", "2.0"))

# Baseline window in minutes. 1hr is the AET-27 spec; matches the
# typical "I want to know if THIS deploy is different from the rest
# of today" question operators ask.
BASELINE_WINDOW_M = int(os.getenv("ANOMALY_BASELINE_WINDOW_M", "60"))

# Current window in minutes. 5min is the same span slo_check uses for
# error_rate_5m, so the two tools' "current" sub-windows align.
CURRENT_WINDOW_M = int(os.getenv("ANOMALY_CURRENT_WINDOW_M", "5"))

# Minimum baseline samples before we'll emit ANOMALY. Below this we
# report INSUFFICIENT_DATA so a cold start can't trigger a false
# anomaly on the second probe. ~10 covers 10min of probe history at
# the AET-24 60s cadence.
MIN_BASELINE_SAMPLES = int(os.getenv("ANOMALY_MIN_BASELINE_SAMPLES", "10"))

# Minimum current-window samples. 2 is enough that one weird probe
# can't single-handedly trigger an alert.
MIN_CURRENT_SAMPLES = int(os.getenv("ANOMALY_MIN_CURRENT_SAMPLES", "2"))

# Sigma floor — see module doc. Prevents alerts when the baseline
# happened to be perfectly stable (sigma → 0).
MIN_SIGMA: dict[str, float] = {
    "response_time_ms": 1.0,
    "error_rate_5m":    0.001,
    "restart_count":    0.5,
}


def _extract_metric(probe: Any, metric: str) -> float | None:
    """Return the metric's value for a single probe, or None when
    missing. Centralises the column-name mapping so a future metric
    addition is a one-line change here."""
    if metric == "response_time_ms":
        return float(probe.response_time_ms) if probe.response_time_ms is not None else None
    if metric == "error_rate_5m":
        return float(probe.error_rate_5m) if probe.error_rate_5m is not None else None
    if metric == "restart_count":
        return float(probe.restart_count) if probe.restart_count is not None else None
    return None


def _detect_one_metric(
    metric: str,
    baseline_values: list[float],
    current_values: list[float],
) -> dict[str, Any] | None:
    """Compute baseline/current/deviation for a single metric. Returns
    a populated alert dict iff the deviation crosses SIGMA_THRESHOLD,
    else None (metric is healthy or has insufficient data — caller
    treats both as "no alert for this metric")."""
    if len(baseline_values) < MIN_BASELINE_SAMPLES:
        return None
    if len(current_values) < MIN_CURRENT_SAMPLES:
        return None

    baseline = statistics.fmean(baseline_values)
    try:
        sigma = statistics.pstdev(baseline_values)
    except statistics.StatisticsError:
        sigma = 0.0
    sigma = max(sigma, MIN_SIGMA.get(metric, 0.0))
    if sigma <= 0:
        # Truly zero variance and no floor — can't compute deviation
        # without inventing one. Skip rather than alert.
        return None

    current = statistics.fmean(current_values)
    deviation = abs(current - baseline) / sigma

    if deviation < SIGMA_THRESHOLD:
        return None

    return {
        "metric": metric,
        "current_value": round(current, 4),
        "baseline": round(baseline, 4),
        "sigma": round(sigma, 4),
        "deviation_sigmas": round(deviation, 2),
        "threshold": SIGMA_THRESHOLD,
        "samples": {
            "baseline": len(baseline_values),
            "current": len(current_values),
        },
    }


class AnomalyDetectTool:
    """Z-score anomaly detector over recent deploy_health probes."""

    def schema(self) -> dict[str, Any]:
        return {
            "name": "anomaly_detect",
            "description": (
                "Flag deploy_health metrics whose CURRENT 5min mean "
                "deviates from the rolling 1hr baseline (excluding "
                "that same 5min) by more than ANOMALY_SIGMA_THRESHOLD "
                "(default 2σ). Returns {verdict, env, alerts[], "
                "summary}. Use BEFORE slo_check when you suspect "
                "the env is behaving oddly — anomaly_detect catches "
                "drift before it crosses a fixed SLO target. Returns "
                "INSUFFICIENT_DATA when the baseline has fewer than "
                f"{MIN_BASELINE_SAMPLES} samples — never alert from "
                "that state."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "env": {
                        "type": "string",
                        "description": (
                            "Environment to analyse: 'development', "
                            "'staging', 'production', or 'demo'."
                        ),
                    },
                    "deploy_id": {
                        "type": "string",
                        "description": (
                            "Optional — limit to one specific deploy."
                        ),
                    },
                    "metrics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional subset of metrics to evaluate. "
                            "Default: ['response_time_ms', "
                            "'error_rate_5m', 'restart_count']."
                        ),
                    },
                },
                "required": ["env"],
            },
        }

    def __init__(self, state: Any = None) -> None:
        self.state = state

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        env = (params.get("env") or "").strip()
        if not env:
            return {
                "verdict": "ERROR",
                "alerts": [],
                "summary": "Verdict: ERROR — env required.",
            }
        if self.state is None:
            return {
                "verdict": "ERROR",
                "alerts": [],
                "summary": "Verdict: ERROR — state store unavailable.",
            }

        deploy_id = params.get("deploy_id") or None
        metrics = params.get("metrics") or [
            "response_time_ms", "error_rate_5m", "restart_count",
        ]

        now = datetime.utcnow()
        baseline_since = now - timedelta(minutes=BASELINE_WINDOW_M)
        current_since = now - timedelta(minutes=CURRENT_WINDOW_M)

        probes = await self.state.list_deploy_health_probes(
            env=env, deploy_id=deploy_id,
            since=baseline_since, limit=5000,
        )

        # Split probes into baseline (older portion of the 1hr window)
        # and current (last 5min). A probe in the current window is
        # NOT also counted in baseline — that's the masking guard.
        baseline_probes = [
            p for p in probes if p.recorded_at < current_since
        ]
        current_probes = [
            p for p in probes if p.recorded_at >= current_since
        ]

        if len(baseline_probes) < MIN_BASELINE_SAMPLES:
            return {
                "verdict": "INSUFFICIENT_DATA",
                "env": env,
                "alerts": [],
                "summary": (
                    f"Verdict: INSUFFICIENT_DATA — only "
                    f"{len(baseline_probes)} baseline probe(s) for "
                    f"env={env}; need ≥{MIN_BASELINE_SAMPLES}."
                ),
                "baseline_samples": len(baseline_probes),
                "current_samples": len(current_probes),
            }

        alerts: list[dict[str, Any]] = []
        for metric in metrics:
            baseline_vals = [
                v for v in (_extract_metric(p, metric) for p in baseline_probes)
                if v is not None
            ]
            current_vals = [
                v for v in (_extract_metric(p, metric) for p in current_probes)
                if v is not None
            ]
            alert = _detect_one_metric(metric, baseline_vals, current_vals)
            if alert:
                alerts.append(alert)

        verdict = "ANOMALY" if alerts else "OK"

        if verdict == "OK":
            summary = (
                f"Verdict: OK — env={env}, "
                f"{len(baseline_probes)} baseline + "
                f"{len(current_probes)} current probe(s), "
                f"no metric exceeded ±{SIGMA_THRESHOLD}σ."
            )
        else:
            top = max(alerts, key=lambda a: a["deviation_sigmas"])
            summary = (
                f"Verdict: ANOMALY — env={env}, {len(alerts)} "
                f"metric(s) over ±{SIGMA_THRESHOLD}σ; worst: "
                f"{top['metric']} = {top['current_value']!r} vs "
                f"baseline {top['baseline']!r} "
                f"({top['deviation_sigmas']}σ)."
            )

        logger.info(
            "anomaly_detect_complete",
            env=env, verdict=verdict, alert_count=len(alerts),
            baseline_samples=len(baseline_probes),
            current_samples=len(current_probes),
        )

        return {
            "verdict": verdict,
            "env": env,
            "alerts": alerts,
            "summary": summary,
            "baseline_samples": len(baseline_probes),
            "current_samples": len(current_probes),
        }
