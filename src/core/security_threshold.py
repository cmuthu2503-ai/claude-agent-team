"""Single source of truth for the AE-4 security gate's severity cutoff.

AET-20 defines ``security_max_severity_to_block`` in
``config/thresholds.yaml``. This module is the ONLY place that
interprets it. The pen_test_simple / sast_scan / dependency_audit /
secret_scan tools all emit findings with a ``severity`` string drawn
from the unified scale below; AET-21's workflow gate calls
``is_blocking()`` once per finding to decide BLOCK vs warn.

Centralising the ordering here avoids the L23 cross-layer drift bug
(four tools + one gate + one UI badge all keeping their own copy of
"is high worse than medium?" was the original sin we're paying off).
"""

from __future__ import annotations

from typing import Any

# Worst → best. Index = rank; lower index = more severe.
# Anything not in this list ranks as "info" (the lowest) so an unknown
# severity never accidentally triggers BLOCK.
SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]
_RANK = {s: i for i, s in enumerate(SEVERITY_ORDER)}

# Default — used when thresholds.yaml is missing or malformed. Matches
# the YAML default so behaviour is identical with/without a config file.
DEFAULT_MAX_SEVERITY = "high"


def _rank(severity: str) -> int:
    """Lower = more severe. Unknown severities sink to info."""
    return _RANK.get((severity or "").lower(), _RANK["info"])


def get_max_severity(thresholds: dict[str, Any] | None) -> str:
    """Pull ``security_max_severity_to_block`` out of the parsed
    thresholds.yaml dict (``ConfigLoader.thresholds``). Tolerant of
    missing/typo'd keys — returns ``DEFAULT_MAX_SEVERITY`` rather than
    raising so a botched config can't disable the security gate
    silently AND can't crash the workflow runner."""
    if not thresholds:
        return DEFAULT_MAX_SEVERITY
    entry = (thresholds.get("thresholds") or {}).get(
        "security_max_severity_to_block",
    )
    if not isinstance(entry, dict):
        return DEFAULT_MAX_SEVERITY
    value = (entry.get("value") or "").lower()
    if value not in _RANK:
        return DEFAULT_MAX_SEVERITY
    return value


def is_blocking(finding_severity: str, max_severity: str) -> bool:
    """True iff the finding's severity is AT OR ABOVE the configured
    cutoff (i.e. equal or worse). E.g. with max='high':
        critical → True, high → True, medium → False, low → False.
    Empty/unknown severities never block."""
    if not finding_severity:
        return False
    return _rank(finding_severity) <= _rank(max_severity)


def split_findings(
    findings: list[dict[str, Any]], max_severity: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convenience for the gate: split a list of findings into
    ``(blocking, non_blocking)`` per the configured cutoff. Findings
    keep their original order in each bucket."""
    blocking: list[dict[str, Any]] = []
    non_blocking: list[dict[str, Any]] = []
    for f in findings:
        if is_blocking(str(f.get("severity", "")), max_severity):
            blocking.append(f)
        else:
            non_blocking.append(f)
    return blocking, non_blocking


# ── AET-35 — architecture review threshold ───────────────────────────────


# Default arch cutoff = 'critical'. Matches the architecture_reviewer's
# verdict logic (CRITICAL → ARCH_VIOLATION; HIGH → annotate-only).
ARCH_DEFAULT_SEVERITY = "critical"


def get_arch_block_severity(thresholds: dict[str, Any] | None) -> str:
    """Pull ``arch_review_block_severity`` out of thresholds.yaml.
    Tolerant of missing config — falls back to ``ARCH_DEFAULT_SEVERITY``.
    Restricted to {'critical', 'high'} because there are no arch
    findings below HIGH; anything else falls back to the default."""
    if not thresholds:
        return ARCH_DEFAULT_SEVERITY
    entry = (thresholds.get("thresholds") or {}).get(
        "arch_review_block_severity",
    )
    if not isinstance(entry, dict):
        return ARCH_DEFAULT_SEVERITY
    value = (entry.get("value") or "").lower()
    if value not in ("critical", "high"):
        return ARCH_DEFAULT_SEVERITY
    return value
