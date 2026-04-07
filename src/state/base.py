"""Abstract StateStore interface — swappable backend (SQLite, Redis, etc.)."""

from abc import ABC, abstractmethod

from src.models.base import (
    AcceptanceCriterion,
    AgentTrace,
    Artifact,
    Deployment,
    Document,
    Metric,
    Notification,
    PromptSession,
    PromptVariant,
    Request,
    Story,
    Subtask,
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

    # ── Subtasks ─────────────────────────────────

    @abstractmethod
    async def create_subtask(self, subtask: Subtask) -> str: ...

    @abstractmethod
    async def get_subtask(self, subtask_id: str) -> Subtask | None: ...

    @abstractmethod
    async def update_subtask(self, subtask: Subtask) -> None: ...

    @abstractmethod
    async def get_subtasks_for_request(self, request_id: str) -> list[Subtask]: ...

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
