"""KB-26 — episodic-memory consolidation job (the anti-rot mechanism).

Episodic memory (``agent_memory``, KB-24) accrues one raw row per finished
Request. Left alone it becomes an unsearchable landfill. This job periodically,
per ``mem_project_<id>`` namespace:

1. **Summarize + expire.** Raw episodes older than ``after_days`` are folded
   into one compact ``kind='summary'`` row, then the raw episodes are deleted.
   The summary keeps the gist (counts by outcome, a few recent goals) at a
   fraction of the rows.

2. **Detect + propose (never auto-promote).** Recurring patterns across those
   episodes — e.g. the same goal failing repeatedly — are written to
   ``kb_promotion_candidates`` as ``pending`` proposals. The single controlled
   doorway from unvetted memory into the citeable KB is the human review gate
   (KB-28); this job only *surfaces* candidates, it never promotes.

Design choices:
- **Deterministic, no LLM.** Summary + signature are pure string ops so the job
  is fast, free, and testable without a model. An LLM-written summary can layer
  on later behind the same interface.
- **Idempotent.** The summary row and each proposal carry a ``content_hash`` so
  a re-run over the same window doesn't duplicate anything.
- **Soft-fail.** Wrapped per-namespace; one app's bad data never stalls the rest
  or crashes the loop.

``consolidate_namespace`` is the unit-testable core; ``make_consolidation_job``
wraps it in the same periodic-asyncio-task shape as the AET-31 anomaly sweeper,
wired from ``src/main.py``'s lifespan.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import uuid
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

from src.knowledge.models import AgentMemory

logger = structlog.get_logger()

# Tunables (env-overridable, like the AET-31 sweeper's constants).
CONSOLIDATION_INTERVAL_S = int(os.getenv("KB_CONSOLIDATION_INTERVAL_S", str(6 * 3600)))
CONSOLIDATION_AFTER_DAYS = int(os.getenv("KB_CONSOLIDATION_AFTER_DAYS", "30"))
CONSOLIDATION_MIN_EPISODES = int(os.getenv("KB_CONSOLIDATION_MIN_EPISODES", "5"))
PATTERN_MIN_OCCURRENCES = int(os.getenv("KB_PATTERN_MIN_OCCURRENCES", "3"))

_GOAL_RE = re.compile(r"^Goal:\s*(.+)$", re.MULTILINE)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "with",
    "build", "add", "fix", "make", "create", "update", "request", "feature",
    "task", "it", "this", "that",
})


@dataclass
class ConsolidationResult:
    namespace: str
    consolidated: int = 0      # raw episodes folded into a summary
    summary_id: str | None = None
    proposals: int = 0         # new promotion candidates created
    skipped: bool = False      # below threshold → nothing to do


def _goal_of(text: str) -> str:
    m = _GOAL_RE.search(text or "")
    return m.group(1).strip() if m else (text or "").strip()[:120]


def _signature(outcome: str, goal: str) -> str:
    """Coarse pattern key: outcome + the salient goal tokens (stopwords +
    request ids dropped, deduped, sorted). Two episodes with the same intent
    and result collapse to the same signature."""
    toks = [t for t in _TOKEN_RE.findall(goal.lower()) if t not in _STOPWORDS and len(t) > 2]
    # Drop request-id-ish tokens (e.g. 'req', long hex) to avoid per-request keys.
    toks = [t for t in toks if not (t == "req" or re.fullmatch(r"[0-9a-f]{6,}", t))]
    salient = sorted(set(toks))[:8]
    return f"{outcome}:{' '.join(salient)}"


def _build_summary(namespace: str, episodes: list[dict[str, Any]]) -> str:
    outcomes = Counter(e.get("outcome") or "unknown" for e in episodes)
    recent_goals = [_goal_of(e.get("text", "")) for e in episodes[-5:]]
    parts = [
        f"Episodic summary for {namespace}: {len(episodes)} episode(s) consolidated.",
        "Outcomes: " + ", ".join(f"{k}×{v}" for k, v in outcomes.most_common()),
    ]
    goals = [g for g in recent_goals if g]
    if goals:
        parts.append("Recent goals: " + "; ".join(goals))
    return "\n".join(parts)


def _detect_patterns(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group episodes by (outcome, goal-signature); any group recurring at least
    ``PATTERN_MIN_OCCURRENCES`` times is a promotion candidate."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in episodes:
        sig = _signature(e.get("outcome") or "unknown", _goal_of(e.get("text", "")))
        groups[sig].append(e)
    out: list[dict[str, Any]] = []
    for sig, members in groups.items():
        if len(members) < PATTERN_MIN_OCCURRENCES:
            continue
        outcome = members[0].get("outcome") or "unknown"
        goal = _goal_of(members[0].get("text", ""))
        out.append({
            "signature": sig,
            "summary": (
                f"Recurring pattern ({len(members)}×, outcome={outcome}): "
                f"\"{goal}\". Consider promoting a vetted lesson to the KB."
            ),
            "evidence_ids": [m["memory_id"] for m in members],
            "occurrences": len(members),
        })
    return out


async def consolidate_namespace(
    store: Any, namespace: str, *, after_days: int = CONSOLIDATION_AFTER_DAYS,
    min_episodes: int = CONSOLIDATION_MIN_EPISODES, embedder: Any = None,
    project_id: str | None = None,
) -> ConsolidationResult:
    """Consolidate one memory namespace. Returns a ``ConsolidationResult``;
    soft-fails are the caller's concern (``make_consolidation_job`` wraps)."""
    episodes = await store.list_episodes_older_than(namespace, days=after_days)
    if len(episodes) < min_episodes:
        return ConsolidationResult(namespace=namespace, skipped=True)

    pid = project_id or (episodes[0].get("project_id") if episodes else None)

    # 1. Detect + propose recurring patterns BEFORE expiring the raw rows
    #    (the proposals cite the episode ids as evidence).
    proposals = 0
    for pat in _detect_patterns(episodes):
        chash = hashlib.sha256(f"{namespace}|{pat['signature']}".encode()).hexdigest()
        created = await store.create_promotion_candidate(
            namespace=namespace, project_id=pid, kind="pattern",
            summary=pat["summary"], evidence_ids=pat["evidence_ids"],
            occurrences=pat["occurrences"], content_hash=chash,
        )
        if created:
            proposals += 1

    # 2. Summarize the window into one kind='summary' row.
    summary_text = _build_summary(namespace, episodes)
    ep_ids = sorted(e["memory_id"] for e in episodes)
    summary_hash = hashlib.sha256(
        ("summary|" + namespace + "|" + "|".join(ep_ids)).encode()
    ).hexdigest()
    embedding = None
    if embedder is not None:
        try:
            res = await embedder.embed_documents([summary_text])
            if res and res.vectors:
                embedding = list(res.vectors[0])
        except Exception as e:  # noqa: BLE001
            logger.warning("kb_consolidation_embed_failed", error=str(e))
    summary_id = await store.insert_memory(AgentMemory(
        memory_id=f"mem-{uuid.uuid4().hex[:12]}", namespace=namespace,
        agent_id="consolidation", kind="summary", text=summary_text,
        outcome="partial", project_id=pid, embedding=embedding,
        content_hash=summary_hash, unvetted=True,
    ))

    # 3. Expire the raw episodes now that they're folded into the summary.
    removed = await store.delete_memories(ep_ids)

    logger.info(
        "kb_consolidated", namespace=namespace, consolidated=removed,
        summary_id=summary_id, proposals=proposals,
    )
    return ConsolidationResult(
        namespace=namespace, consolidated=removed, summary_id=summary_id,
        proposals=proposals,
    )


async def consolidate_all(subsystem: Any) -> list[ConsolidationResult]:
    """Run consolidation across every memory namespace that has episodes.
    Per-namespace soft-fail so one bad app doesn't stall the rest."""
    store = subsystem.knowledge_store
    embedder = getattr(subsystem, "embedder", None)
    results: list[ConsolidationResult] = []
    namespaces = await store.distinct_memory_namespaces("episode")
    for ns in namespaces:
        try:
            results.append(await consolidate_namespace(store, ns, embedder=embedder))
        except Exception as e:  # noqa: BLE001
            logger.warning("kb_consolidation_namespace_failed", namespace=ns, error=str(e))
    return results


def make_consolidation_job(subsystem: Any) -> Callable[[], Awaitable[None]]:
    """Return the periodic background task callable (wrapped in
    ``asyncio.create_task`` by the lifespan). No-ops cleanly when the KB
    subsystem is unavailable."""

    async def _loop() -> None:
        # Stagger the first run so boot isn't competing with ingest.
        await asyncio.sleep(min(120, CONSOLIDATION_INTERVAL_S))
        while True:
            if subsystem is not None and getattr(subsystem, "available", False):
                try:
                    res = await consolidate_all(subsystem)
                    folded = sum(r.consolidated for r in res)
                    proposed = sum(r.proposals for r in res)
                    if folded or proposed:
                        logger.info(
                            "kb_consolidation_pass", namespaces=len(res),
                            folded=folded, proposed=proposed,
                        )
                except Exception as e:  # noqa: BLE001
                    logger.warning("kb_consolidation_loop_error", error=str(e))
            await asyncio.sleep(CONSOLIDATION_INTERVAL_S)

    return _loop
