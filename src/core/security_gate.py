"""AET-21 — structured security gate evaluator.

The security_specialist agent runs four focused tools (secret_scan,
sast_scan, dependency_audit, pen_test_simple) and emits a markdown
report. To make the workflow gate deterministic (and NOT LLM
self-judgment), the agent embeds a JSON-fenced block at the end of
its report::

    ```security-report-json
    {
      "findings": [
        {"rule_id": "B602", "severity": "high", "tool": "sast_scan",
         "file": "src/x.py", "line": 12,
         "message": "...", "fix_hint": "..."},
        ...
      ],
      "by_tool": {
        "secret_scan":      {"verdict": "PASS",  "finding_count": 0},
        "sast_scan":        {"verdict": "BLOCK", "finding_count": 3},
        "dependency_audit": {"verdict": "PASS",  "finding_count": 0},
        "pen_test_simple":  {"verdict": "SKIPPED"}
      }
    }
    ```

``parse_security_report()`` lifts the JSON out of the prose. The gate
then runs the findings through ``security_threshold.split_findings``
using the cutoff loaded from ``thresholds.yaml`` (AET-20). Verdict is
mechanical: any blocking finding → ``BLOCK``; otherwise PASS, with
sub-threshold findings annotated so the agent's next rework cycle
still sees the medium/low signal.

Backward compat: when the agent didn't emit the JSON fence (older
prompt, or rework loop where the model regressed to prose), the
caller falls back to the legacy text-parse in the runner (preserved
verbatim). The fallback path logs ``security_report_no_json_block``
so an operator can spot drift.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from src.core.events import SECURITY_GATE_FAILED, SECURITY_GATE_PASSED
from src.core.security_threshold import (
    DEFAULT_MAX_SEVERITY,
    split_findings,
)

logger = structlog.get_logger()


# Fence accepts either the explicit ``security-report-json`` tag or a
# bare ```json fence as a tolerant fallback. Multiline + DOTALL so the
# JSON body can span line breaks.
_JSON_FENCE_RE = re.compile(
    r"```(?:security-report-json|json)\s*\n(?P<body>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


def parse_security_report(report_text: str) -> dict[str, Any] | None:
    """Lift the JSON block out of a security_specialist markdown report.

    Returns the parsed dict, or None when the block is missing/malformed.
    Tolerates a leading ```json fence (some agents drop the specific
    tag); prefers the explicit ``security-report-json`` fence when both
    are present so a generic example JSON elsewhere in the prose doesn't
    win over the canonical block."""
    if not report_text:
        return None
    # Prefer the explicit fence — scan once for it; fall back to the
    # bare json fence only if the explicit one is missing. The explicit
    # tag is what the agent prompt asks for.
    explicit = re.search(
        r"```security-report-json\s*\n(?P<body>.*?)\n```",
        report_text, re.DOTALL | re.IGNORECASE,
    )
    if explicit:
        body = explicit.group("body")
    else:
        bare = _JSON_FENCE_RE.search(report_text)
        if not bare:
            logger.info(
                "security_report_no_json_block",
                hint="agent emitted prose only; falling back to text-parse path",
                preview=report_text[:200],
            )
            return None
        body = bare.group("body")
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        logger.warning(
            "security_report_json_parse_failed",
            error=str(e), preview=body[:200],
        )
        return None
    if not isinstance(data, dict):
        return None
    return data


def evaluate_security_report(
    report_text: str, max_severity: str = DEFAULT_MAX_SEVERITY,
) -> dict[str, Any] | None:
    """Run a parsed security_report through the AET-20 threshold and
    return a structured gate decision dict::

        {
          "verdict":         "BLOCK" | "PASS",
          "blocking":        [<finding>, …],
          "non_blocking":    [<finding>, …],
          "by_tool":         {<tool>: {"verdict": …, "finding_count": …}, …},
          "max_severity":    "<configured cutoff>",
          "summary":         "<one-line human-readable verdict>",
        }

    Returns None when the report has no JSON block — caller falls back
    to the legacy prose-parse path.
    """
    parsed = parse_security_report(report_text)
    if parsed is None:
        return None

    findings = parsed.get("findings") or []
    if not isinstance(findings, list):
        findings = []

    blocking, non_blocking = split_findings(findings, max_severity)
    verdict = "BLOCK" if blocking else "PASS"

    by_tool = parsed.get("by_tool") or {}
    if not isinstance(by_tool, dict):
        by_tool = {}

    if verdict == "PASS":
        summary = (
            f"Security gate PASS — {len(findings)} finding(s) total, "
            f"{len(blocking)} at/above '{max_severity}'."
        )
    else:
        first = blocking[0]
        summary = (
            f"Security gate BLOCK — {len(blocking)} finding(s) at/above "
            f"'{max_severity}'; first: "
            f"{first.get('tool', '?')}/{first.get('rule_id', '?')} "
            f"at {first.get('file', '?')}:{first.get('line', '?')}"
        )

    return {
        "verdict": verdict,
        "blocking": blocking,
        "non_blocking": non_blocking,
        "by_tool": by_tool,
        "max_severity": max_severity,
        "summary": summary,
    }


def build_security_gate_payload(
    request_id: str, decision: dict[str, Any],
) -> dict[str, Any]:
    """Shape the broadcast payload for SECURITY_GATE_PASSED/FAILED. UI
    consumers (the AET-22 chip + the Request detail page) read these
    fields directly so the keys are stable and intentional. Mirrors
    quality_gate.py::build_quality_gate_payload structure."""
    return {
        "request_id": request_id,
        "verdict": decision.get("verdict"),
        "max_severity": decision.get("max_severity"),
        "blocking": decision.get("blocking", []),
        "blocking_count": len(decision.get("blocking", [])),
        "non_blocking_count": len(decision.get("non_blocking", [])),
        "by_tool": decision.get("by_tool", {}),
        "summary": decision.get("summary", ""),
    }


async def emit_security_gate_event(
    events: Any, request_id: str, decision: dict[str, Any],
) -> None:
    """Fire the matching SECURITY_GATE_* event with a structured payload.
    Wrapped so callers don't have to remember the verdict→event mapping
    (L23 cross-layer label drift defence)."""
    if events is None:
        return
    event_type = (
        SECURITY_GATE_FAILED
        if decision.get("verdict") == "BLOCK"
        else SECURITY_GATE_PASSED
    )
    payload = build_security_gate_payload(request_id, decision)
    try:
        await events.emit(event_type, payload)
    except Exception as e:  # noqa: BLE001
        # Audit-only fallback — never let a failed broadcast crash the
        # workflow runner.
        logger.warning(
            "security_gate_event_emit_failed",
            event_type=event_type, request_id=request_id, error=str(e),
        )
