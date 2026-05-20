# Product Requirements Document (PRD)
# Project Management — Create Projects & Assign Requests

---

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 1.3 |
| Created Date | 2026-05-17 |
| Last Updated | 2026-05-17 |
| Status | Draft |
| Product Owner | Chandramouli |

---

## Table of Contents

| # | Section |
|---|---------|
| 0 | [Context](#0-context) — what we tried, what we kept, what we dropped |
| 1 | [Executive Summary](#1-executive-summary) — Vision, Problem Statement, Target Users |
| 2 | [Goals & Non-Goals](#2-goals--non-goals) |
| 3 | [User Stories](#3-user-stories) — US-001 through US-008 |
| 4 | [Functional Requirements](#4-functional-requirements) |
| 4.1 | &nbsp;&nbsp;[Project CRUD](#41-project-crud) — PRJ-001 through PRJ-017 |
| 4.2 | &nbsp;&nbsp;[Request → Project Assignment](#42-request--project-assignment) — PA-001 through PA-010 |
| 4.3 | &nbsp;&nbsp;[Projects UI](#43-projects-ui) — PUI-001 through PUI-006 |
| 5 | [Data Model](#5-data-model) |
| 5.1 | &nbsp;&nbsp;[New `projects` table](#51-new-projects-table) |
| 5.2 | &nbsp;&nbsp;[Migration for `requests` table](#52-migration-for-requests-table) |
| 5.3 | &nbsp;&nbsp;[Pydantic models](#53-pydantic-models) |
| 5.4 | &nbsp;&nbsp;[Project templates config](#54-project-templates-config) |
| 6 | [API Surface](#6-api-surface) |
| 7 | [UI Design](#7-ui-design) |
| 7.1 | &nbsp;&nbsp;[New Request form — addition](#71-new-request-form-command-center--addition) |
| 7.2 | &nbsp;&nbsp;[Projects list page (`/projects`)](#72-projects-list-page-projects) |
| 7.3 | &nbsp;&nbsp;[Project detail (`/projects/<id>`)](#73-project-detail-projectsid) |
| 8 | [Workflow Integration](#8-workflow-integration) |
| 9 | [Migration & Backfill](#9-migration--backfill) — MIG-001 through MIG-004 |
| 10 | [Permissions (RBAC)](#10-permissions-rbac) |
| 11 | [Edge Cases](#11-edge-cases) |
| 12 | [Out of Scope (v1)](#12-out-of-scope-v1) |
| 13 | [Open Questions](#13-open-questions) |
| 14 | [Implementation Phases](#14-implementation-phases) |
| 15 | [Revision History](#15-revision-history) |

---

## 0. Context

We previously built (and reverted) an "auto-assign requests to projects by
keyword similarity" prototype. The auto-assignment was opaque: a user
couldn't see why a request landed in a given project, and the only visible
UI was a new Projects sidebar entry. **This PRD takes the opposite approach
— explicit project creation by the user, explicit assignment at request
submit time, project association surfaced everywhere requests appear.**

Lessons we're carrying forward:
- The `projects` table schema we built before (project_id, name,
  description, keywords, timestamps) is a fine starting point.
- The Projects list + detail pages we built (`/projects`, `/projects/:id`)
  are reusable shells.
- What we drop: keyword-similarity matching, agent prompt "MERGE MODE"
  for PRD/stories, the UPSERT logic for project-scoped documents.
  These added complexity without clear user-perceived value.

---

## 1. Executive Summary

### 1.1 Vision

Let users group related agent requests under a named project so a body of
work (a product, an initiative, a bug-hunting sprint) has a single home.
Every request — feature, bug, task — is tied to exactly one project.
Project pages aggregate cost, status, agent activity, and outputs so
users can answer questions like "what has the Themes project produced
this week?" without scrolling through a flat request list.

### 1.2 Problem Statement

- **Flat request list.** Today every request lives in a single chronological
  list in Command Center + History. With 50+ requests it becomes hard to
  separate "Themes work" from "Prompt Studio work" from "supervisor bug
  fixes."
- **No grouped cost / time / quality view.** Token spend rolls up
  per-request only. Asking "how much did the Themes effort cost across 5
  requests?" needs SQL.
- **No project-level documentation home.** PRDs, user stories, and outputs
  exist per-request and are reused via keyword search, but there's no
  single place that says "the Themes project's current scope is X."
- **Hard to onboard.** A new collaborator opening Command Center sees a
  flat history with no structure. Projects give the platform a
  navigable taxonomy.

### 1.3 Target Users

| Role | Primary use of projects |
|------|-------------------------|
| Admin | Creates projects; assigns / reassigns requests; archives stale ones |
| Developer | Submits requests against an existing project; reads project status |
| Viewer | Browses projects to see what's shipping; reads per-project artifacts |

---

## 2. Goals & Non-Goals

### Goals

- **G1.** A user can create, edit, archive, and (admin-only) hard-delete a project.
- **G2.** Every new request is assigned to exactly one project at submit time.
- **G3.** Project association is visible everywhere a request is — Command
  Center cards, RequestDetail, StoryBoard breadcrumb, History list.
- **G4.** A project detail page rolls up every request, cost, status, and
  output for that project.
- **G5.** Backfill: every existing request keeps working (assigned to a
  default "Unassigned" project) until manually reassigned.

### Non-Goals (Explicit)

- **NG1.** No automatic assignment by keyword similarity. User picks.
- **NG2.** No merged/evolving PRD or user-stories document per project.
  Each request still produces its own PRD + stories. (The previous
  attempt at this failed; revisit later if there's demand.)
- **NG3.** No multi-project requests. A request belongs to exactly one
  project — keep the model simple.
- **NG4.** No project-level permissions / sharing model. Every
  authenticated user sees every project. RBAC stays at the request
  level (per existing user roles).
- **NG5.** No GitHub-org-style nesting or sub-projects.

---

## 3. User Stories

**US-001 — Create a project**
> *As a developer*, I want to create a new project with a name and
> description, so that I can group future requests under it.

**US-002 — Submit a request into a project**
> *As a developer*, I want to pick a project from a dropdown on the New
> Request form, so that the request is filed under the right work
> stream.

**US-003 — Create a project inline**
> *As a developer*, I want a "+ New project" option in the project
> dropdown on the request form, so that I don't have to leave the page
> to file a request for a new initiative.

**US-004 — Browse projects**
> *As a user*, I want a Projects page listing every project with quick
> stats (request count, active requests, total cost, last activity), so
> that I can navigate by work stream instead of by chronological
> request.

**US-005 — See a project's activity**
> *As a user*, I want a project detail page that lists every request
> in the project (with status), aggregate cost, and shortcut links to
> outputs, so that I can answer "what's the state of this project?"
> without opening 10 request pages.

**US-006 — Reassign a request**
> *As an admin*, I want to change a request's project after the fact,
> so that I can correct misfilings.

**US-007 — Archive a project**
> *As an admin*, I want to archive a project that's no longer active,
> so that it stops appearing in default dropdowns/lists without losing
> its history.

**US-008 — Filter by project**
> *As a user*, I want to filter Command Center and History by project,
> so that I can see only the requests I care about right now.

---

## 4. Functional Requirements

### 4.1 Project CRUD

A project is a rich metadata container, not just a name + description.
Every Create Project form (modal or page) collects all of the following
in one step.

| ID | Requirement | Priority |
|----|-------------|----------|
| PRJ-001 | A project has: `project_id` (UUID `proj-XXXXXXXX`), `name` (req, ≤80 chars), `description` (≤500 chars), `status` (`active` \| `archived`), `color` (hex from preset palette), `icon` (lucide icon name from preset set), `tags` (JSON list, ≤10 entries, each ≤25 chars), `lead_user_id` (FK users, defaults to creator), `repo_url` (optional URL, ≤300 chars), `default_team` (`engineering` \| `research` \| `content` \| `null`), `target_date` (optional ISO date), `template_id` (optional, refs a preset template), `created_by`, `created_at`, `updated_at`. | Critical |
| PRJ-002 | `POST /api/v1/projects` accepts the full field set above. `name` is required; everything else has a sensible default (lead=caller, color=`#00f0ff`, icon=`folder`, tags=`[]`, default_team=`null`, template_id=`null`). Returns the created project. | Critical |
| PRJ-003 | `GET /api/v1/projects` returns all projects (default: active only; `?include_archived=true` to also return archived). | Critical |
| PRJ-004 | `GET /api/v1/projects/{id}` returns the project, its request list (id/desc/status/created_at), aggregate stats (request count by status, total cost USD, last activity timestamp), recent documents, plus the template's starter-checklist (if a template was picked) so the project page can render "next steps" guidance. | Critical |
| PRJ-005 | `PATCH /api/v1/projects/{id}` updates any user-facing field (name/description/color/icon/tags/lead/repo_url/default_team/target_date/status). Any authenticated user can update; **admin-only** to flip status to/from archived OR to reassign `lead_user_id` to someone other than themselves. | High |
| PRJ-006 | `DELETE /api/v1/projects/{id}` is **admin-only** and requires the project to be empty (no requests). Otherwise returns 409 with the request count, forcing the admin to first reassign or delete the requests. | High |
| PRJ-007 | Project names must be unique among non-archived projects (case-insensitive). Archived projects can share a name with a new active one. | Medium |
| PRJ-008 | The system seeds one project named "Unassigned" on first boot. It cannot be renamed, archived, deleted, or have its color/icon/lead changed. It's the default destination for requests created before this feature shipped and for any request whose `project_id` becomes orphaned. | Critical |
| PRJ-009 | **Color palette** is a fixed set of 8 preset swatches (cyan `#00f0ff`, pink `#ff2a6d`, green `#39ff14`, yellow `#f9f871`, orange `#ff8c00`, purple `#b026ff`, blue `#0070f3`, gray `#8080a0`). No free hex picker — keeps the palette consistent with the cyberpunk theme accents. | Medium |
| PRJ-010 | **Icon set** is a fixed set of 8 lucide icons: `folder`, `rocket`, `layers`, `code`, `flask-conical`, `palette`, `bug`, `book-open`. Easy to add more later; closed for v1. | Medium |
| PRJ-011 | **Tag input** auto-lowercases tags, strips whitespace, dedupes within a project, enforces the 10-tag / 25-char-per-tag limits. Tags are NOT globally unique (two projects can both tag `themes`). | Medium |
| PRJ-012 | **Lead user picker** dropdown shows all users with `developer` or `admin` role. `viewer` users are excluded. Defaults to the project creator. Searchable if the user list exceeds 10 entries. | Medium |
| PRJ-013 | **Repo URL** validated as a well-formed URL (`https://` required). If a GitHub URL is detected (`github.com/<owner>/<repo>` pattern), the project page renders a "View on GitHub" button that opens it in a new tab. | Medium |
| PRJ-014 | **Target date** optional, must be ≥ today if provided. Surfaced on the project detail page; once past, the date renders red with an "Overdue" pill. Project doesn't auto-archive on reaching the date — that's a separate explicit action. | Medium |
| PRJ-015 | **Default team** pre-selects the team in the New Request form when the user files a request against this project. `null` (no default) means use the global default (Engineering). | Medium |
| PRJ-016 | **Templates** are loaded from `config/project_templates.yaml` at startup. Each template defines: `id`, `name`, `description`, `starter_checklist` (list of suggested requests with `description`, `task_type`, `priority`). v1 ships 5 templates: `empty`, `web_feature`, `research_initiative`, `content_project`, `bug_sprint`. | Medium |
| PRJ-017 | When a template is picked at create time, the starter_checklist is rendered as a "Next steps" panel on the project detail page — clickable items pre-fill the New Request form with the template's description / task_type / priority but require user confirmation before submitting. Checklist items render dimmed once a matching request has been filed (matched by template + description hash). | Medium |

### 4.2 Request → Project Assignment

| ID | Requirement | Priority |
|----|-------------|----------|
| PA-001 | The `requests` table gains a non-null `project_id` FK to `projects`. Default value: the "Unassigned" project's id. | Critical |
| PA-002 | New Request form: a Project dropdown is required, defaults to the user's most-recently-used active project (per-user localStorage), with all active projects listed (rendered with color swatch + icon + name) + an inline "+ New project" option at the bottom. When a project with a `default_team` is selected, the Team selector pre-fills accordingly. | Critical |
| PA-003 | The "+ New project" option opens the full Create Project modal — every field from PRJ-001 (name, description, color, icon, tags, lead, repo URL, default team, target date, template). Name is required; everything else has a default. On save: create the project, select it in the dropdown, close modal. No page reload. | High |
| PA-004 | `POST /api/v1/requests` accepts `project_id` in the body. Returns 400 if the project doesn't exist or is archived. Returns 400 if missing. | Critical |
| PA-005 | RequestDetail page header: shows "Project: <name>" with a link to `/projects/<id>`. | Critical |
| PA-006 | StoryBoard breadcrumb: shows `Command Center ▸ <project name> ▸ REQ-XXX`. | High |
| PA-007 | History page: adds a "Project" column (project name, clickable to /projects/<id>) and a Project filter dropdown in the toolbar. | High |
| PA-008 | Command Center "Active Requests" cards show a small `<project name>` chip beside the REQ-id, clickable to the project page. | High |
| PA-009 | An admin can change a request's `project_id` via `PATCH /api/v1/requests/{id}` (existing endpoint, just add the field). Non-admins cannot reassign. | High |
| PA-010 | If the project being reassigned to is archived: API returns 400 unless the caller passes `?allow_archived=true` (intentional override for cleanup work). | Medium |

### 4.3 Projects UI

| ID | Requirement | Priority |
|----|-------------|----------|
| PUI-001 | `/projects` lists projects in a table/card grid: name, description (truncated), request count, active count, total cost, last activity timestamp, status badge (active/archived). Default sort: last activity desc. | Critical |
| PUI-002 | Top of `/projects` has a "+ New Project" button (modal — same modal used in PA-003) and a status filter (Active / Archived / All). | Critical |
| PUI-003 | `/projects/<id>` shows: project name, description (inline editable for any user, but archive/unarchive only for admin), 4 stat cards (total requests, active, completed, total cost USD), a list of all requests in the project (same row shape as History), and a "Recent Documents" panel listing the latest 10 docs produced by any request in this project. | Critical |
| PUI-004 | `/projects/<id>` "Submit a request to this project" button opens the same request form as Command Center, but with the project pre-selected. | High |
| PUI-005 | Sidebar gets a Projects entry (between Command Center and Prompt Studio). Same visual style as other nav items. | High |
| PUI-006 | Project status badge: Active = cyan, Archived = muted gray. Render using the existing StatusBadge component for consistency. | Medium |

---

## 5. Data Model

### 5.1 New `projects` table

```
projects
├─ project_id        TEXT PRIMARY KEY     (e.g. "proj-7f8a2c3d")
├─ name              TEXT NOT NULL
├─ description       TEXT DEFAULT ''
├─ status            TEXT NOT NULL DEFAULT 'active'    -- active | archived
├─ color             TEXT NOT NULL DEFAULT '#00f0ff'   -- one of the 8 preset hex codes (PRJ-009)
├─ icon              TEXT NOT NULL DEFAULT 'folder'    -- lucide icon name from preset set (PRJ-010)
├─ tags              TEXT NOT NULL DEFAULT '[]'        -- JSON array of strings
├─ lead_user_id      TEXT                              -- FK users(user_id)
├─ repo_url          TEXT DEFAULT ''                   -- optional, validated URL
├─ default_team      TEXT                              -- engineering | research | content | NULL
├─ target_date       TIMESTAMP                         -- optional ISO date
├─ template_id       TEXT                              -- optional, refs config/project_templates.yaml
├─ created_by        TEXT                              -- user_id
├─ created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
├─ updated_at        TIMESTAMP
└─ INDEX (status, name), INDEX (lead_user_id)
```

Uniqueness of `name` among active projects is enforced at the
application layer (PRJ-007) so an archived project can be replaced by a
new active one with the same name without a UNIQUE-constraint conflict.

### 5.2 Migration for `requests` table

```sql
ALTER TABLE requests ADD COLUMN project_id TEXT;
-- Backfill: every existing request → "Unassigned"
UPDATE requests SET project_id = (SELECT project_id FROM projects WHERE name = 'Unassigned') WHERE project_id IS NULL;
-- New constraint (SQLite allows this via table recreation if strict NOT NULL is wanted; for v1, leave as nullable with app-layer enforcement)
CREATE INDEX idx_requests_project ON requests(project_id);
```

### 5.3 Pydantic models

```python
class Project(BaseModel):
    project_id: str
    name: str
    description: str = ""
    status: Literal["active", "archived"] = "active"
    # Identity / visual
    color: str = "#00f0ff"               # one of the 8 preset hex codes
    icon: str = "folder"                 # one of the 8 preset lucide icon names
    tags: list[str] = Field(default_factory=list)  # max 10 entries, lowercase
    # Ownership / context
    lead_user_id: str | None = None      # defaults to created_by
    repo_url: str = ""                   # optional URL
    default_team: Literal["engineering", "research", "content"] | None = None
    target_date: datetime | None = None  # optional, must be >= today
    template_id: str | None = None       # one of: empty | web_feature | research_initiative | content_project | bug_sprint
    # Audit
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

class Request(BaseModel):
    # ... existing fields ...
    project_id: str  # required, default at API layer = "Unassigned" project's id
```

### 5.4 Project templates config

`config/project_templates.yaml` ships with 5 templates. Schema:

```yaml
templates:
  - id: empty
    name: Empty
    description: A blank project. You'll file requests one at a time.
    starter_checklist: []
  - id: web_feature
    name: Web Feature
    description: A user-facing feature with PRD, frontend implementation, and a bug-fix slot.
    starter_checklist:
      - description: "Write the PRD for <feature name>"
        task_type: feature_request
        priority: high
      - description: "Build the frontend for <feature name>"
        task_type: feature_request
        priority: high
      - description: "Bug fixes for <feature name>"
        task_type: bug_report
        priority: medium
  - id: research_initiative
    ...
```

The orchestrator does not auto-submit these — they render as a
"Next steps" checklist on the project detail page; clicking an item
opens the New Request form pre-filled (PRJ-017).

---

## 6. API Surface

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST`   | `/api/v1/projects` | any | Create project |
| `GET`    | `/api/v1/projects?include_archived=false` | any | List projects |
| `GET`    | `/api/v1/projects/{id}` | any | Project detail + rollups |
| `PATCH`  | `/api/v1/projects/{id}` | any (admin to archive) | Update name/desc/status |
| `DELETE` | `/api/v1/projects/{id}` | admin | Hard-delete (rejected if non-empty) |
| `POST`   | `/api/v1/requests` | any | Existing — gains required `project_id` field |
| `PATCH`  | `/api/v1/requests/{id}` | any (admin to reassign) | Existing — gains `project_id` |
| `GET`    | `/api/v1/requests?project_id=...` | any | Existing — gains filter param |

---

## 7. UI Design

### 7.1 New Request form (Command Center) — addition

```
┌────────────────────────────────────────────────────────┐
│ Describe what you want to build...                     │
│                                                        │
├────────────────────────────────────────────────────────┤
│ Project: [ Themes UI Redesign        ▼ ]   ← REQUIRED  │
│          ├─ ★ Themes UI Redesign  (last used)          │
│          ├─ Supervisor Hardening                       │
│          ├─ Prompt Studio                              │
│          ├─ Unassigned                                 │
│          ├─ ─────────────                              │
│          └─ + New project...                           │
│                                                        │
│ Team:  [ Engineering ] [ Research ] [ Content ]        │
│ Type:  [ Feature ▼ ]   Priority: [ High ] [ Med ] ...  │
│                                          [ DISPATCH ]  │
└────────────────────────────────────────────────────────┘
```

Inline "New Project" modal (full v1 field set):
```
┌─── New Project ─────────────────────────────────────────────┐
│                                                             │
│  Identity                                                   │
│  Name *      [ Themes UI Redesign                    ] 19/80│
│  Description [ Cyberpunk theme + sidebar refactor    ]      │
│              [ + page theme fixes across History ... ]  77/500│
│                                                             │
│  Color   ● ● ● ● ● ● ● ●   (cyan selected — 8 swatches)     │
│  Icon    📁 🚀 🗂 </>  ⚗ 🎨 🐞 📖   (folder selected)         │
│  Tags    [ themes ×] [ ui ×] [ + add tag        ]    2/10   │
│                                                             │
│  Ownership                                                  │
│  Lead    [ chandramouli (you)                       ▼ ]     │
│  Repo    [ https://github.com/cmuthu2503-ai/...     ]       │
│                                                             │
│  Workflow defaults                                          │
│  Default team   ( ) Engineering  ( ) Research  ( ) Content  │
│                 (●) No default                              │
│  Target date    [ 2026-06-30                       📅 ]     │
│                                                             │
│  Template   [ Empty (no starter checklist)          ▼ ]     │
│             ├─ Empty                                        │
│             ├─ Web Feature (PRD + frontend + bug)           │
│             ├─ Research Initiative (3 research requests)    │
│             ├─ Content Project (slide deck + report)        │
│             └─ Bug Sprint (5 bug-report placeholders)       │
│                                                             │
│                                    [ Cancel ]  [  Create  ] │
└─────────────────────────────────────────────────────────────┘
```

Validation rules surface inline (red border + helper text under field):
- Name empty / >80 chars / collides with active project
- Tag >25 chars / >10 tags
- Repo URL malformed (must include `https://`)
- Target date in the past

### 7.2 Projects list page (`/projects`)

Each row leads with the project's color stripe + icon + name; tags
render as small chips after the description; the lead's avatar/initials
sit on the right with the stats line.

```
> PROJECTS                                       [ + New Project ]
  ┌──────────────────────────────────────────────────────────────┐
  │ Status: [ Active ▼ ]   Sort: [ Last activity ▼ ]             │
  ├──────────────────────────────────────────────────────────────┤
  │ ┃ 📁  Themes UI Redesign            [themes] [ui]      [CM]  │
  │ │   Cyberpunk theme + sidebar + page theme fixes ...         │
  │ │   12 reqs · 3 active · $4.27 · target 2026-06-30 · 3h ago  │
  │ ━━ (cyan stripe — project color)                             │
  ├──────────────────────────────────────────────────────────────┤
  │ ┃ 🐞  Supervisor Hardening          [supervisor]       [CM]  │
  │ │   Windows portability + judge LLM + rollback fixes         │
  │ │   8 reqs · 0 active · $1.89 · no target date · 1d ago      │
  │ ━━ (yellow stripe)                                           │
  └──────────────────────────────────────────────────────────────┘
```

### 7.3 Project detail (`/projects/<id>`)

```
┃ 📁 THEMES UI REDESIGN                        [ Submit Request → ]
  Cyberpunk theme + sidebar refactor + page theme fixes
  proj-7f8a2c3d  ·  created 2026-05-12 by chandramouli
  Lead: chandramouli   ·   Tags: [themes] [ui]
  GitHub: github.com/cmuthu2503-ai/claude-agent-team [↗]
  Target: 2026-06-30 (44 days)

  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
  │ TOTAL    │ │ ACTIVE   │ │ COMPLETED│ │ COST USD │
  │   12     │ │   3      │ │    8     │ │  $4.27   │
  └──────────┘ └──────────┘ └──────────┘ └──────────┘

  ★ Next Steps (Web Feature template) ────────────────
  ☑  Write the PRD for <feature name>   (filed as REQ-XXX)
  ☐  Build the frontend for <feature name>
  ☐  Bug fixes for <feature name>

  Requests ───────────────────────────────────────────
  REQ-54BC91   Refer to the theme — ...   completed
  REQ-3EED7F   In prompt studio page, ... completed
  ...

  Recent Documents ───────────────────────────────────
  📄 PRD v1     Refer to the theme...   2d ago
  📄 Stories    User stories for...     2d ago
```

The "Next Steps" panel only renders when a template was selected at
create time (PRJ-017). Checked items are matched by template+description
to filed requests; unchecked items remain clickable to pre-fill the
New Request form.

---

## 8. Workflow Integration

- **Orchestrator (`src/core/orchestrator.py::submit`)**: accepts
  `project_id` in the submit call; validates it exists + is not archived;
  attaches to the `Request` row. No change to workflow logic — projects
  are pure metadata.
- **Events**: emit `project.created`, `project.updated`,
  `project.archived` so the WebSocket activity feed and audit log
  capture project lifecycle. `request.created` payload gains
  `project_id` so the frontend can update Project page badges live.
- **Cost endpoint (`/api/v1/cost/dashboard`)**: add an optional
  `?project_id=` filter so users can scope cost rollups by project.

---

## 9. Migration & Backfill

| ID | Requirement | Priority |
|----|-------------|----------|
| MIG-001 | First-boot migration: create the "Unassigned" project if it doesn't exist. | Critical |
| MIG-002 | Backfill all existing requests with `project_id = <Unassigned project's id>`. | Critical |
| MIG-003 | Provide a one-shot CLI script (`scripts/backfill_projects.py`) that lets an admin retroactively bulk-assign existing requests to real projects (e.g. by keyword match against a list of candidate names). The script writes a dry-run report first, the admin approves, then it applies. | Medium |
| MIG-004 | The frontend's request list must gracefully render requests with `project_id` pointing at a missing (deleted) project: show "Unassigned" inline and surface a warning in the admin log. (Shouldn't happen — DELETE requires empty project — but defensive guard.) | Low |

---

## 10. Permissions (RBAC)

| Action | Viewer | Developer | Admin |
|---|---|---|---|
| List projects | ✓ | ✓ | ✓ |
| View project detail | ✓ | ✓ | ✓ |
| Create project | ✗ | ✓ | ✓ |
| Edit name/description | ✗ | ✓ | ✓ |
| Archive / unarchive | ✗ | ✗ | ✓ |
| Hard-delete (empty project only) | ✗ | ✗ | ✓ |
| Submit request into existing project | ✗ | ✓ | ✓ |
| Reassign a request to another project | ✗ | ✗ | ✓ |

---

## 11. Edge Cases

| # | Case | Behavior |
|---|------|----------|
| 1 | User picks "+ New project" but cancels the modal | No project created; original dropdown selection restored |
| 2 | User submits request, project gets archived between dropdown render and submit | API returns 400; UI re-fetches projects and shows the dropdown with a "(this project was just archived)" note next to the stale option |
| 3 | Admin tries to delete a project with active requests | API returns 409 with `{ "error": "project not empty", "request_count": N }`; UI shows "This project has N requests. Reassign or delete them first." |
| 4 | Two users create projects with the same name simultaneously | Second `POST` returns 409; UI shows "A project with this name already exists." |
| 5 | Project name with only whitespace | API trims and rejects empty as 400 |
| 6 | Description exceeds 500 chars | API truncates with a 200 + warning header, OR returns 400 — pick one. Recommend 400 (force the user to trim). |
| 7 | "Unassigned" project tampering attempt (rename via PATCH) | API returns 403 |
| 8 | Request created via legacy code path (no `project_id`) | API defaults to "Unassigned"; logs a `legacy_request_no_project_id` warning so we can find the offending caller |
| 9 | Project page polling: project gets renamed in another tab | Polling picks up the new name on next refresh (5s); no live event needed |

---

## 12. Out of Scope (v1)

Pulled INTO v1 (was out, now in): color, icon, tags, lead user, repo URL,
default team, target date, project templates. See PRJ-009 through PRJ-017
above for the requirements.

Still out of scope:

- Project-level RBAC (per-project read/write roles, sharing model — RBAC stays at the global user role level)
- Sub-projects / nested hierarchies
- Project-level Slack/email notifications
- Cross-project dependencies / linked requests (a request belongs to exactly one project; no "blocks/blocked-by" between projects)
- Project archive auto-expiry (no time-based auto-archiving; admin must archive explicitly)
- Bulk reassignment UI (one-at-a-time via PATCH is fine for v1; a multi-select UI can come later)
- Free-form color hex picker (v1 ships with 8 preset swatches only — keeps the palette consistent)
- Custom / uploaded icons (v1 ships with 8 preset lucide icons only)
- Tag taxonomies / autocomplete from a global tag pool (v1 tags are per-project free-form strings)
- Editing the starter checklist after project creation (template selection is at-create-time only; you can ignore items but not add new ones to the project's checklist — file ad-hoc requests instead)

---

## 13. Open Questions

1. **"Unassigned" presentation.** Always at the bottom of the dropdown
   (intuitive), or hidden unless explicitly toggled (cleaner)? I lean
   toward visible-at-bottom-with-italic-styling to keep things
   discoverable.
2. **Last-used persistence.** Per-browser (localStorage) or
   server-side per-user? Per-user is better UX across devices but adds
   a new DB column. v1: localStorage; v1.1 if needed.
3. **Project descriptions as markdown?** Reuse `MarkdownRenderer` if
   yes; plain text if no. Recommend plain text for v1 (simpler) —
   upgrade later if we add longer-form description fields.
4. **Soft-deleting archived projects.** Should "archived" be reversible
   forever, or auto-purge after N days? Reversible-forever for v1.
5. **Cost rollup performance.** `GET /api/v1/projects/{id}` sums
   token_usage across all requests in the project. For projects with
   100+ requests this is fine. Beyond 1000, consider a materialized
   `projects.cached_cost_usd` column updated on agent completion.
6. **Migration safety.** The "Unassigned" project must exist before
   `requests.project_id` becomes NOT NULL — sequence carefully in the
   migration script. v1: leave column nullable to avoid the dance.

---

## 14. Implementation Phases

Expanded scope: ~3 days split into 3 PRs.

**Phase 1 — Backend (~8 hours)**
- `projects` table migration with full v1 column set (color, icon, tags,
  lead_user_id, repo_url, default_team, target_date, template_id)
- `project_id` on `requests`, backfill into "Unassigned"
- `Project` Pydantic model + StateStore CRUD with validation (color
  must be in preset palette, icon in preset set, tags ≤10 each ≤25
  chars, repo_url well-formed, target_date in future)
- `/api/v1/projects` routes (POST/GET/PATCH/DELETE)
- `config/project_templates.yaml` loader; expose templates via
  `GET /api/v1/projects/templates`
- Orchestrator `submit()` accepts `project_id`; validates exists + active

**Phase 2 — Frontend Create + List + Detail (~10 hours)**
- `Projects.tsx` list with color stripe + icon + tags chips + lead avatar
- `ProjectDetail.tsx` with metadata header, stat cards, Next-Steps
  checklist (when template selected), request list, recent documents
- Create Project modal — every PRJ-001 field with the right input type
  (text, textarea, color swatch grid, icon grid, tag input, user
  dropdown, URL input, radio group, date picker, template dropdown);
  inline validation per the rules in §7.1
- Sidebar Projects entry
- New Request form: project dropdown (required) + inline "+ New project"
  option that opens the same full Create Project modal

**Phase 3 — Project surfacing everywhere (~4 hours)**
- Project chip (color + icon + name) on Command Center request cards
- Project column + filter on History
- "Project: <name>" line on RequestDetail header
- StoryBoard breadcrumb: `Command Center ▸ <project> ▸ REQ-XXX`
- Cost dashboard: optional `?project_id` filter
- WebSocket events for `project.created` / `project.updated` so the
  Projects list updates without a refresh when someone creates elsewhere

---

## 15. Revision History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-05-17 | Chandramouli | Initial draft — explicit project creation + manual assignment + project-level rollup pages. Carries forward schema from the reverted auto-assign prototype; drops keyword matching + agent MERGE MODE + document UPSERT (kept simple). |
| 1.1 | 2026-05-17 | Chandramouli | Scope expansion: every field previously in "Out of Scope (v1.1)" pulled into v1 — color (PRJ-009), icon (PRJ-010), tags (PRJ-011), lead user (PRJ-012), repo URL (PRJ-013), target date (PRJ-014), default team (PRJ-015), templates with starter checklist (PRJ-016, PRJ-017). Data model expanded with corresponding columns. Create modal mockup rewritten to show all v1 fields. Implementation estimate bumped 1.5 days → ~3 days. Still-out-of-scope list reduced to true v2 items (per-project RBAC, sub-projects, notifications, cross-project linking, auto-archive, bulk reassign, free-form colors/icons, tag taxonomies). |
| 1.2 | 2026-05-17 | Chandramouli | Added Table of Contents at the top — top-level sections 0–15 plus all subsections (4.1–4.3, 5.1–5.4, 7.1–7.3) since this PRD has dense subsection structure throughout. GitHub-flavored anchor links for jump navigation. |
| 1.3 | 2026-05-19 | Chandramouli | New Section 16 "Project Workspaces" — projects now own GitHub repos. Capabilities WS-01..WS-20 ship the auto-create repo flow at project creation, per-project routing of code commits and research artifacts, and a manual backfill button for existing repo-less projects. Adds new env var `PROJECT_WORKSPACES_DIR` (default `project-workspaces/`), new endpoint `POST /projects/:id/create_repo`, `extract_owner_repo` helper, `GitHubPublisher.create_repo` + per-call `repo=` on `commit_files`, `project_repo_slug` validator. Smoke-tested live: CrewAITeam → repo `cmuthu2503-ai/crewaiteam` → research artifacts pushed at commit ec9cdfa3. |

---

## 16. Project Workspaces (WS — v1.3)

### 16.1 Vision

A project is no longer just a label on database rows — it owns a private
GitHub repo. Everything the platform produces for a project (research
artifacts now; code commits next; deploy stack maybe later) lands in that
repo, not in the platform's own repo. The result: cloning the project's
repo gives you the whole project, just like a normal git project.

This is the practical answer to "where is my new project physically
stored?" — in its own GitHub repo.

### 16.2 Three capabilities (all shipped in v1.3)

| Capability | What ships | REQ group |
|---|---|---|
| **1. Auto-create repo** | New project triggers `POST /user/repos` (private, auto_init=true) under the GITHUB_TOKEN user's namespace. Repo URL goes into `project.repo_url`. | WS-01..WS-06, WS-16 |
| **2. Code commits route to project repo** | `_handle_code_commit` resolves request → project → repo_url → `(owner, name)` and passes it as the publish target. Falls back to `GITHUB_REPO` env when the project has no repo (e.g. the platform itself). | WS-07..WS-11 |
| **3. Research artifacts route to project repo** | `_handle_publish` resolves the same way. Local artifacts materialize at `project-workspaces/<slug>/docs/research/<request_folder>/`. GitHub commit goes to the project's repo at `docs/research/<request_folder>/` (no inner slug duplication). | WS-12..WS-15 |

**Capability 4 (per-project supervisor / Docker stack) is intentionally NOT
in v1.3.** Deferred — the platform doesn't deploy your projects for you;
clone the project's repo and run it however you want.

### 16.3 New API surface

- `POST /api/v1/projects` body now accepts `create_repo: bool` (default
  `true`). When true AND `repo_url` is blank, the server creates a private
  GitHub repo named `project_repo_slug(name)` and writes the resulting
  HTML URL back to `repo_url`.
- `POST /api/v1/projects/:id/create_repo` — backfill endpoint for
  existing projects whose `repo_url` is empty. 409 if the project already
  has a `repo_url`. Use a `PATCH` to clear it first if you really want to
  re-bind.

### 16.4 New env vars

- `GITHUB_PROJECT_ORG` (optional) — if set, repos are created under that
  org via `POST /orgs/{org}/repos`. Otherwise under the GITHUB_TOKEN
  user via `POST /user/repos`.
- `PROJECT_WORKSPACES_DIR` (optional, default `project-workspaces/`) —
  root directory under the platform repo for materialized per-project
  artifact mirrors. `.gitignore`d from the platform repo.

### 16.5 Failure modes

| Situation | Behavior |
|---|---|
| `GITHUB_TOKEN` not set | Project creation succeeds with `repo_url=""`. User can backfill later via §16.6 button once the env var is set. |
| Token lacks `repo` scope | `POST /projects` returns **403** with `{error: "github_repo_create_unauthorized", hint: "..."}`. Project is NOT created — fail before insert. |
| Repo name already taken in namespace | **422** with `{error: "github_repo_name_taken", hint: "Pick a different project name."}`. Project not created. |
| Network failure to GitHub | **502** with `{error: "github_repo_create_failed", status, message}`. Project not created. |
| Research artifact push fails after repo create | Soft-fail — files stay locally at `project-workspaces/<slug>/...`, `publish_error` reported in the workflow result. The Request still completes. |

### 16.6 Existing repo-less projects (backfill)

Three projects existed before WS-01 shipped: Agent Team (legitimately
points at the platform repo), TestAITeam (test artifact), CrewAITeam (the
user's first real new project). Anyone with `repo_url=""` can backfill
via the **"Create GitHub Repo"** button on the Project Detail page
(visible only when `repo_url` is empty, not shown for proj-unassigned).
Internally hits `POST /projects/:id/create_repo`.

Bulk backfill via `scripts/backfill_project_repos.py` is deferred — three
projects fit on two hands.

### 16.7 Design choices worth flagging

- **Repo deletion is NOT cascaded.** Deleting a project removes its DB
  rows but leaves the GitHub repo alone. Treating external resources as
  "owned by user, lives forever" matches how `repo_url` works for
  user-pasted URLs.
- **No git clones** — `project-workspaces/<slug>/` is a plain directory
  mirror, not a `git clone` of the project's repo. The platform writes
  artifacts there, then pushes via the GitHub Trees API. The local mirror
  is gitignored. Promotes to full clones later if Capability 4 ships.
- **Slug derived from project name** — `project_repo_slug(name)`
  lowercases, replaces whitespace/dots/underscores with dashes, strips
  non-alphanumeric, collapses runs of dashes, caps at 100 chars. Two
  projects whose names slug to the same value will fail at GitHub with a
  422 (per agreed collision policy).
- **`project_repo_slug` runs in both Python and TypeScript** — backend
  is canonical; frontend has a hand-mirrored implementation for the live
  preview in `CreateProjectModal`. Any drift is corrected server-side.
