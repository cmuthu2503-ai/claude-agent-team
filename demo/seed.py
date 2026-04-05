"""Demo seed script — loads sample data for demo environment."""

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from src.models.base import (
    Deployment,
    DeploymentStatus,
    Notification,
    NotificationSeverity,
    Request,
    RequestStatus,
    Story,
    StoryStatus,
    TaskPriority,
    TaskType,
    Subtask,
    SubtaskStatus,
    User,
    UserRole,
)
from src.state.sqlite_store import SQLiteStateStore


async def seed(db_path: str = "data/agent_team.db") -> None:
    """Seed the database with sample demo data."""
    store = SQLiteStateStore(db_path=db_path)
    await store.initialize()

    # Check if already seeded
    existing = await store.list_requests(limit=1)
    if existing:
        print("Database already seeded. Skipping.")
        await store.close()
        return

    print("Seeding demo database...")

    # Create demo users
    import bcrypt
    pw = bcrypt.hashpw(b"demo123", bcrypt.gensalt()).decode()

    users = [
        User(user_id=str(uuid.uuid4()), username="admin", email="admin@demo.local", role=UserRole.ADMIN),
        User(user_id=str(uuid.uuid4()), username="developer", email="dev@demo.local", role=UserRole.DEVELOPER),
        User(user_id=str(uuid.uuid4()), username="viewer", email="viewer@demo.local", role=UserRole.VIEWER),
    ]
    for u in users:
        await store.create_user(u, pw)

    # Create sample requests
    now = datetime.utcnow()
    requests_data = [
        ("REQ-001", "Build login page with JWT authentication", TaskType.FEATURE, TaskPriority.HIGH, RequestStatus.COMPLETED, now - timedelta(days=3)),
        ("REQ-002", "Fix user profile image upload timeout", TaskType.BUG, TaskPriority.CRITICAL, RequestStatus.COMPLETED, now - timedelta(days=2)),
        ("REQ-003", "Add dark mode support to dashboard", TaskType.FEATURE, TaskPriority.MEDIUM, RequestStatus.IN_PROGRESS, now - timedelta(hours=5)),
        ("REQ-004", "Update API documentation for v2 endpoints", TaskType.DOCS, TaskPriority.LOW, RequestStatus.COMPLETED, now - timedelta(days=1)),
        ("REQ-005", "Build user settings page with profile editing", TaskType.FEATURE, TaskPriority.HIGH, RequestStatus.RECEIVED, now - timedelta(minutes=30)),
    ]

    for rid, desc, ttype, pri, status, created in requests_data:
        req = Request(
            request_id=rid, description=desc, task_type=ttype,
            priority=pri, status=status, created_by="admin",
            created_at=created,
            completed_at=now if status == RequestStatus.COMPLETED else None,
        )
        await store.create_request(req)

    # Create stories for REQ-001
    stories = [
        ("US-001-001", "REQ-001", "Login form UI component", StoryStatus.DONE, "frontend_specialist", 92.0),
        ("US-001-002", "REQ-001", "JWT token generation API", StoryStatus.DONE, "backend_specialist", 88.0),
        ("US-001-003", "REQ-001", "Token refresh middleware", StoryStatus.DONE, "backend_specialist", 85.0),
        ("US-001-004", "REQ-001", "Protected route wrapper", StoryStatus.DONE, "frontend_specialist", 90.0),
        ("US-003-001", "REQ-003", "Theme context provider", StoryStatus.IN_PROGRESS, "frontend_specialist", None),
        ("US-003-002", "REQ-003", "Dark mode CSS variables", StoryStatus.TODO, "frontend_specialist", None),
        ("US-003-003", "REQ-003", "Theme toggle component", StoryStatus.TODO, None, None),
    ]

    for sid, rid, title, status, agent, coverage in stories:
        story = Story(
            story_id=sid, request_id=rid, title=title,
            status=status, assigned_agent=agent, coverage_pct=coverage,
        )
        await store.create_story(story)

    # Create subtasks for REQ-003 (in progress)
    subtasks = [
        ("REQ-003-PRD", "REQ-003", "prd_specialist", SubtaskStatus.COMPLETED),
        ("REQ-003-US", "REQ-003", "user_story_author", SubtaskStatus.COMPLETED),
        ("REQ-003-FE", "REQ-003", "frontend_specialist", SubtaskStatus.IN_PROGRESS),
    ]
    for stid, rid, agent, status in subtasks:
        sub = Subtask(
            subtask_id=stid, request_id=rid, agent_id=agent, status=status,
            started_at=now - timedelta(hours=4),
            completed_at=now - timedelta(hours=3) if status == SubtaskStatus.COMPLETED else None,
        )
        await store.create_subtask(sub)

    # Create a deployment
    dep = Deployment(
        deploy_id="dep-001", request_id="REQ-001",
        git_sha="abc123f", environment="production",
        status=DeploymentStatus.VERIFIED,
        deployed_at=now - timedelta(days=2),
        verified_at=now - timedelta(days=2),
    )
    await store.create_deployment(dep)

    # Create notifications
    notifs = [
        ("n-001", "N-001", NotificationSeverity.INFO, "Request completed", "REQ-001 completed successfully"),
        ("n-002", "N-005", NotificationSeverity.INFO, "Deployed to production", "REQ-001 deployed and verified"),
        ("n-003", "N-010", NotificationSeverity.WARNING, "Coverage below target", "REQ-003 frontend coverage at 72%"),
    ]
    for nid, eid, sev, title, msg in notifs:
        notif = Notification(
            notification_id=nid, event_id=eid, severity=sev,
            title=title, message=msg, created_at=now - timedelta(hours=1),
        )
        await store.create_notification(notif)

    await store.close()
    print(f"Seeded: {len(requests_data)} requests, {len(stories)} stories, {len(subtasks)} subtasks, {len(users)} users, 1 deployment, {len(notifs)} notifications")
    print("Demo credentials: admin/demo123, developer/demo123, viewer/demo123")


if __name__ == "__main__":
    asyncio.run(seed())
