# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Agent Team is a configuration-driven, hierarchical AI agent orchestration platform. It runs a team of specialist LLM agents (PRD, user story author, backend, frontend, code reviewer, DevOps, tester, research, content) through YAML-defined workflows to process "requests" end-to-end — from PRD through code, review, test, commit, and deployment.

- **Backend:** Python 3.12, FastAPI, async/await, Pydantic v2, SQLite via aiosqlite
- **Frontend:** React 19 + TypeScript + Vite, Tailwind v4, Zustand, TanStack Query/Table, React Router v7
- **LLM provider:** **Claude Platform on AWS** (Anthropic-operated, AWS-authenticated) via `anthropic[aws]`. Single provider, single model (`claude-opus-4-7`) for all 9 agents. Not Bedrock, not the direct Anthropic API. Setup: [docs/setup-claude-platform-on-aws.md](docs/setup-claude-platform-on-aws.md).
- **Runs entirely in Docker Compose** — there is no "run locally without Docker" path. Backend port 8000, frontend port 3000.

## Working Standards (Claude's Posture)

> User instruction (verbatim): **"You must be doing these critical thinking and come back to me on improving on working standards."**

This is a standing rule, not a one-off. When working in this repo you must be **proactive**, not reactive. Concretely:

1. **Think one level deeper than the immediate request.** When fixing bug X, also ask "why does X happen at all? What's the actual class? Where else does this class fire?" Don't stop at the cited symptom.
2. **State cost/time impact in concrete terms.** "Saves ~$X × N tasks × M projects per week" or "removes ~Y minutes from the critical path." This is what turns a vague observation into an actionable improvement.
3. **Sweep for sibling instances when fixing one bug.** If REQ-X failed E501, query for *all* recent failures with similar fingerprints. Bake the pattern-class fix into the system (config, formatter, agent prompt section) rather than patching the cited instance.
4. **Maintain a visible "observed but not actioned" list in responses** so the next conversation has a starting point. Don't let waste accumulate silently across sessions.
5. **Don't accept stale UI as user-acceptable.** If a page lies (e.g. TeamStatus showing IN_PROGRESS for a failed request), that's a bug to fix at the source — polling, websocket, etc. — not "user should refresh."
6. **Smaller, more frequent commits** when files don't overlap. Bundle only when they genuinely share a file. Future bisection depends on this.
7. **Flag uncommitted-and-unrelated working-tree state at the start of commit conversations.** Don't let drift accumulate.
8. **Add tests for non-trivial new logic** even when not explicitly asked. Smoke tests at minimum; real pytest/vitest where the logic warrants.

The persistent memory entry at `~/.claude/projects/C--ai-projects-claude-agent-team/memory/feedback_critical_thinking.md` carries the longer rationale and a running list of observations that need follow-up.

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

**Deployment supervisor** runs on the HOST (not in Docker — see "Supervisor scope" below for why) and drives staging + dev rollouts by watching `deployment_states` in the local SQLite at `./data/agent_team.db`:

```bash
make supervisor          # Start supervisor in foreground (see logs live)
make supervisor-bg       # Start in background (logs → supervisor.log)
make supervisor-stop     # Kill the host process

# Or directly:
python supervisor/deploy_supervisor.py
scripts/run-supervisor.bat   # Windows
scripts/run-supervisor.sh    # macOS / Linux
```

Prerequisites on host: Python 3.12, `pip install "anthropic[aws]>=0.52.0"`, Git CLI, Docker CLI + Compose plugin, `.env` file with `ANTHROPIC_AWS_API_KEY` / `ANTHROPIC_AWS_WORKSPACE_ID` / `GITHUB_TOKEN` / `GITHUB_REPO`.

The supervisor's `deploy()` flow on a `code_committed` row:
1. `git fetch origin main && git checkout origin/main -- <files_committed>` — pulls the agent's published files from origin into the host working tree.
2. Calls the **deployment judge LLM** to decide strategy (`deploy_full` / `deploy_staging_only` / `skip` / `hold`).
3. `docker compose build` — rebuilds dev image.
4. `docker compose -f docker-compose.staging.yml up -d --build` + healthcheck on :8010.
5. `docker compose -f docker-compose.yml up -d --build` — rebuild dev + healthcheck on :8000 (120s window + auto-restart fallback + final pass/fail).
6. `docker compose -f docker-compose.staging.yml down` — tear staging down (validation complete).
7. On any failure: `git stash` working tree → `git revert HEAD --no-edit && git push origin main` → rebuild + bring up reverted dev. **Prod step was removed** (was intermittently failing under back-to-back staging→prod builds; not a priority).

### Stable Compose project names

Every compose file has an explicit `name:` at the top so the project name (and the volume/network names derived from it) doesn't drift with the working directory. Concretely:

| File | Project name |
|---|---|
| `docker-compose.yml` | `agent-team-dev` |
| `docker-compose.staging.yml` | `agent-team-staging` |
| `docker-compose.prod.yml` | `agent-team-prod` |
| `docker-compose.demo.yml` | `agent-team-demo` |
| `docker-compose.supervisor.yml` | `agent-team-supervisor` |

Without `name:`, Compose auto-derives the project name from the directory (e.g., `jolly-aryabhata-f0e6ad` for a worktree under `.claude/worktrees/`). That makes volume names ephemeral, which breaks the supervisor's `external: true` volume binding (it has to know the volume name up front). Pinning the project name keeps everything stable across worktree spawns, branch switches, and fresh clones.

Override the file-level name with `COMPOSE_PROJECT_NAME=foo` env var if you intentionally want an isolated environment. The dev volume `agent-team-dev_agent-team-data` is shared between any directory running the dev stack — so request history persists across worktree switches by design.

### Supervisor scope: runs on HOST, not in Docker

The supervisor **must run on the host machine**, not inside a Docker container. The previous containerized supervisor (`docker-compose.supervisor.yml`, deprecated) couldn't manage the dev stack because of a structural Docker-in-Docker bind-mount issue:

> When the supervisor runs `docker compose -f docker-compose.yml up -d --build` from inside its own container, Compose reads volume mounts like `./frontend/vite.config.ts` and resolves them to `/app/project/frontend/vite.config.ts` (the supervisor's view). It then sends that path to the Docker daemon, which runs on the HOST and tries to find the path **on the host's filesystem** — where it doesn't exist. Mount fails. Container doesn't start. Healthcheck times out forever.
>
> This wasn't fixable with a config tweak. Running the supervisor on the host eliminates the path-translation step entirely — relative paths in `docker-compose.yml` resolve against the host (where they actually exist), just as they would for a human running `docker compose` from a terminal.

**What this looks like in practice:**
- Supervisor process runs on the host (foreground `make supervisor` or background `make supervisor-bg`)
- Reads SQLite at `./data/agent_team.db` directly (the dev backend bind-mounts the same dir at `/app/data`)
- Performs `git` and `docker compose` against the main repo as the developer would
- Agents publish commits to `origin/main` via the GitHub Trees API
- Supervisor pulls those commits into the main repo's working tree (`git fetch + git checkout origin/main -- <files>`), then runs `docker compose build` against that consistent source
- All `docker compose` calls happen from the host, so bind mounts in `docker-compose.yml` resolve correctly

The `agent-team-data` volume (previously named) is now a bind mount to `./data/` on the host — that's how the supervisor and the dev backend share the SQLite database without docker-volume gymnastics.

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
6. **Agent execution (`src/agents/executor.py`)** — `AgentSystemExecutor` holds a single `anthropic_client` (an `AsyncAnthropicAWS` instance pointed at Claude Platform on AWS). Every agent shares the client and uses the model from its YAML (`claude-opus-4-7` across the board). `inference_geo` (`us` by default per `config/project.yaml::llm`) is set on every `messages.create()`. Tools are registered into `ToolRegistry` here: file I/O, git, code exec, test runner, static analysis, GitHub API/PR review, Firecrawl web search/scrape.
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
