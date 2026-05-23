# PRD — Build Plan Decomposition (Epic → Feature → Task + Dependency DAG)

Deeper design doc for `docs/prd.md` §6.8. Captures the rationale, the
resolutions to the four open questions left in the parent PRD, the
schema design decisions, a fully-worked example using CrewAI's actual
finalized task list, the three-pass generation prompt skeletons, the
dispatch semantics with concrete walkthroughs, the test strategy, and
the decision log.

This document is **gate BPD-01** in `docs/task-list.md` — Phase B of
the BPD plan can't begin until the open questions here are resolved
and the schema rationale stands up to review.

---

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 1.0 |
| Created Date | 2026-05-22 |
| Status | Draft (awaiting review) |
| Parent PRD | [`docs/prd.md`](prd.md) §6.8 |
| Implementation Plan | [`docs/task-list.md`](task-list.md) BPD-01..45 |
| UI Mockup | [`docs/mockups/build-plan-decomposition.html`](mockups/build-plan-decomposition.html) |

## Table of Contents

1. [Why this exists — problem and evidence](#1-why-this-exists--problem-and-evidence)
2. [Open question resolutions](#2-open-question-resolutions)
3. [Schema rationale](#3-schema-rationale)
4. [Worked example — CrewAI's current 31 tasks → new hierarchy](#4-worked-example--crewais-current-31-tasks--new-hierarchy)
5. [Three-pass generation prompts (skeletons)](#5-three-pass-generation-prompts-skeletons)
6. [Dispatch semantics — three concrete walkthroughs](#6-dispatch-semantics--three-concrete-walkthroughs)
7. [Test strategy](#7-test-strategy)
8. [Implementation phasing reminder](#8-implementation-phasing-reminder)
9. [Decision log](#9-decision-log)

---

## 1. Why this exists — problem and evidence

### 1.1 The recurring failure pattern

Across May 2026, **the CrewAI project (`proj-05d5f57e`) produced four
production incidents in a row** that shared a common shape: an
individual "task" was too large for the agent to deliver in a single
response, and the failure modes cascaded badly.

| Task | What failed | Root cause | Lessons added |
|---|---|---|---|
| **T-6144cc94** — "Build CrewAI orchestrator service" | 4 dispatches, ~110 min total agent time, ~$80 of LLM cost, eventually succeeded | Single task asked agent to emit 8,260 LOC across 13+ files in one response — `max_tokens=8192` truncated every cycle | L15 (raised default to 32K) |
| **T-103e9025** — "Build projects list page" | 3 cycles, same drop-guard rejection re-emitted byte-identical | Single task asked agent to rewrite a 764-line file but agent kept emitting 275-line replacement | L17 (drop-guard loop detector) |
| **T-b4954195** — "Add frontend tests" | 3 cycles, UNIQUE constraint failed: test_cases.test_id | Tester re-emitted same TC-XXX IDs across cycles; backend INSERT raised | L18 (test_cases UPSERT) |
| **T-3e1303b3** — "Add backend tests" | Same UNIQUE constraint, same cycle pattern | Same backend bug | L18 |

Each individual fix (L14-L18 in `docs/agent-lessons-learned.md`) was
correct. But the pattern is structural: **today's "task" is the wrong
unit of execution** — it bundles 4-8 sub-tasks that the agent must
deliver in a single response, with no checkpoint and no parallelism.

### 1.2 The numbers behind the problem

Pulled from CrewAI's actual emission data:

| Task | LOC emitted | Files | Wall time (incl. retries) | Cost |
|---|---|---|---|---|
| T-6144cc94 (orchestrator) | ~8,260 | 13+ | 28 min × 4 attempts = ~110 min | ~$80 |
| T-103e9025 (projects UI) | 1,200+ in one file | 6 | 22 min × 3 attempts | ~$30 |
| T-9e7bda15 (schema migrations) | ~500 | 4 | 12 min × 1 attempt | ~$8 |
| Median task | ~400 | 3-5 | 6-10 min × 1 attempt | ~$3 |

The median task is fine — it's the long-tail tasks (top 20%) that
account for nearly all the production failures and ~70% of total cost.
Atomic decomposition specifically targets the long tail.

### 1.3 What "atomic" means

The contract for an **atomic task** is:

| Dimension | Atomic task target |
|---|---|
| Primary file | 1 (named explicitly in `primary_file`) |
| Additional files touched | ≤ 2 |
| Expected lines of code | 50-300 |
| Wall-clock per agent cycle | 2-3 minutes |
| Acceptance test | 1 sentence ("GET /X returns Y") |
| Failure blast radius | The atomic task only; siblings unaffected |

Today's tasks fail all six criteria simultaneously.

---

## 2. Open question resolutions

The parent PRD §6.8 left four open questions. This section resolves
each one, with the reasoning written down so we don't re-litigate
later.

### 2.1 Three approval gates default vs single mega-approval

**Resolved**: Three sequential approval gates **by default**, with an
opt-in "Approve all and dispatch" mega-button accessible per-project.

**Why default to three gates**:
1. Each level catches a different class of mistake. Epic-level catches
   "you forgot to plan for X". Feature-level catches "this epic should
   be 5 features, not 12". Task-level catches "this task is too
   big / has wrong dependencies".
2. Mistakes get cheaper to fix at higher levels. Rejecting an
   incorrect epic at gate 1 saves ~30 LLM calls for the features +
   tasks under it. Letting the same mistake propagate to gate 3
   wastes ~30 calls.
3. The user already has a 1-gate flow today (review the whole task
   list at finalize time) and reports it's hard to scan. Three smaller
   reviews are easier on attention.

**Why offer the mega-button**:
- Operator mode: after using the gated flow on 3-5 projects, the user
  knows the agent's decomposition is reliable for their domain. The
  mega-button lets them skip ceremony for projects they trust.
- Greenfield prototyping: "I just want to see what the agent thinks"
  is a real use case during exploration.

**The toggle**: per-project setting `decomposition_approval_mode`:
`step_by_step` (default) | `mega_approval`. Stored on `projects` row;
changeable any time.

### 2.2 Cross-epic dependencies — allowed or forbidden?

**Resolved**: Allowed, with a visual warning when the dependency
crosses an epic boundary.

**Why allow**:
- Real projects have unavoidable cross-cutting deps. "Add login button
  to header" (UI epic) genuinely depends on "POST /auth/login endpoint
  exists" (Auth epic). Forcing the agent to invent a synthetic shim
  to avoid the cross-epic edge would produce worse decompositions.
- CrewAI's actual graph (see §4) has 7 cross-epic edges out of ~150
  total dependencies. Removing them would either delay every UI
  feature until the backend epic completes (poor parallelism) or
  duplicate work (poor cleanliness).

**Why warn**:
- Cross-epic deps are a smell when over-used. If 40% of deps cross
  epics, the epic decomposition is wrong — probably should have been
  one larger epic. The warning is the user's prompt to consider that.
- The warning surfaces in the task popup as a small chip and in the
  Build Board with a dashed connecting line.

**Validation**: Cross-epic deps are validated at persist time (target
task_id must exist) but never rejected. Within-epic deps are validated
identically.

### 2.3 Auto-dispatch on by default?

**Resolved**: Off by default. Opt-in per project via
`auto_dispatch_on_deploy: bool` on the `projects` row.

**Why default to off**:
- Auto-dispatch fires LLM calls. A new user opening the platform
  shouldn't have 50 tasks start running automatically because they
  approved a decomposition. The cost surprise is real ($30-100 of
  cloud spend in 30 minutes).
- Per the existing platform philosophy (see §6.7 PRD on Project
  Management), all platform actions are explicit-trigger. Auto-fire
  on event arrival is the exception, not the default.

**Why offer as opt-in**:
- For projects the user is actively babysitting, watching the cascade
  unfold automatically is faster and lower-friction than clicking
  "Dispatch Ready" every 3 minutes. This is the "I'm building this
  feature today; let it run" mode.

**UX**: A clearly-labeled toggle on the Project Detail page's
Deployment panel, near the existing `deploy_judge_preferences`
toggle. Setting flips a flag; next dispatch decision honors it.
Audit log records every auto-fire decision with `from_event`,
`task_ids`, and reasoning.

### 2.4 Greenfield vs legacy migration tool

**Resolved**: V1 ships **legacy compatibility** (BPD-401/402/404) so
existing finalized task lists continue to work end-to-end. The
**decomposition migration tool** (BPD-39) is Medium priority and may
ship in a Phase F follow-up; not blocking V1.

**Why ship legacy compat**:
- CrewAI is mid-build with 31 finalized tasks. Breaking that
  workflow to migrate to the new format is hostile. Legacy compat
  costs ~3 lines (default `feature_id=NULL`, default
  `depends_on=[]`) and zero ongoing maintenance.

**Why defer the migration tool**:
- The tool is non-trivial — it has to run three-pass generation
  against the existing PRD and produce a diff for user approval.
  Estimated ~3-4 hours of work (BPD-39 is L effort).
- Live projects in flight don't need it. They can finish in legacy
  mode. New projects start in the new model.
- Users who want to migrate mid-flight can do it manually: archive
  the current list, regenerate from the PRD with the new flow.

---

## 3. Schema rationale

### 3.1 Why separate `epics` and `features` tables (not just columns)

Considered: add `epic_name TEXT` and `feature_name TEXT` directly to
`project_tasks` rather than introducing two new tables.

**Rejected** because:
1. Acceptance criteria are per-level. An epic's acceptance criterion
   ("user can authenticate end-to-end") is different from a feature's
   ("login form submits and creates a session") which is different
   from a task's ("POST /auth/login returns 200 with token"). Storing
   all three on each task row is denormalized chaos.
2. Status rollups are per-level. "Epic Auth is 3/5 features done" is
   not derivable from a flat `epic_name` column without a GROUP BY +
   custom logic at every read site.
3. Versioning is per-level. Regenerating features under one epic
   shouldn't bump the list_version on every task in every other epic.
   Separate tables make `list_version` scoped naturally.
4. Audit log + event emission make more sense at the epic / feature
   layer than at every task ("Epic Auth approved", not "T-x44
   approved AND T-x45 approved AND …").

Three tables (epics, features, project_tasks) is the right
normalization. The 6 LOC saved by collapsing them is not worth the
denormalization tax.

### 3.2 Why `depends_on` as JSON array (not separate join table)

Considered: introduce `task_dependencies(blocker_task_id,
dependent_task_id)` as a join table for SQL-native graph traversal.

**Rejected for V1** because:
1. The dependency count per task is small (avg 1-3 blockers from
   CrewAI's worked example). A JSON array is fine for that
   cardinality.
2. We never need to query "which tasks depend on T-x44?" — only
   "which tasks does T-x44 depend on?" The reverse lookup is rare
   enough to scan all tasks once per dispatch evaluation (cheap on
   a few hundred rows).
3. SQLite's JSON support (`json_each`) makes the few queries we DO
   need (find blockers of a task, find all tasks whose blockers are
   all in a set) one-liners without joins.
4. Cycle detection runs at persist time, not query time, so we don't
   need recursive CTEs.

**V2 consideration**: if we later need fast reverse lookups or
graph-shaped queries (e.g. "critical path analysis"), introduce
`task_dependencies` as a derived/materialized view from the JSON
arrays. Don't introduce it pre-emptively.

### 3.3 Why `primary_file` is TEXT (not a foreign key)

Considered: a separate `files` table with FK from `project_tasks`.

**Rejected** because:
- Files don't exist independently of the project — they're an artifact
  of agent emission. Storing them as TEXT keeps the schema agent-
  agnostic.
- The agent emits the file path as a string anyway. Round-tripping
  through a normalized table buys nothing.
- File renames / moves are rare in atomic-task land (each task touches
  one file). When they do happen, agent emits the new path and the
  text update is trivial.

### 3.4 Index strategy

```sql
-- epics
CREATE INDEX idx_epics_project_status ON epics(project_id, list_status);
CREATE INDEX idx_epics_project_version ON epics(project_id, list_version);

-- features
CREATE INDEX idx_features_epic ON features(epic_id);
CREATE INDEX idx_features_project ON features(project_id);

-- project_tasks (additional to existing)
CREATE INDEX idx_tasks_feature ON project_tasks(feature_id);
-- No index on depends_on JSON — we scan all tasks for a project anyway
-- when evaluating dispatchability. The scan is cheap on hundreds of rows.
```

Bench target: dispatchability evaluation for a 200-task project
(`SELECT * FROM project_tasks WHERE project_id=?` + Python-side
graph walk) runs under 50ms. Verified at BPD-08 implementation.

### 3.5 Migration strategy (additive only)

All schema changes are **additive**:

```sql
-- New tables (BPD-02, BPD-03)
CREATE TABLE IF NOT EXISTS epics (…);
CREATE TABLE IF NOT EXISTS features (…);

-- Additive columns on project_tasks (BPD-04)
ALTER TABLE project_tasks ADD COLUMN feature_id TEXT REFERENCES features(feature_id);
ALTER TABLE project_tasks ADD COLUMN depends_on TEXT DEFAULT '[]';
ALTER TABLE project_tasks ADD COLUMN primary_file TEXT;
ALTER TABLE project_tasks ADD COLUMN expected_loc INTEGER;
ALTER TABLE project_tasks ADD COLUMN acceptance_test TEXT;
```

Existing rows keep `feature_id=NULL, depends_on='[]'` (the defaults).
They render under a synthetic "Legacy" epic in the UI (BPD-402)
and dispatch unchanged (no blockers because `[]`).

No DROP, no RENAME, no data backfill required for V1. The migration
runs in <100ms on a populated DB.

---

## 4. Worked example — CrewAI's current 31 tasks → new hierarchy

### 4.1 Current state

CrewAI (`proj-05d5f57e`) has **31 finalized tasks across 11 phases**
(all currently `deployed`). The phases:

| # | Phase | Tasks |
|---|---|---|
| 1 | Foundation & Project Scaffolding | 3 |
| 2 | Database Layer | 3 |
| 3 | REST API | 4 |
| 4 | Crew Execution Engine | 4 |
| 5 | Frontend Foundation | 2 |
| 6 | Agents Management UI | 3 |
| 7 | Projects Management UI | 3 |
| 8 | Kanban Board UI | 3 |
| 9 | Dashboard UI | 1 |
| 10 | Run History UI | 1 |
| 11 | Quality & Polish | 4 |

Each task contains 6-10 sub-tasks in its description (the `**Sub-tasks:**`
markdown block). Total sub-task count across all 31 tasks: ~220.

### 4.2 Proposed structure under the new model

| Level | Count | Note |
|---|---|---|
| **Epics** | 11 | One per current phase (preserving the user's existing taxonomy) |
| **Features** | 31 | Today's tasks become features |
| **Atomic tasks** | ~180 | Today's sub-task bullets become individual task rows |

Rough math: 31 features × avg 6 sub-tasks = ~186 atomic tasks. Some
features will have fewer (3-4 for simple things like "Add health
endpoint"), some more (8-12 for complex things like the orchestrator
service). Total in the 150-200 range.

This sits comfortably in the PRD's stated targets:
- 5-12 epics → 11 ✓
- 3-8 features per epic → average 2.8, range 1-4 (a touch on the low
  end; some epics like "Run History UI" have only 1 feature today.
  Acceptable — the agent doesn't need to split a 1-feature epic
  further.)
- 5-15 tasks per feature → average 6, range 3-11 ✓

### 4.3 Detailed walkthrough — Epic 1 "Foundation & Project Scaffolding"

Today's Phase 1 has 3 tasks. Under the new model:

```
Epic E-001 · Foundation & Project Scaffolding
  Acceptance: "Backend, frontend, and Docker Compose dev environment
              all stand up cleanly with one `docker compose up -d`."

  ├── Feature F-001 · Initialize backend FastAPI project
  │   Acceptance: "uvicorn starts, GET /health returns 200, CORS allows :3000"
  │
  │     ├── T-001  Create backend/ directory structure (app/, app/api/,
  │     │         app/db/, app/services/, app/models/ + __init__.py files)
  │     │         primary_file: backend/app/__init__.py
  │     │         expected_loc: ~10  ·  depends_on: []
  │     │
  │     ├── T-002  Add backend/pyproject.toml pinning fastapi, uvicorn,
  │     │         pydantic, crewai, python-dotenv
  │     │         primary_file: backend/pyproject.toml
  │     │         expected_loc: ~30  ·  depends_on: [T-001]
  │     │
  │     ├── T-003  Create app/main.py with FastAPI(title='CrewAI Platform')
  │     │         + /health GET returning {"status":"ok"}
  │     │         primary_file: backend/app/main.py
  │     │         expected_loc: ~25  ·  depends_on: [T-001, T-002]
  │     │
  │     ├── T-004  Register CORS middleware allowing http://localhost:3000
  │     │         primary_file: backend/app/main.py
  │     │         expected_loc: ~10 (additive edit)
  │     │         depends_on: [T-003]
  │     │
  │     ├── T-005  Mount app/api/v1 router prefix
  │     │         primary_file: backend/app/main.py
  │     │         expected_loc: ~8 (additive edit)
  │     │         depends_on: [T-003]
  │     │
  │     ├── T-006  Add .env.example with API key placeholders
  │     │         primary_file: backend/.env.example
  │     │         expected_loc: ~6  ·  depends_on: []
  │     │
  │     └── T-007  Add health endpoint smoke test
  │               primary_file: backend/tests/test_health.py
  │               expected_loc: ~25
  │               depends_on: [T-003, T-004, T-005]
  │               acceptance_test: "uvicorn starts, GET /health returns
  │                                 200 OK with body {"status":"ok"}
  │                                 and CORS headers present"
  │
  ├── Feature F-002 · Initialize frontend React + Vite project
  │   Acceptance: "vite dev server starts, renders 'Hello CrewAI'
  │              at :3000, hot-reload works on edit"
  │   (~6 atomic tasks, similar shape)
  │
  └── Feature F-003 · Configure Docker Compose for dev environment
      Acceptance: "`docker compose up -d` brings up backend on :8000
                 and frontend on :3000, both healthy"
      depends_on (feature-level): [F-001, F-002]
      (~4 atomic tasks)
```

**Total in Epic 1**: 3 features, ~17 atomic tasks.

### 4.4 Dependency graph for Epic 1

Text-form adjacency (lower task → higher task means "depends on"):

```
F-001 internal DAG:

  T-001 ────┬─→ T-002 ─→ T-003 ─┬─→ T-004 ──┐
            │                    │           ├─→ T-007
            │                    └─→ T-005 ──┤
            │                                │
            │                                │
   T-006 ───┴───────────────────────────────┘   (parallel-startable)


Cross-feature edges:

  F-001 (all 7 tasks deployed)  ┐
                                 ├─→ F-003 (Docker compose)
  F-002 (all 6 tasks deployed)  ┘
```

Parallelism analysis for Epic 1:
- **Wave 0 (kicks off immediately)**: T-001, T-006, all of F-002's
  no-dep tasks (6 tasks fire in parallel)
- **Wave 1 (after T-001)**: T-002
- **Wave 2 (after T-002 + T-001)**: T-003
- **Wave 3 (after T-003)**: T-004 + T-005 parallel
- **Wave 4 (after T-003, T-004, T-005)**: T-007
- **Wave 5 (after F-001 + F-002 all deployed)**: F-003's tasks

If each task takes ~2 min, the critical path through this epic is
~10 min (5 waves) vs today's serial execution of the 3 mega-tasks at
~10 min each = 30 min. About 3× faster on this epic alone, with the
huge bonus that any single-task failure doesn't kill the others.

### 4.5 Summary table — all 11 epics

| Epic | Today's tasks | Features (proposed) | Atomic tasks (estimated) |
|---|---|---|---|
| E-001 Foundation & Project Scaffolding | 3 | 3 | ~17 |
| E-002 Database Layer | 3 | 3 | ~16 |
| E-003 REST API | 4 | 4 | ~26 |
| E-004 Crew Execution Engine | 4 | 4 | ~30 (includes the 11-task orchestrator feature) |
| E-005 Frontend Foundation | 2 | 2 | ~12 |
| E-006 Agents Management UI | 3 | 3 | ~16 |
| E-007 Projects Management UI | 3 | 3 | ~18 |
| E-008 Kanban Board UI | 3 | 3 | ~14 |
| E-009 Dashboard UI | 1 | 1 | ~7 |
| E-010 Run History UI | 1 | 1 | ~6 |
| E-011 Quality & Polish | 4 | 4 | ~18 |
| **Total** | **31** | **31** | **~180** |

### 4.6 What this would have changed about T-6144cc94's death

The killer task — **"Build CrewAI orchestrator service"** — has 10
sub-task bullets in its description today. Under the new model it
becomes Feature F-013 with 11 atomic tasks:

```
Feature F-013 · CrewAI orchestrator service
  ├── T-040  Create crew_orchestrator.py skeleton with run_project entry point
  │         primary_file: backend/app/services/crew_orchestrator.py  ·  ~50 LOC  ·  no deps
  ├── T-041  Add tools.py with TOOL_REGISTRY dict (file_read, file_write, web_search, code_exec)
  │         primary_file: backend/app/services/tools.py  ·  ~60 LOC  ·  no deps
  ├── T-042  Add log_scrub.py with scrub_secrets() regex
  │         primary_file: backend/app/services/log_scrub.py  ·  ~30 LOC  ·  no deps
  ├── T-043  Read API keys from env (FR-009)
  │         primary_file: backend/app/services/crew_orchestrator.py  ·  ~20 LOC  ·  depends_on: [T-040]
  ├── T-044  Add build_crewai_agents() loading from DB
  │         ~80 LOC  ·  depends_on: [T-040, T-041]
  ├── T-045  Build BA task constructor
  │         ~40 LOC  ·  depends_on: [T-044]
  ├── T-046  Construct Solution Architect + Scrum Master follow-up tasks → Crew + Process.sequential
  │         ~80 LOC  ·  depends_on: [T-045]
  ├── T-047  Parse crew run output → tasks table insertion
  │         ~100 LOC  ·  depends_on: [T-046]
  ├── T-048  Persist task_outputs rows (FR-014)
  │         ~80 LOC  ·  depends_on: [T-046]
  ├── T-049  Apply scrub_secrets() to all output before logging
  │         ~40 LOC  ·  depends_on: [T-042, T-047, T-048]
  └── T-050  Stub crewai.LLM + smoke test for run_project()
            primary_file: backend/tests/services/test_crew_orchestrator.py
            ~120 LOC  ·  depends_on: [T-040 through T-049]
            acceptance_test: "run_project() called with seeded project →
                              tasks table has new rows, task_outputs has
                              ≥1 row per agent step, no API keys in logs"
```

**Total**: 11 atomic tasks × ~60 LOC avg = 660 LOC (vs 8,260 emitted
in today's monolith — about 1/12th the per-task scope).

**Predicted outcome vs actual outcome**:

| Metric | Actual (today's model) | Predicted (atomic model) |
|---|---|---|
| Dispatches required | 4 | 1 |
| LLM cost | ~$80 | ~$8 |
| Wall-clock (with parallelism) | ~110 min | ~12 min |
| Cycles before success | 7 across 4 dispatches | 0-1 per atomic task |
| Per-task failure blast radius | All 13 files | The one task's primary_file |

The L15-L18 lessons (token cap, drop-guard loop, UPSERT) would all
still apply in the new model as defense-in-depth, but the failures
they catch would be much rarer because no individual task would emit
800+ LOC.

---

## 5. Three-pass generation prompts (skeletons)

Final prompt wording is BPD-10/BPD-12/BPD-14. These are the
shape-defining outlines — they show the inputs, outputs, and key
constraints each pass must enforce.

### 5.1 Pass 1 — PRD → Epics

```
INPUT:
  - Finalized PRD (full text)
  - Optional: finalized API spec
  - Optional: review_comments (regeneration)
  - Optional: previous epic list (for regeneration with comments —
    preserve unaffected epics word-for-word)

OUTPUT (JSON array, 5-12 entries):
  [
    {
      "title": "<≤80 chars, format: '<Epic theme>'>",
      "description": "<1-2 paragraphs: what user-facing value this epic delivers>",
      "acceptance_criteria": "<one sentence: when is the entire epic done?>"
    },
    ...
  ]

CONSTRAINTS:
  - Each epic must be a coherent user-facing capability, not an
    implementation layer. "Authentication" is good; "Database access
    layer" is bad (split across many user-facing epics).
  - Epics are unordered at this level (ordering emerges from
    feature-level + task-level dependencies in passes 2 + 3).
  - "Foundation" or "Project setup" IS allowed as an epic if the
    project has substantial setup not specific to one user feature.
```

### 5.2 Pass 2 — Epic → Features

```
INPUT:
  - One epic's {title, description, acceptance_criteria}
  - Sibling epic titles (so this pass doesn't duplicate work that
    belongs in a different epic)
  - PRD (referenced, in case the epic description elided detail)
  - Optional: review_comments
  - Optional: previous feature list under this epic

OUTPUT (JSON array, 1-8 entries):
  [
    {
      "title": "<≤80 chars, format: '<Verb> <noun>' e.g. 'Login flow'>",
      "description": "<1 paragraph: what user-facing behavior>",
      "acceptance_criteria": "<one sentence>",
      "depends_on_features": ["<other feature_title in same project>", ...]
    },
    ...
  ]

CONSTRAINTS:
  - Each feature must be independently testable (you can demo it in
    isolation without the rest of the epic done).
  - Feature-level deps reference feature TITLES; the system maps titles
    → feature_ids on persist (titles must be unique within project).
  - Cross-epic feature deps allowed; emit warning if used.
  - 1-feature epics are valid (don't force a split).
```

### 5.3 Pass 3 — Feature → Atomic Tasks

```
INPUT:
  - One feature's {title, description, acceptance_criteria}
  - Sibling features under same epic + parent epic title
  - PRD reference + API spec reference (if any)
  - Optional: review_comments
  - Optional: previous task list under this feature

OUTPUT (JSON array, 3-15 entries):
  [
    {
      "title": "<imperative, ≤80 chars: 'Create X', 'Add Y to Z'>",
      "description": "<≤4 lines — the prompt a junior dev would read>",
      "primary_file": "<one file path, e.g. 'backend/app/api/v1/dashboard.py'>",
      "expected_loc": <integer, typical 50-300>,
      "acceptance_test": "<one sentence>",
      "depends_on": [<task indices in this same array, 0-based>],
      "task_type": "feature_request" | "bug_report" | "doc_request"
                 | "demo_request" | "research_request" | "content_request",
      "priority": "low" | "medium" | "high",
      "estimated_agent": "backend_specialist" | "frontend_specialist"
                       | "tester_specialist" | "code_reviewer"
                       | "devops_specialist" | "content_creator"
                       | "research_specialist"
    },
    ...
  ]

CONSTRAINTS:
  - HARD: expected_loc < 30 OR > 500 → tasks parsed but warning surfaced
    to the user (likely too small / too big — over-decomposition or
    under-decomposition signal).
  - HARD: primary_file MUST be present.
  - HARD: acceptance_test MUST be a single sentence (≤200 chars).
  - depends_on within this feature uses array indices; cross-feature
    deps use "feature:<feature_title>:<task_index>" syntax.
  - System maps both forms → task_ids on persist.
  - Cycle detection at persist time per BPD-005.
```

---

## 6. Dispatch semantics — three concrete walkthroughs

### 6.1 Walkthrough A — Single-task dispatch with deps unmet

User clicks "Dispatch" on **T-007 (Add health endpoint smoke test)**
while T-003 and T-004 are still in `backlog` status.

```
POST /api/v1/projects/proj-x/build/dispatch
  body: {"task_ids": ["T-007"]}

→ Server:
  1. Look up T-007
  2. Read T-007.depends_on = ["T-003", "T-004", "T-005"]
  3. Read status of each: T-003=backlog, T-004=backlog, T-005=backlog
  4. 0 of 3 deployed → BLOCKED
  5. Return:
     HTTP 409
     {
       "error": "dependencies_unmet",
       "blockers": [
         {"task_id": "T-003", "title": "Create app/main.py with /health", "status": "backlog"},
         {"task_id": "T-004", "title": "Register CORS middleware",         "status": "backlog"},
         {"task_id": "T-005", "title": "Mount /api/v1 router prefix",      "status": "backlog"}
       ],
       "hint": "Dispatch the blockers first, or use POST /build/dispatch-feature/F-001 to fire all unblocked tasks in this feature."
     }

→ UI:
  - Render error chip on the T-007 row: "Blocked by 3 tasks"
  - Click chip → opens task popup with the depends_on chips highlighted
```

### 6.2 Walkthrough B — "Dispatch All Ready" cascade

User clicks "Dispatch All Ready" on a project where the only `backlog`
tasks are T-001 through T-007 (Feature F-001).

```
POST /api/v1/projects/proj-x/build/dispatch-all-ready

→ Server:
  1. Pull all backlog tasks for project
  2. For each: check if all depends_on are deployed
     - T-001 deps: []                       → DISPATCHABLE
     - T-002 deps: [T-001]                  → blocked (T-001 not yet deployed)
     - T-003 deps: [T-001, T-002]           → blocked
     - T-004 deps: [T-003]                  → blocked
     - T-005 deps: [T-003]                  → blocked
     - T-006 deps: []                       → DISPATCHABLE
     - T-007 deps: [T-003, T-004, T-005]    → blocked
  3. Dispatchable set: {T-001, T-006}
  4. Submit requests for those 2 tasks
  5. Return:
     HTTP 202
     {
       "dispatched": [
         {"task_id": "T-001", "request_id": "REQ-xxxx", "status": "dispatched"},
         {"task_id": "T-006", "request_id": "REQ-yyyy", "status": "dispatched"}
       ],
       "blocked": 5,
       "blocked_summary": {"by_dep_count": {"1": 2, "2": 2, "3": 1}}
     }

→ Background: as T-001 deploys, event handler (BPD-205) re-evaluates.
  If auto_dispatch_on_deploy=true for this project:
    - T-001 deployed at 14:32 → T-002 becomes dispatchable → auto-fire
    - T-002 deployed at 14:35 → T-003 becomes dispatchable → auto-fire
    - T-003 deployed at 14:38 → T-004 + T-005 dispatchable → auto-fire (parallel)
    - T-006 was dispatched at 14:30 (no deps), deployed at 14:33
    - T-004 deployed at 14:41, T-005 deployed at 14:41 → T-007 dispatchable → auto-fire
    - T-007 deployed at 14:44

  Total wall-clock: 12 min for 7 tasks (vs ~21 min serial)
```

### 6.3 Walkthrough C — Cross-epic dependency in action

T-040 (in Feature F-013, Epic E-004 "Crew Execution Engine") depends
on T-027 (in Feature F-008 "Project CRUD", Epic E-003 "REST API")
because the orchestrator needs the project CRUD endpoint to exist
before it can fetch project metadata.

```
T-040 depends_on: ["T-027"]  ← cross-epic dep

UI rendering:
  - T-040's task popup shows the depends_on chip with a small "↗" icon
    indicating cross-epic
  - Hovering the chip: "T-027 lives in Epic E-003 · REST API · Feature F-008 · Project CRUD"
  - Click jumps to T-027's popup
  - Build Board: T-040's card shows a small "🔗 cross-epic" tooltip

Dispatch behavior: identical to within-epic dep.
  T-027 deployed → T-040 becomes dispatchable.
```

---

## 7. Test strategy

### 7.1 Unit-level (Phase A — BPD-09)

| Component | Tests |
|---|---|
| StateStore CRUD: epics | create + get + list + finalize + archive + delete; list_version monotonic per project |
| StateStore CRUD: features | same shape, scoped to epic; archive epic cascades to features under it |
| Dependency-graph helpers (BPD-08) | `get_blockers(task_id)` returns expected set; `get_dispatchable_tasks(project_id)` handles 0/N/all deployed cases; `has_cycle()` finds cycles in tiny + huge graphs; `has_cycle()` returns False on legitimately-deep DAGs (no false positives) |
| Persist-time validators | cycle rejection raises `422 dag_cycle_detected`; cross-feature dep to nonexistent task raises `422 dangling_dep`; valid graphs pass |

### 7.2 Pass-by-pass parser tests (Phase B — BPD-19)

| Pass | Tests |
|---|---|
| Pass 1 | Parse a known-good epic list JSON; parse with missing optional fields; parse malformed JSON (fallback to markdown if implemented); regen-with-comments preserves unchanged epics |
| Pass 2 | Same shape, plus: feature `depends_on_features` resolves to feature_ids; cross-epic dep emits warning chip; sibling-feature-title context appears in prompt |
| Pass 3 | Atomic-task parser: indices → task_ids; cross-feature `feature:X:Y` syntax resolves; expected_loc < 30 emits warning; primary_file missing rejects; cycle detection rejects |

### 7.3 End-to-end smoke (Phase E — BPD-41, BPD-42)

| Scenario | Acceptance |
|---|---|
| **Fresh project, three-pass generation** | Create new project → finalize PRD → run all three passes (no batch endpoint, manual approval each) → dispatch one feature → verify deps enforce → enable auto-dispatch → verify cascade runs → all tasks deployed |
| **Legacy compatibility** | CrewAI's 31 existing tasks dispatch identically to before — no `depends_on` enforcement (because `depends_on='[]'` by default) means today's parallel-fire behavior is preserved |
| **Migration tool** (BPD-39, deferred) | Run decompose-legacy on CrewAI → diff view shows proposed Epic→Feature→Task structure matching §4.5 of this doc → accept → existing tasks unchanged, new hierarchy alongside |

---

## 8. Implementation phasing reminder

```
BPD-01 (THIS DOC)
   │
   ▼
Phase A · Schema + foundation (BPD-02..09)
   │     [critical path; nothing else can start]
   ▼
Phase B · Three-pass generation (BPD-10..19)
   │     [critical path; UI needs these endpoints]
   ▼
Phase C · Dispatch engine (BPD-20..28)  ┬──────┐
                                         │      │
                                         ▼      ▼
                                   Phase D · UI rework (BPD-29..37)
                                         │      │
                                         ▼      ▼
                                   Phase E · Migration + polish (BPD-38..45)
```

Phase D and Phase C can have some parallelism once the endpoints
are signature-stable (mid-Phase-C). Phase E is mostly verification
and can begin as soon as enough of D is buildable.

**Critical path estimate** (no parallelism): A (2h) + B (3h) + C (2h)
+ D (3h) + E (1.5h) = **~11.5 hours**.

**With one developer + parallel D/E**: ~9 hours.

---

## 9. Decision log

| # | Decision | Date | Rationale | Where it shows up |
|---|---|---|---|---|
| 1 | Three-level hierarchy (Epic → Feature → Task) rather than two | 2026-05-22 | Three review gates; matches agile vocabulary; per-level acceptance criteria | §3.1, all of §4 |
| 2 | Three-pass generation, one LLM call per level | 2026-05-22 | Bounded prompt size per pass; checkpoint for user; per-level regen-with-comments | §5 |
| 3 | `depends_on` as JSON array (not join table) | 2026-05-22 | Cardinality is low; SQLite `json_each` is enough; no recursive query needed yet | §3.2 |
| 4 | Atomic task size target: 50-300 LOC, 1 primary file | 2026-05-22 | Matches L15 token budget headroom (32K); failure blast radius bounded to one file | §1.3 |
| 5 | Three approval gates default; mega-button opt-in | 2026-05-22 | Catches mistakes early when they're cheap; mega for power users | §2.1 |
| 6 | Cross-epic deps allowed with warning | 2026-05-22 | Real projects need them; warning catches over-use | §2.2 |
| 7 | Auto-dispatch default OFF | 2026-05-22 | Cost surprise prevention; matches platform's explicit-trigger philosophy | §2.3 |
| 8 | Legacy compat in V1; migration tool deferred | 2026-05-22 | CrewAI mid-build; legacy mode is ~3 lines of code; migration tool is L effort | §2.4 |
| 9 | Additive-only schema migration | 2026-05-22 | Zero risk of data loss; rollback by feature-flag if needed | §3.5 |
| 10 | Cycle detection at persist time, not query time | 2026-05-22 | Cheaper; cycles are rare; query time stays fast | §3.4 |

This doc resolves all open questions and freezes the design surface
for Phases A through E. Reopen this doc before starting work on any
phase if any of the above decisions feel wrong.
