# Feature Gap Design
# GitHub Integration, Deployment & Demo, Notifications & Reports

---

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 1.0 |
| Created Date | 2026-04-05 |
| Last Updated | 2026-04-05 |
| Status | Draft |
| Product Owner | Chandramouli |

---

## AREA 1: GitHub Integration Flow

### 1.1 Answers to All Open Questions

**Q1: Repo creation -- one repo per feature or shared repo?**

Single shared repository per project. The system creates one GitHub repo when the user submits their first feature request. All subsequent features live in this repo. Rationale: agents need to share code (e.g., the Frontend Specialist needs to import from Backend Specialist's API routes). Separate repos would create unnecessary cross-repo dependency management.

The DevOps Specialist (`devops_specialist`) creates the repo during the first `feature_development` workflow run if one does not already exist. The repo name is derived from the first request or configured in a new top-level config value:

```yaml
# config/project.yaml (new file)
project:
  name: "my-project"
  github_org: "agent-team-projects"
  repo_name: "my-project"         # github_org/repo_name
  default_branch: "main"
  tech_stack: "python-react"       # templates: python-react, node-react, python-only
```

**Q2: Branching strategy**

```
main (protected)
  |
  +-- feature/REQ-042-login-page-jwt-auth       (umbrella feature branch)
  |     |
  |     +-- feature/REQ-042-backend             (Backend Specialist's work)
  |     +-- feature/REQ-042-frontend            (Frontend Specialist's work)
  |
  +-- feature/REQ-043-user-profile
  |     |
  |     +-- feature/REQ-043-backend
  |     +-- feature/REQ-043-frontend
  |
  +-- fix/REQ-041-broken-pagination             (bug fix -- flat, no sub-branches)
```

Branch naming convention: `{type}/REQ-{id}-{slug}` where type is `feature`, `fix`, or `docs`. Sub-branches add `-backend` or `-frontend` suffix. The Code Reviewer (`code_reviewer`) creates the umbrella feature branch. The Backend and Frontend Specialists create their sub-branches from the umbrella. PRs merge sub-branches into the umbrella, then the umbrella merges into `main`.

For bug fixes, which are simpler and handled by a single developer agent, a single flat branch is used with no sub-branches.

Who creates what:
- **Code Reviewer** (Development Lead): Creates the umbrella feature branch from `main`
- **Backend Specialist**: Creates `feature/REQ-XXX-backend` from the umbrella branch
- **Frontend Specialist**: Creates `feature/REQ-XXX-frontend` from the umbrella branch
- **DevOps Specialist**: Merges the umbrella branch into `main` after all gates pass

**Q3: GitHub Actions setup**

The DevOps Specialist creates workflow YAML files during the initial repo setup (first request). These are NOT pre-configured templates -- the DevOps agent generates them based on the detected tech stack. However, a set of reference templates exists in `config/github-actions-templates/` for the agent to reference.

Checks that run on every PR:
1. **Lint** (`lint.yml`) -- ESLint/Flake8 depending on stack
2. **Format** (`format.yml`) -- Prettier/Black
3. **Test** (`test.yml`) -- jest/pytest, runs full test suite
4. **Coverage** (`coverage.yml`) -- Generates coverage report, posts as PR comment, blocks merge if below threshold from `config/thresholds.yaml` (default 80%)
5. **Security** (`security.yml`) -- `npm audit` / `safety check` / `pip-audit`

Additional scheduled workflows:
6. **Demo test** (`demo-test.yml`) -- Cron: `0 9 * * 1` (Mondays 9am), runs demo validation
7. **Stale branch cleanup** (`cleanup.yml`) -- Cron: `0 0 * * 0` (Sundays midnight), deletes branches merged > 7 days

The DevOps agent writes these files to `.github/workflows/` in the repo. After the initial creation, the agent updates them when new requirements emerge (e.g., adding an E2E test stage).

**Q4: Issue-to-Story sync**

Yes, bidirectional sync. When the User Story Author creates stories, each story automatically becomes a GitHub Issue. Status sync is bidirectional:

- **Story created** --> GitHub Issue created with labels `user-story`, `REQ-042`, priority label
- **PR merged that closes issue** (`Closes #issue`) --> Story status updated to Done in SQLite
- **Issue manually closed on GitHub** --> Story status updated to Done (webhook listener)
- **Story blocked in system** --> Issue gets `blocked` label

Implementation: The User Story Author agent uses the `git_operations` tool to create issues. A lightweight webhook handler (part of the Orchestrator's API server from `src/core/orchestrator.py`) listens for GitHub webhook events (`issues.closed`, `pull_request.merged`) and updates the SQLite state store.

Mapping stored in SQLite:

```sql
CREATE TABLE story_issue_map (
    story_id TEXT PRIMARY KEY,
    github_issue_number INTEGER NOT NULL,
    request_id TEXT NOT NULL,       -- e.g., REQ-042
    repo_full_name TEXT NOT NULL,   -- e.g., agent-team-projects/my-project
    sync_enabled BOOLEAN DEFAULT 1
);
```

**Q5: PR creation by dev agents**

When Backend/Frontend agents finish coding, they:
1. Stage and commit their changes to their sub-branch
2. Push the sub-branch to origin
3. Create a PR from their sub-branch into the umbrella feature branch using the `git_operations` tool

The PR is created via the GitHub API (wrapped in the `git_operations` tool). The agent populates the PR using a structured template (see section 1.3 below).

Two PRs are created per feature: one from `feature/REQ-042-backend` into `feature/REQ-042-login-page-jwt-auth`, and one from `feature/REQ-042-frontend` into the same umbrella. After both are merged into the umbrella, a final PR is created from the umbrella into `main`. The Code Reviewer creates this final PR after completing review.

**Q6: Code review by AI -- what the Code Reviewer actually does**

The Code Reviewer agent uses the `github_pr_review` tool (already defined in `config/tools.yaml`, exclusively available to `code_reviewer`). The review process:

1. **Fetch the diff**: Read the full PR diff via GitHub API
2. **Run static analysis**: Use `code_analysis` tool (linter, type checker output)
3. **Check coverage**: Use `coverage_report` tool to get coverage delta
4. **Perform review**: The Code Reviewer (Opus 4.6 model -- high reasoning) analyzes the code and produces:
   - **Line-by-line comments**: Posted as GitHub review comments on specific lines using `POST /repos/{owner}/{repo}/pulls/{pr}/reviews` with `comments[]` array containing `path`, `position`, and `body`
   - **Summary comment**: Overall assessment posted as the review body
   - **Verdict**: Either `APPROVE` or `REQUEST_CHANGES` (never `COMMENT` alone -- always a clear signal)

Categories of feedback the Code Reviewer checks:
- Correctness (bugs, logic errors)
- Security (injection, auth bypass)
- Test quality (not just coverage number, but are tests meaningful?)
- Readability and maintainability
- Adherence to project conventions
- API contract consistency (does backend match what frontend expects?)

On `REQUEST_CHANGES`:
- The workflow engine routes back to the `development` stage (`on_fail` in `workflows.yaml`)
- The relevant agent receives the review feedback as input context
- The agent addresses each comment, pushes new commits, and the Code Reviewer re-reviews

On `APPROVE`:
- The PR's CI checks must also pass
- The Code Reviewer merges the PR

**Q7: Branch protection**

Branch protection rules on `main`:
- Require at least 1 PR review approval (the Code Reviewer's `APPROVE` counts)
- Require all status checks to pass (lint, format, test, coverage, security)
- Require branches to be up to date before merging
- No direct pushes to `main` (force push disabled)
- Require linear history (squash merges from umbrella to main)

The Code Reviewer's approval counts as the required review. Since this is an AI-driven system with a single human user, one AI review is sufficient. The human user can optionally add themselves as a required reviewer if they want manual oversight (configurable in `config/project.yaml`).

### 1.2 Complete Flow: "User Submits Feature" to "PRs Merged"

```
USER SUBMITS: "Build a login page with JWT authentication"
        |
        v
[1] ORCHESTRATOR
    - Creates root task ROOT-042
    - Assigns to engineering_lead
        |
        v
[2] ENGINEERING LEAD (Opus 4.6)
    - Analyzes domains: backend, frontend, auth, testing
    - Creates delegation plan:
      SUB-042-A -> prd_specialist (requirements)
      SUB-042-B -> code_reviewer (development) [depends_on: SUB-042-A]
      SUB-042-C -> devops_specialist (delivery) [depends_on: SUB-042-B]
        |
        v
[3] PRD SPECIALIST
    - Writes PRD document
    - Delegates to user_story_author
        |
        v
[4] USER STORY AUTHOR
    - Creates 5 user stories
    - For EACH story: creates a GitHub Issue via git_operations tool
      Issue #10: "US-042-001: Login form with email/password"
      Issue #11: "US-042-002: JWT token generation"
      Issue #12: "US-042-003: Protected route middleware"
      Issue #13: "US-042-004: Registration form"
      Issue #14: "US-042-005: Auth state management"
    - Labels: user-story, REQ-042, priority:high
        |
        v
[5] CODE REVIEWER (Development Lead, Opus 4.6)
    - Creates umbrella branch: feature/REQ-042-login-page-jwt-auth
    - Decomposes into backend + frontend subtasks
    - Delegates to backend_specialist and frontend_specialist (PARALLEL)
        |
        +----------------------------+
        |                            |
        v                            v
[6a] BACKEND SPECIALIST          [6b] FRONTEND SPECIALIST
  (Sonnet 4.6)                     (Sonnet 4.6)
  - git checkout -b                - git checkout -b
    feature/REQ-042-backend          feature/REQ-042-frontend
  - Write API code                 - Write React components
  - Write tests                    - Write tests
  - Run tests locally              - Run tests locally
  - git commit + push              - git commit + push
  - Create PR #15:                 - Create PR #16:
    REQ-042-backend -->              REQ-042-frontend -->
    REQ-042-login-page-jwt-auth      REQ-042-login-page-jwt-auth
  - PR body: (see template)       - PR body: (see template)
  - Link to Issues: #10, #11, #12 - Link to Issues: #13, #14
        |                            |
        +----------------------------+
        |
        v
[7] GITHUB ACTIONS (AUTOMATED - runs on both PRs)
    - lint.yml: ESLint + Flake8 --> PASS
    - format.yml: Prettier + Black --> PASS
    - test.yml: jest + pytest --> PASS
    - coverage.yml: 87% (>= 80%) --> PASS
    - security.yml: No vulnerabilities --> PASS
        |
        v
[8] CODE REVIEWER (Review Phase, Opus 4.6)
    - Fetches PR #15 diff, reviews backend code
      Posts 2 line comments: suggestion on JWT expiry handling
      Verdict: APPROVE
    - Fetches PR #16 diff, reviews frontend code
      Posts 1 line comment: accessibility on login form
      Verdict: REQUEST_CHANGES
        |
        v
[8a] FRONTEND SPECIALIST (Rework)
    - Reads review feedback
    - Fixes accessibility issue
    - Pushes new commit to feature/REQ-042-frontend
    - PR #16 CI re-runs --> PASS
        |
        v
[8b] CODE REVIEWER (Re-review)
    - Re-reviews PR #16 incremental diff
    - Verdict: APPROVE
    - Merges PR #15 into umbrella branch
    - Merges PR #16 into umbrella branch
    - Creates PR #17: umbrella --> main
      "REQ-042: Login page with JWT authentication"
      Links all issues: Closes #10, Closes #11, Closes #12, Closes #13, Closes #14
        |
        v
[9] TESTER SPECIALIST (Sonnet 4.6)
    - Checks out umbrella branch
    - Writes E2E tests for login flow
    - Runs full regression suite
    - All pass --> quality gate PASSED
        |
        v
[10] DEVOPS SPECIALIST (Sonnet 4.6)
    - Merges PR #17 into main (umbrella --> main)
    - GitHub Issues #10-14 auto-close (linked via "Closes #XX")
    - SQLite story status auto-updates via webhook
    - Deploys main to staging
    - Runs smoke tests on staging --> PASS
    - Deploys to production
    - Runs health checks --> PASS
    - Deletes merged branches (feature/REQ-042-*)
        |
        v
[11] ENGINEERING LEAD (Aggregation)
    - Synthesizes final report
    - Returns to user: "Login feature complete. PRs #15, #16, #17 merged.
      Coverage: 87%. Deployed to production."
```

### 1.3 PR Template

The agents fill in this template when creating PRs:

```markdown
## REQ-{id}: {Short Description}

### Summary
{1-3 sentences describing what this PR does and why}

### Type
- [x] Feature / [ ] Bug Fix / [ ] Docs / [ ] Refactor

### Changes
| File | Change |
|------|--------|
| `src/auth/routes.py` | Added POST /auth/login and /auth/register endpoints |
| `src/auth/jwt.py` | JWT token generation and validation utility |
| `tests/test_auth.py` | 8 unit tests for auth endpoints |

### Related Issues
Closes #{issue_number_1}, Closes #{issue_number_2}

### Agent
- **Agent**: Backend Specialist (`backend_specialist`)
- **Request**: REQ-042 — Login page with JWT authentication
- **Branch**: `feature/REQ-042-backend` --> `feature/REQ-042-login-page-jwt-auth`

### Testing
- [ ] Unit tests added/updated
- [ ] Tests pass locally
- [ ] Coverage >= 80%

### Coverage
| Metric | Value |
|--------|-------|
| Lines | 87% |
| Branches | 82% |
| Functions | 91% |

### Notes for Reviewer
{Any specific areas to focus on, trade-offs made, or known limitations}
```

### 1.4 GitHub Actions Pipeline Definition

```yaml
# .github/workflows/ci.yml
name: CI Pipeline

on:
  pull_request:
    branches: [main, 'feature/REQ-*']
  push:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Setup
        uses: actions/setup-node@v4  # or setup-python, depending on stack
      - name: Install dependencies
        run: npm ci  # or pip install -r requirements.txt
      - name: Lint
        run: npm run lint  # or flake8 src/

  format:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Check formatting
        run: npm run format:check  # or black --check src/

  test:
    runs-on: ubuntu-latest
    needs: [lint, format]
    steps:
      - uses: actions/checkout@v4
      - name: Install dependencies
        run: npm ci
      - name: Run tests
        run: npm test -- --coverage
      - name: Check coverage threshold
        run: |
          COVERAGE=$(cat coverage/coverage-summary.json | jq '.total.lines.pct')
          if (( $(echo "$COVERAGE < 80" | bc -l) )); then
            echo "Coverage $COVERAGE% is below 80% threshold"
            exit 1
          fi
      - name: Post coverage comment
        uses: marocchino/sticky-pull-request-comment@v2
        with:
          message: |
            ## Coverage Report
            | Metric | Coverage |
            |--------|----------|
            | Lines | ${{ env.LINE_COV }}% |
            | Branches | ${{ env.BRANCH_COV }}% |
            | Functions | ${{ env.FUNC_COV }}% |

  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Dependency audit
        run: npm audit --audit-level=high  # or pip-audit

# .github/workflows/demo-test.yml
name: Weekly Demo Test
on:
  schedule:
    - cron: '0 9 * * 1'   # From config/thresholds.yaml demo_test_frequency
  workflow_dispatch: {}     # Manual trigger

jobs:
  demo-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Setup environment
        run: make setup-demo
      - name: Run demo
        run: make demo
      - name: Validate demo
        run: make demo-test
      - name: Notify on failure
        if: failure()
        # Posts to notification system (see Area 3)

# .github/workflows/cleanup.yml
name: Stale Branch Cleanup
on:
  schedule:
    - cron: '0 0 * * 0'   # From config/thresholds.yaml stale_branch_age
jobs:
  cleanup:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Delete stale merged branches
        run: |
          git branch -r --merged origin/main |
            grep -v main |
            sed 's/origin\///' |
            xargs -I {} git push origin --delete {}
```

### 1.5 Code Review Interaction Model

```
CODE REVIEWER RECEIVES: PR #15 ready for review
        |
        v
STEP 1: GATHER CONTEXT
  - Fetch PR diff via GitHub API: GET /repos/{owner}/{repo}/pulls/15/files
  - Read linked issues to understand requirements
  - Read user stories referenced in PR description
  - Load project conventions from .editorconfig / eslint config
        |
        v
STEP 2: AUTOMATED CHECKS
  - Run code_analysis tool (static analysis, lint, type check)
  - Run coverage_report tool (get per-file coverage for changed files)
  - Wait for CI status checks (poll GET /repos/{owner}/{repo}/commits/{sha}/check-runs)
        |
        v
STEP 3: AI REVIEW (Opus 4.6 reasoning)
  - Review each changed file for:
    * Correctness: Logic errors, off-by-one, null handling
    * Security: Injection, auth bypass, secrets in code
    * Test quality: Are tests testing real behavior or just gaming coverage?
    * Readability: Clear naming, appropriate comments, manageable complexity
    * Architecture: Does it fit the project patterns? Proper separation of concerns?
    * API contract: Does the backend match the frontend's expectations?
        |
        v
STEP 4: POST REVIEW
  - API call: POST /repos/{owner}/{repo}/pulls/15/reviews
    {
      "body": "## Review Summary\n\nClean implementation of JWT auth...",
      "event": "APPROVE" | "REQUEST_CHANGES",
      "comments": [
        {
          "path": "src/auth/jwt.py",
          "line": 42,
          "body": "Consider using a shorter default expiry (15min instead of 1hr)
                   for access tokens. Use refresh tokens for longer sessions."
        }
      ]
    }
        |
        +-- APPROVE --> Merge PR, proceed to next stage
        |
        +-- REQUEST_CHANGES --> Agent reworks, pushes new commits
              |
              v
            STEP 5: RE-REVIEW (incremental)
              - Only review new commits since last review
              - If all issues addressed: APPROVE
              - If new issues found: REQUEST_CHANGES again (max 3 cycles)
              - After 3 cycles: Escalate to Engineering Lead
```

### Quality Gate — Combined Feedback Loop

The pipeline enforces a combined quality gate after BOTH Code Review and Testing complete:

**Gate Logic:**
- After Tester completes, the workflow runner checks BOTH review output AND test results
- Review pass: `**APPROVED**` with zero `[CRITICAL]` findings
- Test pass: zero `FAIL` test cases with `**READY FOR DEPLOYMENT**`
- BOTH must pass for the gate to open
- On fail: review findings + test failures aggregated into one `rework_instructions` package
- Pipeline routes back to `development` stage
- Dev agents fix ALL issues (review + test) in one pass
- Code Reviewer re-reviews, Tester re-tests
- Max 2 rework cycles, then FAILED (DevOps never runs on broken code)

**Implementation:**
- `src/workflows/runner.py` — `_check_combined_gate()` checks both review and test output
- `src/workflows/runner.py` — `_extract_review_findings()` + `_extract_test_failures()` parse feedback
- `src/workflows/runner.py` — `MAX_REWORK_CYCLES = 2`
- `src/core/orchestrator.py` — checks for `escalation_reason` to set FAILED status
- All 5 agent prompts updated for rework/re-review/re-test behavior

---

## AREA 2: Deployment and Demo

### 2.1 Answers to All Open Questions

**Q1: Deployment target -- practical default recommendation**

**Docker containers managed entirely via Docker Compose.** The entire application stack — backend, frontend, database, and all environments — runs in Docker. No external cloud services required.

- **Default target**: Docker Compose with multi-environment support
- **Why Docker-only**: Self-contained, reproducible, no vendor lock-in, works offline, no cloud accounts needed. The DevOps agent manages everything through `docker compose` commands. Environments are isolated via separate Compose profiles/files.
- **Future option**: If cloud deployment is needed later, the Docker images are already built — just push to any container registry and deploy to any platform.

Configuration in a new section of `config/project.yaml`:

```yaml
deployment:
  provider: "docker"                # docker (only supported provider for v1)
  environments:
    local:
      compose_file: "docker-compose.yml"
      port_backend: 8000
      port_frontend: 3000
      auto_deploy: false
    staging:
      compose_file: "docker-compose.staging.yml"
      port_backend: 8010
      port_frontend: 3010
      auto_deploy: true             # Deploy on PR merge to main
      url: "http://localhost:3010"
    production:
      compose_file: "docker-compose.prod.yml"
      port_backend: 8020
      port_frontend: 3020
      auto_deploy: false            # Requires explicit promotion from staging
      url: "http://localhost:3020"
    demo:
      compose_file: "docker-compose.demo.yml"
      port_backend: 8030
      port_frontend: 3030
      auto_deploy: false            # Manual trigger or cron refresh
      url: "http://localhost:3030"
      seed_data: true
```

**Q2: Environments**

Four environments:

| Environment | Purpose | Lifecycle | URL Pattern |
|-------------|---------|-----------|-------------|
| **Local** | Agent development and testing during coding | Ephemeral — spun up by agents, torn down after | `localhost:8000` / `:3000` |
| **Staging** | Integration testing after merge to main | Persistent — auto-deployed on every merge to `main` | `localhost:8010` / `:3010` |
| **Production** | Live user-facing | Persistent — promoted from staging after smoke tests pass | `localhost:8020` / `:3020` |
| **Demo** | Dedicated demo with seeded data | Persistent — refreshed weekly or on-demand | `localhost:8030` / `:3030` |

Key distinctions:
- **All environments run as Docker Compose stacks** on the same host, isolated by port and Docker network.
- **Local** is used by the Backend and Frontend Specialists while coding and running tests. Ephemeral — spun up/down per task.
- **Staging** mirrors production but gets deployed first. Smoke tests run here before production promotion. Has its own database.
- **Production** is the live environment. Promoted from staging only after smoke tests pass.
- **Demo** is completely separate from staging/production. It has its own database seeded with curated sample data. It is never affected by staging/production deploys.

Docker Compose files:
```
docker-compose.yml              # Local development (default)
docker-compose.staging.yml      # Staging environment (port 8010/3010)
docker-compose.prod.yml         # Production environment (port 8020/3020)
docker-compose.demo.yml         # Demo environment (port 8030/3030, with seed data)
docker-compose.override.yml     # Local overrides (gitignored)
```

**Q3: Deployment pipeline -- exact sequence**

```
CODE MERGED TO MAIN
        |
        v
[1] BUILD
    DevOps Specialist triggers:
    - docker compose -f docker-compose.staging.yml build
    - Tag images: {project}-backend:{git-sha-short}, {project}-frontend:{git-sha-short}
        |
        v
[2] DEPLOY TO STAGING
    - docker compose -f docker-compose.staging.yml down
    - docker compose -f docker-compose.staging.yml up -d
    - Wait for containers to be healthy (poll health endpoint)
    - Timeout: 2 minutes (configurable in thresholds.yaml)
        |
        v
[3] SMOKE TESTS ON STAGING
    Tester Specialist runs:
    - Health check: GET /health returns 200
    - API smoke: Core endpoints return expected shapes
    - UI smoke: Playwright/Cypress hits critical paths
      (login flow, main page load, key feature)
    - Duration: ~2 minutes
        |
        +-- FAIL --> STOP. Do NOT deploy to production.
        |            Alert user (see Area 3).
        |            DevOps Specialist rolls back staging.
        |
        +-- PASS
        |
        v
[4] DEPLOY TO PRODUCTION
    - docker compose -f docker-compose.prod.yml down
    - docker compose -f docker-compose.prod.yml up -d
    - Wait for containers to be healthy
    - Timeout: 2 minutes
        |
        v
[5] HEALTH CHECKS ON PRODUCTION
    DevOps Specialist runs:
    - GET /health returns 200
    - Response time < 500ms
    - Database connectivity check
    - External service connectivity (if applicable)
    - Duration: ~1 minute
        |
        +-- FAIL --> AUTO-ROLLBACK (see Q4 below)
        |
        +-- PASS
        |
        v
[6] POST-DEPLOY VERIFICATION
    - Tag the deployment in SQLite state store:
      { deploy_id, git_sha, timestamp, status: "verified", environment: "production" }
    - Update the Releases screen via WebSocket event: deploy.completed
    - Engineering Lead includes deployment URL in final aggregated response
        |
        v
DONE. Production is live.
```

**Q4: Rollback**

Two rollback mechanisms:

**Auto-rollback (on health check failure):**
```
Production health check FAILS
        |
        v
[1] DevOps Specialist detects failure
    - 3 consecutive health check failures within 1 minute
        |
        v
[2] AUTOMATIC ROLLBACK
    - docker compose -f docker-compose.prod.yml down
    - Retag previous known-good image as :current
    - docker compose -f docker-compose.prod.yml up -d (with previous image)
    - No human intervention required
        |
        v
[3] VERIFY ROLLBACK
    - Run health checks on the rolled-back version
    - If healthy: mark deployment as "rolled_back" in SQLite
    - If still unhealthy: CRITICAL ALERT to user (production is down)
        |
        v
[4] NOTIFY
    - WebSocket event: deploy.rolled_back
    - Releases screen shows orange "Rolled Back" badge
    - Notification sent to user (see Area 3)
    - The failed request's status is set to FAILED with reason
```

**Manual rollback (user-triggered):**
- Releases screen shows a `[Rollback]` button on the latest production deployment
- Clicking it triggers the DevOps Specialist to run the same rollback procedure
- Available via API: `POST /api/releases/:deploy_id/rollback`

Rollback state is stored:

```sql
CREATE TABLE deployments (
    deploy_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    git_sha TEXT NOT NULL,
    environment TEXT NOT NULL,        -- staging | production | demo
    status TEXT NOT NULL,             -- deploying | active | verified | rolled_back | failed
    previous_deploy_id TEXT,          -- for rollback chain
    deployed_at TIMESTAMP,
    verified_at TIMESTAMP,
    rolled_back_at TIMESTAMP
);
```

**Q5: What is the demo?**

The demo is a **live URL** with seeded sample data, runnable with a single command. Specifically:

1. **Live URL**: `http://localhost:3030` -- a fully functional running instance in Docker
2. **Single command**: `docker compose -f docker-compose.demo.yml up -d` (or `make demo`) -- spins up the demo environment
3. **Guided walkthrough**: A Markdown script at `docs/demo-script.md` that walks through the key features step-by-step. The PRD Specialist generates this during the `demo_preparation` workflow.
4. **Not a recording**: Live interactive demo, not a video. However, the Tester Specialist can generate a Playwright trace file that acts as a visual recording if needed.

**Q6: Demo environment**

Completely separate from staging and production:
- Own Docker Compose stack (`docker-compose.demo.yml`) on isolated ports and Docker network
- Own database container with deterministic seed data
- Seed data is version-controlled in `demo/seed-data/` directory
- Seeded with curated, realistic-looking sample data (not random/lorem ipsum)
- The DevOps Specialist creates and manages this environment

Seed data generation:
```
demo/
  seed-data/
    users.json          # 5 sample users with realistic names
    posts.json          # 10 sample posts
    settings.json       # Default app settings
  seed.py               # Script to load seed data into the demo database
  Dockerfile.demo       # Demo-specific Dockerfile (if different from prod)
```

The seed script runs automatically when the demo container starts (Docker entrypoint runs `seed.py` on first boot).

**Q7: Weekly demo tests**

- **Trigger**: GitHub Actions cron job (`0 9 * * 1` -- Mondays at 9am, from `config/thresholds.yaml`)
- **What runs**: The `demo-test.yml` workflow (defined in Area 1 section 1.4):
  1. Start the demo environment (or connect to the live demo URL)
  2. Run the Tester Specialist's demo-specific E2E test suite
  3. Validate all demo-critical paths work
  4. Check demo environment health (DB connectivity, seed data present, endpoints responsive)
- **On failure**:
  1. GitHub Actions marks the workflow run as failed
  2. Notification sent to user via the notification system (see Area 3)
  3. A GitHub Issue is auto-created: "Demo test failure: {date} -- {failure_summary}"
  4. The DevOps Specialist is alerted to investigate
- **On success**: A status badge is updated; the weekly report (Area 3) includes "Demo: healthy"

### 2.2 Environment Architecture Diagram

```
                    ┌──────────────────────────────────────────────────────┐
                    │                    GITHUB                              │
                    │                                                        │
                    │  Repository: agent-team-projects/my-project            │
                    │  .github/workflows/                                    │
                    │    ci.yml (lint, test, coverage on PR)                 │
                    │    deploy.yml (staging + production on merge)          │
                    │    demo-test.yml (weekly cron)                         │
                    │    cleanup.yml (weekly branch cleanup)                 │
                    └────────────────────────┬───────────────────────────────┘
                                             │
                    ┌────────────────────────▼───────────────────────────────┐
                    │           DOCKER HOST (single machine)                  │
                    │                                                         │
                    │  ┌─────────────────────────────────────────────────┐   │
                    │  │  LOCAL IMAGES                                    │   │
                    │  │  myproject-backend:{git-sha}                     │   │
                    │  │  myproject-frontend:{git-sha}                    │   │
                    │  │  Retained: last 10 images (for rollback)         │   │
                    │  └────┬──────────────┬───────────────┬─────────────┘   │
                    │       │              │               │                  │
                    │  ┌────▼──────────┐ ┌─▼────────────┐ ┌▼──────────────┐ │
                    │  │  STAGING       │ │ PRODUCTION    │ │ DEMO          │ │
                    │  │                │ │               │ │               │ │
                    │  │  Compose:      │ │ Compose:      │ │ Compose:      │ │
                    │  │  staging.yml   │ │ prod.yml      │ │ demo.yml      │ │
                    │  │                │ │               │ │               │ │
                    │  │  backend:8010  │ │ backend:8020  │ │ backend:8030  │ │
                    │  │  frontend:3010 │ │ frontend:3020 │ │ frontend:3030 │ │
                    │  │  db:5433       │ │ db:5434       │ │ db:5435       │ │
                    │  │                │ │               │ │               │ │
                    │  │  Network:      │ │ Network:      │ │ Network:      │ │
                    │  │  staging-net   │ │ prod-net      │ │ demo-net      │ │
                    │  │                │ │               │ │ Seeded data   │ │
                    │  │  Auto-deploy   │ │ Promoted from │ │ Refreshed     │ │
                    │  │  on merge      │ │ staging       │ │ weekly        │ │
                    │  └────────────────┘ └───────────────┘ └───────────────┘ │
                    │                                                         │
                    │  ┌─────────────────────────────────────────────────┐   │
                    │  │  LOCAL DEV                                       │   │
                    │  │  docker-compose.yml                              │   │
                    │  │  backend:8000  frontend:3000  db:5432            │   │
                    │  │  Network: dev-net                                │   │
                    │  │  Used by agents during coding                    │   │
                    │  └─────────────────────────────────────────────────┘   │
                    │                                                         │
                    └─────────────────────────────────────────────────────────┘
```

### 2.3 Deployment Pipeline Flow (step by step)

```
PR #17 MERGED TO MAIN  (umbrella branch --> main)
     |
     v
[GITHUB ACTION: deploy.yml TRIGGERED]
     |
     v
+------------------------------------------+
| STEP 1: BUILD                             |
|                                           |
| docker compose -f docker-compose.staging  |
|   .yml build                              |
| Images tagged:                            |
|   myproject-backend:abc1234               |
|   myproject-frontend:abc1234              |
|                                           |
| Duration: ~2 min                          |
| Failure: Alert user, stop pipeline        |
+------------------+------------------------+
                   |
                   v
+------------------------------------------+
| STEP 2: DEPLOY TO STAGING                 |
|                                           |
| docker compose -f docker-compose.staging  |
|   .yml down                               |
| docker compose -f docker-compose.staging  |
|   .yml up -d                              |
|                                           |
| Wait for healthy status (poll /health)    |
| Timeout: 2 min                            |
|                                           |
| Failure: Alert user, stop pipeline        |
+------------------+------------------------+
                   |
                   v
+------------------------------------------+
| STEP 3: SMOKE TESTS                      |
|                                           |
| Tester Specialist runs against staging:   |
|   GET  /health              --> 200       |
|   POST /auth/login          --> 200 + JWT |
|   GET  /api/users/me        --> 200       |
|   Load /login page          --> rendered  |
|   Load /profile page        --> rendered  |
|                                           |
| Duration: ~2 min                          |
|                                           |
| FAIL --> Rollback staging, alert user     |
| PASS --> Continue                         |
+------------------+------------------------+
                   |
                   v
+------------------------------------------+
| STEP 4: DEPLOY TO PRODUCTION              |
|                                           |
| docker compose -f docker-compose.prod     |
|   .yml down                               |
| docker compose -f docker-compose.prod     |
|   .yml up -d                              |
|                                           |
| Wait for healthy status (poll /health)    |
| Timeout: 2 min                            |
|                                           |
| Failure: Auto-rollback production         |
+------------------+------------------------+
                   |
                   v
+------------------------------------------+
| STEP 5: PRODUCTION HEALTH CHECKS          |
|                                           |
| 3 consecutive checks, 20s apart:         |
|   GET /health           --> 200           |
|   Response time         --> < 500ms       |
|   DB connectivity       --> OK            |
|                                           |
| ALL PASS --> Mark deployment "verified"   |
| ANY FAIL --> Auto-rollback to previous    |
|             deployment, alert user        |
+------------------+------------------------+
                   |
                   v
+------------------------------------------+
| STEP 6: POST-DEPLOY                       |
|                                           |
| Record in SQLite deployments table        |
| Emit WebSocket: deploy.completed          |
| Update Releases screen                    |
| Clean up old staging deployments          |
+------------------------------------------+
```

### 2.4 Demo Creation and Testing Flow

```
DEMO PREPARATION (triggered by demo_request or manual)
     |
     v
[DEVOPS SPECIALIST + TESTER SPECIALIST in PARALLEL]
     |                              |
     v                              v
+---------------------+  +---------------------+
| ENVIRONMENT SETUP   |  | TEST PLAN           |
|                     |  |                     |
| 1. Create/update    |  | 1. Identify demo-   |
|    Docker demo env  |  |    critical paths   |
| 2. Deploy latest    |  | 2. Write E2E tests  |
|    stable build     |  |    for demo flows   |
| 3. Run seed script  |  | 3. Create demo      |
|    (demo/seed.py)   |  |    walkthrough      |
| 4. Verify seed data |  |    script (markdown)|
|    is correct       |  |                     |
+----------+----------+  +----------+----------+
           |                         |
           +------------+------------+
                        |
                        v
           +---------------------------+
           | VALIDATION (Tester Spec.) |
           |                           |
           | Run demo E2E tests        |
           | against live demo URL     |
           |                           |
           | Check:                    |
           |  - All pages load         |
           |  - Sample data visible    |
           |  - Core features work     |
           |  - No error states        |
           |                           |
           | PASS --> Demo ready       |
           | FAIL --> Fix and retry    |
           +---------------------------+

WEEKLY DEMO TEST (GitHub Actions cron, Mondays 9am)
     |
     v
+------------------------------------------+
| 1. Connect to demo URL                    |
| 2. Run demo E2E test suite                |
| 3. Validate seed data integrity           |
| 4. Check all demo-critical paths          |
+------------------+-----------+-----------+
                   |           |
                PASS         FAIL
                   |           |
                   v           v
         Log "Demo     Create GitHub Issue
         healthy" in   "Demo failure: {date}"
         weekly report  + Notify user
                        + Alert DevOps Specialist
```

### 2.5 Rollback Procedure

```
TRIGGER: Health check failure OR manual user action
     |
     v
+------------------------------------------+
| 1. IDENTIFY ROLLBACK TARGET               |
|                                           |
|    Query: SELECT * FROM deployments       |
|    WHERE environment = 'production'       |
|    AND status = 'verified'                |
|    ORDER BY deployed_at DESC              |
|    LIMIT 1 OFFSET 1                       |
|    --> previous_deploy: {git_sha: xyz789} |
+------------------+------------------------+
                   |
                   v
+------------------------------------------+
| 2. EXECUTE ROLLBACK                       |
|                                           |
|    docker compose -f docker-compose.prod  |
|      .yml down                            |
|    Retag previous image as :current       |
|    docker compose -f docker-compose.prod  |
|      .yml up -d                           |
|    (redeploys previous verified image)    |
+------------------+------------------------+
                   |
                   v
+------------------------------------------+
| 3. VERIFY ROLLBACK                        |
|                                           |
|    Run health checks on rolled-back ver.  |
|    3 checks, 20s apart                    |
|                                           |
|    HEALTHY --> Mark current as            |
|               "rolled_back", continue     |
|    UNHEALTHY --> CRITICAL ALERT           |
|                  "Production is down"      |
+------------------+------------------------+
                   |
                   v
+------------------------------------------+
| 4. UPDATE STATE                           |
|                                           |
|    UPDATE deployments SET                 |
|      status = 'rolled_back',             |
|      rolled_back_at = NOW()              |
|    WHERE deploy_id = {failed_deploy};    |
|                                           |
|    Emit WebSocket: deploy.rolled_back     |
|    Notify user (Area 3 notification)      |
|    Releases screen shows orange badge     |
+------------------------------------------+
```

---

## AREA 3: Notifications and Reports

### 3.1 Answers to All Open Questions

**Q1: Weekly reports**

- **Trigger**: Cron job scheduled via the workflow engine. The Orchestrator runs a `weekly_report` internal task every Monday at 9am (aligned with `demo_test_frequency` from `config/thresholds.yaml`). This is NOT a GitHub Action -- it runs within the Agent Team system itself. The Engineering Lead synthesizes the report from SQLite state data.
- **Who sees them**: The primary user (Chandramouli / project owner)
- **Delivery (v1)**: Three simultaneous delivery channels:
  1. **Saved as Markdown** to `reports/weekly/YYYY-MM-DD.md` in the project repo (committed by DevOps Specialist)
  2. **Displayed in UI** on the Command Center as a dismissible banner/card that appears on Monday
  3. **Available via API**: `GET /api/reports/weekly/latest`
- **Delivery (v2)**: Email delivery (configured in `config/project.yaml`)

**Q2: Error UX in UI -- agent failure**

In Command Center:
- The request card's pipeline bar shows the failed stage with a red segment
- The Live Activity feed shows: `"Backend Specialist   Task failed: test suite error   12s ago"` with a red indicator
- The card gets an amber/warning border while retry is in progress, red border if permanently failed
- A `[View Details]` link leads to the Request Detail with full error info

In Request Detail:
- The failing agent's timeline card turns red with the error message prominently displayed
- Error details are expandable: stack trace, tool output, number of retries attempted
- A `[Retry]` button appears on the failed agent card
- Downstream waiting stages show: `"Blocked -- waiting on failed Backend Specialist task"`

In Story Board (if implemented as a Kanban view of stories):
- The affected story card gets a red border and a warning icon
- The card shows: `"Blocked: agent failure in development"`
- Hovering/clicking shows which agent failed and the error summary

Actions the user can take:
1. **Retry**: Click `[Retry]` to re-run the failed agent's task with the same inputs
2. **View Logs**: Click `[View Logs]` to see full agent execution trace
3. **Skip**: (v2) Click `[Skip and Continue]` to proceed without the failed stage
4. **Cancel**: Click `[Cancel Request]` to abandon the entire request

**Q3: Quality gate failure UX**

When coverage drops below 80% (or any quality gate fails):

- The Quality Gate Indicator (already designed in `ui-design.md` section 10.5) turns red:
  `"Coverage Gate:  72% (threshold: 80%)     FAILED --> back to development"`
- The pipeline bar shows the review stage in red, with an animated arrow looping back to development
- A **banner notification** appears at the top of Request Detail:
  ```
  ⚠ Quality Gate Failed: Code coverage is 72% (minimum: 80%)
  The development stage is re-running to add more tests.
  ```
- The story card in Story Board gets a yellow/amber border with text: `"Quality gate: coverage 72% < 80%"`
- The story card does NOT flash red -- amber indicates "in rework" (recoverable), red is reserved for permanent failure

In Command Center:
- The request card pipeline bar shows the review stage pulsing amber
- Live feed shows: `"Code Reviewer   Coverage gate failed: 72% < 80%. Sending back to dev."`

**Q4: Blocked task alerts**

- **In Command Center**: A small orange badge appears on the request card: `"1 blocked"`
- **In Request Detail**: The blocked agent's timeline card shows a yellow/amber state: `"Blocked: waiting on Backend Specialist (failed)"` with a clock icon showing how long it has been blocked
- **Toast notification**: A non-intrusive toast slides in from the bottom-right when a task becomes newly blocked: `"REQ-042: Frontend review is blocked -- Backend tests failed"`
- **Notification bell**: The bell icon (see Q6) shows a count badge: `(1)` for the blocked task
- **Duration escalation**: If a task has been blocked for more than the `review_sla` threshold (24 hours from `config/thresholds.yaml`), the notification becomes more urgent: the toast reappears, the bell badge turns red, and the banner on Request Detail becomes red

**Q5: Deployment failure alerts**

When production deploy fails:
1. **Immediate** (within seconds): WebSocket event `deploy.failed` triggers:
   - Red toast notification: `"PRODUCTION DEPLOYMENT FAILED -- REQ-042. Auto-rollback initiated."`
   - Releases screen updates: deployment card turns red
   - Command Center: request card shows `"Deploy: FAILED"` in pipeline bar
2. **Within 1 minute**: After auto-rollback:
   - Toast: `"Production rolled back to previous version. Investigating failure."`
   - Releases screen shows orange "Rolled Back" badge
3. **Channel**: In-app notifications only for v1. Severity: CRITICAL.

**Q6: Notification channels -- v1 minimum**

v1 implements two channels:

1. **In-app notification bell**: A bell icon in the top navigation bar with a count badge. Clicking opens a dropdown panel showing recent notifications, newest first. Each notification has: icon, message, timestamp, and a link to the relevant screen.

2. **In-app toast notifications**: Non-intrusive toast messages that slide in from the bottom-right corner for time-sensitive events. Auto-dismiss after 8 seconds. Toasts only show for the current browser session.

v2 additions (not in scope now, but architecture supports them):
- Browser push notifications (via Service Worker)
- Email digest (daily or per-event, configurable)
- Slack webhook integration
- GitHub notification integration

**Q7: Notification preferences**

Fixed for v1 -- no user configuration. All notification events are delivered through the two channels (bell + toast). The system uses sensible defaults:

- CRITICAL events (deploy failure, production down): Toast + bell + persist until acknowledged
- WARNING events (quality gate failure, blocked task, rollback): Toast + bell
- INFO events (stage completed, PR merged, demo healthy): Bell only (no toast)
- LOW events (branch cleanup, weekly report available): Bell only

v2 will add a settings screen with per-event-type channel preferences.

### 3.2 Notification Event Catalog

Every event that triggers a notification:

| Event ID | Event | Severity | Toast | Bell | Trigger |
|----------|-------|----------|-------|------|---------|
| `N-001` | Request submitted | INFO | No | Yes | User clicks Submit |
| `N-002` | Stage completed | INFO | No | Yes | Any workflow stage finishes |
| `N-003` | All stages completed (request done) | INFO | Yes | Yes | Final stage completes successfully |
| `N-004` | Agent task failed | WARNING | Yes | Yes | Agent returns FAILED status after retries |
| `N-005` | Agent task failed permanently | CRITICAL | Yes | Yes | Agent fails after max retries, no recovery |
| `N-006` | Quality gate failed | WARNING | Yes | Yes | Coverage, review, or test gate fails |
| `N-007` | Quality gate passed (after failure) | INFO | No | Yes | Previously failed gate now passes |
| `N-008` | Task blocked | WARNING | Yes | Yes | Task cannot proceed due to dependency failure |
| `N-009` | Task blocked > SLA | CRITICAL | Yes | Yes | Blocked task exceeds review_sla threshold |
| `N-010` | PR created | INFO | No | Yes | Backend/Frontend agent creates a PR |
| `N-011` | PR review: changes requested | WARNING | Yes | Yes | Code Reviewer requests changes |
| `N-012` | PR merged | INFO | No | Yes | PR is merged |
| `N-013` | Deployed to staging | INFO | No | Yes | Staging deployment succeeds |
| `N-014` | Staging smoke tests failed | WARNING | Yes | Yes | Smoke tests fail on staging |
| `N-015` | Deployed to production | INFO | Yes | Yes | Production deployment succeeds |
| `N-016` | Production deploy failed | CRITICAL | Yes | Yes | Production deployment fails |
| `N-017` | Production auto-rollback | CRITICAL | Yes | Yes | Auto-rollback triggered |
| `N-018` | Production health check failed | CRITICAL | Yes | Yes | Post-deploy health checks fail |
| `N-019` | Demo test passed (weekly) | INFO | No | Yes | Weekly demo cron test passes |
| `N-020` | Demo test failed (weekly) | WARNING | Yes | Yes | Weekly demo cron test fails |
| `N-021` | Weekly report available | INFO | No | Yes | Monday 9am report generated |
| `N-022` | Stale branches cleaned | LOW | No | Yes | Weekly cleanup removes old branches |
| `N-023` | Request retry initiated | INFO | Yes | Yes | User clicks Retry on failed request |

### 3.3 Notification Delivery Model

```
EVENT OCCURS (e.g., deploy.failed)
        |
        v
+------------------------------------------+
| NOTIFICATION SERVICE                      |
| (src/notifications/service.py)            |
|                                           |
| 1. Look up event in notification catalog  |
| 2. Determine severity                     |
| 3. Format message using template          |
| 4. Store in notifications table (SQLite)  |
| 5. Route to appropriate channels          |
+------+------------------+----------------+
       |                  |
       v                  v
+-------------+  +------------------+
| BELL (all   |  | TOAST (WARNING   |
| events)     |  | and CRITICAL     |
|             |  | only)            |
| Store in    |  |                  |
| SQLite      |  | Emit WebSocket   |
| Increment   |  | event:           |
| badge count |  | notification.    |
| Emit WS:    |  | toast            |
| notification|  |                  |
| .new        |  | Frontend shows   |
+-------------+  | toast component  |
                 +------------------+
```

SQLite schema for notifications:

```sql
CREATE TABLE notifications (
    notification_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,           -- N-001, N-002, etc.
    severity TEXT NOT NULL,           -- CRITICAL, WARNING, INFO, LOW
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    request_id TEXT,                  -- nullable, links to the relevant request
    link_url TEXT,                    -- deep link into the UI
    created_at TIMESTAMP NOT NULL,
    read_at TIMESTAMP,               -- null = unread
    dismissed_at TIMESTAMP           -- null = not dismissed
);
```

API endpoints for notifications:

```
GET    /api/notifications              List notifications (paginated, filtered by read/unread)
GET    /api/notifications/unread-count Get count for bell badge
PATCH  /api/notifications/:id/read     Mark as read
PATCH  /api/notifications/read-all     Mark all as read
DELETE /api/notifications/:id          Dismiss a notification
```

WebSocket events:

```
notification.new    -> Bell badge count increments, new item in dropdown
notification.toast  -> Toast component renders with message
```

### 3.4 Error/Failure UI States

**Command Center -- Request Card in Error State:**

```
┌──────────────────────────────────────────────────────────────────────┐
│  ❌ REQ-042  Login page with JWT authentication                      │
│    Started 18 min ago  ·  Feature  ·  High                           │
│                                                                       │
│    Planning      Development        Review     Testing    Deploy      │
│    [████████] >  [████████]  >>>  [████████]  [XXXXXX]  [--------]   │
│     Done          Done             Done       FAILED     Waiting      │
│                                                                       │
│  ┌─ Error ─────────────────────────────────────────────────────────┐ │
│  │  ❌ Tester Specialist failed: E2E test suite error               │ │
│  │     3 retries attempted. Regression test timeout on login flow.  │ │
│  │     Blocked: Deployment stage cannot proceed.                    │ │
│  └──────────────────────────────────────────────────────────────────┘ │
│                                                                       │
│  [ View Details ]    [ Retry Failed Stage ]    [ Cancel Request ]     │
└──────────────────────────────────────────────────────────────────────┘
```

**Command Center -- Request Card with Quality Gate Failure (rework in progress):**

```
┌──────────────────────────────────────────────────────────────────────┐
│  ⚠ REQ-042  Login page with JWT authentication                      │
│    Started 15 min ago  ·  Feature  ·  High                           │
│                                                                       │
│    Planning      Development        Review     Testing    Deploy      │
│    [████████] >  [████░░░░]  <<<  [████████]  [--------]  [--------] │
│     Done          Rework    ◄──── Gate Failed  Waiting     Waiting    │
│                                                                       │
│  ┌─ Warning ───────────────────────────────────────────────────────┐ │
│  │  ⚠ Coverage gate failed: 72% (threshold: 80%)                   │ │
│  │    Backend Specialist is adding tests. Auto-retry in progress.   │ │
│  └──────────────────────────────────────────────────────────────────┘ │
│                                                                       │
│                                                       [ View Details ] │
└──────────────────────────────────────────────────────────────────────┘
```

**Request Detail -- Failed Agent Timeline Card:**

```
┌─ Agent Timeline ──────────────────────────────────────────────────────┐
│                                                                        │
│  12:18  ┌─── ERROR ───────────────────────────────────────────────┐   │
│    ▼    │  ❌ Tester Specialist                         FAILED    │   │
│         │                                                         │   │
│         │  E2E test suite failed after 3 attempts.                │   │
│         │                                                         │   │
│         │  Error: TimeoutError: Login flow test exceeded 30s      │   │
│         │         at tests/e2e/test_login_flow.py:42              │   │
│         │                                                         │   │
│         │  ┌── Retry History ──────────────────────────────┐      │   │
│         │  │  Attempt 1: TimeoutError (30s)     12:12      │      │   │
│         │  │  Attempt 2: TimeoutError (30s)     12:14      │      │   │
│         │  │  Attempt 3: TimeoutError (30s)     12:16      │      │   │
│         │  └───────────────────────────────────────────────┘      │   │
│         │                                                         │   │
│         │  [ View Full Logs ]  [ Retry ]                          │   │
│         └─────────────────────────────────────────────────────────┘   │
│                                                                        │
│  (Blocked: DevOps Specialist cannot deploy until testing passes)       │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

**Story Board -- Story Card Error States:**

```
NORMAL:                    QUALITY GATE FAILED:          AGENT FAILED:
┌───────────────────┐     ┌───────────────────┐         ┌───────────────────┐
│ US-042-001        │     │ ⚠ US-042-001     │         │ ❌ US-042-001     │
│ Login form        │     │ Login form        │         │ Login form        │
│                   │     │                   │         │                   │
│ 🔵 In Development │     │ ⚠ Rework          │         │ ❌ Blocked         │
│                   │     │ Coverage: 72%     │         │ Agent failure     │
│ Backend Spec.     │     │ Need: 80%         │         │ [View Error]      │
│                   │     │                   │         │                   │
│ PR #15            │     │ PR #15            │         │                   │
└───────────────────┘     └───────────────────┘         └───────────────────┘
 (default border)          (amber/orange border)         (red border)
```

### 3.5 Weekly Report Generation Flow

```
MONDAY 9:00 AM (cron trigger from config/thresholds.yaml)
        |
        v
+------------------------------------------+
| ORCHESTRATOR creates internal task:       |
| "Generate weekly report for YYYY-MM-DD"  |
| Assigns to: engineering_lead              |
+------------------+------------------------+
                   |
                   v
+------------------------------------------+
| ENGINEERING LEAD queries SQLite state:    |
|                                           |
| - All requests from past 7 days          |
| - Task completion counts by category     |
| - Quality gate pass/fail rates           |
| - Coverage metrics (current + trend)     |
| - Deployment history (success/rollback)  |
| - Demo test results                      |
| - Blocked tasks and durations            |
| - Agent utilization (tasks per agent)    |
+------------------+------------------------+
                   |
                   v
+------------------------------------------+
| ENGINEERING LEAD synthesizes report:      |
| (uses Opus 4.6 for natural language      |
|  summarization of raw metrics)           |
|                                           |
| Output format:                           |
+------------------------------------------+

## Weekly Report -- March 30 - April 5, 2026

### Summary
| Category     | Total | Done | In Progress | Blocked | Failed |
|-------------|-------|------|-------------|---------|--------|
| Development |    8  |   6  |      1      |    0    |    1   |
| Testing     |    6  |   5  |      1      |    0    |    0   |
| Deployment  |    4  |   3  |      0      |    0    |    1   |
| Demo        |    1  |   1  |      0      |    0    |    0   |

### Code Coverage
- Current: 89% (target: 80%)
- Trend: up from 86% last week
- Lowest: REQ-037 at 72% (gate failed, reworked to 84%)

### Deployments
- Successful: 3 (REQ-038, REQ-039, REQ-040)
- Rolled back: 1 (REQ-034 -- health check timeout)
- Avg deploy time: 4.2 minutes

### Demo Status
- Weekly test: PASSED (Monday 9am)
- Demo URL: http://localhost:3030

### Highlights
- Completed login feature (REQ-042) with 87% coverage
- Fixed pagination bug (REQ-041) -- 2-hour turnaround
- Dark mode toggle shipped (REQ-040)

### Blockers
- None currently active

### Agent Utilization
| Agent              | Tasks | Avg Time | Gate Passes |
|-------------------|-------|----------|-------------|
| Backend Specialist |   4   |  8 min   |    4/4      |
| Frontend Specialist|   3   |  6 min   |    3/3      |
| Tester Specialist  |   5   |  4 min   |    4/5      |
| Code Reviewer      |   7   |  3 min   |    6/7      |

### Next Week Focus
- Prioritize REQ-043 (user profile page)
- Address test timeout issue from REQ-042 E2E suite

+------------------------------------------+
| DELIVERY:                                 |
|                                           |
| 1. Saved to reports/weekly/2026-04-05.md  |
|    (committed by DevOps Specialist)       |
|                                           |
| 2. Notification N-021 created:            |
|    "Weekly report available"              |
|    Bell notification + link to report     |
|                                           |
| 3. UI: Banner card on Command Center:     |
|    "Weekly Report: Apr 5 -- 6 features    |
|     shipped, 89% coverage [View Report]"  |
|    (dismissible, persists until clicked)  |
+------------------------------------------+
```

### 3.6 Notification UI Mockup

**Navigation Bar with Notification Bell:**

```
┌──────────────────────────────────────────────────────────────────────────┐
│  ◆ Agent Team        [Command Center]   History   Releases   Team  🔔(3) [?] │
└──────────────────────────────────────────────────────────────────────────┘
                                                                  ^
                                                                  |
                                                         Bell with badge
                                                         (3) = 3 unread
```

**Notification Bell Dropdown (opened by clicking bell):**

```
                                              ┌──────────────────────────────┐
                                              │  Notifications          [✓ All] │
                                              ├──────────────────────────────┤
                                              │                              │
                                              │  ❌ 2 min ago                │
                                              │  Production deploy failed    │
                                              │  REQ-042 -- auto-rollback    │
                                              │  initiated                   │
                                              │  [View in Releases]          │
                                              │  ─────────────────────────   │
                                              │                              │
                                              │  ⚠  5 min ago               │
                                              │  Coverage gate failed: 72%   │
                                              │  REQ-042 -- reworking        │
                                              │  [View Request]              │
                                              │  ─────────────────────────   │
                                              │                              │
                                              │  ● 12 min ago               │
                                              │  PR #15 created              │
                                              │  REQ-042 backend             │
                                              │  [View on GitHub]            │
                                              │  ─────────────────────────   │
                                              │                              │
                                              │  ● 1 hour ago  (read)       │
                                              │  REQ-040 deployed to prod    │
                                              │  ─────────────────────────   │
                                              │                              │
                                              │       [View All]             │
                                              └──────────────────────────────┘

Legend:
  ❌  = CRITICAL (red icon, bold text)
  ⚠   = WARNING (amber icon)
  ●   = INFO (blue dot)
  (read items have muted/gray text)
  [✓ All] = "Mark all as read" button
```

**Toast Notification (bottom-right corner):**

```
                                    ┌─────────────────────────────────────┐
                                    │ ❌ Production Deploy Failed          │
                                    │                                     │
                                    │ REQ-042: Login page with JWT auth   │
                                    │ Auto-rollback initiated.            │
                                    │                                     │
                                    │ [View Details]           [Dismiss]  │
                                    └─────────────────────────────────────┘
                                    ▲ slides in from bottom-right
                                    ▲ auto-dismisses after 8 seconds
                                    ▲ CRITICAL toasts require manual dismiss
```

**Weekly Report Banner on Command Center:**

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  ◆ Agent Team        [Command Center]   History   Releases   Team  🔔(1) [?] │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─ Weekly Report: April 5 ──────────────────────────────────────────── ✕ ┐ │
│  │  6 features shipped · 89% coverage · 3 deployments · 1 rollback        │ │
│  │  Demo: healthy                                     [ View Full Report ] │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │  What would you like to build?  ...                                    │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│  ...                                                                         │
```

### 3.7 Implementation Architecture

New source files needed:

```
src/
  notifications/
    __init__.py
    service.py           # NotificationService -- creates, routes, stores notifications
    catalog.py           # Event catalog (N-001 through N-023) with templates
    models.py            # Notification data model
  reports/
    __init__.py
    weekly.py            # WeeklyReportGenerator -- queries state, formats report
    templates.py         # Report templates (Markdown format)
```

The `NotificationService` is initialized by the Orchestrator and passed to the Workflow Engine. The Workflow Engine calls `notification_service.notify(event_id, context)` at each relevant lifecycle point. The service looks up the event in the catalog, formats the message, stores it in SQLite, and emits the appropriate WebSocket events.

```python
class NotificationService:
    def __init__(self, state_store: StateStore, websocket_manager: WebSocketManager):
        self.state = state_store
        self.ws = websocket_manager
        self.catalog = NotificationCatalog()

    async def notify(self, event_id: str, context: dict) -> None:
        event = self.catalog.get(event_id)
        notification = Notification(
            event_id=event_id,
            severity=event.severity,
            title=event.format_title(context),
            message=event.format_message(context),
            request_id=context.get("request_id"),
            link_url=event.format_link(context),
        )
        await self.state.save_notification(notification)
        await self.ws.emit("notification.new", notification.to_dict())
        if event.severity in ("CRITICAL", "WARNING"):
            await self.ws.emit("notification.toast", notification.to_dict())
```

The weekly report generator is triggered by the Orchestrator's scheduler:

```python
class WeeklyReportGenerator:
    def __init__(self, state_store: StateStore, eng_lead: BaseAgent):
        self.state = state_store
        self.eng_lead = eng_lead

    async def generate(self) -> str:
        # Query all metrics from the past 7 days
        metrics = await self.state.get_weekly_metrics()
        # Use Engineering Lead (Opus 4.6) to synthesize natural language report
        report = await self.eng_lead.execute(Task(
            description="Generate weekly report from metrics",
            inputs={"metrics": metrics}
        ))
        return report.output  # Markdown string
```

---

## Summary of Key Architectural Decisions Across All Three Areas

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Single shared repo | vs. one repo per feature | Agents need to share code; fewer repos to manage |
| Umbrella branching | vs. flat feature branches | Allows parallel backend/frontend work with independent PRs, clean merge into main |
| Docker Compose as deploy target | vs. Railway/AWS/GCP/Vercel | Self-contained, no vendor lock-in, works offline, no cloud accounts needed. Docker images portable to any platform later. |
| Separate demo environment | vs. staging=demo | Demo needs stable seeded data; staging changes on every merge |
| Auto-rollback on health failure | vs. manual-only rollback | Production availability is paramount; 3 failed health checks trigger automatic recovery |
| In-app bell + toast for v1 | vs. email/Slack/push | Minimum viable notification; all users are in the UI; external channels add complexity |
| Fixed notification preferences | vs. configurable per-user | Single user system in v1; simplifies implementation |
| Cron-based weekly reports | vs. on-demand only | Consistent cadence aligned with demo tests; user never has to remember to request |
| Notifications stored in SQLite | vs. ephemeral only | Persistent history; user can review missed notifications; supports "View All" |

### Critical Files for Implementation

- `C:/ai-projects/claude-agent-team/docs/architecture.md` - Core architecture defining agent hierarchy, workflow DAGs, tool permissions, and state management that all three feature areas must integrate with
- `C:/ai-projects/claude-agent-team/docs/agent-invocation-design.md` - Defines the orchestrator execution model, agent tool-use loop, artifact passing, and failure handling that GitHub integration and notifications hook into
- `C:/ai-projects/claude-agent-team/docs/ui-design.md` - Defines the 5-screen UI, WebSocket events, component library, color system, and API surface that notifications/error UX must extend
- `C:/ai-projects/claude-agent-team/docs/prd-template.md` - Contains the GitHub integration requirements (GH-001 through IT-005), deployment task stages, demo requirements, and weekly report format that these designs implement
- `C:/ai-projects/claude-agent-team/docs/ui-mockups.md` - Contains the three UI design versions (Chat/Dashboard/Terminal) whose patterns the notification bell, toast, and error states must match for visual consistency