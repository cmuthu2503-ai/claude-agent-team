"""Core Pydantic models for the Agent Team system."""

from datetime import datetime
from enum import StrEnum
from typing import Any

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


class SubtaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


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
    created_by: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    estimated_cost_usd: float | None = None
    actual_cost_usd: float | None = None
    provider: str = "anthropic"  # anthropic | bedrock — LLM provider for this request
    # Artifacts produced by the workflow (set by publish/code_commit handlers)
    published_files: list[str] = Field(default_factory=list)  # repo-relative paths
    commit_sha: str | None = None  # short SHA of the publish commit
    commit_url: str | None = None  # GitHub commit URL


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
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_login_at: datetime | None = None


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


# ── Deployment State Machine ─────────────────────


class DeploymentStep(StrEnum):
    CODE_COMMITTED = "code_committed"
    BUILDING = "building"
    STAGING_DEPLOYING = "staging_deploying"
    STAGING_HEALTHY = "staging_healthy"
    PROD_DEPLOYING = "prod_deploying"
    PROD_HEALTHY = "prod_healthy"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"


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
    """Token usage record for a single LLM call."""

    usage_id: str
    request_id: str
    subtask_id: str
    agent_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    recorded_at: datetime = Field(default_factory=datetime.utcnow)


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
    # LLM provider used
    provider: str = "anthropic"  # anthropic | bedrock
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
