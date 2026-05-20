"""Abstract StateStore interface — swappable backend (SQLite, Redis, etc.)."""

from abc import ABC, abstractmethod
from datetime import datetime

from src.models.base import (
    AcceptanceCriterion,
    AgentTrace,
    Artifact,
    ArtifactKind,
    ArtifactStatus,
    BuildMessage,
    Deployment,
    Document,
    Metric,
    Notification,
    Project,
    ProjectArtifact,
    ProjectTask,
    PromptSession,
    PromptVariant,
    Request,
    Story,
    Subtask,
    TaskStatus,
    TestCase,
    TokenUsage,
    User,
)


class StateStore(ABC):
    """Abstract interface for all state persistence operations."""

    # ── Requests ─────────────────────────────────

    @abstractmethod
    async def create_request(self, request: Request) -> str: ...

    @abstractmethod
    async def get_request(self, request_id: str) -> Request | None: ...

    @abstractmethod
    async def update_request(self, request: Request) -> None: ...

    @abstractmethod
    async def list_requests(
        self, status: str | None = None, limit: int = 20, offset: int = 0
    ) -> list[Request]: ...

    @abstractmethod
    async def delete_request(self, request_id: str) -> None:
        """Hard-delete a request and all rows that reference it (cascade).

        Implementations must remove: subtasks, artifacts, stories (+ their
        acceptance_criteria and test_cases), deployments, deployment_states,
        token_usage, agent_traces, documents, notifications, and finally the
        request row itself. Use a single transaction.
        """
        ...

    # ── Subtasks ─────────────────────────────────

    @abstractmethod
    async def create_subtask(self, subtask: Subtask) -> str: ...

    @abstractmethod
    async def get_subtask(self, subtask_id: str) -> Subtask | None: ...

    @abstractmethod
    async def update_subtask(self, subtask: Subtask) -> None: ...

    @abstractmethod
    async def get_subtasks_for_request(self, request_id: str) -> list[Subtask]: ...

    @abstractmethod
    async def get_active_subtasks(self) -> list[Subtask]:
        """Return all subtasks currently in IN_PROGRESS, newest started first."""
        ...

    # ── Artifacts ────────────────────────────────

    @abstractmethod
    async def save_artifact(self, artifact: Artifact) -> str: ...

    @abstractmethod
    async def get_artifacts_for_subtask(self, subtask_id: str) -> list[Artifact]: ...

    # ── Stories ──────────────────────────────────

    @abstractmethod
    async def create_story(self, story: Story) -> str: ...

    @abstractmethod
    async def get_stories_for_request(self, request_id: str) -> list[Story]: ...

    @abstractmethod
    async def update_story(self, story: Story) -> None: ...

    # ── Acceptance Criteria ───────────────────────

    @abstractmethod
    async def create_acceptance_criterion(self, ac: AcceptanceCriterion) -> str: ...

    @abstractmethod
    async def get_acceptance_criteria_for_story(self, story_id: str) -> list[AcceptanceCriterion]: ...

    @abstractmethod
    async def update_acceptance_criterion(self, ac: AcceptanceCriterion) -> None: ...

    # ── Test Cases ────────────────────────────────

    @abstractmethod
    async def create_test_case(self, tc: TestCase) -> str: ...

    @abstractmethod
    async def get_test_cases_for_story(self, story_id: str) -> list[TestCase]: ...

    @abstractmethod
    async def update_test_case(self, tc: TestCase) -> None: ...

    # ── Prompt Studio ─────────────────────────────

    @abstractmethod
    async def create_prompt_session(self, session: PromptSession) -> str: ...

    @abstractmethod
    async def get_prompt_session(self, session_id: str) -> PromptSession | None: ...

    @abstractmethod
    async def list_prompt_sessions_for_user(
        self, user_id: str, limit: int = 20, offset: int = 0
    ) -> list[PromptSession]: ...

    @abstractmethod
    async def update_prompt_session_selection(
        self, session_id: str, selected_variant_id: str
    ) -> None: ...

    @abstractmethod
    async def create_prompt_variant(self, variant: PromptVariant) -> str: ...

    @abstractmethod
    async def get_prompt_variants_for_session(
        self, session_id: str
    ) -> list[PromptVariant]: ...

    # ── Users ────────────────────────────────────

    @abstractmethod
    async def create_user(self, user: User, password_hash: str) -> str: ...

    @abstractmethod
    async def get_user_by_username(self, username: str) -> tuple[User, str] | None: ...

    @abstractmethod
    async def get_user(self, user_id: str) -> User | None: ...

    @abstractmethod
    async def list_users(self) -> list[User]: ...

    @abstractmethod
    async def update_user(self, user: User) -> None: ...

    @abstractmethod
    async def update_password(self, user_id: str, password_hash: str) -> None: ...

    # ── Deployments ──────────────────────────────

    @abstractmethod
    async def create_deployment(self, deployment: Deployment) -> str: ...

    @abstractmethod
    async def get_deployment(self, deploy_id: str) -> Deployment | None: ...

    @abstractmethod
    async def update_deployment(self, deployment: Deployment) -> None: ...

    @abstractmethod
    async def list_deployments(
        self, environment: str | None = None, limit: int = 20
    ) -> list[Deployment]: ...

    # ── Notifications ────────────────────────────

    @abstractmethod
    async def create_notification(self, notification: Notification) -> str: ...

    @abstractmethod
    async def get_notifications(
        self, user_id: str | None = None, unread_only: bool = False, limit: int = 50
    ) -> list[Notification]: ...

    @abstractmethod
    async def mark_notification_read(self, notification_id: str) -> None: ...

    @abstractmethod
    async def mark_all_notifications_read(self, user_id: str) -> None: ...

    # ── Documents ─────────────────────────────────

    @abstractmethod
    async def save_document(self, doc: Document) -> str: ...

    @abstractmethod
    async def get_document(self, document_id: str) -> Document | None: ...

    @abstractmethod
    async def get_documents_for_request(self, request_id: str) -> list[Document]: ...

    @abstractmethod
    async def search_documents(
        self, query: str, doc_type: str | None = None, limit: int = 10
    ) -> list[Document]: ...

    @abstractmethod
    async def update_document(self, doc: Document) -> None: ...

    # ── Projects ─────────────────────────────────

    @abstractmethod
    async def create_project(self, project: Project) -> str: ...

    @abstractmethod
    async def get_project(self, project_id: str) -> Project | None: ...

    @abstractmethod
    async def list_projects(self, include_archived: bool = False) -> list[Project]: ...

    @abstractmethod
    async def update_project(self, project: Project) -> None: ...

    @abstractmethod
    async def delete_project(self, project_id: str) -> None:
        """Hard-delete. Caller is responsible for ensuring no requests reference
        this project (PRJ-006). Backend route returns 409 otherwise."""
        ...

    @abstractmethod
    async def find_project_by_name(self, name: str, active_only: bool = True) -> Project | None:
        """Case-insensitive lookup used for uniqueness enforcement (PRJ-007)."""
        ...

    @abstractmethod
    async def get_requests_for_project(self, project_id: str) -> list[Request]: ...

    @abstractmethod
    async def count_requests_for_project(self, project_id: str) -> dict[str, int]:
        """Returns {'total': N, 'active': N, 'completed': N, 'failed': N} for
        the project detail page stat cards."""
        ...

    @abstractmethod
    async def allocate_project_ports(self) -> tuple[int, int]:
        """Pick the next available (backend, frontend) port pair for a
        new project. Sequential MAX+1 allocation starting at
        ``PROJECT_BACKEND_PORT_BASE`` / ``PROJECT_FRONTEND_PORT_BASE``.
        Caller is responsible for INSERTing the new project row with
        these values; unique indexes catch any race condition."""
        ...

    @abstractmethod
    async def update_project_deploy(
        self,
        project_id: str,
        *,
        deploy_status: str | None = None,
        deploy_url: str | None = None,
        deploy_last_started_at: datetime | None = None,
        deploy_error: str | None = None,
    ) -> None:
        """Targeted update for the deploy-lifecycle fields only.
        Used by the Deploy / Stop endpoints; pass ``None`` to leave a
        field unchanged."""
        ...

    # ── Project Artifacts (PDB-03) ───────────────
    # Versioned brief + PRD per project. Tasks live in their own structured
    # table (Phase B). At most one row per (project_id, kind) has
    # status='finalized' at any time; enforced by the application during
    # finalize_artifact() (transaction: flip current finalized → archived,
    # then set new row to finalized).

    @abstractmethod
    async def create_artifact(self, artifact: ProjectArtifact) -> str: ...

    @abstractmethod
    async def get_artifact(
        self,
        project_id: str,
        kind: ArtifactKind,
        version: int | None = None,
    ) -> ProjectArtifact | None:
        """When version is None, returns the row with the highest version
        for this (project_id, kind). Used by routes to load "the current"
        artifact regardless of draft / finalized state."""
        ...

    @abstractmethod
    async def list_artifacts(
        self, project_id: str, kind: ArtifactKind
    ) -> list[ProjectArtifact]:
        """Full version history for one (project_id, kind), newest first.
        Mainly for an audit-log view we haven't built yet."""
        ...

    @abstractmethod
    async def update_artifact_content(self, artifact_id: str, content: str) -> None:
        """Save-draft path. Only mutates content; does NOT touch status or
        version. Caller must ensure the artifact is still in 'draft' status
        (we won't enforce here so finalize-then-amend flows stay possible)."""
        ...

    @abstractmethod
    async def finalize_artifact(
        self, artifact_id: str, finalized_by: str | None = None
    ) -> ProjectArtifact:
        """Transaction: archive any other finalized row for the same
        (project_id, kind), then mark THIS row finalized with timestamps.
        Returns the updated artifact."""
        ...

    @abstractmethod
    async def delete_artifacts(
        self, project_id: str, kind: ArtifactKind
    ) -> int:
        """Hard-delete every row for a (project_id, kind) — drafts,
        finalized, and archived alike. Returns the number of rows
        removed. Used by the "Delete PRD" action; downstream tasks
        keep their rows but lose the parent reference (no FK, so this
        is allowed)."""
        ...

    # ── Project Tasks (PDB-15) ───────────────────
    # Structured rows for a project's task list. A project has at most one
    # `list_status='finalized'` version at a time; older versions flip to
    # 'archived' when a new list is finalized. The dispatcher in Phase C
    # reads task_type / priority / estimated_agent to create Requests.

    @abstractmethod
    async def create_task(self, task: ProjectTask) -> str: ...

    @abstractmethod
    async def list_tasks_for_project(
        self,
        project_id: str,
        list_status: ArtifactStatus | None = None,
        list_version: int | None = None,
    ) -> list[ProjectTask]:
        """When list_status is None, returns tasks from the latest version
        (regardless of draft/finalized). When list_status is supplied,
        filters to rows with that list_status. When list_version is
        supplied, only that version is returned."""
        ...

    @abstractmethod
    async def get_task(self, task_id: str) -> ProjectTask | None: ...

    @abstractmethod
    async def update_task(self, task_id: str, fields: dict) -> ProjectTask:
        """Partial update — only mutates keys present in `fields`. Returns
        the updated row. Stamps `updated_at`."""
        ...

    @abstractmethod
    async def set_task_status(
        self,
        task_id: str,
        task_status: TaskStatus,
        request_id: str | None = None,
    ) -> None:
        """Used by the Phase C dispatcher and by the request.status_changed
        handler. Set request_id only when transitioning to 'dispatched';
        on subsequent transitions pass None to leave the existing value
        alone."""
        ...

    @abstractmethod
    async def finalize_task_list(
        self, project_id: str, list_version: int
    ) -> None:
        """Atomic flip: archive any other finalized list_version for this
        project, then mark every row of `list_version` as finalized."""
        ...

    @abstractmethod
    async def archive_task_list(
        self, project_id: str, list_version: int
    ) -> None:
        """Flip every row of `list_version` to list_status='archived'.
        Used when the user wants to regenerate after a finalize: archive
        the current finalized version first, then a new generation can
        proceed."""
        ...

    @abstractmethod
    async def delete_task_list_draft(
        self, project_id: str, list_version: int
    ) -> None:
        """Hard-delete every row of a draft list_version. Used when the
        user regenerates a draft — the old draft is discarded entirely
        (versions are only kept after they've been finalized at least once)."""
        ...

    @abstractmethod
    async def delete_task(self, task_id: str) -> None:
        """Hard-delete a single task row. The route layer is responsible
        for guarding the task_status precondition (only safe to delete
        when not actively dispatched / in-flight). If the task has a
        linked Request, the request's `source_task_id` is set to NULL
        rather than deleted — the Request stays valid, it just loses its
        back-link to the (now gone) project task."""
        ...

    # ── Build Session Messages (PDB-33) ──────────
    # Chat history between the user and the project_orchestrator agent.
    # Each row is one conversation turn; tool_calls captures structured
    # summaries of what the agent did during an assistant turn.

    @abstractmethod
    async def create_message(self, message: BuildMessage) -> str: ...

    @abstractmethod
    async def list_messages_for_project(
        self,
        project_id: str,
        limit: int = 200,
        before: str | None = None,
    ) -> list[BuildMessage]:
        """Returns the most-recent `limit` messages in chronological order
        (oldest first, so the UI can append directly). When `before` is a
        message_id, returns messages strictly older than that one — used
        for pagination by infinite scroll."""
        ...

    # ── Token Usage ──────────────────────────────

    @abstractmethod
    async def record_token_usage(self, usage: TokenUsage) -> None: ...

    @abstractmethod
    async def get_token_usage_for_request(self, request_id: str) -> list[TokenUsage]: ...

    @abstractmethod
    async def get_daily_cost(self) -> float: ...

    @abstractmethod
    async def get_monthly_cost(self) -> float: ...

    # ── Metrics & Traces ─────────────────────────

    @abstractmethod
    async def record_metric(self, metric: Metric) -> None: ...

    @abstractmethod
    async def record_agent_trace(self, trace: AgentTrace) -> None: ...

    @abstractmethod
    async def update_agent_trace(self, trace: AgentTrace) -> None: ...

    # ── Lifecycle ────────────────────────────────

    @abstractmethod
    async def initialize(self) -> None:
        """Create tables and initialize the store."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close connections and clean up."""
        ...
