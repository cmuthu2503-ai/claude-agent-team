# Expansion Playbook
# How to Add Agents and Teams to the Agent Team System

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

This playbook provides step-by-step instructions for expanding the agent team. The system is designed so that **adding agents and teams requires only YAML configuration changes** — no code modifications needed.

### 1.1 What You Can Do

| Action | Files Changed | Code Changes |
|--------|--------------|-------------|
| Add an agent to an existing team | 3 YAML files | None |
| Create a new team with agents | 2+ YAML files | None |
| Add a new workflow | 1 YAML file | None |
| Add a new tool | 1 YAML file | Implementation code (if new tool type) |
| Change a threshold (coverage, SLA) | 1 YAML file | None |

---

## 2. Adding a New Agent to an Existing Team

### Example: Adding a "Database Specialist" to the Development Team

#### Step 1: Create the Agent Definition

Copy `config/agents/_template.yaml` to `config/agents/database_specialist.yaml` and fill in:

```yaml
# config/agents/database_specialist.yaml

agent_id: database_specialist
display_name: "Database Specialist"
role: "Database Specialist"
team: development
reports_to: code_reviewer

responsibilities:
  - id: DB-001
    description: "Design and maintain database schemas, migrations, and seed data"
    category: development
  - id: DB-002
    description: "Optimize queries, indexing, and database performance"
    category: development
  - id: DB-003
    description: "Write database-level tests and data integrity checks"
    category: testing
  - id: DB-004
    description: "Manage database backups and recovery procedures"
    category: deployment

tools:
  - file_read
  - file_write
  - git_operations
  - code_exec
  - test_runner
  - code_analysis
  - database_operations
  - web_search

outputs:
  - name: "Database Schema"
    format: code
  - name: "Migration Scripts"
    format: code
  - name: "Database Tests"
    format: code
  - name: "Query Performance Report"
    format: report

delegation:
  can_delegate_to: []
  max_concurrent_tasks: 3

quality_gates:
  - gate_id: coverage_check
    description: "Database code coverage must meet minimum threshold"
    threshold: code_coverage_minimum

metadata:
  created: "2026-04-04"
  version: "1.0"
```

#### Step 2: Register in Team

Edit `config/teams.yaml` — add the agent to the Development team's member list:

```yaml
  development:
    display_name: "Development Team"
    lead: code_reviewer
    members: [code_reviewer, backend_specialist, frontend_specialist, database_specialist]  # ADDED
    domain: [backend, frontend, api, database, ui, components, code-review]
    parent_team: engineering
```

#### Step 3: Update Lead's Delegation Rules

Edit `config/agents/code_reviewer.yaml` — add the new agent to the delegation list:

```yaml
delegation:
  can_delegate_to: [backend_specialist, frontend_specialist, database_specialist]  # ADDED
  max_concurrent_tasks: 5
```

#### Step 4: Add Tools (If New Tool Types Needed)

If the agent needs a tool that doesn't exist in `config/tools.yaml`, add it:

```yaml
  database_operations:
    description: "Database schema management, migrations, query execution"
    category: database
    available_to: [database_specialist, backend_specialist]
```

#### Step 5: Add to Workflows (If Applicable)

Edit `config/workflows.yaml` — add the agent to relevant workflow stages:

```yaml
      development:
        parallel:
          backend:
            agents: [backend_specialist, database_specialist]  # ADDED
            inputs: [user_stories]
            outputs: [backend_code, backend_tests, api_docs, database_schema]
          frontend:
            agents: [frontend_specialist]
            inputs: [user_stories]
            outputs: [frontend_code, frontend_tests]
```

#### Checklist

- [ ] Agent YAML file created in `config/agents/`
- [ ] Agent added to team in `config/teams.yaml`
- [ ] Agent added to lead's `can_delegate_to` list
- [ ] New tools added to `config/tools.yaml` (if needed)
- [ ] Agent added to workflow stages in `config/workflows.yaml` (if needed)
- [ ] PRD updated with new agent's responsibility table (if needed)

---

## 3. Creating a New Team

### Example: Adding a "Security Team" Under Engineering

#### Step 1: Create Agent Definitions

Create YAML files for each team member:

**`config/agents/security_lead.yaml`:**
```yaml
agent_id: security_lead
display_name: "Security Lead"
role: "Security Lead"
team: security
reports_to: engineering_lead

responsibilities:
  - id: SEC-001
    description: "Conduct security reviews of all code changes"
    category: review
  - id: SEC-002
    description: "Manage vulnerability scanning and dependency audits"
    category: testing
  - id: SEC-003
    description: "Define and enforce security policies and compliance requirements"
    category: planning
  - id: SEC-004
    description: "Coordinate security team and prioritize security tasks"
    category: planning

tools:
  - file_read
  - file_write
  - git_operations
  - code_analysis
  - security_scan
  - dependency_audit
  - web_search

outputs:
  - name: "Security Review Report"
    format: markdown
  - name: "Vulnerability Report"
    format: report
  - name: "Security Policy Documents"
    format: markdown

delegation:
  can_delegate_to: [security_engineer]
  max_concurrent_tasks: 4

quality_gates:
  - gate_id: no_critical_vulnerabilities
    description: "No critical or high-severity vulnerabilities"
    threshold: security_vulnerability_threshold

metadata:
  created: "2026-04-04"
  version: "1.0"
```

**`config/agents/security_engineer.yaml`:**
```yaml
agent_id: security_engineer
display_name: "Security Engineer"
role: "Security Engineer"
team: security
reports_to: security_lead

responsibilities:
  - id: SECE-001
    description: "Perform penetration testing and vulnerability assessments"
    category: testing
  - id: SECE-002
    description: "Implement security fixes and hardening measures"
    category: development
  - id: SECE-003
    description: "Write security-focused automated tests"
    category: testing

tools:
  - file_read
  - file_write
  - git_operations
  - code_exec
  - security_scan
  - dependency_audit
  - test_runner
  - web_search

outputs:
  - name: "Penetration Test Results"
    format: report
  - name: "Security Fixes"
    format: code
  - name: "Security Tests"
    format: code

delegation:
  can_delegate_to: []
  max_concurrent_tasks: 3

metadata:
  created: "2026-04-04"
  version: "1.0"
```

#### Step 2: Add Team to teams.yaml

```yaml
  security:
    display_name: "Security Team"
    lead: security_lead
    members: [security_lead, security_engineer]
    domain: [security, authentication, authorization, vulnerability, compliance, encryption]
    parent_team: engineering
```

Also update the Engineering team's sub_teams list:

```yaml
  engineering:
    display_name: "Engineering"
    lead: engineering_lead
    sub_teams: [planning, development, delivery, security]  # ADDED
    domain: [all]
```

#### Step 3: Update Engineering Lead's Delegation Rules

Edit `config/agents/engineering_lead.yaml`:

```yaml
delegation:
  can_delegate_to: [prd_specialist, code_reviewer, devops_specialist, security_lead]  # ADDED
```

#### Step 4: Add Security-Specific Tools

Edit `config/tools.yaml`:

```yaml
  security_scan:
    description: "Run security vulnerability scans on codebase"
    category: security
    available_to: [security_lead, security_engineer]

  dependency_audit:
    description: "Audit dependencies for known vulnerabilities"
    category: security
    available_to: [security_lead, security_engineer, devops_specialist]
```

#### Step 5: Add Security Thresholds

Edit `config/thresholds.yaml`:

```yaml
  security_vulnerability_threshold:
    value: 0
    unit: critical_vulnerabilities
    enforcement: block_merge
    description: "No critical vulnerabilities allowed in any PR"

  security_review_sla:
    value: 48
    unit: hours
    enforcement: alert
    description: "Maximum time for security review after PR submission"
```

#### Step 6: Add Security Stage to Workflows

Edit `config/workflows.yaml` — add a security review stage to the feature workflow:

```yaml
      review:
        agents: [code_reviewer]
        # ... existing config ...
        next: [security_review]    # CHANGED: route to security before testing

      security_review:             # NEW STAGE
        agents: [security_lead]
        inputs: [review_report, backend_code, frontend_code]
        outputs: [security_report]
        quality_gates:
          - gate: no_critical_vulnerabilities
            required: true
        on_fail: development
        next: [testing]

      testing:
        agents: [tester_specialist]
        inputs: [backend_code, frontend_code, review_report, security_report]  # UPDATED: added security_report
        # ... rest unchanged ...
```

#### Checklist

- [ ] Agent YAML files created for all team members
- [ ] Team added to `config/teams.yaml` with lead, members, and domain
- [ ] Parent team's `sub_teams` list updated
- [ ] Engineering Lead's `can_delegate_to` updated
- [ ] Team-specific tools added to `config/tools.yaml`
- [ ] Team-specific thresholds added to `config/thresholds.yaml` (if needed)
- [ ] Workflow stages updated in `config/workflows.yaml`
- [ ] PRD updated with new team and agent responsibility tables

---

## 4. Adding a New Workflow

### Example: Adding a "Spike / Research" Workflow

Edit `config/workflows.yaml`:

```yaml
  spike_research:
    description: "Time-boxed research spike — no deployment"
    trigger: spike_request
    stages:

      scope:
        agents: [engineering_lead]
        outputs: [spike_scope]
        next: [research]

      research:
        parallel:
          backend_research:
            agents: [backend_specialist]
            inputs: [spike_scope]
            outputs: [backend_findings]
          frontend_research:
            agents: [frontend_specialist]
            inputs: [spike_scope]
            outputs: [frontend_findings]
        next: [synthesize]

      synthesize:
        agents: [engineering_lead]
        inputs: [backend_findings, frontend_findings]
        outputs: [spike_report]

      document:
        agents: [prd_specialist]
        inputs: [spike_report]
        outputs: [spike_prd]
```

No other files need to change. The workflow engine picks up new workflows automatically.

---

## 5. Changing a Threshold

### Example: Raising Code Coverage to 90%

Edit `config/thresholds.yaml`:

```yaml
  code_coverage_minimum:
    value: 90              # CHANGED from 80
    unit: percent
    enforcement: block_merge
    description: "Minimum code coverage required on every PR"
```

This single change propagates everywhere — every agent's quality gate that references `code_coverage_minimum` will now enforce 90%.

---

## 6. Removing an Agent

### Example: Removing the User Story Author (merging into PRD Specialist)

#### Step 1: Remove the Agent Definition

Delete `config/agents/user_story_author.yaml`.

#### Step 2: Remove from Team

Edit `config/teams.yaml`:

```yaml
  planning:
    display_name: "Planning Team"
    lead: prd_specialist
    members: [prd_specialist]                # REMOVED user_story_author
    domain: [requirements, documentation, user-stories, acceptance-criteria]
    parent_team: engineering
```

#### Step 3: Update Lead's Delegation Rules

Edit `config/agents/prd_specialist.yaml`:

```yaml
delegation:
  can_delegate_to: []    # REMOVED user_story_author
```

#### Step 4: Update Workflows

Edit `config/workflows.yaml` — merge the `story_creation` stage into `requirements`:

```yaml
      requirements:
        agents: [prd_specialist]
        outputs: [prd_document, user_stories]    # UPDATED: PRD Specialist now produces both
        next: [development]                       # UPDATED: skip story_creation stage
```

Delete the `story_creation` stage.

---

## 7. Validation Checklist

After any expansion change, verify:

| Check | How to Verify |
|-------|--------------|
| Every agent has a `reports_to` that exists | Run config validator |
| Every `can_delegate_to` target exists as an `agent_id` | Run config validator |
| Every team member exists as an `agent_id` | Run config validator |
| Team leads are listed in their team's `members` | Run config validator |
| Every tool in agent configs exists in `tools.yaml` | Run config validator |
| Every threshold reference in agents exists in `thresholds.yaml` | Run config validator |
| Workflow agent references match existing `agent_id`s | Run config validator |
| No circular delegation chains exist | Run config validator |
| `sub_teams` entries match actual team IDs | Run config validator |

The config validator (`src/config/validator.py`) runs all these checks automatically. Run it after every config change:

```bash
python -m src.config.validator
```

---

## 8. Common Expansion Scenarios

### 8.1 Scaling the Development Team

```
Current:                          After Adding 3 Agents:

Development Team                  Development Team
├── Code Reviewer (lead)          ├── Code Reviewer (lead)
├── Backend Specialist            ├── Backend Specialist
└── Frontend Specialist           ├── Frontend Specialist
                                  ├── Database Specialist    (NEW)
                                  ├── API Specialist         (NEW)
                                  └── Mobile Developer       (NEW)
```

**Impact**: Only `teams.yaml`, `code_reviewer.yaml` delegation, and 3 new agent YAMLs.

### 8.2 Adding a Full QA Team (Splitting from Delivery)

```
Current:                          After Split:

Delivery Team                     Delivery Team
├── DevOps Specialist (lead)      ├── DevOps Specialist (lead)
└── Tester Specialist             └── Release Manager        (NEW)

                                  QA Team                    (NEW)
                                  ├── Tester Specialist (lead, MOVED)
                                  ├── QA Automation Eng.     (NEW)
                                  └── Performance Tester     (NEW)
```

**Impact**: `teams.yaml` (new QA team, update delivery, update engineering sub_teams), `tester_specialist.yaml` (change `reports_to` to `engineering_lead`, add `can_delegate_to`), `devops_specialist.yaml` (remove tester from delegation), `engineering_lead.yaml` (add `tester_specialist` to delegation), 2 new agent YAMLs.

### 8.3 Creating a Design Team from Scratch

```
                                  Design Team                (NEW)
                                  ├── UX Lead               (NEW, team lead)
                                  ├── UI Designer           (NEW)
                                  └── UX Researcher         (NEW)
```

**Impact**: 3 new agent YAMLs, `teams.yaml` (new team), `engineering_lead.yaml` (add delegation), optionally `workflows.yaml` (add design stage before development).

---

## 9. Anti-Patterns to Avoid

| Anti-Pattern | Why It's Bad | What to Do Instead |
|---|---|---|
| Delegating across teams (Backend -> Tester) | Breaks hierarchy; unclear accountability | Route through team leads: Backend -> Code Reviewer -> Engineering Lead -> DevOps -> Tester |
| Creating agents without a team | Orphan agents can't receive delegated work | Every agent must belong to a team |
| Giving all tools to every agent | Violates least-privilege; agents do work outside their role | Only grant tools relevant to the agent's responsibilities |
| Skipping the config validator | Broken references cause runtime failures | Always run `python -m src.config.validator` after changes |
| Hardcoding thresholds in agent configs | Same value scattered across files; hard to update | Reference `thresholds.yaml` keys in quality gates |
| Creating deeply nested team hierarchies | Slow delegation chains; too many hops for simple tasks | Keep hierarchy to 3 levels max: Engineering Lead -> Team Lead -> Agent |
