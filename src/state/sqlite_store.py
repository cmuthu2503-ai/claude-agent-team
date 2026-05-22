"""SQLite implementation of StateStore — WAL mode for crash safety."""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from src.models.base import (
    AcceptanceCriterion,
    AgentTrace,
    ArtifactKind,
    ArtifactStatus,
    BuildMessage,
    DeployDecision,
    DeploymentState,
    Document,
    Artifact,
    Deployment,
    Metric,
    Notification,
    Project,
    ProjectArtifact,
    ProjectStatus,
    ProjectTask,
    PromptSession,
    PromptVariant,
    Request,
    RequestStatus,
    Story,
    Subtask,
    SubtaskStatus,
    TaskStatus,
    TestCase,
    TokenUsage,
    User,
    UserRole,
)
from src.state.base import StateStore

SCHEMA_SQL = """
-- Requests & Subtasks
CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    task_type TEXT NOT NULL DEFAULT 'feature_request',
    priority TEXT NOT NULL DEFAULT 'medium',
    status TEXT NOT NULL DEFAULT 'received',
    tags TEXT DEFAULT '[]',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    estimated_cost_usd REAL,
    actual_cost_usd REAL,
    provider TEXT NOT NULL DEFAULT 'anthropic',
    published_files TEXT DEFAULT '[]',
    commit_sha TEXT,
    commit_url TEXT,
    code_commit_error TEXT,
    project_id TEXT  -- FK projects(project_id); nullable, app-layer defaults to 'proj-unassigned'
);

CREATE TABLE IF NOT EXISTS subtasks (
    subtask_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    input_artifacts TEXT DEFAULT '[]',
    output_artifacts TEXT DEFAULT '[]',
    output_text TEXT DEFAULT '',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT,
    FOREIGN KEY (request_id) REFERENCES requests(request_id)
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    subtask_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    format TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Stories
CREATE TABLE IF NOT EXISTS stories (
    story_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'todo',
    priority TEXT,
    assigned_agent TEXT,
    coverage_pct REAL,
    github_issue_number INTEGER,
    FOREIGN KEY (request_id) REFERENCES requests(request_id)
);

CREATE TABLE IF NOT EXISTS acceptance_criteria (
    ac_id TEXT PRIMARY KEY,
    story_id TEXT NOT NULL,
    criterion_text TEXT NOT NULL,
    given_clause TEXT DEFAULT '',
    when_clause TEXT DEFAULT '',
    then_clause TEXT DEFAULT '',
    is_met BOOLEAN DEFAULT 0,
    FOREIGN KEY (story_id) REFERENCES stories(story_id)
);

CREATE TABLE IF NOT EXISTS test_cases (
    test_id TEXT PRIMARY KEY,
    story_id TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    last_run_at TIMESTAMP,
    FOREIGN KEY (story_id) REFERENCES stories(story_id)
);

-- Auth
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'developer',
    is_active BOOLEAN DEFAULT 1,
    must_change_password BOOLEAN DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    revoked_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Deployments
CREATE TABLE IF NOT EXISTS deployments (
    deploy_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    git_sha TEXT NOT NULL,
    environment TEXT NOT NULL,
    status TEXT NOT NULL,
    previous_deploy_id TEXT,
    deployed_at TIMESTAMP,
    verified_at TIMESTAMP,
    rolled_back_at TIMESTAMP
);

-- Notifications
CREATE TABLE IF NOT EXISTS notifications (
    notification_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    request_id TEXT,
    link_url TEXT,
    user_id TEXT,
    created_at TIMESTAMP NOT NULL,
    read_at TIMESTAMP,
    dismissed_at TIMESTAMP
);

-- Token Usage & Cost
CREATE TABLE IF NOT EXISTS token_usage (
    usage_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    subtask_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS budget_config (
    config_id TEXT PRIMARY KEY DEFAULT 'default',
    daily_limit_usd REAL,
    monthly_limit_usd REAL,
    per_request_limit_usd REAL,
    alert_threshold_pct REAL DEFAULT 0.8,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by TEXT NOT NULL DEFAULT 'system'
);

-- Observability
CREATE TABLE IF NOT EXISTS metrics (
    metric_id TEXT PRIMARY KEY,
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    labels TEXT DEFAULT '{}',
    recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_traces (
    trace_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    subtask_id TEXT NOT NULL,
    llm_calls INTEGER NOT NULL DEFAULT 0,
    tool_calls INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    duration_ms INTEGER,
    error_message TEXT,
    PRIMARY KEY (trace_id, subtask_id)
);

-- Deployment State Machine
CREATE TABLE IF NOT EXISTS deployment_states (
    deployment_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    commit_sha TEXT DEFAULT '',
    current_step TEXT NOT NULL DEFAULT 'code_committed',
    step_history TEXT DEFAULT '[]',
    files_committed TEXT DEFAULT '[]',
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT,
    rollback_sha TEXT DEFAULT '',
    -- Judgment from the deployment-judge LLM (Milestone 1, hybrid agent).
    -- Populated by the supervisor BEFORE it runs any docker commands. Lets
    -- the agent decide skip/staging-only/full/hold based on the commit shape
    -- without the LLM ever issuing the actual deploy commands itself.
    strategy TEXT DEFAULT '',           -- skip | deploy_staging_only | deploy_full | hold
    strategy_reasoning TEXT DEFAULT '', -- agent's plain-language explanation
    risk TEXT DEFAULT ''                -- low | medium | high
);

CREATE INDEX IF NOT EXISTS idx_deployment_states_request ON deployment_states(request_id);
CREATE INDEX IF NOT EXISTS idx_deployment_states_step ON deployment_states(current_step);

-- Documents (Knowledge Base)
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    version INTEGER DEFAULT 1,
    tags TEXT DEFAULT '[]',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP
);

-- Projects (parent container for requests; one PRD per project, etc.)
-- See docs/prd-projects-feature.md §5.1 for the field-by-field rationale.
CREATE TABLE IF NOT EXISTS projects (
    project_id      TEXT PRIMARY KEY,           -- 'proj-<uuid>' or 'proj-unassigned'
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'active',     -- active | archived
    color           TEXT NOT NULL DEFAULT '#00f0ff',    -- one of 8 preset hex (PRJ-009)
    icon            TEXT NOT NULL DEFAULT 'folder',     -- one of 8 preset lucide names (PRJ-010)
    tags            TEXT NOT NULL DEFAULT '[]',         -- JSON array of strings (PRJ-011)
    lead_user_id    TEXT,                                -- FK users(user_id), defaults to created_by
    repo_url        TEXT NOT NULL DEFAULT '',           -- optional URL (PRJ-013)
    default_team    TEXT,                                -- 'engineering' | 'research' | 'content' | NULL (PRJ-015)
    target_date     TIMESTAMP,                           -- optional, ISO date (PRJ-014)
    template_id     TEXT,                                -- refs config/project_templates.yaml (PRJ-016)
    created_by      TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP
);

-- Project-driven Build (PDB-01) — versioned brief + PRD per project.
-- Tasks list lives in its own structured table (added in Phase B).
CREATE TABLE IF NOT EXISTS project_artifacts (
    artifact_id     TEXT PRIMARY KEY,                   -- 'art-<uuid>'
    project_id      TEXT NOT NULL,                       -- FK projects(project_id)
    kind            TEXT NOT NULL,                       -- 'brief' | 'prd'
    version         INTEGER NOT NULL,                    -- monotonic within (project_id, kind)
    status          TEXT NOT NULL DEFAULT 'draft',       -- 'draft' | 'finalized' | 'archived'
    content         TEXT NOT NULL DEFAULT '',
    created_by      TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP,                           -- last content mutation (PDB-43)
    finalized_at    TIMESTAMP,
    finalized_by    TEXT,
    UNIQUE (project_id, kind, version)
);
CREATE INDEX IF NOT EXISTS idx_artifacts_project_kind_status
    ON project_artifacts(project_id, kind, status);

-- Project-driven Build (PDB-13) — structured task list per project.
-- A project has one active list_version at a time; older versions flip to
-- list_status='archived' when a new list is finalized. task_status tracks
-- the per-task lifecycle once dispatched: backlog → dispatched →
-- in_progress → review → testing → deployed | failed | cancelled.
CREATE TABLE IF NOT EXISTS project_tasks (
    task_id          TEXT PRIMARY KEY,                  -- 'T-<uuid>'
    project_id       TEXT NOT NULL,                      -- FK projects(project_id)
    list_version     INTEGER NOT NULL,                   -- which generation of the task list this belongs to
    list_status      TEXT NOT NULL DEFAULT 'draft',      -- 'draft' | 'finalized' | 'archived'
    ordinal          INTEGER NOT NULL,                   -- display order within the list
    title            TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    task_type        TEXT NOT NULL DEFAULT 'feature_request',
    priority         TEXT NOT NULL DEFAULT 'medium',     -- 'low' | 'medium' | 'high'
    estimated_agent  TEXT,                                -- e.g. 'backend_specialist', 'frontend_specialist'
    task_status      TEXT NOT NULL DEFAULT 'backlog',    -- backlog | dispatched | in_progress | review | testing | deployed | failed | cancelled
    request_id       TEXT,                                -- set when dispatched (Phase C)
    amended          INTEGER NOT NULL DEFAULT 0,         -- 1 if added/modified by chat after finalize (Phase D)
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tasks_project_status
    ON project_tasks(project_id, list_status, task_status);
CREATE INDEX IF NOT EXISTS idx_tasks_request
    ON project_tasks(request_id);

-- Project-driven Build (PDB-33) — chat with project_orchestrator agent.
-- One row per turn (user message, assistant message, or tool result).
-- tool_calls JSON column carries structured summaries the UI renders as chips
-- ("Dispatched T-001 → REQ-A1B2C3", "Modified T-007 priority …").
CREATE TABLE IF NOT EXISTS build_session_messages (
    message_id    TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    role          TEXT NOT NULL,                     -- 'user' | 'assistant' | 'tool'
    content       TEXT NOT NULL DEFAULT '',
    tool_calls    TEXT,                               -- JSON array of {tool, input, result_summary} or NULL
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by    TEXT
);
CREATE INDEX IF NOT EXISTS idx_msgs_project_time
    ON build_session_messages(project_id, created_at);

-- Prompt Studio
CREATE TABLE IF NOT EXISTS prompt_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    use_case TEXT NOT NULL,
    target_audience TEXT DEFAULT '',
    desired_output TEXT DEFAULT '',
    tone TEXT DEFAULT '',
    constraints TEXT DEFAULT '',
    options TEXT DEFAULT '{}',
    provider TEXT NOT NULL DEFAULT 'anthropic',
    template_id TEXT,
    selected_variant_id TEXT
);

CREATE TABLE IF NOT EXISTS prompt_variants (
    variant_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    iteration INTEGER NOT NULL DEFAULT 0,
    variant_index INTEGER NOT NULL DEFAULT 1,
    approach TEXT DEFAULT '',
    prompt_text TEXT NOT NULL,
    techniques TEXT DEFAULT '[]',
    feedback_applied TEXT DEFAULT '',
    generated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES prompt_sessions(session_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_prompt_sessions_user ON prompt_sessions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_prompt_variants_session ON prompt_variants(session_id, iteration, variant_index);

CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
CREATE INDEX IF NOT EXISTS idx_requests_created ON requests(created_at);
CREATE INDEX IF NOT EXISTS idx_subtasks_request ON subtasks(request_id);
CREATE INDEX IF NOT EXISTS idx_stories_request ON stories(request_id);
CREATE INDEX IF NOT EXISTS idx_acceptance_criteria_story ON acceptance_criteria(story_id);
CREATE INDEX IF NOT EXISTS idx_test_cases_story ON test_cases(story_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_request ON token_usage(request_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_recorded ON token_usage(recorded_at);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, read_at);
CREATE INDEX IF NOT EXISTS idx_metrics_name_time ON metrics(metric_name, recorded_at);
CREATE INDEX IF NOT EXISTS idx_agent_traces_request ON agent_traces(request_id);
CREATE INDEX IF NOT EXISTS idx_deployments_env ON deployments(environment, status);
CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_documents_request ON documents(request_id);
CREATE INDEX IF NOT EXISTS idx_projects_status_name ON projects(status, name);
CREATE INDEX IF NOT EXISTS idx_projects_lead ON projects(lead_user_id);
CREATE INDEX IF NOT EXISTS idx_requests_project ON requests(project_id);

-- AI Deploy Judge (per-project) — audit trail + override learning.
-- One row per judge decision OR user override. The latest non-applied
-- row for a project is the "current recommendation" the UI shows.
CREATE TABLE IF NOT EXISTS deploy_decisions (
    decision_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    -- Snapshot of the drift the judge looked at — JSON array of
    -- {commit_sha, files, added, removed}. Lets the UI render the
    -- "X commits since last deploy" panel without re-querying.
    drift_summary TEXT NOT NULL DEFAULT '[]',
    -- Range of commits the decision covers (so we can detect "still
    -- current?" by comparing to projects.last_deploy_commit_sha).
    from_commit_sha TEXT,
    to_commit_sha TEXT,
    -- Judge output:
    --   action: skip | restart-backend | restart-frontend |
    --           rebuild-backend | rebuild-frontend | rebuild-all | hold
    --   risk:    low | medium | high
    --   confidence: low | medium | high
    --   reasoning: free-text explanation (1-3 sentences)
    --   from_llm: 1 if judge LLM actually ran; 0 if fallback default
    action TEXT NOT NULL,
    risk TEXT NOT NULL,
    confidence TEXT NOT NULL,
    reasoning TEXT NOT NULL DEFAULT '',
    from_llm INTEGER NOT NULL DEFAULT 1,
    -- Lifecycle:
    --   pending  → judge has proposed, user hasn't acted
    --   applied  → user clicked Apply (or recommendation was auto-applied)
    --   overridden → user clicked a different button than recommended
    --   superseded → newer commit landed before this was acted on
    status TEXT NOT NULL DEFAULT 'pending',
    -- If overridden, the action the user actually chose. NULL otherwise.
    overridden_action TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    applied_at TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);
CREATE INDEX IF NOT EXISTS idx_deploy_decisions_project_status
  ON deploy_decisions(project_id, status, created_at);
"""


class SQLiteStateStore(StateStore):
    """SQLite-backed state store with WAL mode for crash safety."""

    def __init__(self, db_path: str | None = None) -> None:
        # Resolution order mirrors src/main.py — `CREWAI_DB_PATH` wins so
        # the docker-compose path `/app/data/crewai.db` (on the
        # `crewai_data` named volume) is picked up automatically when
        # callers instantiate the store with no explicit argument
        # (tests, scripts, etc.). Falls back to `./data/agent_team.db`
        # so non-Docker local invocations keep working unchanged.
        if db_path is None:
            db_path = (
                os.environ.get("CREWAI_DB_PATH")
                or os.environ.get("AIAGENT_DB_PATH")
                or os.environ.get("DATABASE_PATH")
                or "data/agent_team.db"
            )
        # Ensure the parent dir exists eagerly so a caller writing
        # outside the normal `initialize()` path (e.g. ad-hoc tooling
        # that opens the connection directly) doesn't hit ENOENT.
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._db = await aiosqlite.connect(self.db_path)
            self._db.row_factory = aiosqlite.Row
            # DELETE (rollback-journal) mode rather than WAL. Reasoning:
            # this DB is bind-mounted into the container from the host
            # so the host supervisor (a separate process) can read +
            # write the same file. Docker Desktop's bind-mount layer
            # on Windows breaks WAL's shared-memory mmap — a second
            # connection (from another process OR even from a separate
            # in-process aiosqlite instance) fails with "unable to
            # open database file" while the first connection is alive.
            # The primary connection ALSO holds a stale read snapshot
            # for external writes, with no reliable way to advance it.
            # DELETE mode sidesteps both problems: each query gets a
            # fresh view of the committed data. Trade-off is that
            # writers briefly block readers, but at our load (single
            # backend + supervisor polling every 5s + UI polling every
            # 5s) the contention is negligible.
            await self._db.execute("PRAGMA journal_mode=DELETE")
            await self._db.execute("PRAGMA foreign_keys=ON")
        return self._db

    async def initialize(self) -> None:
        db = await self._get_db()
        await db.executescript(SCHEMA_SQL)
        # Migrate: add columns that newer schema versions added (for existing DBs)
        migrations = [
            "ALTER TABLE subtasks ADD COLUMN output_text TEXT DEFAULT ''",
            "ALTER TABLE requests ADD COLUMN provider TEXT NOT NULL DEFAULT 'anthropic'",
            "ALTER TABLE requests ADD COLUMN published_files TEXT DEFAULT '[]'",
            "ALTER TABLE requests ADD COLUMN commit_sha TEXT",
            "ALTER TABLE requests ADD COLUMN commit_url TEXT",
            "ALTER TABLE requests ADD COLUMN code_commit_error TEXT",
            # Deployment judge fields (Milestone 1, hybrid agent). Populated by
            # the supervisor BEFORE it runs docker commands; lets the agent
            # decide skip/staging-only/full/hold based on the commit shape.
            "ALTER TABLE deployment_states ADD COLUMN strategy TEXT DEFAULT ''",
            "ALTER TABLE deployment_states ADD COLUMN strategy_reasoning TEXT DEFAULT ''",
            "ALTER TABLE deployment_states ADD COLUMN risk TEXT DEFAULT ''",
            # Project management (PM-02). project_id is nullable; app layer
            # defaults missing values to 'proj-unassigned' (seeded by PM-12).
            "ALTER TABLE requests ADD COLUMN project_id TEXT",
            # Project-driven Build (PDB-08). Cost attribution for single-agent
            # calls that produce a brief / PRD / tasks list — no Request gets
            # created in those paths so request_id is empty; project_artifact_id
            # carries the rollup link. Cost dashboard UNIONs across both keys.
            "ALTER TABLE token_usage ADD COLUMN project_artifact_id TEXT",
            # Project-driven Build (PDB-23). NULL = one-off Submit Request
            # (today's flow); set to T-XXXX when the dispatcher created this
            # Request from a project's finalized tasks list. The per-project
            # Story Board reads this back-link to display task ↔ request.
            "ALTER TABLE requests ADD COLUMN source_task_id TEXT",
            # Project-driven Build (PDB-43). Track when the artifact's
            # content was last mutated so the UI can show a banner when a
            # brief is edited AFTER the PRD has been finalized (i.e.
            # "your finalized PRD may be stale — regenerate?").
            "ALTER TABLE project_artifacts ADD COLUMN updated_at TIMESTAMP",
            # Review-driven regeneration — when a brief/PRD is regenerated
            # with user feedback, store the feedback that drove that
            # revision so the version-history view can show "v0.2 — added
            # rate limiting per review" instead of just a date. NULL for
            # first-draft (v0.1) artifacts that weren't driven by review.
            "ALTER TABLE project_artifacts ADD COLUMN review_input TEXT",
            # Same idea for task-list regeneration. All rows of a given
            # list_version share the same review_input (denormalized for
            # simplicity — a separate per-list table wasn't worth the join
            # cost at v1 scale).
            "ALTER TABLE project_tasks ADD COLUMN review_input TEXT",
            # Per-project working tree + per-project deploy (the
            # "every project is its own running app" feature). Adds:
            #   - kind: which template was scaffolded (web-app / api-service / frontend-app)
            #   - deploy_backend_port / deploy_frontend_port: allocated at create time, immutable for life of project
            #   - deploy_status: stopped | deploying | running | failed
            #   - deploy_url: launch URL when running
            #   - deploy_last_started_at: last "Deploy" timestamp
            #   - deploy_error: last failure message (if any)
            # Default `web-app` is the most common case; legacy projects
            # inherit it but won't have a scaffold on disk — the Deploy
            # endpoint rejects them with a clear error.
            "ALTER TABLE projects ADD COLUMN kind TEXT NOT NULL DEFAULT 'web-app'",
            "ALTER TABLE projects ADD COLUMN deploy_backend_port INTEGER",
            "ALTER TABLE projects ADD COLUMN deploy_frontend_port INTEGER",
            "ALTER TABLE projects ADD COLUMN deploy_status TEXT NOT NULL DEFAULT 'stopped'",
            "ALTER TABLE projects ADD COLUMN deploy_url TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE projects ADD COLUMN deploy_last_started_at TIMESTAMP",
            "ALTER TABLE projects ADD COLUMN deploy_error TEXT",
            # Partial unique indexes — enforce no two projects share a
            # backend or frontend port, but only when the port is set
            # (NULL means "not allocated yet"). SQLite supports
            # WHERE-clause partial indexes.
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_deploy_backend_port "
            "  ON projects(deploy_backend_port) WHERE deploy_backend_port IS NOT NULL",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_deploy_frontend_port "
            "  ON projects(deploy_frontend_port) WHERE deploy_frontend_port IS NOT NULL",
            # AI Deploy Judge (per-project) — Phase 1 of the smart-route
            # feature. last_deploy_commit_sha is the "baseline" the judge
            # measures drift from; deploy_judge_preferences is free-text
            # that the user can edit to teach the judge their project's
            # quirks ("tests are slow, prefer restart over rebuild" etc.)
            # which we feed into the prompt as additional context.
            "ALTER TABLE projects ADD COLUMN last_deploy_commit_sha TEXT",
            "ALTER TABLE projects ADD COLUMN deploy_judge_preferences TEXT   NOT NULL DEFAULT ''",
            # AI Deploy Judge (per-project) — Phase 4. When the user clicks
            # Apply / Override, the backend writes the chosen action here
            # AND flips deploy_status to pending_deploy. The supervisor's
            # next poll picks up both fields and dispatches the matching
            # docker compose invocation (Phase 5). NULL means "default to
            # the existing rebuild-all behaviour" — preserves backward
            # compatibility with manual Deploy button clicks that haven't
            # gone through the judge.
            "ALTER TABLE projects ADD COLUMN deploy_pending_action TEXT",
            # The deploy_decisions table itself — added here so an
            # existing DB without it gets the table on next boot. (The
            # SCHEMA_SQL block above also has it, but executescript
            # only runs CREATE TABLE IF NOT EXISTS, so this is a no-op
            # on a fresh DB and a creation on an old one.)
            (
                "CREATE TABLE IF NOT EXISTS deploy_decisions ("
                "  decision_id TEXT PRIMARY KEY,"
                "  project_id TEXT NOT NULL,"
                "  drift_summary TEXT NOT NULL DEFAULT '[]',"
                "  from_commit_sha TEXT,"
                "  to_commit_sha TEXT,"
                "  action TEXT NOT NULL,"
                "  risk TEXT NOT NULL,"
                "  confidence TEXT NOT NULL,"
                "  reasoning TEXT NOT NULL DEFAULT '',"
                "  from_llm INTEGER NOT NULL DEFAULT 1,"
                "  status TEXT NOT NULL DEFAULT 'pending',"
                "  overridden_action TEXT,"
                "  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,"
                "  applied_at TIMESTAMP,"
                "  FOREIGN KEY (project_id) REFERENCES projects(project_id)"
                ")"
            ),
            "CREATE INDEX IF NOT EXISTS idx_deploy_decisions_project_status "
            "  ON deploy_decisions(project_id, status, created_at)",
        ]
        for stmt in migrations:
            try:
                await db.execute(stmt)
            except Exception:
                pass  # Column already exists — ignore
        await db.commit()

        # PM-12 + PM-13: Seed the immutable Unassigned project and backfill
        # any pre-existing requests that don't have a project yet. Idempotent —
        # the INSERT is gated on a SELECT, the UPDATE is a no-op once every
        # request has a project_id.
        from src.models.base import (
            UNASSIGNED_PROJECT_ID,
        )  # local import — model file imports nothing from us, no cycle

        async with db.execute(
            "SELECT 1 FROM projects WHERE project_id = ?", (UNASSIGNED_PROJECT_ID,)
        ) as cur:
            exists = await cur.fetchone() is not None
        if not exists:
            await db.execute(
                """INSERT INTO projects
                   (project_id, name, description, status, color, icon, tags,
                    lead_user_id, repo_url, default_team, target_date,
                    template_id, created_by, created_at)
                   VALUES (?, ?, ?, 'active', ?, ?, '[]', NULL, '', NULL, NULL, NULL, NULL, ?)""",
                (
                    UNASSIGNED_PROJECT_ID,
                    "Unassigned",
                    "Default project for legacy or orphaned requests. Cannot be renamed, archived, or deleted.",
                    "#8080a0",  # gray, distinct from any active project
                    "folder",
                    datetime.utcnow().isoformat(),
                ),
            )
        await db.execute(
            "UPDATE requests SET project_id = ? WHERE project_id IS NULL OR project_id = ''",
            (UNASSIGNED_PROJECT_ID,),
        )
        await db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ── Requests ─────────────────────────────────

    async def create_request(self, request: Request) -> str:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO requests
               (request_id, description, task_type, priority, status, tags,
                created_by, created_at, estimated_cost_usd, provider,
                published_files, commit_sha, commit_url, project_id,
                source_task_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                request.request_id,
                request.description,
                request.task_type,
                request.priority,
                request.status,
                json.dumps(request.tags),
                request.created_by,
                request.created_at.isoformat(),
                request.estimated_cost_usd,
                request.provider,
                json.dumps(request.published_files),
                request.commit_sha,
                request.commit_url,
                request.project_id,
                request.source_task_id,
            ),
        )
        await db.commit()
        return request.request_id

    async def get_request(self, request_id: str) -> Request | None:
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM requests WHERE request_id = ?", (request_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_request(row)

    async def update_request(self, request: Request) -> None:
        db = await self._get_db()
        await db.execute(
            """UPDATE requests SET status=?, completed_at=?, actual_cost_usd=?,
               published_files=?, commit_sha=?, commit_url=?, code_commit_error=?,
               project_id=?
               WHERE request_id=?""",
            (
                request.status,
                request.completed_at.isoformat() if request.completed_at else None,
                request.actual_cost_usd,
                json.dumps(request.published_files),
                request.commit_sha,
                request.commit_url,
                request.code_commit_error,
                request.project_id,
                request.request_id,
            ),
        )
        await db.commit()

    async def list_requests(
        self, status: str | None = None, limit: int = 20, offset: int = 0
    ) -> list[Request]:
        db = await self._get_db()
        if status:
            sql = "SELECT * FROM requests WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params = (status, limit, offset)
        else:
            sql = "SELECT * FROM requests ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params = (limit, offset)
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_request(r) for r in rows]

    async def delete_request(self, request_id: str) -> None:
        """Cascade-delete a request and every table that references it.

        SQLite FKs are advisory by default in this schema (no ON DELETE CASCADE),
        so we delete children explicitly in dependency order inside a single
        transaction. Nothing is raised if rows don't exist — the operation is
        idempotent by design so the caller can re-try a failed delete safely.

        The project_tasks back-link (``project_tasks.request_id``) is NULLed
        rather than deleted — the task row itself remains as a record. The
        task keeps its ``task_status`` (e.g. 'failed') so the user still
        sees that it was dispatched once; the dangling pointer is just
        cleared so the task popup can't try to fetch a deleted request.
        Without this, REQ-FEC71B's DELETE on 2026-05-22 left T-103e9025
        pointing at a row that no longer existed — the popup loaded but
        couldn't render the agent timeline or commit info.
        """
        db = await self._get_db()
        # Children of stories must go before stories themselves
        await db.execute(
            "DELETE FROM acceptance_criteria WHERE story_id IN "
            "(SELECT story_id FROM stories WHERE request_id=?)",
            (request_id,),
        )
        await db.execute(
            "DELETE FROM test_cases WHERE story_id IN "
            "(SELECT story_id FROM stories WHERE request_id=?)",
            (request_id,),
        )
        # Direct children of requests
        for table in (
            "stories",
            "artifacts",
            "subtasks",
            "deployments",
            "deployment_states",
            "token_usage",
            "agent_traces",
            "documents",
            "notifications",
        ):
            await db.execute(f"DELETE FROM {table} WHERE request_id=?", (request_id,))
        # Project-task back-link: null it out so the task survives but the
        # pointer to the deleted request goes away. Idempotent.
        await db.execute(
            "UPDATE project_tasks SET request_id=NULL WHERE request_id=?",
            (request_id,),
        )
        # Finally the request itself
        await db.execute("DELETE FROM requests WHERE request_id=?", (request_id,))
        await db.commit()

    def _row_to_request(self, row: aiosqlite.Row) -> Request:
        # Some columns may not exist in older DBs — guard each
        def _safe_get(name: str, default: Any = None) -> Any:
            try:
                return row[name]
            except (IndexError, KeyError):
                return default

        provider = _safe_get("provider") or "claude_platform_aws"
        published_files_raw = _safe_get("published_files") or "[]"
        try:
            published_files = json.loads(published_files_raw) if published_files_raw else []
        except Exception:
            published_files = []
        return Request(
            request_id=row["request_id"],
            description=row["description"],
            task_type=row["task_type"],
            priority=row["priority"],
            status=RequestStatus(row["status"]),
            tags=json.loads(row["tags"]) if row["tags"] else [],
            created_by=row["created_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
            estimated_cost_usd=row["estimated_cost_usd"],
            actual_cost_usd=row["actual_cost_usd"],
            provider=provider,
            published_files=published_files,
            commit_sha=_safe_get("commit_sha"),
            commit_url=_safe_get("commit_url"),
            code_commit_error=_safe_get("code_commit_error"),
            project_id=_safe_get("project_id"),
            source_task_id=_safe_get("source_task_id"),
        )

    # ── Subtasks ─────────────────────────────────

    async def create_subtask(self, subtask: Subtask) -> str:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO subtasks
               (subtask_id, request_id, agent_id, status, input_artifacts, output_artifacts)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                subtask.subtask_id,
                subtask.request_id,
                subtask.agent_id,
                subtask.status,
                json.dumps(subtask.input_artifacts),
                json.dumps(subtask.output_artifacts),
            ),
        )
        await db.commit()
        return subtask.subtask_id

    async def get_subtask(self, subtask_id: str) -> Subtask | None:
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM subtasks WHERE subtask_id = ?", (subtask_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_subtask(row)

    async def update_subtask(self, subtask: Subtask) -> None:
        db = await self._get_db()
        await db.execute(
            """UPDATE subtasks SET status=?, output_artifacts=?, output_text=?, started_at=?,
               completed_at=?, error_message=? WHERE subtask_id=?""",
            (
                subtask.status,
                json.dumps(subtask.output_artifacts),
                subtask.output_text,
                subtask.started_at.isoformat() if subtask.started_at else None,
                subtask.completed_at.isoformat() if subtask.completed_at else None,
                subtask.error_message,
                subtask.subtask_id,
            ),
        )
        await db.commit()

    async def get_subtasks_for_request(self, request_id: str) -> list[Subtask]:
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM subtasks WHERE request_id = ?", (request_id,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_subtask(r) for r in rows]

    async def get_active_subtasks(self) -> list[Subtask]:
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM subtasks WHERE status = ? ORDER BY started_at IS NULL, started_at DESC",
            (SubtaskStatus.IN_PROGRESS,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_subtask(r) for r in rows]

    def _row_to_subtask(self, row: aiosqlite.Row) -> Subtask:
        return Subtask(
            subtask_id=row["subtask_id"],
            request_id=row["request_id"],
            agent_id=row["agent_id"],
            status=row["status"],
            input_artifacts=json.loads(row["input_artifacts"]) if row["input_artifacts"] else [],
            output_artifacts=json.loads(row["output_artifacts"]) if row["output_artifacts"] else [],
            output_text=row["output_text"] if "output_text" in row.keys() else "",
            started_at=(datetime.fromisoformat(row["started_at"]) if row["started_at"] else None),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
            error_message=row["error_message"],
        )

    # ── Artifacts ────────────────────────────────

    async def save_artifact(self, artifact: Artifact) -> str:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO artifacts
               (artifact_id, subtask_id, request_id, name, file_path, format, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                artifact.artifact_id,
                artifact.subtask_id,
                artifact.request_id,
                artifact.name,
                artifact.file_path,
                artifact.format,
                artifact.created_at.isoformat(),
            ),
        )
        await db.commit()
        return artifact.artifact_id

    async def get_artifacts_for_subtask(self, subtask_id: str) -> list[Artifact]:
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM artifacts WHERE subtask_id = ?", (subtask_id,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            Artifact(
                artifact_id=r["artifact_id"],
                subtask_id=r["subtask_id"],
                request_id=r["request_id"],
                name=r["name"],
                file_path=r["file_path"],
                format=r["format"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    # ── Stories ──────────────────────────────────

    async def create_story(self, story: Story) -> str:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO stories
               (story_id, request_id, title, description, status, priority,
                assigned_agent, coverage_pct, github_issue_number)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                story.story_id,
                story.request_id,
                story.title,
                story.description,
                story.status,
                story.priority,
                story.assigned_agent,
                story.coverage_pct,
                story.github_issue_number,
            ),
        )
        await db.commit()
        return story.story_id

    async def get_stories_for_request(self, request_id: str) -> list[Story]:
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM stories WHERE request_id = ?", (request_id,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            Story(
                story_id=r["story_id"],
                request_id=r["request_id"],
                title=r["title"],
                description=r["description"] or "",
                status=r["status"],
                priority=r["priority"],
                assigned_agent=r["assigned_agent"],
                coverage_pct=r["coverage_pct"],
                github_issue_number=r["github_issue_number"],
            )
            for r in rows
        ]

    async def update_story(self, story: Story) -> None:
        db = await self._get_db()
        await db.execute(
            """UPDATE stories SET status=?, assigned_agent=?, coverage_pct=?,
               github_issue_number=? WHERE story_id=?""",
            (
                story.status,
                story.assigned_agent,
                story.coverage_pct,
                story.github_issue_number,
                story.story_id,
            ),
        )
        await db.commit()

    # ── Acceptance Criteria ───────────────────────

    async def create_acceptance_criterion(self, ac: AcceptanceCriterion) -> str:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO acceptance_criteria
               (ac_id, story_id, criterion_text, given_clause, when_clause, then_clause, is_met)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                ac.ac_id,
                ac.story_id,
                ac.criterion_text,
                ac.given_clause,
                ac.when_clause,
                ac.then_clause,
                ac.is_met,
            ),
        )
        await db.commit()
        return ac.ac_id

    async def get_acceptance_criteria_for_story(self, story_id: str) -> list[AcceptanceCriterion]:
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM acceptance_criteria WHERE story_id = ?", (story_id,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            AcceptanceCriterion(
                ac_id=r["ac_id"],
                story_id=r["story_id"],
                criterion_text=r["criterion_text"],
                given_clause=r["given_clause"] or "",
                when_clause=r["when_clause"] or "",
                then_clause=r["then_clause"] or "",
                is_met=bool(r["is_met"]),
            )
            for r in rows
        ]

    async def update_acceptance_criterion(self, ac: AcceptanceCriterion) -> None:
        db = await self._get_db()
        await db.execute(
            "UPDATE acceptance_criteria SET is_met=? WHERE ac_id=?",
            (ac.is_met, ac.ac_id),
        )
        await db.commit()

    # ── Test Cases ────────────────────────────────

    async def create_test_case(self, tc: TestCase) -> str:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO test_cases
               (test_id, story_id, name, status, last_run_at)
               VALUES (?, ?, ?, ?, ?)""",
            (tc.test_id, tc.story_id, tc.name, tc.status, tc.last_run_at),
        )
        await db.commit()
        return tc.test_id

    async def get_test_cases_for_story(self, story_id: str) -> list[TestCase]:
        db = await self._get_db()
        async with db.execute("SELECT * FROM test_cases WHERE story_id = ?", (story_id,)) as cursor:
            rows = await cursor.fetchall()
        return [
            TestCase(
                test_id=r["test_id"],
                story_id=r["story_id"],
                name=r["name"],
                status=r["status"],
                last_run_at=r["last_run_at"],
            )
            for r in rows
        ]

    async def update_test_case(self, tc: TestCase) -> None:
        db = await self._get_db()
        await db.execute(
            "UPDATE test_cases SET status=?, last_run_at=? WHERE test_id=?",
            (tc.status, tc.last_run_at, tc.test_id),
        )
        await db.commit()

    # ── Prompt Studio ─────────────────────────────

    async def create_prompt_session(self, session: PromptSession) -> str:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO prompt_sessions
               (session_id, user_id, created_at, use_case, target_audience,
                desired_output, tone, constraints, options, provider,
                template_id, selected_variant_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session.session_id,
                session.user_id,
                session.created_at.isoformat(),
                session.use_case,
                session.target_audience,
                session.desired_output,
                session.tone,
                session.constraints,
                json.dumps(session.options),
                session.provider,
                session.template_id,
                session.selected_variant_id,
            ),
        )
        await db.commit()
        return session.session_id

    async def get_prompt_session(self, session_id: str) -> PromptSession | None:
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM prompt_sessions WHERE session_id = ?", (session_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_prompt_session(row)

    async def list_prompt_sessions_for_user(
        self, user_id: str, limit: int = 20, offset: int = 0
    ) -> list[PromptSession]:
        db = await self._get_db()
        async with db.execute(
            """SELECT * FROM prompt_sessions
               WHERE user_id = ?
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (user_id, limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_prompt_session(r) for r in rows]

    async def update_prompt_session_selection(
        self, session_id: str, selected_variant_id: str
    ) -> None:
        db = await self._get_db()
        await db.execute(
            "UPDATE prompt_sessions SET selected_variant_id=? WHERE session_id=?",
            (selected_variant_id, session_id),
        )
        await db.commit()

    async def create_prompt_variant(self, variant: PromptVariant) -> str:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO prompt_variants
               (variant_id, session_id, iteration, variant_index, approach,
                prompt_text, techniques, feedback_applied, generated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                variant.variant_id,
                variant.session_id,
                variant.iteration,
                variant.variant_index,
                variant.approach,
                variant.prompt_text,
                json.dumps(variant.techniques),
                variant.feedback_applied,
                variant.generated_at.isoformat(),
            ),
        )
        await db.commit()
        return variant.variant_id

    async def get_prompt_variants_for_session(self, session_id: str) -> list[PromptVariant]:
        db = await self._get_db()
        async with db.execute(
            """SELECT * FROM prompt_variants
               WHERE session_id = ?
               ORDER BY iteration ASC, variant_index ASC""",
            (session_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_prompt_variant(r) for r in rows]

    def _row_to_prompt_session(self, row: aiosqlite.Row) -> PromptSession:
        opts_raw = row["options"] or "{}"
        try:
            options = json.loads(opts_raw)
        except Exception:
            options = {}
        return PromptSession(
            session_id=row["session_id"],
            user_id=row["user_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            use_case=row["use_case"],
            target_audience=row["target_audience"] or "",
            desired_output=row["desired_output"] or "",
            tone=row["tone"] or "",
            constraints=row["constraints"] or "",
            options=options,
            provider=row["provider"] or "claude_platform_aws",
            template_id=row["template_id"],
            selected_variant_id=row["selected_variant_id"],
        )

    def _row_to_prompt_variant(self, row: aiosqlite.Row) -> PromptVariant:
        techs_raw = row["techniques"] or "[]"
        try:
            techniques = json.loads(techs_raw)
        except Exception:
            techniques = []
        return PromptVariant(
            variant_id=row["variant_id"],
            session_id=row["session_id"],
            iteration=row["iteration"],
            variant_index=row["variant_index"],
            approach=row["approach"] or "",
            prompt_text=row["prompt_text"],
            techniques=techniques,
            feedback_applied=row["feedback_applied"] or "",
            generated_at=datetime.fromisoformat(row["generated_at"]),
        )

    # ── Users ────────────────────────────────────

    async def create_user(self, user: User, password_hash: str) -> str:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO users
               (user_id, username, email, password_hash, role, is_active,
                must_change_password, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user.user_id,
                user.username,
                user.email,
                password_hash,
                user.role,
                user.is_active,
                user.must_change_password,
                user.created_at.isoformat(),
            ),
        )
        await db.commit()
        return user.user_id

    async def get_user_by_username(self, username: str) -> tuple[User, str] | None:
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        user = User(
            user_id=row["user_id"],
            username=row["username"],
            email=row["email"],
            role=UserRole(row["role"]),
            is_active=bool(row["is_active"]),
            must_change_password=bool(row["must_change_password"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            last_login_at=(
                datetime.fromisoformat(row["last_login_at"]) if row["last_login_at"] else None
            ),
        )
        return user, row["password_hash"]

    async def get_user(self, user_id: str) -> User | None:
        db = await self._get_db()
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return User(
            user_id=row["user_id"],
            username=row["username"],
            email=row["email"],
            role=UserRole(row["role"]),
            is_active=bool(row["is_active"]),
            must_change_password=bool(row["must_change_password"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            last_login_at=(
                datetime.fromisoformat(row["last_login_at"]) if row["last_login_at"] else None
            ),
        )

    async def list_users(self) -> list[User]:
        db = await self._get_db()
        async with db.execute("SELECT * FROM users ORDER BY created_at") as cursor:
            rows = await cursor.fetchall()
        return [
            User(
                user_id=r["user_id"],
                username=r["username"],
                email=r["email"],
                role=UserRole(r["role"]),
                is_active=bool(r["is_active"]),
                must_change_password=bool(r["must_change_password"]),
                created_at=datetime.fromisoformat(r["created_at"]),
                last_login_at=(
                    datetime.fromisoformat(r["last_login_at"]) if r["last_login_at"] else None
                ),
            )
            for r in rows
        ]

    async def update_user(self, user: User) -> None:
        db = await self._get_db()
        await db.execute(
            """UPDATE users SET email=?, role=?, is_active=?,
               must_change_password=?, last_login_at=? WHERE user_id=?""",
            (
                user.email,
                user.role,
                user.is_active,
                user.must_change_password,
                user.last_login_at.isoformat() if user.last_login_at else None,
                user.user_id,
            ),
        )
        await db.commit()

    async def update_password(self, user_id: str, password_hash: str) -> None:
        db = await self._get_db()
        await db.execute(
            "UPDATE users SET password_hash=?, must_change_password=0 WHERE user_id=?",
            (password_hash, user_id),
        )
        await db.commit()

    # ── Deployments ──────────────────────────────

    async def create_deployment(self, deployment: Deployment) -> str:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO deployments
               (deploy_id, request_id, git_sha, environment, status,
                previous_deploy_id, deployed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                deployment.deploy_id,
                deployment.request_id,
                deployment.git_sha,
                deployment.environment,
                deployment.status,
                deployment.previous_deploy_id,
                deployment.deployed_at.isoformat() if deployment.deployed_at else None,
            ),
        )
        await db.commit()
        return deployment.deploy_id

    async def get_deployment(self, deploy_id: str) -> Deployment | None:
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM deployments WHERE deploy_id = ?", (deploy_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_deployment(row)

    async def update_deployment(self, deployment: Deployment) -> None:
        db = await self._get_db()
        await db.execute(
            """UPDATE deployments SET status=?, verified_at=?, rolled_back_at=?
               WHERE deploy_id=?""",
            (
                deployment.status,
                deployment.verified_at.isoformat() if deployment.verified_at else None,
                deployment.rolled_back_at.isoformat() if deployment.rolled_back_at else None,
                deployment.deploy_id,
            ),
        )
        await db.commit()

    async def list_deployments(
        self, environment: str | None = None, limit: int = 20
    ) -> list[Deployment]:
        db = await self._get_db()
        if environment:
            sql = "SELECT * FROM deployments WHERE environment=? ORDER BY deployed_at DESC LIMIT ?"
            params = (environment, limit)
        else:
            sql = "SELECT * FROM deployments ORDER BY deployed_at DESC LIMIT ?"
            params = (limit,)
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_deployment(r) for r in rows]

    def _row_to_deployment(self, row: aiosqlite.Row) -> Deployment:
        return Deployment(
            deploy_id=row["deploy_id"],
            request_id=row["request_id"],
            git_sha=row["git_sha"],
            environment=row["environment"],
            status=row["status"],
            previous_deploy_id=row["previous_deploy_id"],
            deployed_at=(
                datetime.fromisoformat(row["deployed_at"]) if row["deployed_at"] else None
            ),
            verified_at=(
                datetime.fromisoformat(row["verified_at"]) if row["verified_at"] else None
            ),
            rolled_back_at=(
                datetime.fromisoformat(row["rolled_back_at"]) if row["rolled_back_at"] else None
            ),
        )

    # ── Notifications ────────────────────────────

    async def create_notification(self, notification: Notification) -> str:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO notifications
               (notification_id, event_id, severity, title, message,
                request_id, link_url, user_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                notification.notification_id,
                notification.event_id,
                notification.severity,
                notification.title,
                notification.message,
                notification.request_id,
                notification.link_url,
                notification.user_id,
                notification.created_at.isoformat(),
            ),
        )
        await db.commit()
        return notification.notification_id

    async def get_notifications(
        self, user_id: str | None = None, unread_only: bool = False, limit: int = 50
    ) -> list[Notification]:
        db = await self._get_db()
        conditions = []
        params: list = []
        if user_id:
            conditions.append("(user_id = ? OR user_id IS NULL)")
            params.append(user_id)
        if unread_only:
            conditions.append("read_at IS NULL")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM notifications {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [
            Notification(
                notification_id=r["notification_id"],
                event_id=r["event_id"],
                severity=r["severity"],
                title=r["title"],
                message=r["message"],
                request_id=r["request_id"],
                link_url=r["link_url"],
                user_id=r["user_id"],
                created_at=datetime.fromisoformat(r["created_at"]),
                read_at=(datetime.fromisoformat(r["read_at"]) if r["read_at"] else None),
                dismissed_at=(
                    datetime.fromisoformat(r["dismissed_at"]) if r["dismissed_at"] else None
                ),
            )
            for r in rows
        ]

    async def mark_notification_read(self, notification_id: str) -> None:
        db = await self._get_db()
        await db.execute(
            "UPDATE notifications SET read_at=? WHERE notification_id=?",
            (datetime.utcnow().isoformat(), notification_id),
        )
        await db.commit()

    async def mark_all_notifications_read(self, user_id: str) -> None:
        db = await self._get_db()
        now = datetime.utcnow().isoformat()
        await db.execute(
            "UPDATE notifications SET read_at=? WHERE (user_id=? OR user_id IS NULL) AND read_at IS NULL",
            (now, user_id),
        )
        await db.commit()

    # ── Token Usage ──────────────────────────────

    async def record_token_usage(self, usage: TokenUsage) -> None:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO token_usage
               (usage_id, request_id, subtask_id, agent_id, model,
                input_tokens, output_tokens, cost_usd, recorded_at,
                project_artifact_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                usage.usage_id,
                usage.request_id,
                usage.subtask_id,
                usage.agent_id,
                usage.model,
                usage.input_tokens,
                usage.output_tokens,
                usage.cost_usd,
                usage.recorded_at.isoformat(),
                usage.project_artifact_id,
            ),
        )
        await db.commit()

    async def get_token_usage_for_request(self, request_id: str) -> list[TokenUsage]:
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM token_usage WHERE request_id = ?", (request_id,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            TokenUsage(
                usage_id=r["usage_id"],
                request_id=r["request_id"],
                subtask_id=r["subtask_id"],
                agent_id=r["agent_id"],
                model=r["model"],
                input_tokens=r["input_tokens"],
                output_tokens=r["output_tokens"],
                cost_usd=r["cost_usd"],
                recorded_at=datetime.fromisoformat(r["recorded_at"]),
            )
            for r in rows
        ]

    async def get_daily_cost(self) -> float:
        db = await self._get_db()
        today = datetime.utcnow().date().isoformat()
        async with db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) as total FROM token_usage WHERE recorded_at >= ?",
            (today,),
        ) as cursor:
            row = await cursor.fetchone()
        return float(row["total"]) if row else 0.0

    async def get_monthly_cost(self) -> float:
        db = await self._get_db()
        first_of_month = datetime.utcnow().replace(day=1).date().isoformat()
        async with db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) as total FROM token_usage WHERE recorded_at >= ?",
            (first_of_month,),
        ) as cursor:
            row = await cursor.fetchone()
        return float(row["total"]) if row else 0.0

    # ── Metrics & Traces ─────────────────────────

    async def record_metric(self, metric: Metric) -> None:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO metrics (metric_id, metric_name, metric_value, labels, recorded_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                metric.metric_id,
                metric.metric_name,
                metric.metric_value,
                json.dumps(metric.labels),
                metric.recorded_at.isoformat(),
            ),
        )
        await db.commit()

    async def record_agent_trace(self, trace: AgentTrace) -> None:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO agent_traces
               (trace_id, request_id, agent_id, subtask_id, llm_calls, tool_calls,
                input_tokens, output_tokens, status, started_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trace.trace_id,
                trace.request_id,
                trace.agent_id,
                trace.subtask_id,
                trace.llm_calls,
                trace.tool_calls,
                trace.input_tokens,
                trace.output_tokens,
                trace.status,
                trace.started_at.isoformat(),
            ),
        )
        await db.commit()

    async def update_agent_trace(self, trace: AgentTrace) -> None:
        db = await self._get_db()
        await db.execute(
            """UPDATE agent_traces SET llm_calls=?, tool_calls=?, input_tokens=?,
               output_tokens=?, status=?, completed_at=?, duration_ms=?, error_message=?
               WHERE trace_id=? AND subtask_id=?""",
            (
                trace.llm_calls,
                trace.tool_calls,
                trace.input_tokens,
                trace.output_tokens,
                trace.status,
                trace.completed_at.isoformat() if trace.completed_at else None,
                trace.duration_ms,
                trace.error_message,
                trace.trace_id,
                trace.subtask_id,
            ),
        )
        await db.commit()

    # ── Documents ────────────────────────────────

    async def save_document(self, doc: Document) -> str:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO documents
               (document_id, request_id, doc_type, title, content, agent_id, version, tags, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                doc.document_id,
                doc.request_id,
                doc.doc_type,
                doc.title,
                doc.content,
                doc.agent_id,
                doc.version,
                json.dumps(doc.tags),
                doc.created_at.isoformat(),
            ),
        )
        await db.commit()
        return doc.document_id

    async def get_document(self, document_id: str) -> Document | None:
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM documents WHERE document_id = ?", (document_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_document(row)

    async def get_documents_for_request(self, request_id: str) -> list[Document]:
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM documents WHERE request_id = ? ORDER BY created_at", (request_id,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_document(r) for r in rows]

    async def search_documents(
        self, query: str, doc_type: str | None = None, limit: int = 10
    ) -> list[Document]:
        db = await self._get_db()
        # Split query into keywords and search title, content, tags
        keywords = [k.strip().lower() for k in query.split() if len(k.strip()) > 2]
        if not keywords:
            return []

        conditions = []
        params: list = []
        for kw in keywords[:5]:  # Max 5 keywords
            conditions.append(
                "(LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(tags) LIKE ?)"
            )
            params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])

        where = " AND ".join(conditions)
        if doc_type:
            where = f"doc_type = ? AND ({where})"
            params.insert(0, doc_type)

        sql = f"SELECT * FROM documents WHERE {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_document(r) for r in rows]

    async def update_document(self, doc: Document) -> None:
        db = await self._get_db()
        await db.execute(
            """UPDATE documents SET title=?, content=?, version=?, tags=?, updated_at=?
               WHERE document_id=?""",
            (
                doc.title,
                doc.content,
                doc.version,
                json.dumps(doc.tags),
                datetime.utcnow().isoformat(),
                doc.document_id,
            ),
        )
        await db.commit()

    async def delete_document(self, document_id: str) -> bool:
        """Hard-delete a document by id. Returns True if a row was removed.

        Documents are standalone artifacts (PRDs, code-review reports, test
        reports, etc.) — they aren't FK-referenced by anything else, so a
        single DELETE is safe.
        """
        db = await self._get_db()
        cursor = await db.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
        await db.commit()
        return (cursor.rowcount or 0) > 0

    def _row_to_document(self, row: aiosqlite.Row) -> Document:
        return Document(
            document_id=row["document_id"],
            request_id=row["request_id"],
            doc_type=row["doc_type"],
            title=row["title"],
            content=row["content"],
            agent_id=row["agent_id"],
            version=row["version"],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=(datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None),
        )

    # ── Projects ─────────────────────────────────

    async def create_project(self, project: Project) -> str:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO projects
               (project_id, name, description, status, color, icon, tags,
                lead_user_id, repo_url, default_team, target_date,
                template_id, created_by, created_at,
                kind, deploy_backend_port, deploy_frontend_port,
                deploy_status, deploy_url,
                deploy_last_started_at, deploy_error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?)""",
            (
                project.project_id,
                project.name,
                project.description,
                project.status,
                project.color,
                project.icon,
                json.dumps(project.tags),
                project.lead_user_id,
                project.repo_url,
                project.default_team,
                project.target_date.isoformat() if project.target_date else None,
                project.template_id,
                project.created_by,
                project.created_at.isoformat(),
                str(project.kind),
                project.deploy_backend_port,
                project.deploy_frontend_port,
                str(project.deploy_status),
                project.deploy_url,
                (
                    project.deploy_last_started_at.isoformat()
                    if project.deploy_last_started_at
                    else None
                ),
                project.deploy_error,
            ),
        )
        await db.commit()
        return project.project_id

    async def allocate_project_ports(self) -> tuple[int, int]:
        """Atomically pick the next available (backend, frontend) port
        pair for a new project. Used at project-creation time so the
        scaffolded ``docker-compose.yml`` can bake ports inline.

        Algorithm: ``MAX(<column>) + 1`` (or the base port if no
        projects have allocated ports yet). Cheap and predictable; gaps
        from deleted projects are not reused (plenty of headroom under
        16-bit space).

        Race-safety: aiosqlite serializes writes, but to be defensive
        we run the SELECT + read inside an IMMEDIATE transaction so a
        concurrent reader can't slip a write in between.

        Raises if ports would overflow 65535 (vanishingly unlikely).
        """
        from src.models.base import (
            PROJECT_BACKEND_PORT_BASE,
            PROJECT_FRONTEND_PORT_BASE,
        )

        db = await self._get_db()
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute(
                "SELECT COALESCE(MAX(deploy_backend_port), ? - 1) + 1, "
                "       COALESCE(MAX(deploy_frontend_port), ? - 1) + 1 "
                "FROM projects",
                (PROJECT_BACKEND_PORT_BASE, PROJECT_FRONTEND_PORT_BASE),
            )
            row = await cur.fetchone()
            backend_port = int(row[0])
            frontend_port = int(row[1])
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        if backend_port > 65535 or frontend_port > 65535:
            raise ValueError(
                f"Project port allocation exhausted: backend={backend_port}, "
                f"frontend={frontend_port}. Reclaim ports from deleted projects "
                f"or shift the base ports."
            )
        return backend_port, frontend_port

    async def get_project(self, project_id: str) -> Project | None:
        # Cross-process freshness: handled by DELETE journal mode at
        # the connection level (see _get_db). No per-call ceremony
        # required — the next query naturally sees the supervisor's
        # most recent committed deploy_status write.
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM projects WHERE project_id = ?", (project_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_project(row) if row else None

    async def list_projects(self, include_archived: bool = False) -> list[Project]:
        db = await self._get_db()
        # Sort by updated_at then created_at, both desc — matches the
        # "Last activity" default sort on the Projects list page (PUI-001).
        if include_archived:
            sql = "SELECT * FROM projects ORDER BY COALESCE(updated_at, created_at) DESC"
            params: tuple = ()
        else:
            sql = (
                "SELECT * FROM projects WHERE status = ? "
                "ORDER BY COALESCE(updated_at, created_at) DESC"
            )
            params = (ProjectStatus.ACTIVE,)
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_project(r) for r in rows]

    async def update_project(self, project: Project) -> None:
        db = await self._get_db()
        await db.execute(
            """UPDATE projects SET
                 name = ?, description = ?, status = ?, color = ?, icon = ?,
                 tags = ?, lead_user_id = ?, repo_url = ?, default_team = ?,
                 target_date = ?, template_id = ?, updated_at = ?
               WHERE project_id = ?""",
            (
                project.name,
                project.description,
                project.status,
                project.color,
                project.icon,
                json.dumps(project.tags),
                project.lead_user_id,
                project.repo_url,
                project.default_team,
                project.target_date.isoformat() if project.target_date else None,
                project.template_id,
                datetime.utcnow().isoformat(),
                project.project_id,
            ),
        )
        await db.commit()

    async def delete_project(self, project_id: str) -> None:
        db = await self._get_db()
        await db.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
        await db.commit()

    async def find_project_by_name(self, name: str, active_only: bool = True) -> Project | None:
        db = await self._get_db()
        if active_only:
            sql = "SELECT * FROM projects WHERE LOWER(name) = LOWER(?) AND status = ? LIMIT 1"
            params: tuple = (name, ProjectStatus.ACTIVE)
        else:
            sql = "SELECT * FROM projects WHERE LOWER(name) = LOWER(?) LIMIT 1"
            params = (name,)
        async with db.execute(sql, params) as cursor:
            row = await cursor.fetchone()
        return self._row_to_project(row) if row else None

    async def get_requests_for_project(self, project_id: str) -> list[Request]:
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM requests WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_request(r) for r in rows]

    async def list_commits_since_deploy(
        self,
        project_id: str,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return committed Requests for this project, ordered oldest-first.

        Drives the AI Deploy Judge's drift computation. A "commit" here
        is a Request whose code_commit stage published files to GitHub
        (i.e. commit_sha is non-NULL). Filtered to those completed after
        ``since`` when provided — usually the project's last successful
        deploy timestamp.

        Returns a list of dicts (not a Pydantic model) because the shape
        is consumed directly by the judge prompt's JSON renderer and by
        the UI panel's drift summary. Dict keys:
          - request_id, commit_sha
          - description (request description, truncated to 200 chars)
          - files (list[str] from published_files)
          - file_count (len(files); convenience for the UI)
          - completed_at (ISO string)
        """
        db = await self._get_db()
        sql = (
            "SELECT request_id, description, commit_sha, published_files, "
            "       completed_at "
            "FROM requests "
            "WHERE project_id = ? "
            "  AND commit_sha IS NOT NULL AND commit_sha != '' "
            "  AND completed_at IS NOT NULL"
        )
        params: list = [project_id]
        if since is not None:
            sql += " AND completed_at > ?"
            params.append(since.isoformat())
        sql += " ORDER BY completed_at ASC"
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            files_raw = r["published_files"] or "[]"
            try:
                files = json.loads(files_raw)
                if not isinstance(files, list):
                    files = []
            except (ValueError, TypeError):
                files = []
            out.append(
                {
                    "request_id": r["request_id"],
                    "commit_sha": r["commit_sha"],
                    "description": (r["description"] or "")[:200],
                    "files": files,
                    "file_count": len(files),
                    "completed_at": r["completed_at"],
                }
            )
        return out

    async def count_requests_for_project(self, project_id: str) -> dict[str, int]:
        """Per-status counts for the project detail page stat cards (PUI-003).
        Returns {total, active, completed, failed} with `active` meaning any
        non-terminal status."""
        db = await self._get_db()
        async with db.execute(
            "SELECT status, COUNT(*) AS n FROM requests WHERE project_id = ? GROUP BY status",
            (project_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        by_status = {r["status"]: r["n"] for r in rows}
        total = sum(by_status.values())
        completed = by_status.get(RequestStatus.COMPLETED, 0)
        failed = by_status.get(RequestStatus.FAILED, 0)
        cancelled = by_status.get(RequestStatus.CANCELLED, 0)
        active = total - completed - failed - cancelled
        return {
            "total": total,
            "active": active,
            "completed": completed,
            "failed": failed,
        }

    def _row_to_project(self, row: aiosqlite.Row) -> Project:
        # Deploy-related columns may be missing on rows persisted before
        # the per-project working-tree migration ran on this DB file —
        # defend with try/except so reads stay backwards-compatible.
        def _opt(col: str, default=None):
            try:
                v = row[col]
            except (IndexError, KeyError):
                return default
            return v if v is not None else default

        deploy_started = _opt("deploy_last_started_at")
        return Project(
            project_id=row["project_id"],
            name=row["name"],
            description=row["description"] or "",
            status=row["status"],
            color=row["color"] or "#00f0ff",
            icon=row["icon"] or "folder",
            tags=json.loads(row["tags"]) if row["tags"] else [],
            lead_user_id=row["lead_user_id"],
            repo_url=row["repo_url"] or "",
            default_team=row["default_team"],
            target_date=(
                datetime.fromisoformat(row["target_date"]) if row["target_date"] else None
            ),
            template_id=row["template_id"],
            created_by=row["created_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=(datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None),
            kind=_opt("kind", "web-app"),
            deploy_backend_port=_opt("deploy_backend_port"),
            deploy_frontend_port=_opt("deploy_frontend_port"),
            deploy_status=_opt("deploy_status", "stopped"),
            deploy_url=_opt("deploy_url", "") or "",
            deploy_last_started_at=(
                datetime.fromisoformat(deploy_started) if deploy_started else None
            ),
            deploy_error=_opt("deploy_error"),
            # AI Deploy Judge baseline + preferences. Both defensively
            # use _opt so reads against an old DB (pre-migration) still
            # produce a Project rather than KeyError-ing.
            last_deploy_commit_sha=_opt("last_deploy_commit_sha"),
            deploy_judge_preferences=_opt("deploy_judge_preferences", "") or "",
            deploy_pending_action=_opt("deploy_pending_action"),
        )

    async def update_project_deploy(
        self,
        project_id: str,
        *,
        deploy_status: str | None = None,
        deploy_url: str | None = None,
        deploy_last_started_at: datetime | None = None,
        deploy_error: str | None = None,
        last_deploy_commit_sha: str | None = None,
        deploy_pending_action: str | None = None,
    ) -> None:
        """Targeted update for deploy-lifecycle fields only. Used by
        the Deploy / Stop endpoints to flip status between the
        ``stopped → deploying → running | failed`` states without
        racing against the broader ``update_project`` call (which
        clobbers everything).

        Pass ``None`` for any field to leave it unchanged. To explicitly
        clear ``deploy_error`` pass an empty string (the DB-side check
        treats ``""`` as "set to NULL" — keeps the column tidy).

        ``last_deploy_commit_sha`` is advanced by the supervisor on a
        successful deploy (or by ``skip``-action decisions) so the AI
        Deploy Judge measures drift from the right baseline.

        ``deploy_pending_action`` is set by the AI Deploy Judge's
        Apply/Override endpoints to tell the supervisor which docker
        compose invocation to run when it picks up the pending_deploy
        row. Pass empty string to clear (e.g. when supervisor finishes
        and the project is back to running)."""
        sets = []
        params: list = []
        if deploy_status is not None:
            sets.append("deploy_status = ?")
            params.append(deploy_status)
        if deploy_url is not None:
            sets.append("deploy_url = ?")
            params.append(deploy_url)
        if deploy_last_started_at is not None:
            sets.append("deploy_last_started_at = ?")
            params.append(deploy_last_started_at.isoformat())
        if deploy_error is not None:
            sets.append("deploy_error = ?")
            params.append(deploy_error or None)  # "" → NULL for tidiness
        if last_deploy_commit_sha is not None:
            sets.append("last_deploy_commit_sha = ?")
            params.append(last_deploy_commit_sha or None)
        if deploy_pending_action is not None:
            sets.append("deploy_pending_action = ?")
            params.append(deploy_pending_action or None)
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(datetime.utcnow().isoformat())
        params.append(project_id)
        db = await self._get_db()
        await db.execute(
            f"UPDATE projects SET {', '.join(sets)} WHERE project_id = ?",
            tuple(params),
        )
        await db.commit()

    async def update_project_deploy_preferences(
        self,
        project_id: str,
        preferences: str,
    ) -> None:
        """Persist the user's free-text Deploy Judge preferences. Fed into
        the judge's prompt as additional context so it learns the user's
        idiosyncratic conventions ('treat src/state/ as rebuild-backend
        always')."""
        db = await self._get_db()
        await db.execute(
            "UPDATE projects SET deploy_judge_preferences = ?, updated_at = ? WHERE project_id = ?",
            (preferences or "", datetime.utcnow().isoformat(), project_id),
        )
        await db.commit()

    # ── AI Deploy Judge (per-project) ────────────

    async def create_deploy_decision(self, decision: DeployDecision) -> str:
        """Insert a fresh judge recommendation. Caller should call
        supersede_pending_decisions(project_id) first to mark any older
        PENDING rows as SUPERSEDED — that keeps the UI's "current
        recommendation" query (`get_latest_pending_decision`) returning
        at most one row."""
        db = await self._get_db()
        await db.execute(
            """INSERT INTO deploy_decisions
               (decision_id, project_id, drift_summary, from_commit_sha,
                to_commit_sha, action, risk, confidence, reasoning,
                from_llm, status, overridden_action, created_at,
                applied_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                decision.decision_id,
                decision.project_id,
                json.dumps(decision.drift_summary),
                decision.from_commit_sha,
                decision.to_commit_sha,
                str(decision.action),
                str(decision.risk),
                str(decision.confidence),
                decision.reasoning,
                1 if decision.from_llm else 0,
                str(decision.status),
                (
                    str(decision.overridden_action)
                    if decision.overridden_action is not None
                    else None
                ),
                decision.created_at.isoformat(),
                (decision.applied_at.isoformat() if decision.applied_at is not None else None),
            ),
        )
        await db.commit()
        return decision.decision_id

    async def get_latest_pending_decision(
        self,
        project_id: str,
    ) -> DeployDecision | None:
        db = await self._get_db()
        async with db.execute(
            """SELECT * FROM deploy_decisions
               WHERE project_id = ? AND status = 'pending'
               ORDER BY created_at DESC LIMIT 1""",
            (project_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_deploy_decision(row) if row else None

    async def supersede_pending_decisions(self, project_id: str) -> int:
        """Mark all PENDING rows for this project SUPERSEDED so the
        next get_latest_pending_decision returns the fresh one."""
        db = await self._get_db()
        cursor = await db.execute(
            "UPDATE deploy_decisions SET status = 'superseded' "
            "WHERE project_id = ? AND status = 'pending'",
            (project_id,),
        )
        await db.commit()
        return cursor.rowcount or 0

    async def mark_decision_applied(self, decision_id: str) -> None:
        db = await self._get_db()
        await db.execute(
            "UPDATE deploy_decisions SET status = 'applied', applied_at = ? "
            "WHERE decision_id = ? AND status = 'pending'",
            (datetime.utcnow().isoformat(), decision_id),
        )
        await db.commit()

    async def mark_decision_overridden(
        self,
        decision_id: str,
        overridden_action: str,
    ) -> None:
        db = await self._get_db()
        await db.execute(
            "UPDATE deploy_decisions SET status = 'overridden', "
            "overridden_action = ?, applied_at = ? "
            "WHERE decision_id = ? AND status = 'pending'",
            (overridden_action, datetime.utcnow().isoformat(), decision_id),
        )
        await db.commit()

    async def list_recent_overrides(
        self,
        project_id: str,
        limit: int = 5,
    ) -> list[DeployDecision]:
        db = await self._get_db()
        async with db.execute(
            """SELECT * FROM deploy_decisions
               WHERE project_id = ? AND status = 'overridden'
               ORDER BY created_at DESC LIMIT ?""",
            (project_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_deploy_decision(r) for r in rows]

    def _row_to_deploy_decision(self, row: aiosqlite.Row) -> DeployDecision:
        return DeployDecision(
            decision_id=row["decision_id"],
            project_id=row["project_id"],
            drift_summary=json.loads(row["drift_summary"] or "[]"),
            from_commit_sha=row["from_commit_sha"],
            to_commit_sha=row["to_commit_sha"],
            action=row["action"],
            risk=row["risk"],
            confidence=row["confidence"],
            reasoning=row["reasoning"] or "",
            from_llm=bool(row["from_llm"]),
            status=row["status"],
            overridden_action=row["overridden_action"],
            created_at=datetime.fromisoformat(row["created_at"]),
            applied_at=(datetime.fromisoformat(row["applied_at"]) if row["applied_at"] else None),
        )

    # ── Project Artifacts (PDB-04) ───────────────

    async def create_artifact(self, artifact: ProjectArtifact) -> str:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO project_artifacts
               (artifact_id, project_id, kind, version, status, content,
                created_by, created_at, finalized_at, finalized_by,
                review_input)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                artifact.artifact_id,
                artifact.project_id,
                str(artifact.kind),
                artifact.version,
                str(artifact.status),
                artifact.content,
                artifact.created_by,
                artifact.created_at.isoformat(),
                artifact.finalized_at.isoformat() if artifact.finalized_at else None,
                artifact.finalized_by,
                artifact.review_input,
            ),
        )
        await db.commit()
        return artifact.artifact_id

    async def get_artifact(
        self,
        project_id: str,
        kind: ArtifactKind,
        version: int | None = None,
    ) -> ProjectArtifact | None:
        db = await self._get_db()
        if version is None:
            # Latest by version. Ties impossible per UNIQUE constraint.
            sql = (
                "SELECT * FROM project_artifacts "
                "WHERE project_id = ? AND kind = ? "
                "ORDER BY version DESC LIMIT 1"
            )
            params: tuple = (project_id, str(kind))
        else:
            sql = (
                "SELECT * FROM project_artifacts WHERE project_id = ? AND kind = ? AND version = ?"
            )
            params = (project_id, str(kind), version)
        async with db.execute(sql, params) as cursor:
            row = await cursor.fetchone()
        return self._row_to_artifact(row) if row else None

    async def list_artifacts(self, project_id: str, kind: ArtifactKind) -> list[ProjectArtifact]:
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM project_artifacts "
            "WHERE project_id = ? AND kind = ? "
            "ORDER BY version DESC",
            (project_id, str(kind)),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_artifact(r) for r in rows]

    async def update_artifact_content(self, artifact_id: str, content: str) -> None:
        db = await self._get_db()
        await db.execute(
            "UPDATE project_artifacts SET content = ?, updated_at = ? WHERE artifact_id = ?",
            (content, datetime.utcnow().isoformat(), artifact_id),
        )
        await db.commit()

    async def finalize_artifact(
        self, artifact_id: str, finalized_by: str | None = None
    ) -> ProjectArtifact:
        """Atomic flip: any other finalized row for the same (project_id, kind)
        gets archived, then this row is marked finalized with timestamps."""
        db = await self._get_db()
        async with db.execute(
            "SELECT project_id, kind FROM project_artifacts WHERE artifact_id = ?",
            (artifact_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            raise ValueError(f"Artifact {artifact_id!r} not found.")
        project_id, kind = row["project_id"], row["kind"]

        now_iso = datetime.utcnow().isoformat()
        await db.execute("BEGIN")
        try:
            await db.execute(
                "UPDATE project_artifacts SET status = ? "
                "WHERE project_id = ? AND kind = ? AND status = ? AND artifact_id != ?",
                (
                    str(ArtifactStatus.ARCHIVED),
                    project_id,
                    kind,
                    str(ArtifactStatus.FINALIZED),
                    artifact_id,
                ),
            )
            await db.execute(
                "UPDATE project_artifacts SET status = ?, finalized_at = ?, finalized_by = ? "
                "WHERE artifact_id = ?",
                (str(ArtifactStatus.FINALIZED), now_iso, finalized_by, artifact_id),
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise

        result = await self.get_artifact_by_id(artifact_id)
        if result is None:
            # Shouldn't happen — we just updated it.
            raise RuntimeError(f"Artifact {artifact_id!r} vanished after finalize.")
        return result

    async def get_artifact_by_id(self, artifact_id: str) -> ProjectArtifact | None:
        """Lookup by primary key. Used by routes that already know the
        artifact_id and don't need the (project_id, kind) shape."""
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM project_artifacts WHERE artifact_id = ?",
            (artifact_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_artifact(row) if row else None

    async def delete_artifacts(self, project_id: str, kind: ArtifactKind) -> int:
        """Hard-delete every artifact row matching (project_id, kind).
        Returns the row count for the API response so the UI can show
        'Deleted N versions.' Used by ``DELETE /projects/:id/prd``."""
        db = await self._get_db()
        cursor = await db.execute(
            "DELETE FROM project_artifacts WHERE project_id = ? AND kind = ?",
            (project_id, str(kind)),
        )
        deleted = cursor.rowcount or 0
        await db.commit()
        return deleted

    async def delete_artifact_by_id(self, artifact_id: str) -> bool:
        """Hard-delete a single artifact row by id. Returns True if a row
        was removed. Used by the generate-PRD / generate-API-spec routes
        to clean up the empty draft they create up front when the agent
        call then fails or returns empty — otherwise the orphaned empty
        draft becomes the latest version and the UI shows a blank editor
        next time the user opens the project."""
        db = await self._get_db()
        cursor = await db.execute(
            "DELETE FROM project_artifacts WHERE artifact_id = ?",
            (artifact_id,),
        )
        await db.commit()
        return (cursor.rowcount or 0) > 0

    def _row_to_artifact(self, row: aiosqlite.Row) -> ProjectArtifact:
        # `updated_at` + `review_input` may be missing on rows from before
        # the migration that added them — defend with try/except.
        try:
            updated_at_raw = row["updated_at"]
        except (IndexError, KeyError):
            updated_at_raw = None
        try:
            review_input = row["review_input"]
        except (IndexError, KeyError):
            review_input = None
        return ProjectArtifact(
            artifact_id=row["artifact_id"],
            project_id=row["project_id"],
            kind=ArtifactKind(row["kind"]),
            version=int(row["version"]),
            status=ArtifactStatus(row["status"]),
            content=row["content"] or "",
            created_by=row["created_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=(datetime.fromisoformat(updated_at_raw) if updated_at_raw else None),
            finalized_at=(
                datetime.fromisoformat(row["finalized_at"]) if row["finalized_at"] else None
            ),
            finalized_by=row["finalized_by"],
            review_input=review_input,
        )

    # ── Project Tasks (PDB-15) ───────────────────

    async def create_task(self, task: ProjectTask) -> str:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO project_tasks
               (task_id, project_id, list_version, list_status, ordinal,
                title, description, task_type, priority, estimated_agent,
                task_status, request_id, amended, created_at, updated_at,
                review_input)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.task_id,
                task.project_id,
                task.list_version,
                str(task.list_status),
                task.ordinal,
                task.title,
                task.description,
                task.task_type,
                task.priority,
                task.estimated_agent,
                str(task.task_status),
                task.request_id,
                1 if task.amended else 0,
                task.created_at.isoformat(),
                task.updated_at.isoformat() if task.updated_at else None,
                task.review_input,
            ),
        )
        await db.commit()
        return task.task_id

    async def list_tasks_for_project(
        self,
        project_id: str,
        list_status: ArtifactStatus | None = None,
        list_version: int | None = None,
    ) -> list[ProjectTask]:
        db = await self._get_db()

        # When neither filter is supplied, default to "latest version" so
        # the UI doesn't have to pull a mixed bag of archived + current
        # rows. Pick the max(list_version) for this project that ISN'T
        # archived if any non-archived versions exist; otherwise fall back
        # to overall max.
        if list_version is None and list_status is None:
            async with db.execute(
                "SELECT COALESCE(MAX(list_version), 0) AS v FROM project_tasks "
                "WHERE project_id = ? AND list_status != ?",
                (project_id, str(ArtifactStatus.ARCHIVED)),
            ) as cursor:
                row = await cursor.fetchone()
                v = int(row["v"]) if row and row["v"] else 0
            if v == 0:
                # No non-archived rows — return empty rather than serving
                # archived data the UI doesn't ask for.
                return []
            list_version = v

        where = ["project_id = ?"]
        params: list = [project_id]
        if list_version is not None:
            where.append("list_version = ?")
            params.append(list_version)
        if list_status is not None:
            where.append("list_status = ?")
            params.append(str(list_status))

        sql = "SELECT * FROM project_tasks WHERE " + " AND ".join(where) + " ORDER BY ordinal ASC"
        async with db.execute(sql, tuple(params)) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_task(r) for r in rows]

    async def get_task(self, task_id: str) -> ProjectTask | None:
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM project_tasks WHERE task_id = ?", (task_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_task(row) if row else None

    async def update_task(self, task_id: str, fields: dict) -> ProjectTask:
        """Whitelisted PATCH — silently ignores unknown keys to keep the
        route layer free of awkward field-by-field plumbing."""
        allowed = {
            "title",
            "description",
            "task_type",
            "priority",
            "estimated_agent",
            "ordinal",
            "amended",
        }
        sets = []
        params: list = []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "amended":
                params.append(1 if v else 0)
            else:
                params.append(v)
            sets.append(f"{k} = ?")
        if not sets:
            existing = await self.get_task(task_id)
            if existing is None:
                raise ValueError(f"Task {task_id!r} not found.")
            return existing
        sets.append("updated_at = ?")
        params.append(datetime.utcnow().isoformat())
        params.append(task_id)

        db = await self._get_db()
        await db.execute(
            f"UPDATE project_tasks SET {', '.join(sets)} WHERE task_id = ?",
            tuple(params),
        )
        await db.commit()
        updated = await self.get_task(task_id)
        if updated is None:
            raise ValueError(f"Task {task_id!r} not found after update.")
        return updated

    async def set_task_status(
        self,
        task_id: str,
        task_status: TaskStatus,
        request_id: str | None = None,
    ) -> None:
        db = await self._get_db()
        now_iso = datetime.utcnow().isoformat()
        if request_id is not None:
            await db.execute(
                "UPDATE project_tasks "
                "SET task_status = ?, request_id = ?, updated_at = ? "
                "WHERE task_id = ?",
                (str(task_status), request_id, now_iso, task_id),
            )
        else:
            await db.execute(
                "UPDATE project_tasks SET task_status = ?, updated_at = ? WHERE task_id = ?",
                (str(task_status), now_iso, task_id),
            )
        await db.commit()

    async def finalize_task_list(self, project_id: str, list_version: int) -> None:
        """Atomic flip per PDB-15: archive any other finalized list_version
        for this project, then mark every row of `list_version` as finalized."""
        db = await self._get_db()
        await db.execute("BEGIN")
        try:
            await db.execute(
                "UPDATE project_tasks SET list_status = ? "
                "WHERE project_id = ? AND list_status = ? AND list_version != ?",
                (
                    str(ArtifactStatus.ARCHIVED),
                    project_id,
                    str(ArtifactStatus.FINALIZED),
                    list_version,
                ),
            )
            await db.execute(
                "UPDATE project_tasks SET list_status = ? "
                "WHERE project_id = ? AND list_version = ?",
                (str(ArtifactStatus.FINALIZED), project_id, list_version),
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    async def archive_task_list(self, project_id: str, list_version: int) -> None:
        db = await self._get_db()
        await db.execute(
            "UPDATE project_tasks SET list_status = ? WHERE project_id = ? AND list_version = ?",
            (str(ArtifactStatus.ARCHIVED), project_id, list_version),
        )
        await db.commit()

    async def delete_task_list_draft(self, project_id: str, list_version: int) -> None:
        db = await self._get_db()
        await db.execute(
            "DELETE FROM project_tasks "
            "WHERE project_id = ? AND list_version = ? AND list_status = ?",
            (project_id, list_version, str(ArtifactStatus.DRAFT)),
        )
        await db.commit()

    async def delete_task(self, task_id: str) -> None:
        """Single-row delete. Detaches any linked Request first so the
        Request stays valid but loses its source_task_id back-link."""
        db = await self._get_db()
        await db.execute(
            "UPDATE requests SET source_task_id = NULL WHERE source_task_id = ?",
            (task_id,),
        )
        await db.execute(
            "DELETE FROM project_tasks WHERE task_id = ?",
            (task_id,),
        )
        await db.commit()

    def _row_to_task(self, row: aiosqlite.Row) -> ProjectTask:
        try:
            review_input = row["review_input"]
        except (IndexError, KeyError):
            review_input = None
        return ProjectTask(
            task_id=row["task_id"],
            project_id=row["project_id"],
            list_version=int(row["list_version"]),
            list_status=ArtifactStatus(row["list_status"]),
            ordinal=int(row["ordinal"]),
            title=row["title"],
            description=row["description"] or "",
            task_type=row["task_type"] or "feature_request",
            priority=row["priority"] or "medium",
            estimated_agent=row["estimated_agent"],
            task_status=TaskStatus(row["task_status"]),
            request_id=row["request_id"],
            amended=bool(row["amended"]),
            review_input=review_input,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=(datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None),
        )

    # ── Build Session Messages (PDB-33) ──────────

    async def create_message(self, message: BuildMessage) -> str:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO build_session_messages
               (message_id, project_id, role, content, tool_calls,
                created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                message.message_id,
                message.project_id,
                message.role,
                message.content,
                json.dumps(message.tool_calls) if message.tool_calls else None,
                message.created_at.isoformat(),
                message.created_by,
            ),
        )
        await db.commit()
        return message.message_id

    async def list_messages_for_project(
        self,
        project_id: str,
        limit: int = 200,
        before: str | None = None,
    ) -> list[BuildMessage]:
        db = await self._get_db()
        # Pull the most recent `limit` rows; reverse to chronological so
        # the UI can append in order.
        if before:
            # Cursor-based pagination — fetch the timestamp of the cursor
            # row, then ask for older messages.
            async with db.execute(
                "SELECT created_at FROM build_session_messages WHERE message_id = ?",
                (before,),
            ) as cursor:
                cursor_row = await cursor.fetchone()
            if not cursor_row:
                return []
            cursor_ts = cursor_row["created_at"]
            async with db.execute(
                "SELECT * FROM build_session_messages "
                "WHERE project_id = ? AND created_at < ? "
                "ORDER BY created_at DESC LIMIT ?",
                (project_id, cursor_ts, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with db.execute(
                "SELECT * FROM build_session_messages "
                "WHERE project_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        # Reverse for chronological order.
        return [self._row_to_message(r) for r in reversed(rows)]

    async def delete_messages_for_project(self, project_id: str) -> int:
        """Wipe the build-chat transcript for a project. Returns the
        rowcount so the API can report 'cleared N messages'. Single
        DELETE — no cascade needed (build_session_messages doesn't
        own any referencing tables)."""
        db = await self._get_db()
        cursor = await db.execute(
            "DELETE FROM build_session_messages WHERE project_id = ?",
            (project_id,),
        )
        deleted = cursor.rowcount or 0
        await db.commit()
        return deleted

    def _row_to_message(self, row: aiosqlite.Row) -> BuildMessage:
        tool_calls_raw = row["tool_calls"]
        try:
            tool_calls = json.loads(tool_calls_raw) if tool_calls_raw else None
        except Exception:
            tool_calls = None
        return BuildMessage(
            message_id=row["message_id"],
            project_id=row["project_id"],
            role=row["role"],
            content=row["content"] or "",
            tool_calls=tool_calls,
            created_at=datetime.fromisoformat(row["created_at"]),
            created_by=row["created_by"],
        )

    # ── Deployment State Machine ─────────────────

    async def create_deployment_state(self, state: DeploymentState) -> str:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO deployment_states
               (deployment_id, request_id, commit_sha, current_step, step_history,
                files_committed, started_at, rollback_sha)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                state.deployment_id,
                state.request_id,
                state.commit_sha,
                state.current_step,
                json.dumps(state.step_history),
                json.dumps(state.files_committed),
                state.started_at.isoformat(),
                state.rollback_sha,
            ),
        )
        await db.commit()
        return state.deployment_id

    async def get_deployment_state(self, deployment_id: str) -> DeploymentState | None:
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM deployment_states WHERE deployment_id = ?", (deployment_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_deployment_state(row)

    async def get_deployment_state_for_request(self, request_id: str) -> DeploymentState | None:
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM deployment_states WHERE request_id = ? ORDER BY started_at DESC LIMIT 1",
            (request_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return self._row_to_deployment_state(row)

    async def get_pending_deployments(self) -> list[DeploymentState]:
        """Get deployments waiting for the sidecar to pick up."""
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM deployment_states WHERE current_step = 'code_committed' ORDER BY started_at"
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_deployment_state(r) for r in rows]

    async def update_deployment_state(self, state: DeploymentState) -> None:
        db = await self._get_db()
        await db.execute(
            """UPDATE deployment_states SET commit_sha=?, current_step=?, step_history=?,
               files_committed=?, updated_at=?, completed_at=?, error_message=?, rollback_sha=?
               WHERE deployment_id=?""",
            (
                state.commit_sha,
                state.current_step,
                json.dumps(state.step_history),
                json.dumps(state.files_committed),
                datetime.utcnow().isoformat(),
                state.completed_at.isoformat() if state.completed_at else None,
                state.error_message,
                state.rollback_sha,
                state.deployment_id,
            ),
        )
        await db.commit()

    def _row_to_deployment_state(self, row: aiosqlite.Row) -> DeploymentState:
        # The judge fields are added by a later ALTER TABLE migration; for very
        # old DBs the columns may not be present, so look them up defensively.
        def _safe_get(name: str, default: Any = "") -> Any:
            try:
                return row[name]
            except (IndexError, KeyError):
                return default

        return DeploymentState(
            deployment_id=row["deployment_id"],
            request_id=row["request_id"],
            commit_sha=row["commit_sha"] or "",
            current_step=row["current_step"],
            step_history=json.loads(row["step_history"]) if row["step_history"] else [],
            files_committed=json.loads(row["files_committed"]) if row["files_committed"] else [],
            started_at=datetime.fromisoformat(row["started_at"]),
            updated_at=(datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
            error_message=row["error_message"],
            rollback_sha=row["rollback_sha"] or "",
            strategy=_safe_get("strategy") or "",
            strategy_reasoning=_safe_get("strategy_reasoning") or "",
            risk=_safe_get("risk") or "",
        )
