# Product Requirements Document (PRD)
# Agent Team — PRD & User Story Documentation System

---

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 3.13 |
| Created Date | 2026-04-04 |
| Last Updated | 2026-05-22 |
| Status | Draft |
| Product Owner | Chandramouli |

---

## Table of Contents

The **Added** column records the date a section first appeared in the PRD
(not the date the underlying functionality shipped). Dates without a
known revision are marked "—".

| # | Section | Added |
|---|---------|-------|
| 0 | [Recent Changes (post-v3.4)](#0-recent-changes-post-v34) | 2026-05-17 |
| 1 | [Executive Summary](#1-executive-summary) — Vision, Problem Statement, Target Users | 2026-04-04 |
| 2 | [Goals](#2-goals) | 2026-04-04 |
| 3 | [Agent Team Structure](#3-agent-team-structure) — Hierarchy, roster, team definitions, workflow DAGs, config system | 2026-04-04 |
| 4 | [Agent Responsibilities](#4-agent-responsibilities) — Per-agent detailed responsibilities (4.1 Engineering Lead → 4.8 Tester Specialist) | 2026-04-04 |
| 5 | [UI Features & Enhancements](#5-ui-features--enhancements) — Theme system, sidebar nav, screenshots, activity feed, Story Board, cost dashboard | 2026-04-06 |
| 6 | [GitHub Integration](#6-github-integration) — sub-sections below | 2026-04-04 |
| 6.1 | &nbsp;&nbsp;[Repository Setup](#61-repository-setup) | 2026-04-04 |
| 6.2 | &nbsp;&nbsp;[GitHub Actions — Automated Checks](#62-github-actions--automated-checks) | 2026-04-04 |
| 6.3 | &nbsp;&nbsp;[Issue Tracking & PR Management](#63-issue-tracking--pr-management) | 2026-04-04 |
| 6.4 | &nbsp;&nbsp;[Research Publishing Pipeline](#64-research-publishing-pipeline) | 2026-04-08 |
| 6.5 | &nbsp;&nbsp;[Web Search Integration (Firecrawl)](#65-web-search-integration-firecrawl) | 2026-04-08 |
| 6.6 | &nbsp;&nbsp;[Prompt Studio](#66-prompt-studio) | 2026-04-08 |
| 6.7 | &nbsp;&nbsp;[Project Management](#67-project-management) | **2026-05-17** |
| 6.8 | &nbsp;&nbsp;[Build Plan Decomposition (Epic → Feature → Task)](#68-build-plan-decomposition--epic--feature--task) | **2026-05-22** |
| 7 | [Task Management System](#7-task-management-system) — Categories, deployment supervisor (host process), judge LLM, rollback, cross-platform reliability, stable Compose naming | 2026-04-04 |
| 8 | [Demo Creation](#8-demo-creation) | 2026-04-04 |
| 9 | [Edge Cases & Risk Mitigation](#9-edge-cases--risk-mitigation) | 2026-04-04 |
| 10 | [Expected Output Formats](#10-expected-output-formats) — PRD, user story, weekly report templates | 2026-04-04 |
| 11 | [Constraints](#11-constraints) | 2026-04-04 |
| 12 | [Evaluation Criteria](#12-evaluation-criteria) | 2026-04-04 |
| 13 | [Sample User Stories](#13-sample-user-stories) | 2026-04-04 |
| 14 | [Appendix](#14-appendix) — Glossary, references, external links, revision history | 2026-04-04 |

---

## 0. Recent Changes (post-v3.4)

The platform has evolved meaningfully since v3.4 (2026-04-06). The following
high-impact deltas are reflected throughout the document but called out here
so a reader doesn't get blindsided by stale paragraphs in later sections:

- **Single LLM provider.** The multi-provider toggle (Anthropic direct /
  Bedrock / OpenAI / Ollama) has been removed. All 9 agents now run on
  **Claude Platform on AWS** (Anthropic-operated, AWS-authenticated) via
  the `anthropic[aws]` SDK on `claude-opus-4-7`. Setup:
  [docs/setup-claude-platform-on-aws.md](setup-claude-platform-on-aws.md).
  Any reference below to an Anthropic-vs-Bedrock toggle is historical —
  the toggle UI and the per-page provider state are gone.
- **Theme system trimmed.** The 6-theme catalog (Linear, Vercel, Discord,
  Flat, Brutalist, Y2K) is now 2 themes: **Vercel** and **Cyberpunk
  Hyperdrive** (the new default). Cyberpunk Hyperdrive adds a CSS effects
  layer (CRT scanlines, vignette, flicker, neon glow) plus a React overlay
  component (matrix-rain columns, scrolling data ticker wired to live
  agent/cost stats, radar widget, floating particles, glitch-shifted
  section headers).
- **Sidebar navigation.** Top-bar nav items moved into a left sidebar
  (`Sidebar.tsx`). The top bar now only carries logo + theme controls +
  user/role/logout. Active page is highlighted with cyan pulse-glow under
  cyberpunk.
- **Supervisor runs on the host, not in Docker.** The
  containerized supervisor (`docker-compose.supervisor.yml`) is
  deprecated. The supervisor process now runs on the developer's host
  machine (`make supervisor` / `make supervisor-bg`) so its `docker
  compose` invocations against the dev stack resolve bind-mount paths
  correctly. Three Windows-portability fixes shipped alongside: argv-form
  git checkout (cmd.exe doesn't strip single quotes), UTF-8 subprocess
  decoding (cp1252 fails on docker progress output), urllib-based
  healthchecks (curl `-o /dev/null` exits 23 on Windows).
- **Story Board fixes.** Theme-aware color tokens, tab persistence (no
  more "snap back to Story Board" every 3s on Agent Timeline / Outputs),
  cyan pipeline-completion color (was matrix-green), de-duped breadcrumb
  + header, removal of hardcoded `PR #43 — Merged` / `PR #46 — Under
  Review` / fabricated reviewer comments.
- **What did NOT change.** Agent roster (9 agents). Workflow DAGs. Story
  Board Kanban layout. Cost dashboard. Auth model. GitHub Trees-API
  publishing. Research/Content pipelines.

---

## 1. Executive Summary

### 1.1 Product Vision

Build a scalable, configuration-driven agent team organized under an Engineering hierarchy that efficiently handles PRD (Product Requirements Document) and User Story documentation, ensuring clarity for junior and specialist developers. The system enables easy tracking of completion status across development tasks, testing tasks, deployment tasks, and demo creation — all integrated with GitHub for automated workflows. The architecture supports expansion from 8 agents to 20+ through YAML configuration changes alone, with no code modifications required.

### 1.2 Problem Statement

Software development teams face recurring challenges in documentation and project tracking:

- **Documentation Gaps**: PRDs and user stories are often incomplete, inconsistent, or written at a level that junior developers struggle to follow
- **Fragmented Tracking**: Development, testing, deployment, and demo tasks are tracked in disconnected systems, making it hard to get a unified view of project progress
- **Code Quality Drift**: Without dedicated code review oversight and coverage enforcement, quality degrades over time
- **Manual Overhead**: Creating and maintaining documentation, managing GitHub workflows, and coordinating task status requires significant manual effort that agents can automate
- **Rigid Team Structure**: Hardcoded agent definitions make it difficult to scale the team, add new roles, or reorganize without rewriting code

### 1.3 Target Users

**Primary Users:**
- Junior developers who need clear, actionable user stories and PRD documentation
- Specialist developers who need precise technical requirements and acceptance criteria

**Secondary Users:**
- Product managers overseeing documentation quality
- QA engineers tracking testing tasks and coverage
- DevOps engineers managing deployment pipelines and demo environments

---

## 2. Goals

- **G1**: Produce well-structured PRD documents in Markdown format with clear sections for requirements, design, and implementation
- **G2**: Generate user story documentation that is easily understandable by junior developers, using plain language and simple diagrams
- **G3**: Track development, testing, deployment, and demo tasks with unified status reporting
- **G4**: Maintain 80% code coverage at all times through automated review and enforcement
- **G5**: Integrate seamlessly with GitHub for automated code checks, issue tracking, and PR management
- **G6**: Deploy new features on a regular schedule (weekly or bi-weekly)
- **G7**: Test the demo feature weekly to ensure continued functionality
- **G8**: Support team expansion from 8 to 20+ agents through configuration-only changes (zero code modifications)

---

## 3. Agent Team Structure

> **Architecture Reference**: For full configuration schemas, YAML examples, and component details, see [architecture.md](architecture.md). For step-by-step expansion instructions, see [expansion-playbook.md](expansion-playbook.md).

### 3.1 Overview

The agent team is a **hierarchical, configuration-driven system** consisting of 8 agents organized into 3 sub-teams under an Engineering Lead. All agents, teams, workflows, tools, and thresholds are defined in YAML configuration files (`config/`), enabling the team to scale from 8 to 20+ agents without code changes.

### 3.2 Engineering Team Hierarchy

```
                        ┌──────────────────────────────────┐
                        │       Engineering Lead            │
                        │  Decomposes work, delegates to    │
                        │  team leads, aggregates results   │
                        └──────┬──────────┬───────┬────────┘
                               │          │       │
              ┌────────────────┘          │       └──────────────────┐
              │                           │                          │
    ┌─────────▼──────────┐    ┌───────────▼──────────┐    ┌─────────▼──────────┐
    │   Planning Team     │    │   Development Team    │    │   Delivery Team     │
    │   Lead: PRD Spec.   │    │   Lead: Code Reviewer  │    │   Lead: DevOps Sp.  │
    │                     │    │                        │    │                     │
    │  - PRD Specialist   │    │  - Code Reviewer       │    │  - DevOps Spec.     │
    │  - User Story Author│    │  - Backend Specialist   │    │  - Tester Spec.     │
    └─────────────────────┘    │  - Frontend Specialist  │    └─────────────────────┘
                               └────────────────────────┘
```

### 3.3 Agent Roster (8 Agents)

| # | Agent ID | Role | Team | Reports To | Delegates To |
|---|----------|------|------|------------|-------------|
| 1 | `engineering_lead` | **Engineering Lead** | engineering | — | prd_specialist, code_reviewer, devops_specialist |
| 2 | `prd_specialist` | **PRD Specialist** (Planning Lead) | planning | engineering_lead | user_story_author |
| 3 | `user_story_author` | **User Story Author** | planning | prd_specialist | — |
| 4 | `code_reviewer` | **Code Reviewer** (Development Lead) | development | engineering_lead | backend_specialist, frontend_specialist |
| 5 | `backend_specialist` | **Backend Specialist** | development | code_reviewer | — |
| 6 | `frontend_specialist` | **Frontend Specialist** | development | code_reviewer | — |
| 7 | `devops_specialist` | **DevOps Specialist** (Delivery Lead) | delivery | engineering_lead | tester_specialist |
| 8 | `tester_specialist` | **Tester Specialist** | delivery | devops_specialist | — |

### Multi-Team Architecture

The system supports three specialized teams, each with their own workflow:

| Team | Trigger | Agents | Workflow |
|------|---------|--------|----------|
| **Engineering** | `feature_request`, `bug_report`, `doc_request`, `demo_request` | PRD Specialist, User Story Author, Backend Specialist, Frontend Specialist, Code Reviewer, Tester Specialist, DevOps Specialist (7 agents) | Full development pipeline with combined feedback loop |
| **Research** | `research_request` | Research Specialist (1 agent) | Research → Assessment Report |
| **Content** | `content_request` | Content Creator (1 agent) | Create → Content Artifact |

#### Request Routing

The orchestrator routes requests to the correct team based on `task_type`:

| Request Type | Team | Pipeline |
|-------------|------|----------|
| `feature_request` | Engineering | PRD → Stories → Dev → Review → Test → DevOps |
| `bug_report` | Engineering | Triage → Fix → Review+Test → DevOps |
| `doc_request` | Engineering | PRD → Stories |
| `research_request` | Research | Research Specialist → Report |
| `content_request` | Content | Content Creator → Artifact |

### Research Team

| ID | Requirement | Priority |
|----|-------------|----------|
| RT-001 | Research Specialist agent produces structured assessment reports on any given topic | Critical |
| RT-002 | Report includes: executive summary, key findings with confidence levels, pros/cons analysis, comparison tables, recommendation | Critical |
| RT-003 | Research output saved as document type `research_report` in the knowledge base | High |
| RT-004 | Research reports searchable via /api/v1/documents/search | High |

### Content Team

| ID | Requirement | Priority |
|----|-------------|----------|
| CT-001 | Content Creator agent produces presentations, professional documents, and technical guides | Critical |
| CT-002 | Presentations formatted as slide decks (Slide 1: Title, Visual, Speaker Notes, Key Points) | Critical |
| CT-003 | Documents formatted as structured markdown with clear headings and tables | Critical |
| CT-004 | Content output saved as document type `content_artifact` in the knowledge base | High |
| CT-005 | Content artifacts searchable via /api/v1/documents/search | High |

### 3.4 Team Definitions

| Team | Lead | Members | Domain |
|------|------|---------|--------|
| **Engineering** | Engineering Lead | All agents | All — top-level coordination |
| **Planning** | PRD Specialist | PRD Specialist, User Story Author | Requirements, documentation, user stories |
| **Development** | Code Reviewer | Code Reviewer, Backend Spec., Frontend Spec. | Backend, frontend, APIs, UI, code review |
| **Delivery** | DevOps Specialist | DevOps Specialist, Tester Specialist | Testing, CI/CD, deployment, monitoring |

### 3.5 Workflow — Feature Development (DAG-Based)

The workflow engine replaces the original linear pipeline with parallel execution and quality gates. Workflows are defined in `config/workflows.yaml`. See [architecture.md](architecture.md) Section 4 for full workflow definitions.

```
                    ┌──────────────────┐
                    │  Engineering Lead  │ ◄── Receives stakeholder request
                    │  Decomposes &      │     Delegates to team leads
                    │  delegates         │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │   Requirements    │  Planning Team
                    │   PRD Specialist  │──────► PRD Document
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │  Story Creation   │  Planning Team
                    │  User Story Author│──────► User Stories
                    └────────┬─────────┘
                             │
                ┌────────────┴────────────┐
                │      PARALLEL            │  Development Team
                ▼                          ▼
       ┌────────────────┐       ┌──────────────────┐
       │ Backend Dev     │       │ Frontend Dev      │
       │ Backend Spec.   │       │ Frontend Spec.    │
       └────────┬───────┘       └────────┬─────────┘
                │                         │
                └────────────┬────────────┘
                             │
                    ┌────────▼─────────┐
                    │   Code Review     │◄── Gate: coverage ≥ threshold
                    │   Code Reviewer   │◄── Gate: review approval
                    └────────┬─────────┘
                             │         │
                             │    on_fail: back to Development
                    ┌────────▼─────────┐
                    │    Testing        │  Delivery Team
                    │  Tester Spec.     │◄── Gate: all tests pass
                    └────────┬─────────┘     Gate: no regressions
                             │
                    ┌────────▼─────────┐
                    │   Deployment      │  Delivery Team
                    │   DevOps Spec.    │──── staging → production → verified
                    └──────────────────┘
```

**Other available workflows** (defined in `config/workflows.yaml`):

| Workflow | Trigger | Stages | Notes |
|----------|---------|--------|-------|
| `feature_development` | Feature request | Requirements → Stories → Dev (parallel) → Review → Test → Deploy | Full pipeline with parallel backend/frontend |
| `bug_fix` | Bug report | Triage → Fix → Review + Test (parallel) → Hotfix Deploy | Expedited; review and testing run in parallel |
| `documentation_update` | Doc request | Draft → Stories | No deployment needed |
| `demo_preparation` | Demo request | Prepare (parallel: env + test plan) → Validate | Environment and test plan created in parallel |

### 3.6 Configuration System Overview

All agents, teams, workflows, tools, and thresholds are defined in YAML configuration files. This enables team expansion without code changes.

| Config File | Purpose | Impact of Changes |
|---|---|---|
| `config/agents/*.yaml` | One file per agent — role, team, responsibilities, tools, delegation rules | Add/modify/remove agents |
| `config/teams.yaml` | Team compositions, hierarchy, leads, domain tags | Add/reorganize teams |
| `config/workflows.yaml` | DAG-based workflow pipelines with parallel stages and quality gates | Change how work flows through the team |
| `config/tools.yaml` | Tool registry and role-based permissions | Control what tools each agent can use |
| `config/thresholds.yaml` | Configurable values (coverage %, SLAs, deployment frequency) | Tune all operational thresholds from one file |

> **Full schema details**: See [architecture.md](architecture.md) Sections 3.2–3.6.
> **How to expand**: See [expansion-playbook.md](expansion-playbook.md).

### 3.7 Future Growth Path

The system supports these expansions through YAML-only changes:

| Future Agent | Team Placement | Config Changes Required |
|---|---|---|
| Database Specialist | Development | 1 new agent YAML + update team + lead delegation |
| Security Lead + Engineer | New: Security Team | 2 new agent YAMLs + new team + Eng. Lead delegation |
| UX Designer | New: Design Team | 1 new agent YAML + new team + Eng. Lead delegation |
| Performance Engineer | Delivery | 1 new agent YAML + update team + lead delegation |
| Technical Writer | Planning | 1 new agent YAML + update team + lead delegation |
| Data Engineer + ML Engineer | New: Data Team | 2 new agent YAMLs + new team + Eng. Lead delegation |

---

## 4. Agent Responsibilities

> Each agent's responsibilities are defined in its YAML config file (`config/agents/{agent_id}.yaml`). The tables below document the current responsibilities for each agent.

### 4.1 Engineering Lead — Detailed Responsibilities

| ID | Responsibility | Description |
|----|---------------|-------------|
| EL-001 | Task Decomposition | Analyze incoming work requests and break them into subtasks for team leads |
| EL-002 | Delegation | Route subtasks to the appropriate team lead based on domain and priority |
| EL-003 | Result Aggregation | Collect and synthesize results from all team leads into a unified response |
| EL-004 | Quality Oversight | Ensure all quality gates pass before marking work as complete |
| EL-005 | Cross-Team Coordination | Resolve dependencies and blockers between Planning, Development, and Delivery teams |
| EL-006 | Escalation Handling | Handle tasks that don't fit cleanly into one team's domain |

### 4.2 PRD Specialist — Detailed Responsibilities

| ID | Responsibility | Description |
|----|---------------|-------------|
| PRD-001 | Requirement Gathering | Collect and organize requirements from stakeholder inputs |
| PRD-002 | Document Structuring | Write PRDs following the standard template (see Section 10.1) |
| PRD-003 | Requirement Traceability | Link each requirement to its user story and acceptance criteria |
| PRD-004 | Version Management | Maintain document versions and track changes |
| PRD-005 | Completeness Review | Ensure no requirement gaps exist before handoff to development |

### 4.3 User Story Author — Detailed Responsibilities

| ID | Responsibility | Description |
|----|---------------|-------------|
| US-001 | Story Creation | Write user stories in "As a [role], I want [action], so that [benefit]" format |
| US-002 | Acceptance Criteria | Define clear, testable acceptance criteria for each story |
| US-003 | Junior-Friendly Writing | Use plain language; avoid jargon; include simple diagrams where helpful |
| US-004 | Stakeholder Collaboration | Validate stories with stakeholders before marking as ready |
| US-005 | Story Prioritization | Assign priority (Critical / High / Medium / Low) based on business value |

### 4.4 Code Reviewer — Detailed Responsibilities

| ID | Responsibility | Description |
|----|---------------|-------------|
| CR-001 | PR Review | Review all pull requests for correctness, readability, and maintainability |
| CR-002 | Coverage Enforcement | Ensure code coverage stays at or above 80% on every PR |
| CR-003 | Constructive Feedback | Provide actionable, specific feedback — no vague "looks wrong" comments |
| CR-004 | Standards Compliance | Verify adherence to linting rules, formatting, and project conventions |
| CR-005 | Knowledge Sharing | Include explanations in reviews that help junior developers learn |
| CR-006 | Combined Quality Gate | Participate in combined feedback loop with Tester. On re-review: verify all previous findings are FIXED. Only APPROVE when zero critical issues remain. |
| CR-007 | Compilation Gate | FIRST verify every file compiles before reviewing quality. Truncated files, missing imports, syntax errors = automatic CHANGES REQUESTED. Non-negotiable. |

### 4.5 Backend Specialist — Detailed Responsibilities

| ID | Responsibility | Description |
|----|---------------|-------------|
| BE-001 | API Development | Design and implement RESTful or GraphQL APIs based on user stories |
| BE-002 | Database Design | Create and maintain database schemas, migrations, and seed data |
| BE-003 | Business Logic | Implement server-side business logic, validation, and data processing |
| BE-004 | Backend Testing | Write unit and integration tests for all backend code (coverage ≥ 80%) |
| BE-005 | API Documentation | Document API endpoints, request/response formats, and error codes |
| BE-006 | Performance | Optimize queries, caching, and server-side performance |

### 4.6 Frontend Specialist — Detailed Responsibilities

| ID | Responsibility | Description |
|----|---------------|-------------|
| FE-001 | UI Development | Build user interface components and pages from user stories and designs |
| FE-002 | Client-Side Logic | Implement state management, routing, and client-side data handling |
| FE-003 | Responsive Design | Ensure UI works across desktop, tablet, and mobile viewports |
| FE-004 | Frontend Testing | Write unit and component tests for all frontend code (coverage ≥ 80%) |
| FE-005 | Accessibility | Follow WCAG guidelines; ensure keyboard navigation and screen reader support |
| FE-006 | API Integration | Connect frontend components to backend APIs; handle loading/error states |

### 4.7 DevOps Specialist — Detailed Responsibilities

| ID | Responsibility | Description |
|----|---------------|-------------|
| DO-001 | CI/CD Pipelines | Create and maintain GitHub Actions workflows for build, test, and deploy |
| DO-002 | Deployment | Manage staging and production deployments; implement rollback procedures |
| DO-003 | Infrastructure | Configure servers, containers, cloud resources, and networking |
| DO-004 | Monitoring | Set up logging, alerting, and health check dashboards |
| DO-005 | Security Hardening | Configure secrets management, access controls, and dependency scanning |
| DO-006 | Demo Environment | Maintain a dedicated demo environment with automated data seeding |

### 4.8 Tester Specialist — Detailed Responsibilities

| ID | Responsibility | Description |
|----|---------------|-------------|
| TS-001 | Test Strategy | Design test plans covering unit, integration, E2E, and regression testing |
| TS-002 | Automated Tests | Write and maintain automated test suites for all test levels |
| TS-003 | E2E Testing | Create end-to-end tests that validate complete user workflows |
| TS-004 | Regression Testing | Run regression suites before each deployment to catch regressions |
| TS-005 | Test Reporting | Generate test reports with pass/fail counts, coverage metrics, and trends |
| TS-006 | Demo Testing | Execute weekly demo tests and report results (see Section 8.2) |

---

## 5. UI Features & Enhancements

### Light/Dark Theme Toggle

| ID | Requirement | Priority |
|----|-------------|----------|
| UI-001 | Sun/moon toggle icon in navbar switches between light and dark mode for the current theme | Critical |
| UI-002 | All 2 themes (Vercel, Cyberpunk Hyperdrive) have both light and dark color palettes | Critical |
| UI-003 | Mode (light/dark) persists to localStorage independently of theme selection | Critical |
| UI-004 | Theme selection persists to localStorage independently of mode | High |
| UI-005 | CSS selectors use [data-theme="X"][data-mode="Y"] for 4 palette combinations | High |

### Theme System

| ID | Requirement | Priority |
|----|-------------|----------|
| UI-006 | 2 selectable themes (Vercel, Cyberpunk Hyperdrive) available via dropdown in navbar; Cyberpunk Hyperdrive is the default | High |
| UI-007 | Each theme defines CSS custom properties (--bg-primary, --text-primary, --accent, etc.) | High |
| UI-008 | All UI components use var(--xxx) for colors, not hardcoded values | High |
| UI-008a | Cyberpunk Hyperdrive ships a CSS effects layer (CRT scanlines, vignette, flicker, neon glow on headers/buttons/links) scoped to `[data-theme="cyberpunk-hyperdrive"]` so other themes are untouched | High |
| UI-008b | A React `<CyberpunkOverlay>` (only mounted when the cyberpunk theme is active) renders matrix-rain columns, a scrolling data ticker wired to live API stats (agents, requests, cost), a radar widget, and floating particles | High |

### Sidebar Navigation

| ID | Requirement | Priority |
|----|-------------|----------|
| UI-008c | Primary navigation lives in a left sidebar (Command Center, Prompt Studio, Diagrams, History, Releases, Team, Cost, Users-admin-only). Top bar only carries logo + theme controls + user/role/logout | High |
| UI-008d | Active sidebar item visually distinguished (subtle bg tint + accent text); under cyberpunk theme this becomes a continuous pulse-glow | High |

### Inline Screenshot Attachments

| ID | Requirement | Priority |
|----|-------------|----------|
| UI-009 | Users can paste screenshots (Ctrl+V) directly into the request description text box | High |
| UI-010 | Users can drag-and-drop image files into the description | High |
| UI-011 | Users can click "Attach image" button to browse and select files | High |
| UI-012 | Attached images display inline with text in the editor | High |
| UI-013 | Files uploaded as multipart/form-data, stored on server, served via /api/v1/requests/attachments/ | High |

### Live Activity Feed

| ID | Requirement | Priority |
|----|-------------|----------|
| UI-014 | Command Center shows real-time agent activity via WebSocket connection | High |
| UI-015 | Activity feed shows agent name, status, progress messages, timestamps | Medium |
| UI-016 | Request Detail page auto-polls every 3 seconds while request is in progress | Medium |

### Agent Output Visibility

| ID | Requirement | Priority |
|----|-------------|----------|
| UI-017 | Request Detail page shows expandable agent pipeline with numbered steps | Critical |
| UI-018 | Each agent's full output rendered as formatted markdown (headings, tables, code blocks) | Critical |
| UI-019 | Expand All / Collapse All buttons for batch viewing | Medium |
| UI-020 | Agent outputs deduplicated — shows best version when rework creates duplicates | Medium |

### Story Board Redesign (Mockup: story-board-view.html)

| ID | Requirement | Priority |
|----|-------------|----------|
| SB-001 | Pipeline overview bar with dot indicators per stage (PRD, Stories, Dev, Review, Testing, Done) showing story counts, connectors, and animation for active stages | High |
| SB-002 | Aggregate stats row: total stories, tests passing/total, average coverage %, PR count | High |
| SB-003 | Tab bar: Story Board / Agent Timeline / Outputs / Test Report — switching views without navigation | High |
| SB-004 | Story cards with color-coded agent badges: green=backend, pink=frontend, yellow=tester, blue=reviewer, with pulsing dot for active agents | Medium |
| SB-005 | Test cases displayed per story card with pass ✓ / fail ✗ / running ○ / pending ○ icons, linked via "Traces To: US-XXX" from Tester output | Critical |
| SB-006 | Coverage bar per story card: green ≥80%, yellow 60-79%, red <60%, extracted from Tester output | Medium |
| SB-007 | PR badge per story card showing PR number and status (Open / Under Review / Merged) | Medium |
| SB-008 | Acceptance criteria checkboxes per story card, parsed from User Story Author's Given/When/Then output | High |
| SB-009 | Reviewer comment inline on story cards in Review column | Low |
| SB-010 | Card styling: left border accent per column color, hover shadow lift, active card blue left border | Medium |
| SB-011 | Breadcrumb navigation: Command Center > REQ-XXX > Story Board | Low |

### Data Requirements for Story Board

| ID | Requirement | Priority |
|----|-------------|----------|
| SD-001 | Parse acceptance criteria from User Story Author output and store per story in database | High |
| SD-002 | Parse test cases from Tester output, link to story IDs via "Traces To: US-XXX AC-X", store in test_cases table | Critical |
| SD-003 | Extract coverage percentage per story from Tester output, update stories.coverage_pct | Medium |
| SD-004 | Pipeline stage counts computed from story statuses (count per column) | Medium |

### Markdown Rendering

| ID | Requirement | Priority |
|----|-------------|----------|
| UI-021 | Custom markdown renderer handles: headings, bold, tables, code blocks, lists, checkboxes, blockquotes | High |
| UI-022 | Code blocks display with monospace font, themed background, and border | Medium |
| UI-023 | Tables render with proper headers, borders, and themed styling | Medium |

### Cost Dashboard

| ID | Requirement | Priority |
|----|-------------|----------|
| UI-024 | Cost page shows today's spend, monthly spend, all-time spend, total API calls | High |
| UI-025 | Breakdown by model (Opus vs Sonnet) with token counts and cost | High |
| UI-026 | Breakdown by agent with call count, tokens, and cost | High |
| UI-027 | Top 10 most expensive requests listed | Medium |

---

## 6. GitHub Integration

### 6.1 Repository Setup

| ID | Requirement | Priority |
|----|-------------|----------|
| GH-001 | Create a new GitHub repository for the project with a standardized structure | High |
| GH-002 | Configure branch protection rules on `main` (require PR review, passing checks) | High |
| GH-003 | Set up issue templates for bugs, features, and tasks | Medium |
| GH-004 | Configure PR templates with checklist (tests, docs, coverage) | Medium |

### 6.2 GitHub Actions — Automated Checks

| ID | Requirement | Priority |
|----|-------------|----------|
| GA-001 | Implement linting checks on every PR (e.g., ESLint, Flake8, or equivalent) | High |
| GA-002 | Implement formatting checks on every PR (e.g., Prettier, Black, or equivalent) | High |
| GA-003 | Run automated test suite on every PR | High |
| GA-004 | Enforce 80% code coverage threshold — block merge if below | High |
| GA-005 | Run security scanning (e.g., dependency audit) on PRs | Medium |
| GA-006 | Generate and publish coverage reports as PR comments | Medium |

### 6.3 Issue Tracking & PR Management

| ID | Requirement | Priority |
|----|-------------|----------|
| IT-001 | Map each user story to a GitHub issue | High |
| IT-002 | Use GitHub labels to categorize: `dev-task`, `test-task`, `deploy-task`, `demo-task` | High |
| IT-003 | Link PRs to issues for automatic status tracking | High |
| IT-004 | Use GitHub milestones for sprint/release tracking | Medium |
| IT-005 | Configure assignee and reviewer auto-assignment rules | Low |

### 6.4 Research Publishing Pipeline

When a user submits a `research_request`, the system runs a 3-stage workflow that produces structured artifacts and publishes them to both the local filesystem and the GitHub repository.

**Workflow stages:**

1. **Research** — `research_specialist` produces a structured research report (markdown)
2. **Generate** — `content_creator` receives the research report as input and produces additional artifacts:
   - `report.md` — polished, publication-ready version of the research
   - `summary.md` — one-page executive summary (≤ 400 words)
   - `slides.md` — 8-12 slide deck source (markdown, `---` separated)
   - `architecture.mmd` — Mermaid diagram (only if applicable to the topic)
3. **Publish** — system stage (`ResearchPublisher`) renders binary artifacts and commits everything to GitHub:
   - Renders `report.md` → `report.pdf` via WeasyPrint
   - Renders `slides.md` → `slides.pptx` via python-pptx
   - Writes all files to `docs/research/REQ-<id>-<slug>/`
   - Commits atomically to GitHub via the Trees API in a single commit

**Storage convention:**

```
docs/research/
  REQ-A3F2C1-vector-databases/
    research-report.md     ← raw output from Research Specialist
    report.md              ← polished markdown
    report.pdf             ← rendered PDF
    summary.md             ← executive summary
    slides.md              ← slide deck source
    slides.pptx            ← rendered PowerPoint
    architecture.mmd       ← optional Mermaid diagram source
```

**GitHub publishing:**

| ID | Requirement | Priority |
|----|-------------|----------|
| RPP-001 | Use GitHub Trees API for atomic multi-file commits (no git CLI dependency in container) | High |
| RPP-002 | Authenticate via `GITHUB_TOKEN` PAT with `contents:write` scope | High |
| RPP-003 | Soft-fail on publish errors — request still completes successfully if research itself succeeded | High |
| RPP-004 | Render slides as `.pptx` (not just markdown) so business users can open in PowerPoint | High |
| RPP-005 | Render report as `.pdf` so it can be shared without requiring a markdown viewer | High |
| RPP-006 | Folder naming: `REQ-<id>-<slug>` where slug is derived from the request title | Medium |
| RPP-007 | Include source files (`.md`, `.mmd`) alongside rendered files for editability | Medium |
| RPP-008 | Emit `research_publish.completed` event with commit SHA for UI display | Medium |

**Future enhancements (not in current scope):**

| ID | Enhancement | Priority |
|----|-------------|----------|
| FE-1 | "Published Artifacts" tab on the Story Board for research requests, showing clickable links to each file in the GitHub repo | High |
| ~~FE-2~~ | ~~Refactor `CodeWriter._git_commit_and_push()` to use the same GitHub Trees API approach~~ — **DONE.** `src/core/github_publisher.py` is now a shared module used by both `ResearchPublisher` and `CodeWriter`. The git CLI dependency is gone. | ~~High~~ |
| FE-3 | Versioning — if the same research topic is re-submitted, create `REQ-XXX-<slug>-v2/` instead of overwriting | Medium |
| FE-4 | Bidirectional sync — if someone edits the report on GitHub, surface the updated version in the UI | Low |
| FE-5 | Real Mermaid PNG rendering via `mermaid-cli` (requires Node.js sidecar) | Low |
| FE-6 | Additional output formats: DOCX (via `python-docx`), Confluence page (via Atlassian API) | Low |

### 6.5 Web Search Integration (Firecrawl)

**Problem:** All Claude models have a knowledge cutoff (early 2025 for Sonnet 4). Without external data access, agents produce stale answers — especially research, where the user needs current market data, pricing, model versions, and trends.

**Solution:** Integrate [Firecrawl](https://firecrawl.dev) as a web search + scraping service exposed to all agents as **two tools** they can call during their tool-use loop. The agent decides when to search based on the prompt. No tool wrapping work in the agent loop — the existing `BaseAgent._call_llm()` already handles Anthropic's tool-use protocol.

**Why Firecrawl** (not Tavily, Brave, Serper, etc.):
- Built for LLMs — returns clean markdown directly, no HTML parsing needed
- Combines search + scrape in one API (Tavily-style) **plus** a separate scrape mode for known URLs
- Handles JavaScript-rendered content (modern SPAs work)
- Open-source core (self-hostable if needed)
- Predictable credit-based pricing

**Provider compatibility**: tool use is part of Anthropic's Messages API, which the single supported provider (**Claude Platform on AWS**) implements natively. Firecrawl tool calls flow through the same `BaseAgent._call_llm()` loop with no provider-specific branching.

**Two tools exposed to agents:**

| Tool | Calls | When the agent uses it | Returns |
|------|-------|------------------------|---------|
| `web_search` | Firecrawl `/search` with `scrapeOptions={formats:["markdown"]}` | "Find me articles about X" — discovery mode | List of `{url, title, markdown}` for top N results, each truncated to ~3000 chars |
| `web_scrape` | Firecrawl `/scrape` with `formats=["markdown"]`, `onlyMainContent=true` | "Read this specific URL deeply" — when the agent already has a URL | Single `{url, title, markdown}` with full content |

**Truncation strategy**: search results are capped at ~3000 chars per item. The agent can call `web_scrape` on any URL it wants to read in full. This keeps token usage sane while preserving the agent's ability to drill down.

**No artificial call cap.** The natural ceiling comes from `BaseAgent.max_iterations = 5` — each agent run can issue at most a handful of tool batches. Every Firecrawl call is logged with `request_id`, `agent_id`, query/url, and result size for observability. A hard cap can be added later if logs show runaway behavior.

**Soft-fail behavior**: if Firecrawl is unreachable or returns an error, the tool returns the error string in the tool result. The agent decides whether to retry, fall back to training-data answers, or summarize what it has.

**Key requirements:**

| ID | Requirement | Priority |
|----|-------------|----------|
| WST-001 | All 9 agents have `web_search` and `web_scrape` available as tools | High |
| WST-002 | Research Specialist system prompt MUST mandate `web_search` for any time-sensitive topic (market data, model versions, pricing, news) | High |
| WST-003 | Tools work identically across all 9 agents under the single Claude Platform on AWS provider | High |
| WST-004 | `FIRECRAWL_API_KEY` configured via `.env` (no UI configuration) | High |
| WST-005 | Soft-fail on Firecrawl errors — agent run continues, error visible in tool result | High |
| WST-006 | Search results truncated to ~3000 chars per item to control token usage | Medium |
| WST-007 | Every Firecrawl call logged with request_id + agent_id + query + result size | Medium |
| WST-008 | Existing "knowledge cutoff disclaimer" in `research_specialist.yaml` removed/replaced — no longer accurate once web search is live | Medium |

**Future enhancements (deferred):**

| ID | Enhancement | Priority |
|----|-------------|----------|
| FE-9 | `web_crawl` tool — recursively crawl an entire site (Firecrawl `/crawl`). Useful for "scrape this competitor's docs" | Medium |
| FE-10 | `web_extract` tool — schema-based structured extraction (Firecrawl `/extract`). Useful for building comparison tables | Medium |
| FE-11 | Per-request cost tracking — count Firecrawl credits used per request and surface in cost dashboard | Medium |
| FE-12 | Search result caching — if two agents in the same workflow search for the same query, hit a cache | Low |
| FE-13 | Frontend display of "sources used" — show the URLs the agent fetched in the Outputs tab of Story Board | High |
| FE-14 | Hard call cap via env var — add an emergency brake if logs show runaway behavior | Low |

### 6.6 Prompt Studio

**Problem:** Users know what they want the AI to do but write loose, unstructured prompts that don't follow prompt engineering best practices. Results are inconsistent, verbose, and miss key techniques like role definition, structured output, and chain-of-thought hints.

**Solution:** A dedicated page where users enter their requirements via structured fields and receive 3 professionally-engineered prompt variants — each using a different approach — that they can copy, select, and iteratively refine.

**This feature is NOT part of the agent pipeline.** It's a stateless one-shot LLM call (using the shared Claude Platform on AWS client — no per-page provider toggle anymore) that returns 1 variant in a single API call. It does not touch the orchestrator, workflow runner, story board, or research publisher.

**Key workflow:**

1. User navigates to `Prompt Studio` from the top nav
2. Optionally loads a starting template (Code Reviewer, Research Analyst, Marketing Copywriter, SQL Explainer, Customer Support Agent, Technical Writer)
3. Fills in structured inputs: use case, target audience, desired output, tone, constraints
4. Optionally configures advanced options: target LLM, output format, few-shot, chain-of-thought, length, use case category
5. Clicks **Generate 3 Variants** → backend calls Claude with a carefully-crafted meta-prompt, parses the JSON response, returns 3 variants
6. Each variant displayed side-by-side with: approach label, copyable prompt text, "Techniques applied" collapsible, Copy button, Select button
7. User picks a variant → optionally enters refinement feedback ("make it more concise", "add JSON schema output") and clicks **Refine** → new 3 variants generated
8. All sessions and variants persisted in the DB; user can revisit via the **History** tab

**Meta-prompt design:**

The backend's meta-prompt instructs Claude to produce 3 DISTINCT variants using different approaches:
- **Variant 1 (Structured XML):** heavy `<context>`, `<task>`, `<output_format>` tags — best for Claude
- **Variant 2 (Conversational Markdown):** natural prose with headers — best for GPT
- **Variant 3 (Concise Imperative):** terse, action-oriented, minimal overhead

Each variant explicitly applies: role definition, task statement, output format, constraints, structured delimiters. Few-shot examples and chain-of-thought hints are added only when the user enables those options.

Claude returns a strict JSON object `{"variants": [{"approach", "prompt", "techniques": [...]}, ...]}` that the backend parses into `PromptVariant` rows.

**Data model:**

```sql
prompt_sessions: session_id, user_id, created_at, use_case, target_audience,
                 desired_output, tone, constraints, options (JSON), provider,
                 template_id, selected_variant_id

prompt_variants: variant_id, session_id, iteration, variant_index, approach,
                 prompt_text, techniques (JSON array), feedback_applied,
                 generated_at
```

- `iteration = 0` → initial generation, `iteration >= 1` → refinements
- `variant_index` ∈ {1, 2, 3} within each iteration
- `selected_variant_id` records which variant the user picked (drives refinement context)

**Key requirements:**

| ID | Requirement | Priority |
|----|-------------|----------|
| PS-001 | Structured input: use case (required), audience, desired output, tone, constraints | High |
| PS-002 | Advanced options collapsible: target LLM, output format, few-shot, CoT, length, category | High |
| PS-003 | Single provider (Claude Platform on AWS) — no per-page toggle (was Claude/Bedrock; removed when the provider consolidated) | High |
| PS-004 | Single-variant generation in ONE LLM call, returned as parsed JSON (was "3 variants" — narrowed to 1 to reduce token spend) | High |
| PS-005 | Each variant has: approach label, prompt text, techniques applied, copy button, select button | High |
| PS-006 | Starting templates loaded from `config/prompt_templates.yaml` (6 defaults) | High |
| PS-007 | Iterative refinement: feedback on selected variant generates 3 new variants in a new iteration | High |
| PS-008 | History tab: list past sessions (paginated, 20/page), click to reload | High |
| PS-009 | All logged-in users can access (no role restriction) | High |
| PS-010 | Techniques applied shown as collapsible section under each variant | Medium |

**Future enhancements (deferred):**

| ID | Enhancement | Priority |
|----|-------------|----------|
| FE-15 | User-defined templates saved to DB | Medium |
| FE-16 | Share prompt via public URL (read-only snapshot) | Low |
| FE-17 | Side-by-side variant comparison with diff highlighting | Medium |
| FE-18 | Token count estimation for each variant before copying | Medium |
| FE-19 | "Try this prompt" button that sends it to a test chat interface | Medium |
| FE-20 | Public prompt library — browse high-quality community prompts | Low |
| FE-21 | Export as JSON/YAML for programmatic use in other tools | Low |

#### 6.6.1 Execute Tab — Prompt Playground

**Problem:** After generating a prompt in the Generator tab, users want to actually run it against an LLM to see what it produces — without leaving the app, copy-pasting into a separate playground, or building their own chat client.

**Solution:** A new "Execute" tab inside Prompt Studio that turns the page into a multi-turn chat playground. The user pastes (or auto-fills from a Generator variant) a system prompt, then chats with the LLM in a conversation thread. Responses stream in token-by-token. Optional Firecrawl web tools can be enabled per session.

**Workflow:**

1. User clicks the "Execute" tab (between Generator and History)
2. Top section: a single textarea for the **System Prompt**, auto-filled from a Generator variant if the user clicked "Try in Execute" there
3. Below: a chat conversation pane (initially empty), then a chat input + Send button
4. User types a message → click Send (or Enter)
5. Backend opens a Server-Sent Events stream, calls Claude Platform on AWS with `messages.stream(...)`, and yields tokens as they arrive
6. Frontend appends the assistant's response token-by-token into a new chat bubble
7. If web tools are enabled and the model decides to call `web_search` or `web_scrape`, the backend executes the tool, sends a tool-call event to the frontend (rendered as a collapsible card in the chat), and continues the model's response from where it left off
8. After the assistant turn completes, the chat input re-enables. User can send a follow-up — full conversation history is sent on each turn for multi-turn context
9. "Clear conversation" button resets without page reload

**Key technical details:**

- **Streaming:** uses `anthropic[aws]` SDK's `client.messages.stream()` against Claude Platform on AWS. Backend forwards events as SSE; frontend reads via `fetch` + `ReadableStream`.
- **Tool-use loop:** when tools are enabled and the model emits `tool_use` blocks, the backend pauses the stream, executes each tool, appends the result as a `tool_result` content block in the user role, and starts a new stream. Loops up to `MAX_ITERATIONS = 5` per user turn before giving up.
- **Multi-turn:** the frontend keeps the full conversation in component state and sends the entire history on every turn. State is lost on tab change — no persistence (stateless playground).
- **Tools available:** `web_search` and `web_scrape` (Firecrawl) only — opt-in via checkbox in Advanced Options. Other agent tools (file_read, code_exec, github_api, etc.) are deliberately NOT exposed because they belong in the agent pipeline, not a playground.
- **Auto-prepended tool hint:** when the tool checkbox is enabled, the backend prepends a one-line instruction to the system prompt: *"You have web_search and web_scrape tools available — use them when you need current information you don't have in your training."* This nudges the model to actually use the tools when appropriate.
- **Cost + token tracking:** displayed per-turn and as a running total for the conversation.
- **Provider:** Claude Platform on AWS (single provider — the per-page toggle was removed when the platform consolidated).

**Key requirements:**

| ID | Requirement | Priority |
|----|-------------|----------|
| PSE-001 | New "Execute" tab in Prompt Studio between Generator and History | High |
| PSE-002 | System Prompt textarea (large, primary) — auto-fillable from Generator variants | High |
| PSE-003 | Multi-turn chat conversation panel — user/assistant bubbles in chronological order | High |
| PSE-004 | Server-Sent Events streaming — tokens appear live, not after the full response | High |
| PSE-005 | Optional `web_search` + `web_scrape` tools via "Enable web tools" checkbox | High |
| PSE-006 | Tool-use loop wraps streaming — model can call tools mid-conversation | High |
| PSE-007 | "Try in Execute" button on each Generator variant card → switches tab + pre-fills system prompt | High |
| PSE-008 | Token counts (input/output), latency, cost shown per turn + cumulative | High |
| PSE-009 | "Clear conversation" button to reset state without page reload | Medium |
| PSE-010 | Tool calls displayed inline as collapsible cards (tool name, input args, result preview) | Medium |
| PSE-011 | Markdown rendering for assistant responses (reuse existing MarkdownRenderer) | Medium |
| PSE-012 | Conversation state lost on tab change (stateless v1) | Low |

**Future enhancements (deferred):**

| ID | Enhancement | Priority |
|----|-------------|----------|
| FE-22 | Persist execution sessions in DB (linked to source prompt session) | Medium |
| FE-23 | "Save as preset" — bookmark a (system prompt + first message) combo for reuse | Medium |
| FE-24 | Download conversation as `.md` or `.json` | Low |
| FE-25 | ~~Side-by-side execute mode — run the same conversation on Claude AND Bedrock to compare~~ — DROPPED (single-provider platform; no second provider to compare against) | Low |
| FE-26 | Additional tool toggles (file_read for project context, code_exec sandbox) | Low |
| FE-27 | Model parameter A/B test — run the same prompt 2-3 times with different temperatures and compare side by side | Low |

---

### 6.7 Project Management

**Problem:** The platform tracks every agent request in a single flat
chronological list. Once you accumulate 30+ requests it's hard to
separate "Themes work" from "Supervisor hardening" from one-off bug
fixes. There's no project-level rollup of cost, status, or outputs.

**Solution:** A first-class **Project** concept. Users explicitly
create projects, then every new request is filed into exactly one
project at submit time via a required dropdown. Project pages aggregate
every request in the project (cost, status, recent documents). All
existing places that surface requests (Command Center cards, History
list, RequestDetail header, StoryBoard breadcrumb) gain a clickable
project reference so the project association is visible everywhere.

This is **explicit assignment, not auto-match.** A previous prototype
auto-assigned requests via keyword similarity and was reverted — users
couldn't see why a request landed where it did, and the only visible
surface was a sidebar entry. This version puts the project in the
user's face at every relevant decision point.

Detailed design and rationale: [docs/prd-projects-feature.md](prd-projects-feature.md).

#### Project CRUD

Projects are rich metadata containers — the Create Project form collects
the full v1 field set in a single step (no progressive disclosure).

| ID | Requirement | Priority |
|----|-------------|----------|
| PRJ-001 | Project model: `project_id`, `name` (req, ≤80 chars, unique among active), `description` (≤500 chars), `status` (active \| archived), **`color`** (preset hex from 8-swatch palette), **`icon`** (lucide icon name from preset 8-icon set), **`tags`** (JSON list, ≤10 entries, each ≤25 chars), **`lead_user_id`** (FK users, defaults to creator), **`repo_url`** (optional URL, ≤300 chars), **`default_team`** (engineering \| research \| content \| null), **`target_date`** (optional ISO date), **`template_id`** (optional, refs preset template), `created_by`, timestamps | Critical |
| PRJ-002 | `POST /api/v1/projects` — any authenticated user can create. Accepts the full PRJ-001 field set; only `name` is required, everything else has a default (lead=caller, color=cyan, icon=folder, etc.) | Critical |
| PRJ-003 | `GET /api/v1/projects` lists all (default active-only; `?include_archived=true` to opt in) | Critical |
| PRJ-004 | `GET /api/v1/projects/{id}` returns project + request list + aggregate stats + recent documents + the template's starter checklist (if set) for rendering "Next Steps" guidance | Critical |
| PRJ-005 | `PATCH /api/v1/projects/{id}` — any user can edit name/desc/color/icon/tags/lead/repo_url/default_team/target_date; **admin-only** to flip status to/from archived OR to reassign `lead_user_id` to someone else | High |
| PRJ-006 | `DELETE /api/v1/projects/{id}` — **admin-only**, rejected with 409 if non-empty | High |
| PRJ-007 | Active-name uniqueness enforced case-insensitively at application layer; archived projects can share a name with new active ones | Medium |
| PRJ-008 | System seeds an immutable "Unassigned" project on first boot — catches legacy/orphaned requests | Critical |
| PRJ-009 | **Color** picked from 8 preset swatches (cyan / pink / green / yellow / orange / purple / blue / gray) — no free hex | Medium |
| PRJ-010 | **Icon** picked from 8 preset lucide icons (folder / rocket / layers / code / flask-conical / palette / bug / book-open) | Medium |
| PRJ-011 | **Tags** auto-lowercased, deduped within a project, enforced ≤10 entries / ≤25 chars each | Medium |
| PRJ-012 | **Lead user** dropdown shows developer + admin users (viewers excluded); defaults to creator; searchable for >10 users | Medium |
| PRJ-013 | **Repo URL** validated as well-formed URL; GitHub URLs surface a "View on GitHub" button on the project page | Medium |
| PRJ-014 | **Target date** optional, must be ≥ today on create; renders red "Overdue" pill once past, but no auto-archive | Medium |
| PRJ-015 | **Default team** pre-selects the team selector on the New Request form when the user files into this project | Medium |
| PRJ-016 | **Templates** loaded from `config/project_templates.yaml` at startup; v1 ships 5: `empty`, `web_feature`, `research_initiative`, `content_project`, `bug_sprint` | Medium |
| PRJ-017 | When a template is picked at create-time, its `starter_checklist` renders as a "Next Steps" panel on the project detail page — clickable items pre-fill the New Request form; checked off once a matching request is filed | Medium |

#### Request → Project Assignment

| ID | Requirement | Priority |
|----|-------------|----------|
| PA-001 | `requests` table gains `project_id` (FK to projects); defaults to Unassigned | Critical |
| PA-002 | New Request form: Project dropdown is REQUIRED; defaults to user's most-recently-used active project (localStorage); active projects sorted alphabetically (rendered with color swatch + icon + name); "+ New project..." inline option at the bottom. When a project with `default_team` is selected, the Team selector pre-fills accordingly | Critical |
| PA-003 | Inline "+ New project" opens the **full** Create Project modal — every PRJ-001 field (name, description, color, icon, tags, lead, repo URL, default team, target date, template). Saves, selects the new project, closes — no page reload | High |
| PA-004 | `POST /api/v1/requests` validates `project_id` exists + is not archived; rejects with 400 otherwise | Critical |
| PA-005 | RequestDetail page header shows "Project: \<name\>" linking to `/projects/{id}` | Critical |
| PA-006 | StoryBoard breadcrumb: `Command Center ▸ \<project name\> ▸ REQ-XXX` | High |
| PA-007 | History page gains a "Project" column + filter dropdown | High |
| PA-008 | Command Center request cards show a clickable project chip beside the REQ-id | High |
| PA-009 | `PATCH /api/v1/requests/{id}` accepts `project_id` — **admin-only** for reassignment | High |
| PA-010 | Reassigning into an archived project requires `?allow_archived=true` (intentional override for cleanup) | Medium |

#### Projects UI

| ID | Requirement | Priority |
|----|-------------|----------|
| PUI-001 | `/projects` lists projects with: color stripe + icon, name, description, tags chips, lead avatar, request count, active count, total cost USD, target date (or "no target"), last activity, status badge; default sort: last activity desc | Critical |
| PUI-002 | Top of `/projects`: "+ New Project" button (same modal as PA-003) + status filter (Active / Archived / All) | Critical |
| PUI-003 | `/projects/{id}`: header with color/icon, name, description, lead, tags, repo link, target date, status; 4 stat cards (total / active / completed / cost USD); "Next Steps" checklist (when template was selected — PRJ-017); full request list; "Recent Documents" panel with latest 10 docs | Critical |
| PUI-004 | `/projects/{id}` "Submit Request" button pre-selects this project on the New Request form | High |
| PUI-005 | Sidebar gets a Projects entry between Command Center and Prompt Studio | High |
| PUI-006 | Status badges: Active = cyan, Archived = muted gray (renders via existing StatusBadge) | Medium |

#### Backfill & Migration

| ID | Requirement | Priority |
|----|-------------|----------|
| MIG-001 | First-boot migration creates the "Unassigned" project | Critical |
| MIG-002 | All existing requests backfilled with `project_id = <Unassigned>` | Critical |
| MIG-003 | Admin-only CLI script `scripts/backfill_projects.py` with dry-run mode for retroactive bulk reassignment to real projects | Medium |
| MIG-004 | Frontend defensively renders "Unassigned" for any request whose `project_id` points at a missing project (shouldn't happen given PRJ-006, but safety net) | Low |

#### Permissions (RBAC)

| Action | Viewer | Developer | Admin |
|---|---|---|---|
| List + view projects | ✓ | ✓ | ✓ |
| Create / edit name+desc | ✗ | ✓ | ✓ |
| Archive / unarchive | ✗ | ✗ | ✓ |
| Hard-delete (empty only) | ✗ | ✗ | ✓ |
| Submit request into project | ✗ | ✓ | ✓ |
| Reassign request | ✗ | ✗ | ✓ |

#### Non-Goals (explicit for v1)

- No automatic assignment by keyword similarity
- No merged/evolving PRD or user-stories per project (each request still produces its own)
- No multi-project requests (one project per request)
- No per-project RBAC (project-level permissions stay flat — global user roles only)
- No sub-projects / nesting / cross-project linking
- No project-level Slack/email notifications
- No auto-archive on target-date or inactivity (admin must archive explicitly)
- No bulk reassignment UI (one-at-a-time via PATCH for v1)
- No free-form color hex picker (8 preset swatches only) or custom icons (8 preset lucide icons only)
- No global tag taxonomy / autocomplete (tags are per-project free-form strings)
- No edit-the-starter-checklist post-create (template selection is at-create-time only)

---

### 6.8 Build Plan Decomposition — Epic → Feature → Task

**Problem.** Today's task generation (PDB-16/17/18) produces a flat list
of 30-40 "story-sized" tasks. Each row's `description` carries 4-8
sub-task bullets that the agent must deliver in a single response.
Production data from CrewAI (May 2026) showed the consequences:

- **Token-truncated emissions** were the root cause of T-6144cc94's
  four-attempt death streak — the agent emitted 8,260 LOC across 13+
  files in one response and got cut off at the 8K-token cap.
- **Multi-cycle drop-guard loops** killed T-103e9025 — a 764-line file
  was re-emitted at 275 lines three cycles in a row because the
  "task" was too large to fix in one rework cycle.
- **No dependency awareness** means "Dispatch All" fires every backlog
  row in parallel — Phase 7 UI tasks launch before Phase 1 schema
  tasks have created the tables they consume.
- **All-or-nothing review** — the user reviews the entire 30-task list
  in one shot; there's no checkpoint for "approve the high-level
  shape before generating the details".

**Solution.** Replace the flat task list with a three-level hierarchy
(**Epic → Feature → Task**) plus an explicit task-level dependency
DAG. Each level is generated by a separate LLM pass with its own
review checkpoint. The dispatch engine enforces dependencies — a
task can't fire until every entry in its `depends_on` is `deployed`.

```
PROJECT
└── EPIC          ← semantic grouping (e.g. "Authentication", "Dashboard")
    └── FEATURE   ← deliverable capability (e.g. "Login flow", "Password reset")
        └── TASK  ← atomic unit (50-300 LOC, ONE primary file, ONE acceptance test)
            └── depends_on: [task_id, …]    ← the DAG
```

Mapping from today's model: what we call a "task" today (with phase
prefix in the title + sub-task bullets in the description) becomes a
**Feature**; the sub-task bullets become individual **Task** rows;
the phase numbering collapses into the **Epic** layer. Tasks gain a
`primary_file`, `acceptance_test`, `expected_loc`, and `depends_on`.

Detailed design and rationale: [`docs/prd-build-plan-decomposition.md`](prd-build-plan-decomposition.md) (TBD — to be created by BPD-01).

#### Hierarchy Data Model

| ID | Requirement | Priority |
|----|-------------|----------|
| BPD-001 | New `epics` table: `epic_id` (PK, `E-<8hex>`), `project_id` (FK), `list_version`, `list_status` (draft\|finalized\|archived), `ordinal`, `title`, `description`, `acceptance_criteria` (one-sentence top-level test), `review_input` (the comments used to produce this version), `created_at`, `updated_at` | Critical |
| BPD-002 | New `features` table: `feature_id` (PK, `F-<8hex>`), `epic_id` (FK), `project_id` (FK), `list_version`, `ordinal`, `title`, `description`, `acceptance_criteria`, `depends_on` (JSON array of feature_ids), `created_at`, `updated_at` | Critical |
| BPD-003 | Extend `project_tasks` with: `feature_id` (FK, nullable for legacy rows), `depends_on` (JSON array of task_ids, default `[]`), `primary_file` (TEXT — the ONE file this task owns), `expected_loc` (INTEGER, typical 50-300), `acceptance_test` (TEXT, one sentence) | Critical |
| BPD-004 | Legacy task rows without `feature_id` continue to function end-to-end; they render under a synthetic "Legacy" epic for visual continuity | High |
| BPD-005 | Cycle detection at persist time: if the generated `depends_on` graph contains a cycle (T-x → T-y → T-x), the generation is rejected with `422 dag_cycle_detected` listing the offending edge — user must regenerate | Critical |
| BPD-006 | Cross-feature / cross-epic `depends_on` allowed — flagged with an info banner in the UI ("this task depends on T-x44 from the Auth epic"). Validated at persist time (referenced task_id must exist in the same project) | High |
| BPD-007 | Each level (epic / feature / task) supports versioning + draft/finalize/archive identical to today's task list semantics (PDB-13/15) | Critical |

#### Three-Pass Generation

| ID | Requirement | Priority |
|----|-------------|----------|
| BPD-101 | `POST /api/v1/projects/{id}/epics/generate` — Pass 1. PRD + (optional) API spec → 5-12 epics. Body: `{review_comments?}` for regenerate-with-feedback. Output: epic rows in `draft` status | Critical |
| BPD-102 | `POST /api/v1/projects/{id}/epics/{epic_id}/features/generate` — Pass 2. Single epic context + sibling-epic titles → 3-8 features. Generates feature rows under that epic | Critical |
| BPD-103 | `POST /api/v1/projects/{id}/features/{feature_id}/tasks/generate` — Pass 3. Single feature context + sibling-feature titles + parent-epic titles → 5-15 atomic task rows. Each task includes `primary_file`, `acceptance_test`, `depends_on` indices (mapped to task_ids on persist) | Critical |
| BPD-104 | Batch generators: `POST /epics/{id}/features/generate-all` (runs BPD-102 for every epic in this project sequentially) and `POST /features/tasks/generate-all` (runs BPD-103 for every feature). Streams progress so the UI can show "generating features for epic 3/8…" | High |
| BPD-105 | Orchestrator generator: `POST /projects/{id}/build-plan/generate` — runs all three passes back-to-back, auto-finalizing each level before starting the next. Intended for "I trust the agent, just give me the plan" flow; emits a single audit-log event documenting the cascade | Medium |
| BPD-106 | Generation prompts MUST emit atomic task rows: explicit `primary_file`, ≤2 additional files touched, single acceptance criterion, 50-300 LOC expected. Tasks parsed with `expected_loc < 30` (excluding test files) trigger a parse warning so the user can spot over-decomposition | High |
| BPD-107 | Each generation pass uses `max_tokens=32_000` (per L15) and streaming; soft-fails on the L16 transient-network classifier; surfaces `stop_reason="max_tokens"` to the user with a clear "regenerate after splitting this epic" hint when truncation is detected | High |
| BPD-108 | Review-comments flow at every level: regenerating epics with comments preserves features/tasks under epics that weren't changed; regenerating features under one epic doesn't touch siblings; regenerating tasks under one feature doesn't touch siblings | Medium |

#### Dispatch Engine (Dependency-Aware)

| ID | Requirement | Priority |
|----|-------------|----------|
| BPD-201 | `POST /projects/{id}/build/dispatch` enforces `depends_on`: any task whose blockers aren't all `deployed` is refused with `409 dependencies_unmet`, body listing the blocker `task_id`s and their current status | Critical |
| BPD-202 | New endpoint `POST /projects/{id}/build/dispatch-feature/{feature_id}` — fans out all tasks under one feature whose dependencies are met; re-evaluates as each lands | High |
| BPD-203 | New endpoint `POST /projects/{id}/build/dispatch-epic/{epic_id}` — same fan-out at epic scope | High |
| BPD-204 | New endpoint `POST /projects/{id}/build/dispatch-all-ready` — across the entire project, fires every backlog task with all dependencies satisfied. Re-runs on each task `deployed` event (BPD-205) | Critical |
| BPD-205 | EventEmitter handler: on `request.deployed` for a project task, recompute the dispatchable set and (if auto-dispatch is on for this project) auto-fire newly-unblocked tasks. Emits `project.tasks.auto_dispatched` with the list of fired task_ids | High |
| BPD-206 | Per-project setting `auto_dispatch_on_deploy: bool` (default `false`) controls whether BPD-205 actually fires tasks or only logs the dispatchable set for UI display | High |
| BPD-207 | Feature is "complete" when every task under it is `deployed`. Epic is "complete" when every feature under it is complete. Status rollups computed on demand from child statuses (no denormalized state) | Medium |
| BPD-208 | Per-epic and per-feature "deploy" batching: supervisor can defer per-task deploys until all tasks in a feature land, then run one combined `docker compose up -d --build`. Opt-in per project to avoid breaking single-task observability | Low |

#### UI Changes

| ID | Requirement | Priority |
|----|-------------|----------|
| BPD-301 | Task List page reorganized as three collapsible levels — epic → feature → task. Default state: epics collapsed, showing `N/M features done · X/Y tasks · Z blocked`. Expand to drill in | Critical |
| BPD-302 | Each level has its own toolbar action set: epic-level "Generate Features", feature-level "Generate Tasks", task-level "Dispatch / Edit / Delete". Mirrors today's per-row affordances | Critical |
| BPD-303 | Task popup (existing TaskDrillIn) gains `primary_file`, `acceptance_test`, and `depends_on` chips (clickable to jump to blockers). Header crumb shows `Epic › Feature › Task` | High |
| BPD-304 | Build Board (per-project) gains epic + feature filter dropdowns. Cards show parent feature + epic as small chips. Dependency-blocked cards stay in Backlog with a chain icon + "blocked by 3 tasks" tooltip | High |
| BPD-305 | New epic-detail popup (parallel to task popup): shows all features in the epic with status, acceptance criterion, aggregate cost / wall time / commits | Medium |
| BPD-306 | Project header stat chip changes from `4/12 tasks · 2 failed` to `3/8 epics done · 47/156 tasks · 5 blocked` | Medium |
| BPD-307 | Generation flow UI: three approval gates by default (review epics → review features → review tasks). "Approve all and dispatch" mega-button is opt-in per project | High |
| BPD-308 | Dependency validation surfaces in the Task List editor — a task referencing a non-existent or cycle-creating `depends_on` shows an inline error chip with "Fix" suggestion | Medium |

#### Migration & Compatibility

| ID | Requirement | Priority |
|----|-------------|----------|
| BPD-401 | Schema migration: ADD COLUMN statements for epics, features, and the new task columns. No data loss; legacy task rows get `feature_id=NULL, depends_on='[]'` defaults | Critical |
| BPD-402 | Frontend defensively renders legacy tasks (those with `feature_id IS NULL`) under a synthetic "Legacy" epic at the top of the Task List page. No "fix this" prompt — user can leave them as-is | High |
| BPD-403 | Optional migration tool: `POST /projects/{id}/build-plan/decompose-legacy` runs the new three-pass generation against the project's PRD, then offers a diff view ("here's how your existing tasks would map into the new hierarchy"). User reviews + accepts | Medium |
| BPD-404 | In-flight project (CrewAI as of 2026-05-22) is grandfathered: existing task rows continue to function unchanged through the new dispatch enforcement (no `depends_on` set = no blockers = dispatchable). The new model applies to projects whose task list is generated AFTER BPD ships | Critical |

#### Non-Goals (explicit for v1)

- No per-task time estimates / Gantt charts (cycle-time histograms are Phase 3)
- No multi-assignee / claim-task / parking-lot semantics — agent dispatch is still automatic
- No cross-project dependency graph (deps are within one project)
- No partial-feature dispatch with manual override of `depends_on` (forces a regenerate to change the DAG; v2 may add an inline edit)
- No GitHub-issue mirroring of epics/features (the Build Board IS the planning surface)
- No per-epic GitHub branch / PR strategy (still trunk-based via the supervisor); a future enhancement could create a branch per epic for staging review

---

## 7. Task Management System

### 7.1 Task Categories

The system tracks four categories of tasks, each with distinct lifecycle stages:

#### 7.1.1 Development Tasks

| Stage | Description | Exit Criteria |
|-------|------------|---------------|
| Backlog | Story defined, not yet started | User story approved |
| In Progress | Developer actively working | Code committed to feature branch |
| In Review | PR submitted, awaiting review | Code Reviewer approved |
| Done | PR merged to main | CI checks pass, merged |

#### 7.1.2 Testing Tasks

| Stage | Description | Exit Criteria |
|-------|------------|---------------|
| Not Started | Test plan not yet created | — |
| Test Design | Writing test cases from acceptance criteria | Test cases documented |
| Test Execution | Running tests (manual or automated) | All tests executed |
| Pass / Fail | Results recorded | Coverage ≥ 80%, all critical tests pass |

#### 7.1.3 Deployment Tasks

| Stage | Description | Exit Criteria |
|-------|------------|---------------|
| Pending | Feature merged, not yet deployed | — |
| Staging | Deployed to staging environment | Smoke tests pass |
| Production | Deployed to production | Health checks pass |
| Verified | Post-deploy verification complete | No regressions detected |

#### Rework Tasks — Combined Feedback Loop (Approach B)

When both Code Review and Testing complete, a combined quality gate evaluates both results:

| Check | Pass Condition | Fail Condition |
|-------|---------------|----------------|
| Code Review | Verdict = `**APPROVED**`, zero `[CRITICAL]` findings | `**CHANGES REQUESTED**` or any `[CRITICAL]` finding |
| Testing | Zero `FAIL` test cases, verdict = `**READY FOR DEPLOYMENT**` | Any `FAIL` test case or `**NEEDS FIXES**` |

**Combined gate must pass BOTH checks.** If either fails:

| Stage | Description | Exit Criteria |
|-------|------------|---------------|
| Feedback Aggregation | Combine review findings + test failures into one package | Rework instructions generated |
| Rework | Backend + Frontend agents fix ALL issues (review + test) | Fixed code with "Changes in this revision" |
| Re-Review | Code Reviewer verifies fixes, marks each as FIXED/STILL OPEN | All critical issues resolved |
| Re-Test | Tester re-runs full test plan, tags previously-failing tests with [RETEST] | All tests pass |
| Combined Gate | Re-evaluate both review and test results | Both pass OR max cycles reached |

**Pipeline rules:**
- Compilation check is absolute priority — no broken code passes review under any circumstance
- Maximum rework cycles: 2
- After 2 failed cycles: request status = FAILED, DevOps does NOT run
- DevOps only runs when BOTH gates pass
- Each rework cycle includes BOTH code review AND testing (not just one)

### Level 3 Autonomous Deployment

#### Code Commit Gate

| ID | Requirement | Priority |
|----|-------------|----------|
| DC-001 | Backend Specialist's code output must be parsed and written to actual project files on disk | Critical |
| DC-002 | Frontend Specialist's code output must be parsed and written to actual project files on disk | Critical |
| DC-003 | Python code must pass `ruff check` (zero errors) before commit | Critical |
| DC-004 | TypeScript code must pass `tsc --noEmit` (zero errors) before commit | Critical |
| DC-005 | All tests must pass (`pytest` for backend, `npm run build` for frontend) before commit | Critical |
| DC-006 | Code committed to GitHub with descriptive commit message listing all files and agents | Critical |
| DC-007 | If any compilation or test step fails, request status = FAILED, no deployment | Critical |

#### Deployment State Machine

| ID | Requirement | Priority |
|----|-------------|----------|
| DS-001 | `deployment_state` table tracks every deployment step with status, timestamp, and detail | Critical |
| DS-002 | Steps tracked: code_committed → building → staging_deploying → staging_healthy → prod_deploying → prod_healthy → completed | Critical |
| DS-003 | On container restart, DevOps agent reads deployment_state and resumes from last step | Critical |
| DS-004 | Step history stored as JSON array — full audit trail of every state transition | High |

#### Deployment Supervisor (Host Process)

| ID | Requirement | Priority |
|----|-------------|----------|
| SS-001 | Supervisor runs as a Python process on the developer host (`make supervisor` / `make supervisor-bg`), NOT inside Docker. Host execution is required because Compose bind-mount paths in `docker-compose.yml` only resolve correctly when `docker compose` is invoked from the host filesystem. | Critical |
| SS-002 | Supervisor polls `deployment_states` for rows with `current_step = 'code_committed'` and picks them up for processing. | Critical |
| SS-003 | Deployment flow per row: sync files from origin/main → ask the deployment judge for a strategy → docker compose build → staging up + healthcheck → staging teardown → dev rebuild + healthcheck (with retry fallback). **Production deploy step removed** — was intermittently failing under back-to-back staging→prod builds and was not a priority. | Critical |
| SS-004 | Supervisor records every step transition (`syncing`, `judging`, `building`, `staging_deploying`, `staging_healthy`, `dev_rebuilding`, `completed`, `failed`, `rolled_back`, etc.) in `deployment_states.step_history` as a JSON array — full audit trail. | Critical |
| SS-005 | On staging or dev healthcheck failure: supervisor runs the rollback flow (git stash → `git revert HEAD --no-edit` on local main → `git push origin main`) and rebuilds dev from the reverted commit. | Critical |
| SS-006 | Supervisor rebuilds dev containers after a successful deploy so the developer's local dev stack reflects the just-shipped code. | High |
| SS-007 | Supervisor runs a **mirror loop** (independent of any specific deployment): continuous `git fetch origin main` + `git merge --ff-only origin/main` so the host working tree stays current as agents push commits via the GitHub Trees API. Failed ff-merges (dirty working tree) are logged but don't abort the loop. | High |
| SS-008 | Supervisor performs a **surgical sync** per deployment: `git fetch origin main` + `git checkout origin/main -- <files_committed>` rather than `git pull` / `git reset --hard`, so uncommitted work on other paths is preserved. | High |
| SS-009 | Supervisor's healthcheck window for dev rebuild is 120s + auto-restart fallback + final 80s pass before declaring failure (was 30s — too short for cold container starts, especially on Windows hosts). | High |
| SS-010 | Supervisor process termination is idempotent: on restart, it `cleanup_stale_inflight_rows()` to flip any `deploying` / `building` rows back to `code_committed` so deployments resume cleanly after a supervisor crash or `Ctrl+C`. | Medium |

#### Deployment Judge LLM

| ID | Requirement | Priority |
|----|-------------|----------|
| DJ-001 | Before docker work, supervisor calls a judge LLM with the commit's files + diff summary and gets a strategy decision: `deploy_full` / `deploy_staging_only` / `skip` / `hold`. | Critical |
| DJ-002 | Judge's strategy, reasoning, and risk fields persist on the `deployment_states` row (`strategy`, `strategy_reasoning`, `risk`). | High |
| DJ-003 | `skip` strategy: supervisor marks the deployment `completed` without docker work (files synced to host working tree already). Used for doc-only or research-only commits. | High |
| DJ-004 | `hold` strategy: supervisor marks `on_hold` and stops. Manual unblock required — used when the judge sees a high-risk diff (schema migration, auth change, etc.). | High |
| DJ-005 | `deploy_staging_only`: build + staging healthcheck only; skip dev rebuild. Used for risky commits that the judge wants staging-validated before touching dev. | Medium |
| DJ-006 | `deploy_full`: the standard flow described in SS-003. | Critical |

#### Rollback

| ID | Requirement | Priority |
|----|-------------|----------|
| RB-001 | Rollback reverts the offending commit (`git revert HEAD --no-edit`) on local main and pushes to origin (`git push origin main`). The revert is a forward commit, not a force-push — history is preserved. | Critical |
| RB-002 | Rollback brings dev back up from the reverted commit via `docker compose -f docker-compose.yml up -d` with a 180s timeout (was 60s — too short for cold container starts on Windows). | Critical |
| RB-003 | Rollback updates `deployment_states` with `rolled_back` step + reason in `error_message`. | High |
| RB-004 | Staging is torn down before rollback (validation environment, no traffic to drain). No prod rollback step — prod deploy was removed from the supervisor flow. | High |
| RB-005 | If the rollback itself fails (e.g., dev rebuild times out post-revert), supervisor marks `Rollback aborted` in `error_message` so the user sees both the original failure AND the failed-rollback signal in one place. | High |

#### Cross-Platform Reliability (Windows host support)

The supervisor must work identically on Linux, macOS, and Windows developer
hosts. Three Windows-specific bugs that bit production deployments and the
guardrails added to prevent them recurring:

| ID | Requirement | Priority |
|----|-------------|----------|
| CP-001 | `subprocess.run` calls that interpolate dynamic data (file paths, user input) MUST use argv-list form (`shell=False`), not shell-string. Reason: cmd.exe does not strip single quotes the way /bin/sh does, so `git checkout origin/main -- 'path'` was 404'ing every file on Windows. The `run_cmd` helper accepts `str | list[str]` — list form is shell-free. | Critical |
| CP-002 | All subprocess.run calls pin `encoding="utf-8", errors="replace"` to override the platform default. Reason: Windows defaults to cp1252, which can't decode UTF-8 progress spinners in `docker compose build` output — the reader thread crashed and left `result.stdout = None`, blowing up `result.stdout + result.stderr` with NoneType+str. | Critical |
| CP-003 | Health checks use `urllib.request` (stdlib, no shell), not `curl -sf -o /dev/null`. Reason: `/dev/null` doesn't exist on Windows — curl returned exit code 23 (body-write failure) even when the HTTP response was 200, causing every staging healthcheck to be spuriously interpreted as a failure and triggering a rollback. | Critical |
| CP-004 | Supervisor logs force UTF-8 stdout/stderr on `sys.platform == "win32"` so emoji glyphs in log lines (✅ ⚖ 🧹 📥) don't crash the process at the first log call. | High |
| CP-005 | New `subprocess.run(shell=True)` calls require an inline comment explaining (a) why argv form won't work, (b) what the input is and where it came from, (c) what shell injection mitigations apply. PRs without this justification are blocked. | Medium |

#### Stable Compose Project Naming

| ID | Requirement | Priority |
|----|-------------|----------|
| CN-001 | Every Compose file has an explicit top-level `name:` (`agent-team-dev`, `agent-team-staging`, `agent-team-prod`, `agent-team-demo`). Without this, Compose derives the project name from the working directory (e.g., `jolly-aryabhata-f0e6ad` for a worktree), which makes volume names ephemeral and breaks the supervisor's `external: true` volume binding (it has to know the volume name up front). | Critical |
| CN-002 | The `data/` SQLite dir is a host bind-mount (`./data:/app/data`), NOT a named volume. Reason: supervisor reads SQLite directly from the host (per SS-001), and named-volume Docker gymnastics weren't a tractable way to share the DB between supervisor-on-host and backend-in-container. | Critical |
| CN-003 | `reports` and `backups` stay as named volumes — they're write-only caches the host doesn't need to read. | Medium |
| CN-004 | Override with `COMPOSE_PROJECT_NAME=foo` env var when an isolated dev environment is intentionally desired (env var wins over `name:` at the file level). | Low |

#### Pipeline Gate: Zero Tolerance

```
Backend + Frontend → Compile → Test → Git Push → DevOps → Sidecar
                      ↓ FAIL at any step
                   Request FAILED. No deployment. Period.
```

No uncompiled code reaches Docker build. No failed tests reach staging. No unhealthy staging reaches production.

#### 7.1.4 Demo Tasks

| Stage | Description | Exit Criteria |
|-------|------------|---------------|
| Planned | Demo scope defined | Demo script written |
| Prepared | Demo environment ready, data seeded | Dry run successful |
| Delivered | Demo presented to stakeholders | Feedback captured |
| Follow-up | Action items tracked from demo feedback | Items added to backlog |

### 7.2 Task Management Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| TM-001 | Unified dashboard showing status across all four task categories | High |
| TM-002 | Automatic status updates when GitHub issues/PRs change state | High |
| TM-003 | Weekly task completion reports with metrics | Medium |
| TM-004 | Blocked-task alerts when dependencies are unresolved | Medium |
| TM-005 | Integration with the agent team workflow — agents can create and update tasks | High |

---

## Document Persistence & Knowledge Base

### Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| DP-001 | Store every agent's output as a first-class document in a `documents` table with type, title, content, agent_id, version, tags | Critical |
| DP-002 | Auto-extract keyword tags from document content (technology, domain, feature names) | High |
| DP-003 | Before running the pipeline, search existing documents for matching/similar requirements | Critical |
| DP-004 | If matching PRD found (confidence > 70%), skip PRD + Story creation stages and reuse existing documents | Critical |
| DP-005 | Show "Similar requests found" in the UI when user types a description that matches existing work | High |
| DP-006 | Skipped agents show "Reused from REQ-XXX" badge in the Request Detail timeline | Medium |
| DP-007 | User can click "Regenerate" to force fresh PRD/story creation even when match exists | Medium |
| DP-008 | Document versioning — rework cycles increment the version number | Medium |
| DP-009 | Provide a searchable documents API: GET /api/v1/documents/search?q=... | High |
| DP-010 | All document types persisted: PRD, user stories, code review, test report, deploy report | High |

### Document Types

| Type | Source Agent | Content |
|------|------------|---------|
| `prd` | PRD Specialist | Full PRD markdown with numbered requirements |
| `user_stories` | User Story Author | All stories with acceptance criteria |
| `backend_code` | Backend Specialist | Implementation code with file paths |
| `frontend_code` | Frontend Specialist | React/TypeScript components |
| `code_review` | Code Reviewer | Review report with findings and verdict |
| `test_report` | Tester Specialist | Test plan, results, evidence |
| `deploy_report` | DevOps Specialist | Deployment checklist and status |

### Pipeline Skip Logic

When a matching document is found:
```
Pipeline (normal):     PRD → Stories → Dev → Review → Test → DevOps
Pipeline (with reuse): ────skip────── → Dev → Review → Test → DevOps
```

Reuse conditions:
- Keyword match confidence > 70%
- Existing PRD status is from a completed request
- User confirms reuse (or auto-reuse if exact match)

---

## 8. Demo Creation

### 8.1 Demo Feature Design

| ID | Requirement | Priority |
|----|-------------|----------|
| DM-001 | Build a demo feature that showcases core project functionality end-to-end | High |
| DM-002 | Demo must be runnable with a single command (e.g., `npm run demo` or `make demo`) | High |
| DM-003 | Include sample data that illustrates realistic usage scenarios | Medium |
| DM-004 | Provide a guided walkthrough script for presenters | Medium |

### 8.2 Demo Testing Framework

| ID | Requirement | Priority |
|----|-------------|----------|
| DT-001 | Automated tests that validate the demo runs successfully | High |
| DT-002 | Weekly scheduled test runs (via GitHub Actions cron) to catch regressions | High |
| DT-003 | Test coverage for all demo-critical paths | Medium |
| DT-004 | Alerting when demo tests fail (e.g., GitHub notification, Slack alert) | Medium |

---

## 9. Edge Cases & Risk Mitigation

### 9.1 Code Review Process

| Risk | Mitigation |
|------|-----------|
| Developer frustration from slow reviews | Set SLA: reviews completed within 24 hours of PR submission |
| Inconsistent review quality | Use a code review checklist; Code Reviewer agent follows standardized criteria |
| Coverage gaming (low-value tests to hit 80%) | Code Reviewer evaluates test quality, not just coverage numbers |
| Disagreements on review feedback | Escalation path: developer can request re-review with justification |

### 9.2 GitHub Repository Maintenance

| Task | Frequency | Description |
|------|-----------|-------------|
| Stale branch cleanup | Weekly | Delete branches merged > 7 days ago |
| Dependency updates | Bi-weekly | Run Dependabot or equivalent; review and merge updates |
| Issue triage | Weekly | Review open issues, close stale ones, re-prioritize |
| Label audit | Monthly | Ensure labels are consistent and up-to-date |

### 9.3 Task Priority Management

| Priority | SLA | Examples |
|----------|-----|---------|
| Critical | Immediate — blocks release | Production bugs, security vulnerabilities |
| High | Within current sprint | Core feature work, failing CI |
| Medium | Next 1-2 sprints | Enhancements, tech debt, documentation improvements |
| Low | Backlog — address as capacity allows | Nice-to-haves, cosmetic fixes |

### 9.4 Scaling Risks

| Risk | Mitigation |
|------|-----------|
| Config drift across YAML files | Run `python -m src.config.validator` after every change; CI validates on PR |
| Delegation chain too deep (>3 hops) | Keep hierarchy to 3 levels max: Engineering Lead -> Team Lead -> Agent |
| Orphan agents not receiving work | Config validator checks every agent belongs to a team and has a `reports_to` |
| Tool permission creep | Quarterly audit of `tools.yaml`; principle of least privilege |
| Workflow bottlenecks as team grows | Monitor stage durations; add parallel tracks or split bottleneck stages |

---

## 10. Expected Output Formats

### 10.1 PRD Document Format

PRD documents must be written in Markdown with the following structure:

```
1. Executive Summary
2. Goals
3. Product Overview (features, architecture)
4. Detailed Requirements (functional requirements tables with IDs)
5. Non-Functional Requirements
6. Edge Cases & Risk Mitigation
7. Evaluation Criteria
8. Appendix (glossary, references)
```

Each requirement must have:
- A unique ID (e.g., `REQ-001`)
- A clear description
- A priority level (Critical / High / Medium / Low)

### 10.2 User Story Documentation Format

User stories follow this template (designed for junior developer clarity):

```
### Story: [US-XXX] [Short Title]

**As a** [type of user],
**I want** [action/feature],
**So that** [benefit/value].

---

**Priority:** [Critical / High / Medium / Low]
**Estimated Effort:** [S / M / L / XL]

**Acceptance Criteria:**
- [ ] Given [context], when [action], then [expected result]
- [ ] Given [context], when [action], then [expected result]

**Notes for Developers:**
- [Plain-language explanation of what this means technically]
- [Any gotchas or things to watch out for]

**Diagram (if applicable):**
[Simple ASCII or Mermaid diagram showing the workflow]
```

### 10.3 Task Management Report Format

Weekly reports include:

```
## Weekly Task Report — [Date Range]

### Summary
| Category | Total | Done | In Progress | Blocked | Not Started |
|----------|-------|------|-------------|---------|-------------|
| Development | X | X | X | X | X |
| Testing | X | X | X | X | X |
| Deployment | X | X | X | X | X |
| Demo | X | X | X | X | X |

### Code Coverage
- Current: XX%
- Target: 80%
- Trend: ↑ / ↓ / →

### Highlights
- [Key accomplishments this week]

### Blockers
- [Any blocked tasks and why]

### Next Week Focus
- [Priorities for the coming week]
```

### 10.4 Agent Output Quality Standards

All agents must adhere to these non-negotiable quality standards:

| Standard | Applies To | Enforcement |
|----------|-----------|-------------|
| Complete files only | Backend, Frontend Specialists | No truncated code, no placeholders, no "..." or "TODO" |
| Compilation verification | Backend, Frontend Specialists | Self-verification checklist before submission |
| Compilation gate | Code Reviewer | FIRST checks every file compiles before quality review |
| Structured reports | All agents | Must follow the specified output template for their role |
| No clarification questions | All agents | Produce output directly from provided context |
| Combined quality gate | Code Reviewer + Tester | Both must pass before DevOps runs |

---

## 11. Constraints

All thresholds are configurable in `config/thresholds.yaml`. Default values:

| Constraint | Default Value | Config Key | Enforcement |
|-----------|--------------|-----------|-------------|
| Code Coverage | ≥ 80% | `code_coverage_minimum` | GitHub Actions blocks PR merge if below threshold |
| Deployment Frequency | Weekly | `deployment_frequency` | Docker Compose deployment pipeline (staging → prod) |
| Demo Testing | Weekly (Mondays 9am) | `demo_test_frequency` | GitHub Actions cron job |
| Review SLA | 24 hours | `review_sla` | Alert if exceeded |
| Stale Branch Age | 7 days | `stale_branch_age` | Automated cleanup |
| Max Concurrent Tasks | 3 per agent | `max_concurrent_tasks_per_agent` | Queue overflow prevention |

---

## 12. Evaluation Criteria

### 12.1 PRD Document Quality

| Criteria | Measurement |
|---------|-------------|
| Clarity | All requirements understandable without additional context |
| Completeness | No requirement gaps — every feature has defined requirements with IDs |
| Consistency | Terminology, formatting, and priority levels are uniform across the document |
| Traceability | Every requirement links to at least one user story |

### 12.2 User Story Documentation Clarity

| Criteria | Measurement |
|---------|-------------|
| Junior-Developer Readability | A developer with < 1 year experience can implement from the story alone |
| Acceptance Criteria Quality | Each criterion is testable and unambiguous |
| Completeness | Every feature has corresponding user stories |
| Diagram Usage | Complex workflows include visual aids |

### 12.3 Task Management Efficiency

| Criteria | Measurement |
|---------|-------------|
| Tracking Accuracy | Task statuses reflect reality within 24 hours |
| Report Timeliness | Weekly reports delivered by end-of-day Monday |
| Blocker Resolution | Blocked tasks are escalated within 24 hours |
| Coverage Compliance | Code coverage stays ≥ 80% across all reports |

---

### 12.4 Configuration System Quality

| Criteria | Measurement |
|---------|-------------|
| Config Validation | All YAML configs pass schema validation with zero errors |
| Expansion Ease | A new agent can be added in under 15 minutes following the playbook |
| Hierarchy Integrity | No orphan agents, no circular delegation, all references resolve |
| Threshold Consistency | All operational values sourced from `thresholds.yaml`, none hardcoded |

---

## 13. Sample User Stories

### Story: [US-001] Set Up GitHub Repository

**As a** developer,
**I want** a well-structured GitHub repository with branch protection and CI/CD,
**So that** the team can collaborate safely with automated quality checks.

---

**Priority:** Critical
**Estimated Effort:** M

**Acceptance Criteria:**
- [ ] Given the repo exists, when a PR is opened, then linting and formatting checks run automatically
- [ ] Given branch protection is enabled, when a PR has failing checks, then merge is blocked
- [ ] Given a PR is opened, then code coverage is calculated and posted as a comment
- [ ] Given coverage drops below 80%, then the PR cannot be merged

**Notes for Developers:**
- Use GitHub Actions for CI — see `.github/workflows/` for workflow files
- Branch protection goes on `main` — require at least 1 approval + passing checks
- Use a coverage tool appropriate to the project language (e.g., Jest for JS, pytest-cov for Python)

---

### Story: [US-002] Create PRD Template

**As a** PRD Specialist agent,
**I want** a standardized PRD template,
**So that** all PRD documents follow a consistent, complete structure.

---

**Priority:** High
**Estimated Effort:** S

**Acceptance Criteria:**
- [ ] Given the template exists, when a new PRD is needed, then the specialist can copy and fill it in
- [ ] Given the template, when reviewed, then it contains all sections listed in Section 10.1
- [ ] Given the template, when used by a junior developer, then all sections are self-explanatory

---

### Story: [US-003] Implement Weekly Task Report

**As a** project manager,
**I want** automated weekly task reports,
**So that** I can track progress across development, testing, deployment, and demo tasks.

---

**Priority:** High
**Estimated Effort:** M

**Acceptance Criteria:**
- [ ] Given it is Monday, when the report runs, then it includes counts for all four task categories
- [ ] Given there are blocked tasks, when the report runs, then blockers are highlighted
- [ ] Given code coverage data exists, then the report shows current coverage and trend

---

### Story: [US-004] Add a New Agent to an Existing Team

**As a** team administrator,
**I want** to add a new specialist agent by editing YAML config files only,
**So that** the team can grow without requiring code changes or redeployment.

---

**Priority:** High
**Estimated Effort:** S

**Acceptance Criteria:**
- [ ] Given a new agent YAML is created in `config/agents/`, when the config validator runs, then it passes with no errors
- [ ] Given the agent is added to `teams.yaml` and lead's delegation list, when work is submitted, then the new agent receives delegated tasks
- [ ] Given the expansion playbook is followed, when a non-developer follows the steps, then the agent is added in under 15 minutes
- [ ] Given the new agent is added, when existing workflows run, then no existing agents are affected

**Notes for Developers:**
- Follow the step-by-step guide in `docs/expansion-playbook.md`
- Always run `python -m src.config.validator` after making config changes
- Remember to update 3 files: new agent YAML, `teams.yaml`, lead's `can_delegate_to`

---

## 14. Appendix

### 14.1 Glossary

| Term | Definition |
|------|-----------|
| PRD | Product Requirements Document — describes what to build and why |
| User Story | A short description of a feature from the end user's perspective |
| Acceptance Criteria | Conditions that must be met for a story to be considered complete |
| Code Coverage | The percentage of code executed by automated tests |
| CI/CD | Continuous Integration / Continuous Deployment — automated build, test, and deploy pipeline |
| SLA | Service Level Agreement — a target response or completion time |
| DAG | Directed Acyclic Graph — a workflow structure where stages can run in parallel but never loop |
| Quality Gate | A pass/fail check that must succeed before a workflow stage can proceed |
| Working Lead | An agent that both performs specialist work AND coordinates their team |

### 14.2 Related Documents

| Document | Path | Description |
|----------|------|-------------|
| System Architecture | [docs/architecture.md](architecture.md) | Full config schemas, component design, YAML examples |
| Expansion Playbook | [docs/expansion-playbook.md](expansion-playbook.md) | Step-by-step guide to add agents, teams, workflows |

### 14.3 External References

- Existing PRD: `C:\ai-projects\agent-team\references\prd-agent-team.md`
- Agent Team Plan: `C:\ai-projects\agent-team\AGENT_TEAM_PLAN.md`

### 14.4 Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-04-04 | Chandramouli | Initial draft — 7 agents, linear workflow |
| 2.0 | 2026-04-04 | Chandramouli | Scalable architecture — 8 agents, team hierarchy, DAG workflows, config-driven system |
| 3.0 | 2026-04-06 | Chandramouli | Added Section 5 (UI Features & Enhancements) — light/dark theme toggle, 6 themes, screenshot attachments, live activity feed, agent output visibility, markdown rendering, cost dashboard. Renumbered sections 6-14. |
| 3.5 | 2026-05-17 | Chandramouli | Refresh pass: added Section 0 (Recent Changes); trimmed theme catalog from 6 → 2 (Vercel + Cyberpunk Hyperdrive); added UI-008a/b/c/d for the cyberpunk effects layer, overlay component, and left sidebar; removed multi-provider toggle references (Anthropic/Bedrock) — single provider is Claude Platform on AWS; updated Prompt Studio reqs (1 variant, no provider toggle); marked FE-25 obsolete; documented supervisor host execution + Windows portability fixes. Filename renamed from `prd-template.md` → `prd.md`. |
| 3.6 | 2026-05-17 | Chandramouli | Backfilled missing PRD entries for already-shipped supervisor work: rewrote stale Sidecar Supervisor section (renamed to "Deployment Supervisor (Host Process)") with current SS-001–010; added new sections for the Deployment Judge LLM (DJ-001–006), rewrote Rollback (RB-001–005) to drop image retagging + prod rollback, added Cross-Platform Reliability subsection (CP-001–005) covering the argv-form / UTF-8 / urllib / emoji fixes, and added Stable Compose Project Naming subsection (CN-001–004). |
| 3.7 | 2026-05-17 | Chandramouli | Added Section 6.7 Project Management — explicit-assignment model (no auto-match), REQ groups PRJ-001–008 (CRUD), PA-001–010 (assignment), PUI-001–006 (UI), MIG-001–004 (backfill), plus RBAC matrix and explicit Non-Goals. Detailed design lives in docs/prd-projects-feature.md. |
| 3.8 | 2026-05-17 | Chandramouli | Added Table of Contents at the top of the document for navigation. Two-level depth: top-level sections 0–14 plus subsections 6.1–6.7 (the GitHub Integration / Project Management area) which has the highest section density. |
| 3.9 | 2026-05-17 | Chandramouli | TOC gained an "Added" column recording when each section first appeared in the PRD. Section 6.7 Project Management marked **2026-05-17** (today). Earlier sections backfilled from the v1.0 / v2.0 / v3.0 revision history dates; the GitHub Integration subsections 6.4–6.6 marked 2026-04-08 (the approximate timeframe based on related task-list entries). |
| 3.10 | 2026-05-17 | Chandramouli | Section 6.7 Project Management — expanded v1 scope. Every previously-out-of-scope create-form field moved INTO v1: color (PRJ-009), icon (PRJ-010), tags (PRJ-011), lead user (PRJ-012), repo URL (PRJ-013), target date (PRJ-014), default team (PRJ-015), templates with starter checklist (PRJ-016, PRJ-017). PA-002/003 updated to describe expanded form behavior. PUI-001/003 updated to display new fields. Non-Goals list pruned to true v2 items. Detail in docs/prd-projects-feature.md v1.1. |
| 3.11 | 2026-05-17 | Chandramouli | Project Management feature **shipped** (43/50 tasks done, 4 deferred for v2). Backend: projects table + 5 CRUD endpoints + `/projects/templates`, Unassigned seed + 13-request backfill, project_id on Request, archived-project submission rejection, RBAC (any user create/edit; admin-only archive/delete/lead-reassign). Frontend: Projects list page (/projects), Project detail with rollup stats + Next Steps from template checklist, CreateProjectModal with all 10 v1 fields, required Project dropdown on New Request form, Project chips surfaced on Command Center, History (+filter), RequestDetail, StoryBoard breadcrumb, CostDashboard (+filter). Module-level project cache with WebSocket invalidation on `project.*` events. Bugs found and fixed during smoke test: GET /requests/{id} response was missing project_id; App.tsx didn't register /projects routes despite importing the pages; CostDashboard "Today"/"This Month" cards weren't scoped by project_id filter. Deferred to v2: OpenAPI typegen (PM-19), TanStack Query hooks (PM-20), CreateProjectModal Vitest suite (PM-40), backend project store tests (PM-18). |
| 3.12 | 2026-05-18 | Chandramouli | Project-driven Build feature **shipped** (all 50 PDB tasks done across 5 phases). Adds a stage-gated, human-in-the-loop authoring flow inside every project: write a brief → generate a PRD via the existing `prd_specialist` agent → edit & finalize → generate a task list via `user_story_author` (one-shot fenced-JSON output, regex markdown fallback) → edit & finalize → dispatch tasks (each becomes a Request via the existing orchestrator path with the new `source_task_id` back-link) → chat with the new `project_orchestrator` agent (6 tools: list/dispatch/cancel/modify/add task + get_project_status) → watch progress on the new project-mode Story Board at `/stories/project/:id`. New tables: `project_artifacts`, `project_tasks`, `build_session_messages`. New columns: `requests.source_task_id`, `token_usage.project_artifact_id`. New executor entrypoint: `single_agent_call()` (no Request, no workflow, no events). New EventEmitter `on(handler)` hook drives the PDB-25 `request.status_changed → project_tasks.task_status` mapper. New WS event types: `project.prd_generated`, `project.prd_finalized`, `project.tasks_finalized`, `project.build.message`. Cost dashboard scoping extended to UNION on artifact_id. Bugs caught + fixed during smoke: agent_id was `prd_author` in route but `prd_specialist` in YAML; executor.agent_executor wasn't on app.state at boot; tool-use loop needed manual orchestrator handle passed to `run_chat_turn` (closure pattern). Detailed PRD: docs/prd-project-driven-build.md v1.0. |
