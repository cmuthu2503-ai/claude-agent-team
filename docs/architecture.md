# System Architecture
# Agent Team — Scalable Engineering Agent Orchestration

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

## 1. Architecture Overview

The Agent Team system is a **configuration-driven, hierarchical agent orchestration platform** designed to scale from 8 agents to 20+ without code changes. Agents are organized into sub-teams under an Engineering Lead, with all definitions (agents, teams, workflows, tools, thresholds) managed through YAML configuration files.

### 1.1 Design Principles

| Principle | Description |
|-----------|-------------|
| **Config-Driven** | Agents, teams, workflows, tools, and thresholds are defined in YAML — not hardcoded |
| **Team-of-Teams** | New teams slot under Engineering Lead without restructuring existing teams |
| **Working Leads** | Team leads perform their specialist role AND coordinate their team — efficient at small scale |
| **DAG Workflows** | Directed Acyclic Graph pipelines replace linear handoffs — enabling parallel execution |
| **Role-Based Access** | Tool permissions are assigned per agent role, enforced at runtime |
| **Zero-Code Expansion** | Adding an agent or team requires only YAML file changes |

---

## 2. Engineering Team Hierarchy

### 2.1 Organization Chart

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
    │                     │    │                        ���    │                     │
    │  - PRD Specialist   │    │  - Code Reviewer       │    │  - DevOps Spec.     ���
    │  - User Story Author│    │  - Backend Specialist   │    │  - Tester Spec.     │
    └─────────────────────┘    │  - Frontend Specialist  │    └─────────────────────┘
                               └────────────────────────┘
```

### 2.2 Agent Roster (8 Agents)

| # | Agent ID | Role | Team | Reports To | Delegation Targets |
|---|----------|------|------|------------|-------------------|
| 1 | `engineering_lead` | Engineering Lead | engineering | — | planning_lead, development_lead, delivery_lead |
| 2 | `prd_specialist` | PRD Specialist & Planning Lead | planning | engineering_lead | user_story_author |
| 3 | `user_story_author` | User Story Author | planning | prd_specialist | — |
| 4 | `code_reviewer` | Code Reviewer & Development Lead | development | engineering_lead | backend_specialist, frontend_specialist |
| 5 | `backend_specialist` | Backend Specialist | development | code_reviewer | — |
| 6 | `frontend_specialist` | Frontend Specialist | development | code_reviewer | — |
| 7 | `devops_specialist` | DevOps Specialist & Delivery Lead | delivery | engineering_lead | tester_specialist |
| 8 | `tester_specialist` | Tester Specialist | delivery | devops_specialist | — |

### 2.3 Delegation Rules

Agents can **only** delegate to their direct reports. This prevents chaotic task routing and ensures accountability flows through the hierarchy.

```
engineering_lead ──► prd_specialist (planning lead)
                 ──► code_reviewer (development lead)
                 ──► devops_specialist (delivery lead)

prd_specialist   ──► user_story_author

code_reviewer    ──► backend_specialist
                 ──► frontend_specialist

devops_specialist ──► tester_specialist
```

### 2.4 Team Definitions

| Team | Lead | Members | Domain |
|------|------|---------|--------|
| **Engineering** | Engineering Lead | All agents | All domains — top-level coordination |
| **Planning** | PRD Specialist | PRD Specialist, User Story Author | Requirements, documentation, user stories, acceptance criteria |
| **Development** | Code Reviewer | Code Reviewer, Backend Specialist, Frontend Specialist | Backend, frontend, APIs, database, UI, code review |
| **Delivery** | DevOps Specialist | DevOps Specialist, Tester Specialist | Testing, CI/CD, deployment, infrastructure, monitoring |

---

## 3. Configuration System

All system behavior is defined in YAML configuration files. This is the foundation that makes the system expandable without code changes.

### 3.1 Configuration File Structure

```
config/
├── agents/
│   ├── _template.yaml              # Template for creating new agents
│   ├── engineering_lead.yaml
│   ├── prd_specialist.yaml
│   ��── user_story_author.yaml
│   ├── code_reviewer.yaml
│   ├── backend_specialist.yaml
│   ├── frontend_specialist.yaml
│   ├── devops_specialist.yaml
│   └── tester_specialist.yaml
├── teams.yaml                      # Team compositions and hierarchy
├── workflows.yaml                  # DAG-based workflow pipelines
├── tools.yaml                      # Tool registry and role-based permissions
└── thresholds.yaml                 # Configurable thresholds (coverage, SLAs, etc.)
```

### 3.2 Agent Definition Schema

Every agent is defined in its own YAML file following a standard schema. To add a new agent, copy `_template.yaml` and fill in the fields.

```yaml
# config/agents/_template.yaml

agent_id: ""                        # Unique snake_case identifier
display_name: ""                    # Human-readable name
role: ""                            # Job title
team: ""                            # team_id from teams.yaml
reports_to: ""                      # agent_id of direct manager (null for Engineering Lead)

responsibilities:
  - id: ""                          # Unique ID (e.g., "BE-001")
    description: ""                 # What this agent does
    category: ""                    # planning | development | review | testing | deployment

tools: []                           # List of tool_ids from tools.yaml

outputs:                            # What this agent produces
  - name: ""
    format: ""                      # markdown | json | yaml | code | report

delegation:
  can_delegate_to: []               # agent_ids this agent can delegate to
  max_concurrent_tasks: 3           # How many tasks this agent handles at once

quality_gates:                      # Conditions that must pass before output is accepted
  - gate_id: ""
    description: ""
    threshold: ""                   # Reference to thresholds.yaml key

metadata:
  created: ""
  version: ""
```

### 3.3 Agent Definition Example

```yaml
# config/agents/backend_specialist.yaml

agent_id: backend_specialist
display_name: "Backend Specialist"
role: "Backend Specialist"
team: development
reports_to: code_reviewer

responsibilities:
  - id: BE-001
    description: "Design and implement RESTful or GraphQL APIs based on user stories"
    category: development
  - id: BE-002
    description: "Create and maintain database schemas, migrations, and seed data"
    category: development
  - id: BE-003
    description: "Implement server-side business logic, validation, and data processing"
    category: development
  - id: BE-004
    description: "Write unit and integration tests for all backend code"
    category: testing
  - id: BE-005
    description: "Document API endpoints, request/response formats, and error codes"
    category: development
  - id: BE-006
    description: "Optimize queries, caching, and server-side performance"
    category: development

tools:
  - file_read
  - file_write
  - git_operations
  - code_exec
  - test_runner
  - code_analysis
  - web_search
  - database_operations

outputs:
  - name: "API Endpoints"
    format: code
  - name: "Database Migrations"
    format: code
  - name: "Backend Services"
    format: code
  - name: "Server-Side Tests"
    format: code
  - name: "API Documentation"
    format: markdown

delegation:
  can_delegate_to: []
  max_concurrent_tasks: 3

quality_gates:
  - gate_id: coverage_check
    description: "Backend code coverage must meet minimum threshold"
    threshold: code_coverage_minimum

metadata:
  created: "2026-04-04"
  version: "1.0"
```

### 3.4 Team Configuration

```yaml
# config/teams.yaml

teams:
  engineering:
    display_name: "Engineering"
    lead: engineering_lead
    sub_teams: [planning, development, delivery]
    domain: [all]

  planning:
    display_name: "Planning Team"
    lead: prd_specialist
    members: [prd_specialist, user_story_author]
    domain: [requirements, documentation, user-stories, acceptance-criteria]
    parent_team: engineering

  development:
    display_name: "Development Team"
    lead: code_reviewer
    members: [code_reviewer, backend_specialist, frontend_specialist]
    domain: [backend, frontend, api, database, ui, components, code-review]
    parent_team: engineering

  delivery:
    display_name: "Delivery Team"
    lead: devops_specialist
    members: [devops_specialist, tester_specialist]
    domain: [testing, deployment, ci-cd, infrastructure, monitoring, e2e]
    parent_team: engineering

hierarchy:
  root: engineering
  delegation_direction: top_down
```

### 3.5 Tool Permissions

```yaml
# config/tools.yaml

tools:
  file_read:
    description: "Read file contents"
    category: core
    available_to: [all]

  file_write:
    description: "Create or modify files"
    category: core
    available_to: [prd_specialist, user_story_author, backend_specialist, frontend_specialist, devops_specialist, tester_specialist]

  git_operations:
    description: "Git status, diff, commit, branch operations"
    category: core
    available_to: [code_reviewer, backend_specialist, frontend_specialist, devops_specialist, tester_specialist]

  github_pr_review:
    description: "Review pull requests, post comments, approve/request changes"
    category: github
    available_to: [code_reviewer]

  code_exec:
    description: "Execute code in a sandboxed environment"
    category: development
    available_to: [backend_specialist, frontend_specialist, tester_specialist]

  code_analysis:
    description: "Static analysis, linting, formatting checks"
    category: quality
    available_to: [code_reviewer, backend_specialist, frontend_specialist]

  coverage_report:
    description: "Generate and read code coverage reports"
    category: quality
    available_to: [code_reviewer, tester_specialist]

  test_runner:
    description: "Execute test suites (unit, integration, E2E)"
    category: testing
    available_to: [tester_specialist, backend_specialist, frontend_specialist]

  deployment:
    description: "Deploy to staging/production environments"
    category: infrastructure
    available_to: [devops_specialist]

  ci_cd_pipeline:
    description: "Create and manage GitHub Actions workflows"
    category: infrastructure
    available_to: [devops_specialist]

  monitoring:
    description: "Set up and read monitoring dashboards and alerts"
    category: infrastructure
    available_to: [devops_specialist]

  database_operations:
    description: "Database schema management, migrations, queries"
    category: database
    available_to: [backend_specialist]

  web_search:
    description: "Search the web for information"
    category: research
    available_to: [all]
```

### 3.6 Configurable Thresholds

All values that were previously hardcoded (80% coverage, 24h SLA, weekly cadence) are now centralized in one file:

```yaml
# config/thresholds.yaml

thresholds:
  code_coverage_minimum:
    value: 80
    unit: percent
    enforcement: block_merge
    description: "Minimum code coverage required on every PR"

  review_sla:
    value: 24
    unit: hours
    enforcement: alert
    description: "Maximum time for code review after PR submission"

  deployment_frequency:
    value: weekly
    options: [daily, weekly, biweekly, monthly]
    enforcement: scheduled
    description: "Target deployment cadence"

  demo_test_frequency:
    value: weekly
    enforcement: cron
    cron_expression: "0 9 * * 1"
    description: "How often demo tests run"

  stale_branch_age:
    value: 7
    unit: days
    enforcement: automated_cleanup
    description: "Delete branches merged longer than this"

  max_concurrent_tasks_per_agent:
    value: 3
    enforcement: queue
    description: "Maximum parallel tasks any single agent handles"

  task_timeout:
    value: 300
    unit: seconds
    enforcement: cancel_with_partial_results
    description: "Maximum time for a single agent task before timeout"
```

---

## 4. Workflow Engine

### 4.1 Overview

Workflows define how tasks flow through the agent team. Unlike the original linear pipeline (PRD -> Stories -> Dev -> Review -> Test -> Deploy), the workflow engine supports:

- **Parallel stages** — Backend and Frontend work simultaneously
- **Quality gates** — Configurable pass/fail conditions at each stage
- **Failure routing** — Failed gates route back to a previous stage (e.g., review failure -> back to development)
- **Multiple workflow types** — Different workflows for features, bugfixes, docs, demos
- **Domain-based routing** — Tasks route to the right agent based on domain tags

### 4.2 Workflow Definitions

```yaml
# config/workflows.yaml

workflows:

  # ──────────────────────────────────────────────
  # Standard feature development lifecycle
  # ─────────���─────────��──────────────────────────
  feature_development:
    description: "Standard feature development lifecycle"
    trigger: feature_request
    stages:

      requirements:
        agents: [prd_specialist]
        outputs: [prd_document]
        next: [story_creation]

      story_creation:
        agents: [user_story_author]
        inputs: [prd_document]
        outputs: [user_stories]
        next: [development]

      development:
        parallel:
          backend:
            agents: [backend_specialist]
            inputs: [user_stories]
            outputs: [backend_code, backend_tests, api_docs]
          frontend:
            agents: [frontend_specialist]
            inputs: [user_stories]
            outputs: [frontend_code, frontend_tests]
        next: [review]

      review:
        agents: [code_reviewer]
        inputs: [backend_code, frontend_code, backend_tests, frontend_tests]
        outputs: [review_report]
        quality_gates:
          - gate: coverage_check
            threshold: code_coverage_minimum
          - gate: review_approval
            required: true
        on_fail: development
        next: [testing]

      testing:
        agents: [tester_specialist]
        inputs: [backend_code, frontend_code, review_report]
        outputs: [test_report]
        quality_gates:
          - gate: all_tests_pass
            required: true
          - gate: regression_clear
            required: true
        on_fail: development
        next: [deployment]

      deployment:
        agents: [devops_specialist]
        inputs: [test_report]
        outputs: [deployment_report]
        stages: [staging, production, verified]
        quality_gates:
          - gate: smoke_tests_pass
            stage: staging
          - gate: health_checks_pass
            stage: production

  # ─────���────────────────��───────────────────────
  # Expedited bug fix workflow
  # ────────────────────────────��─────────────────
  bug_fix:
    description: "Expedited bug fix workflow — shorter pipeline"
    trigger: bug_report
    stages:

      triage:
        agents: [engineering_lead]
        outputs: [bug_analysis]
        next: [fix]

      fix:
        agents: [backend_specialist, frontend_specialist]
        routing: domain_based
        inputs: [bug_analysis]
        outputs: [fix_code, fix_tests]
        next: [review_and_test]

      review_and_test:
        parallel:
          review:
            agents: [code_reviewer]
            inputs: [fix_code]
            outputs: [review_report]
          test:
            agents: [tester_specialist]
            inputs: [fix_code, fix_tests]
            outputs: [test_report]
        next: [hotfix_deploy]

      hotfix_deploy:
        agents: [devops_specialist]
        inputs: [review_report, test_report]
        outputs: [deployment_report]

  # ────────────────────────���─────────────────────
  # Documentation-only workflow
  # ─────────────────��────────────────────────────
  documentation_update:
    description: "PRD and documentation updates — no deployment"
    trigger: doc_request
    stages:

      draft:
        agents: [prd_specialist]
        outputs: [prd_document]
        next: [stories]

      stories:
        agents: [user_story_author]
        inputs: [prd_document]
        outputs: [user_stories]

  # ─────���────────────────────���───────────────────
  # Demo preparation workflow
  # ──────────────��───────────────────────────────
  demo_preparation:
    description: "Demo creation and testing"
    trigger: demo_request
    stages:

      prepare:
        parallel:
          environment:
            agents: [devops_specialist]
            outputs: [demo_environment]
          test_plan:
            agents: [tester_specialist]
            outputs: [demo_test_plan]
        next: [validate]

      validate:
        agents: [tester_specialist]
        inputs: [demo_environment, demo_test_plan]
        outputs: [demo_test_report]
```

### 4.3 Workflow Visualization — Feature Development

```
                    ┌──────────────┐
                    │ Requirements  │
                    │ PRD Specialist│
                    └──────┬───────┘
                           │
                    ┌──────▼─────��─┐
                    │Story Creation │
                    │ User Story    │
                    │ Author        │
                    └──────┬───────┘
                           │
              ┌────────────┴────────────┐
              │                         │
     ┌────────▼────────┐     ┌────��────▼────────┐
     │ Backend Dev      │     │ Frontend Dev      │
     │ Backend Spec.    │     │ Frontend Spec.    ���
     └────────��────────┘     └──────��──┬────────┘
              │                         │
              └────────────┬────────────┘
                           │
                    ┌──────▼───────┐
                    │  Code Review  │◄──── Quality Gate: coverage ≥ 80%
                    │  Code Reviewer│──┐   Quality Gate: approval required
                    └──────┬───────┘  │
                           │          │ on_fail: back to Development
                    ┌──────▼───────┐  │
                    │   Testing     │◄─┘
                    │ Tester Spec.  │──── Quality Gate: all tests pass
                    └──────���───────┘     Quality Gate: no regressions
                           │
                    ┌─────��▼───────┐
                    │  Deployment   │
                    │  DevOps Spec. │──── staging → production → verified
                    └──────────────┘
```

### 4.4 Workflow Visualization — Bug Fix (Expedited)

```
     ┌──────────────┐
     │   Triage      │
     │  Eng. Lead    │
     └──────┬───────┘
            │
     ┌��─────▼───────┐
     │     Fix       │  (domain-routed to Backend OR Frontend)
     └──────┬───────┘
            │
   ┌────────┴────────┐
   │                  │
   ▼                  ▼
┌──────────┐  ┌───────────┐
│  Review   │  │  Testing   │   (parallel — faster turnaround)
│  Code Rev.│  │  Tester Sp.│
└─────┬────┘  └─────┬─────┘
      │              │
      └──────┬───────┘
             │
      ┌────���─▼───────┐
      │ Hotfix Deploy │
      │ DevOps Spec.  │
      └──────────────┘
```

---

## 5. Task Lifecycle

### 5.1 Task State Machine

Every task flows through a standard lifecycle regardless of workflow type:

```
RECEIVED ──► ANALYZING ──► DELEGATED ──► IN_PROGRESS ──► AGGREGATING ──► COMPLETED
                                              │                              │
                                              ▼                              ▼
                                           FAILED                        REPORTED
```

| State | Description | Who Acts |
|-------|-------------|---------|
| RECEIVED | Task entered the system | Orchestrator |
| ANALYZING | Engineering Lead or team lead is decomposing the task | Lead agent |
| DELEGATED | Subtasks created and assigned to agents | Dispatcher |
| IN_PROGRESS | Agent is actively working on the task | Assigned agent |
| AGGREGATING | All subtasks complete; lead is synthesizing results | Lead agent |
| COMPLETED | Task finished successfully | System |
| FAILED | Task could not be completed | System (triggers on_fail routing) |
| REPORTED | Results delivered to stakeholder | Orchestrator |

### 5.2 Task Routing Flow

```
1. Stakeholder submits request
        │
        ▼
2. Engineering Lead receives task
        │
        ▼
3. Engineering Lead analyzes and creates delegation plan:
   {
     "delegate_to": "code_reviewer",
     "task_summary": "Implement user dashboard feature",
     "priority": "high",
     "task_type": "development"
   }
        │
        ▼
4. Dispatcher validates delegation target (must be a direct report)
        │
        ▼
5. Code Reviewer (dev lead) further decomposes:
   [
     { "delegate_to": "backend_specialist", "task": "Build REST API" },
     { "delegate_to": "frontend_specialist", "task": "Build React UI" }
   ]
        ��
        ▼
6. Backend + Frontend execute in parallel
        │
        ▼
7. Results flow upward:
   Backend Specialist ──► Code Reviewer (aggregates)
   Frontend Specialist ──► Code Reviewer (aggregates)
                               ��
                               ▼
                    Code Reviewer ──► Engineering Lead (aggregates)
                               │
                               ▼
                    Engineering Lead ──► Stakeholder response
```

---

## 6. Core System Components

### 6.1 Component Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      Agent Team System                           │
├───────────────────────────────────────���──────────────────────────┤
│                                                                  │
│  ┌────────────────┐                                              │
│  │  Config Loader  │──── Reads all YAML configs at startup       │
│  │  + Validator    │──── Validates against JSON schemas           │
│  └───────┬────────┘                                              │
│          │                                                       │
│  ┌───────▼────────┐     ┌────���─────────────┐                    │
│  │  Agent Factory  │────►│  Agent Registry   │                   │
│  │  (creates from  │     │  (lookup by id,   │                   │
│  │   YAML configs) │     │   role, or team)  │                   ���
│  └────────────────┘     └────────┬─────────┘                    │
│                                  │                               │
│  ┌───────────────────────────────▼───────────────────────────┐  │
│  │                    Orchestrator                             │  ���
│  │  ┌──────────────┐  ┌──────────────┐  ┌─���────────────┐    │  │
│  ��  │  Workflow     │  │  Dispatcher   │  │  Aggregator   │   │  │
│  │  │  Engine       │  │  (validates   │  │  (synthesizes │   │  │
���  │  │  (DAG stages, │  │   delegation, │  │   subtask     │   │  │
│  │  │   quality     │  │   routes to   │  │   results)    │   │  │
│  │  │   gates)      │  │   agents)     │  │               │   │  │
│  │  ���──────────────┘  └���─────────────┘  └��─────────────┘    │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────┐                                            │
│  │  Tool Registry    │──── Role-based access control             │
│  │  (resolves tools  │──── Validates agent has permission        │
│  │   from tools.yaml)│                                           │
│  └──────────────────��                                            │
│                                                                  │
└────��─────────────────────────────────────────���─────────────────���─┘
```

### 6.2 Component Descriptions

| Component | File | Responsibility |
|-----------|------|---------------|
| **Config Loader** | `src/config/loader.py` | Reads all YAML files from `config/`; resolves references between files |
| **Config Validator** | `src/config/validator.py` | Validates YAML configs against JSON schemas; ensures delegation rules are consistent with team hierarchy |
| **Agent Factory** | `src/agents/factory.py` | Reads agent YAML files, instantiates `BaseAgent` subclasses |
| **Agent Registry** | `src/agents/registry.py` | Indexes all agents; provides lookup by `agent_id`, `role`, `team` |
| **Orchestrator** | `src/core/orchestrator.py` | Entry point for all work requests; manages end-to-end lifecycle |
| **Workflow Engine** | `src/core/workflow_engine.py` | Parses `workflows.yaml`; manages stage transitions; enforces quality gates |
| **Dispatcher** | `src/core/dispatcher.py` | Validates delegation targets against hierarchy; routes tasks to agents |
| **Aggregator** | `src/core/aggregator.py` | Collects subtask results; uses lead agent to synthesize summary |
| **Tool Registry** | `src/tools/registry.py` | Resolves tool access per agent role; blocks unauthorized tool usage |

### 6.3 BaseAgent Interface

```python
class BaseAgent(ABC):
    """Every agent implements this interface."""

    def __init__(self, config: AgentConfig, tool_registry: ToolRegistry):
        self.agent_id = config.agent_id
        self.role = config.role
        self.team = config.team
        self.tools = tool_registry.get_tools_for(self.agent_id)

    async def process_task(self, task: Task) -> TaskResult:
        """Execute the task and return results."""

    async def delegate(self, task: Task) -> DelegationPlan:
        """Decompose task into subtasks for direct reports (leads only)."""

    def get_tools(self) -> list[Tool]:
        """Return tools available to this agent."""
```

---

## 7. Project Directory Structure

```
C:\ai-projects\claude-agent-team\
│
├── docs/
│   ├── prd-template.md              # Product Requirements Document
│   ├── architecture.md              # This document — system architecture
│   └── expansion-playbook.md        # How to add agents and teams
│
├── config/
│   ├── agents/
│   │   ├── _template.yaml           # Agent definition template
│   │   ├── engineering_lead.yaml
│   │   ├── prd_specialist.yaml
│   │   ├── user_story_author.yaml
│   │   ├── code_reviewer.yaml
│   │   ├── backend_specialist.yaml
│   │   ├── frontend_specialist.yaml
│   │   ├── devops_specialist.yaml
│   │   └── tester_specialist.yaml
│   ├── teams.yaml
│   ├── workflows.yaml
��   ├── tools.yaml
│   └── thresholds.yaml
│
├── src/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── orchestrator.py
│   │   ├── workflow_engine.py
│   │   ├── dispatcher.py
│   │   └── aggregator.py
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── factory.py
│   │   ├── registry.py
│   │   └── types.py
│   ├── workflows/
│   │   ├── __init__.py
���   │   ├── stage.py
│   │   ├── runner.py
│   │   └── loader.py
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── base_tool.py
│   │   ├── registry.py
│   │   └── (tool implementations)
│   ├── config/
│   │   ├── __init__.py
│   │   ├��─ loader.py
│   │   ├── validator.py
│   │   └── schemas/
│   │       ├── agent_schema.json
│   │       ├── team_schema.json
│   │       ├── workflow_schema.json
│   │       └── tools_schema.json
│   ��── utils/
│       ├── __init__.py
│       ├── logging.py
│       └── exceptions.py
│
├── tests/
│   ├── conftest.py
│   ├── test_factory.py
│   ├── test_registry.py
│   ├── test_workflow_engine.py
│   ├── test_dispatcher.py
│   └── test_config_validation.py
│
├── pyproject.toml
│
├── Dockerfile.backend               # Multi-stage: dev + production
├── Dockerfile.frontend              # Multi-stage: dev + production (React build → nginx)
├── docker-compose.yml               # Local development (default)
├── docker-compose.staging.yml       # Staging environment (ports 8010/3010)
├── docker-compose.prod.yml          # Production environment (ports 8020/3020)
├── docker-compose.demo.yml          # Demo environment (ports 8030/3030, seed data)
├── Makefile                         # Convenience: make dev, make staging, make prod, make demo
│
└── demo/
    ├── seed-data/                   # Curated sample data for demo
    │   ├── users.json
    │   └── sample-data.json
    └── seed.py                      # Script to load seed data into demo DB
```

---

## 8. Future Growth Path

### 8.1 Planned Expansion (YAML-Only Changes)

| Future Agent | Team Placement | Files to Change |
|---|---|---|
| Database Specialist | Development | Add `config/agents/database_specialist.yaml`; update `teams.yaml` + `code_reviewer.yaml` delegation |
| API Specialist | Development | Add `config/agents/api_specialist.yaml`; update `teams.yaml` + `code_reviewer.yaml` delegation |
| Security Lead | New: Security Team | Add `config/agents/security_lead.yaml`; add team to `teams.yaml`; update `engineering_lead.yaml` delegation |
| Security Engineer | Security Team | Add `config/agents/security_engineer.yaml`; update `teams.yaml` + `security_lead.yaml` delegation |
| UX Designer | New: Design Team | Add `config/agents/ux_designer.yaml`; add team to `teams.yaml`; update `engineering_lead.yaml` delegation |
| Performance Engineer | Delivery | Add `config/agents/performance_engineer.yaml`; update `teams.yaml` + `devops_specialist.yaml` delegation |
| Technical Writer | Planning | Add `config/agents/technical_writer.yaml`; update `teams.yaml` + `prd_specialist.yaml` delegation |
| Data Engineer | New: Data Team | Add `config/agents/data_engineer.yaml`; add team to `teams.yaml`; update `engineering_lead.yaml` delegation |
| ML Engineer | Data Team | Add `config/agents/ml_engineer.yaml`; update `teams.yaml` + `data_engineer.yaml` delegation |

### 8.2 Expanded Org Chart (15+ Agents)

```
                            ┌──────────────────────┐
                            │   Engineering Lead     │
                            └──┬───┬───┬───┬───┬───┘
                               │   │   │   │   │
          ┌────────────────────┘   │   │   │   └────────────────────┐
          │              ┌─────────┘   │   └─────────┐              │
          ▼              ▼             ▼             ▼              ▼
   ┌────────────┐ ┌────────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐
   │  Planning   │ │Development │ │ Delivery  │ │ Security  │ │   Data     │
   │  Team       │ │ Team       │ │ Team      │ │ Team      │ │   Team     │
   │             │ │            │ │           │ ���           │ │            │
   │ PRD Spec.   │ │ Code Rev.  │ │ DevOps Sp.│ │ Sec. Lead │ │ Data Eng.  │
   │ Story Auth. │ │ Backend Sp.│ │ Tester Sp.��� │ Sec. Eng. │ │ ML Eng.    │
   │ Tech Writer │ │ Frontend Sp│ │ Perf. Eng.│ └───────────┘ └────────────┘
   └─────────────┘ │ DB Spec.   │ └───────────┘
                   │ API Spec.  │
                   └─────────��──┘
```

---

## 9. Architectural Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| One YAML per agent | vs. single agents.yaml | Avoids merge conflicts; clear ownership; easy to add/remove |
| Working leads | vs. dedicated managers | 8 agents is too few for pure management; keeps leads productive |
| DAG workflows in YAML | vs. hardcoded in code | Non-developers can modify; no deploys needed for workflow changes |
| Centralized thresholds | vs. per-agent thresholds | Single source of truth; easy to audit; DRY |
| Engineering Lead as agent | vs. orchestrator-only | Consistent with config-driven pattern; gets its own config, tools, delegation rules |
| Top-down delegation only | vs. any-to-any routing | Prevents chaotic routing; clear accountability chain |
| Domain-based routing | vs. manual assignment | Automatically routes tasks to the right team based on domain tags |
