# Agent Invocation Design
# Auto-Dispatch (Push-Based) Task Routing

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

## 1. Overview

The Agent Team uses a **push-based auto-dispatch model**. When a development task is submitted, the Engineering Lead automatically analyzes the request, decomposes it into subtasks, and routes each subtask to the appropriate agent based on domain analysis and delegation rules. No manual task assignment or task board is required.

### 1.1 Core Principle

```
User describes WHAT to build  ──►  System decides WHO does it and HOW
```

The user never needs to know which agent handles what. They submit a request in natural language, and the system handles decomposition, routing, execution, and result aggregation automatically.

---

## 2. End-to-End Invocation Flow

### 2.1 Complete Flow Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                        AGENT INVOCATION FLOW                                 │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────���───────┐                                                             │
│  │  USER INPUT  │  "Build a login page with JWT authentication"              │
│  └──────┬──────┘                                                             │
│         │                                                                    │
│         ▼                                                                    │
│  ┌─────────────────────────────────────────────┐                             │
│  │  STEP 1: INTAKE                              │                            │
│  │  Orchestrator receives the request            │                            │
│  │  Creates root Task object with unique ID      │                            │
│  │  Routes to Engineering Lead                   │                            │
│  └──────┬──────────────────────────────────────┘                             │
│         │                                                                    │
│         ▼                                                                    │
│  ┌─────────────────────────────────────────────┐                             │
│  │  STEP 2: ANALYSIS & DECOMPOSITION            │                            │
│  │  Engineering Lead analyzes the request        │                            │
│  │  Identifies domains: [backend, frontend,      │                            │
│  │    auth, testing]                             │                            │
│  │  Generates Delegation Plan (JSON)             │                            │
│  └──────┬──────────────────────────────────────┘                             │
│         │                                                                    │
│         ▼                                                                    │
│  ┌─────────────────────────────────────────────┐                             │
│  │  STEP 3: DELEGATION PLAN                      │                            │
│  │                                               │                            │
│  │  {                                            │                            │
│  │    "subtasks": [                              │                            │
│  │      {                                        │                            │
│  │        "delegate_to": "code_reviewer",        │                            │
│  │        "summary": "Implement login feature    │                            │
│  │          with JWT auth (backend + frontend)", │                            │
│  │        "priority": "high",                    │                            │
│  │        "domains": ["backend", "frontend",     │                            │
│  │          "auth"]                              │                            │
│  │      },                                       │                            │
│  │      {                                        │                            │
│  │        "delegate_to": "devops_specialist",    │                            │
│  │        "summary": "Test and deploy login      │                            │
│  │          feature",                            │                            │
│  │        "priority": "high",                    │                            │
│  │        "domains": ["testing", "deployment"],  │                            │
│  │        "depends_on": ["subtask_1"]            │                            │
│  │      }                                        │                            │
│  │    ]                                          │                            │
│  │  }                                            │                            │
│  └──────┬──────────────────────────────────────┘                             │
│         │                                                                    │
│         ▼                                                                    │
│  ┌─────────────────────────────────────────────┐                             │
│  │  STEP 4: DISPATCHER VALIDATES                 │                            │
│  │  ✓ code_reviewer is a direct report of        │                            │
│  │    engineering_lead                           │                            │
│  │  ✓ devops_specialist is a direct report of    │                            │
│  │    engineering_lead                           │                            │
│  │  ✓ dependency chain is valid (no cycles)      │                            │
│  └──────┬──────────────────────────────────────┘                             │
│         │                                                                    │
│         ▼                                                                    │
│  ┌─────────────────────────────────────────────┐                             │
│  │  STEP 5: TEAM LEADS FURTHER DECOMPOSE        │                            │
│  │                                               │                            │
│  │  Code Reviewer (Dev Lead) decomposes:         │                            │
│  │  ┌─────────────────────────���───────────┐      │                            │
│  │  │ → backend_specialist:               │      │                            │
│  │  │   "Build JWT auth REST API          │      │                            │
│  │  │    - POST /auth/login               │      │                            │
│  │  │    - POST /auth/register            │      │                            │
│  │  │    - GET /auth/me                   │      │                            │
│  │  │    - JWT token generation/validation"│      │                            │
│  │  │                                     │      │                            │
│  │  │ → frontend_specialist:              │      │                            │
│  │  │   "Build login page UI              │      │                            │
│  │  │    - Login form component           │      │                            │
│  │  │    - Registration form component    │      │                            │
│  │  │    - Auth state management          │      │                            │
│  │  │    - Protected route wrapper"       │      │                            │
│  │  └─────────────────────────────────────┘      │                            │
│  │                                               │                            │
│  │  DevOps Specialist (Delivery Lead) decomposes:│                            │
│  │  ┌─────────────────────────────────────┐      │                            │
│  │  │ → tester_specialist:                │      │                            │
│  │  │   "Write auth E2E tests             │      │                            │
│  │  │    - Login flow test                │      │                            │
│  │  │    - Registration flow test         │      │                            │
│  │  │    - JWT expiry test                │      │                            │
│  │  │    - Protected route access test"   │      │                            │
│  │  └─────────────────────────────────────┘      │                            │
│  └──────┬──────────────────────────────────────┘                             │
│         │                                                                    │
│         ▼                                                                    │
│  ┌─────────────────────────────────────────────┐                             │
│  │  STEP 6: AGENTS EXECUTE (PARALLEL)            │                            │
│  │                                               │                            │
│  │  ┌──────────────────┐ ┌──────────────────┐    │                            │
│  │  │ Backend Spec.     │ │ Frontend Spec.    │   │  ◄── PARALLEL              │
│  │  │                   │ │                   │   │                            │
│  │  │ 1. Read user story│ │ 1. Read user story│   │                            │
│  │  │ 2. Write API code │ │ 2. Write UI code  │   │                            │
│  │  │ 3. Write tests    │ │ 3. Write tests    │   │                            │
│  │  │ 4. Run tests      │ │ 4. Run tests      │   │                            │
│  │  │ 5. Commit & PR    │ │ 5. Commit & PR    │   │                            │
│  │  └────────┬─────────┘ └────────┬─────────┘    │                            │
│  │           └──────���───┬─────────┘               │                            │
│  │                      ▼                         │                            │
│  │           ┌──────────────────┐                 │                            │
│  │           │ Code Reviewer     │                │  ◄── QUALITY GATE          │
│  │           │ Reviews both PRs  │                │                            │
│  │           │ Checks coverage   │                │                            │
│  │           └────────┬─────────┘                 │                            │
│  │                    │ Approved                   │                            │
│  │                    ▼                            │                            │
│  │           ┌──────────────────┐                 │                            │
│  │           │ Tester Spec.      │                │  ◄── DEPENDS_ON: dev done  │
│  │           │ Runs E2E tests    │                │                            │
│  │           └────────┬─────────┘                 │                            │
│  │                    │ Tests pass                 │                            │
│  │                    ▼                            │                            │
│  │           ┌──────────────────┐                 │                            │
│  │           │ DevOps Spec.      │                │  ◄── DEPLOYS               │
│  │           │ Deploy to staging │                │                            │
│  │           │ Run smoke tests   │                │                            │
│  │           │ Deploy to prod    │                │                            │
│  │           └────────┬─────────┘                 │                            │
│  └────────────────────┼────────────────────────────┘                             │
│                       │                                                      │
│                       ▼                                                      │
│  ┌─────────────────────────────────────────────┐                             │
│  │  STEP 7: AGGREGATION                         │                            │
│  │                                               │                            │
│  │  Results flow upward:                         │                            │
│  │  Backend Spec.  ──► Code Reviewer (aggregates)│                            │
│  │  Frontend Spec. ──► Code Reviewer (aggregates)│                            │
│  │  Tester Spec.   ──► DevOps Spec. (aggregates) │                            │
│  │                                               │                            │
│  │  Code Reviewer  ──► Engineering Lead           │                            │
│  │  DevOps Spec.   ──► Engineering Lead           │                            │
│  │                                               │                            │
│  │  Engineering Lead synthesizes final response   │                            │
│  └──────┬──────────────────────────────────────┘                             │
│         │                                                                    │
│         ▼                                                                    │
│  ┌─────────────┐                                                             │
│  │  USER OUTPUT │  "Login feature complete. Backend API at /auth/*,          │
│  │              │   React login page deployed. 94% coverage.                 │
│  │              │   Staging: http://localhost:3010/login                       │
│  │              │   PRs: #42 (backend), #43 (frontend)"                      │
│  └─────────────┘                                                             │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. How Auto-Dispatch Routing Works

### 3.1 Domain-Based Routing

The Engineering Lead (and team leads) use **domain analysis** to decide which agent handles each subtask. This is NOT keyword matching ��� it's LLM-powered reasoning using the team and agent definitions.

#### How the Engineering Lead Decides

The Engineering Lead's system prompt includes:

1. **The full team hierarchy** (from `config/teams.yaml`)
2. **Each team's domain tags** (e.g., Development: `[backend, frontend, api, database, ui, code-review]`)
3. **Each agent's responsibilities** (from `config/agents/*.yaml`)
4. **Delegation rules** (who it can delegate to)

When a request arrives, the Engineering Lead:

```
1. ANALYZES the request to identify required domains
   "Build a login page with JWT auth"
   → Domains: backend (API), frontend (UI), auth (security), testing

2. MATCHES domains to teams
   → backend + frontend + auth  →  Development Team (lead: code_reviewer)
   → testing + deployment       →  Delivery Team (lead: devops_specialist)

3. GENERATES delegation plan as structured JSON
   → Subtask 1: code_reviewer handles development (backend + frontend)
   → Subtask 2: devops_specialist handles delivery (testing + deploy)
   → Subtask 2 depends on Subtask 1

4. DISPATCHER validates and dispatches
```

#### How Team Leads Further Route

Team leads receive their subtask and further decompose using the same pattern:

```
Code Reviewer receives: "Implement login feature with JWT auth"
   → backend domain tasks  →  backend_specialist
   → frontend domain tasks →  frontend_specialist
   → These run in PARALLEL (no dependency between them)
   → Code Reviewer reviews AFTER both complete (quality gate)
```

### 3.2 Routing Decision Matrix

| Request Contains | Routes To | Via |
|---|---|---|
| API, database, server, backend | Backend Specialist | Engineering Lead → Code Reviewer �� Backend Spec. |
| UI, page, component, frontend | Frontend Specialist | Engineering Lead → Code Reviewer → Frontend Spec. |
| API + UI (full-stack) | Backend + Frontend (parallel) | Engineering Lead → Code Reviewer → Both in parallel |
| Deploy, CI/CD, infrastructure | DevOps Specialist | Engineering Lead → DevOps Spec. |
| Test, coverage, QA | Tester Specialist | Engineering Lead → DevOps Spec. → Tester Spec. |
| PRD, requirements, spec | PRD Specialist | Engineering Lead → PRD Spec. |
| User story, acceptance criteria | User Story Author | Engineering Lead → PRD Spec. → User Story Author |
| Full feature (end-to-end) | All teams | Engineering Lead → All three team leads |

### 3.3 Dependency Resolution

The dispatcher enforces execution order based on `depends_on` declarations:

```
Execution Timeline:
────────────────────────────────────────────────────────────►  time

Phase 1 (PARALLEL):
  ├── Backend Specialist: Build JWT API        ████████████
  └── Frontend Specialist: Build login UI      ████████████

Phase 2 (SEQUENTIAL - depends on Phase 1):
  └── Code Reviewer: Review both PRs                       ████

Phase 3 (SEQUENTIAL - depends on Phase 2):
  └── Tester Specialist: Run E2E tests                          ████

Phase 4 (SEQUENTIAL - depends on Phase 3):
  └── DevOps Specialist: Deploy                                      ███
```

Independent tasks run **in parallel**. Dependent tasks **wait** for their prerequisites.

---

## 4. Agent Execution Model

### 4.1 What Happens When an Agent Is Invoked

Each agent, when it receives a task, follows this execution loop:

```
AGENT RECEIVES TASK
        │
        ▼
┌───────────────────────────────────────────────┐
│  1. LOAD CONTEXT                               │
│     - Read system prompt (from YAML config)     │
│     - Read task description                     │
│     - Read any input artifacts from prior stages │
│     - Load available tools                      │
└───────┬───────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────┐
│  2. PLAN                                        │
│     - Agent reasons about how to accomplish task│
│     - Identifies which tools to use             │
│     - Creates an execution plan                 │
└───────┬───────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────┐
│  3. EXECUTE (iterative tool-use loop)           │
│                                                 │
│     ┌──► Call LLM with tools ──► Get response   │
│     │         │                                 │
│     │         ├── Tool call requested?           │
│     │         │   YES → Execute tool → Feed      │
│     │         │         result back to LLM ──┐   │
│     │         │                              │   │
│     │         └── NO (final answer)          │   │
│     │              │                         │   │
│     │              ▼                         │   │
│     │         Return TaskResult              │   │
│     │                                        │   │
│     └────────────────────────────────────────┘   │
│                                                 │
│  Example for Backend Specialist:                │
│    LLM call 1: "I'll create the auth module"    │
│      → tool: file_write("src/auth/routes.py")   │
│    LLM call 2: "Now the JWT utility"            │
│      → tool: file_write("src/auth/jwt.py")      │
│    LLM call 3: "Write tests"                    │
│      → tool: file_write("tests/test_auth.py")   │
│    LLM call 4: "Run tests"                      │
│      → tool: test_runner("pytest tests/")        │
│    LLM call 5: "Tests pass. Commit and PR."     │
│      → tool: git_operations("commit + push")     │
│      → tool: github_pr("Create PR #42")          │
│    LLM call 6: "Done. PR #42 ready for review." │
│      → Return TaskResult                        │
└───────────────────────────────────────────────┘
```

### 4.2 Current Agent Execution Mode

All agents operate in **text-only mode** (tools: []). The LLM receives the full pipeline context and produces structured text output directly. This design choice was made because:

1. Tool-equipped agents entered explore loops (calling file_read repeatedly) without producing output
2. Rate limits (30k tokens/min) made multi-round tool interactions impractical
3. Text-only output ensures every agent produces a complete, visible deliverable
4. The pipeline context grows as each agent adds their output — downstream agents see everything

The Backend and Frontend Specialists produce code as formatted text with file paths:

```
### `path/to/file.py`
```python
[complete file contents]
```
```

These code outputs are reviewed by the Code Reviewer and tested by the Tester Specialist based on the text content.

### 4.3 Tool Execution During Agent Tasks (Original Design — Superseded)

> **Note**: The tool-based execution model described below has been **superseded** by the text-only mode above. It is retained for historical reference.

Each agent originally had access to specific tools (defined in `config/tools.yaml`). When an agent was invoked, it could use tools to interact with the real system:

| Tool | What It Does | Originally Used By |
|------|-------------|---------|
| `file_read` | Read source files, configs, docs | All agents |
| `file_write` | Create/modify source files | Backend, Frontend, DevOps, Tester, PRD, User Story |
| `git_operations` | Branch, commit, push, diff | Backend, Frontend, DevOps, Tester, Code Reviewer |
| `code_exec` | Run code in sandboxed environment | Backend, Frontend, Tester |
| `test_runner` | Execute test suites (pytest, jest, etc.) | Tester, Backend, Frontend |
| `github_pr` | Create PRs, post comments, approve | Code Reviewer |
| `code_analysis` | Run linters, formatters, static analysis | Code Reviewer, Backend, Frontend |
| `coverage_report` | Generate/read coverage reports | Code Reviewer, Tester |
| `deployment` | Deploy via Docker Compose (build, up, down, rollback) | DevOps |
| `ci_cd_pipeline` | Create/manage GitHub Actions workflows | DevOps |

### 4.4 Agent Communication — Artifact Passing

Agents don't talk to each other directly. Instead, they produce **artifacts** that become inputs for the next stage:

```
Backend Specialist produces:
  ├── backend_code: "src/auth/*.py" (file paths)
  ├── backend_tests: "tests/test_auth.py" (file paths)
  ├── api_docs: "docs/api/auth.md" (file path)
  └── pr_number: "#42" (GitHub PR reference)

        │
        │  These artifacts are passed as inputs to...
        ▼

Code Reviewer receives:
  ├── backend_code → Reviews these files
  ├── backend_tests → Checks test quality
  ├── frontend_code → Reviews these files (from Frontend Spec.)
  ├── frontend_tests → Checks test quality
  └── Produces: review_report (approval + comments)

        │
        ▼

Tester Specialist receives:
  ├── backend_code → Knows what to test
  ├── frontend_code → Knows what to test
  └── review_report → Knows what was flagged
```

---

## 5. Invocation Entry Points

### 5.1 How a Task Enters the System

The orchestrator exposes a single entry point. Tasks can be submitted through multiple channels:

```
                    ┌──────────────────────────┐
                    │     ENTRY POINTS          │
                    ├──────────────────────────┤
                    │                          │
                    │  CLI Command             │
                    │  $ agent-team run        │
                    │    "Build login page     │
                    │     with JWT auth"       │
                    │                          │
                    │  Python API              │
                    │  orchestrator.submit(    │
                    │    "Build login page..." │
                    │  )                       │
                    │                          │
                    │  Claude Code (Phase 1)   │
                    │  User types request,     │
                    │  Claude Code spawns      │
                    │  Engineering Lead agent  │
                    │                          │
                    └────────────┬─────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │     ORCHESTRATOR          │
                    │  orchestrator.submit()    │
                    │                          │
                    │  1. Create root Task      │
                    │  2. Send to Eng. Lead     │
                    │  3. Manage lifecycle      │
                    │  4. Return final result   │
                    └──────────────────────────┘
```

### 5.2 Phase 1 Entry Point — Claude Code

During prototyping, the user interacts through Claude Code:

```
User (in Claude Code):
  "Build a login page with JWT authentication"

Claude Code (acting as Orchestrator):
  1. Reads config/agents/engineering_lead.yaml for system prompt
  2. Spawns Engineering Lead as a sub-agent
  3. Engineering Lead returns delegation plan
  4. Claude Code spawns Backend Specialist sub-agent (with its YAML prompt)
  5. Claude Code spawns Frontend Specialist sub-agent (with its YAML prompt)
  6. Both execute in parallel (file writes, git commits, etc.)
  7. Claude Code spawns Code Reviewer sub-agent (reviews PRs)
  8. Claude Code spawns Tester sub-agent (runs tests)
  9. Claude Code spawns DevOps sub-agent (deploys)
  10. Results aggregated and returned to user
```

### 5.3 Phase 2 Entry Point — Python CLI / API

In production, the orchestrator runs as a Python process:

```bash
# CLI invocation
$ python -m agent_team run "Build a login page with JWT authentication"

# Or as a Python API
from agent_team import Orchestrator

orchestrator = Orchestrator(config_dir="config/")
result = await orchestrator.submit("Build a login page with JWT authentication")
print(result.summary)
```

---

## 6. Orchestrator Internal Flow (Code-Level)

### 6.1 Orchestrator — Main Entry Point

```python
class Orchestrator:
    def __init__(self, config_dir: str):
        self.config = ConfigLoader(config_dir).load()
        self.agent_factory = AgentFactory(self.config)
        self.agent_registry = AgentRegistry(self.agent_factory.create_all())
        self.dispatcher = Dispatcher(self.agent_registry, self.config.teams)
        self.aggregator = Aggregator()
        self.workflow_engine = WorkflowEngine(self.config.workflows)

    async def submit(self, request: str) -> FinalResult:
        # Step 1: Create root task
        root_task = Task(
            id=generate_id(),
            description=request,
            status="RECEIVED",
            assigned_to="engineering_lead"
        )

        # Step 2: Send to Engineering Lead for decomposition
        eng_lead = self.agent_registry.get("engineering_lead")
        delegation_plan = await eng_lead.analyze_and_delegate(root_task)

        # Step 3: Validate delegation targets
        self.dispatcher.validate(delegation_plan)

        # Step 4: Execute subtasks (respecting dependencies and parallelism)
        subtask_results = await self.execute_plan(delegation_plan)

        # Step 5: Aggregate results
        final_result = await self.aggregator.synthesize(
            agent=eng_lead,
            task=root_task,
            results=subtask_results
        )

        return final_result

    async def execute_plan(self, plan: DelegationPlan) -> list[TaskResult]:
        # Group independent tasks for parallel execution
        execution_groups = self.dispatcher.resolve_dependencies(plan)

        results = []
        for group in execution_groups:
            # Execute all tasks in this group concurrently
            group_results = await asyncio.gather(*[
                self.execute_subtask(subtask)
                for subtask in group
            ])
            results.extend(group_results)

        return results

    async def execute_subtask(self, subtask: Task) -> TaskResult:
        agent = self.agent_registry.get(subtask.assigned_to)

        # Check if this agent is a lead who will further decompose
        if agent.can_delegate:
            sub_plan = await agent.analyze_and_delegate(subtask)
            self.dispatcher.validate(sub_plan)
            sub_results = await self.execute_plan(sub_plan)
            return await self.aggregator.synthesize(agent, subtask, sub_results)
        else:
            # Leaf agent — execute directly
            return await agent.execute(subtask)
```

### 6.2 Agent Execution

```python
class BaseAgent:
    async def execute(self, task: Task) -> TaskResult:
        # Build messages with system prompt + task context
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self.format_task(task)}
        ]

        # Iterative tool-use loop
        while True:
            response = await self.model.invoke(
                messages=messages,
                tools=self.get_tool_schemas()
            )

            if response.has_tool_calls:
                for tool_call in response.tool_calls:
                    # Validate agent has permission for this tool
                    self.tool_registry.validate_access(self.agent_id, tool_call.name)

                    # Execute the tool
                    tool_result = await self.tool_registry.execute(tool_call)

                    # Feed result back into conversation
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({"role": "tool", "content": tool_result})
            else:
                # No more tool calls — agent is done
                return TaskResult(
                    task_id=task.id,
                    agent_id=self.agent_id,
                    status="COMPLETED",
                    output=response.content,
                    artifacts=self.collect_artifacts()
                )
```

---

## 7. Failure Handling

### 7.1 What Happens When an Agent Fails

```
Agent execution fails
        │
        ├── Tool error (e.g., test fails)?
        │   └── Agent retries up to 3 times
        │       └── Still failing? → Return FAILED result with error details
        │
        ├── LLM error (API timeout, rate limit)?
        │   └── Exponential backoff retry (3 attempts)
        │       └── Still failing? → Return FAILED result
        │
        └── Quality gate fails (e.g., coverage < 80%)?
            └── Workflow engine routes back to development stage (on_fail)
            └── Agent gets feedback: "Coverage is 72%. Need 80%. Add tests for X."
            └── Agent re-executes with the feedback
```

### 7.2 Failure Propagation

```
Backend Specialist FAILS
        │
        ▼
Code Reviewer receives partial results
        │
        ├── Can the failure be isolated?
        │   YES → Continue with Frontend results, report Backend failure
        │   NO  → Entire Development phase marked FAILED
        │
        ▼
Engineering Lead receives failure
        │
        ├── Retry the failed subtask? (up to max_retries)
        ├── Route to a different agent? (if available)
        └── Report failure to user with details
```

---

## 8. Concrete Example: Full Trace

### Request: "Add a user profile page that shows the user's name, email, and avatar"

**Step 1 — Orchestrator creates root task:**
```
Task #ROOT-001
  description: "Add a user profile page that shows the user's name, email, and avatar"
  status: RECEIVED
  assigned_to: engineering_lead
```

**Step 2 — Engineering Lead analyzes and delegates:**
```json
{
  "analysis": "Full-stack feature: needs a backend API endpoint to fetch user profile data, and a frontend page to display it. No auth changes needed (existing JWT). Testing and deployment after dev.",
  "subtasks": [
    {
      "id": "SUB-001",
      "delegate_to": "code_reviewer",
      "summary": "Implement user profile feature (backend API + frontend page)",
      "domains": ["backend", "frontend"],
      "priority": "high"
    },
    {
      "id": "SUB-002",
      "delegate_to": "devops_specialist",
      "summary": "Test and deploy user profile feature",
      "domains": ["testing", "deployment"],
      "priority": "high",
      "depends_on": ["SUB-001"]
    }
  ]
}
```

**Step 3 — Code Reviewer (Dev Lead) further decomposes:**
```json
{
  "subtasks": [
    {
      "id": "SUB-001-A",
      "delegate_to": "backend_specialist",
      "summary": "Create GET /api/users/me/profile endpoint returning name, email, avatar_url",
      "priority": "high"
    },
    {
      "id": "SUB-001-B",
      "delegate_to": "frontend_specialist",
      "summary": "Create /profile page with ProfileCard component showing name, email, avatar",
      "priority": "high"
    }
  ]
}
```

**Step 4 — Backend Specialist executes:**
```
→ file_write("src/api/routes/profile.py")     # GET /api/users/me/profile
→ file_write("src/api/schemas/profile.py")     # ProfileResponse schema
→ file_write("tests/api/test_profile.py")      # 4 test cases
→ test_runner("pytest tests/api/test_profile.py")  # ✅ 4/4 pass
→ git_operations("commit -m 'Add user profile API endpoint'")
→ git_operations("push origin feature/user-profile-api")
→ github_pr("Create PR #44: Add user profile API endpoint")

Output: TaskResult(
  artifacts={
    "backend_code": ["src/api/routes/profile.py", "src/api/schemas/profile.py"],
    "backend_tests": ["tests/api/test_profile.py"],
    "pr_number": "#44"
  }
)
```

**Step 5 — Frontend Specialist executes (parallel with Step 4):**
```
→ file_write("src/pages/ProfilePage.tsx")       # Profile page
→ file_write("src/components/ProfileCard.tsx")   # ProfileCard component
→ file_write("src/hooks/useProfile.ts")          # API hook
→ file_write("tests/pages/ProfilePage.test.tsx") # Component tests
→ test_runner("jest tests/pages/")               # ✅ 6/6 pass
→ git_operations("commit -m 'Add user profile page'")
→ git_operations("push origin feature/user-profile-ui")
→ github_pr("Create PR #45: Add user profile page")

Output: TaskResult(
  artifacts={
    "frontend_code": ["src/pages/ProfilePage.tsx", "src/components/ProfileCard.tsx"],
    "frontend_tests": ["tests/pages/ProfilePage.test.tsx"],
    "pr_number": "#45"
  }
)
```

**Step 6 — Code Reviewer reviews (after both PRs are ready):**
```
→ github_pr("Review PR #44")   # ✅ Approved — clean API design
→ github_pr("Review PR #45")   # ✅ Approved — good component structure
→ coverage_report("generate")  # 87% coverage — passes 80% gate
→ github_pr("Merge PR #44")
→ github_pr("Merge PR #45")

Output: TaskResult(
  artifacts={
    "review_report": "Both PRs approved. Coverage: 87%.",
    "merged_prs": ["#44", "#45"]
  }
)
```

**Step 7 — Tester Specialist executes (depends on dev completion):**
```
→ file_write("tests/e2e/test_profile_flow.py")   # E2E test
→ test_runner("pytest tests/e2e/test_profile_flow.py")  # ✅ 3/3 pass
→ test_runner("pytest --cov")                     # 87% total coverage

Output: TaskResult(
  artifacts={
    "test_report": "E2E: 3/3 pass. Coverage: 87%. No regressions.",
    "e2e_tests": ["tests/e2e/test_profile_flow.py"]
  }
)
```

**Step 8 — DevOps Specialist deploys:**
```
→ deployment("docker compose -f docker-compose.staging.yml up -d")  # ✅ Staging containers running
→ test_runner("smoke-tests --target localhost:3010")               # ✅ Smoke tests pass
→ deployment("docker compose -f docker-compose.prod.yml up -d")    # ✅ Production containers running
→ monitoring("health-check --target localhost:3020")                # ✅ All endpoints healthy

Output: TaskResult(
  artifacts={
    "deployment_report": "Deployed to production. Health checks pass.",
    "staging_url": "http://localhost:3010/profile",
    "production_url": "http://localhost:3020/profile"
  }
)
```

**Step 9 — Engineering Lead aggregates and responds:**
```
"User profile feature complete.

Backend:
  - GET /api/users/me/profile endpoint (PR #44, merged)

Frontend:
  - /profile page with ProfileCard component (PR #45, merged)

Quality:
  - Code coverage: 87% (target: 80% ✅)
  - E2E tests: 3/3 passing
  - Code review: Approved

Deployment:
  - Staging: http://localhost:3010/profile
  - Production: http://localhost:3020/profile
  - Health checks: All passing"
```

---

## 9. Technology Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **AI Models** | Claude Opus 4.6 + Sonnet 4.6 | Opus 4.6 for leads, review, and reasoning; Sonnet 4.6 for leaf agents (coding, testing) — balances quality and cost |
| **Codebase Access** | Direct access | Agents write files, run git, execute code directly in the project directory |
| **Framework** | Claude Agent SDK | Purpose-built for multi-agent orchestration — less boilerplate, SDK handles coordination |
| **State Management** | SQLite now, Redis later | SQLite: zero infrastructure, crash-safe, no separate server. Swap to Redis when scaling to multiple machines |

### 9.1 Model Assignment Per Agent

| Agent | Model | Rationale |
|-------|-------|-----------|
| Engineering Lead | **Opus 4.6** | Complex decomposition, strategic reasoning |
| PRD Specialist | **Opus 4.6** | Requirements analysis requires deep reasoning |
| User Story Author | **Sonnet 4.6** | Structured writing task, follows templates |
| Code Reviewer | **Opus 4.6** | Quality judgment, architectural reasoning |
| Backend Specialist | **Sonnet 4.6** | Code generation, implementation |
| Frontend Specialist | **Sonnet 4.6** | Code generation, implementation |
| DevOps Specialist | **Sonnet 4.6** | Infrastructure scripting, deployment |
| Tester Specialist | **Sonnet 4.6** | Test writing, execution |

### 9.2 State Management — Abstraction Layer

The state layer is designed with a swappable backend so SQLite can be replaced by Redis later without code changes:

```python
# Abstract interface — same API regardless of backend
class StateStore(ABC):
    async def create_task(self, task: Task) -> str: ...
    async def get_task(self, task_id: str) -> Task: ...
    async def update_task(self, task_id: str, **updates) -> None: ...
    async def get_subtasks(self, parent_id: str) -> list[Task]: ...
    async def get_agent_state(self, agent_id: str) -> AgentState: ...
    async def save_artifact(self, task_id: str, artifact: Artifact) -> None: ...

# Phase 1: SQLite implementation
class SQLiteStateStore(StateStore):
    def __init__(self, db_path: str = "agent_team.db"):
        self.db = sqlite3.connect(db_path)
        self._create_tables()

# Phase 2: Redis implementation (swap in when needed)
class RedisStateStore(StateStore):
    def __init__(self, redis_url: str):
        self.redis = redis.from_url(redis_url)
```

**Why this works:**
- SQLite is crash-safe — WAL mode ensures no data loss on power failure
- The `StateStore` interface hides the backend — orchestrator and agents never know which is in use
- Migration path: change one line in config to switch from SQLite to Redis

---

## 10. Summary

| Aspect | Design Decision |
|--------|----------------|
| **Assignment Model** | Auto-dispatch (push-based) — no manual task board |
| **Routing Logic** | LLM-powered domain analysis using team/agent configs |
| **Decomposition** | Two-level: Engineering Lead → Team Leads → Agents |
| **Parallelism** | Independent subtasks run concurrently (e.g., Backend + Frontend) |
| **Dependencies** | Explicit `depends_on` — dependent tasks wait for prerequisites |
| **Quality Gates** | Configurable pass/fail conditions between workflow stages |
| **Failure Handling** | Retry → reroute → report, with `on_fail` routing back to prior stage |
| **Artifact Passing** | Agents produce named outputs; next stage receives them as inputs |
| **Entry Points** | Phase 1: Claude Code; Phase 2: Claude Agent SDK CLI/API |
| **AI Models** | Opus 4.6 (leads/review) + Sonnet 4.6 (leaf agents) |
| **Codebase Access** | Direct — agents write files and run commands in project directory |
| **Framework** | Claude Agent SDK |
| **State** | SQLite (Phase 1) → Redis (Phase 2, when multi-machine) |
| **Deployment** | Docker Compose — all environments (local, staging, prod, demo) run as isolated Docker stacks |
