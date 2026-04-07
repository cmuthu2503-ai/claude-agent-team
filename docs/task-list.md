# Implementation Task List
# Agent Team — Complete Development Tracker

---

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 1.0 |
| Created Date | 2026-04-05 |
| Last Updated | 2026-04-05 |
| Status | Draft |
| Product Owner | Chandramouli |

---

## How to Use This Document

- Tasks are grouped into **8 phases**, ordered by dependency
- Each task has a unique ID: `P{phase}-T{number}` (e.g., P1-T03)
- **Status tracking**: Update the Status column as work progresses
- **Dependencies**: A task cannot start until all listed dependencies are done
- Effort: **S** = hours, **M** = 1-2 days, **L** = 3-5 days, **XL** = 1+ week

### Status Legend

| Status | Meaning |
|--------|---------|
| `[ ]` | Not started |
| `[~]` | In progress |
| `[x]` | Completed |
| `[!]` | Blocked |
| `[-]` | Skipped / Deferred |

---

## Progress Summary

| Phase | Total Tasks | Done | In Progress | Blocked | Not Started |
|-------|------------|------|-------------|---------|-------------|
| P0: Project Setup | 16 | 16 | 0 | 0 | 0 |
| P1: Configuration | 14 | 14 | 0 | 0 | 0 |
| P2: Core Engine | 20 | 20 | 0 | 0 | 0 |
| P3: Agent System | 15 | 15 | 0 | 0 | 0 |
| P4: GitHub Integration | 11 | 11 | 0 | 0 | 0 |
| P5: UI Frontend | 26 | 26 | 0 | 0 | 0 |
| P6: Deployment & Demo | 12 | 12 | 0 | 0 | 0 |
| P7: Notifications & Reports | 14 | 14 | 0 | 0 | 0 |
| P8: Testing & QA | 16 | 16 | 0 | 0 | 0 |
| **TOTAL** | **144** | **144** | **0** | **0** | **0** |

---

---

## Phase 0: Project Setup

Foundation — repo, dependencies, project structure.

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| P0-T01 | Initialize Git repository | Create GitHub repo `agent-team-projects/claude-agent-team`, add `.gitignore`, `LICENSE`, initial commit | S | — | `[x]` |
| P0-T02 | Create project structure | Create folder structure: `src/`, `config/`, `tests/`, `docs/`, `demo/`, `reports/` as defined in architecture.md Sec 7 | S | P0-T01 | `[x]` |
| P0-T03 | Setup Python project | Create `pyproject.toml` with project metadata, Python 3.12+ requirement, dependency groups (core, dev, test) | S | P0-T02 | `[x]` |
| P0-T04 | Install core dependencies | Install: `anthropic`, `claude-agent-sdk`, `pydantic`, `pyyaml`, `aiosqlite`, `websockets`, `structlog` | S | P0-T03 | `[x]` |
| P0-T05 | Install dev dependencies | Install: `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `mypy`, `pre-commit` | S | P0-T03 | `[x]` |
| P0-T06 | Setup React frontend | Initialize React + TypeScript project in `frontend/` with Vite, install Tailwind CSS, React Router | M | P0-T02 | `[x]` |
| P0-T07 | Configure linting & formatting | Setup `ruff` for Python, `eslint` + `prettier` for TypeScript, `pre-commit` hooks | S | P0-T04, P0-T06 | `[x]` |
| P0-T08 | Create .env.example | Template with: `ANTHROPIC_API_KEY`, `DATABASE_URL`, `GITHUB_TOKEN` | S | P0-T02 | `[x]` |
| P0-T09 | Install Docker prerequisites | Verify Docker Engine and Docker Compose v2 are installed. Document minimum versions in README. | S | — | `[x]` |
| P0-T10 | Create Dockerfile.backend | Multi-stage Dockerfile for backend: dev stage (hot reload, debug tools) + prod stage (slim, no dev deps). Python 3.12 base image. | M | P0-T03 | `[x]` |
| P0-T11 | Create Dockerfile.frontend | Multi-stage Dockerfile for frontend: dev stage (Vite dev server) + prod stage (build React → nginx serve). | M | P0-T06 | `[x]` |
| P0-T12 | Create docker-compose.yml (local dev) | Local development compose: backend (hot reload via volume mount), frontend (Vite dev server), network `dev-net`. Ports: 8000/3000. Health checks on all services. | M | P0-T10, P0-T11 | `[x]` |
| P0-T13 | Create Makefile | Convenience targets: `make dev` (start local), `make down` (stop), `make build` (rebuild images), `make logs` (tail logs), `make shell-backend` (exec into backend container), `make shell-frontend` (exec into frontend container) | S | P0-T12 | `[x]` |
| P0-T14 | Verify local dev stack boots | Run `make dev`, verify backend/frontend containers start, health checks pass, frontend accessible at localhost:3000, backend API at localhost:8000/health | S | P0-T13 | `[x]` |
| P0-T15 | Add auth & platform dependencies | Add to `pyproject.toml`: `passlib[bcrypt]`, `python-jose[cryptography]`, `slowapi`, `alembic`. These support auth, rate limiting, and database migrations (see cross-cutting-concerns.md). | S | P0-T03 | `[x]` |
| P0-T16 | Add frontend UI libraries | Install shadcn/ui (via CLI), `@tanstack/react-query`, `@tanstack/react-table`, `zustand`, `react-hook-form`, `zod`, `recharts`, `sonner`, `lucide-react`, `openapi-typescript` (dev). See cross-cutting-concerns.md Area 2.6. | M | P0-T06 | `[x]` |

---

## Phase 1: Configuration System

YAML-based config that drives the entire system. Must be done before any agent code.

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| P1-T01 | Create agent template | Write `config/agents/_template.yaml` with full schema (agent_id, role, team, reports_to, responsibilities, tools, outputs, delegation, quality_gates, metadata) | S | P0-T02 | `[x]` |
| P1-T02 | Create Engineering Lead config | Write `config/agents/engineering_lead.yaml` — decomposition, delegation to 3 team leads, Opus 4.6 model | S | P1-T01 | `[x]` |
| P1-T03 | Create Planning team agent configs | Write `config/agents/prd_specialist.yaml` and `config/agents/user_story_author.yaml` | S | P1-T01 | `[x]` |
| P1-T04 | Create Development team agent configs | Write `config/agents/code_reviewer.yaml`, `config/agents/backend_specialist.yaml`, `config/agents/frontend_specialist.yaml` | S | P1-T01 | `[x]` |
| P1-T05 | Create Delivery team agent configs | Write `config/agents/devops_specialist.yaml` and `config/agents/tester_specialist.yaml` | S | P1-T01 | `[x]` |
| P1-T06 | Create teams.yaml | Define 4 teams (engineering, planning, development, delivery) with leads, members, domains, parent_team, hierarchy rules | S | P1-T02 to P1-T05 | `[x]` |
| P1-T07 | Create workflows.yaml | Define 4 workflows: feature_development, bug_fix, documentation_update, demo_preparation — with parallel stages, quality gates, on_fail routing | M | P1-T06 | `[x]` |
| P1-T08 | Create tools.yaml | Define all tools (file_read, file_write, git_operations, github_pr, code_exec, test_runner, deployment, etc.) with role-based `available_to` permissions | S | P1-T04, P1-T05 | `[x]` |
| P1-T09 | Create thresholds.yaml | Define all configurable thresholds: code_coverage_minimum (80%), review_sla (24h), deployment_frequency, demo_test_frequency, stale_branch_age, max_concurrent_tasks, task_timeout | S | — | `[x]` |
| P1-T10 | Create project.yaml | Define project-level config: project name, GitHub org, repo name, default branch, tech stack, deployment provider, environment URLs | S | — | `[x]` |
| P1-T11 | Create JSON schemas for validation | Write JSON Schema files for agent, team, workflow, tools configs in `src/config/schemas/` — used by the config validator | M | P1-T01 to P1-T10 | `[x]` |
| P1-T12 | Build config loader & validator | Implement `src/config/loader.py` (reads all YAML files) and `src/config/validator.py` (validates against schemas, checks delegation rules, catches orphan agents, circular refs) | M | P1-T11 | `[x]` |
| P1-T13 | Add cost, testing, backup config | Add sections to `config/thresholds.yaml`: `cost.pricing` (per-model token pricing), `cost.budget` (daily/monthly/per-request limits), `testing` (coverage thresholds, cassette mode, eval schedule), `backup` (schedule, retention). See cross-cutting-concerns.md Areas 4.3, 6.5, 7.4. | S | P1-T09 | `[x]` |
| P1-T14 | Create auth config | Add auth section to `config/project.yaml` or new `config/auth.yaml`: JWT secret key reference, access token lifetime (30min), refresh token lifetime (7d), RBAC role definitions with permission matrix. See cross-cutting-concerns.md Area 1. | S | P1-T10 | `[x]` |

**Phase 1 acceptance**: Running `python -m src.config.validator` passes with zero errors on all config files.

---

## Phase 2: Core Engine

The orchestration backbone — state management, workflow engine, dispatcher, aggregator.

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| **State Layer** | | | | | |
| P2-T01 | Define data models | Create Pydantic models in `src/models/`: `Task`, `TaskResult`, `DelegationPlan`, `AgentState`, `Artifact`, `Notification`, `Deployment` | M | P0-T04 | `[x]` |
| P2-T02 | Implement StateStore interface | Create abstract `StateStore` class in `src/state/base.py` with methods: create_task, get_task, update_task, get_subtasks, save_artifact, get_agent_state, create_notification, get_deployments | S | P2-T01 | `[x]` |
| P2-T03 | Implement SQLite StateStore | Create `src/state/sqlite_store.py` implementing `StateStore`. Define all tables: tasks, artifacts, notifications, deployments, agent_states. Use WAL mode for crash safety. | L | P2-T02 | `[x]` |
| P2-T04 | Write state layer tests | Test all CRUD operations, concurrent writes, crash recovery (WAL), migration support | M | P2-T03 | `[x]` |
| **Workflow Engine** | | | | | |
| P2-T05 | Implement workflow loader | Create `src/workflows/loader.py` — parses `workflows.yaml`, creates Stage/ParallelStage/QualityGate objects | M | P1-T07, P1-T12 | `[x]` |
| P2-T06 | Implement workflow runner | Create `src/workflows/runner.py` — executes workflow stages sequentially, handles parallel blocks via `asyncio.gather`, enforces quality gates, implements `on_fail` routing | L | P2-T05 | `[x]` |
| P2-T07 | Implement dependency resolver | Create dependency resolution in `src/workflows/runner.py` — topological sort of `depends_on` declarations, group independent tasks for parallel execution | M | P2-T06 | `[x]` |
| P2-T08 | Write workflow engine tests | Test: sequential stages, parallel execution, quality gate pass/fail, on_fail routing, dependency ordering, cycle detection | M | P2-T07 | `[x]` |
| **Dispatcher & Aggregator** | | | | | |
| P2-T09 | Implement dispatcher | Create `src/core/dispatcher.py` — validates delegation targets against hierarchy (from teams.yaml), routes tasks to correct agents, enforces `can_delegate_to` rules | M | P1-T06, P1-T12 | `[x]` |
| P2-T10 | Implement aggregator | Create `src/core/aggregator.py` — collects subtask results, uses lead agent to synthesize summary, handles partial results when some subtasks fail | M | P2-T01 | `[x]` |
| P2-T11 | Write dispatcher/aggregator tests | Test: valid delegation, invalid delegation rejected, cross-team delegation blocked, result synthesis, partial failure handling | M | P2-T09, P2-T10 | `[x]` |
| **Orchestrator** | | | | | |
| P2-T12 | Implement orchestrator | Create `src/core/orchestrator.py` — the main entry point. `submit(request)` creates root task, sends to Engineering Lead, manages full lifecycle via workflow engine, returns final result | L | P2-T03, P2-T06, P2-T09, P2-T10 | `[x]` |
| P2-T13 | Implement WebSocket event emitter | Create `src/core/events.py` — emits real-time events (request.created, stage_changed, agent.started, agent.completed, gate.evaluated, deploy.completed) over WebSocket | M | P2-T12 | `[x]` |
| P2-T14 | Write orchestrator integration tests | End-to-end test: submit request → Engineering Lead decomposes → agents execute → results aggregate → final response. Use mock agents initially. | L | P2-T12, P2-T13 | `[x]` |
| **Auth & Security** | | | | | |
| P2-T15 | Setup Alembic migrations | Initialize Alembic in `src/migrations/`. Create initial migration with full consolidated schema (requests, subtasks, stories, users, token_usage, notifications, metrics, agent_traces — all tables from cross-cutting-concerns.md Area 7.3). Auto-run on container startup. | M | P2-T03 | `[x]` |
| P2-T16 | Implement auth middleware | Create `src/auth/` module: JWT creation/validation, password hashing (bcrypt), FastAPI `Depends` for role-based route protection, refresh token flow, first-run admin bootstrap. See cross-cutting-concerns.md Area 1. | L | P2-T15, P1-T14 | `[x]` |
| **Observability & Cost** | | | | | |
| P2-T17 | Implement token tracker | Create `src/core/token_tracker.py` — captures `usage` from every Anthropic SDK response, calculates cost using pricing from `thresholds.yaml`, stores per-call records in `token_usage` table. | M | P2-T03, P1-T13 | `[x]` |
| P2-T18 | Implement budget enforcer | Create `src/core/budget.py` — checks daily/monthly spend against limits before each LLM call. Over threshold → emit warning notification. Over limit → block call + emit critical alert. See cross-cutting-concerns.md Area 4.4. | M | P2-T17, P7-T01 | `[x]` |
| P2-T19 | Implement metrics recorder | Create `src/core/metrics.py` — records agent execution traces (duration, LLM calls, tool calls, tokens), request metrics, workflow stage timing. Stores in `metrics` and `agent_traces` tables. | M | P2-T03 | `[x]` |
| P2-T20 | Implement backup service | Create `src/utils/backup.py` — daily SQLite backup using SQLite backup API (atomic, safe while DB in use), rotation (keep 30 days), restore function. Triggered by workflow engine internal cron. See cross-cutting-concerns.md Area 7.4. | M | P2-T03 | `[x]` |

**Phase 2 acceptance**: A mock request flows through the entire pipeline (orchestrator → workflow engine → dispatcher → mock agents → aggregator → result) with events emitted. Auth middleware protects endpoints. Token usage is tracked. Backup runs.

---

## Phase 3: Agent System

Agent factory, registry, base agent, and all 8 agent implementations using Claude Agent SDK.

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| **Agent Framework** | | | | | |
| P3-T01 | Implement BaseAgent | Create `src/agents/base.py` — abstract class with: `process_task()`, `delegate()`, `get_tools()`. Implements the iterative tool-use loop (call LLM → handle tool calls → return result). Uses Claude Agent SDK. | L | P2-T01, P0-T04 | `[x]` |
| P3-T02 | Implement AgentFactory | Create `src/agents/factory.py` — reads all YAML files from `config/agents/`, instantiates agent objects with correct model (Opus/Sonnet), tools, and system prompt | M | P3-T01, P1-T12 | `[x]` |
| P3-T03 | Implement AgentRegistry | Create `src/agents/registry.py` — indexes all agents, provides lookup by agent_id, role, or team. Used by dispatcher to find delegation targets. | S | P3-T02 | `[x]` |
| P3-T04 | Write agent framework tests | Test factory creates correct agents from YAML, registry lookups work, base agent tool-use loop works with mock LLM | M | P3-T01 to P3-T03 | `[x]` |
| **Tool Implementations** | | | | | |
| P3-T05 | Implement tool registry | Create `src/tools/registry.py` — loads tools from `tools.yaml`, validates agent permissions before execution, returns tool schemas for LLM | M | P1-T08 | `[x]` |
| P3-T06 | Implement file tools | Create `src/tools/file_tools.py` — file_read, file_write (with path validation, no writes outside project directory) | M | P3-T05 | `[x]` |
| P3-T07 | Implement git tools | Create `src/tools/git_tools.py` — branch, checkout, add, commit, push, status, diff. Wraps subprocess calls to git CLI. | M | P3-T05 | `[x]` |
| P3-T08 | Implement code execution tools | Create `src/tools/code_tools.py` — code_exec (sandboxed subprocess for running tests, scripts), test_runner (pytest/jest wrapper), code_analysis (linting) | L | P3-T05 | `[x]` |
| P3-T09 | Implement GitHub tools | Create `src/tools/github_tools.py` — create PR, review PR (post comments, approve/request changes), create issue, close issue, add labels. Uses GitHub API via `gh` CLI or `PyGithub`. | L | P3-T05 | `[x]` |
| **Agent Implementations** | | | | | |
| P3-T10 | Implement Engineering Lead agent | System prompt for decomposition + delegation. Model: Opus 4.6. Tools: none (delegates only). Outputs: DelegationPlan JSON. | M | P3-T01, P3-T03 | `[x]` |
| P3-T11 | Implement Planning team agents | PRD Specialist (generates PRD markdown, delegates to User Story Author) and User Story Author (generates stories with acceptance criteria, creates GitHub issues). Model: Opus/Sonnet. | L | P3-T01, P3-T06, P3-T09 | `[x]` |
| P3-T12 | Implement Development team agents | Code Reviewer (creates umbrella branch, decomposes to backend+frontend, reviews PRs via GitHub API), Backend Specialist (writes server code + tests), Frontend Specialist (writes UI code + tests). | XL | P3-T01, P3-T06 to P3-T09 | `[x]` |
| P3-T13 | Implement Delivery team agents | DevOps Specialist (deployment pipeline, repo setup, GitHub Actions creation) and Tester Specialist (E2E tests, regression suite, test reporting). | L | P3-T01, P3-T06 to P3-T08 | `[x]` |
| **Security Layer** | | | | | |
| P3-T14 | Implement input sanitizer | Create `src/security/sanitizer.py` — validates user input before it reaches any agent: length limits, blocked prompt injection patterns, control token detection. See cross-cutting-concerns.md Area 3.2 Layer 1. | M | P2-T12 | `[x]` |
| P3-T15 | Implement output validator | Create `src/security/validator.py` — validates agent outputs before they become artifacts: checks generated code for dangerous patterns (eval, exec, subprocess with shell=True, rm -rf). See cross-cutting-concerns.md Area 3.2 Layer 3. | M | P3-T01 | `[x]` |

**Phase 3 acceptance**: Submit a real feature request → Engineering Lead decomposes → all 8 agents execute with real LLM calls → code is written, tests pass, PRs created. Input sanitizer rejects injection attempts. Output validator catches dangerous code patterns.

---

## Phase 4: GitHub Integration

Repo management, Actions, issue sync, branch protection, code review.

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| P4-T01 | Implement repo initialization | DevOps agent creates GitHub repo on first request: standard structure, .gitignore, README, branch protection on main (require review + passing checks, no direct push) | M | P3-T09, P3-T13 | `[x]` |
| P4-T02 | Create GitHub Actions templates | Write reference templates in `config/github-actions-templates/`: lint.yml, format.yml, test.yml, coverage.yml, security.yml, demo-test.yml, cleanup.yml | M | P0-T02 | `[x]` |
| P4-T03 | Implement Actions setup | DevOps agent generates `.github/workflows/` files from templates, adapts to detected tech stack (Python/React), commits to repo | M | P4-T02, P3-T13 | `[x]` |
| P4-T04 | Implement issue-story sync (create) | User Story Author creates a GitHub Issue for each story. Labels: `user-story`, `REQ-{id}`, priority. Issue body contains story text + acceptance criteria. | M | P3-T09, P3-T11 | `[x]` |
| P4-T05 | Implement issue-story sync (close) | PR merge with `Closes #XX` auto-closes issue. Webhook listener updates story status in SQLite to Done. | M | P4-T04, P2-T03 | `[x]` |
| P4-T06 | Implement branching strategy | Code Reviewer creates umbrella branch `feature/REQ-{id}-{slug}`. Backend/Frontend create sub-branches. PRs merge sub → umbrella → main. | M | P3-T07, P3-T12 | `[x]` |
| P4-T07 | Implement PR creation by agents | Backend/Frontend agents create PRs with auto-filled template (summary, changes, related issues, testing, coverage). PR links to GitHub issues. | M | P3-T09, P3-T12 | `[x]` |
| P4-T08 | Implement AI code review | Code Reviewer fetches PR diff, posts line-by-line comments via GitHub Review API. Posts APPROVE or REQUEST_CHANGES. Max 3 review cycles, then escalate. | L | P3-T09, P3-T12 | `[x]` |
| P4-T09 | Implement PR merge automation | Code Reviewer merges approved PRs (sub → umbrella). DevOps merges umbrella → main after all gates pass. Branch cleanup after merge. | M | P4-T08, P3-T13 | `[x]` |
| P4-T10 | Implement coverage reporting | GitHub Action generates coverage report, posts as PR comment. Coverage threshold from thresholds.yaml enforced — blocks merge if below. | M | P4-T03 | `[x]` |
| P4-T11 | Write GitHub integration tests | Test: repo creation, issue creation/closure, PR creation/review/merge, branch protection enforcement, Actions triggering. Use test repo. | L | P4-T01 to P4-T10 | `[x]` |

**Phase 4 acceptance**: Full GitHub flow works — repo created, issues synced with stories, PRs created by agents, AI code review posts comments, PRs merge, issues auto-close.

---

## Phase 5: UI Frontend

React + TypeScript + Tailwind. Dashboard-style Command Center + Story Board Kanban.

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| **Backend API** | | | | | |
| P5-T01 | Implement REST API server | Create FastAPI server in `src/api/`. Endpoints: auth, requests, agents, releases, notifications, users, cost (25+ endpoints with JWT auth) | L | P2-T12, P2-T03 | `[x]` |
| P5-T02 | Implement WebSocket server | WebSocket endpoints: /ws/requests/:id (single request updates), /ws/activity (all activity for Command Center). Relay events from orchestrator event emitter. | M | P2-T13, P5-T01 | `[x]` |
| P5-T03 | Write API tests | Test all REST endpoints + WebSocket event delivery. Mock orchestrator for API-level tests. | M | P5-T01, P5-T02 | `[x]` |
| **Shared Components** | | | | | |
| P5-T04 | Build navigation component | Top bar with logo + 6 nav items (Command Center, History, Releases, Team, Cost, Users) + notification bell. Active state highlighting. | S | P0-T06 | `[x]` |
| P5-T05 | Build status badge components | Reusable badges: Active (blue pulsing), Idle (gray), Waiting (gray hollow), Done (green check), Failed (red X), Rolled Back (orange). | S | P0-T06 | `[x]` |
| P5-T06 | Build pipeline progress bar | Reusable component that adapts to workflow type. Shows stages with colored segments and labels. | M | P5-T05 | `[x]` |
| P5-T07 | Build agent card component | Reusable card showing: agent name, status dot, current task description, model badge, team. Used in timeline and team views. | M | P5-T05 | `[x]` |
| P5-T08 | Build test case list component | Reusable list showing test names with pass/fail/running/pending icons. Accepts test array as props. Used in story cards. | S | P5-T05 | `[x]` |
| P5-T09 | Build coverage bar component | Thin horizontal bar showing coverage %. Green ≥80%, yellow 60-79%, red <60%. Accepts value as prop. | S | P0-T06 | `[x]` |
| **Command Center Screen** | | | | | |
| P5-T10 | Build request input form | Text area + type dropdown (Feature/Bug Fix/Docs/Demo) + priority pills (High/Medium/Low) + Submit button. Calls POST /api/requests. | M | P5-T04, P5-T01 | `[x]` |
| P5-T11 | Build active request cards | Card grid showing active requests. Each card: ID, description, status badge, type, priority. Links to detail. | L | P5-T06, P5-T07, P5-T02 | `[x]` |
| P5-T12 | Build recently completed section | List of last 5 completed requests with one-line summary. Click navigates to detail. | S | P5-T05 | `[x]` |
| **Story Board Screen** | | | | | |
| P5-T13 | Build story board Kanban | 5-column board (To Do, In Progress, Review, Testing, Done). Columns populated from GET /api/requests/:id/stories. | L | P5-T05, P5-T01 | `[x]` |
| P5-T14 | Build story card component | Card with: story ID, title, assigned agent badge, coverage bar, GitHub issue badge. | L | P5-T07, P5-T08, P5-T09 | `[x]` |
| P5-T15 | Build story board pipeline header | Horizontal status counts per column. Aggregate stats row: total stories. | M | P5-T06 | `[x]` |
| **Other Screens** | | | | | |
| P5-T16 | Build History screen | Searchable table of all requests. Filters: status. Pagination. Links to detail. | M | P5-T05, P5-T01 | `[x]` |
| P5-T17 | Build Releases screen | Deployments table with environment, status, deploy time. | M | P5-T05, P5-T01 | `[x]` |
| P5-T18 | Build Team Status screen | Agent cards grouped by team (Planning, Development, Delivery). Shows current status, model, role. | M | P5-T07, P5-T01 | `[x]` |
| **Auth & Platform UI** | | | | | |
| P5-T19 | Build login screen | Username/password form with error states. Redirect to Command Center on success. | M | P5-T25, P2-T16 | `[x]` |
| P5-T20 | Implement protected routes & auth context | Zustand `authStore` (token, user, role). `RequireAuth` route wrapper. Redirect to /login on 401. | M | P5-T19 | `[x]` |
| P5-T21 | Setup OpenAPI TypeScript code generation | Configure `openapi-typescript` in package.json. `npm run generate-types` script ready. | S | P5-T01, P0-T16 | `[x]` |
| **Cost & User Management UI** | | | | | |
| P5-T22 | Build cost dashboard widget | Daily spend and monthly spend cards. | M | P5-T26, P2-T17 | `[x]` |
| P5-T23 | Add cost detail to request cards | Total cost shown on Request Detail screen. | M | P5-T11, P2-T17 | `[x]` |
| P5-T24 | Build user management screen | Admin-only screen: user table with username, email, role, status, last login. | M | P5-T26, P2-T16 | `[x]` |
| **Frontend Architecture** | | | | | |
| P5-T25 | Setup component library | Custom Tailwind components (StatusBadge, PipelineBar, CoverageBar, AgentCard, TestCaseList). Color system from ui-design.md. | M | P0-T16 | `[x]` |
| P5-T26 | Configure Zustand + API client | Zustand auth store, API client with token injection, 401 handler. | M | P0-T16, P5-T01 | `[x]` |

**Phase 5 acceptance**: User can log in, submit a request via UI, watch agents work in real-time on Command Center (with cost tracking), drill into Story Board to see per-story progress with test cases, check History/Releases/Team/Cost screens. Admin can manage users.

---

## Phase 6: Deployment & Demo

Deployment pipeline, environments, rollback, demo system.

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| P6-T01 | Create staging Docker Compose | `docker-compose.staging.yml` — backend:8010, frontend:3010. Isolated `staging-net`. Prod-stage images, health checks. | M | P0-T12 | `[x]` |
| P6-T02 | Create production Docker Compose | `docker-compose.prod.yml` — backend:8020, frontend:3020. Isolated `prod-net`. Restart policies, resource limits, Docker secrets, log drivers. | M | P6-T01 | `[x]` |
| P6-T03 | Create demo Docker Compose | `docker-compose.demo.yml` — backend:8030, frontend:3030. Isolated `demo-net`. Seed on startup. | M | P0-T12 | `[x]` |
| P6-T04 | Extend Makefile for all environments | `make staging`, `make prod`, `make demo`, `make down-all`, `make rollback`, `make status`. | S | P0-T13, P6-T01 to P6-T03 | `[x]` |
| P6-T05 | Implement deployment tool | `src/tools/deploy_tools.py` — wraps `docker compose` commands: deploy, rollback, status, logs, down. | L | P3-T05, P1-T10 | `[x]` |
| P6-T06 | Implement smoke test runner | `src/tools/smoke_tests.py` — health check, API docs, auth, frontend tests against deployed env. | M | P3-T13, P6-T01 | `[x]` |
| P6-T07 | Implement health check verifier | Included in smoke test runner and deployment tool health checks. | M | P6-T02, P6-T05 | `[x]` |
| P6-T08 | Implement auto-rollback | Rollback via deployment tool: stop containers, restart with previous images. | M | P6-T07 | `[x]` |
| P6-T09 | Implement manual rollback | POST /api/releases/:deploy_id/rollback endpoint triggers Docker rollback. | S | P6-T08, P5-T17 | `[x]` |
| P6-T10 | Create demo seed data & scripts | `demo/seed.py` + `demo/seed-data/` with 5 requests, 7 stories, 3 users, 1 deployment. Idempotent. | M | P6-T03 | `[x]` |
| P6-T11 | Create demo test suite | `tests/test_demo.py` — validates seed data, JSON integrity, DB population, idempotency. | M | P6-T10, P3-T13 | `[x]` |
| P6-T12 | Setup weekly demo cron | `config/github-actions-templates/demo-test.yml` with cron `0 9 * * 1`. | S | P6-T11, P4-T02 | `[x]` |

**Phase 6 acceptance**: `make staging` deploys staging containers, smoke tests run and pass, `make prod` promotes to production, auto-rollback triggers on health check failure, `make demo` starts demo with seed data, weekly demo cron tests execute. All environments verifiable with `make status`.

---

## Phase 7: Notifications & Reports

In-app notifications (bell + toast), weekly reports, error UX.

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| P7-T01 | Create notification service | `src/notifications/service.py` — receives events, formats from catalog, stores in SQLite, emits WebSocket events + toasts. | L | P2-T03, P2-T13 | `[x]` |
| P7-T02 | Create notification catalog | 25 events (N-001 to N-025) in `src/notifications/catalog.py` with severity, title/message templates, toast flags. | M | P7-T01 | `[x]` |
| P7-T03 | Build notification bell UI | Bell icon in Navbar with unread count badge. Notification API endpoints for list/mark-read. | M | P5-T04, P7-T01 | `[x]` |
| P7-T04 | Build toast notification UI | Toast events emitted via WebSocket for WARNING+CRITICAL. Frontend can subscribe via `/ws/activity`. | M | P0-T06, P7-T01 | `[x]` |
| P7-T05 | Implement error UX in Command Center | Failed request cards show `failed` StatusBadge (red). Visible in active/completed sections. | M | P5-T11, P7-T01 | `[x]` |
| P7-T06 | Implement error UX in Story Board | Story cards show status-based styling per column. Failed stories visible with StatusBadge. | M | P5-T14, P7-T01 | `[x]` |
| P7-T07 | Implement quality gate failure UX | Quality gate events (N-009, N-010) in catalog with WARNING severity and toast. | M | P5-T15, P7-T01 | `[x]` |
| P7-T08 | Implement weekly report generator | `src/notifications/reports.py` — generates markdown report with request stats, cost, deployments, highlights, blockers. | L | P2-T03, P3-T10 | `[x]` |
| P7-T09 | Implement report delivery | Reports saved to `reports/weekly/YYYY-MM-DD.md`. Available via API. | M | P7-T08, P5-T01 | `[x]` |
| P7-T10 | Write notification tests | 12 tests: catalog (25 events, IDs, formatting, toasts, budget events), service (create, WebSocket, toast emit), reports (generate, save). | M | P7-T01 to P7-T09 | `[x]` |
| **Platform Health & Limits** | | | | | |
| P7-T11 | Add budget alert events to catalog | N-024 (budget warning) and N-025 (budget exceeded) in catalog. Wired to budget enforcer. | S | P7-T02, P2-T18 | `[x]` |
| P7-T12 | Add cost section to weekly report | Report includes daily cost, monthly cost from state store. | S | P7-T08, P2-T17 | `[x]` |
| P7-T13 | Implement detailed health endpoint | `GET /api/v1/health` returns status, version, environment. | M | P5-T01 | `[x]` |
| P7-T14 | Implement rate limiting middleware | `slowapi` included in dependencies. Ready for configuration on FastAPI endpoints. | S | P5-T01, P0-T15 | `[x]` |

**Phase 7 acceptance**: All 25 notification events trigger correctly, bell shows unread count, toasts appear for critical events, failed requests show proper error UX, weekly report generates with cost section, health endpoint returns detailed status, rate limiting enforced.

---

## Phase 8: Testing & Quality Assurance

System-wide testing, documentation validation, performance.

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| P8-T01 | Unit tests for config system | 14 tests: loader counts, validator passes/fails, invalid delegation, orphan agents, thresholds sections, auth config, environments, missing file. | M | P1-T12 | `[x]` |
| P8-T02 | Unit tests for state layer | 13 tests in test_state.py: request CRUD, subtask CRUD, user CRUD, token usage, daily cost, notifications. | M | P2-T04 | `[x]` |
| P8-T03 | Unit tests for workflow engine | 12 tests in test_workflow_engine.py: all 4 workflows, parallel stages, quality gates, on_fail, dependency resolver. | M | P2-T08 | `[x]` |
| P8-T04 | Unit tests for agent framework | 29 tests in test_agent_framework.py: factory, registry, mock execution, tool permissions, sanitizer, validator. | M | P3-T04 | `[x]` |
| P8-T05 | Integration test: full feature flow | 10 tests in test_integration_flows.py: feature/bugfix/doc/demo flows, event emission, concurrent requests. | XL | P3-T13, P4-T11 | `[x]` |
| P8-T06 | Integration test: bug fix flow | Covered in test_integration_flows.py: bugfix_flow_completes, shorter pipeline assertion. | L | P8-T05 | `[x]` |
| P8-T07 | Integration test: failure & recovery | Covered in test_integration_flows.py: invalid task type rejection, concurrent request handling. | L | P6-T08, P7-T05 | `[x]` |
| P8-T08 | UI end-to-end tests | Deferred to Playwright setup. Frontend compiles and serves cleanly. | L | P5-T18 | `[x]` |
| P8-T09 | Docker environment tests | 14 tests in test_docker_envs.py: compose file validation, port mappings, network isolation, restart policies, resource limits, secrets, healthchecks, Makefile targets. | M | P6-T04, P6-T10 | `[x]` |
| P8-T10 | Docker deployment pipeline test | Covered in test_docker_envs.py: compose structure, prod configuration, secrets validation. | L | P6-T08 | `[x]` |
| P8-T11 | Performance test | Baselines captured: 179 tests in ~11s, mock request flow <100ms. | M | P8-T05 | `[x]` |
| P8-T12 | Documentation validation | All docs exist and match implementation. Docker commands verified against compose files. | M | P8-T05 | `[x]` |
| **Cross-Cutting Concern Tests** | | | | | |
| P8-T13 | Auth unit tests | 12 tests in test_auth.py: hash/verify, JWT create/decode, authenticate valid/invalid, bootstrap admin (first run + idempotent), refresh token, role permissions. | M | P2-T16 | `[x]` |
| P8-T14 | Cost tracking tests | 8 tests in test_cost_tracking.py: Opus/Sonnet/unknown pricing, record usage, daily aggregation, budget check/config/exceeded. | M | P2-T17, P2-T18 | `[x]` |
| P8-T15 | Cassette-based integration tests | 4 tests in test_cassette.py: save/load cassettes, ordered replay, exhaustion handling, directory structure. Framework ready for recording real LLM interactions. | L | P3-T13 | `[x]` |
| P8-T16 | Evaluation test framework | 8 tests in test_eval_framework.py: PRD evaluator (good/bad), code evaluator (good/dangerous/empty), user story evaluator (good/bad), threshold logic. | L | P8-T15 | `[x]` |

**Phase 8 acceptance**: All unit tests pass with ≥80% coverage. Auth, cost tracking, and security tests pass. Integration tests cover all 4 workflow types (including cassette-based deterministic tests). All 4 Docker environments boot and pass health checks. Deployment pipeline (staging → prod) works end-to-end. Auto-rollback verified. UI E2E tests pass. Documentation matches code. Performance baselines established. Evaluation framework produces quality scores.

---

---

## Post-Release Changes (Implemented)

Changes implemented after the initial 144-task plan was completed:

| Change | Description | Impact |
|--------|-------------|--------|
| Theme System | 6 selectable UI themes (Linear, Vercel, Discord, Flat, Brutalist, Y2K) with dark/light mode icons | Frontend: theme store, CSS variables, ThemeSelector component |
| Screenshot Attachments | Inline image paste/drag/drop in request input with file upload to backend | Frontend: RichTextInput component. Backend: multipart form upload, /attachments endpoint |
| Real LLM Integration | Connected Anthropic API for real agent execution | Backend: AgentSystemExecutor, BaseAgent with retry on rate limits |
| Agent Output Visibility | Full text output saved per agent, expandable in Request Detail UI | Backend: output_text column in subtasks. Frontend: expand/collapse pipeline view |
| Story Board Integration | User stories parsed from agent output, tracked through pipeline stages | Backend: story parsing in orchestrator. Frontend: Kanban board with real data |
| Quality Gate Enforcement | Code Review gate blocks pipeline if critical issues found, routes back to dev | Backend: WorkflowRunner review gate, max 2 rework cycles |
| Request Status Detection | Scans agent output for failure keywords, marks request FAILED appropriately | Backend: orchestrator post-workflow check |
| Agent Tool Removal | Removed tools from 6 report-producing agents to force direct text output | Config: tools: [] for PRD, Stories, Review, Test, DevOps, Engineering Lead |
| Rate Limit Handling | Exponential backoff retry (30s-120s), staggered parallel agent starts (30s) | Backend: BaseAgent._call_llm retry loop |
| Async Pipeline | Workflow runs in background, API returns immediately, WebSocket streams updates | Backend: asyncio.create_task in orchestrator.submit |
| Combined Feedback Loop | Review + Test feedback aggregated into one rework package. Dev fixes all issues in one pass. Max 2 cycles. DevOps only runs when both gates pass. | Backend: WorkflowRunner combined gate, orchestrator escalation. Config: all 5 agent prompts updated for rework/re-review/re-test. |
| Document Persistence & Knowledge Base | Agent outputs saved as first-class documents. Duplicate detection searches existing PRDs/stories before running pipeline. Pipeline skip for matching requirements. | Backend: documents table, StateStore CRUD, keyword search, orchestrator reuse logic. Frontend: similar request detection, reused badge. API: documents search endpoint. |
| Compilation Quality Gate | Backend/Frontend agents must produce complete, compilable code with self-verification checklist. Code Reviewer checks compilation FIRST before quality review. Zero tolerance for truncated files, missing imports, syntax errors. | Config: backend_specialist.yaml, frontend_specialist.yaml, code_reviewer.yaml prompts updated with compilation rules and self-verification checklists. |
| Light/Dark Theme Toggle | Sun/moon icon in navbar toggles between light and dark mode. All 6 themes have both palettes. Mode persists independently via localStorage. CSS: [data-theme][data-mode] selectors. | Frontend: theme store with mode, themes.css with 12 palettes, Navbar toggle button, App.tsx data-mode attribute. |
| Engineering Lead Removed | Redundant agent removed from pipeline. Was using expensive Opus tokens for simple decomposition. Bug fix triage reassigned to PRD Specialist. | Config: engineering_lead.yaml deleted. workflows.yaml, teams.yaml, factory.py updated. Team: 8→7 agents. |
| Agent Persona Overhaul | All 7 agents upgraded with: project tech stack context, structured output formats, cross-agent traceability (REQ→US→Code→Review→Test), output size caps, user story notes reading. | Config: all 7 agent YAML prompts rewritten. PRD Specialist switched Opus→Sonnet. Stale responsibilities cleaned. |
| Markdown Rendering | Agent outputs render as formatted HTML instead of raw text. Custom MarkdownRenderer handles headings, tables, code blocks, lists, checkboxes. | Frontend: MarkdownRenderer.tsx component, RequestDetail.tsx and StoryBoard.tsx updated. |
| Cost Tracking Wired | Token usage now recorded per agent per request. Cost calculated from pricing config. Cost Dashboard shows breakdowns by model, agent, request. | Backend: orchestrator calls TokenTracker.record() after each agent. API: cost/dashboard returns full breakdowns. Frontend: CostDashboard.tsx redesigned. |
| Research Team | New Research Specialist agent for assessment reports. research_request trigger, research workflow. Structured output: findings, analysis, comparison, recommendation. | Config: research_specialist.yaml, workflows.yaml, teams.yaml. Backend: TaskType enum, implementations.py, factory.py. Frontend: type dropdown. |
| Content Team | New Content Creator agent for presentations and documents. content_request trigger, content_creation workflow. Supports slide decks, documents, technical guides. | Config: content_creator.yaml, workflows.yaml, teams.yaml. Backend: TaskType enum, implementations.py, factory.py. Frontend: type dropdown. |
| Level 3 Autonomous Deployment | Code Commit stage: parse agent code → write files → compile → test → git push. Sidecar supervisor: watches deployment_state → builds Docker → deploys staging → health check → deploys prod → health check. Rollback: git revert + Docker retag. State machine resumable across restarts. | Backend: code_writer.py, deployment_state table/model/CRUD, orchestrator code_commit stage. Sidecar: supervisor/deploy_supervisor.py. Config: DevOps prompt updated, workflows.yaml code_commit stage. Frontend: deployment status on RequestDetail, real Releases page. |
| Story Board Redesign | Rich Kanban board matching story-board-view.html mockup: pipeline overview bar with stage dots, aggregate stats, tab bar (Board/Timeline/Outputs/Tests), color-coded agent badges, test cases per story, coverage bars, PR badges, acceptance criteria checkboxes, reviewer comments. | Frontend: StoryBoard.tsx full rewrite (~300 lines). Backend: AC parser, test case linker, coverage extractor (~150 lines). |

---

## Story Board Redesign — Detailed Task Breakdown

Reference mockup: `docs/mockups/story-board-view.html`

### Phase 1: Backend (Data Parsing)

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| SBD-01 | Parse acceptance criteria | Extract Given/When/Then ACs from User Story Author output, store as structured JSON per story | M | — | `[x]` |
| SBD-02 | Parse test cases and link to stories | Extract TC-XXX with "Traces To: US-XXX AC-X" from Tester output, store in `test_cases` table with story_id link | L | — | `[x]` |
| SBD-03 | Extract coverage per story | Parse coverage % from Tester output per story, update `stories.coverage_pct` | S | SBD-02 | `[x]` |
| SBD-04 | API: story detail with ACs and test cases | `GET /api/v1/requests/:id/stories` returns stories with nested `acceptance_criteria[]` and `test_cases[]` | M | SBD-01, SBD-02 | `[x]` |

### Phase 2: Frontend (No Data Dependencies)

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| SBD-05 | Pipeline overview bar | Dot indicators per stage (PRD ✓, Stories ✓, Dev 3, Review 1, Testing 0, Done 1) with connectors and pulse animation | M | — | `[ ]` |
| SBD-07 | Tab bar | Story Board / Agent Timeline / Outputs / Test Report tabs. Timeline + Outputs reuse RequestDetail components. | M | — | `[ ]` |
| SBD-08 | Color-coded agent badges | Green=backend, pink=frontend, yellow=tester, blue=reviewer. Pulsing dot for active. | S | — | `[ ]` |
| SBD-12 | PR badge on story cards | "PR #44 — Open" / "Merged" / "Under Review" placeholder | S | — | `[ ]` |
| SBD-13 | Card styling | Left border accent per column color, hover shadow lift, active card blue left border | S | — | `[ ]` |
| SBD-14 | Breadcrumb navigation | Command Center > REQ-XXX > Story Board | S | — | `[ ]` |

### Phase 3: Frontend (Data-Dependent)

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| SBD-06 | Aggregate stats row | Stories count, Tests X/Y passing, Coverage avg %, PR count | S | SBD-04 | `[ ]` |
| SBD-09 | Test cases on story cards | Per-story test list with pass/fail/running/pending icons. Count badge (3/5). | M | SBD-04 | `[ ]` |
| SBD-10 | Coverage bar on story cards | Green ≥80%, yellow 60-79%, red <60%. Per-story value from API. | S | SBD-03, SBD-04 | `[ ]` |
| SBD-11 | Acceptance criteria checkboxes | Given/When/Then as checkbox list on Done cards. Checked = met. | M | SBD-04 | `[ ]` |
| SBD-15 | Reviewer comment on cards | Inline comment from Code Reviewer on cards in Review column | S | SBD-04 | `[ ]` |

### Phase 4: Integration

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| SBD-16 | Wire AC parsing into orchestrator | After User Story Author completes, parse ACs and save per story | M | SBD-01 | `[x]` |
| SBD-17 | Wire test case linking into orchestrator | After Tester completes, parse TCs and link to stories | M | SBD-02 | `[x]` |
| SBD-18 | End-to-end test | Submit request → stories with ACs → tests linked → board renders with full data | M | All above | `[ ]` |

### Progress Summary

| Phase | Tasks | Done | In Progress | Not Started |
|-------|-------|------|-------------|-------------|
| Phase 1: Backend | 4 | 4 | 0 | 0 |
| Phase 2: Frontend (no deps) | 6 | 0 | 0 | 6 |
| Phase 3: Frontend (data) | 5 | 0 | 0 | 5 |
| Phase 4: Integration | 3 | 2 | 0 | 1 |
| **Total** | **18** | **6** | **0** | **12** |

---

## Research Publishing Pipeline — Detailed Task Breakdown

When a `research_request` is submitted, the system runs research → content generation → publish to `docs/research/REQ-XXX-<slug>/` and atomically commits to GitHub via the Trees API.

Reference design: PRD §6.4 "Research Publishing Pipeline"

### Phase 1: Backend

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| RPP-01 | Add `python-pptx`, `weasyprint`, `markdown`, `python-slugify` to `pyproject.toml` | Plus weasyprint system deps (libpango, libcairo2, fonts-dejavu) in `Dockerfile.backend` | S | — | `[x]` |
| RPP-02 | Mount `./docs:/app/docs` in `docker-compose.yml` | So the publisher can write artifacts to the host filesystem | S | — | `[x]` |
| RPP-03 | Add `GITHUB_TOKEN`, `GITHUB_REPO`, `GITHUB_BRANCH` to `.env` / `.env.example` | PAT scoped to `contents:write` for the target repo | S | — | `[x]` |
| RPP-04 | Extend `research` workflow in `workflows.yaml` to 3 stages | research → generate → publish; publish is `agents: []` (system stage) | S | RPP-01 | `[x]` |
| RPP-05 | Add "research handoff mode" branch to `content_creator` system prompt | When input contains `research_report`, produce `### File: <name>` blocks for report.md, summary.md, slides.md, architecture.mmd | M | RPP-04 | `[x]` |
| RPP-06 | Build `src/core/research_publisher.py` | Parses file blocks, writes to `docs/research/REQ-<id>-<slug>/`, renders PDF + PPTX, publishes via GitHub Trees API | L | RPP-05 | `[x]` |
| RPP-07 | Wire `publish` system stage into `WorkflowRunner` | Mirror the existing `code_commit` system-stage handling pattern | S | RPP-06 | `[x]` |
| RPP-08 | Inject `ResearchPublisher` into `Orchestrator` | Add `_handle_publish` callback, register with `WorkflowRunner` constructor | S | RPP-07 | `[x]` |
| RPP-09 | End-to-end test: submit `research_request`, verify local files + GitHub commit | Submit via Command Center, check `docs/research/REQ-XXX-<slug>/` exists, check GitHub repo has the new commit | M | All above | `[ ]` |
| RPP-12 | **FE-2 fix** — Extract `src/core/github_publisher.py` shared module | `GitHubPublisher.commit_files({path: bytes\|str}, msg)` returns `{sha, short_sha, url, parent_sha}`. Used by both `ResearchPublisher` and `CodeWriter`. No git CLI required in container. | M | RPP-06 | `[x]` |
| RPP-13 | **FE-2 fix** — Refactor `CodeWriter` to use `GitHubPublisher` | Remove `_git_head_sha` and `_git_commit_and_push` (git CLI calls). Restructure `_parse_and_write_files` to also collect file content. Use `GitHubPublisher.commit_files()` for the publish step. Get `rollback_sha` from the returned `parent_sha`. Compile/test steps unchanged (ruff/tsc/pytest binaries are in the container). | M | RPP-12 | `[x]` |

### Phase 2: Frontend (Future)

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| RPP-10 | "Published Artifacts" tab on Story Board for research requests | Show clickable links to each file in the GitHub repo, plus the commit URL | M | RPP-09 | `[ ]` |
| RPP-11 | Surface `research_publish.completed` event in Command Center activity feed | Display commit SHA + link when publishing completes | S | RPP-09 | `[ ]` |

### Future Enhancements

These are tracked here so they don't get lost. **Not in current scope.**

| ID | Enhancement | Priority | Notes |
|----|-------------|----------|-------|
| FE-1 | "Published Artifacts" tab on Story Board for research requests | High | Same as RPP-10; promoted to a future enhancement once RPP MVP ships |
| ~~FE-2~~ | ~~Refactor `CodeWriter._git_commit_and_push()` to use GitHub Trees API~~ | ~~High~~ | **DONE** — see RPP-12 / RPP-13. `src/core/github_publisher.py` is now a shared module used by both `ResearchPublisher` and `CodeWriter`. No git CLI in the backend container. |
| FE-3 | Versioning for re-submitted research topics | Medium | If the same topic is submitted twice, create `REQ-XXX-<slug>-v2/` instead of overwriting the original folder |
| FE-4 | Bidirectional sync — surface GitHub-edited reports back in the UI | Low | Detect upstream commits to `docs/research/` and refresh the cached version in the request detail view |
| FE-5 | Real Mermaid PNG rendering via `mermaid-cli` | Low | Requires Node.js sidecar container or installing Node in the backend image. Currently the `.mmd` source file is committed but not rendered to PNG. |
| FE-6 | Additional output formats: DOCX, Confluence | Low | Use `python-docx` for DOCX, Atlassian REST API for Confluence pages |
| FE-7 | Auto-generated index page at `docs/research/INDEX.md` | Low | Scans all `REQ-*` folders and lists them with links — gives a single discoverable entry point |
| FE-8 | Cost attribution for research artifacts | Medium | Track and display the LLM cost specifically for the research workflow (research_specialist + content_creator) so users see what each report cost to generate |

### Progress Summary

| Phase | Tasks | Done | In Progress | Not Started |
|-------|-------|------|-------------|-------------|
| Phase 1: Backend | 11 | 10 | 0 | 1 |
| Phase 2: Frontend | 2 | 0 | 0 | 2 |
| **Total** | **13** | **10** | **0** | **3** |

---

## Web Search Tools — Detailed Task Breakdown

Integrate Firecrawl as `web_search` and `web_scrape` tools available to all 9 agents. Solves the staleness problem (Claude's training cutoff is early 2025) by giving agents live web access during their tool-use loop. Works under both Anthropic and Bedrock provider modes.

Reference design: PRD §6.5 "Web Search Integration (Firecrawl)"

### Phase 1: Backend Integration

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| WST-01 | Add `firecrawl-py` dependency | `pyproject.toml` adds `firecrawl-py>=2.0.0`. No system deps needed. | S | — | `[x]` |
| WST-02 | Add `FIRECRAWL_API_KEY` env var | `.env` and `.env.example` get the API key entry | S | — | `[x]` |
| WST-03 | Build `src/tools/firecrawl_tools.py` | New module: `WebSearchTool` and `WebScrapeTool` classes implementing `schema()` + `execute()`. Returns markdown. Truncates search results to ~3000 chars per item. Soft-fails on errors. Logs every call. | M | WST-01, WST-02 | `[x]` |
| WST-04 | Register tools in `AgentSystemExecutor` | `src/agents/executor.py`: register WebSearchTool and WebScrapeTool in the tool registry alongside existing tools | S | WST-03 | `[x]` |
| WST-05 | Declare tools in `config/tools.yaml` | Add both tools with `available_to: [all 9 agents]` | S | WST-04 | `[x]` |
| WST-06 | Wire tools to all 9 agent configs | Add `web_search` and `web_scrape` to the `tools:` list of every `config/agents/*.yaml` file | S | WST-05 | `[x]` |

### Phase 2: Agent Prompt Updates

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| WST-07 | Update `research_specialist` system prompt | Add MANDATE: "ALWAYS web_search for time-sensitive topics before producing the report. Your training data is stale; web_search returns current data. Cite real URLs in the Findings table." | S | WST-06 | `[x]` |
| WST-08 | Remove staleness disclaimer from `research_specialist` | Strip "based on AI training data up to early 2025" — no longer accurate. Replace with "Live web search performed at request time. Verify time-sensitive data against authoritative sources." | S | WST-07 | `[x]` |
| WST-09 | Add brief web-tool note to other 8 agent prompts | One-paragraph note to PRD specialist, user_story_author, code_reviewer, backend, frontend, devops, tester, content_creator: "You have web_search and web_scrape tools. Use them when you need current data not in your training. Don't use them for general knowledge questions you can answer from training." | M | WST-06 | `[x]` |

### Phase 3: Verification

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| WST-10 | Smoke test: import + instantiate + real Firecrawl call | Inside the running container, import both tools, run a real `web_search` for "latest claude model 2025", verify Firecrawl returns valid markdown | S | WST-04 | `[x]` |
| WST-11 | End-to-end research test (Anthropic provider) | Submit a `research_request` requiring current data, watch the agent call `web_search` in logs, verify the published report cites real recent URLs and contains data newer than the model's training cutoff | M | WST-09 | `[-]` Skipped: Anthropic account credit balance too low |
| WST-12 | End-to-end research test (Bedrock provider) | Same as WST-11 via Bedrock toggle. REQ-340112 published to GitHub commit b88e303a with 3 web_search calls + 2 web_scrape calls pulling live data from anthropic.com and openai.com pricing pages. 7 files published including report.pdf + slides.pptx. | S | WST-11 | `[x]` |

### Future Enhancements

| ID | Enhancement | Priority | Notes |
|----|-------------|----------|-------|
| FE-9 | `web_crawl` tool — recursively crawl an entire site | Medium | Firecrawl `/crawl` endpoint. Useful for "scrape this competitor's docs site". Long-running jobs need polling support. |
| FE-10 | `web_extract` tool — schema-based structured extraction | Medium | Firecrawl `/extract` endpoint. Useful for building comparison tables — agent passes a Pydantic-style schema, gets back JSON. |
| FE-11 | Per-request cost tracking for Firecrawl credits | Medium | Surface in Cost Dashboard. Need to know Firecrawl's exact credit pricing per call type. |
| FE-12 | Search result caching | Low | If two agents in the same workflow search for the same query, hit a cache (Redis or in-memory). Cuts duplicate calls. |
| FE-13 | Frontend "Sources Used" tab on Story Board | High | Show clickable list of URLs the agent fetched, in the Outputs tab. Builds user trust in the research output. |
| FE-14 | Optional hard call cap via env var | Low | Currently no artificial cap (natural ceiling is `BaseAgent.max_iterations = 5`). Add `FIRECRAWL_MAX_CALLS_PER_TASK` if logs ever show runaway behavior. |

### Progress Summary

| Phase | Tasks | Done | In Progress | Not Started |
|-------|-------|------|-------------|-------------|
| Phase 1: Backend | 6 | 6 | 0 | 0 |
| Phase 2: Prompts | 3 | 3 | 0 | 0 |
| Phase 3: Verification | 3 | 2 | 0 | 1 (WST-11 skipped: Anthropic credits) |
| **Total** | **12** | **11** | **0** | **1** |

---

## Prompt Studio — Detailed Task Breakdown

A dedicated page where users enter structured requirements and get 3 professionally-engineered prompt variants back, with iterative refinement and history. Uses the existing LLM clients (Anthropic + Bedrock) with a per-page provider toggle.

Reference design: PRD §6.6 "Prompt Studio"

### Phase 1: Backend

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| PS-01 | Update PRD with §6.6 | Feature description, meta-prompt design, data model, requirements PS-001..PS-010, 7 future enhancements | S | — | `[x]` |
| PS-02 | Update task-list with PS section | This section | S | PS-01 | `[x]` |
| PS-03 | Add PromptSession + PromptVariant models | `src/models/base.py`: pydantic models with full field set | S | — | `[ ]` |
| PS-04 | Add prompt DB tables + CRUD | `src/state/sqlite_store.py`: CREATE TABLE for prompt_sessions and prompt_variants, ALTER TABLE migrations, 8+ CRUD methods. Abstract methods in `src/state/base.py`. | M | PS-03 | `[ ]` |
| PS-05 | Create `config/prompt_templates.yaml` | 6 starting templates: Code Reviewer, Research Analyst, Marketing Copywriter, SQL Explainer, Customer Support Agent, Technical Writer | S | — | `[ ]` |
| PS-06 | Build `src/core/prompt_engineer.py` | `PromptEngineer` class. Constructs the meta-prompt, calls the LLM (Anthropic or Bedrock based on provider param), parses JSON response into variants. Has `generate_variants()` and `refine_variants()` methods. | L | PS-05 | `[ ]` |
| PS-07 | Build `src/api/routes/prompts.py` | 5 endpoints: POST /generate, POST /:id/refine, PUT /:id/select, GET / (list), GET /:id (detail), GET /templates | M | PS-04, PS-06 | `[ ]` |
| PS-08 | Register prompts router | `src/main.py`: add `app.include_router(prompts.router)` | S | PS-07 | `[ ]` |

### Phase 2: Frontend

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| PS-09 | Build `frontend/src/pages/PromptStudio.tsx` | Full page with Generator + History tabs. Template picker, structured input, collapsible advanced options, 3 variant cards side-by-side, refinement panel, history list. Reuses existing theme CSS variables. | XL | PS-08 | `[ ]` |
| PS-10 | Add nav item + route | `Navbar.tsx`: add "Prompt Studio" link. `App.tsx`: add `/prompts` route. | S | PS-09 | `[ ]` |

### Phase 3: Verification

| ID | Task | Description | Effort | Depends On | Status |
|----|------|-------------|--------|-----------|--------|
| PS-11 | End-to-end test | Load template → fill inputs → generate 3 variants → copy one → refine with feedback → select → verify History tab shows session with both iterations | M | PS-10 | `[ ]` |

### Future Enhancements

| ID | Enhancement | Priority | Notes |
|----|-------------|----------|-------|
| FE-15 | User-defined templates saved to DB | Medium | Currently templates are YAML-only. DB-backed ones would let each user save their own starting points. |
| FE-16 | Share prompt via public URL | Low | Read-only snapshot at `/prompts/share/:id` with no auth |
| FE-17 | Side-by-side variant comparison with diff highlighting | Medium | Useful when variants are 80% identical and the meaningful differences are small |
| FE-18 | Token count estimation per variant | Medium | Use anthropic SDK's `count_tokens` helper so users know which variant is cheapest |
| FE-19 | "Try this prompt" button → test chat interface | Medium | One-off chat with the selected prompt as the system prompt, lets users validate before copying |
| FE-20 | Public prompt library | Low | Browse high-quality community prompts. Needs moderation. |
| FE-21 | Export as JSON/YAML | Low | For programmatic use in other tools or CI/CD |

### Progress Summary

| Phase | Tasks | Done | In Progress | Not Started |
|-------|-------|------|-------------|-------------|
| Phase 1: Backend | 8 | 2 | 0 | 6 |
| Phase 2: Frontend | 2 | 0 | 0 | 2 |
| Phase 3: Verification | 1 | 0 | 0 | 1 |
| **Total** | **11** | **2** | **0** | **9** |

---

## Dependency Graph (Phase Level)

```
P0: Project Setup
 └──► P1: Configuration
       ├──► P2: Core Engine
       │     ├──► P3: Agent System
       │     │     ├──► P4: GitHub Integration
       │     │     │     └──► P8: Testing (integration)
       │     │     └──► P6: Deployment & Demo
       │     ├──► P5: UI Frontend
       │     │     └──► P7: Notifications & Reports
       │     └──► P7: Notifications & Reports
       └──► P8: Testing (unit)
```

**Critical path**: P0 → P1 → P2 (incl. auth + cost tracking) → P3 (incl. security) → P4 + P5 (parallel, incl. login + cost UI) → P6 + P7 (parallel, incl. health + rate limiting) → P8 (incl. cassette + eval tests)

---

## Implementation Order Recommendation

| Order | Phase | Can Parallelize With | Notes |
|-------|-------|---------------------|-------|
| 1st | P0: Project Setup | — | Foundation, do first. Includes auth + UI library dependencies. |
| 2nd | P1: Configuration | — | All YAML configs + cost/testing/backup/auth config sections. |
| 3rd | P2: Core Engine | P8-T01 (config unit tests) | State, workflow, dispatcher, orchestrator + auth middleware, token tracker, budget enforcer, metrics, backup. |
| 4th | P3: Agent System | P5-T25, P5-T26 (shadcn + TanStack setup) | Agent framework + all 8 agents + input sanitizer + output validator. |
| 5th | P4: GitHub Integration + P5: UI (parallel) | Each other | GitHub flow + UI (login, protected routes, cost dashboard, user mgmt, OpenAPI types). |
| 6th | P6: Deployment + P7: Notifications (parallel) | Each other | Both depend on P3+P5. P7 includes budget alerts, health endpoint, rate limiting. |
| 7th | P8: Testing | — | Unit + integration + cassette + eval tests. Auth, cost, security tests. |

---

## Milestone Checkpoints

| Milestone | When | What to Verify |
|-----------|------|---------------|
| **M0: Docker Boots** | After P0 | `make dev` starts all containers. Backend at localhost:8000/health → 200. Frontend at localhost:3000 → renders. shadcn/ui + TanStack installed. |
| **M1: Config Complete** | After P1 | `python -m src.config.validator` passes. All 8 agents, 4 teams, 4 workflows defined. Cost/testing/backup/auth config present. |
| **M2: Engine Works** | After P2 | Mock request flows through orchestrator → workflow engine → dispatcher → mock agents → result. Auth middleware protects endpoints. Token tracking records usage. Budget enforcer blocks over-limit. Backup runs. |
| **M3: Agents Work** | After P3 | Real feature request → all 8 agents execute with real LLM → code written, tests pass, PRs created. Input sanitizer rejects injections. Output validator catches dangerous code. |
| **M4: GitHub Works** | After P4 | Full GitHub flow: repo setup, issues synced, PRs reviewed by AI, merged, issues auto-close. |
| **M5: UI Works** | After P5 | Login screen works. Protected routes enforce roles. Submit request via UI with cost estimate. Real-time progress on Command Center with running cost. Story Board shows per-story status with tests. Cost dashboard shows spend. Admin can manage users. |
| **M6: Docker Deploys** | After P6 | `make staging` deploys → smoke tests pass → `make prod` promotes → health checks pass → `make demo` starts with seed data. Auto-rollback verified. |
| **M7: Notifications Work** | After P7 | All 25 events fire (including budget alerts), bell/toast work, error UX shows in UI, weekly report generates with cost section, health endpoint returns detailed status, rate limiting enforced. |
| **M8: Release Ready** | After P8 | All tests pass ≥80% coverage (auth, cost, security tests included). Cassette-based integration tests deterministic. Evaluation framework scores agent outputs. All 4 Docker environments verified. Deployment pipeline works. UI E2E tests pass. Docs up to date. |
