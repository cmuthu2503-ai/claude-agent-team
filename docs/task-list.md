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
