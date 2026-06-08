"""Approval-gate observability signals (HAI-62 / FR-083).

A small, queryable health snapshot of the gate so an operator (or Hermes itself)
can see at a glance: how deep the approval backlog is, how often proposals expire
unactioned (a sign humans aren't keeping up / the TTL is wrong), and how active the
service-token identities are. Pure read — derived from the proposals + service_tokens
tables, no new bookkeeping.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

# A proposal is "decided" once it reaches a terminal outcome. expired counts as a
# NON-action outcome (nobody confirmed/rejected in time); the rate of expired over
# all decided proposals is the headline "are we keeping up?" signal.
_TERMINAL_DECIDED = ("executed", "failed", "rejected", "expired")

_DEFAULT_RECENT_WINDOW_SECONDS = 86400  # 24h


async def build_gate_metrics(
    state: Any,
    *,
    now: datetime | None = None,
    recent_window_seconds: int = _DEFAULT_RECENT_WINDOW_SECONDS,
) -> dict[str, Any]:
    """Compute the gate's observability signals. ``now`` is injectable for tests."""
    now = now or datetime.utcnow()

    counts = await state.proposal_status_counts()
    pending = counts.get("pending", 0)
    expired = counts.get("expired", 0)
    decided = sum(counts.get(s, 0) for s in _TERMINAL_DECIDED)
    expired_rate = round(expired / decided, 4) if decided else 0.0

    # Service-token activity — a proxy for "how much is Hermes doing". We don't keep
    # a per-call counter, so we report live token count + how many were used in the
    # recent window (last_used_at, debounced by HAI-52).
    try:
        tokens = await state.list_service_tokens()
    except Exception:  # noqa: BLE001 — metrics must never raise
        tokens = []
    cutoff = now - timedelta(seconds=recent_window_seconds)
    active = sum(1 for t in tokens if not t.is_revoked)
    revoked = sum(1 for t in tokens if t.is_revoked)
    recently_used = sum(1 for t in tokens if t.last_used_at and t.last_used_at >= cutoff)

    return {
        "pending_backlog_depth": pending,
        "expired_without_action_rate": expired_rate,
        "proposals_by_status": {
            s: counts.get(s, 0)
            for s in ("pending", "confirmed", "executed", "failed", "rejected", "expired")
        },
        "proposals_total": sum(counts.values()),
        "service_tokens": {
            "active": active,
            "revoked": revoked,
            "recently_used": recently_used,
            "window_seconds": recent_window_seconds,
        },
        "ts": now.isoformat(),
    }
