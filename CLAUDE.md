# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Agent Team is a configuration-driven, hierarchical AI agent orchestration platform. It runs a team of specialist LLM agents (PRD, user story author, backend, frontend, code reviewer, DevOps, tester, research, content) through YAML-defined workflows to process "requests" end-to-end — from PRD through code, review, test, commit, and deployment.

- **Backend:** Python 3.12, FastAPI, async/await, Pydantic v2, SQLite via aiosqlite
- **Frontend:** React 19 + TypeScript + Vite, Tailwind v4, Zustand, TanStack Query/Table, React Router v7
- **LLM providers:** Anthropic direct API or Amazon Bedrock (via `anthropic[bedrock]`)
- **Runs entirely in Docker Compose** — there is no "run locally without Docker" path. Backend port 8000, frontend port 3000.

## Common Commands

All development goes through the Makefile + `docker compose`. Use Unix shell syntax (this is bash on Windows, not PowerShell).

```bash
make dev                       # Start local stack (backend+frontend), builds if needed
make down                      # Stop local stack
make build                     # Rebuild all images --no-cache
make logs                      # Tail all container logs
make logs-backend              # Tail backend only
make logs-frontend             # Tail frontend only
make shell-backend             # bash inside backend container
make shell-frontend            # sh inside frontend container
make health                    # curl backend /api/v1/health
make status                    # Show container status across all envs
make clean                     # Remove containers, volumes, and local images
```

**Other environments** (each has its own compose file, network, and port range):

```bash
make staging   # ports 8010/3010 — docker-compose.staging.yml
make prod      # ports 8020/3020 — docker-compose.prod.yml
make demo      # ports 8030/3030 — docker-compose.demo.yml (seed data)
make down-all  # Stop everything across all envs
```

**Deployment supervisor** (standalone container that survives app rebuilds, has Docker socket access, drives staging→prod rollouts by watching `deployment_states` in the shared SQLite volume):

```bash
make supervisor          # Start the supervisor stack
make supervisor-logs     # Tail
make supervisor-stop     # Stop
```

### Tests

Backend tests run **inside the backend container** against the mounted source. `pytest` is configured in `pyproject.toml` with `asyncio_mode = "auto"`, coverage against `src/`, and `--cov-fail-under=80`.

```bash
# All tests
docker compose exec backend pytest

# Single file / single test
docker compose exec backend pytest tests/test_orchestrator.py
docker compose exec backend pytest tests/test_orchestrator.py::test_submit_request

# Skip coverage gate (useful while iterating)
docker compose exec backend pytest --no-cov tests/test_state.py

# With output / stop on first failure
docker compose exec backend pytest -xvs tests/test_workflow_engine.py
```

### Lint / format / typecheck

```bash
# Backend (ruff + mypy configured in pyproject.toml; line length 100, py312, strict mypy)
docker compose exec backend ruff check src tests
docker compose exec backend ruff format src tests
docker compose exec backend mypy src

# Frontend (run inside the frontend container, or from frontend/ on host if you have node)
docker compose exec frontend npm run lint
docker compose exec frontend npm run format
docker compose exec frontend npm run build        # tsc -b && vite build
docker compose exec frontend npm run generate-types  # regenerate src/api/schema.d.ts from live OpenAPI
```

## CRITICAL: Restart containers after code changes

After editing any file under `src/` or `frontend/src/`, **always** run the matching restart before verifying the change. Do **not** trust uvicorn `--reload` or Vite HMR in this repo — they routinely go stale and serve old code even when the file on disk is updated.

```bash
docker compose restart backend     # after editing src/
docker compose restart frontend    # after editing frontend/src/
# then wait ~5s and confirm `docker ps` shows (healthy) before testing
```

Restart both if you touched both. This is not optional — do it automatically.

## Architecture

### Config-driven agent system

Everything agent-related is defined in YAML under `config/` and loaded by `src/config/loader.py` (`ConfigLoader.load_all()`):

- `config/project.yaml` — project metadata, env URLs/ports, auth/RBAC config
- `config/agents/*.yaml` — one file per agent (agent_id, model, system_prompt, delegation rules, tool grants). Files starting with `_` are templates and skipped by the loader.
- `config/teams.yaml` — team hierarchy (engineering → planning/development/delivery/research/content), leads, members, domains
- `config/workflows.yaml` — DAG workflows: `feature_development`, `bug_fix`, `documentation_update`, `demo_preparation`, `research`, `content_creation`. Each has stages, parallel blocks, quality gates, and `on_fail` rework targets.
- `config/tools.yaml` — tool catalog and per-agent `available_to` permissions (enforced at runtime via `src/tools/registry.py`)
- `config/thresholds.yaml` — coverage minimums, rework limits, etc.

**Adding a new agent is a YAML-only change** in the happy path: drop a file in `config/agents/`, wire it into a team in `teams.yaml`, reference it from a stage in `workflows.yaml`, and grant tools in `tools.yaml`. No code changes required.

### Request lifecycle (backend)

1. **Entry point:** `src/main.py` — FastAPI app with lifespan-managed singletons on `app.state`: `config`, `state_store` (SQLite), `events` (EventEmitter), `auth_service`, `orchestrator`, plus an optional real `AgentSystemExecutor` when `ANTHROPIC_API_KEY` or AWS creds are present. Falls back to a mock executor when neither is set.
2. **API routes:** `src/api/routes/` — `auth`, `requests`, `agents`, `releases`, `notifications`, `users`, `documents`, `cost`, `prompts`. WebSocket in `src/api/websocket.py`. All routes mounted under `/api/v1`.
3. **Orchestrator (`src/core/orchestrator.py`)** is the top-level coordinator. It implements the `AgentExecutor` protocol consumed by the workflow runner. `submit()` creates a `Request`, picks a workflow by `task_type`, and kicks off a background task running `WorkflowRunner.run()`. Results are aggregated and persisted via `StateStore`.
4. **Dispatcher (`src/core/dispatcher.py`)** enforces delegation rules — an agent can only delegate to its direct reports as defined in the YAML. `route_by_domain()` maps domain keywords (e.g. "frontend", "testing") to the appropriate team lead.
5. **Workflow runner (`src/workflows/runner.py`)** executes a `WorkflowDefinition` as a DAG: sequential stages, `ParallelStage` blocks, quality gates with `MAX_REWORK_CYCLES = 2` rework loops back to the `on_fail` stage. Each stage calls `executor.execute_agent(agent_id, request_id, inputs)` — the Orchestrator dispatches this to either the real `AgentSystemExecutor` or a mock path with randomized delays from `MOCK_AGENT_DELAYS`.
6. **Agent execution (`src/agents/executor.py`)** — `AgentSystemExecutor` holds both `anthropic_client` (direct API, per-agent model from YAML) and `bedrock_client` (all agents on Claude Sonnet 4 via `BEDROCK_MODEL_ID`). The provider is chosen per-request. Tools are registered into `ToolRegistry` here: file I/O, git, code exec, test runner, static analysis, GitHub API/PR review, Firecrawl web search/scrape.
7. **State (`src/state/sqlite_store.py`)** — all persistent state (requests, stories, subtasks, events, tokens/cost, deployments, users, releases) goes through `StateStore` in `src/state/base.py`. Never touch SQLite directly from routes.

### Special pipelines

- **Code commit → deploy** (`src/core/code_writer.py`, `src/core/github_publisher.py`): after the `code_commit` stage, generated code is written to disk and committed via the GitHub Trees API. The standalone **supervisor** (`supervisor/deploy_supervisor.py`) watches `deployment_states` in the shared volume and drives staging→prod rollouts with rollback-on-failure.
- **Research → publish** (`src/core/research_publisher.py`): the `research` workflow's terminal `publish` stage has `agents: []` and is handled by `ResearchPublisher`. It parses `### File: <name>` blocks from the research/content agent outputs, writes artifacts under `docs/research/REQ-XXX-<slug>/`, renders `slides.md`→pptx (python-pptx) and `report.md`→pdf (weasyprint), then atomically commits via the GitHub Trees API. **Soft-fails:** GitHub errors don't fail the request; the local files remain.

### Frontend

- **Entry:** `frontend/src/main.tsx` → `App.tsx` wraps everything in `BrowserRouter` + `RequireAuth`. Theme (via `data-theme`/`data-mode` attributes on the root and `themes.css`) is in `stores/theme.ts`; JWT auth state in `stores/auth.ts`.
- **Pages** (`frontend/src/pages/`): `CommandCenter`, `RequestDetail`, `StoryBoard`, `PromptStudio`, `History`, `Releases`, `TeamStatus`, `CostDashboard`, `UserManagement`, `Login`. Routing is defined once in `App.tsx`.
- **API types** are generated from the live backend OpenAPI schema into `frontend/src/api/schema.d.ts` via `npm run generate-types` (requires the backend to be running at localhost:8000).
- **StoryBoard** uses a Kanban layout with full test visibility — this is the frozen/approved UI pattern; don't redesign without explicit request.

### Auth

JWT-based, bcrypt-hashed local users in SQLite. Three roles with inheritance: `viewer` → `developer` → `admin`. Permissions are enforced per route — see the full matrix in `docs/cross-cutting-concerns.md` AREA 1 and the role definitions in `config/project.yaml` under `auth:`. On first run, `AuthService.bootstrap_admin()` creates an `admin` user and logs a one-time password (force password change on first login).

## Repo conventions

- **Docs go in the working directory.** Any new plan/PRD/research/design doc goes under `docs/` in this repo. Never create project artifacts in `~/.claude/` or other external paths.
- **Docs format:** existing design docs (`docs/architecture.md`, `docs/cross-cutting-concerns.md`, `docs/feature-gaps-design.md`, etc.) use a "Document Information" table + numbered sections. Match that style when adding new docs.
- **`docs/task-list.md`** is the canonical phase/task tracker — 8 phases, IDs like `P1-T03`, status legend `[ ] [~] [x] [!] [-]`. Update statuses there when completing work, rather than introducing a parallel tracker.
- Ruff is configured for `E, W, F, I, N, UP, B, SIM` with line length 100. Mypy is strict on `src/`.
- Pytest requires 80% coverage; use `--no-cov` only while iterating locally.
