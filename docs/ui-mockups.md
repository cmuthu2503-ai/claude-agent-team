# UI Mockups — 3 Design Versions
# Agent Team — Screen Comparison

---

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 1.0 |
| Created Date | 2026-04-05 |
| Status | Review — Pick one version before implementation |
| Product Owner | Chandramouli |

---

## Overview

Three distinct design approaches for the Agent Team UI. Each version shows the **Command Center** (primary screen) and **Request Detail** (secondary screen). Pick the one that feels right.

| Version | Style | Vibe |
|---------|-------|------|
| **A** | Chat-style | Like ChatGPT — conversational, input at bottom, results flow up |
| **B** | Dashboard-style | Like Linear/Jira — clean cards, structured grid, input at top |
| **C** | Terminal-style | Like Vercel/Railway — dark, minimal, developer-focused |

---

---

# VERSION A: Chat-Style

**Inspiration:** ChatGPT, Slack, Claude.ai
**Vibe:** Conversational. You "talk" to the agent team. Results appear as messages.

---

## A.1 — Command Center

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                                                                                  │
│                          ◆  A G E N T   T E A M                                 │
│                                                                                  │
│  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐                                        │
│  │ Home │  │ Past │  │ Ship │  │ Team │                                        │
│  └──────┘  └──────┘  └──────┘  └──────┘                                        │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│                                                                                  │
│     ┌─────────────────────────────────────────────────────────────────────┐      │
│     │                                                                     │      │
│     │  ✅  REQ-040 · Add dark mode toggle · Completed 2 hours ago        │      │
│     │  ─────────────────────────────────────────────────────────────────  │      │
│     │                                                                     │      │
│     │  Done! Here's what was built:                                       │      │
│     │                                                                     │      │
│     │  • Backend: Dark mode preference API (PR #63)                       │      │
│     │  • Frontend: Theme toggle component (PR #64)                        │      │
│     │  • Coverage: 91% · Tests: 34/34 passing                            │      │
│     │  • Deployed to production ✅                                        │      │
│     │                                                                     │      │
│     │  📄 PRD  📄 Stories  💻 PR #63  💻 PR #64                          │      │
│     │                                                                     │      │
│     └─────────────────────────────────────────────────────────────────────┘      │
│                                                                                  │
│                                                                                  │
│     ┌─────────────────────────────────────────────────────────────────────┐      │
│     │                                                                     │      │
│     │  🔵  REQ-041 · Fix broken pagination · 28 min ago                  │      │
│     │  ─────────────────────────────────────────────────────────────────  │      │
│     │                                                                     │      │
│     │  Triage  ━━━━━━━━  Fix  ━━━━━━━━  Review+Test  ━━━━░░░░  Deploy   │      │
│     │   done              done          in progress     waiting          │      │
│     │                                                                     │      │
│     │  💬 Code Reviewer: "Reviewing PR #67. Clean fix,               "   │      │
│     │     checking edge cases for page boundary..."          30s ago      │      │
│     │  💬 Tester: "Running regression suite... 42/47          "          │      │
│     │     tests complete."                                   15s ago      │      │
│     │                                                                     │      │
│     └─────────────────────────────────────────────────────────────────────┘      │
│                                                                                  │
│                                                                                  │
│     ┌─────────────────────────────────────────────────────────────────────┐      │
│     │                                                                     │      │
│     │  🔵  REQ-042 · Login page with JWT auth · 12 min ago              │      │
│     │  ─────────────────────────────────────────────────────────────────  │      │
│     │                                                                     │      │
│     │  Planning  ━━━━━━━━  Dev  ━━━━░░░░  Review  ────────  Deploy      │      │
│     │   done               building       waiting           waiting      │      │
│     │                                                                     │      │
│     │  💬 Backend Specialist: "Writing JWT token             "           │      │
│     │     generation in src/auth/jwt.py..."          12s ago             │      │
│     │  💬 Frontend Specialist: "Building LoginForm           "           │      │
│     │     component with email/password fields..."    8s ago             │      │
│     │                                                                     │      │
│     └─────────────────────────────────────────────────────────────────────┘      │
│                                                                                  │
│                                                                                  │
│                                                                                  │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │                                                                            │  │
│  │  What would you like to build?                                             │  │
│  │                                                                            │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│       [Feature ▾]    [High ▾]                                  [ Send  ▶ ]      │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### A.2 — Request Detail

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  ← Back                         ◆ Agent Team                                    │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  REQ-042 · Login page with JWT authentication                                    │
│  Feature · High · Started 12 min ago                                            │
│                                                                                  │
│  Planning  ━━━━━━━━  Dev  ━━━━░░░░  Review  ────────  Test  ────────  Deploy    │
│                                                                                  │
│  ─────────────────────────────────────────────────────────────────────────────   │
│                                                                                  │
│     ⚙ Engineering Lead                                              12:00       │
│     ───────────────────────────────────────────────────────────────              │
│     Analyzed request. Breaking into backend API + frontend page.                │
│     Delegating to Development Team and Delivery Team.                           │
│                                                                                  │
│                                                                                  │
│     📋 PRD Specialist                                         ✅    12:01       │
│     ───────────────────────────────────────────────────────────────              │
│     Created PRD with 6 requirements.                                            │
│                                                                                  │
│     📎 prd-login-feature.md                              [View] [Copy]          │
│                                                                                  │
│                                                                                  │
│     📝 User Story Author                                  ✅    12:03          │
│     ───────────────────────────────────────────────────────────────              │
│     5 user stories with acceptance criteria.                                    │
│                                                                                  │
│     📎 user-stories-login.md                             [View] [Copy]          │
│                                                                                  │
│                                                                                  │
│     ⚙ Code Reviewer                                              12:05         │
│     ───────────────────────────────────────────────────────────────              │
│     Splitting into parallel tracks: Backend (JWT API) + Frontend (Login UI)     │
│                                                                                  │
│                                                                                  │
│     ┌─────────────────────────────┐  ┌──────────────────────────────┐           │
│     │ 🔵 Backend Specialist       │  │ 🔵 Frontend Specialist       │           │
│     │                             │  │                              │           │
│     │ "Building JWT auth API...   │  │ "Creating login page with    │           │
│     │  Added POST /auth/login     │  │  email and password fields.  │           │
│     │  and POST /auth/register.   │  │  Adding form validation."    │           │
│     │  Now writing token          │  │                              │           │
│     │  validation middleware."    │  │  Files so far:               │           │
│     │                             │  │   src/pages/Login.tsx        │           │
│     │  Files so far:              │  │   src/components/            │           │
│     │   src/auth/routes.py        │  │     LoginForm.tsx            │           │
│     │   src/auth/jwt.py           │  │                              │           │
│     │   src/auth/middleware.py     │  │  ━━━━━━░░░░  ~60%           │           │
│     │                             │  │                              │           │
│     │  ━━━━━━━━░░░  ~75%         │  │                              │           │
│     └─────────────────────────────┘  └──────────────────────────────┘           │
│                                                                                  │
│     ○ Waiting: Code Review → Testing → Deployment                               │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### Version A — Key Traits

| Trait | Description |
|-------|-------------|
| Input position | **Bottom** — like a chat box, always accessible |
| Feed direction | Newest at bottom, scroll up for history |
| Agent updates | Shown as **quoted messages** ("💬 Backend: Writing JWT...") |
| Tone | Conversational — agents "talk" about what they're doing |
| Pipeline bar | Thin, inline within each request bubble |
| Color usage | Minimal — mostly monochrome with blue active accents |
| Best for | Users who prefer a **conversational** feel, similar to ChatGPT or Slack |

---

---

# VERSION B: Dashboard-Style

**Inspiration:** Linear, Notion, GitHub Projects
**Vibe:** Clean, structured, card-based. Clear visual hierarchy. Input at top.

---

## B.1 — Command Center

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  ◆ Agent Team                                                                    │
│                                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │  Command Center     History     Releases     Team                          │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌─ New Request ──────────────────────────────────────────────────────────────┐  │
│  │                                                                            │  │
│  │  ┌──────────────────────────────────────────────────────────────────────┐  │  │
│  │  │  Describe what you want to build...                                  │  │  │
│  │  │                                                                      │  │  │
│  │  │                                                                      │  │  │
│  │  └──────────────────────────────────────────────────────────────────────┘  │  │
│  │                                                                            │  │
│  │  ┌───────────┐  ┌───────────┐                      ┌──────────────────┐   │  │
│  │  │ ● Feature │  │ ◐ High    │                      │  Submit Request  │   │  │
│  │  │   Bug Fix │  │   Medium  │                      │        →         │   │  │
│  │  │   Docs    │  │   Low     │                      └──────────────────┘   │  │
│  │  │   Demo    │  └───────────┘                                             │  │
│  │  │   Spike   │                                                            │  │
│  │  └───────────┘                                                            │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  ┌─ Active (2) ───────────────────────────────────────────────────────────────┐  │
│  │                                                                            │  │
│  │  ┌──────────────────────────────────┐  ┌──────────────────────────────────┐│  │
│  │  │                                  │  │                                  ││  │
│  │  │  REQ-042                         │  │  REQ-041                         ││  │
│  │  │  Login page with JWT auth        │  │  Fix broken pagination           ││  │
│  │  │  Feature · High · 12 min         │  │  Bug Fix · High · 28 min         ││  │
│  │  │                                  │  │                                  ││  │
│  │  │  ┌──────────────────────────┐    │  │  ┌──────────────────────────┐    ││  │
│  │  │  │ Planning       ████████ │    │  │  │ Triage         ████████ │    ││  │
│  │  │  │ Development    ████░░░░ │    │  │  │ Fix            ████████ │    ││  │
│  │  │  │ Review         ──────── │    │  │  │ Review + Test  ██████░░ │    ││  │
│  │  │  │ Testing        ──────── │    │  │  │ Deploy         ──────── │    ││  │
│  │  │  │ Deployment     ──────── │    │  │  └──────────────────────────┘    ││  │
│  │  │  └──────────────────────────┘    │  │                                  ││  │
│  │  │                                  │  │  Agents:                          ││  │
│  │  │  Agents:                         │  │  🔵 Code Reviewer                ││  │
│  │  │  🔵 Backend Specialist           │  │  🔵 Tester Specialist            ││  │
│  │  │     Writing API routes...        │  │                                  ││  │
│  │  │  🔵 Frontend Specialist          │  │                                  ││  │
│  │  │     Creating LoginForm...        │  │                                  ││  │
│  │  │                                  │  │                                  ││  │
│  │  │  ─────────────────────────────   │  │  ─────────────────────────────   ││  │
│  │  │  [ View Details ]                │  │  [ View Details ]                ││  │
│  │  │                                  │  │                                  ││  │
│  │  └──────────────────────────────────┘  └──────────────────────────────────┘│  │
│  │                                                                            │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  ┌─ Recently Completed ──────────────────────────────────────────────────────┐   │
│  │                                                                            │  │
│  │  ┌──────────────────────────────────┐  ┌──────────────────────────────────┐│  │
│  │  │ ✅ REQ-040                       │  │ ✅ REQ-039                       ││  │
│  │  │ Dark mode toggle                 │  │ API rate limiting                ││  │
│  │  │                                  │  │                                  ││  │
│  │  │ Coverage  PRs      Deployed      │  │ Coverage  PR      Deployed       ││  │
│  │  │ 91%       #63,#64  Production ✅ │  │ 88%       #61     Production ✅  ││  │
│  │  │                                  │  │                                  ││  │
│  │  └──────────────────────────────────┘  └──────────────────────────────────┘│  │
│  │                                                                            │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### B.2 — Request Detail

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  ◆ Agent Team          Command Center   History   Releases   Team               │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ← Back to Command Center                                                       │
│                                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │  REQ-042                                                                   │  │
│  │  Login page with JWT authentication                                        │  │
│  │  Feature · High · 12 min ago · Chandramouli                               │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  ┌─ Pipeline ─────────────────────────────────────────────────────────────────┐  │
│  │                                                                            │  │
│  │   ✅          ✅          🔵            🔵           ○          ○          │  │
│  │  Require-    Stories    Backend       Frontend     Review     Testing      │  │
│  │  ments                                                                     │  │
│  │  1:42        2:18       ~75%          ~60%         wait       wait         │  │
│  │                          ╰─── parallel ───╯                                │  │
│  │                                                                            │  │
│  │                                                              ○             │  │
│  │                                                            Deploy          │  │
│  │                                                             wait           │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  ┌─ Timeline ──────────────────────┐  ┌─ Outputs ─────────────────────────────┐ │
│  │                                  │  │                                       │ │
│  │  12:06   PARALLEL EXECUTION      │  │  📄 prd-login-feature.md     [View]  │ │
│  │  ┌────────────────────────────┐  │  │     PRD Specialist                   │ │
│  │  │ 🔵 Backend Specialist      │  │  │                                       │ │
│  │  │ Writing JWT auth API       │  │  │  📄 user-stories-login.md    [View]  │ │
│  │  │                            │  │  │     User Story Author                │ │
│  │  │ src/auth/routes.py         │  │  │                                       │ │
│  │  │ src/auth/jwt.py            │  │  │  (more outputs appear as agents      │ │
│  │  │ src/auth/middleware.py     │  │  │   complete their work)               │ │
│  │  │                            │  │  │                                       │ │
│  │  │ ━━━━━━━━░░░  ~75%         │  │  ├───────────────────────────────────────┤ │
│  │  └────────────────────────────┘  │  │                                       │ │
│  │  ┌────────────────────────────┐  │  │  Quality Gates                       │ │
│  │  │ 🔵 Frontend Specialist     │  │  │                                       │ │
│  │  │ Creating login page UI     │  │  │  ○ Coverage ≥ 80%       Pending      │ │
│  │  │                            │  │  │  ○ Review approval      Pending      │ │
│  │  │ src/pages/Login.tsx        │  │  │  ○ All tests pass       Pending      │ │
│  │  │ src/components/            │  │  │  ○ Smoke tests pass     Pending      │ │
│  │  │   LoginForm.tsx            │  │  │                                       │ │
│  │  │                            │  │  │                                       │ │
│  │  │ ━━━━━━░░░░  ~60%          │  │  │                                       │ │
│  │  └────────────────────────────┘  │  │                                       │ │
│  │                                  │  │                                       │ │
│  │  12:05   Code Reviewer           │  │                                       │ │
│  │  Decomposed into Backend +       │  │                                       │ │
│  │  Frontend tracks.                │  │                                       │ │
│  │                                  │  │                                       │ │
│  │  12:03   User Story Author  ✅   │  │                                       │ │
│  │  5 user stories created.         │  │                                       │ │
│  │                                  │  │                                       │ │
│  │  12:01   PRD Specialist     ✅   │  │                                       │ │
│  │  PRD with 6 requirements.        │  │                                       │ │
│  │                                  │  │                                       │ │
│  │  12:00   Engineering Lead        │  │                                       │ │
│  │  Analyzed. Domains: backend,     │  │                                       │ │
│  │  frontend, auth, testing.        │  │                                       │ │
│  │                                  │  │                                       │ │
│  └──────────────────────────────────┘  └───────────────────────────────────────┘ │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### Version B — Key Traits

| Trait | Description |
|-------|-------------|
| Input position | **Top** — prominent card with visible type/priority selectors |
| Layout | **Card grid** — active requests as side-by-side cards |
| Pipeline bar | **Vertical** within each card — shows stages as a stacked list |
| Detail view | **Two-column** — timeline on left, outputs + gates on right |
| Color usage | White cards on light gray background, colored status indicators |
| Agent updates | Compact — agent name + one-line status + progress bar |
| Best for | Users who prefer a **structured, scannable** layout like Linear or Notion |

---

---

# VERSION C: Terminal-Style

**Inspiration:** Vercel, Railway, GitHub Actions
**Vibe:** Dark, minimal, developer-focused. Feels like watching a CI/CD pipeline run.

---

## C.1 — Command Center

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                                                                                  │
│  ▌Agent Team                            Command Center  History  Releases  Team  │
│                                                                                  │
├──────────────────────────────────────────────────────────────────────────────────┤
│  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │
│                                                                                  │
│  > What would you like to build?                                                │
│  ┌──────────────────────────────────────────────────────────────────────────┐    │
│  │ Build a user profile page with name, email, avatar and edit form_       │    │
│  └──────────────────────────────────────────────────────────────────────────┘    │
│  feature · high                                              [ Submit ⏎ ]       │
│                                                                                  │
│  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │
│                                                                                  │
│  ● REQ-042 · Login page with JWT auth · 12m · feature                           │
│  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄    │
│                                                                                  │
│  planning ━━━━━━━━━━━━━━━━━━━━  done                                            │
│  development ━━━━━━━━━━━░░░░░░  building                                        │
│  review ─────────────────────   waiting                                          │
│  testing ────────────────────   waiting                                          │
│  deploy ─────────────────────   waiting                                          │
│                                                                                  │
│   12:06:12  backend-specialist   writing src/auth/routes.py                      │
│   12:06:08  frontend-specialist  creating src/pages/Login.tsx                    │
│   12:05:23  code-reviewer        decomposed → backend + frontend (parallel)      │
│   12:03:05  user-story-author    ✓ 5 stories created                            │
│   12:01:23  prd-specialist       ✓ prd-login-feature.md                         │
│   12:00:21  engineering-lead     ✓ analyzed → dev-team, delivery-team            │
│                                                                       [details]  │
│                                                                                  │
│  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄    │
│                                                                                  │
│  ● REQ-041 · Fix broken pagination · 28m · bugfix                               │
│  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄    │
│                                                                                  │
│  triage ━━━━━━━━━━━━━━━━━━━━━━  done                                            │
│  fix ━━━━━━━━━━━━━━━━━━━━━━━━━  done                                            │
│  review+test ━━━━━━━━━━━░░░░░░  in progress                                     │
│  deploy ─────────────────────   waiting                                          │
│                                                                                  │
│   12:32:45  code-reviewer        reviewing PR #67                                │
│   12:32:30  tester-specialist    regression suite 42/47 passing                  │
│                                                                       [details]  │
│                                                                                  │
│  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄    │
│                                                                                  │
│  ✓ REQ-040 · Dark mode toggle · 2h ago · 91% · #63,#64 · production             │
│  ✓ REQ-039 · API rate limiting · 5h ago · 88% · #61 · production                │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### C.2 — Request Detail

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  ▌Agent Team             ← back                                                 │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  REQ-042 · login page with JWT authentication                                    │
│  feature · high · 12m · chandramouli                                            │
│                                                                                  │
│  planning ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  done     1:42                       │
│  development ━━━━━━━━━━━━━━━━━━━━━░░░░░░░░  building                            │
│  review ──────────────────────────────────  waiting                              │
│  testing ─────────────────────────────────  waiting                              │
│  deploy ──────────────────────────────────  waiting                              │
│                                                                                  │
│  ═══════════════════════════════════════════════════════════════════════════════  │
│                                                                                  │
│  ┌─ engineering-lead ────────────────────────────────────────  ✓ 1:02 ──────┐   │
│  │                                                                           │   │
│  │  > analyzed request                                                       │   │
│  │  > domains: backend, frontend, auth, testing                              │   │
│  │  > delegating to: code-reviewer (dev), devops-specialist (delivery)       │   │
│  │                                                                           │   │
│  └───────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│  ┌─ prd-specialist ──────────────────────────────────────────  ✓ 1:42 ──────┐   │
│  │                                                                           │   │
│  │  > created PRD document with 6 requirements                               │   │
│  │  > output: prd-login-feature.md                              [view] [raw] │   │
│  │                                                                           │   │
│  └───────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│  ┌─ user-story-author ───────────────────────────────────────  ✓ 2:18 ──────┐   │
│  │                                                                           │   │
│  │  > created 5 user stories with acceptance criteria                        │   │
│  │  > output: user-stories-login.md                             [view] [raw] │   │
│  │                                                                           │   │
│  └───────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│  ┌─ code-reviewer ───────────────────────────────────────────  ✓ 0:45 ──────┐   │
│  │                                                                           │   │
│  │  > decomposed into parallel tracks                                        │   │
│  │  > backend-specialist: JWT auth API endpoints                             │   │
│  │  > frontend-specialist: login page UI                                     │   │
│  │                                                                           │   │
│  └───────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│  ┌─ parallel ────────────────────────────────────────────────  running ──────┐   │
│  │                                                                           │   │
│  │  ┌─ backend-specialist ──────────┐  ┌─ frontend-specialist ─────────┐    │   │
│  │  │                               │  │                               │    │   │
│  │  │  > src/auth/routes.py    new  │  │  > src/pages/Login.tsx   new  │    │   │
│  │  │  > src/auth/jwt.py       new  │  │  > src/components/       new  │    │   │
│  │  │  > src/auth/middleware   new   │  │      LoginForm.tsx            │    │   │
│  │  │  > tests/test_auth.py   new   │  │  > tests/Login.test.tsx  new  │    │   │
│  │  │                               │  │                               │    │   │
│  │  │  ━━━━━━━━━━━━━━░░░░  75%     │  │  ━━━━━━━━━━━░░░░░░░  60%     │    │   │
│  │  │                               │  │                               │    │   │
│  │  └───────────────────────────────┘  └───────────────────────────────┘    │   │
│  │                                                                           │   │
│  └───────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│  ┌─ gates ───────────────────────────────────────────────────  pending ──────┐   │
│  │                                                                           │   │
│  │  ○  coverage ≥ 80%           pending                                      │   │
│  │  ○  review approved          pending                                      │   │
│  │  ○  all tests pass           pending                                      │   │
│  │  ○  smoke tests pass         pending                                      │   │
│  │                                                                           │   │
│  └───────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### Version C — Key Traits

| Trait | Description |
|-------|-------------|
| Input position | **Top** — single-line with `>` prompt, like a terminal |
| Background | **Dark** — dark gray/black background, light text |
| Pipeline bar | **Full-width horizontal bars** — each stage is its own row with progress fill |
| Log style | **Timestamped log entries** — feels like watching CI/CD output |
| Agent names | **kebab-case** (backend-specialist, not Backend Specialist) — developer feel |
| Detail blocks | **Bordered blocks** with agent name in header — like GitHub Actions job steps |
| File changes | Listed with `new`/`modified` tags — like a git diff summary |
| Color usage | Green for success, blue for active, dim for waiting. Minimal color. |
| Best for | Users who prefer a **developer-focused, CI/CD pipeline** aesthetic |

---

---

# COMPARISON SUMMARY

## Side-by-Side

| Aspect | Version A (Chat) | Version B (Dashboard) | Version C (Terminal) |
|--------|------------------|----------------------|---------------------|
| **Input position** | Bottom | Top (card) | Top (prompt line) |
| **Feed direction** | Newest at bottom | Cards in grid | Log entries top-down |
| **Active request display** | Stacked bubbles | Side-by-side cards | Stacked log blocks |
| **Pipeline visualization** | Inline thin bar | Vertical stage list in card | Full-width horizontal bars |
| **Agent activity style** | Chat messages ("💬") | Compact one-liners | Timestamped log lines |
| **Detail view layout** | Single column feed | Two-column (timeline + outputs) | Single column log blocks |
| **Parallel work display** | Side-by-side cards in feed | Side-by-side cards in timeline | Side-by-side terminal blocks |
| **Visual density** | Low — spacious | Medium — structured | High — information-dense |
| **Theme** | Light, warm | Light, clean | Dark, minimal |
| **Best audience** | Non-technical users, PMs | Mixed teams (devs + PMs) | Developers, DevOps |

## Decision Factors

| If you want... | Choose |
|----------------|--------|
| Feels familiar to ChatGPT/Claude users | **Version A** |
| Most scannable with multiple active requests | **Version B** |
| Feels like watching a CI/CD pipeline | **Version C** |
| Best for non-developers | **Version A** |
| Best for mixed dev + PM teams | **Version B** |
| Best for pure developer teams | **Version C** |
| Easiest to build (least custom CSS) | **Version B** |
| Most unique/distinctive | **Version C** |

---

## Mobile Comparison

| Aspect | Version A | Version B | Version C |
|--------|-----------|-----------|-----------|
| Mobile-friendly? | ✅ Naturally — chat is mobile-native | ⚠️ Cards need to stack | ✅ Log format works on narrow screens |
| Input on mobile | Bottom sheet (like iMessage) | Top card (scrolls away) | Top prompt (sticky) |
| Pipeline on mobile | Inline thin bar works | Vertical list works | Full-width bars work |

---

## Pick One

Review the mockups above and choose the version that feels right for your team. The chosen version will be the basis for the production UI implementation.
