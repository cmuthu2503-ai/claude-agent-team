"""Demo seed script — loads sample data matching story-board-view.html mockup."""

import asyncio
import uuid
from datetime import datetime, timedelta

from src.models.base import (
    AcceptanceCriterion,
    Deployment,
    DeploymentStatus,
    Document,
    Notification,
    NotificationSeverity,
    Request,
    RequestStatus,
    Story,
    StoryStatus,
    TaskPriority,
    TaskType,
    TestCase,
    Subtask,
    SubtaskStatus,
    User,
    UserRole,
)
from src.state.sqlite_store import SQLiteStateStore


async def seed(db_path: str = "data/agent_team.db") -> None:
    """Seed the database with demo data matching the story-board-view.html mockup."""
    store = SQLiteStateStore(db_path=db_path)
    await store.initialize()

    # Check if already seeded
    existing = await store.list_requests(limit=1)
    if existing:
        print("Database already seeded. Skipping.")
        await store.close()
        return

    print("Seeding demo database...")

    # ── Users ──────────────────────────────────────
    import bcrypt
    pw = bcrypt.hashpw(b"demo123", bcrypt.gensalt()).decode()

    users = [
        User(user_id=str(uuid.uuid4()), username="admin", email="admin@demo.local", role=UserRole.ADMIN),
        User(user_id=str(uuid.uuid4()), username="chandramouli", email="chandramouli@demo.local", role=UserRole.ADMIN),
        User(user_id=str(uuid.uuid4()), username="developer", email="dev@demo.local", role=UserRole.DEVELOPER),
        User(user_id=str(uuid.uuid4()), username="viewer", email="viewer@demo.local", role=UserRole.VIEWER),
    ]
    for u in users:
        existing_user = await store.get_user_by_username(u.username)
        if existing_user:
            await store.update_password(existing_user[0].user_id, pw)
        else:
            await store.create_user(u, pw)

    now = datetime.utcnow()

    # ── Requests ───────────────────────────────────
    # REQ-042 is the primary request matching the mockup — active, in development
    # Others provide realistic Command Center history
    requests_data = [
        ("REQ-042", "Login page with JWT authentication", TaskType.FEATURE, TaskPriority.HIGH, RequestStatus.IN_PROGRESS, "chandramouli", now - timedelta(minutes=12)),
        ("REQ-041", "Fix password reset email not sending", TaskType.BUG, TaskPriority.CRITICAL, RequestStatus.COMPLETED, "chandramouli", now - timedelta(days=1)),
        ("REQ-040", "Add user profile avatar upload", TaskType.FEATURE, TaskPriority.MEDIUM, RequestStatus.COMPLETED, "developer", now - timedelta(days=2)),
        ("REQ-039", "Update API documentation for v2 endpoints", TaskType.DOCS, TaskPriority.LOW, RequestStatus.COMPLETED, "developer", now - timedelta(days=3)),
        ("REQ-038", "Dark mode support for dashboard", TaskType.FEATURE, TaskPriority.MEDIUM, RequestStatus.COMPLETED, "chandramouli", now - timedelta(days=5)),
    ]

    for rid, desc, ttype, pri, status, created_by, created in requests_data:
        req = Request(
            request_id=rid, description=desc, task_type=ttype,
            priority=pri, status=status, created_by=created_by,
            created_at=created,
            completed_at=now - timedelta(hours=2) if status == RequestStatus.COMPLETED else None,
        )
        await store.create_request(req)

    # ── Subtasks for REQ-042 (full pipeline) ───────
    subtasks_042 = [
        ("REQ-042-EL", "REQ-042", "engineering_lead", SubtaskStatus.COMPLETED, now - timedelta(minutes=11), now - timedelta(minutes=10)),
        ("REQ-042-PRD", "REQ-042", "prd_specialist", SubtaskStatus.COMPLETED, now - timedelta(minutes=10), now - timedelta(minutes=8)),
        ("REQ-042-US", "REQ-042", "user_story_author", SubtaskStatus.COMPLETED, now - timedelta(minutes=8), now - timedelta(minutes=6)),
        ("REQ-042-BE", "REQ-042", "backend_specialist", SubtaskStatus.IN_PROGRESS, now - timedelta(minutes=6), None),
        ("REQ-042-FE", "REQ-042", "frontend_specialist", SubtaskStatus.IN_PROGRESS, now - timedelta(minutes=6), None),
        ("REQ-042-CR", "REQ-042", "code_reviewer", SubtaskStatus.IN_PROGRESS, now - timedelta(minutes=3), None),
    ]
    for stid, rid, agent, status, started, completed in subtasks_042:
        sub = Subtask(
            subtask_id=stid, request_id=rid, agent_id=agent, status=status,
            started_at=started, completed_at=completed,
        )
        await store.create_subtask(sub)

    # ── Stories for REQ-042 (matching mockup exactly) ──
    #
    # Mockup layout:
    #   To Do: 0 (all picked up)
    #   In Progress: US-001, US-002, US-003
    #   Review: US-004
    #   Testing: 0
    #   Done: US-005
    stories_042 = [
        # (story_id, request_id, title, description, status, priority, assigned_agent, coverage_pct)
        ("US-001", "REQ-042",
         "JWT Authentication API Endpoints",
         "As a user, I want to register and login via REST API so that I receive a JWT token for authentication.",
         StoryStatus.IN_PROGRESS, "high", "backend_specialist", 87.0),

        ("US-002", "REQ-042",
         "Login Page UI",
         "As a user, I want a login page with email and password fields so that I can authenticate.",
         StoryStatus.IN_PROGRESS, "high", "frontend_specialist", 72.0),

        ("US-003", "REQ-042",
         "Registration Page UI",
         "As a new user, I want a registration form with name, email, and password so I can create an account.",
         StoryStatus.IN_PROGRESS, "medium", "frontend_specialist", None),

        ("US-004", "REQ-042",
         "Protected Route Middleware",
         "As a developer, I want a route guard that redirects unauthenticated users to login.",
         StoryStatus.REVIEW, "high", "code_reviewer", 93.0),

        ("US-005", "REQ-042",
         "JWT Token Refresh",
         "As a user, I want my session to auto-refresh so I don't get logged out unexpectedly.",
         StoryStatus.DONE, "medium", "backend_specialist", 91.0),
    ]

    for sid, rid, title, desc, status, pri, agent, cov in stories_042:
        story = Story(
            story_id=sid, request_id=rid, title=title, description=desc,
            status=status, priority=pri, assigned_agent=agent, coverage_pct=cov,
        )
        await store.create_story(story)

    # ── Acceptance Criteria (matching mockup) ──────
    acceptance_criteria = [
        # US-001: JWT Auth API
        ("US-001-AC-01", "US-001",
         "Given valid credentials, when POST /auth/register is called, then user is created and JWT token returned",
         "valid credentials", "POST /auth/register is called", "user is created and JWT token returned", False),
        ("US-001-AC-02", "US-001",
         "Given correct email and password, when POST /auth/login is called, then a valid JWT is returned",
         "correct email and password", "POST /auth/login is called", "a valid JWT is returned", False),
        ("US-001-AC-03", "US-001",
         "Given wrong password, when POST /auth/login is called, then 401 Unauthorized is returned",
         "wrong password", "POST /auth/login is called", "401 Unauthorized is returned", False),
        ("US-001-AC-04", "US-001",
         "Given a valid JWT token, when GET /auth/me is called, then the user profile is returned",
         "a valid JWT token", "GET /auth/me is called", "the user profile is returned", False),
        ("US-001-AC-05", "US-001",
         "Given an expired JWT token, when GET /auth/me is called, then 401 is returned",
         "an expired JWT token", "GET /auth/me is called", "401 is returned", False),

        # US-002: Login Page UI
        ("US-002-AC-01", "US-002",
         "Given a user visits /login, when the page loads, then email and password input fields are rendered",
         "a user visits /login", "the page loads", "email and password input fields are rendered", False),
        ("US-002-AC-02", "US-002",
         "Given empty email field, when form is submitted, then a validation error is shown",
         "empty email field", "form is submitted", "a validation error is shown", False),
        ("US-002-AC-03", "US-002",
         "Given valid credentials, when login form is submitted, then the login API is called",
         "valid credentials", "login form is submitted", "the login API is called", False),
        ("US-002-AC-04", "US-002",
         "Given successful API response, when login completes, then user is redirected to dashboard",
         "successful API response", "login completes", "user is redirected to dashboard", False),

        # US-003: Registration Page UI
        ("US-003-AC-01", "US-003",
         "Given a user visits /register, when the page loads, then name, email, and password fields are rendered",
         "a user visits /register", "the page loads", "name, email, and password fields are rendered", False),
        ("US-003-AC-02", "US-003",
         "Given password less than 8 characters, when form is submitted, then a validation error is shown",
         "password less than 8 characters", "form is submitted", "a validation error is shown", False),
        ("US-003-AC-03", "US-003",
         "Given email already exists, when register is submitted, then an error message is displayed",
         "email already exists", "register is submitted", "an error message is displayed", False),
        ("US-003-AC-04", "US-003",
         "Given valid form data, when register is submitted, then the register API is called",
         "valid form data", "register is submitted", "the register API is called", False),
        ("US-003-AC-05", "US-003",
         "Given successful registration, when API responds, then user is redirected to login page",
         "successful registration", "API responds", "user is redirected to login page", False),

        # US-004: Protected Route Middleware
        ("US-004-AC-01", "US-004",
         "Given an unauthenticated user, when they access a protected route, then they are redirected to /login",
         "an unauthenticated user", "they access a protected route", "they are redirected to /login", True),
        ("US-004-AC-02", "US-004",
         "Given an authenticated user, when they access a protected route, then the child component renders",
         "an authenticated user", "they access a protected route", "the child component renders", True),
        ("US-004-AC-03", "US-004",
         "Given an expired token, when a protected route is accessed, then user is redirected to login",
         "an expired token", "a protected route is accessed", "user is redirected to login", True),
        ("US-004-AC-04", "US-004",
         "Given redirect to login, when the original URL is preserved, then user returns after login",
         "redirect to login", "the original URL is preserved", "user returns after login", True),

        # US-005: JWT Token Refresh (Done — all met)
        ("US-005-AC-01", "US-005",
         "Given a token nearing expiry, when the refresh check runs, then the token refreshes automatically 5 min before expiry",
         "a token nearing expiry", "the refresh check runs", "the token refreshes automatically 5 min before expiry", True),
        ("US-005-AC-02", "US-005",
         "Given a valid refresh token, when POST /auth/refresh is called, then the refresh token is rotated on each use",
         "a valid refresh token", "POST /auth/refresh is called", "the refresh token is rotated on each use", True),
        ("US-005-AC-03", "US-005",
         "Given an invalid refresh token, when POST /auth/refresh is called, then 401 is returned",
         "an invalid refresh token", "POST /auth/refresh is called", "401 is returned", True),
    ]

    for ac_id, story_id, text, given, when_, then_, is_met in acceptance_criteria:
        ac = AcceptanceCriterion(
            ac_id=ac_id, story_id=story_id, criterion_text=text,
            given_clause=given, when_clause=when_, then_clause=then_, is_met=is_met,
        )
        await store.create_acceptance_criterion(ac)

    # ── Test Cases (matching mockup exactly) ───────
    #
    # Mockup shows:
    #   US-001: 3/5 passing (3 pass, 1 running, 1 pending)
    #   US-002: 2/4 passing (2 pass, 1 running, 1 pending)
    #   US-003: 0/5 (all pending — just started)
    #   US-004: 4/4 passing (all pass — in review)
    #   US-005: 4/4 passing (all pass — done)
    #   Total: 14/22 passing
    test_cases_data = [
        # US-001: JWT Auth API — 3/5 passing
        ("TC-001", "US-001", "POST /auth/register creates user and returns token", "pass"),
        ("TC-002", "US-001", "POST /auth/login returns valid JWT for correct credentials", "pass"),
        ("TC-003", "US-001", "POST /auth/login returns 401 for wrong password", "pass"),
        ("TC-004", "US-001", "GET /auth/me returns user profile with valid token", "running"),
        ("TC-005", "US-001", "GET /auth/me returns 401 with expired token", "pending"),

        # US-002: Login Page UI — 2/4 passing
        ("TC-006", "US-002", "Renders email and password input fields", "pass"),
        ("TC-007", "US-002", "Shows validation error for empty email", "pass"),
        ("TC-008", "US-002", "Calls login API on form submit", "running"),
        ("TC-009", "US-002", "Redirects to dashboard after successful login", "pending"),

        # US-003: Registration Page UI — 0/5 (all pending)
        ("TC-010", "US-003", "Renders name, email, password fields", "pending"),
        ("TC-011", "US-003", "Validates password minimum 8 characters", "pending"),
        ("TC-012", "US-003", "Shows error if email already exists", "pending"),
        ("TC-013", "US-003", "Calls register API on submit", "pending"),
        ("TC-014", "US-003", "Redirects to login after registration", "pending"),

        # US-004: Protected Route Middleware — 4/4 passing
        ("TC-015", "US-004", "Redirects to /login when not authenticated", "pass"),
        ("TC-016", "US-004", "Renders child component when authenticated", "pass"),
        ("TC-017", "US-004", "Handles expired token by redirecting", "pass"),
        ("TC-018", "US-004", "Preserves original URL for post-login redirect", "pass"),

        # US-005: JWT Token Refresh — 4/4 passing
        ("TC-019", "US-005", "Refreshes token before expiry", "pass"),
        ("TC-020", "US-005", "Returns new token with extended expiry", "pass"),
        ("TC-021", "US-005", "Rejects refresh with invalid refresh token", "pass"),
        ("TC-022", "US-005", "Rotates refresh token on use", "pass"),
    ]

    for tc_id, story_id, name, status in test_cases_data:
        tc = TestCase(
            test_id=tc_id, story_id=story_id, name=name, status=status,
            last_run_at=now - timedelta(minutes=5) if status in ("pass", "fail") else None,
        )
        await store.create_test_case(tc)

    # ── Stories for older completed requests ───────
    stories_041 = [
        ("US-041-001", "REQ-041", "Password reset email trigger", StoryStatus.DONE, "backend_specialist", 95.0),
        ("US-041-002", "REQ-041", "Email template rendering", StoryStatus.DONE, "backend_specialist", 88.0),
    ]
    stories_040 = [
        ("US-040-001", "REQ-040", "Avatar upload API endpoint", StoryStatus.DONE, "backend_specialist", 90.0),
        ("US-040-002", "REQ-040", "Avatar cropper component", StoryStatus.DONE, "frontend_specialist", 85.0),
        ("US-040-003", "REQ-040", "Profile page avatar display", StoryStatus.DONE, "frontend_specialist", 92.0),
    ]
    stories_038 = [
        ("US-038-001", "REQ-038", "Theme context provider", StoryStatus.DONE, "frontend_specialist", 94.0),
        ("US-038-002", "REQ-038", "Dark mode CSS variables", StoryStatus.DONE, "frontend_specialist", 89.0),
        ("US-038-003", "REQ-038", "Theme toggle component", StoryStatus.DONE, "frontend_specialist", 91.0),
    ]

    for stories_list in [stories_041, stories_040, stories_038]:
        for sid, rid, title, status, agent, cov in stories_list:
            story = Story(
                story_id=sid, request_id=rid, title=title,
                status=status, assigned_agent=agent, coverage_pct=cov,
            )
            await store.create_story(story)

    # ── Documents for REQ-042 (agent outputs) ──────
    docs = [
        ("doc-042-prd", "REQ-042", "prd", "PRD: Login page with JWT authentication", "prd_specialist",
         "# PRD: Login page with JWT authentication\n\n"
         "## Overview\nBuild a complete authentication system with login, registration, JWT token management, and protected routes.\n\n"
         "## Requirements\n"
         "- REQ-001: User registration with email/password\n"
         "- REQ-002: User login returning JWT access + refresh tokens\n"
         "- REQ-003: Protected route middleware\n"
         "- REQ-004: Automatic token refresh\n"
         "- REQ-005: Login and registration UI pages\n\n"
         "## Non-Functional\n- Passwords hashed with bcrypt\n- JWT expiry: 30 min access, 7 day refresh\n- Rate limiting on auth endpoints"),

        ("doc-042-stories", "REQ-042", "user_stories", "User Stories: Login page with JWT authentication", "user_story_author",
         "## User Stories for REQ-042\n\n"
         "### US-001 JWT Authentication API Endpoints\n"
         "**As a** user, **I want** to register and login via REST API **so that** I receive a JWT token.\n"
         "**Priority:** High\n**Effort:** M\n**Traces to:** REQ-001, REQ-002\n\n"
         "**Acceptance Criteria:**\n"
         "- Given valid credentials, when POST /auth/register is called, then user is created and JWT token returned\n"
         "- Given correct email and password, when POST /auth/login is called, then a valid JWT is returned\n"
         "- Given wrong password, when POST /auth/login is called, then 401 Unauthorized is returned\n"
         "- Given a valid JWT token, when GET /auth/me is called, then the user profile is returned\n"
         "- Given an expired JWT token, when GET /auth/me is called, then 401 is returned\n\n"
         "### US-002 Login Page UI\n"
         "**As a** user, **I want** a login page with email and password fields **so that** I can authenticate.\n"
         "**Priority:** High\n**Effort:** M\n\n"
         "### US-003 Registration Page UI\n"
         "**As a** new user, **I want** a registration form **so that** I can create an account.\n"
         "**Priority:** Medium\n**Effort:** M\n\n"
         "### US-004 Protected Route Middleware\n"
         "**As a** developer, **I want** a route guard **so that** unauthenticated users are redirected.\n"
         "**Priority:** High\n**Effort:** S\n\n"
         "### US-005 JWT Token Refresh\n"
         "**As a** user, **I want** automatic session refresh **so that** I don't get logged out unexpectedly.\n"
         "**Priority:** Medium\n**Effort:** M\n"),
    ]

    for doc_id, rid, dtype, title, agent, content in docs:
        doc = Document(
            document_id=doc_id, request_id=rid, doc_type=dtype,
            title=title, agent_id=agent, content=content,
        )
        await store.save_document(doc)

    # ── Deployments ────────────────────────────────
    deployments = [
        Deployment(deploy_id="dep-041", request_id="REQ-041", git_sha="f4a2c1b", environment="production",
                   status=DeploymentStatus.VERIFIED, deployed_at=now - timedelta(days=1), verified_at=now - timedelta(days=1)),
        Deployment(deploy_id="dep-040", request_id="REQ-040", git_sha="a8d3e7f", environment="production",
                   status=DeploymentStatus.VERIFIED, deployed_at=now - timedelta(days=2), verified_at=now - timedelta(days=2)),
        Deployment(deploy_id="dep-038", request_id="REQ-038", git_sha="c1e9b42", environment="production",
                   status=DeploymentStatus.VERIFIED, deployed_at=now - timedelta(days=5), verified_at=now - timedelta(days=5)),
    ]
    for dep in deployments:
        await store.create_deployment(dep)

    # ── Notifications ──────────────────────────────
    notifs = [
        ("n-001", "N-001", NotificationSeverity.INFO, "REQ-042 stories created",
         "User Story Author generated 5 stories for Login page with JWT authentication",
         "REQ-042", now - timedelta(minutes=6)),
        ("n-002", "N-002", NotificationSeverity.INFO, "Backend Specialist working on US-001",
         "JWT Authentication API Endpoints — 3/5 tests passing, 87% coverage",
         "REQ-042", now - timedelta(minutes=4)),
        ("n-003", "N-003", NotificationSeverity.INFO, "US-004 moved to Review",
         "Protected Route Middleware — all 4 tests passing, 93% coverage. Code Reviewer assigned.",
         "REQ-042", now - timedelta(minutes=3)),
        ("n-004", "N-004", NotificationSeverity.INFO, "US-005 completed",
         "JWT Token Refresh — all tests passing, 91% coverage. PR #43 merged.",
         "REQ-042", now - timedelta(minutes=2)),
        ("n-005", "N-005", NotificationSeverity.WARNING, "Coverage below target on US-002",
         "Login Page UI frontend coverage at 72% — target is 80%",
         "REQ-042", now - timedelta(minutes=1)),
        ("n-006", "N-006", NotificationSeverity.INFO, "REQ-041 deployed to production",
         "Password reset email fix deployed and verified",
         "REQ-041", now - timedelta(days=1)),
    ]
    for nid, eid, sev, title, msg, rid, created in notifs:
        notif = Notification(
            notification_id=nid, event_id=eid, severity=sev,
            title=title, message=msg, request_id=rid, created_at=created,
        )
        await store.create_notification(notif)

    await store.close()

    total_stories = len(stories_042) + len(stories_041) + len(stories_040) + len(stories_038)
    print(f"Seeded: {len(requests_data)} requests, {total_stories} stories, "
          f"{len(acceptance_criteria)} ACs, {len(test_cases_data)} TCs, "
          f"{len(subtasks_042)} subtasks, {len(docs)} documents, "
          f"{len(deployments)} deployments, {len(notifs)} notifications")
    print("Demo credentials: admin/demo123, chandramouli/demo123, developer/demo123, viewer/demo123")


if __name__ == "__main__":
    asyncio.run(seed())
