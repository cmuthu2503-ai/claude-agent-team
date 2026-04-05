"""SQLite implementation of StateStore — WAL mode for crash safety."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

from src.models.base import (
    AgentTrace,
    Artifact,
    Deployment,
    Metric,
    Notification,
    Request,
    RequestStatus,
    Story,
    Subtask,
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
    actual_cost_usd REAL
);

CREATE TABLE IF NOT EXISTS subtasks (
    subtask_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    input_artifacts TEXT DEFAULT '[]',
    output_artifacts TEXT DEFAULT '[]',
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

-- Indexes
CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
CREATE INDEX IF NOT EXISTS idx_requests_created ON requests(created_at);
CREATE INDEX IF NOT EXISTS idx_subtasks_request ON subtasks(request_id);
CREATE INDEX IF NOT EXISTS idx_stories_request ON stories(request_id);
CREATE INDEX IF NOT EXISTS idx_test_cases_story ON test_cases(story_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_request ON token_usage(request_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_recorded ON token_usage(recorded_at);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, read_at);
CREATE INDEX IF NOT EXISTS idx_metrics_name_time ON metrics(metric_name, recorded_at);
CREATE INDEX IF NOT EXISTS idx_agent_traces_request ON agent_traces(request_id);
CREATE INDEX IF NOT EXISTS idx_deployments_env ON deployments(environment, status);
"""


class SQLiteStateStore(StateStore):
    """SQLite-backed state store with WAL mode for crash safety."""

    def __init__(self, db_path: str = "data/agent_team.db") -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._db = await aiosqlite.connect(self.db_path)
            self._db.row_factory = aiosqlite.Row
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute("PRAGMA foreign_keys=ON")
        return self._db

    async def initialize(self) -> None:
        db = await self._get_db()
        await db.executescript(SCHEMA_SQL)
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
                created_by, created_at, estimated_cost_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            """UPDATE requests SET status=?, completed_at=?, actual_cost_usd=?
               WHERE request_id=?""",
            (
                request.status,
                request.completed_at.isoformat() if request.completed_at else None,
                request.actual_cost_usd,
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

    def _row_to_request(self, row: aiosqlite.Row) -> Request:
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
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"]
                else None
            ),
            estimated_cost_usd=row["estimated_cost_usd"],
            actual_cost_usd=row["actual_cost_usd"],
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
            """UPDATE subtasks SET status=?, output_artifacts=?, started_at=?,
               completed_at=?, error_message=? WHERE subtask_id=?""",
            (
                subtask.status,
                json.dumps(subtask.output_artifacts),
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

    def _row_to_subtask(self, row: aiosqlite.Row) -> Subtask:
        return Subtask(
            subtask_id=row["subtask_id"],
            request_id=row["request_id"],
            agent_id=row["agent_id"],
            status=row["status"],
            input_artifacts=json.loads(row["input_artifacts"]) if row["input_artifacts"] else [],
            output_artifacts=json.loads(row["output_artifacts"]) if row["output_artifacts"] else [],
            started_at=(
                datetime.fromisoformat(row["started_at"]) if row["started_at"] else None
            ),
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
                story.story_id, story.request_id, story.title, story.description,
                story.status, story.priority, story.assigned_agent,
                story.coverage_pct, story.github_issue_number,
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
                story_id=r["story_id"], request_id=r["request_id"],
                title=r["title"], description=r["description"] or "",
                status=r["status"], priority=r["priority"],
                assigned_agent=r["assigned_agent"], coverage_pct=r["coverage_pct"],
                github_issue_number=r["github_issue_number"],
            )
            for r in rows
        ]

    async def update_story(self, story: Story) -> None:
        db = await self._get_db()
        await db.execute(
            """UPDATE stories SET status=?, assigned_agent=?, coverage_pct=?,
               github_issue_number=? WHERE story_id=?""",
            (story.status, story.assigned_agent, story.coverage_pct,
             story.github_issue_number, story.story_id),
        )
        await db.commit()

    # ── Users ────────────────────────────────────

    async def create_user(self, user: User, password_hash: str) -> str:
        db = await self._get_db()
        await db.execute(
            """INSERT INTO users
               (user_id, username, email, password_hash, role, is_active,
                must_change_password, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user.user_id, user.username, user.email, password_hash,
                user.role, user.is_active, user.must_change_password,
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
            user_id=row["user_id"], username=row["username"], email=row["email"],
            role=UserRole(row["role"]), is_active=bool(row["is_active"]),
            must_change_password=bool(row["must_change_password"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            last_login_at=(
                datetime.fromisoformat(row["last_login_at"]) if row["last_login_at"] else None
            ),
        )
        return user, row["password_hash"]

    async def get_user(self, user_id: str) -> User | None:
        db = await self._get_db()
        async with db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return User(
            user_id=row["user_id"], username=row["username"], email=row["email"],
            role=UserRole(row["role"]), is_active=bool(row["is_active"]),
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
                user_id=r["user_id"], username=r["username"], email=r["email"],
                role=UserRole(r["role"]), is_active=bool(r["is_active"]),
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
                user.email, user.role, user.is_active, user.must_change_password,
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
                deployment.deploy_id, deployment.request_id, deployment.git_sha,
                deployment.environment, deployment.status,
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
            deploy_id=row["deploy_id"], request_id=row["request_id"],
            git_sha=row["git_sha"], environment=row["environment"],
            status=row["status"], previous_deploy_id=row["previous_deploy_id"],
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
                notification.notification_id, notification.event_id,
                notification.severity, notification.title, notification.message,
                notification.request_id, notification.link_url,
                notification.user_id, notification.created_at.isoformat(),
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
                notification_id=r["notification_id"], event_id=r["event_id"],
                severity=r["severity"], title=r["title"], message=r["message"],
                request_id=r["request_id"], link_url=r["link_url"],
                user_id=r["user_id"],
                created_at=datetime.fromisoformat(r["created_at"]),
                read_at=(datetime.fromisoformat(r["read_at"]) if r["read_at"] else None),
                dismissed_at=(datetime.fromisoformat(r["dismissed_at"]) if r["dismissed_at"] else None),
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
                input_tokens, output_tokens, cost_usd, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                usage.usage_id, usage.request_id, usage.subtask_id,
                usage.agent_id, usage.model, usage.input_tokens,
                usage.output_tokens, usage.cost_usd, usage.recorded_at.isoformat(),
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
                usage_id=r["usage_id"], request_id=r["request_id"],
                subtask_id=r["subtask_id"], agent_id=r["agent_id"],
                model=r["model"], input_tokens=r["input_tokens"],
                output_tokens=r["output_tokens"], cost_usd=r["cost_usd"],
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
                metric.metric_id, metric.metric_name, metric.metric_value,
                json.dumps(metric.labels), metric.recorded_at.isoformat(),
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
                trace.trace_id, trace.request_id, trace.agent_id, trace.subtask_id,
                trace.llm_calls, trace.tool_calls, trace.input_tokens,
                trace.output_tokens, trace.status, trace.started_at.isoformat(),
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
                trace.llm_calls, trace.tool_calls, trace.input_tokens,
                trace.output_tokens, trace.status,
                trace.completed_at.isoformat() if trace.completed_at else None,
                trace.duration_ms, trace.error_message,
                trace.trace_id, trace.subtask_id,
            ),
        )
        await db.commit()
