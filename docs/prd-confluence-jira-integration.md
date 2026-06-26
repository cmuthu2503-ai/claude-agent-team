# Product Requirements Document (PRD)
# Confluence & JIRA Integration for Agent Team Platform

---

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 2.0 |
| Created Date | 2026-06-26 |
| Last Updated | 2026-06-26 |
| Status | Draft — corrected against source |
| Product Owner | Chandramouli |
| Parent System | Agent Team Platform (`claude-agent-team`) |
| Dependencies | Atlassian Cloud REST APIs (Confluence v2 + JIRA v3) |
| Supersedes | v1.0 (2026-06-26) — see §0 for what changed and why |

---

## Table of Contents

0. [What Changed in v2.0 (and why)](#0-what-changed-in-v20-and-why)
1. [Executive Summary](#1-executive-summary)
2. [Goals & Goal Traceability](#2-goals--goal-traceability)
3. [Product Overview](#3-product-overview)
4. [Detailed Feature Requirements](#4-detailed-feature-requirements)
   - [4.1 Confluence Integration](#41-confluence-integration)
   - [4.2 JIRA Integration](#42-jira-integration)
   - [4.3 Configuration & Secret Management](#43-configuration--secret-management)
   - [4.4 Error Handling & Resilience](#44-error-handling--resilience)
   - [4.5 Lifecycle: Unfinalize, Regenerate, Delete](#45-lifecycle-unfinalize-regenerate-delete)
5. [Non-Functional Requirements](#5-non-functional-requirements)
6. [Technical Architecture](#6-technical-architecture)
7. [Data Model](#7-data-model)
8. [API & Client Design](#8-api--client-design)
9. [Integration Points (verified against source)](#9-integration-points-verified-against-source)
10. [Constraints & Risks](#10-constraints--risks)
11. [Scope: v1 vs v2](#11-scope-v1-vs-v2)
12. [Appendix](#12-appendix)

---

## 0. What Changed in v2.0 (and why)

v1.0 was a well-structured spec, but a review against the actual codebase
(`src/api/routes/projects.py`, `src/core/events.py`, `src/state/sqlite_store.py`,
`src/core/project_task_status.py`, `src/core/auto_dispatch.py`) found **three
load-bearing assumptions that are false**. v2.0 corrects them. Each correction is
traceable to source.

| # | v1.0 assumption | Reality in code | v2.0 correction |
|---|-----------------|-----------------|-----------------|
| C1 | JIRA status-sync listens for `project.task.dispatched` and `project.task_status.changed` events | **Neither event exists.** `StateStore.set_task_status()` (`sqlite_store.py:2918`) writes the DB row and emits nothing. The only task-status driver is `project_task_status.py:79`, which calls `set_task_status()` directly with no downstream emit. The only dispatch events are the **batch** `project.tasks.auto_dispatched` / `project.tasks.dispatch_proposed` (`auto_dispatch.py:155,194`). | **NEW prerequisite task:** make `set_task_status()` emit `project.task_status.changed`. The JIRA status-sync handler hangs off that single, authoritative event. (See ERR/JIRA-R section + Phase 0 in tasks doc.) |
| C2 | Confluence idempotency keyed on `entity_id = artifact_id`; page title `PRD v{version}` | **Every PRD/API-Spec regeneration mints a NEW `artifact_id`** (`projects.py:1691`, `art-{uuid4().hex[:12]}`) and bumps `version` (`:1689`). Keying on `artifact_id` means a re-finalize after refine MISSES the mapping → duplicate page. Version-stamped titles guarantee a new page per version regardless. | **Re-key mappings to the STABLE parent layer** `(project_id, entity_type)` — `artifact_id` is volatile, the `(project, kind)` pair survives regeneration. Use a **version-less page title** and let Confluence's native page history track versions. |
| C3 | Tasks → JIRA Sub-tasks created **on dispatch**; nothing created for backlog tasks (JIRA-R12/R15) | The user's stated goal is "publish Tasks belonging to respective Features" — i.e. the **finalized plan**, not a dispatch-time trickle. Dispatch is also the broken trigger from C1 and may be gated behind a human-confirmed proposal in governed mode (`auto_dispatch.py:171`). | **Create the full Epic→Story→Sub-task tree at Task-list finalize** (`finalize_tasks`, which already emits `project.tasks_finalized` — `projects.py:3100`). Status-sync (v2) is the only thing that reacts to dispatch/execution. |

Two further structural changes:
- **Scope split (v1 vs v2).** v1.0 bundled an async bidirectional-leaning status-sync
  subsystem the user never asked for and that sits on the broken C1 events. v2.0 ships
  **one-way publish in v1**; status-sync moves to v2 behind the C1 fix. See §11.
- **Pushes run off the event bus, not inline.** Bulk finalize (`finalize_epic_endpoint`
  finalizes an epic + all features + all tasks atomically) would otherwise fan out dozens
  of synchronous Atlassian calls inside one HTTP request. See NFR-P3 / §6.3.

---

## 1. Executive Summary

### 1.1 Product Vision

The Agent Team Platform generates PRDs, API specifications, epics, features, and tasks,
persisting them to SQLite, the host filesystem (`docs/PRD.md`, `docs/api-spec.md`,
`docs/tasks.md`), and the project's GitHub repo. This PRD defines a **Confluence & JIRA
integration layer** that additionally:

1. Publishes the finalized **PRD** and **API Spec** to **Confluence**.
2. Publishes the finalized **Epic → Feature → Task** hierarchy to **JIRA** as an
   **Epic → Story → Sub-task** tree.

The integration follows the platform's established **soft-fail, `meta`-envelope** pattern —
the exact pattern used today by `write_finalized_prd()` (`project_workspace.py:137`) and
`_push_finalized_doc_to_repo()` (`projects.py:1989`). Integration failures are reported in
the response `meta` and **never roll back** the platform's finalize transaction.

### 1.2 Problem Statement

- **PRD and API Spec are siloed.** After finalizing, the user manually copies content into
  Confluence if their org documents there.
- **The Epic→Feature→Task plan has no JIRA presence.** Organizations that track work in
  JIRA cannot consume the platform's build plan without manual re-entry.
- **No traceable link** between a platform artifact and its external counterpart, so
  re-finalize/regenerate can't update the right page/issue.

### 1.3 Target Users

- **Primary:** Technical leads & product managers who use Confluence for docs and JIRA for
  work tracking alongside the platform.
- **Secondary:** Development teams who consume JIRA issues without touching the platform.

---

## 2. Goals & Goal Traceability

| ID | Goal | Satisfied by |
|----|------|--------------|
| G1 | Publish finalized PRD + API Spec markdown to Confluence | CONF-R4..R12 |
| G2 | Create a JIRA Epic→Story→Sub-task tree mirroring the platform's Epic→Feature→Task hierarchy, reflecting the **finalized plan** (not just dispatched work) | JIRA-R4..R16 |
| G3 | Re-finalize / regenerate **updates** the existing page/issue rather than duplicating | CONF-R5, JIRA-R7/R10/R15, via stable `(project_id, entity_type)` keying (§7) |
| G4 | Surface integration status (published / failed / skipped / queued) in the `meta` envelope | CONF-R11, JIRA-R17, OBS-R1 |
| G5 | Integration failures NEVER block platform operations (soft-fail) | ERR-R5, NFR-R1/R2 |
| G6 | All credentials via Docker secrets / env (never in code or config files) | CFG-R1..R4, NFR-S1 |

> **Reviewer note on G2:** The literal user requirement is "publish Epics, Features
> belonging to respective Epics, and Tasks belonging to respective Features." v2.0 honors
> that by building the whole tree at finalize. v1.0's dispatch-only Sub-task creation
> (JIRA-R12/R15) would have produced a *partial* tree and is removed.

---

## 3. Product Overview

### 3.1 High-Level Flow (v1 — one-way publish)

```
Platform Action                         Confluence                     JIRA
──────────────────────────────────────────────────────────────────────────────────
Finalize PRD            ─emit project.prd_finalized──►  upsert page (version-less title)
Finalize API Spec       ─emit project.api_spec_finalized►  upsert page
Finalize Epic           ─emit project.epic_finalized*──────────────►  upsert Epic issue
Finalize Feature        ─emit project.feature_finalized*────────────►  upsert Story (child of Epic)
Finalize Task list      ─emit project.tasks_finalized───────────────►  upsert Sub-tasks (children of Stories)
                                                                        + reconcile dependency links
```

`*` `project.epic_finalized` / `project.feature_finalized` are **new events** this feature
adds to the existing finalize endpoints (`finalize_epic_endpoint` `projects.py:3956`,
`finalize_feature_endpoint` `:4010`), which currently emit nothing. Adding them mirrors how
`patch_prd` already emits `project.prd_finalized` (`:1906`) and `patch_api_spec` emits
`project.api_spec_finalized` (`:2332`).

### 3.2 High-Level Flow (v2 — status sync, deferred)

```
Task status changes (set_task_status) ─emit project.task_status.changed─► transition JIRA issue
```

Requires the Phase-0 emit fix (C1). Not in v1. See §11.

### 3.3 Key Design Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Publish on **finalize**, never on draft/generate | Matches the existing host-write + GitHub-push pattern; draft churn must not pollute Confluence/JIRA |
| D2 | **Single configurable parent Confluence space**, one page-tree per project (NOT space-per-project) | Space creation needs space-admin rights many orgs deny; the global space-key namespace collides; per-project pages under one space is robust and is v1.0's own FE3 promoted to v1 |
| D3 | JIRA hierarchy **Epic → Story → Sub-task** | Natural mapping from Epic → Feature → Task |
| D4 | **Soft-fail always** — push result in `meta`, transaction never rolls back | Matches `host_write` / `github_push` (`projects.py:1915,1928`) |
| D5 | **Mappings keyed on `(project_id, entity_type, entity_id, integration)` where `entity_id` is a STABLE id** — `project_id` for prd/api_spec (one each per project), `epic_id`/`feature_id` (stable across the row's life) for the tree | `artifact_id` is regenerated on every PRD/API-Spec regen (`projects.py:1691`); keying on it breaks update-vs-create. `epic_id`/`feature_id` are minted once (`E-<8hex>`, `F-<8hex>`) and survive |
| D6 | Pushes execute via the **EventEmitter handler bus** (`events.on(...)`, registered at boot like `project_task_status` `main.py:289`), not inline in the route | Bulk epic-subtree finalize fans out many calls; inline blocks the request and risks the JIRA rate cap |

---

## 4. Detailed Feature Requirements

### 4.1 Confluence Integration

#### 4.1.1 Space & Page Placement

| ID | Requirement |
|----|-------------|
| CONF-R1 | The system SHALL publish into a **single configured parent space** (`CONFLUENCE_SPACE_KEY`). It SHALL NOT create a space per project in v1. If the parent space does not exist, publishing SHALL soft-fail with `error: "space_not_found"`. |
| CONF-R2 | Each platform project SHALL map to a **parent page** (the "project home") created under the configured space, titled `{project.name}`. PRD and API Spec pages SHALL be children of that project home page. |
| CONF-R3 | Project-home and child-page creation SHALL be idempotent — tracked via `integration_mappings` keyed on the stable `(project_id, entity_type)` (see §7). |

#### 4.1.2 PRD Publishing

| ID | Requirement |
|----|-------------|
| CONF-R4 | When a PRD is finalized (`patch_prd` with `status=finalized`, emitting `project.prd_finalized`), the system SHALL upsert a Confluence page titled **`Product Requirements Document`** (version-LESS) under the project home page. |
| CONF-R5 | On re-finalize (after refine/regenerate — which mints a new `artifact_id`), the system SHALL **update the same page** by looking up the mapping on `(project_id, entity_type='prd')`. It SHALL NOT create a duplicate. *(Acceptance: finalize → refine → finalize again yields exactly ONE PRD page whose Confluence version count incremented.)* |
| CONF-R6 | The page SHALL record the platform PRD `version` and `artifact_id` in a small metadata header (panel/labels) for traceability, even though the title is version-less. |
| CONF-R7 | Markdown SHALL be converted to Confluence Storage Format via an **explicit `markdown → HTML → Storage Format` pipeline** (e.g. the `markdown` lib → sanitized XHTML). The conversion SHALL be unit-tested against a PRD containing tables, nested lists, and fenced code blocks. *(Do not assume `atlassian-python-api` converts markdown — it does not; its converter targets deprecated Confluence wiki markup. See Risk R-CONV.)* |

#### 4.1.3 API Spec Publishing

| ID | Requirement |
|----|-------------|
| CONF-R8 | When an API Spec is finalized (`patch_api_spec`, emitting `project.api_spec_finalized` `projects.py:2332`), the system SHALL upsert a page titled **`API Specification`** (version-less) under the project home page. |
| CONF-R9 | Same `(project_id, entity_type='api_spec')` keying and update-vs-create idempotency as CONF-R5. |
| CONF-R10 | Same conversion + test requirement as CONF-R7. |

#### 4.1.4 Publishing Metadata

| ID | Requirement |
|----|-------------|
| CONF-R11 | The `meta` envelope on the finalize response SHALL include `confluence_push: {ok, page_id, page_url, action: "created"\|"updated"\|"skipped"\|"queued", error}` — mirroring `HostWriteResult.as_dict()` (`project_workspace.py:90`) and the `github_push` dict (`projects.py:2034`). |
| CONF-R12 | Publishing SHALL be skipped silently (`{ok: true, action: "skipped", reason: "disabled"}`) when `CONFLUENCE_ENABLED != "true"`. |

### 4.2 JIRA Integration

#### 4.2.1 JIRA Project Setup

| ID | Requirement |
|----|-------------|
| JIRA-R1 | The system SHALL require a **pre-existing** JIRA project. The project key SHALL come from config (`JIRA_PROJECT_KEY`) with optional per-project override stored in `integration_mappings.jira_project_key`. |
| JIRA-R2 | If no JIRA project key is resolvable for a platform project, JIRA publishing SHALL be skipped silently (`action: "skipped", reason: "no_project_key"`). |
| JIRA-R3 | On first publish per project, the system SHALL verify the JIRA project exists and credentials have write access; failure SHALL be a non-blocking `error: "auth_failed"` or `"project_not_found"` in `meta`. |
| JIRA-R3a | The system SHALL dynamically discover the instance-specific **Epic-link / parent field** id (e.g. `customfield_xxxxx` or the team-managed `parent` field) at client init and cache it. It SHALL degrade gracefully (Story created without parent link, logged WARNING) if discovery fails. |

#### 4.2.2 Epic → JIRA Epic

| ID | Requirement |
|----|-------------|
| JIRA-R4 | When an Epic is finalized (`finalize_epic_endpoint` `projects.py:3956`, which v2.0 extends to emit **`project.epic_finalized`**), the system SHALL create or update a JIRA **Epic** issue. |
| JIRA-R5 | The JIRA Epic summary SHALL be `{epic.title}`; description SHALL include `{epic.description}` + `{epic.acceptance_criteria}` (`base.py:558-560`). |
| JIRA-R6 | Update-vs-create SHALL be resolved by mapping lookup on `(project_id, entity_type='epic', entity_id=epic_id)`. `epic_id` (`E-<8hex>`) is stable across the row's life. |
| JIRA-R7 | The created JIRA Epic key SHALL be stored in `integration_mappings.external_ref` for child Story resolution. |
| JIRA-R7a | **Regeneration caveat:** a Pass-1 epic regeneration mints new `epic_id`s and archives the old list (`base.py:555`). Finalizing the new list creates NEW JIRA Epics; the old ones are left intact (see §4.5). This is intentional and matches the "unfinalized issues stay" decision. |

#### 4.2.3 Feature → JIRA Story

| ID | Requirement |
|----|-------------|
| JIRA-R8 | When a Feature is finalized (`finalize_feature_endpoint` `projects.py:4010`, extended to emit **`project.feature_finalized`**), the system SHALL create or update a JIRA **Story**. |
| JIRA-R9 | The Story SHALL be linked as a child of the parent JIRA Epic via the field discovered in JIRA-R3a. The parent Epic key SHALL be resolved from the mapping for `(entity_type='epic', entity_id=feature.epic_id)` (`base.py:574`). If the parent Epic has no mapping yet (feature finalized before its epic), the link SHALL be deferred to a reconciliation pass (JIRA-R16) rather than dropped. |
| JIRA-R10 | Story summary `{feature.title}`; description includes `{feature.description}` + `{feature.acceptance_criteria}`. Update-vs-create keyed on `(entity_type='feature', entity_id=feature_id)`. |

#### 4.2.4 Task → JIRA Sub-task (at finalize, NOT dispatch)

| ID | Requirement |
|----|-------------|
| JIRA-R11 | When the **task list is finalized** (`finalize_tasks` `projects.py:3067`, which already emits `project.tasks_finalized` `:3100`), the system SHALL create or update a JIRA **Sub-task** for **every finalized task** under its parent Story. |
| JIRA-R12 | Sub-task summary `{task.title}`; description includes `{task.description}`, `primary_file`, `expected_loc`, and `acceptance_test` (`base.py:534-536`). |
| JIRA-R13 | The parent Story SHALL be resolved from the mapping for `(entity_type='feature', entity_id=task.feature_id)` (`base.py:532`). Tasks with `feature_id=None` (legacy tasks) SHALL be created as standalone issues (or skipped — configurable), logged at INFO. |
| JIRA-R14 | Update-vs-create keyed on `(entity_type='task', entity_id=task_id)`. **Note:** a task-list regeneration mints new `task_id`s and bumps `list_version` (`base.py:509`); finalizing a new list version creates new Sub-tasks. Superseded Sub-tasks from the prior version are left intact (§4.5). |

#### 4.2.5 Dependency Links

| ID | Requirement |
|----|-------------|
| JIRA-R15 | Intra-feature task dependencies (`task.depends_on`, `base.py:533`) and rare feature deps (`feature.depends_on`, `base.py:582`) SHALL be expressed as JIRA issue links (`blocks` / `is blocked by`). |
| JIRA-R16 | Because finalize order is user-driven, link targets may not exist when a link is first attempted. The publisher SHALL run a **deferred reconciliation pass** after the full tree is published (at `project.tasks_finalized`): resolve each `depends_on` id to its mapped JIRA key and create any missing links. Unresolvable links SHALL be logged WARNING and recorded in `sync_error`, never dropped silently. |

#### 4.2.6 JIRA Publishing Metadata

| ID | Requirement |
|----|-------------|
| JIRA-R17 | All JIRA push operations SHALL contribute to a `meta.jira_push` summary: `{ok, issues: [{entity_type, entity_id, issue_key, issue_url, action}], errors: [...], skipped_reason?}`. Because the tree push runs on the event bus (D6), the synchronous finalize response MAY return `{ok: true, action: "queued"}`; the detailed result is retrievable via the status endpoint (OBS-R1). |

#### 4.2.7 Status Synchronization (v2 — DEFERRED)

| ID | Requirement |
|----|-------------|
| JIRA-R18 | *(v2)* When `set_task_status()` emits `project.task_status.changed` (Phase-0 fix C1), a `JiraStatusSyncHandler` SHALL transition the mapped JIRA issue per a configurable status map. |
| JIRA-R19 | *(v2)* Default map: `in_progress→In Progress`, `review→In Review`, `testing→In Testing`, `deployed→Done`, `failed→To Do`, `cancelled→Cancelled` (platform `TaskStatus`, `base.py:495-503`). |
| JIRA-R20 | *(v2)* Transition failures (workflow mismatch) SHALL be logged WARNING and SHALL NOT affect the platform task's status. |

### 4.3 Configuration & Secret Management

| ID | Requirement |
|----|-------------|
| CFG-R1 | Credentials SHALL be read via the **existing** `read_secret(secret_name, env_var)` helper (`src/utils/secrets.py`) — Docker-secret file first, env var fallback. Confluence: `read_secret("confluence_api_token", "CONFLUENCE_API_TOKEN")`. JIRA: `read_secret("jira_api_token", "JIRA_API_TOKEN")`. |
| CFG-R2 | Non-secret config (`*_ENABLED`, `*_URL`, `*_EMAIL`, `CONFLUENCE_SPACE_KEY`, `JIRA_PROJECT_KEY`, status map) SHALL live in `config/project.yaml` under a new `integrations` section, overridable by env vars. |
| CFG-R3 | The integration modules SHALL NOT log token material at any level (follow `secrets.py:48` — log the source, never the value). |
| CFG-R4 | The platform SHALL boot and run normally when integration credentials are absent (NFR-R1). |

### 4.4 Error Handling & Resilience

| ID | Requirement |
|----|-------------|
| ERR-R1 | All Confluence/JIRA API calls SHALL have a 30-second timeout. |
| ERR-R2 | HTTP 401/403 SHALL be reported `ok: false, error: "auth_failed"` — not retried. |
| ERR-R3 | HTTP 429 SHALL be retried up to 3× with exponential backoff (1s, 2s, 4s), honoring `Retry-After` when present. |
| ERR-R4 | Network errors (conn refused, DNS) SHALL be retried once after 2s. |
| ERR-R5 | Integration failures SHALL NOT roll back the platform finalize. The result lands in `meta.*_push.ok=false`; the user can re-trigger via a retry endpoint (OBS-R2). |
| ERR-R6 | Because the `atlassian-python-api` client is **synchronous**, every call SHALL be wrapped in `asyncio.to_thread()` so it never blocks the FastAPI event loop. |
| ERR-R7 | An EventEmitter handler that raises is already swallowed by the bus (`events.py:178-181`), but the publisher SHALL ALSO catch internally and persist `sync_status='error'` + `sync_error` so failures are observable, not just swallowed. |

### 4.5 Lifecycle: Unfinalize, Regenerate, Delete

| ID | Requirement |
|----|-------------|
| LIFE-R1 | On **unfinalize** (`unfinalize_epic_endpoint` `projects.py:4055`, `unfinalize_feature_endpoint` `:4103`), v1 SHALL mark the affected `integration_mappings` rows `sync_status='stale'` and SHALL NOT delete/close the JIRA issue or Confluence page. The stale flag SHALL be surfaced via OBS-R1. |
| LIFE-R2 | On **regenerate** (new `artifact_id` / new `epic_id`/`feature_id`/`task_id` + archived old rows), finalizing the new version creates new external resources; old mappings SHALL be marked `sync_status='superseded'`. No external deletion in v1. |
| LIFE-R3 | On **delete** (`delete_prd` `projects.py:1942`, `delete_api_spec` `:2361`), v1 SHALL mark mappings `sync_status='orphaned'` and log. External deletion is a v2 enhancement (FE2). |
| LIFE-R4 | These behaviors SHALL be documented in the UI/README so users understand external drift is intentional in v1 and must be cleaned up manually. |

---

## 5. Non-Functional Requirements

### 5.1 Performance

| ID | Requirement |
|----|-------------|
| NFR-P1 | A single Confluence page upsert SHALL complete within 5s (p95). |
| NFR-P2 | A single JIRA issue create/update SHALL complete within 3s (p95). |
| NFR-P3 | The **tree push** (epic-subtree or task-list finalize, which can be dozens of issues) SHALL run on the **EventEmitter handler bus** (D6), NOT inline in the HTTP request. The finalize endpoint returns promptly with `meta.jira_push.action="queued"`. |
| NFR-P4 | The publisher SHALL throttle JIRA calls (≥200ms spacing or a small concurrency cap) to stay under the rate limit (C1). |

### 5.2 Security

| ID | Requirement |
|----|-------------|
| NFR-S1 | API tokens SHALL be read only via `read_secret()` (CFG-R1); never stored in config files, the DB, or logs. |
| NFR-S2 | `integration_mappings` SHALL NOT store tokens. |
| NFR-S3 | All Atlassian calls SHALL use HTTPS. |
| NFR-S4 | A `structlog`-based redaction guard SHALL be applied in the integrations package; a code-review gate confirms no token field is logged. |

### 5.3 Reliability

| ID | Requirement |
|----|-------------|
| NFR-R1 | The platform SHALL boot and operate normally when integrations are disabled/unconfigured. |
| NFR-R2 | A failed push SHALL NOT poison subsequent pushes (each push reads fresh state; no cached failure). |
| NFR-R3 | The `integration_mappings` table and new events SHALL survive `docker compose restart backend` (the mandated post-`src/`-edit step) — i.e. all state is in SQLite, never in-process. |

### 5.4 Observability

| ID | Requirement |
|----|-------------|
| OBS-R1 | A read endpoint `GET /api/v1/projects/{id}/integrations` SHALL return all `integration_mappings` rows for the project (entity_type, external_ref, external_url, sync_status, last_synced_at, sync_error) so the UI can show publish state and drift. |
| OBS-R2 | A retry endpoint `POST /api/v1/projects/{id}/integrations/retry` (admin/developer) SHALL re-attempt failed/stale pushes for the project. |

---

## 6. Technical Architecture

### 6.1 Module Structure (new — `src/integrations/` does NOT exist yet)

```
src/
├── integrations/
│   ├── __init__.py
│   ├── confluence.py        # ConfluenceCloudClient — page CRUD + md→storage conversion
│   ├── jira.py              # JiraCloudClient — issue + link CRUD, field discovery
│   ├── markdown_convert.py  # markdown → HTML → Confluence Storage Format (CONF-R7)
│   ├── publisher.py         # IntegrationPublisher — orchestrates pushes (event-bus driven)
│   └── handlers.py          # make_integration_publish_handler(state, events) — events.on(...)
├── models/
│   └── integration.py       # IntegrationMapping pydantic model
└── api/routes/
    └── integrations.py      # OBS-R1 status + OBS-R2 retry endpoints (new router)
```

The publish handler is registered at boot in `src/main.py` right beside the existing
`events.on(make_project_task_status_handler(state))` (`main.py:289`) and
`events.on(make_auto_dispatch_handler(...))` (`main.py:301`).

### 6.2 Dependencies

- **`atlassian-python-api>=3.41.0`** — JIRA issue/link CRUD + Confluence page CRUD. **NOT**
  relied upon for markdown conversion (CONF-R7 / Risk R-CONV).
- **`markdown>=3.10`** — already in the dependency tree (`markdown-3.10.2` present in
  `.venv`); reuse for the md→HTML step.

### 6.3 Wire Points & New Events (verified against source)

| Wire point | File:line | Today | Change |
|------------|-----------|-------|--------|
| PRD finalize | `projects.py:1906` | emits `project.prd_finalized` | no code change to route; the publish handler subscribes to this event |
| API Spec finalize | `projects.py:2332` | emits `project.api_spec_finalized` | handler subscribes |
| Epic finalize | `projects.py:3992` | emits **nothing** | **add** `events.emit("project.epic_finalized", {...})` |
| Feature finalize | `projects.py:4038` | emits **nothing** | **add** `events.emit("project.feature_finalized", {...})` |
| Task-list finalize | `projects.py:3100` | emits `project.tasks_finalized` | handler subscribes; this is the trigger for the whole Sub-task tree + link reconciliation |
| `set_task_status` | `sqlite_store.py:2918` | emits **nothing** | **(v2, Phase-0)** add `project.task_status.changed` emit for status-sync |

> The event-driven design means the finalize routes stay almost untouched (only two new
> `emit(...)` calls) — all integration logic lives in the handler, consistent with how
> `project_task_status` and `auto_dispatch` already work.

---

## 7. Data Model

### 7.1 New Table: `integration_mappings`

```sql
CREATE TABLE IF NOT EXISTS integration_mappings (
    mapping_id       TEXT PRIMARY KEY,           -- "map-<8hex>"
    project_id       TEXT NOT NULL,
    entity_type      TEXT NOT NULL,              -- 'project_home' | 'prd' | 'api_spec' | 'epic' | 'feature' | 'task'
    entity_id        TEXT NOT NULL,              -- STABLE id (see §7.2)
    integration      TEXT NOT NULL,              -- 'confluence' | 'jira'
    external_ref     TEXT NOT NULL,              -- Confluence page_id or JIRA issue key
    external_url     TEXT DEFAULT '',
    last_synced_at   TEXT,
    sync_status      TEXT DEFAULT 'ok',          -- 'ok' | 'error' | 'stale' | 'superseded' | 'orphaned' | 'pending'
    sync_error       TEXT,
    jira_project_key TEXT,                        -- denormalized per-project override
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT,
    UNIQUE(project_id, entity_type, entity_id, integration)
);
CREATE INDEX IF NOT EXISTS idx_integration_mappings_lookup
    ON integration_mappings(project_id, entity_type, entity_id, integration);
```

### 7.2 Entity-id keying — the correctness fix (C2/D5)

| `entity_type` | `entity_id` value | Stable across regen? | Source |
|---------------|-------------------|----------------------|--------|
| `project_home` | `project_id` | yes | `Project.project_id` |
| `prd` | **`project_id`** (NOT `artifact_id`) | yes — one PRD page per project | `projects.py:1691` mints new `artifact_id` per regen, so we key on the project |
| `api_spec` | **`project_id`** | yes — one API Spec page per project | same reasoning |
| `epic` | `epic_id` (`E-<8hex>`) | stable for the row's life; new id on Pass-1 regen (handled by LIFE-R2) | `base.py:553` |
| `feature` | `feature_id` (`F-<8hex>`) | same | `base.py:573` |
| `task` | `task_id` | stable for the row's life; new id on list regen (LIFE-R2) | `base.py:507` |

> The version (`artifact_id`, `list_version`) is recorded in `sync_error`-adjacent metadata
> or the page body (CONF-R6), but is **never** part of the key. This is what makes
> "update on re-finalize" (G3) actually work.

---

## 8. API & Client Design

### 8.1 ConfluenceCloudClient (sync lib, wrapped in `to_thread`)

```python
class ConfluenceCloudClient:
    def __init__(self, url: str, email: str, api_token: str, space_key: str): ...
    async def ensure_project_home(self, project_id: str, project_name: str,
                                  existing_page_id: str | None) -> PageResult: ...
    async def upsert_page(self, parent_page_id: str, title: str, content_md: str,
                          existing_page_id: str | None,
                          meta_header: dict) -> PageResult: ...
# PageResult: {ok, page_id, page_url, action: 'created'|'updated', error}
```

### 8.2 JiraCloudClient

```python
class JiraCloudClient:
    def __init__(self, url: str, email: str, api_token: str, project_key: str): ...
    async def verify_project(self) -> dict: ...
    async def discover_parent_field(self) -> str: ...          # JIRA-R3a
    async def upsert_epic(self, title, description, existing_key) -> IssueResult: ...
    async def upsert_story(self, parent_epic_key, title, description, existing_key) -> IssueResult: ...
    async def upsert_subtask(self, parent_story_key, title, description, existing_key) -> IssueResult: ...
    async def ensure_link(self, inward_key, outward_key, link_type="Blocks") -> dict: ...  # JIRA-R15/16
    async def transition_issue(self, issue_key, target_status) -> dict: ...                 # v2
# IssueResult: {ok, issue_key, issue_url, action, error}
```

### 8.3 IntegrationPublisher (event-bus driven, soft-fail)

```python
def make_integration_publish_handler(state, events):
    async def handler(event_type: str, data: dict) -> None:
        if event_type == "project.prd_finalized":        await _publish_prd(...)
        elif event_type == "project.api_spec_finalized":  await _publish_api_spec(...)
        elif event_type == "project.epic_finalized":      await _publish_epic(...)
        elif event_type == "project.feature_finalized":   await _publish_feature(...)
        elif event_type == "project.tasks_finalized":     await _publish_task_tree_and_links(...)
        # all paths: soft-fail, persist mapping + sync_status, never raise
    return handler
```

---

## 9. Integration Points (verified against source)

| Concern | Verified fact | Citation |
|---------|---------------|----------|
| Soft-fail envelope shape | `HostWriteResult.as_dict()` returns `{ok, path, bytes, error}`; `github_push` returns `{ok, repo, path, sha, url}` or `{ok: false, skipped}` | `project_workspace.py:90`, `projects.py:2034` |
| Event bus + boot registration | `EventEmitter.on()` handlers run after WS delivery, errors swallowed; registered in `main.py` lifespan | `events.py:150,178`; `main.py:289,301` |
| Stage gating (user's workflow) | API Spec gen blocked until PRD finalized; Epics blocked until PRD+API Spec finalized; Features/Tasks likewise | `projects.py:2124, 4749, 4761, 5119, 5128, 5490` |
| Finalize emits | PRD `project.prd_finalized`, API Spec `project.api_spec_finalized`, Task list `project.tasks_finalized` exist; Epic/Feature finalize emit nothing | `projects.py:1906, 2332, 3100` |
| Task status driver emits nothing | `set_task_status` is a bare UPDATE+commit | `sqlite_store.py:2918` |
| Hierarchy fields for mapping | Epic/Feature/Task models carry `title`, `description`, `acceptance_criteria`, `depends_on`, `feature_id`, `epic_id`, `primary_file`, `expected_loc`, `acceptance_test` | `base.py:506-585` |
| Secrets pattern | `read_secret(secret_name, env_var)` — file-first, env-fallback | `secrets.py:31` |

---

## 10. Constraints & Risks

### 10.1 Constraints

| ID | Constraint |
|----|------------|
| C1 | JIRA Cloud rate limit (~ instance-dependent, low hundreds/min). Tree push must throttle (NFR-P4). |
| C2 | Markdown → Storage Format is lossy for complex tables/nested lists/code; mitigate with the tested converter (CONF-R7) + a "View/Edit in Confluence" link. |
| C3 | JIRA parent/Epic-link field id is instance-specific; discover dynamically (JIRA-R3a). |
| C4 | JIRA workflows are instance-specific; v2 transitions only attempt known statuses and fail gracefully. |
| C5 | `atlassian-python-api` is synchronous; all calls wrapped in `asyncio.to_thread()` (ERR-R6). |

### 10.2 Risks

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|-----------|--------|------------|
| R-CONV | `atlassian-python-api` does NOT convert markdown→storage as v1.0 assumed | High | High | Explicit tested converter (CONF-R7); spike it in Phase 1 before building on it |
| R-EVT | Status-sync depends on a non-existent event | (resolved) | High | Phase-0 emit fix (C1); status-sync deferred to v2 |
| R-DUP | Duplicate pages/issues on re-finalize | (resolved) | High | Stable `(project_id, entity_type)` keying (§7.2) |
| R-RATE | Bulk finalize trips rate limit / request timeout | Medium | Medium | Event-bus async push + throttle (NFR-P3/P4) |
| R-LINK | Dependency links dropped when target issue absent | Medium | Medium | Deferred reconciliation pass (JIRA-R16) |
| R-LEAK | Token leakage in logs | Low | High | `read_secret` source-only logging + redaction guard (NFR-S4) |

---

## 11. Scope: v1 vs v2

| Capability | v1 | v2 |
|------------|----|----|
| Phase-0: `set_task_status` emits `project.task_status.changed` | prerequisite only if pulling v2 forward | ✅ |
| Confluence PRD + API Spec publish (one-way) | ✅ | |
| JIRA Epic→Story→Sub-task tree at finalize (one-way) | ✅ | |
| Dependency-link reconciliation | ✅ | |
| Mapping status endpoint + retry (OBS-R1/R2) | ✅ | |
| JIRA status-sync on task status change | | ✅ |
| External delete/close on unfinalize/delete | | ✅ |
| Per-project settings UI (space key, project key) | | ✅ |
| Confluence page hierarchy beyond home→child | | ✅ |

**v1 ships exactly the user's stated request** (publish PRD/API-Spec to Confluence;
publish the Epic/Feature/Task hierarchy to JIRA), one-way, soft-fail, idempotent.
Everything reactive/bidirectional is v2.

---

## 12. Appendix

### 12.1 Glossary

| Term | Definition |
|------|------------|
| BPD | Build Plan Decomposition — the platform's Epic→Feature→Task generation workflow (`base.py:539`) |
| Storage Format | Confluence's XHTML-based page content format |
| Soft-fail | A failure logged + reported in `meta` that does not roll back the primary transaction |
| Stable id | An id minted once and unchanged for a row's life (`epic_id`, `feature_id`, `task_id`, `project_id`) — as opposed to `artifact_id`, regenerated per PRD/API-Spec regen |
| Event bus | `EventEmitter` server-side handlers registered via `events.on()` (`events.py:150`) |

### 12.2 Resolved Questions (were Open in v1.0)

- **Q1 (space auto-create?):** No. Publish under a single configured parent space; one
  project-home page per project (D2). Per-project space is out of scope.
- **Q2 (issues at finalize or dispatch?):** **Finalize.** The whole tree is built when the
  task list is finalized so JIRA mirrors the approved plan (G2). Dispatch is irrelevant to
  v1 publishing.
- **Q3 (unfinalize/delete → close JIRA?):** No external mutation in v1. Mark mappings
  stale/orphaned and surface drift (LIFE-R1/R3); external close is v2 (FE2).

### 12.3 Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-06-26 | Hermes Agent | Initial draft |
| 2.0 | 2026-06-26 | Atlas (review) | Corrected 3 false code assumptions (event model, mapping key, finalize-vs-dispatch); event-bus async push; single-space design; v1/v2 split; lifecycle + observability requirements; goal-traceability matrix; every claim cited to source |
