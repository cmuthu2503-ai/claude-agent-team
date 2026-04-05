"""Notification catalog — defines all notification events with severity and templates."""

from dataclasses import dataclass


@dataclass
class NotificationEvent:
    event_id: str
    name: str
    severity: str  # critical | warning | info | low
    title_template: str
    message_template: str
    show_toast: bool = False


CATALOG: dict[str, NotificationEvent] = {
    # Request lifecycle
    "N-001": NotificationEvent("N-001", "request_received", "info", "Request received", "Request {request_id} has been submitted."),
    "N-002": NotificationEvent("N-002", "request_analyzing", "info", "Analyzing request", "Engineering Lead is analyzing {request_id}."),
    "N-003": NotificationEvent("N-003", "request_completed", "info", "Request completed", "Request {request_id} completed successfully."),
    "N-004": NotificationEvent("N-004", "request_failed", "critical", "Request failed", "Request {request_id} failed: {error}", show_toast=True),
    # Agent lifecycle
    "N-005": NotificationEvent("N-005", "agent_started", "low", "Agent started", "{agent_id} started working on {request_id}."),
    "N-006": NotificationEvent("N-006", "agent_completed", "low", "Agent completed", "{agent_id} completed task for {request_id}."),
    "N-007": NotificationEvent("N-007", "agent_failed", "warning", "Agent failed", "{agent_id} failed on {request_id}: {error}", show_toast=True),
    # Quality gates
    "N-008": NotificationEvent("N-008", "gate_passed", "info", "Quality gate passed", "{gate} passed for {request_id}."),
    "N-009": NotificationEvent("N-009", "gate_failed", "warning", "Quality gate failed", "{gate} failed for {request_id}: {reason}", show_toast=True),
    "N-010": NotificationEvent("N-010", "coverage_below_target", "warning", "Coverage below target", "Coverage at {coverage}% (target: {target}%) for {request_id}.", show_toast=True),
    # GitHub
    "N-011": NotificationEvent("N-011", "pr_created", "info", "PR created", "PR #{pr_number} created for {request_id}."),
    "N-012": NotificationEvent("N-012", "pr_approved", "info", "PR approved", "PR #{pr_number} approved for {request_id}."),
    "N-013": NotificationEvent("N-013", "pr_changes_requested", "warning", "Changes requested", "PR #{pr_number} needs changes: {reason}"),
    "N-014": NotificationEvent("N-014", "pr_merged", "info", "PR merged", "PR #{pr_number} merged for {request_id}."),
    "N-015": NotificationEvent("N-015", "issue_created", "low", "Issue created", "Issue #{issue_number} created for story {story_id}."),
    "N-016": NotificationEvent("N-016", "issue_closed", "low", "Issue closed", "Issue #{issue_number} closed."),
    # Deployment
    "N-017": NotificationEvent("N-017", "deploy_started", "info", "Deployment started", "Deploying {request_id} to {environment}."),
    "N-018": NotificationEvent("N-018", "deploy_completed", "info", "Deployment completed", "Successfully deployed to {environment}."),
    "N-019": NotificationEvent("N-019", "deploy_failed", "critical", "Deployment failed", "Deploy to {environment} failed: {error}", show_toast=True),
    "N-020": NotificationEvent("N-020", "rollback_triggered", "critical", "Rollback triggered", "Auto-rollback on {environment}: {reason}", show_toast=True),
    "N-021": NotificationEvent("N-021", "health_check_failed", "critical", "Health check failed", "Health check failed on {environment}.", show_toast=True),
    # Demo
    "N-022": NotificationEvent("N-022", "demo_test_passed", "info", "Demo tests passed", "Weekly demo test suite passed."),
    "N-023": NotificationEvent("N-023", "demo_test_failed", "warning", "Demo tests failed", "Weekly demo tests failed: {error}", show_toast=True),
    # Budget (cross-cutting-concerns)
    "N-024": NotificationEvent("N-024", "budget_warning", "warning", "Budget warning", "Daily spend at {pct}% of ${limit} limit (${current} used).", show_toast=True),
    "N-025": NotificationEvent("N-025", "budget_exceeded", "critical", "Budget exceeded", "Daily budget of ${limit} exceeded. Requests paused.", show_toast=True),
}


class NotificationCatalog:
    """Provides lookup and formatting for notification events."""

    def get(self, event_id: str) -> NotificationEvent | None:
        return CATALOG.get(event_id)

    def format_title(self, event_id: str, context: dict) -> str:
        event = self.get(event_id)
        if not event:
            return "Unknown event"
        try:
            return event.title_template.format(**context)
        except KeyError:
            return event.title_template

    def format_message(self, event_id: str, context: dict) -> str:
        event = self.get(event_id)
        if not event:
            return "Unknown event"
        try:
            return event.message_template.format(**context)
        except KeyError:
            return event.message_template

    def all_events(self) -> list[NotificationEvent]:
        return list(CATALOG.values())
