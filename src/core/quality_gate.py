"""Quality gate event emission helper — Phase AE-3 (AET-05).

Centralizes the construction of `quality.gate.failed` / `quality.gate.passed`
event payloads so the workflow runner (AET-06) and any future callers can
emit consistent events without re-deriving the payload shape.

The payload shape is the contract between this module, the EventEmitter
broadcaster, the WebSocket handlers, and the frontend UI surfaces (AET-07).
Changes to the shape ripple to all of those — keep this module the single
authoritative definition (L21 lesson: single source of truth for cross-layer
strings + structures).

Why this lives in its own module rather than as inline code in the
workflow runner:
  - testable in isolation without spinning up the workflow runner
  - the helper is also useful from outside the runner (e.g. an admin
    endpoint that re-evaluates the gate against historical emissions)
  - keeps the runner's gate code short — runner reads structured verdict
    → calls one helper → moves on
"""

from __future__ import annotations

from typing import Any

import structlog

from src.core.events import (
    QUALITY_GATE_FAILED,
    QUALITY_GATE_PASSED,
    EventEmitter,
)

logger = structlog.get_logger()


# Set of valid verdict strings the policy_check tool returns. Mirrors
# the strings produced by src/tools/policy_check.py::execute() —
# updating one without the other would silently miss events.
_VALID_VERDICTS = {"BLOCK", "PASS_WITH_WARNINGS", "PASS"}


def _resolve_event_type(verdict: str) -> str:
    """Verdict → event_type mapping.

    BLOCK              → quality.gate.failed (workflow halts)
    PASS_WITH_WARNINGS → quality.gate.passed (workflow advances with warnings)
    PASS               → quality.gate.passed (workflow advances cleanly)

    The inner `verdict` field on the payload disambiguates clean-pass
    from warned-pass for subscribers that care (UI: green chip vs
    yellow chip). Workflow runners that only care "did the gate halt
    or not" can switch on the event_type alone.
    """
    return QUALITY_GATE_FAILED if verdict == "BLOCK" else QUALITY_GATE_PASSED


def build_quality_gate_payload(
    *,
    request_id: str,
    verdict: str,
    violations: list[dict[str, Any]] | None = None,
    summary: dict[str, Any] | None = None,
    stage: str | None = None,
    rework_cycle: int | None = None,
) -> dict[str, Any]:
    """Build the canonical event payload.

    Parameters mirror what `policy_check.execute()` returns so the
    caller's code is short:

        result = await policy_check.execute({"emissions": [...]})
        payload = build_quality_gate_payload(
            request_id=request_id,
            verdict=result["verdict"],
            violations=result["violations"],
            summary=result["summary"],
            stage="code_review.quality_check",
            rework_cycle=current_cycle,
        )

    `stage` and `rework_cycle` are optional context — useful for the UI
    to show "blocked at cycle 2 of code_review.quality_check stage"
    instead of just "blocked".

    Returns a dict matching the documented contract:

      {
        "request_id":   str,
        "verdict":      "BLOCK" | "PASS_WITH_WARNINGS" | "PASS",
        "violations":   list of {rule_id, rule_name, severity,
                                 target_path, agent_id, snippet,
                                 rationale, fix_hint, lesson_ref},
        "summary":      {enforce_count, warn_count, info_count,
                         total_emissions_checked, total_violations},
        "stage":        optional workflow stage identifier,
        "rework_cycle": optional cycle index (0-based),
      }
    """
    if verdict not in _VALID_VERDICTS:
        # Don't silently swallow — a typo in the caller would make the
        # UI show "blocked" forever. Loud failure follows L11/L21.
        raise ValueError(
            f"quality_gate.invalid_verdict: {verdict!r} "
            f"(expected one of {sorted(_VALID_VERDICTS)})"
        )
    payload: dict[str, Any] = {
        "request_id": request_id,
        "verdict": verdict,
        "violations": list(violations or []),
        "summary": dict(summary or {}),
    }
    if stage is not None:
        payload["stage"] = stage
    if rework_cycle is not None:
        payload["rework_cycle"] = rework_cycle
    return payload


async def emit_quality_gate_event(
    events: EventEmitter,
    *,
    request_id: str,
    verdict: str,
    violations: list[dict[str, Any]] | None = None,
    summary: dict[str, Any] | None = None,
    stage: str | None = None,
    rework_cycle: int | None = None,
) -> dict[str, Any]:
    """Build the payload + emit the right event_type + audit-log it.

    Returns the payload for callers that want to attach it to a
    structured response (e.g. the workflow runner can include it in
    the stage's `quality_report` output).

    The audit-log entry uses ``logger.info`` with the rule_ids that
    fired so a log scrape (`grep quality_gate_decision logs/`) can
    reconstruct the gate history without joining the events table.
    """
    payload = build_quality_gate_payload(
        request_id=request_id,
        verdict=verdict,
        violations=violations,
        summary=summary,
        stage=stage,
        rework_cycle=rework_cycle,
    )
    event_type = _resolve_event_type(verdict)
    await events.emit(event_type, payload)

    # Audit-log entry — separate from the event broadcast so it survives
    # WebSocket subscriber failures and goes through the structlog
    # pipeline (visible to log scrapers + supervisor monitoring).
    rule_ids = [v.get("rule_id") for v in payload["violations"] if v.get("rule_id")]
    logger.info(
        "quality_gate_decision",
        request_id=request_id,
        verdict=verdict,
        event_type=event_type,
        stage=stage,
        rework_cycle=rework_cycle,
        enforce_count=payload["summary"].get("enforce_count", 0),
        warn_count=payload["summary"].get("warn_count", 0),
        rule_ids=rule_ids,
    )
    return payload
