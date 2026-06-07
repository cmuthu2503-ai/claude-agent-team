# Agent Lessons Learned — Pending Review Queue

Auto-generated lessons from the `self_learning_agent` land here for human
review before being promoted into the canonical `agent-lessons-learned.md`
that all code-writing agents read on every invocation.

**Why a review gate (AET-13):**
- The self-learning agent is enthusiastic. Without a human in the loop,
  noisy or low-signal lessons would bloat the system prompt budget of
  every code-writing agent forever.
- Approval is one-click: `POST /api/v1/lessons/{lesson_id}/approve`
  moves the block to the canonical doc. `…/reject` drops it.
- Dedup runs against BOTH this file and the canonical doc so the same
  pattern doesn't queue up twice.

**Format:** identical to canonical lessons. Each entry is bracketed by
HTML comment markers so the API endpoints can find/remove individual
blocks unambiguously:

```
<!-- pending-lesson:start id=L<NN> request_id=REQ-XXX created=<ISO8601> -->
## L<NN> — <title>
**Signature:** `…`
**Cause:** …
**Fix:** …
**Observed in:** REQ-XXX (YYYY-MM-DD)
<!-- pending-lesson:end id=L<NN> -->
```

The markers are the source of truth for the approve/reject endpoints —
do NOT hand-edit them, and do NOT remove this header.

---

_(queue empty)_
