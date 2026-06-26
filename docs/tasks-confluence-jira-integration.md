# Confluence & JIRA Integration — Implementation Tasks

> **Project**: Agent Team Platform (`claude-agent-team`)
> **PRD**: [`docs/prd-confluence-jira-integration.md`](prd-confluence-jira-integration.md) (v2.0)
> **Version**: 2.0
> **Created**: June 26, 2026
> **Status**: Ready for Implementation
> **Supersedes**: tasks v1.0 — re-sequenced around the v2.0 PRD corrections

---

## Table of Contents

1. [Overview](#overview)
2. [Progress Summary](#progress-summary)
3. [Implementation Rules](#implementation-rules)
4. [Requirement → Task Traceability](#requirement--task-traceability)
5. [Phases](#phases)
   - [Phase 0: Verify Assumptions (do this FIRST)](#phase-0-verify-assumptions-do-this-first)
   - [Phase 1: Dependencies, Config, Markdown-Conversion Spike](#phase-1-dependencies-config-markdown-conversion-spike)
   - [Phase 2: Model & Store (integration_mappings)](#phase-2-model--store-integration_mappings)
   - [Phase 3: Confluence Client](#phase-3-confluence-client)
   - [Phase 4: JIRA Client](#phase-4-jira-client)
   - [Phase 5: Integration Publisher + Event Handler](#phase-5-integration-publisher--event-handler)
   - [Phase 6: New Finalize Events (Epic/Feature)](#phase-6-new-finalize-events-epicfeature)
   - [Phase 7: Observability + Retry Endpoints](#phase-7-observability--retry-endpoints)
   - [Phase 8: Configuration & Secrets Wiring](#phase-8-configuration--secrets-wiring)
   - [Phase 9: Testing & Docker Verification](#phase-9-testing--docker-verification)
   - [Phase 10: Docs & Polish](#phase-10-docs--polish)
   - [Phase V2 (deferred): JIRA Status Sync](#phase-v2-deferred-jira-status-sync)

---

## Overview

Add **one-way publishing** of finalized artifacts to Confluence (PRD, API Spec) and JIRA
(Epic→Story→Sub-task tree) for the Agent Team Platform. The whole integration runs off the
existing **EventEmitter handler bus** (`events.on(...)`, `src/main.py:289`), soft-fails like
`write_finalized_prd()` / `_push_finalized_doc_to_repo()`, and is **idempotent** via mappings
keyed on stable ids.

**Three corrections from v1.0 are baked into the sequencing** (see PRD §0):
1. Mappings key on `project_id` (PRD/API-Spec) and `epic_id`/`feature_id`/`task_id` — **never
   `artifact_id`** (regenerated per regen → would duplicate).
2. The JIRA tree is built **at task-list finalize** (`project.tasks_finalized` already
   emits), not on dispatch.
3. Status-sync depends on an event that doesn't exist; it is **deferred to v2** behind a
   one-line emit fix.

---

## Progress Summary

| Phase | Tasks | Status |
|-------|-------|--------|
| Phase 0: Verify Assumptions | T0.1–T0.3 | Pending |
| Phase 1: Deps, Config, Conversion Spike | T1.1–T1.4 | Pending |
| Phase 2: Model & Store | T2.1–T2.4 | Pending |
| Phase 3: Confluence Client | T3.1–T3.4 | Pending |
| Phase 4: JIRA Client | T4.1–T4.6 | Pending |
| Phase 5: Publisher + Handler | T5.1–T5.6 | Pending |
| Phase 6: New Finalize Events | T6.1–T6.2 | Pending |
| Phase 7: Observability + Retry | T7.1–T7.2 | Pending |
| Phase 8: Config & Secrets Wiring | T8.1–T8.2 | Pending |
| Phase 9: Testing & Docker Verify | T9.1–T9.5 | Pending |
| Phase 10: Docs & Polish | T10.1–T10.2 | Pending |
| Phase V2: Status Sync (deferred) | TV2.1–TV2.3 | Deferred |

---

## Implementation Rules

1. **All new code in `src/integrations/`** + one new model file + one new route file. Touch
   existing files only at the documented wire points.
2. **Soft-fail always** — return result dicts shaped like `HostWriteResult.as_dict()`
   (`{ok, ..., error}`), never raise out of the event handler.
3. **Async-wrap the sync Atlassian client** — every external call via `asyncio.to_thread()`.
4. **No tokens in logs** — read via `read_secret()`; log the source name, never the value.
5. **TDD** — write the test alongside each unit; respect the repo coverage gate.
6. **Restart after `src/` edits** — `docker compose restart backend` (per CLAUDE.md), then
   re-run the verify step. State lives in SQLite so it survives restart.
7. **Conventional commits**, one per task (e.g. `feat(integrations): add IntegrationMapping model`).

---

## Requirement → Task Traceability

| PRD Requirement | Task(s) |
|-----------------|---------|
| CONF-R1..R3 (space/home/idempotent) | T3.2, T5.2 |
| CONF-R4..R7 (PRD publish, version-less title, conversion) | T1.4, T3.3, T5.2 |
| CONF-R8..R10 (API Spec publish) | T5.3 |
| CONF-R11..R12 (meta, skip-when-disabled) | T5.1, T5.2 |
| JIRA-R1..R3a (project setup, field discovery) | T4.2, T4.3 |
| JIRA-R4..R7a (Epic) | T4.4, T5.4, T6.1 |
| JIRA-R8..R10 (Story) | T4.5, T5.4, T6.2 |
| JIRA-R11..R14 (Sub-task tree at finalize) | T4.6, T5.5 |
| JIRA-R15..R16 (dependency links + reconciliation) | T4.6, T5.5 |
| JIRA-R17 (meta) | T5.1, T5.5 |
| CFG-R1..R4 (secrets/config) | T1.2, T8.1, T8.2 |
| ERR-R1..R7 (resilience) | T3.1, T4.1, T5.6 |
| LIFE-R1..R4 (lifecycle) | T5.6, T6.x, T10.2 |
| NFR-P3/P4 (event-bus async, throttle) | T5.1, T5.5 |
| NFR-S1..S4 (security) | T1.2, T8.1, T9.4 |
| NFR-R3 (survives restart) | T2.2, T9.5 |
| OBS-R1/R2 (status + retry) | T7.1, T7.2 |
| Phase-0 emit fix (C1, enables v2) | T0.3, TV2.1 |

---

## Phases

### Phase 0: Verify Assumptions (do this FIRST)

> The whole point of v2.0 is that v1.0 built on false facts. Re-verify against the live tree
> before writing code — the line numbers below are from this review and may drift.

#### Task 0.1: Confirm finalize events & the missing ones

**Verify (read-only):**
- `search_files(pattern='emit\\("project\\.', path='src/api/routes/projects.py')` shows
  `project.prd_finalized` (~L1906), `project.api_spec_finalized` (~L2332),
  `project.tasks_finalized` (~L3100) — and **no** epic/feature finalize emit.
- `finalize_epic_endpoint` (~L3956) and `finalize_feature_endpoint` (~L4010) call
  `finalize_*_subtree` + `_sync_build_plan_to_disk` and emit nothing.
- **Done when:** you've confirmed exactly which events exist. If they've moved, update
  Phase 6 line refs.

#### Task 0.2: Confirm artifact-id volatility (the C2 fix justification)

**Verify:**
- `generate_prd`/PRD-mint path (~`projects.py:1691`) sets
  `artifact_id=f"art-{uuid.uuid4().hex[:12]}"` and `next_version` bumps each regen.
- **Done when:** you've confirmed a re-finalize after refine yields a different
  `artifact_id`. This is why §7.2 keys PRD/API-Spec mappings on `project_id`, not
  `artifact_id`.

#### Task 0.3: Confirm `set_task_status` emits nothing (the C1 / v2 boundary)

**Verify:**
- `set_task_status` (`sqlite_store.py:2918`) is a bare `UPDATE ... ; commit` — no emit.
- `project_task_status.py:79` calls it directly; `auto_dispatch.py:155,194` emits only the
  **batch** `project.tasks.auto_dispatched` / `project.tasks.dispatch_proposed`.
- **Done when:** confirmed. This justifies deferring status-sync to v2 (Phase V2) — do NOT
  build it on the batch dispatch events.

---

### Phase 1: Dependencies, Config, Markdown-Conversion Spike

#### Task 1.1: Add `atlassian-python-api` dependency

- Add `"atlassian-python-api>=3.41.0"` to `[project] dependencies` in `pyproject.toml`
  (after `httpx>=0.27.0`, ~L23). `markdown` is already present (`markdown-3.10.2` in `.venv`).
- Run `uv sync`.
- **Test:** `uv run python -c "from atlassian import Confluence, Jira; print('OK')"` exits 0.

#### Task 1.2: Add the `integrations` config section

Append to `config/project.yaml`:

```yaml
integrations:
  confluence:
    enabled: false
    url: ""                      # https://your-domain.atlassian.net/wiki
    email: ""
    space_key: ""                # single parent space (CONF-R1) — pages nest under it
    api_token_ref: "confluence_api_token"   # read via read_secret(); env CONFLUENCE_API_TOKEN
  jira:
    enabled: false
    url: ""                      # https://your-domain.atlassian.net
    email: ""
    project_key: ""              # default JIRA project key (JIRA-R1)
    api_token_ref: "jira_api_token"
    throttle_ms: 200             # NFR-P4 spacing between calls
    status_map:                  # v2 only (status sync)
      in_progress: "In Progress"
      review: "In Review"
      testing: "In Testing"
      deployed: "Done"
      failed: "To Do"
      cancelled: "Cancelled"
```

- **Test:** `python -c "import yaml; yaml.safe_load(open('config/project.yaml')); print('OK')"`.

#### Task 1.3: Create the package

- Create `src/integrations/__init__.py` with docstring
  `"""Atlassian Confluence & JIRA integration layer (one-way publish)."""`.
- **Test:** `python -c "import src.integrations; print('OK')"`.

#### Task 1.4: Markdown → Storage Format converter + SPIKE (CONF-R7 / Risk R-CONV)

**Files:** create `src/integrations/markdown_convert.py`.

- Implement `md_to_storage(md: str) -> str` using `markdown` (with `tables`, `fenced_code`,
  `sane_lists` extensions) → HTML → light XHTML cleanup acceptable to Confluence Storage
  Format.
- **SPIKE FIRST:** before relying on it, confirm `atlassian-python-api` does NOT already give
  you a markdown path. If `Confluence.create_page` accepts `representation="storage"` with
  the produced XHTML, you're good.
- **Test (`tests/test_markdown_convert.py`):** feed a fixture containing a GFM table, a nested
  bulleted list, and a fenced ```python block (i.e. what real agent PRDs contain). Assert the
  output contains `<table>`, nested `<ul>`, and a `<ac:structured-macro ac:name="code">` or
  `<pre>` block — and that it round-trips through `Confluence` validation in mock.
- **Done when:** the three constructs convert without loss. If `atlassian-python-api`'s own
  converter is the deprecated wiki-markup one (expected), this module is mandatory.

---

### Phase 2: Model & Store (`integration_mappings`)

#### Task 2.1: `IntegrationMapping` model

**File:** create `src/models/integration.py`.

```python
"""Integration mapping models — tracks Confluence/JIRA sync state."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

EntityType = Literal["project_home", "prd", "api_spec", "epic", "feature", "task"]
IntegrationName = Literal["confluence", "jira"]
SyncStatus = Literal["ok", "error", "stale", "superseded", "orphaned", "pending"]

class IntegrationMapping(BaseModel):
    mapping_id: str                        # "map-<8hex>"
    project_id: str
    entity_type: EntityType
    entity_id: str                         # STABLE id — project_id for prd/api_spec; epic_id/feature_id/task_id
    integration: IntegrationName
    external_ref: str                      # Confluence page_id or JIRA issue key
    external_url: str = ""
    last_synced_at: datetime | None = None
    sync_status: SyncStatus = "ok"
    sync_error: str | None = None
    jira_project_key: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = None
```

- **Test:** `python -c "from src.models.integration import IntegrationMapping; print('OK')"`.

#### Task 2.2: Add the table (NFR-R3 — survives restart)

**File:** `src/state/sqlite_store.py`. Find the schema block (search
`CREATE TABLE IF NOT EXISTS project_tasks`, ~L428) and add the `integration_mappings`
`CREATE TABLE` + index from PRD §7.1 after it.

- **Test:**
```bash
python -c "
import asyncio
from src.state.sqlite_store import SQLiteStateStore
async def t():
    s = SQLiteStateStore(db_path='/tmp/test_cji.db'); await s.initialize()
    db = await s._get_db()
    async with db.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='integration_mappings'\") as c:
        assert await c.fetchone(); print('OK')
    await s.close()
asyncio.run(t())"
```

#### Task 2.3: CRUD methods on the store

Add to `SQLiteStateStore` near the `project_artifacts` block (~L2593):
`upsert_integration_mapping`, `get_integration_mapping(project_id, entity_type, entity_id, integration)`,
`list_integration_mappings(project_id)` (for OBS-R1), `set_mapping_sync_status(mapping_id, status, error=None)`
(for LIFE-R1/R2/R3), and the `_row_to_integration_mapping` helper. Use `INSERT OR REPLACE`
against the UNIQUE key.

#### Task 2.4: CRUD tests

**File:** `tests/test_integration_mappings.py`. Cover: upsert+get; upsert-replaces (same key
updates row); get-missing→None; `list_integration_mappings` returns all for a project;
`set_mapping_sync_status` flips status+error. **Run:** `pytest tests/test_integration_mappings.py -v`.

---

### Phase 3: Confluence Client

#### Task 3.1: Client skeleton + resilience wrapper (ERR-R1..R6)

**File:** create `src/integrations/confluence.py`. Define `ConfluencePushResult` dataclass
(`ok, page_id, page_url, action, error` + `as_dict()` mirroring `HostWriteResult.as_dict()`),
the `Confluence` client init, and a private `_call(fn, *a, **kw)` helper that wraps
`asyncio.to_thread`, applies the 30s timeout, 429 backoff (1/2/4s), and single network retry.

#### Task 3.2: `ensure_project_home` (CONF-R2/R3)

Create-or-get the per-project home page under the configured space; idempotent via the
`(project_id, entity_type='project_home')` mapping. Returns the page id used as parent.

#### Task 3.3: `upsert_page` (CONF-R4..R7)

Create-or-update a child page under the project home with a **version-less title**, body =
`md_to_storage(content)` (T1.4) + a small metadata header carrying platform `version` +
`artifact_id` (CONF-R6). When `existing_page_id` is set, update (bumping Confluence's own
version); else create.

#### Task 3.4: Confluence client tests (mocked)

Mock the `atlassian.Confluence` calls. Assert: create path returns `action="created"`; with
an existing page id returns `action="updated"`; conversion is invoked; **no token appears in
any log record** (capture `caplog`).

---

### Phase 4: JIRA Client

#### Task 4.1: Client skeleton + resilience wrapper

**File:** create `src/integrations/jira.py`. `IssueResult` dataclass (`ok, issue_key,
issue_url, action, error` + `as_dict()`), client init, same `_call` resilience wrapper as T3.1.

#### Task 4.2: `verify_project` (JIRA-R3)

Verify the configured project exists + credentials can write; map 401/403 → `auth_failed`,
404 → `project_not_found`. Cache the verdict per project key.

#### Task 4.3: `discover_parent_field` (JIRA-R3a)

Discover the instance-specific Epic-link/parent field id via the createmeta/field APIs; cache
it. Degrade gracefully (return `None`, log WARNING) so a Story can still be created unlinked.

#### Task 4.4: `upsert_epic` (JIRA-R4..R7)

Create-or-update a JIRA Epic from `(epic.title, epic.description + acceptance_criteria)`.
Update path keyed by the caller via the `(entity_type='epic', entity_id=epic_id)` mapping.

#### Task 4.5: `upsert_story` (JIRA-R8..R10)

Create-or-update a Story; link to parent Epic via the discovered field. If the parent Epic key
is unknown at call time, create unlinked and return a flag so the publisher defers the link
(JIRA-R16).

#### Task 4.6: `upsert_subtask` + `ensure_link` (JIRA-R11..R16)

`upsert_subtask(parent_story_key, ...)` from task fields incl. `primary_file`, `expected_loc`,
`acceptance_test`. `ensure_link(inward, outward, type="Blocks")` is idempotent (don't
duplicate an existing link). **JIRA client tests:** mock all calls; assert create/update
actions, parent-link payload uses the discovered field, `ensure_link` is idempotent, throttle
spacing honored, no token logged.

---

### Phase 5: Integration Publisher + Event Handler

#### Task 5.1: `IntegrationPublisher` + handler factory (NFR-P3, CONF-R11/12, JIRA-R17)

**Files:** create `src/integrations/publisher.py` and `src/integrations/handlers.py`.
`make_integration_publish_handler(state, events)` returns an `events.on`-compatible
`async handler(event_type, data)` that dispatches per event (PRD §8.3). Reads config; if a
side is disabled, records `action="skipped"` and returns. **Never raises** (handler errors are
swallowed by the bus `events.py:178`, but we also persist `sync_status`).

#### Task 5.2: `_publish_prd` (CONF-R4..R7, G3)

On `project.prd_finalized`: resolve mapping on `(project_id, 'prd')`; ensure project home;
upsert the version-less PRD page; persist mapping (`external_ref`=page_id, `last_synced_at`,
`sync_status`). Fetch PRD content via `state.get_artifact(project_id, ArtifactKind.PRD)`.

#### Task 5.3: `_publish_api_spec` (CONF-R8..R10)

On `project.api_spec_finalized`: same shape keyed on `(project_id, 'api_spec')`, title
`API Specification`.

#### Task 5.4: `_publish_epic` / `_publish_feature` (JIRA-R4..R10)

On `project.epic_finalized` (Phase 6) → upsert Epic, persist `(epic, epic_id)` mapping.
On `project.feature_finalized` → resolve parent Epic key from `(epic, feature.epic_id)` mapping,
upsert Story, persist `(feature, feature_id)` mapping. Use `state.get_epic` / `state.get_feature`.

#### Task 5.5: `_publish_task_tree_and_links` (JIRA-R11..R16, the heart of G2)

On `project.tasks_finalized`: load finalized tasks (`state.list_tasks_for_project(project_id,
list_version=...)`). For each: resolve parent Story from `(feature, task.feature_id)`; upsert
Sub-task; persist `(task, task_id)` mapping. **Then** run the deferred reconciliation pass:
for every `task.depends_on` / `feature.depends_on`, resolve the mapped JIRA key and
`ensure_link`; record unresolved links in `sync_error` (never drop). Throttle per
`throttle_ms`. This path may create dozens of issues — it runs on the bus, off the request
thread (NFR-P3).

#### Task 5.6: Lifecycle status transitions (LIFE-R1..R3)

Subscribe to `project.prd_deleted` / `project.api_spec_deleted` (exist: `projects.py:1968,2381`)
→ mark mapping `orphaned`. Provide a publisher method the unfinalize/delete paths can call to
mark `stale`. (No external deletion in v1.)

---

### Phase 6: New Finalize Events (Epic/Feature)

> The only edits to existing business logic. Two `emit` calls, mirroring how `patch_prd`
> already emits (`projects.py:1906`).

#### Task 6.1: Emit `project.epic_finalized`

In `finalize_epic_endpoint` (`projects.py:3992`), after `finalize_epic_subtree` +
`_sync_build_plan_to_disk`, add:
```python
await request.app.state.events.emit("project.epic_finalized", {
    "project_id": project_id, "epic_id": epic_id, "title": epic.title,
})
```
- **Verify:** finalize an epic, confirm a JIRA Epic upserts (mock or live), mapping row written.

#### Task 6.2: Emit `project.feature_finalized`

In `finalize_feature_endpoint` (`projects.py:4038`), after `finalize_feature_subtree`, add the
analogous emit with `feature_id` + `epic_id`. **Restart backend**, then verify.

> Note: `finalize_epic_endpoint` finalizes the whole subtree, so the epic emit may be followed
> by per-feature work. Keep emits granular (epic emit here, feature emit in its own endpoint)
> and let the handler decide. Tasks come via `project.tasks_finalized` only.

---

### Phase 7: Observability + Retry Endpoints

#### Task 7.1: `GET /api/v1/projects/{id}/integrations` (OBS-R1)

**File:** create `src/api/routes/integrations.py` (new router; register in `src/api/main.py`
beside the other routers). Return `list_integration_mappings(project_id)` rows. Auth: any
authenticated user (read).

#### Task 7.2: `POST /api/v1/projects/{id}/integrations/retry` (OBS-R2)

Re-attempt `error`/`stale` mappings for the project by re-emitting the relevant finalize
events (or calling publisher methods directly). Auth: `require_role("developer", "admin")`.

---

### Phase 8: Configuration & Secrets Wiring

#### Task 8.1: Load integration config + secrets at boot

In `src/main.py` lifespan (near `events.on(make_project_task_status_handler(state))`, L289),
read the `integrations` config, resolve tokens via
`read_secret("confluence_api_token", "CONFLUENCE_API_TOKEN")` /
`read_secret("jira_api_token", "JIRA_API_TOKEN")`, construct clients, and register:
```python
events.on(make_integration_publish_handler(state, events))
logger.info("integration_publish_handler_registered")
```
Guard so absent creds → handler runs in skip mode (NFR-R1). **Never log token values.**

#### Task 8.2: Docker secrets + env example

- Add `confluence_api_token` / `jira_api_token` to the `secrets:` blocks in
  `docker-compose.prod.yml` (+ `secrets/README.md`).
- Add the `CONFLUENCE_*` / `JIRA_*` env vars to `.env.example`.
- **Verify:** `docker compose config` parses.

---

### Phase 9: Testing & Docker Verification

#### Task 9.1: Unit suite green
`pytest tests/test_integration_mappings.py tests/test_markdown_convert.py
tests/integrations/ -v` — all pass. Prove any pre-existing suite failures are pre-existing
(`git stash` / `git checkout HEAD~1` baseline) so you don't get blamed for them.

#### Task 9.2: Publisher integration test (mock clients)
End-to-end through the handler with mocked Confluence/JIRA: emit `project.prd_finalized` →
assert page upsert + mapping; emit again (simulating re-finalize with a NEW artifact_id) →
assert **same** page updated, **no duplicate** (the C2 regression test — this is the test that
would have caught v1.0).

#### Task 9.3: Tree + dependency-link test
Seed an epic→feature→task tree with `depends_on`; emit epic/feature/tasks finalized
out of order; assert Stories link to Epics, Sub-tasks to Stories, and all `depends_on` links
reconciled (JIRA-R16).

#### Task 9.4: Security test (NFR-S4)
Assert no `structlog` record in the integrations package contains the token value; `read_secret`
is the only credential path.

#### Task 9.5: Docker smoke (NFR-R3, the real north-star)
`docker compose up -d --build backend`; with integrations **disabled**, confirm the stack boots
and a PRD finalize still returns 200 with `meta.confluence_push.action="skipped"`. Then with a
test Atlassian sandbox enabled, finalize a real PRD and confirm a page appears + `meta` carries
the live `page_url`. Restart backend; confirm `integration_mappings` rows persist (state in
SQLite).

---

### Phase 10: Docs & Polish

#### Task 10.1: Setup doc
`docs/setup-confluence-jira-integration.md`: how to mint Atlassian API tokens, set the parent
space key + JIRA project key, the env/secret names, and how to read `meta.*_push` results.

#### Task 10.2: Document v1 lifecycle behavior (LIFE-R4)
In the setup doc + README: unfinalize/regenerate/delete do **not** mutate external resources in
v1; mappings flip to `stale`/`superseded`/`orphaned` and are visible via the integrations
endpoint; manual cleanup is expected. External close/delete is v2.

---

### Phase V2 (deferred): JIRA Status Sync

> Do NOT build on the batch dispatch events. Requires the Phase-0 emit fix first.

#### Task TV2.1: Emit `project.task_status.changed` (the C1 fix)
In `set_task_status` (`sqlite_store.py:2918`), after `commit`, emit
`project.task_status.changed` with `{project_id, task_id, old_status, new_status, request_id}`.
The store needs an `events` reference (inject at construction, or emit from the one caller
`project_task_status.py:79` instead — pick the single authoritative chokepoint). Add a test
asserting the event fires exactly once per real status change.

#### Task TV2.2: `JiraStatusSyncHandler` (JIRA-R18..R20)
New handler subscribing to `project.task_status.changed`: look up `(task, task_id)` mapping;
map status via config `status_map`; `transition_issue`. Soft-fail; log WARNING on workflow
mismatch; never touch platform status.

#### Task TV2.3: `transition_issue` on the JIRA client
Implement + test transition discovery (available transitions for the issue) and the mapped
move; gracefully skip unknown transitions.

---

## Definition of Done (v1)

- [ ] Phase 0 verifications recorded (events/ids confirmed against live tree).
- [ ] Markdown converter passes table/nested-list/code-block fixtures (T1.4).
- [ ] `integration_mappings` keyed on stable ids; re-finalize updates, never duplicates (T9.2).
- [ ] PRD + API Spec publish to Confluence under one parent space; `meta.confluence_push` populated.
- [ ] Epic→Story→Sub-task tree builds at finalize; dependency links reconciled (T9.3).
- [ ] All pushes run on the event bus, soft-fail, never block finalize; throttled (NFR-P3/P4).
- [ ] No tokens in logs (T9.4); creds only via `read_secret`.
- [ ] Stack boots + finalize works with integrations disabled (skip path); state survives restart (T9.5).
- [ ] Status sync explicitly deferred to v2 with the emit-fix path documented.
