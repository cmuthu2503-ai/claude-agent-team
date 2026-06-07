# Architecture Reviewer Audit · AET-33

## Document Information

| Field | Value |
|---|---|
| Author | Phase AE-5 (AET-33) |
| Date | 2026-05-28 |
| Status | Complete |
| Source | `subtasks` table, `agent_id = 'architecture_reviewer'`, all 5 historical rows |
| Goal | Classify each historical output as **useful block** / **false positive** / **missed issue** so AET-34 can sharpen the system prompt against actual failure modes. |

## 1 · Method

Queried the dev SQLite (`/app/data/agent_team.db`) for every `architecture_reviewer` subtask row, retrieved the full `output_text`, and graded each one against three axes:

1. **Verdict accuracy** — was the APPROVED / ARCH_VIOLATION call correct given the underlying code change?
2. **Rule-application fidelity** — did the agent actually run each of its 6 rules, or did it skip / guess?
3. **Output-cost efficiency** — was the report length proportional to the change being reviewed?

Classification labels (per the AET-33 spec):

| Label | Meaning |
|---|---|
| **useful block** | Correctly raised an ARCH_VIOLATION on a real architectural problem |
| **false positive** | Raised an ARCH_VIOLATION on something that wasn't actually a violation |
| **missed issue** | Approved code that had an unflagged architectural problem |
| **clean approve** | Correctly returned APPROVED with no violations to find (counts as a "useful no-block") |
| **verbose approve** | Correctly APPROVED, but emitted heavyweight prose for a trivial change |

## 2 · Audit Corpus

| # | Subtask ID | Request | Status | Output (chars) | Classification | Verdict |
|---|---|---|---|---|---|---|
| 1 | `REQ-52A7B4-ARCHITECTURE_REVIEWER-f373` | REQ-52A7B4 | completed | 2,721 | verbose approve | APPROVED |
| 2 | `REQ-52A7B4-ARCHITECTURE_REVIEWER-4292` | REQ-52A7B4 | completed | 2,228 | verbose approve | APPROVED |
| 3 | `REQ-52A7B4-ARCHITECTURE_REVIEWER-c194` | REQ-52A7B4 | completed | 52 | clean approve (efficient) | APPROVED |
| 4 | `REQ-60C0AD-ARCHITECTURE_REVIEWER-4ebf` | REQ-60C0AD | completed | 1,663 | clean approve | APPROVED |
| 5 | `REQ-60C0AD-ARCHITECTURE_REVIEWER-f680` | REQ-60C0AD | completed | 703 | clean approve | APPROVED |

**Score (verdict precision):**

| Metric | Count | Notes |
|---|---|---|
| Correct APPROVED | 5 / 5 | Every approval matched ground truth |
| False positives | 0 / 5 | No block was raised on a non-violation |
| Missed issues | 0 / 5 | No corpus row had an unflagged real violation |
| Useful blocks | 0 / 5 | Corpus contained no real arch violations to catch — so this is a **null measurement**, not a deficit |

## 3 · Per-subtask findings

### #1 — `…f373` · REQ-52A7B4 · infrastructure cycle (initial)

**Change reviewed:** `backend/Dockerfile` + `backend/.dockerignore` + root `.dockerignore` + README — purely container build definitions, no Python source.

**Strengths:**
- Explicitly verified files on disk before ruling (applied L22 — "READ MAIN.PY AND APP.TSX BEFORE RULING").
- Correctly noted *vacuous pass* on rules 1, 2, 3, 5 because no Python or React was touched.
- Surfaced a positive sanity check ("Dockerfile entrypoint `app.main:app` matches the real FastAPI app") even though not strictly required — adds operator confidence at modest cost.

**Weakness:**
- **Verbosity disproportionate to the change.** 2,721 chars of prose for "infrastructure files only — nothing to flag." A leaner template (the Rules Checked table + one-line verdict) would have been ~600 chars.

### #2 — `…4292` · REQ-52A7B4 · infrastructure cycle (rework, +1 line)

**Change reviewed:** Single new line `python-dotenv>=1.0,<2.0` added to `backend/requirements.txt`.

**Strengths:**
- Verified file on disk byte-by-byte ("`python-dotenv>=1.0,<2.0` is line 4").
- Cross-checked that the new dep satisfied the existing `from dotenv import load_dotenv` import in `main.py`.

**Weakness:**
- **Same over-engineering pattern as #1.** A 1-line dependency add does not warrant 2,228 chars of "Rules Checked" tabulation. Lesson #3 ("nothing to review cycles") catches the *zero-change* case but not the *trivial-change-that-touches-no-source* case.

### #3 — `…c194` · REQ-52A7B4 · genuinely no-op cycle

**Change reviewed:** Nothing — rework cycle with no new code.

**Strengths:**
- Perfectly applied lesson #3: emitted the one-line `"No new code to review — verdict unchanged: APPROVED."` and stopped. 52 chars total. ✅
- This is the gold-standard efficient path the other infrastructure subtasks should converge to.

**Weakness:** none.

### #4 — `…4ebf` · REQ-60C0AD · frontend component add

**Change reviewed:** New `HealthStatus` React component imported and used inline in `App.tsx`.

**Strengths:**
- **Correctly distinguished page-vs-component for rule 3** — explicitly said "HealthStatus is an inline UI component (not a page), so no `<Route>` entry required." This is precisely the kind of false-positive trap rule 3 invites; the agent navigated it correctly.
- Verified existing route table was intact (`/`, `/projects`, `/projects/:id`, `/agents`, `*`).
- Confirmed no circular imports between `HealthStatus.tsx` and `App.tsx`.

**Weakness:** moderate verbosity (1,663 chars) — could trim the per-rule "no change → pass" rows.

### #5 — `…f680` · REQ-60C0AD · frontend rework re-confirmation

**Change reviewed:** Same frontend change post-rework.

**Strengths:**
- Concise table-only output (703 chars) — closer to the desired template.
- Correctly inherited the previous cycle's analysis of the page-vs-component distinction.

**Weakness:** none significant.

## 4 · Patterns the audit surfaces

### 4.1 No false-positive blocks in the corpus

The agent never raised an ARCH_VIOLATION on something that wasn't a real violation. This is the most important property — false positives waste rework cycles and erode trust in the gate.

### 4.2 No missed issues in the corpus

None of the 5 subtasks had an actual architectural violation in the underlying code. This is a **null measurement** — the corpus is too small (and too biased toward infrastructure/frontend changes) to establish a recall rate. AET-36 should explicitly include synthetic "should-have-blocked" cases to measure recall.

### 4.3 Over-verbose approvals on trivial changes

| Cycle type | Count | Avg output size | Ideal |
|---|---|---|---|
| Genuine no-op (no files changed) | 1 | 52 chars | ≤100 chars |
| Trivial infrastructure-only (1-line dep add, dockerfile only) | 2 | 2,475 chars | ≤600 chars |
| Frontend component add (real review needed) | 1 | 1,663 chars | 1,000-1,500 chars |
| Re-confirmation cycle | 1 | 703 chars | 200-500 chars |

**Conclusion:** lesson #3 in the system prompt covers the zero-change case but not the *trivial-change* case. Two of the five corpus rows wasted ~4 KB of tokens producing ceremonial Rules Checked tables for changes that couldn't possibly affect any of the six architectural rules.

### 4.4 Page-vs-component distinction is a known false-positive trap

Subtask #4 navigated this correctly, but only because the agent reasoned about it explicitly in its scratchpad. The system prompt's rule 3 doesn't currently call out the trap — the agent would have been forgiven for raising a false flag. Codifying the distinction would harden the prompt against future regressions.

## 5 · Recommendations for AET-34 (prompt tuning)

| # | Recommendation | Rationale | Expected impact |
|---|---|---|---|
| **R1** | Add a "TRIVIAL-CHANGE FAST PATH" lesson: when the rework set contains only `*.txt`, `Dockerfile`, `.dockerignore`, `README.md`, lock files, or other non-source files, emit the Rules Checked table with all 6 marked "Pass — non-source change" and one-line verdict. Skip the prose. | Halves token spend on infrastructure cycles (#1, #2) | Token spend / cycle ≥40% reduction |
| **R2** | Strengthen rule 3 with an explicit "page vs component" definition: a "page" is any file under `frontend/src/pages/*.tsx`; everything else is a component and DOES NOT require a `<Route>` entry. | Codifies the heuristic subtask #4 used implicitly; protects against regression | Prevents false-positive blocks |
| **R3** | Tighten the "ARCH_VIOLATION" definition: only rules 1, 2, 3 produce CRITICAL findings (block-worthy). Rules 4, 5, 6 produce HIGH findings (annotate, do not block) UNLESS they form a chain (e.g. circular import that breaks the build → CRITICAL). | Matches the verdict-precision data — rules 1-3 are the "structural integrity" core; 4-6 are quality signals. AET-35's `arch_review_block_severity` threshold codifies this in YAML. | Reduces over-blocking on Pydantic/import nits |
| **R4** | Add an explicit "DO NOT review the file you are reviewing's CONTENT for logic correctness" callout citing examples ("don't comment on variable names, function length, or test coverage"). The current prompt says this once at the bottom; surfacing it in the lessons section makes it stickier. | Already followed in corpus, but cheap insurance for future drift | Defensive |
| **R5** | Add an anti-pattern list mirroring the prompt's "rule definition" style: "**Do NOT flag:** comments above an `import`, type aliases that look like Pydantic models, route handlers under `src/api/routes/*/internal/` (test fixtures)." | Pre-empts categories the prompt hasn't seen yet | Defensive |

## 6 · Inputs for AET-36 smoke test

AET-36's smoke test should include both replayed audit-corpus cases AND new synthetic ones to measure recall (which the corpus alone can't establish):

| Case ID | Type | Expected verdict | Pulled from |
|---|---|---|---|
| ACR-01 | Pure no-op cycle | one-line APPROVED | Subtask #3 (`…c194`) |
| ACR-02 | Infrastructure-only (Dockerfile + requirements.txt) | APPROVED, ≤600 chars (post-R1) | Subtask #1/#2 (`…f373` / `…4292`) |
| ACR-03 | Frontend component add (not a page) | APPROVED, no false-flag on rule 3 | Subtask #4 (`…4ebf`) |
| ACR-04 (NEW) | Page added under `frontend/src/pages/` WITHOUT `<Route>` entry in `App.tsx` | ARCH_VIOLATION on rule 3 | synthetic recall probe |
| ACR-05 (NEW) | Route file under `src/api/routes/` imports `aiosqlite` directly | ARCH_VIOLATION on rule 1 (CRITICAL — bypasses StateStore) | synthetic recall probe |
| ACR-06 (NEW) | Pydantic model uses `@validator` (v1) | HIGH finding only, APPROVED (per R3) | synthetic — confirms AET-35 threshold |

## 7 · Summary

- **Precision (corpus):** 5 / 5 verdicts correct, 0 false positives. The agent already does the *blocking* judgment right.
- **Recall (corpus):** unmeasurable from this data — AET-36 must add synthetic recall probes.
- **Efficiency:** mixed — one cycle was gold-standard (52 chars), two were over-engineered (~2.5 KB each), two were middling.
- **Net direction for AET-34:** trim verbosity on trivial changes, codify the page-vs-component rule, and split CRITICAL vs HIGH along rules-1-3 vs rules-4-6 lines so AET-35's threshold has something to grip on.
