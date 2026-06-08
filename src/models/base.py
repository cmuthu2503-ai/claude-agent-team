"""Core Pydantic models for the Agent Team system."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────


class RequestStatus(StrEnum):
    RECEIVED = "received"
    ANALYZING = "analyzing"
    DELEGATED = "delegated"
    IN_PROGRESS = "in_progress"
    AGGREGATING = "aggregating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"  # user killed the request before it finished


class SubtaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"  # parent request was cancelled while this was running


class ProposalStatus(StrEnum):
    """Approval-gate proposal lifecycle (HAI-21/22 / FR-030)."""

    PENDING = "pending"      # awaiting a human decision
    CONFIRMED = "confirmed"  # approved; about to execute
    REJECTED = "rejected"    # human said no — never runs
    EXPIRED = "expired"      # TTL elapsed before a decision — never runs
    EXECUTED = "executed"    # the underlying action ran successfully
    FAILED = "failed"        # confirmed but the action's handler failed


class TaskType(StrEnum):
    FEATURE = "feature_request"
    BUG = "bug_report"
    DOCS = "doc_request"
    DEMO = "demo_request"
    RESEARCH = "research_request"
    CONTENT = "content_request"


class TaskPriority(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class UserRole(StrEnum):
    VIEWER = "viewer"
    DEVELOPER = "developer"
    ADMIN = "admin"


class StoryStatus(StrEnum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    TESTING = "testing"
    DONE = "done"


class DeploymentStatus(StrEnum):
    DEPLOYING = "deploying"
    ACTIVE = "active"
    VERIFIED = "verified"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class NotificationSeverity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"
    LOW = "low"


# ── Core Models ──────────────────────────────────


class Request(BaseModel):
    """A user-submitted request (feature, bug, docs, demo)."""

    request_id: str
    description: str
    task_type: TaskType = TaskType.FEATURE
    priority: TaskPriority = TaskPriority.MEDIUM
    status: RequestStatus = RequestStatus.RECEIVED
    tags: list[str] = Field(default_factory=list)
    # KB-09 — Knowledge Bucket(s) this request's agents are grounded in.
    # Selected at submit (frozen mock Screen 04). The executor injects these
    # into the retrieval scope; agents retrieve ONLY within them. Empty =
    # platform knowledge only (whole kb_platform namespace).
    bucket_ids: list[str] = Field(default_factory=list)
    # Parent project. Defaults to the immutable Unassigned project at the
    # API layer when not supplied; see docs/prd-projects-feature.md.
    project_id: str | None = None
    # Project-driven Build (PDB-23): when this Request was created by the
    # dispatcher from a finalized project task, this back-links to that task.
    # NULL for one-off Submit Request flow.
    source_task_id: str | None = None
    created_by: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    estimated_cost_usd: float | None = None
    actual_cost_usd: float | None = None
    # Legacy DB column — kept for backward compatibility with rows from before the
    # Claude Platform on AWS migration. Always "claude_platform_aws" for new rows;
    # not exposed in the API or UI anymore.
    provider: str = "claude_platform_aws"
    # Artifacts produced by the workflow (set by publish/code_commit handlers)
    published_files: list[str] = Field(default_factory=list)  # repo-relative paths
    commit_sha: str | None = None  # short SHA of the publish commit
    commit_url: str | None = None  # GitHub commit URL
    # Code-commit failure detail. Set when the workflow died because CodeWriter
    # refused the agent's output (truncation, ruff, tsc, etc.). The UI uses
    # this to render the code_commit stage's failure reason instead of leaving
    # users guessing why the request stopped after testing succeeded.
    code_commit_error: str | None = None


class Subtask(BaseModel):
    """A subtask delegated to a specific agent."""

    subtask_id: str
    request_id: str
    agent_id: str
    status: SubtaskStatus = SubtaskStatus.PENDING
    input_artifacts: list[str] = Field(default_factory=list)
    output_artifacts: list[str] = Field(default_factory=list)
    output_text: str = ""  # Full text output from the agent
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None


class Artifact(BaseModel):
    """A file or output produced by an agent."""

    artifact_id: str
    subtask_id: str
    request_id: str
    name: str
    file_path: str
    format: str  # markdown | json | yaml | code | report
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SubtaskPlan(BaseModel):
    """A planned subtask within a delegation plan."""

    agent_id: str
    description: str
    depends_on: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)


class DelegationPlan(BaseModel):
    """Plan produced by Engineering Lead for decomposing a request."""

    request_id: str
    task_type: TaskType
    subtasks: list[SubtaskPlan] = Field(default_factory=list)


class Story(BaseModel):
    """A user story linked to a request."""

    story_id: str
    request_id: str
    title: str
    description: str = ""
    status: StoryStatus = StoryStatus.TODO
    priority: TaskPriority | None = None
    assigned_agent: str | None = None
    coverage_pct: float | None = None
    github_issue_number: int | None = None


class AcceptanceCriterion(BaseModel):
    """A single acceptance criterion for a user story (Given/When/Then)."""

    ac_id: str
    story_id: str
    criterion_text: str  # Full text of the criterion
    given_clause: str = ""
    when_clause: str = ""
    then_clause: str = ""
    is_met: bool = False


class TestCase(BaseModel):
    """A test case linked to a story."""

    test_id: str
    story_id: str
    name: str
    status: str = "pending"  # pending | running | pass | fail
    last_run_at: datetime | None = None


# ── Auth Models ──────────────────────────────────


class User(BaseModel):
    """A system user."""

    user_id: str
    username: str
    email: str
    role: UserRole = UserRole.DEVELOPER
    is_active: bool = True
    must_change_password: bool = False
    # KB-29 — knowledge-base curator capability. Orthogonal to ``role``: a list
    # of scopes this user may curate (approve/retire docs, promote memory→KB).
    # Values: "platform" (the kb_platform corpus), a project_id (that app), or
    # "*" (all). Empty = not a curator. Admins are implicit curators everywhere.
    kb_curator_scopes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_login_at: datetime | None = None


class ServiceToken(BaseModel):
    """A long-lived service identity (HAI-01 / FR-010) for headless principals
    such as the Hermes Agent integration.

    The raw token is shown exactly once at creation and never persisted — only
    its SHA-256 hash lives in the DB, so this model deliberately omits the hash.
    ``role`` reuses the viewer→developer→admin hierarchy; ``revoked_at`` being
    set means the token is rejected at authentication time.
    """

    token_id: str
    name: str
    role: UserRole = UserRole.VIEWER
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None


class Proposal(BaseModel):
    """An action awaiting human approval (HAI-21/22 / FR-030).

    The unit of the approval gate: a service principal (Hermes) can only mutate
    state by creating one of these, and it executes ONLY after a human confirms
    it (FR-038). ``payload`` carries the action handler's args; ``status`` walks
    the ProposalStatus lifecycle.
    """

    proposal_id: str
    action_type: str
    target_ref: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    status: ProposalStatus = ProposalStatus.PENDING
    proposed_by: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    decided_by: str | None = None
    decided_at: datetime | None = None
    executed_at: datetime | None = None
    ttl_seconds: int = 86400
    result_ref: str | None = None
    error: str | None = None
    idempotency_key: str | None = None
    # HAI-30 — one-time channel-approval token (hash only; raw is delivered to the
    # human via the proposal.created event, never the API response). Never exposed
    # by the route's _public serializer.
    approval_token_hash: str | None = None
    approval_token_used: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            ProposalStatus.REJECTED,
            ProposalStatus.EXPIRED,
            ProposalStatus.EXECUTED,
            ProposalStatus.FAILED,
        )


# ── Document Persistence ─────────────────────────


class Document(BaseModel):
    """A persisted document produced by an agent (PRD, stories, code, reviews, etc.)."""

    document_id: str
    request_id: str
    doc_type: str  # prd | user_stories | backend_code | frontend_code | code_review | test_report | deploy_report
    title: str
    content: str
    agent_id: str
    version: int = 1
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = None


# ── Projects ─────────────────────────────────────
# A project groups related requests so the platform has a sense of
# "what work stream is this part of." Every request belongs to exactly
# one project; the seeded immutable "proj-unassigned" project catches
# legacy/orphaned rows. See docs/prd-projects-feature.md §5.3 for
# field-by-field rationale.


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class DefaultTeam(StrEnum):
    ENGINEERING = "engineering"
    RESEARCH = "research"
    CONTENT = "content"


# Closed-set palette + icon names enforced at validation time (PRJ-009, PRJ-010).
PROJECT_COLOR_PALETTE: tuple[str, ...] = (
    "#00f0ff",  # cyan (default)
    "#ff2a6d",  # pink
    "#39ff14",  # matrix green
    "#f9f871",  # yellow
    "#ff8c00",  # orange
    "#b026ff",  # purple
    "#0070f3",  # blue
    "#8080a0",  # gray
)
PROJECT_ICON_SET: tuple[str, ...] = (
    "folder",
    "rocket",
    "layers",
    "code",
    "flask-conical",
    "palette",
    "bug",
    "book-open",
)
UNASSIGNED_PROJECT_ID = "proj-unassigned"


# Per-project deployable kinds. The string values match the
# subdirectory names under ``config/project-templates/`` so the
# scaffolder can look up the template by kind directly.
class ProjectKind(StrEnum):
    WEB_APP = "web-app"          # FastAPI backend + Vite/React frontend (2 ports)
    API_SERVICE = "api-service"  # FastAPI only (1 port)
    FRONTEND_APP = "frontend-app"  # Vite/React only (1 port)


# Per-project deployment lifecycle. Stored on the projects row;
# transitions driven by the Deploy/Stop endpoints (which flip to a
# pending_* state) and the host-side supervisor (which picks pending
# rows up, runs the `docker compose` commands, and flips to the
# terminal state).
#
# State machine:
#   stopped → pending_deploy → deploying → running | failed
#   running → pending_stop → stopped | failed
#
# Why pending_* exists: the backend container can't run `docker compose`
# directly (Docker-in-Docker path-resolution issue, same as the platform
# supervisor). Backend marks the intent; host-side supervisor executes
# it. UI polls the row to surface live status.
class DeployStatus(StrEnum):
    STOPPED = "stopped"                # initial state; no containers running
    PENDING_DEPLOY = "pending_deploy"  # user clicked Deploy; supervisor will pick up
    DEPLOYING = "deploying"            # supervisor running `docker compose up -d --build`
    RUNNING = "running"                # healthcheck passed; launch URL is live
    PENDING_STOP = "pending_stop"      # user clicked Stop; supervisor will pick up
    STOPPING = "stopping"              # supervisor running `docker compose down`
    FAILED = "failed"                  # last operation errored; see `deploy_error`


# Base ports for per-project allocation. Sequential assignment from
# these. Chosen to avoid collision with platform dev (8000/3000),
# staging (8010/3010), prod (8020/3020), demo (8030/3030).
PROJECT_BACKEND_PORT_BASE = 8100
PROJECT_FRONTEND_PORT_BASE = 3100


class Project(BaseModel):
    """A parent container for requests. PRD: docs/prd-projects-feature.md."""

    project_id: str
    name: str
    description: str = ""
    status: ProjectStatus = ProjectStatus.ACTIVE
    # Identity / visual
    color: str = "#00f0ff"
    icon: str = "folder"
    tags: list[str] = Field(default_factory=list)
    # Ownership / context
    lead_user_id: str | None = None
    repo_url: str = ""
    default_team: DefaultTeam | None = None
    target_date: datetime | None = None
    template_id: str | None = None
    # Per-project app scaffold + deploy (added in the per-project
    # working-tree feature). Existing rows from before this feature
    # default to ``kind = web-app`` and ``deploy_status = stopped`` —
    # they won't have a scaffold on disk, so the Deploy endpoint
    # rejects them with a clear error and offers a retro-scaffold
    # button (not in v1).
    kind: ProjectKind = ProjectKind.WEB_APP
    deploy_backend_port: int | None = None  # allocated at create time
    deploy_frontend_port: int | None = None
    deploy_status: DeployStatus = DeployStatus.STOPPED
    deploy_url: str = ""  # e.g. http://localhost:3100 when running
    deploy_last_started_at: datetime | None = None
    deploy_error: str | None = None  # last deploy attempt's error, if any
    # AI Deploy Judge baseline + per-project preferences. last_deploy_commit_sha
    # is the commit the running container was built from — drift is measured
    # by comparing it to the project's latest code_commit (the supervisor
    # advances it on successful deploys). deploy_judge_preferences is free-text
    # the user can edit to teach the judge ("treat any change in src/state/ as
    # rebuild-backend", "skip is fine for docs/", etc.) — fed into the
    # judge prompt as extra context. Empty by default.
    last_deploy_commit_sha: str | None = None
    deploy_judge_preferences: str = ""
    # AI Deploy Judge — chosen action that the supervisor will execute
    # on the next pending_deploy pickup. NULL means "fall back to the
    # legacy rebuild-all behaviour" (manual Deploy button clicks that
    # never went through the judge).
    deploy_pending_action: str | None = None
    # BPD-25 — auto-dispatch on `request.deployed`. When True, the
    # post-deploy event handler (BPD-24) recomputes the dispatchable
    # set (BPD-205) and fires every newly-unblocked task automatically.
    # Default False per BPD §2.3 — cost-surprise prevention; opt-in via
    # the project's Deployment panel.
    auto_dispatch_on_deploy: bool = False
    # Audit
    created_by: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = None


# ── Project-driven Build (PDB-02) ──────────────────
# Versioned per-project artifacts (brief, PRD) authored by the user with
# agent assistance. Tasks list lives in its own structured table (Phase B).
# PRD: docs/prd-project-driven-build.md.


class ArtifactKind(StrEnum):
    BRIEF = "brief"
    PRD = "prd"
    # Generated after the PRD is finalized. Enterprise-grade REST API
    # specification (OpenAPI 3.1 + narrative). Same versioning /
    # draft-finalized lifecycle as PRD.
    API_SPEC = "api_spec"


class ArtifactStatus(StrEnum):
    DRAFT = "draft"
    FINALIZED = "finalized"
    ARCHIVED = "archived"


class ProjectArtifact(BaseModel):
    artifact_id: str
    project_id: str
    kind: ArtifactKind
    version: int
    status: ArtifactStatus = ArtifactStatus.DRAFT
    content: str = ""
    created_by: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = None  # PDB-43: last content mutation
    finalized_at: datetime | None = None
    finalized_by: str | None = None
    # Review-driven regeneration. NULL for the first-draft of a kind
    # (v0.1). Populated when this version was produced by the agent
    # using the user's review comments against the prior version.
    review_input: str | None = None


# ── Project-driven Build: task list (PDB-14) ─────────
# Structured rows (not a markdown blob) so the build dispatcher can read
# task_type / priority / estimated_agent directly when creating Requests.


class TaskStatus(StrEnum):
    BACKLOG = "backlog"
    DISPATCHED = "dispatched"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    TESTING = "testing"
    DEPLOYED = "deployed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProjectTask(BaseModel):
    task_id: str
    project_id: str
    list_version: int
    list_status: ArtifactStatus = ArtifactStatus.DRAFT  # reuses draft/finalized/archived
    ordinal: int
    title: str
    description: str = ""
    task_type: str = "feature_request"
    priority: str = "medium"
    estimated_agent: str | None = None
    task_status: TaskStatus = TaskStatus.BACKLOG
    request_id: str | None = None
    amended: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = None
    # Review-driven regeneration. NULL for the first-draft of a list
    # version. Populated (same value on every row of that list_version)
    # when the list was produced by the agent using the user's review
    # comments against the prior task list.
    review_input: str | None = None

    # ── BPD (Build Plan Decomposition, §6.8 / BPD-003) fields ──
    # New in v3.13. Legacy tasks (those generated before BPD shipped)
    # have feature_id=None, depends_on=[], etc. — they render under a
    # synthetic "Legacy" epic in the UI and dispatch unchanged.
    feature_id: str | None = None              # FK features.feature_id when emitted under the new hierarchy
    depends_on: list[str] = Field(default_factory=list)  # task_ids that must be `deployed` before dispatch
    primary_file: str | None = None             # the ONE file this task owns (atomic-task contract)
    expected_loc: int | None = None             # rough size hint, typical 50-300
    acceptance_test: str | None = None          # one-sentence "done when X" criterion


# ── Build Plan Decomposition models (BPD §6.8) ────────────────────
# Three-level hierarchy on top of project_tasks. Epic groups features
# semantically; Feature groups atomic tasks. Each level has its own
# acceptance criterion and supports the same draft/finalize/archive
# lifecycle as project_artifacts and project_tasks.


class Epic(BaseModel):
    """Top-level grouping in the Build Plan Decomposition hierarchy.

    One epic = one user-facing capability area (e.g. "Authentication",
    "Dashboard"). The agent's Pass-1 generator emits 5-12 epics per
    project from the finalized PRD."""

    epic_id: str                                 # "E-<8hex>"
    project_id: str
    list_version: int                            # monotonic per project; new version on each Pass-1 regen
    list_status: ArtifactStatus = ArtifactStatus.DRAFT
    ordinal: int                                 # display order within list_version
    title: str
    description: str = ""
    acceptance_criteria: str = ""                # one-sentence "epic done when X" criterion
    review_input: str | None = None              # comments that drove this regeneration (same value on every row of a list_version)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = None


class Feature(BaseModel):
    """Mid-level grouping in the Build Plan Decomposition hierarchy.

    One feature = one deliverable capability within an epic (e.g.
    "Login flow", "Password reset"). The agent's Pass-2 generator
    emits 1-8 features per epic. Atomic project_tasks hang underneath."""

    feature_id: str                              # "F-<8hex>"
    epic_id: str
    project_id: str
    list_version: int
    list_status: ArtifactStatus = ArtifactStatus.DRAFT
    ordinal: int
    title: str
    description: str = ""
    acceptance_criteria: str = ""
    depends_on: list[str] = Field(default_factory=list)  # feature_ids in same project (rare; mostly task-level)
    review_input: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = None


# ── AI Deploy Judge (per-project) ────────────────
# One row per judge decision or user override. The latest non-applied
# row for a project is the "current recommendation" the UI shows.
# See docs/agent-pitfalls.md and CLAUDE.md "Working Standards" — the
# previous architecture had per-task DevOps polling that wasted 10 min
# per task; this replaces that loop with a single AI judge call per
# coalesced change set + a smart-route to the cheapest docker action.


class DeployAction(StrEnum):
    """The six actions the judge can recommend.

    Mapping to docker:
      skip               -> nothing; just advance last_deploy_commit_sha
      restart-backend    -> `docker compose restart backend`
      restart-frontend   -> `docker compose restart frontend`
      rebuild-backend    -> `docker compose up -d --build backend`
      rebuild-frontend   -> `docker compose up -d --build frontend`
      rebuild-all        -> `docker compose up -d --build` (the existing path)
      hold               -> nothing; row stays pending for manual override
    """

    SKIP = "skip"
    RESTART_BACKEND = "restart-backend"
    RESTART_FRONTEND = "restart-frontend"
    REBUILD_BACKEND = "rebuild-backend"
    REBUILD_FRONTEND = "rebuild-frontend"
    REBUILD_ALL = "rebuild-all"
    HOLD = "hold"


class DeployRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DeployDecisionStatus(StrEnum):
    """Lifecycle of a single judge recommendation:

      pending    — judge proposed it; user hasn't acted yet
      applied    — Apply (or auto-apply for low-risk) was clicked / fired
      overridden — user clicked a different action than recommended
      superseded — a newer commit invalidated this decision before action
    """

    PENDING = "pending"
    APPLIED = "applied"
    OVERRIDDEN = "overridden"
    SUPERSEDED = "superseded"


class DeployDecision(BaseModel):
    decision_id: str
    project_id: str
    # Snapshot of the drift the judge looked at — list of
    # {commit_sha, files, added, removed}. Persisted as JSON in the
    # column; surfaced to the UI verbatim so the panel can render
    # "X commits since last deploy" without a fresh query.
    drift_summary: list[dict[str, Any]] = Field(default_factory=list)
    from_commit_sha: str | None = None
    to_commit_sha: str | None = None
    action: DeployAction
    risk: DeployRiskLevel
    confidence: DeployRiskLevel  # reusing low/medium/high
    reasoning: str = ""
    # 1 if judge LLM actually returned; 0 if we fell back to a safe
    # default (no creds, API error, malformed JSON, etc.)
    from_llm: bool = True
    status: DeployDecisionStatus = DeployDecisionStatus.PENDING
    # If overridden, the action the user actually chose. None otherwise.
    overridden_action: DeployAction | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    applied_at: datetime | None = None


# ── Project-driven Build: chat with project_orchestrator (PDB-33) ────
# One row per conversation turn. `tool_calls` is a JSON array of
# {tool, input, result_summary} describing what the agent did during
# that assistant turn — rendered inline as chips in the UI.


class BuildMessage(BaseModel):
    message_id: str
    project_id: str
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str | None = None


# ── Deployment State Machine ─────────────────────


class DeploymentStep(StrEnum):
    CODE_COMMITTED = "code_committed"
    JUDGING = "judging"        # supervisor is asking the deployment judge for a strategy
    BUILDING = "building"
    SYNCING = "syncing"        # supervisor is `git fetch + checkout`-ing files from origin/main
    STAGING_DEPLOYING = "staging_deploying"
    STAGING_HEALTHY = "staging_healthy"
    PROD_DEPLOYING = "prod_deploying"
    PROD_HEALTHY = "prod_healthy"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    ON_HOLD = "on_hold"        # judge picked "hold" — paused for manual unblock


class DeploymentState(BaseModel):
    """Tracks every step of an autonomous deployment — resumable across restarts."""

    deployment_id: str
    request_id: str
    commit_sha: str = ""
    current_step: str = DeploymentStep.CODE_COMMITTED
    step_history: list[dict] = Field(default_factory=list)  # [{step, status, timestamp, detail}]
    files_committed: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    rollback_sha: str = ""  # previous commit SHA for rollback
    # Judgment from the deployment-judge LLM (set by the supervisor BEFORE
    # any docker work). Empty until the judge runs.
    strategy: str = ""           # skip | deploy_staging_only | deploy_full | hold
    strategy_reasoning: str = ""  # plain-language explanation
    risk: str = ""               # low | medium | high


# ── Deploy Health Probes (AET-23 — Phase AE-1 ops_heal_agent) ────────────


class DeployHealthProbe(BaseModel):
    """One health snapshot for a single deploy/environment, written
    every 60s by the supervisor's background probe loop (AET-24).

    APPEND-ONLY time-series — never mutated after insert. The
    ops_heal_agent reads recent rows through slo_check (AET-25) and
    anomaly_detect (AET-26) to decide whether to fire auto_rollback
    (AET-27).

    The denormalised top-level columns are the ones rolling-window
    queries scan on every probe cycle (response_time_ms percentiles,
    error_rate, restart spikes). Anything richer goes into
    raw_metrics_json so we don't have to migrate when adding new
    SLOs — schema-on-read.
    """

    probe_id: str
    deploy_id: str
    env: str  # development | staging | production | demo
    recorded_at: datetime = Field(default_factory=datetime.utcnow)
    response_time_ms: int | None = None
    error_rate_5m: float | None = None  # fraction (0.0-1.0), NOT percent
    http_status: int | None = None
    restart_count: int = 0
    raw_metrics: dict[str, Any] = Field(default_factory=dict)


class RollbackRequest(BaseModel):
    """AET-29 — ops_heal_agent → supervisor hand-off for a queued
    rollback. Inserted by ``auto_rollback`` tool inside the backend
    container; consumed by the supervisor host process which performs
    the actual git revert + redeploy.

    Status lifecycle::

        pending     → queued by the agent, supervisor hasn't started yet
        in_flight   → supervisor picked it up; running git revert
        completed   → supervisor revert succeeded
        failed      → revert errored (see error_message)
        rejected    → supervisor declined (env unknown, etc.)

    Idempotency: ``auto_rollback`` MUST NOT insert a second pending /
    in_flight row for the same env. Once a row reaches a terminal
    state, a fresh request is allowed.
    """

    request_id: str
    deploy_id: str
    env: str
    status: str = "pending"  # see lifecycle above
    reason: str = ""
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    rollback_sha: str = ""
    error_message: str | None = None
    requested_by: str = "ops_heal_agent"


# ── Deployment Models ────────────────────────────


class Deployment(BaseModel):
    """A deployment record."""

    deploy_id: str
    request_id: str
    git_sha: str
    environment: str  # staging | production | demo
    status: DeploymentStatus = DeploymentStatus.DEPLOYING
    previous_deploy_id: str | None = None
    deployed_at: datetime | None = None
    verified_at: datetime | None = None
    rolled_back_at: datetime | None = None


# ── Notification Models ──────────────────────────


class Notification(BaseModel):
    """An in-app notification."""

    notification_id: str
    event_id: str
    severity: NotificationSeverity
    title: str
    message: str
    request_id: str | None = None
    link_url: str | None = None
    user_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    read_at: datetime | None = None
    dismissed_at: datetime | None = None


# ── Cost & Observability Models ──────────────────


class TokenUsage(BaseModel):
    """Token usage record for a single LLM call.

    Most rows attach to a Request via `request_id`. Project-driven Build
    (PDB-08) introduces single-agent calls that produce per-project
    artifacts (brief/PRD/tasks) without creating a Request — those rows
    leave `request_id` empty and set `project_artifact_id` instead. The
    cost dashboard UNIONs across both keys when scoping by project.
    """

    usage_id: str
    request_id: str = ""
    subtask_id: str = ""
    agent_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    recorded_at: datetime = Field(default_factory=datetime.utcnow)
    project_artifact_id: str | None = None
    # Direct project linkage. For workflow-runner calls (which have a
    # request_id) it's also derivable via request → project, but storing
    # it here lets the cost dashboard filter by project in O(1) without
    # the OR-of-subqueries shape. Populated by the orchestrator's token
    # tracker and by single_agent_call (BPD generators pass their
    # project_id directly). NULL only for pre-migration rows or for
    # platform-level (no-project) calls.
    project_id: str | None = None


class AgentTrace(BaseModel):
    """Execution trace for a single agent run."""

    trace_id: str
    request_id: str
    agent_id: str
    subtask_id: str
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    status: str = "running"
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    duration_ms: int | None = None
    error_message: str | None = None


class Metric(BaseModel):
    """A single metric data point."""

    metric_id: str
    metric_name: str
    metric_value: float
    labels: dict[str, str] = Field(default_factory=dict)
    recorded_at: datetime = Field(default_factory=datetime.utcnow)


# ── Prompt Studio Models ─────────────────────────


class PromptSession(BaseModel):
    """A single Prompt Studio session — structured inputs + metadata."""

    session_id: str
    user_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # Structured input fields
    use_case: str  # required
    target_audience: str = ""
    desired_output: str = ""
    tone: str = ""
    constraints: str = ""
    # Advanced options as a flexible dict (target_model, output_format, few_shot, cot, length, category)
    options: dict[str, Any] = Field(default_factory=dict)
    # Legacy DB column — provider is no longer selectable. Always "claude_platform_aws".
    provider: str = "claude_platform_aws"
    # Starting template id (if any)
    template_id: str | None = None
    # Which variant the user selected (drives refinement context)
    selected_variant_id: str | None = None


class PromptVariant(BaseModel):
    """A single generated prompt variant within a session."""

    variant_id: str
    session_id: str
    iteration: int = 0  # 0 = initial generation, 1+ = refinements
    variant_index: int = 1  # 1, 2, 3 within an iteration
    approach: str = ""  # e.g. "Structured XML", "Conversational Markdown"
    prompt_text: str
    techniques: list[str] = Field(default_factory=list)
    feedback_applied: str = ""  # Only for iterations > 0
    generated_at: datetime = Field(default_factory=datetime.utcnow)
