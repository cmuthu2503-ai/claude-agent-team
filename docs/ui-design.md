# UI Design
# Agent Team — User Interface Specification

---

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 1.0 |
| Created Date | 2026-04-04 |
| Last Updated | 2026-04-04 |
| Status | Draft |
| Product Owner | Chandramouli |

---

## 1. Design Philosophy

The user describes **what** to build. The system handles **everything else**.

The mental model is: **email compose + package tracking**. You write what you want, hit send, and watch it get delivered. That's it.

### 1.1 Design Principles

| Principle | Rule |
|-----------|------|
| **One input, full automation** | The text box is the only thing the user MUST interact with |
| **Watch, don't manage** | Progress is visible but never requires user intervention |
| **No training needed** | A junior developer can use it on day one |
| **4 screens, not 40** | Every screen earns its place; if in doubt, leave it out |
| **Real-time by default** | Active work updates live — no refresh button needed |

---

## 2. Screen Inventory (5 Screens)

| # | Screen | Purpose | User Time |
|---|--------|---------|-----------|
| 1 | **Command Center** | Submit requests + watch active work | 80% |
| 2 | **Request Detail** | Deep view of a single request's agent timeline | 15% |
| 3 | **History** | Search/filter all past requests | 3% |
| 4 | **Releases** | Deployment pipeline and health | 1% |
| 5 | **Team Status** | Agent activity at a glance | 1% |

The user spends 95% of their time on screens 1 and 2.

---

## 3. Navigation

```
┌──────────────────────────────────────────────────────────────────────────┐
│  ◆ Agent Team        [Command Center]   History   Releases   Team  [?]  │
└──────────────────────────────────────────────────────────────────────────┘
```

- Always visible at the top
- 4 items only — no dropdowns, no sub-menus
- Active screen is highlighted
- `[?]` opens a quick-help overlay (one-paragraph explanation of each screen)

---

## 4. Screen 1: Command Center (Home)

This is the primary screen. Submit requests at the top, watch active work below.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  ◆ Agent Team        [Command Center]   History   Releases   Team     [?]   │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │                                                                        │  │
│  │  What would you like to build?                                         │  │
│  │                                                                        │  │
│  │  ┌──────────────────────────────────────────────────────────────────┐  │  │
│  │  │                                                                  │  │  │
│  │  │  Build a user profile page that shows name, email, and avatar    │  │  │
│  │  │  with an edit form. Include image upload for the avatar.         │  │  │
│  │  │                                                                  │  │  │
│  │  └──────────────────────────────────────────────────────────────────┘  │  │
│  │                                                                        │  │
│  │   Type: [Feature ▾]     Priority: [High ▾]     [ Submit Request >>> ] │  │
│  │                                                                        │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ── Active Requests ──────────────────────────────────────────────────────   │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │  ● REQ-042  Login page with JWT authentication                        │  │
│  │    Started 12 min ago  ·  Feature  ·  High                            │  │
│  │                                                                        │  │
│  │    Planning      Development        Review     Testing    Deploy       │  │
│  │    [████████] >  [████░░░░]  >>>  [--------]  [--------]  [--------]  │  │
│  │     Done          In Progress       Waiting    Waiting     Waiting     │  │
│  │                                                                        │  │
│  │    ┌─ Live ──────────────────────────────────────────────────────┐     │  │
│  │    │  🔵 Backend Specialist   Writing API routes...    12s ago   │     │  │
│  │    │  🔵 Frontend Specialist  Creating LoginForm...     8s ago   │     │  │
│  │    │  ✅ User Story Author    5 stories created        2 min ago │     │  │
│  │    └─────────────────────────────────────────────────────────────┘     │  │
│  │                                                       [ View Details ] │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │  ● REQ-041  Fix broken pagination on search results                   │  │
│  │    Started 28 min ago  ·  Bug Fix  ·  High                            │  │
│  │                                                                        │  │
│  │    Triage       Fix          Review + Test      Deploy                 │  │
│  │    [████████]  [████████] >  [██████░░]  >>>  [--------]              │  │
│  │     Done         Done         In Progress       Waiting                │  │
│  │                                                                        │  │
│  │    ┌─ Live ──────────────────────────────────────────────────────┐     │  │
│  │    │  🔵 Code Reviewer       Reviewing PR #67...       30s ago  │     │  │
│  │    │  🔵 Tester Specialist   Running regression...     15s ago  │     │  │
│  │    └─────────────────────────────────────────────────────────────┘     │  │
│  │                                                       [ View Details ] │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ── Recently Completed ───────────────────────────────────────────────────   │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │  ✅ REQ-040  Add dark mode toggle           Completed 2 hours ago     │  │
│  │     Coverage: 91%  |  PRs: #63, #64  |  Deployed to production        │  │
│  │                                                                        │  │
│  │  ✅ REQ-039  Update API rate limiting        Completed 5 hours ago     │  │
│  │     Coverage: 88%  |  PR: #61  |  Deployed to production              │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

- **Text area is the first thing the user sees** — no wizards, no forms, just type what you want
- **Type dropdown** maps to workflow triggers: Feature, Bug Fix, Documentation, Demo, Spike
- **Pipeline bar** adapts shape per workflow type (Feature = 6 stages, Bug Fix = 4 stages)
- **Live Activity feed** auto-updates via WebSocket — latest 3 agent actions shown
- **Recently Completed** shows last 3-5 finished requests with one-line outcomes

### Additional Command Center Features

- **Inline screenshot attachments**: Paste (Ctrl+V), drag-and-drop, or click to attach images directly in the description text box
- **Similar request detection**: As the user types, the system searches existing PRDs and shows "Similar PRDs found" if matches exist
- **Live Activity feed**: Real-time WebSocket-based activity stream showing agent progress, status changes, and completion events

---

## 5. Screen 2: Request Detail

Accessed by clicking any request card. The deep-dive view.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  ◆ Agent Team        Command Center   History   Releases   Team        [?]  │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ← Back                                                                      │
│                                                                              │
│  REQ-042: Login page with JWT authentication                                 │
│  Chandramouli  ·  12 min ago  ·  Feature  ·  High                           │
│                                                                              │
│  ┌─ Pipeline ──────────────────────────────────────────────────────────┐     │
│  │                                                                      │     │
│  │  Requirements    Stories    Backend    Frontend    Review    Test     │     │
│  │    [ ✅ ]  >>>  [ ✅ ]  >>> [ 🔵 ] /// [ 🔵 ] >>> [ ○ ] >>> [ ○ ]  │     │
│  │    1:42          2:18       Active     Active      Wait      Wait   │     │
│  │                                                                      │     │
│  │                             ^^^ parallel ^^^                 Deploy  │     │
│  │                                                               [ ○ ]  │     │
│  │                                                               Wait   │     │
│  └──────────────────────────────────────────────────────────────────────┘     │
│                                                                              │
│  ┌─ Agent Timeline ────────────────────────────────────────────────────┐     │
│  │                                                                      │     │
│  │  12:00  ┌─────────────────────────────────────────────────────┐      │     │
│  │    ▼    │  ⚙ Engineering Lead                          1:02  │      │     │
│  │         │  Analyzed request. Domains: backend, frontend,      │      │     │
│  │         │  auth, testing. Created 2 subtasks.                 │      │     │
│  │         │  [View Delegation Plan]                             │      │     │
│  │         └─────────────────────────────────────────────────────┘      │     │
│  │                                                                      │     │
│  │  12:01  ┌─────────────────────────────────────────────────────┐      │     │
│  │    ▼    │  📋 PRD Specialist                       ✅ 1:42   │      │     │
│  │         │  Created PRD with 6 requirements.                   │      │     │
│  │         │  📄 [prd-login-feature.md]                          │      │     │
│  │         └─────────────────────────────────────────────────────┘      │     │
│  │                                                                      │     │
│  │  12:03  ┌─────────────────────────────────────────────────────┐      │     │
│  │    ▼    │  📝 User Story Author                    ✅ 2:18   │      │     │
│  │         │  Created 5 user stories with acceptance criteria.   │      │     │
│  │         │  📄 [user-stories-login.md]                         │      │     │
│  │         └─────────────────────────────────────────────────────┘      │     │
│  │                                                                      │     │
│  │  12:05  ┌─────────────────────────────────────────────────────┐      │     │
│  │    ▼    │  ⚙ Code Reviewer (Dev Lead)                  0:45  │      │     │
│  │         │  Decomposed: Backend (JWT API) + Frontend (login UI)│      │     │
│  │         └─────────────────────────────────────────────────────┘      │     │
│  │                                                                      │     │
│  │  12:06  ┌─ PARALLEL ──────────────────────────────────────────┐      │     │
│  │    ▼    │                                                      │      │     │
│  │         │  ┌────────────────────┐  ┌────────────────────┐     │      │     │
│  │         │  │ 🔵 Backend Spec.   │  │ 🔵 Frontend Spec.  │     │      │     │
│  │         │  │                    │  │                     │     │      │     │
│  │         │  │ Writing JWT API... │  │ Creating Login UI...│     │      │     │
│  │         │  │                    │  │                     │     │      │     │
│  │         │  │ Files:             │  │ Files:              │     │      │     │
│  │         │  │  src/auth/routes.py│  │  src/pages/Login.tsx│     │      │     │
│  │         │  │  src/auth/jwt.py   │  │  src/components/    │     │      │     │
│  │         │  │  tests/test_auth.py│  │    LoginForm.tsx    │     │      │     │
│  │         │  │                    │  │                     │     │      │     │
│  │         │  │ ████████░░░  ~75%  │  │ ██████░░░░  ~60%   │     │      │     │
│  │         │  └────────────────────┘  └─────────────────────┘    │      │     │
│  │         │                                                      │      │     │
│  │         └──────────────────────────────────────────────────────┘      │     │
│  │                                                                      │     │
│  │  (Waiting: Code Reviewer, Tester Specialist, DevOps Specialist)      │     │
│  │                                                                      │     │
│  └──────────────────────────────────────────────────────────────────────┘     │
│                                                                              │
│  ┌─ Outputs ───────────────────────────────────────────────────────────┐     │
│  │  📄 prd-login-feature.md        PRD Specialist       [View] [Copy]  │     │
│  │  📄 user-stories-login.md       User Story Author    [View] [Copy]  │     │
│  └──────────────────────────────────────────────────────────────────────┘     │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

- **Pipeline at top** shows DAG structure — parallel stages use `///` connectors
- **Agent timeline** is a vertical chronological feed — each card = one agent's work
- **Parallel work** shown side-by-side in a grouped block, updating independently
- **Artifact links** are clickable (`[View]` opens inline, `[Copy]` copies file path)
- **Waiting agents** listed at the bottom so the user knows what's coming next

---

## 6. Screen 3: History

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  ◆ Agent Team        Command Center   [History]   Releases   Team     [?]   │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─ Search & Filter ──────────────────────────────────────────────────┐     │
│  │  [🔍 Search requests...                              ]              │     │
│  │  Status: [All ▾]    Type: [All ▾]    Date: [Last 30 days ▾]       │     │
│  └──────────────────────────────────────────────────────────────────────┘     │
│                                                                              │
│  Showing 42 requests                                                         │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │  ID        Request                     Type      Status     Date     │    │
│  │  ────────────────────────────────────────────────────────────────    │    │
│  │  REQ-042   Login page with JWT auth    Feature   🔵 Active   12m    │    │
│  │            Agents: 4/8 engaged                                       │    │
│  │                                                                      │    │
│  │  REQ-041   Fix broken pagination       Bug Fix   🔵 Active   28m    │    │
│  │            PR: #67  |  Agents: 3/8 engaged                          │    │
│  │                                                                      │    │
│  │  REQ-040   Add dark mode toggle        Feature   ✅ Done     2 hrs  │    │
│  │            Coverage: 91%  |  PRs: #63, #64  |  Deployed              │    │
│  │                                                                      │    │
│  │  REQ-039   Update API rate limiting    Feature   ✅ Done     5 hrs  │    │
│  │            Coverage: 88%  |  PR: #61  |  Deployed                    │    │
│  │                                                                      │    │
│  │  REQ-037   Add email notifications     Feature   ❌ Failed   1 day  │    │
│  │            Coverage: 72% — gate failed (threshold: 80%)              │    │
│  │            [ Retry ]                                                  │    │
│  │                                                                      │    │
│  │  ────────────────────────────────────────────────────────────────    │    │
│  │  ◄ Prev   Page 1 of 5   Next ►                                     │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─ Stats ─────────────────────────────────────────────────────────────┐    │
│  │  Total: 42  |  ✅ Done: 37  |  🔵 Active: 2  |  ❌ Failed: 3       │    │
│  │  Avg Coverage: 89%  |  Avg Time: 22 min  |  Success Rate: 88%      │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

- **Simple table** — each row shows what matters: request, status, coverage, PRs, deployment
- **Failed requests** have a `[ Retry ]` button that re-submits with the same parameters
- **Stats bar** at bottom gives a quick health overview
- **Click any row** to navigate to Request Detail

---

## 7. Screen 4: Releases

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  ◆ Agent Team        Command Center   History   [Releases]   Team     [?]   │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─ Deployment Pipeline ───────────────────────────────────────────────┐    │
│  │                                                                      │    │
│  │   PENDING (2)           STAGING (1)            PRODUCTION            │    │
│  │   ──────────            ──────────             ──────────            │    │
│  │                                                                      │    │
│  │   ┌─────────────┐     ┌─────────────┐      Last deployed:           │    │
│  │   │ REQ-042     │     │ REQ-041     │      REQ-040 (2 hrs ago)      │    │
│  │   │ Login page  │     │ Fix paginate│                                │    │
│  │   │ Waiting for │     │ Smoke tests │      Health: ✅ All OK         │    │
│  │   │ dev phase   │     │ running...  │      Uptime: 99.97%           │    │
│  │   └─────────────┘     │ [View Logs] │                                │    │
│  │                        └─────────────┘                               │    │
│  │   ┌─────────────┐                                                    │    │
│  │   │ REQ-043     │                                                    │    │
│  │   │ Doc update  │                                                    │    │
│  │   │ No deploy   │                                                    │    │
│  │   └─────────────┘                                                    │    │
│  │                                                                      │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ── Recent Deployments ───────────────────────────────────────────────────   │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │  Date          Request                   Status     Coverage  PRs    │    │
│  │  ────────────────────────────────────────────────────────────────    │    │
│  │  Today 10:15   REQ-040 Dark mode toggle  ✅ Live     91%    #63,#64 │    │
│  │  Today 07:30   REQ-039 API rate limiting ✅ Live     88%    #61     │    │
│  │  Yesterday     REQ-038 Fix token expiry  ✅ Live     85%    #59     │    │
│  │  Apr 2         REQ-036 Onboarding flow   ✅ Live     93%    #55,#56 │    │
│  │  Apr 1         REQ-034 Admin panel       🔙 Rolled   82%    #50     │    │
│  │                                            Back                      │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─ Health ────────────────────────────────────────────────────────────┐    │
│  │  Frequency: 4 this week (target: weekly ✅)                         │    │
│  │  Success rate: 95% (19/20 last 30 days)                             │    │
│  │  Avg deploy time: 4 min    |    Rollbacks: 1 in 30 days            │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

- **Kanban pipeline** (Pending → Staging → Production) — items flow automatically
- **Documentation-only requests** show "No deploy" so users aren't confused
- **Deployment health** surfaces metrics from `config/thresholds.yaml`
- **Rollbacks** are visually distinct (orange)

---

## 8. Screen 5: Team Status

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  ◆ Agent Team        Command Center   History   Releases   [Team]     [?]   │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─ Engineering Lead ──────────────────────────────────────────────────┐    │
│  │  ⚙ Engineering Lead               🔵 Active                        │    │
│  │  Analyzing REQ-042: Login page with JWT authentication              │    │
│  │  Tasks today: 4  |  Avg decomposition: 62s                         │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─ Planning Team ──────────────┐  ┌─ Development Team ──────────────┐     │
│  │                               │  │                                  │     │
│  │  📋 PRD Specialist            │  │  🔍 Code Reviewer (Lead)         │     │
│  │     ⬤ Idle                    │  │     ○ Waiting                     │     │
│  │     Done today: 2             │  │     Waiting on REQ-042 dev       │     │
│  │     Avg time: 1:42            │  │     Reviews today: 1             │     │
│  │                               │  │                                  │     │
│  │  📝 User Story Author         │  │  💻 Backend Specialist            │     │
│  │     ⬤ Idle                    │  │     🔵 Active                     │     │
│  │     Done today: 2             │  │     REQ-042: Writing API routes   │     │
│  │     Stories created: 11       │  │                                  │     │
│  │                               │  │  🎨 Frontend Specialist           │     │
│  │                               │  │     🔵 Active                     │     │
│  │                               │  │     REQ-042: Creating LoginForm   │     │
│  │                               │  │                                  │     │
│  └───────────────────────────────┘  └──────────────────────────────────┘     │
│                                                                              │
│  ┌─ Delivery Team ─────────────────────────────────────────────────────┐    │
│  │                                                                      │    │
│  │  🚀 DevOps Specialist (Lead)          🧪 Tester Specialist           │    │
│  │     🔵 Active                            ○ Waiting                    │    │
│  │     REQ-041: Running smoke tests         Waiting on REQ-042 review   │    │
│  │     Deploys today: 1                     Tests today: 47 (100% pass) │    │
│  │                                                                      │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─ Metrics (Today) ───────────────────────────────────────────────────┐    │
│  │  Requests: 4  |  Active agents: 3/8  |  Avg coverage: 89%          │    │
│  │  Gate passes: 7/8  |  Failed: 1 (coverage on REQ-037)              │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

- **Layout mirrors the team hierarchy** from `config/teams.yaml`
- **Each agent card** shows: status, current task, and one daily metric
- **Purely informational** — no buttons, no interactions needed
- **Dynamically rendered** from config — adding agents/teams updates this screen automatically

---

## 9. User Flows

### Primary Flow: Submit → Watch → Done

```
User opens app
      │
      ▼
┌──────────────────────────────┐
│  COMMAND CENTER               │
│                               │
│  1. Type what to build        │
│  2. Pick type (Feature/Bug)   │
│  3. Click [Submit Request]    │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│  REQUEST APPEARS IN LIST      │
│                               │
│  Pipeline bar: "Analyzing..."  │
│  Live feed: Engineering Lead   │
│                               │
│  User can:                    │
│  A) Stay here and watch bar   │
│  B) Click [View Details]      │
└──────┬───────────┬───────────┘
       │           │
  (stay)     (click detail)
       │           │
       ▼           ▼
┌────────────┐  ┌────────────────┐
│ Watch bar  │  │ REQUEST DETAIL  │
│ fill up    │  │ Full agent      │
│            │  │ timeline with   │
│ Good for   │  │ parallel work   │
│ most users │  │ and artifacts   │
└────────────┘  └───────┬────────┘
                        │
                        ▼
               ┌────────────────┐
               │ COMPLETED       │
               │ Summary:        │
               │  Coverage: 87%  │
               │  PRs: #44, #45  │
               │  Deployed: prod │
               └────────────────┘
```

### Secondary Flows

| Flow | Path |
|------|------|
| Check past work | Nav → History → click row → Request Detail |
| Check what's deployed | Nav → Releases → view pipeline + deploy log |
| Check agent activity | Nav → Team → view all agents at a glance |
| Retry failed request | History → find failed row → click `[ Retry ]` |

---

## 10. UI Components

### 10.1 Status Badge System

```
  🔵 Active       Blue pulsing dot     Agent or request actively executing
  ⬤  Idle         Gray solid dot       Agent ready, no current task
  ○  Waiting      Gray hollow dot      Waiting for dependency
  ✅ Done         Green check          Completed successfully
  ❌ Failed       Red X                Failed (with retry option)
  🔙 Rolled Back  Orange arrow         Deployment was rolled back
```

### 10.2 Pipeline Progress Bar

Adapts to workflow type:

```
Feature (6 stages):
[████████] > [████████] > [████░░░░] > [████░░░░] > [--------] > [--------]
 Planning     Stories      Backend      Frontend      Review       Deploy

Bug Fix (4 stages):
[████████] > [████████] > [██████░░] > [--------]
 Triage        Fix         Review+Test   Deploy

Documentation (2 stages):
[████████] > [████████]
 Draft          Stories
```

Parallel stages use `///` instead of `>>>`:
```
[ ✅ ] >>> [ ✅ ] >>> [ 🔵 ] /// [ 🔵 ] >>> [ ○ ] >>> [ ○ ]
 Reqs       Stories    Backend    Frontend    Review     Test
```

### 10.3 Request Card

```
┌──────────────────────────────────────────────────────────────────┐
│  ● REQ-042  Login page with JWT authentication                   │
│    12 min ago  ·  Feature  ·  High                               │
│                                                                   │
│    [Pipeline Progress Bar]                                        │
│                                                                   │
│    ┌─ Live ───────────────────────────────────────────────┐      │
│    │  🔵 Backend Specialist   Writing API routes...  12s  │      │
│    │  🔵 Frontend Specialist  Creating LoginForm...   8s  │      │
│    └──────────────────────────────────────────────────────┘      │
│                                                   [ View Details ]│
└──────────────────────────────────────────────────────────────────┘
```

### 10.4 Agent Card (Timeline)

```
┌──────────────────────────────────────────────────────────┐
│  🔵 Backend Specialist                            3:42   │
│  Writing JWT auth API routes                             │
│  Files: src/auth/routes.py, src/auth/jwt.py (+1)         │
│  Progress: ████████░░░  ~75%                             │
└──────────────────────────────────────────────────────────┘
```

### 10.5 Quality Gate Indicator

```
  ✅ Coverage Gate:  87% (threshold: 80%)     PASSED
  ❌ Coverage Gate:  72% (threshold: 80%)     FAILED → back to development
  ✅ Review Gate:    Approved                  PASSED
  ✅ All Tests:      47/47 passing             PASSED
```

### 10.6 Artifact Link

```
  📄 prd-login-feature.md        PRD Specialist       [View] [Copy]
  💻 src/auth/routes.py           Backend Specialist    [View] [Diff]
  🔗 PR #42                       Backend Specialist    [Open on GitHub]
```

---

## 11. Color System

| Status | Color | Hex | Background | Use |
|--------|-------|-----|-----------|-----|
| Active | Blue | #2563EB | #EFF6FF | Active agents, in-progress requests |
| Done | Green | #16A34A | #F0FDF4 | Completed work, passed gates |
| Failed | Red | #DC2626 | #FEF2F2 | Failed gates, errors |
| Waiting | Gray | #6B7280 | #F9FAFB | Idle agents, pending stages |
| Warning | Orange | #D97706 | #FFFBEB | Rollbacks, retries |

---

## 12. Real-Time Updates

### What Updates Live (WebSocket)

| Element | Update Trigger | Screen |
|---------|---------------|--------|
| Pipeline progress bar | Stage transition | Command Center, Request Detail |
| Live Activity feed | Every agent action | Command Center |
| Agent timeline | Agent starts/finishes | Request Detail |
| Agent progress % | Every 5 seconds | Request Detail parallel view |
| Agent status dots | Status change | Team Status |
| Quality gate results | Gate evaluation | Request Detail |

### WebSocket Events

```
request.created         → New card appears in Command Center
request.stage_changed   → Pipeline bar advances
request.completed       → Card moves to "Recently Completed"
request.failed          → Card shows failure state + retry option

agent.started           → New card in agent timeline
agent.progress          → Progress bar updates
agent.action            → Live Activity feed gets new line
agent.completed         → Agent card gets ✅ and duration
agent.failed            → Agent card gets ❌

gate.passed             → Green gate indicator appears
gate.failed             → Red gate indicator + on_fail routing shown

deploy.stage_changed    → Release Kanban card moves columns
deploy.completed        → Production column updates
```

---

## 13. Mobile Layout

The Command Center must work on mobile. Other screens are desktop-first.

```
┌───────────────────────────┐
│ ◆ Agent Team         [≡]  │
├───────────────────────────┤
│                           │
│ What would you like       │
│ to build?                 │
│                           │
│ ┌───────────────────────┐ │
│ │ Build a login page    │ │
│ │ with JWT auth...      │ │
│ └───────────────────────┘ │
│                           │
│ [Feature ▾]  [High ▾]    │
│ [    Submit Request    ]  │
│                           │
│ ── Active ──────────────  │
│                           │
│ ┌───────────────────────┐ │
│ │ ● REQ-042             │ │
│ │ Login page w/ JWT     │ │
│ │                       │ │
│ │ Planning    ████████  │ │
│ │ Dev         ████░░░░  │ │
│ │ Review      --------  │ │
│ │ Testing     --------  │ │
│ │ Deploy      --------  │ │
│ │                       │ │
│ │ 🔵 Backend: Writing   │ │
│ │    API routes...      │ │
│ │ 🔵 Frontend: Making   │ │
│ │    LoginForm.tsx...   │ │
│ │                       │ │
│ │       [Details]       │ │
│ └───────────────────────┘ │
│                           │
└───────────────────────────┘
```

- Pipeline bar goes **vertical** (one stage per row) — more readable on narrow screens
- Live Activity feed is already vertical — works naturally
- Hamburger menu `[≡]` for navigation on mobile
- Parallel agent cards **stack vertically** instead of side-by-side on Request Detail

---

## 14. API Surface

The frontend calls these endpoints (backed by the Orchestrator from `agent-invocation-design.md`):

```
REST:
  POST   /api/requests              Submit a new request
  GET    /api/requests              List requests (pagination, filters)
  GET    /api/requests/:id          Get request detail + agent timeline
  POST   /api/requests/:id/retry    Re-submit a failed request
  GET    /api/agents                Get all agent statuses
  GET    /api/releases              Get deployment history + health

WebSocket:
  WS     /ws/requests/:id           Subscribe to updates for one request
  WS     /ws/activity               Subscribe to all activity (Command Center)
```

---

## 15. Tech Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Framework | React + TypeScript | Component-based, strong ecosystem, matches frontend agent's domain |
| Styling | Tailwind CSS | Utility-first, no custom CSS architecture needed, fast to build |
| Real-time | WebSocket (native browser API) | Simple, lightweight, no library dependency |
| Routing | React Router | 5 flat routes, no nesting needed |
| State | React Context + useReducer | Simple app state; no need for Redux/Zustand at this scale |
| Charts | None | Intentionally excluded — numbers and progress bars are sufficient |

### Theme System

The application supports 6 selectable UI themes with dark/light mode indicators:

| Theme | Mode | Style |
|-------|------|-------|
| Linear | Dark | Purple accents, clean minimal |
| Vercel | Dark | Black & white, ultra-minimal |
| Discord | Dark | Blurple accents, channel-style |
| Flat Design | Light | Bold Metro colors, zero shadows |
| Brutalist | Light | Raw monospace, anti-design |
| Y2K | Dark | Neon cyan glow, retro-future |

Theme selection persists to localStorage. The theme selector in the navbar shows moon/sun icons indicating dark/light mode, with a dropdown organized into "Dark Themes" and "Light Themes" sections.

---

## 16. What Was Deliberately Excluded

| Feature | Why Excluded |
|---------|-------------|
| Manual task assignment | System auto-dispatches — adding assignment UI contradicts the core value |
| Agent config editor | YAML editing is the intended mechanism (see expansion-playbook.md) |
| Workflow editor | Same — YAML is the source of truth |
| Chat with agents | No back-and-forth needed — user describes, system executes |
| Gantt charts | Pipeline bar is sufficient; Gantt adds complexity without value |
| Custom dashboards | Fixed layout = no configuration needed = simpler |
| Notification preferences | Active requests are visible on Command Center; browser notifications are Phase 2 |
| Multi-user / permissions | Not in scope for v1 — single user interacting with the agent team |
| Dark mode | ~~Phase 2~~ **Implemented** — 6 themes available (Linear, Vercel, Discord, Flat, Brutalist, Y2K) with dark/light variants |

---

## 17. Related Documents

| Document | Relevance |
|----------|-----------|
| [architecture.md](architecture.md) | Team hierarchy, workflow DAGs, component design — UI must reflect these |
| [agent-invocation-design.md](agent-invocation-design.md) | Task lifecycle states, WebSocket events, Orchestrator API — UI data source |
| [prd-template.md](prd-template.md) | Task categories, quality thresholds, output formats |
| [expansion-playbook.md](expansion-playbook.md) | Team Status screen must dynamically render from config |
