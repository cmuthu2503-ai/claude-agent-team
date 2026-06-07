# Phase AE — Agentic Engineering Enhancements
# Task List & Implementation Tracker

---

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 1.0 |
| Created Date | 2026-05-24 |
| Last Updated | 2026-05-24 |
| Status | Draft |
| Product Owner | Chandramouli |
| PRD Reference | [docs/prd.md §6.9](prd.md#69-agentic-engineering-enhancements-phase-ae) |

---

## Table of Contents

| Phase | Section | Agents | Status |
|-------|---------|--------|--------|
| AE-1 | [Security Agent](#phase-ae-1--security-agent-security_specialist) | `security_specialist` | `[ ]` Not started |
| AE-2 | [Self-Learning Agent](#phase-ae-2--self-learning-agent-self_learning_agent) | `self_learning_agent` | `[ ]` Not started |
| AE-3 | [Quality Guardian](#phase-ae-3--quality-guardian-quality_guardian) | `quality_guardian` | `[ ]` Not started |
| AE-4 | [Ops/Heal Agent](#phase-ae-4--opsheal-agent-ops_heal_agent) | `ops_heal_agent` | `[ ]` Not started |
| AE-5 | [Architecture Reviewer](#phase-ae-5--architecture-reviewer-architecture_reviewer) | `architecture_reviewer` | `[ ]` Not started |

---

## How to Use This Document

- Tasks are grouped by agent phase in delivery order (AE-1 → AE-5)
- Each task has a unique ID: `AE{phase}-T{number}` (e.g., AE1-T03)
- Sub-tasks are numbered `T{number}.{sub}` (e.g., AE1-T03.2)
- **Every file to edit is named explicitly** — you should never need to guess
- **Every task ends with a test checkpoint** — do not move to the next task until the ✅ test passes
- Effort: **S** = a few hours, **M** = half a day, **L** = full day, **XL** = multiple days

### Status Legend

| Status | Meaning |
|--------|---------|
| `[ ]` | Not started |
| `[~]` | In progress |
| `[x]` | Completed |
| `[!]` | Blocked — see notes |
| `[-]` | Skipped / Deferred |

---

## Progress Summary

| Phase | Total Tasks | Done | In Progress | Blocked | Not Started |
|-------|------------|------|-------------|---------|-------------|
| AE-1: Security Agent | 10 | 0 | 0 | 0 | 10 |
| AE-2: Self-Learning Agent | 8 | 0 | 0 | 0 | 8 |
| AE-3: Quality Guardian | 6 | 0 | 0 | 0 | 6 |
| AE-4: Ops/Heal Agent | 8 | 0 | 0 | 0 | 8 |
| AE-5: Architecture Reviewer | 5 | 0 | 0 | 0 | 5 |
| **Total** | **37** | **0** | **0** | **0** | **37** |

---

## Dependency Graph (Task Level)

```
AE-1 (Security)        →  AE-3 (Quality Guardian, needs security_report as input)
AE-2 (Self-Learning)   →  standalone (post-processing hook, no upstream dep)
AE-3 (Quality)         →  requires AE-1 done first
AE-4 (Ops/Heal)        →  standalone (triggered post-deploy, no upstream dep)
AE-5 (Arch Reviewer)   →  standalone (runs in parallel with code_reviewer)

Recommended build order:  AE-5 → AE-2 → AE-1 → AE-3 → AE-4
(AE-5 is easiest — YAML only; AE-2 unblocks the self-learning loop immediately;
 AE-1 + AE-3 share a workflow position; AE-4 is the most infrastructure-heavy)
```

---

## Phase AE-1 — Security Agent (`security_specialist`)

> **What this phase does:** Adds an automated security scanning stage between `testing`
> and `code_commit`. Every request will now have its code checked for vulnerabilities,
> dependency CVEs, and accidentally included secrets before anything is committed to GitHub.
>
> **Why it matters:** Without this, generated code with known security issues ships silently.
> One undetected hard-coded API key or a critical CVE in a dependency can cost hours of
> incident response. This phase closes Stage 4 of the Agentic Engineering framework.

---

### AE1-T01 — Create the `security_scan` tool (Python backend)

**What:** Write a new Python module that wraps 4 security scanning CLI tools into a single callable tool for agents.
**File to create:** `src/tools/security_scan.py`
**Effort:** M

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T01.1 | Create file | Create `src/tools/security_scan.py` — start with an empty file, just the module docstring | `[ ]` |
| T01.2 | Add imports | Add at the top: `import asyncio`, `import subprocess`, `import json`, `import os` | `[ ]` |
| T01.3 | Create class | Add `class SecurityScanTool:` — this class will hold all scanning methods | `[ ]` |
| T01.4 | Add `run_bandit()` | Add `async def run_bandit(self, file_paths: list[str]) -> dict`. It runs `bandit -r <files> -f json -q` via `subprocess.run(["bandit", "-r", *file_paths, "-f", "json", "-q"], capture_output=True, text=True)`, then parses `result.stdout` as JSON. Return `{"tool": "bandit", "findings": [...], "error_count": N}`. If bandit is not installed, return `{"tool": "bandit", "status": "SKIPPED", "reason": "bandit not installed"}` | `[ ]` |
| T01.5 | Add `run_npm_audit()` | Add `async def run_npm_audit(self, project_dir: str = "frontend") -> dict`. It runs `subprocess.run(["npm", "audit", "--json"], cwd=project_dir, capture_output=True, text=True)`, parses the JSON output. Return `{"tool": "npm_audit", "vulnerabilities": [...], "high_count": N, "critical_count": N}` | `[ ]` |
| T01.6 | Add `run_safety_check()` | Add `async def run_safety_check(self) -> dict`. It runs `subprocess.run(["safety", "check", "--json"], capture_output=True, text=True)`, parses JSON. Return `{"tool": "safety", "vulnerabilities": [...], "critical_cve_count": N}`. If safety is not installed → return SKIPPED | `[ ]` |
| T01.7 | Add `run_detect_secrets()` | Add `async def run_detect_secrets(self, file_paths: list[str]) -> dict`. It runs `subprocess.run(["detect-secrets", "scan", *file_paths], capture_output=True, text=True)`, parses JSON output. Return `{"tool": "detect_secrets", "secrets_found": N, "details": [...]}`. If not installed → SKIPPED | `[ ]` |
| T01.8 | Add `execute()` main entry | Add `async def execute(self, inputs: dict) -> dict`. It calls all 4 scan methods above, collects results, then sets `verdict = "FAIL"` if any tool returned HIGH/CRITICAL findings or any secrets were detected, else `verdict = "PASS"`. Return `{"verdict": "PASS"/"FAIL", "scan_results": [...], "summary": "..."}` | `[ ]` |
| T01.9 | Add error safety | Wrap every `subprocess.run` call in try/except. If any scanner crashes unexpectedly, log the error and mark that scan as `"status": "ERROR"` — never let one broken scanner crash the whole tool | `[ ]` |
| T01.10 | ✅ Manual test | Create a small test file `tmp_test.py` with contents `import subprocess; subprocess.call(input(), shell=True)`. Run `python -c "import asyncio; from src.tools.security_scan import SecurityScanTool; t=SecurityScanTool(); print(asyncio.run(t.run_bandit(['tmp_test.py'])))"`. Verify it shows a finding. Delete `tmp_test.py` | `[ ]` |

---

### AE1-T02 — Register `security_scan` in the tool executor

**What:** Tell the agent executor about the new tool so agents can call it via Anthropic's tool-use protocol.
**File to edit:** Find where tools like `file_read` and `code_exec` are registered. Likely `src/tools/executor.py` or `src/agents/executor.py`.
**Effort:** S

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T02.1 | Find the registration file | Run `grep -rn "file_read" src/` to find the file where tools are registered as Anthropic tool schemas. Note the exact file path | `[ ]` |
| T02.2 | Import the new tool | At the top of that file, add: `from src.tools.security_scan import SecurityScanTool` | `[ ]` |
| T02.3 | Add to tool map | Find the dictionary/list where tools are mapped by name (e.g., `"file_read": FileReadTool()`). Add: `"security_scan": SecurityScanTool()` | `[ ]` |
| T02.4 | Add Anthropic tool schema | Find where the Anthropic tool definitions (JSON with `name`, `description`, `input_schema`) are listed. Add a definition for `security_scan` with `input_schema` containing one field: `files` (array of strings — the file paths to scan) | `[ ]` |
| T02.5 | ✅ Import test | Run `docker compose exec backend python -c "from src.tools.security_scan import SecurityScanTool; print('OK')"`. Should print `OK` with no errors | `[ ]` |

---

### AE1-T03 — Create `config/agents/security_specialist.yaml`

**What:** Define the new agent's identity, system prompt, tools, and output format. This is the "job description" for the security agent.
**File to create:** `config/agents/security_specialist.yaml`
**Effort:** M

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T03.1 | Copy template | Copy `config/agents/_template.yaml` to `config/agents/security_specialist.yaml` as a starting point | `[ ]` |
| T03.2 | Set identity fields | Set: `agent_id: security_specialist`, `display_name: "Security Specialist"`, `role: "Security Specialist"`, `team: delivery`, `reports_to: devops_specialist`, `model: claude-opus-4-7`, `max_iterations: 15` | `[ ]` |
| T03.3 | Write system_prompt | **Structure with the four canonical section headers first** (see `config/agents/backend_specialist.yaml` for the reference layout): `PROJECT CONTEXT:` → `YOUR OUTPUT FORMAT — follow this exactly:` → `LESSONS FROM PRIOR FAILURES — APPLY THESE AUTOMATICALLY:` → `RULES:` → `WEB TOOLS:`. **Then fill in the content:** (a) call `security_scan` tool with the list of generated files; (b) use `web_search` to verify any CVE found by the dependency scan; (c) perform an OWASP Top-10 manual review of the code logic; (d) classify all findings as CRITICAL/HIGH/MEDIUM/LOW; (e) output a Security Scan Report following the exact markdown table format in PRD §6.9.1; (f) set verdict to `FAIL` if any CRITICAL or HIGH finding exists, otherwise `PASS` | `[ ]` |
| T03.4 | Set tools list | Add: `tools: [security_scan, web_search, web_scrape]` | `[ ]` |
| T03.5 | Add responsibilities | In the `responsibilities:` block, write IDs **`SEC-001` through `SEC-007`** — **no `-R-` infix**. The PRD uses `SEC-R-001` in its requirement tables, but agent YAML files use a different format: `PREFIX-NNN` matching `TS-001`, `BE-001`, `CR-001` in existing agents. Copy descriptions from PRD §4.9 but use the corrected ID format | `[ ]` |
| T03.6 | Add output definition | Add the outputs block using **YAML block-list style** — write it as three lines: `outputs:` on its own line, then `  - name: "Security Report"` (2-space indent), then `    format: markdown` (4-space indent). **Do not** write `outputs: [{name: "Security Report", format: markdown}]` — that flow-dict shorthand is valid YAML but inconsistent with all 10 existing agent files and breaks when a second output is added | `[ ]` |
| T03.7 | Add `delegation:` block | Add the required delegation block — copy-paste this exactly: `delegation:` on one line, then `  can_delegate_to: []` (2-space indent), then `  max_concurrent_tasks: 3`. **This block is mandatory even for leaf agents that delegate to nobody** — the config loader raises `KeyError` at startup if it is absent | `[ ]` |
| T03.8 | Add `quality_gates:` block | Add `quality_gates: []` as an explicit empty list. Security pass/fail gates are enforced in `config/workflows.yaml`, not inside the agent YAML — but the config loader expects the key to exist. An **omitted** `quality_gates:` key returns `None` instead of `[]` and breaks gate evaluation in the workflow runner | `[ ]` |
| T03.9 | Add `metadata:` block | Add the metadata block: `metadata:` on one line, then `  created: "2026-05-24"` and `  version: "1.0"` on the next two lines. Required by `_template.yaml` | `[ ]` |
| T03.10 | ✅ Config validation | Run `docker compose exec backend python -m src.config.validator`. It should pass with no errors. If it says "unknown agent" or "missing field", fix the YAML and re-run | `[ ]` |

---

### AE1-T04 — Add `security_scan` to `config/tools.yaml`

**What:** Register the tool in the config system so access control is enforced (only `security_specialist` can call it).
**File to edit:** `config/tools.yaml`
**Effort:** S

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T04.1 | Open tools.yaml | Open `config/tools.yaml` and scroll to the bottom of the file | `[ ]` |
| T04.2 | Add tool entry | Append the following block at the end (keep the same indentation style as the other tools): `security_scan:\n  description: "Run SAST (bandit/eslint-security), dependency audit (safety/npm audit), and secrets detection (detect-secrets) against project files"\n  category: security\n  available_to:\n    - security_specialist` | `[ ]` |
| T04.3 | ✅ Config validation | Run `docker compose exec backend python -m src.config.validator`. Should pass | `[ ]` |

---

### AE1-T05 — Add `security_specialist` to `config/teams.yaml`

**What:** Tell the team system that `security_specialist` is a member of the delivery team, so it shows up in team listings and delegation checks.
**File to edit:** `config/teams.yaml`
**Effort:** S

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T05.1 | Open teams.yaml | Open `config/teams.yaml` and find the `delivery` team block | `[ ]` |
| T05.2 | Add to members list | In the `members:` list under delivery, add `- security_specialist` (same indentation as `devops_specialist` and `tester_specialist`) | `[ ]` |
| T05.3 | ✅ Config validation | Run config validator. Check that no "unknown agent in team" error appears | `[ ]` |

---

### AE1-T06 — Insert `security` stage into `config/workflows.yaml`

**What:** Modify the `feature_development` and `bug_fix` workflows so the security scanning stage runs between testing and code commit.
**File to edit:** `config/workflows.yaml`
**Effort:** M

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T06.1 | Edit feature_development | Open `config/workflows.yaml`. Find the `testing` stage under `feature_development`. Change its `next:` from `[code_commit]` to `[security]` | `[ ]` |
| T06.2 | Add security stage (feature_development) | Directly after the `testing` stage block, insert the new `security` stage: `security:\n  agents: [security_specialist]\n  inputs: [backend_code, frontend_code, test_report]\n  outputs: [security_report]\n  quality_gates:\n    - gate: no_critical_vulnerabilities\n      required: true\n    - gate: no_secrets_detected\n      required: true\n  on_fail: development\n  next: [code_commit]` | `[ ]` |
| T06.3 | Edit bug_fix | Find the `review_and_test` stage under `bug_fix`. Change its `next:` from `[code_commit]` to `[security]`. Add the same `security` stage block after `review_and_test` (same YAML as T06.2 except `on_fail: fix`) | `[ ]` |
| T06.4 | ✅ Config validation | Run `docker compose exec backend python -m src.config.validator`. Also run: `docker compose exec backend python -c "from src.config.loader import ConfigLoader; c=ConfigLoader().load_all(); print(list(c.workflows['feature_development'].stages.keys()))"` — the output list should include `'security'` | `[ ]` |

---

### AE1-T07 — Add quality gate evaluators for the security stage

**What:** The workflow runner needs to know how to evaluate `no_critical_vulnerabilities` and `no_secrets_detected` — it reads the security agent's output and decides pass/fail.
**File to edit:** `src/workflows/runner.py`
**Effort:** M

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T07.1 | Find existing gate evaluators | Search `src/workflows/runner.py` for where `coverage_check` or `review_approval` gates are evaluated. Understand the pattern (likely a method like `_evaluate_gate(gate_name, stage_outputs)`) | `[ ]` |
| T07.2 | Add `no_critical_vulnerabilities` evaluator | In the gate evaluation logic, add a case for `no_critical_vulnerabilities`: look in the security_report output text for the string `Verdict: ✅ PASS` — if found, gate passes. If the text contains `Verdict: ❌ FAIL`, gate fails | `[ ]` |
| T07.3 | Add `no_secrets_detected` evaluator | Add a case for `no_secrets_detected`: look for the secrets detection row in the report — if it shows `0 patterns` or `0 secrets`, gate passes. Any non-zero number of secrets = gate fail | `[ ]` |
| T07.4 | Add to thresholds.yaml | Open `config/thresholds.yaml` and add: `no_critical_vulnerabilities: true` and `no_secrets_detected: true` as threshold keys | `[ ]` |
| T07.5 | ✅ Unit test | Run `docker compose exec backend pytest tests/test_workflow_engine.py -xvs`. All existing tests should still pass (you haven't broken any existing gate logic) | `[ ]` |

---

### AE1-T08 — Add `security_specialist` to `_LESSONS_CONSUMER_AGENTS`

**What:** Make the security agent receive the `agent-lessons-learned.md` content injected into its system prompt so it benefits from past lessons.
**File to edit:** `src/agents/base.py`
**Effort:** S

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T08.1 | Find the set | Open `src/agents/base.py` and search for `_LESSONS_CONSUMER_AGENTS`. You will see a Python set listing agent IDs | `[ ]` |
| T08.2 | Add the agent | Add `"security_specialist"` to the set (copy the syntax of the existing entries) | `[ ]` |
| T08.3 | ✅ Verify injection | Run `docker compose exec backend python -c "from src.agents.base import _LESSONS_CONSUMER_AGENTS; print('security_specialist' in _LESSONS_CONSUMER_AGENTS)"`. Should print `True` | `[ ]` |

---

### AE1-T09 — Persist `security_report` in the documents table

**What:** After the security agent runs, save its output as a searchable document (same as how `code_review` and `test_report` outputs are saved today).
**File to edit:** `src/core/orchestrator.py`
**Effort:** S

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T09.1 | Find existing save logic | Search `src/core/orchestrator.py` for `document_type.*code_review` or `document_type.*test_report` to find the code that saves agent outputs as documents | `[ ]` |
| T09.2 | Add security_report save | Copy that pattern and add a parallel block that saves the security agent's output as `document_type = "security_report"` when the security stage completes | `[ ]` |
| T09.3 | ✅ Database check | Submit a test request via the UI (even if it fails mid-way). Then run `docker compose exec backend python -c "import asyncio, aiosqlite; asyncio.run(main())"` where `main()` does `async with aiosqlite.connect('data/agent_team.db') as db: rows = await (await db.execute(\"SELECT * FROM documents WHERE document_type='security_report'\")).fetchall(); print(rows)`. Should show at least one row | `[ ]` |

---

### AE1-T10 — Write pytest tests for the security stage

**What:** Automated tests prove the security scanning works correctly before you rely on it in production.
**File to create:** `tests/test_security_agent.py`
**Effort:** M

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T10.1 | Create test file | Create `tests/test_security_agent.py` with the standard imports: `import pytest`, `from src.tools.security_scan import SecurityScanTool` | `[ ]` |
| T10.2 | Test: clean file passes | Write `test_security_scan_clean_file_passes()`. Create a temporary Python file with no security issues (e.g., just `x = 1 + 1`). Call `SecurityScanTool().run_bandit([tmpfile])`. Assert the result shows 0 critical/high findings | `[ ]` |
| T10.3 | Test: dangerous code fails | Write `test_security_scan_detects_shell_injection()`. Write a temp file containing `import subprocess; subprocess.call(input(), shell=True)`. Call `run_bandit()` on it. Assert it returns at least one HIGH/CRITICAL finding | `[ ]` |
| T10.4 | Test: secret detection | Write `test_security_scan_detects_hardcoded_secret()`. Write a temp file with `aws_secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"`. Call `run_detect_secrets()`. Assert `secrets_found > 0` | `[ ]` |
| T10.5 | Test: PASS verdict | Write `test_security_scan_execute_returns_pass()`. Feed a clean set of files to `execute()`. Assert `result["verdict"] == "PASS"` | `[ ]` |
| T10.6 | Test: FAIL verdict | Write `test_security_scan_execute_returns_fail_on_vulnerability()`. Feed a file with a known issue. Assert `result["verdict"] == "FAIL"` | `[ ]` |
| T10.7 | ✅ Run all tests | Run `docker compose exec backend pytest tests/test_security_agent.py -xvs`. All 5 tests should pass | `[ ]` |

---

## Phase AE-2 — Self-Learning Agent (`self_learning_agent`)

> **What this phase does:** Adds an agent that automatically writes new lessons to
> `docs/agent-lessons-learned.md` whenever a request fails. Today you have to manually
> notice a failure pattern and add a lesson by hand. After this phase, the platform
> notices its own mistakes and records them automatically.
>
> **Why it matters:** This is the highest-leverage phase. Every lesson recorded
> compounds across all future requests. The runtime injection mechanism already
> reads the lessons doc on every agent invocation — we just needed an agent that
> could write to it.

---

### AE2-T01 — Create the `lessons_writer` tool (Python backend)

**What:** A specially restricted file-write tool that can ONLY write to `docs/agent-lessons-learned.md`. The path restriction is a safety guard — we never want an agent accidentally overwriting other files.
**File to create:** `src/tools/lessons_writer.py`
**Effort:** S

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T01.1 | Create file | Create `src/tools/lessons_writer.py` | `[ ]` |
| T01.2 | Add constant | Add `LESSONS_FILE = "docs/agent-lessons-learned.md"` at the top — single source of truth for the allowed path | `[ ]` |
| T01.3 | Create class | Add `class LessonsWriterTool:` | `[ ]` |
| T01.4 | Add `read_lessons()` | Add `async def read_lessons(self) -> str`. Opens `LESSONS_FILE`, reads the full text, returns it as a string. This lets the agent check what lessons already exist before writing a new one | `[ ]` |
| T01.5 | Add `append_lesson()` | Add `async def append_lesson(self, lesson_text: str) -> dict`. Opens `LESSONS_FILE` in append mode (`"a"`), writes `"\n\n" + lesson_text`, closes the file. Returns `{"success": True, "path": LESSONS_FILE}` | `[ ]` |
| T01.6 | Add path guard | In `append_lesson()`, add a check at the top: if the system has `DRY_RUN=true` in env vars, just print the lesson to the log and return `{"success": True, "dry_run": True}` without writing to disk | `[ ]` |
| T01.7 | Add `execute()` | Add `async def execute(self, inputs: dict) -> dict`. Check `inputs["action"]`: if `"read"` → call `read_lessons()`, if `"append"` → call `append_lesson(inputs["lesson_text"])`. Any other action → raise ValueError | `[ ]` |
| T01.8 | ✅ Manual test | Run: `docker compose exec backend python -c "import asyncio; from src.tools.lessons_writer import LessonsWriterTool; t=LessonsWriterTool(); print(asyncio.run(t.read_lessons())[:200])"`. Should print the first 200 characters of the existing lessons file without error | `[ ]` |

---

### AE2-T02 — Register `lessons_writer` in the tool executor

**What:** Same pattern as AE1-T02. Make the tool callable by agents.
**File to edit:** Same tool executor file found in AE1-T02
**Effort:** S

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T02.1 | Import | Add `from src.tools.lessons_writer import LessonsWriterTool` at the top of the executor file | `[ ]` |
| T02.2 | Register | Add `"lessons_writer": LessonsWriterTool()` to the tool map | `[ ]` |
| T02.3 | Add Anthropic schema | Add the tool definition JSON with two actions described in the schema: `read` (no extra params) and `append` (requires `lesson_text: string`) | `[ ]` |
| T02.4 | ✅ Import test | Run `docker compose exec backend python -c "from src.tools.lessons_writer import LessonsWriterTool; print('OK')"` | `[ ]` |

---

### AE2-T03 — Create `config/agents/self_learning_agent.yaml`

**What:** Define the agent that analyses failures and writes new lessons.
**File to create:** `config/agents/self_learning_agent.yaml`
**Effort:** M

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T03.1 | Copy template | Copy `_template.yaml` to `self_learning_agent.yaml` | `[ ]` |
| T03.2 | Set identity | Set: `agent_id: self_learning_agent`, `display_name: "Self-Learning Agent"`, `role: "Self-Learning Agent"`, `team: engineering`, `reports_to: project_orchestrator`, `model: claude-opus-4-7`, `max_iterations: 10` | `[ ]` |
| T03.3 | Write system_prompt | **Structure with these exact section headers in order** (no `WEB TOOLS:` section — this agent has no web tools): `YOUR ROLE:` → `HOW TO ANALYSE FAILURES:` → `OUTPUT FORMAT:` → `RULES:`. See `config/agents/backend_specialist.yaml` for the general section-header pattern. **Then fill in the content:** (1) call `lessons_writer` with action `"read"` to get existing lessons; (2) analyse the provided failure context (request outputs, error messages) to extract the root cause; (3) check if the pattern already appears in existing lessons — if YES, draft a `[Update YYYY-MM-DD]` note; if NO, draft a full `## L<NN>` section with Signature / Cause / Fix / Observed-in fields; (4) call `lessons_writer` with action `"append"` to write the lesson; (5) if no new pattern is found, do nothing and output "No new lesson needed" | `[ ]` |
| T03.4 | Set tools | Add: `tools: [lessons_writer, file_read]`. **Do NOT add `git_operations`** — this agent reads failure context from the documents table via `file_read`, not from git history. Additionally, `git_operations.available_to` in `config/tools.yaml` does not include `self_learning_agent`; calling the tool would raise a `ToolNotPermittedError` at runtime (see PRD §6.9.6 — YAML-007) | `[ ]` |
| T03.5 | Add note in YAML | Add a comment above the tools list: `# DO NOT add self_learning_agent to _LESSONS_CONSUMER_AGENTS — it writes the doc, not reads it` | `[ ]` |
| T03.6 | Add `delegation:` block | Add: `delegation:` / `  can_delegate_to: []` / `  max_concurrent_tasks: 3`. Mandatory for every agent, including this one which has no subordinates | `[ ]` |
| T03.7 | Add `quality_gates:` block | Add `quality_gates: []`. This agent is a post-processing hook, not a gated workflow stage — it owns no quality gates. Still required as an explicit empty list so the config loader does not return `None` | `[ ]` |
| T03.8 | Add `metadata:` block | Add: `metadata:` / `  created: "2026-05-24"` / `  version: "1.0"` | `[ ]` |
| T03.9 | ✅ Config validation | Run `docker compose exec backend python -m src.config.validator`. Pass = OK | `[ ]` |

---

### AE2-T04 — Register tool and team membership

**Files to edit:** `config/tools.yaml`, `config/teams.yaml`
**Effort:** S

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T04.1 | Add to tools.yaml | Append to `config/tools.yaml`: `lessons_writer:\n  description: "Scoped append-only write for docs/agent-lessons-learned.md — records new failure patterns discovered by the self_learning_agent"\n  category: learning\n  available_to:\n    - self_learning_agent` | `[ ]` |
| T04.2 | Add to teams.yaml | Find the `engineering` team in `config/teams.yaml`. Add `- self_learning_agent` to its members list | `[ ]` |
| T04.3 | ✅ Config validation | Run config validator. Both changes should pass | `[ ]` |

---

### AE2-T05 — Confirm `self_learning_agent` is NOT in `_LESSONS_CONSUMER_AGENTS`

**What:** Explicit verification step — this agent must never receive the lessons doc in its prompt (it writes the doc; having it read its own writes creates circular behaviour).
**File to check:** `src/agents/base.py`
**Effort:** S

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T05.1 | Search the set | Open `src/agents/base.py`, find `_LESSONS_CONSUMER_AGENTS`. Verify `"self_learning_agent"` is NOT in the set | `[ ]` |
| T05.2 | ✅ Assert exclusion | Run: `docker compose exec backend python -c "from src.agents.base import _LESSONS_CONSUMER_AGENTS; assert 'self_learning_agent' not in _LESSONS_CONSUMER_AGENTS; print('OK — correctly excluded')"` | `[ ]` |

---

### AE2-T06 — Add the post-failure trigger hook in the orchestrator

**What:** After any request is marked as FAILED, the orchestrator should silently kick off the self_learning_agent as a background task. The key word is "silently" — this must never affect or delay the main request outcome.
**File to edit:** `src/core/orchestrator.py`
**Effort:** M

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T06.1 | Find FAILED status set | Search `src/core/orchestrator.py` for where `status` is set to `FAILED` (or `RequestStatus.FAILED`). There should be one primary location | `[ ]` |
| T06.2 | Add background task | Directly after the FAILED status is written to the DB, add: `asyncio.create_task(self._trigger_self_learning(request.id))`. This starts the self-learning analysis in the background and the current code flow immediately continues | `[ ]` |
| T06.3 | Create the method | Add a new method to the Orchestrator class: `async def _trigger_self_learning(self, request_id: str) -> None:`. Inside: (a) fetch all documents for this request from state_store; (b) build a context dict with the agent outputs and the failure reason; (c) call `await self.executor.execute_agent("self_learning_agent", request_id, context)`; (d) wrap everything in `try/except Exception as e: logger.error(f"Self-learning hook failed: {e}")` — never propagate exceptions | `[ ]` |
| T06.4 | Add rollback trigger | Find where `deployment.rollback_requested` is handled. Add the same `asyncio.create_task(self._trigger_self_learning(request_id))` call there too | `[ ]` |
| T06.5 | ✅ Smoke test | Submit a request via the UI with a deliberately bad description that will fail. Watch `docker compose logs backend`. You should see a log line like `"Triggered self-learning analysis for REQ-XXXXXX"` after the FAILED status appears | `[ ]` |

---

### AE2-T07 — Add `lessons.*` event types

**What:** Emit events when lessons are added or skipped so the frontend can show a notification.
**File to edit:** `src/core/events.py` (or wherever event type constants are defined)
**Effort:** S

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T07.1 | Find event types file | Run `grep -rn "request.status_changed\|EventType" src/` to find where event types are defined | `[ ]` |
| T07.2 | Add new types | Add two new event type constants: `LESSONS_ADDED = "lessons.added"` and `LESSONS_NO_NEW_PATTERN = "lessons.no_new_pattern"` | `[ ]` |
| T07.3 | Emit in agent output handler | In `_trigger_self_learning()`, after `execute_agent` returns: if the output contains "new lesson added", emit a `lessons.added` event; if it contains "No new lesson needed", emit `lessons.no_new_pattern` | `[ ]` |
| T07.4 | ✅ WebSocket check | With the backend running, open browser dev-tools Network tab, connect to the WebSocket. Trigger a failed request. After the FAILED status, you should see a `lessons.*` event appear in the WebSocket messages | `[ ]` |

---

### AE2-T08 — Write pytest tests for the self-learning agent

**File to create:** `tests/test_self_learning_agent.py`
**Effort:** M

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T08.1 | Create file | Create `tests/test_self_learning_agent.py` | `[ ]` |
| T08.2 | Test: read lessons | Write `test_read_lessons_returns_string()`. Call `LessonsWriterTool().execute({"action": "read"})`. Assert the result is a non-empty string that contains "# Agent Lessons Learned" | `[ ]` |
| T08.3 | Test: append is idempotent | Write `test_append_then_cleanup()`. Call `execute({"action": "append", "lesson_text": "## L999 — TEST LESSON\nSignature: TEST"})`. Read the file. Assert "L999" appears. Then call `append_lesson` again with the same text — the agent should deduplicate (this tests the DRY-RUN guard at minimum) | `[ ]` |
| T08.4 | Test: dry run does not write | Write `test_dry_run_mode()`. Set env var `DRY_RUN=true`. Call `execute({"action": "append", "lesson_text": "## L998 — DRY RUN TEST"})`. Read the file. Assert "L998" does NOT appear in the file. Unset env var | `[ ]` |
| T08.5 | Test: orchestrator triggers hook | Write `test_orchestrator_triggers_self_learning_on_fail()`. Create a mock orchestrator, call the FAILED path. Assert `execute_agent` was called with `"self_learning_agent"` as the first argument | `[ ]` |
| T08.6 | ✅ Run all | Run `docker compose exec backend pytest tests/test_self_learning_agent.py -xvs`. All tests should pass. Also manually delete any test lesson lines (L999, L998) from `docs/agent-lessons-learned.md` after the test run | `[ ]` |

---

## Phase AE-3 — Quality Guardian (`quality_guardian`)

> **What this phase does:** Adds a "big picture" reviewer that checks things the code
> reviewer cannot: does the frontend actually call the endpoints the backend defined?
> Did the tester cover every PRD requirement? Are there N+1 query patterns?
> Its risk rating also feeds the deployment supervisor's decision.
>
> **Depends on:** AE-1 must be complete first (quality_guardian accepts `security_report` as input)

---

### AE3-T01 — Create `config/agents/quality_guardian.yaml`

**File to create:** `config/agents/quality_guardian.yaml`
**Effort:** M

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T01.1 | Copy template | Copy `_template.yaml` to `quality_guardian.yaml` | `[ ]` |
| T01.2 | Set identity | Set: `agent_id: quality_guardian`, `display_name: "Quality Guardian"`, `role: "Quality Guardian"`, `team: delivery`, `reports_to: devops_specialist`, `model: claude-opus-4-7`, `max_iterations: 20` | `[ ]` |
| T01.3 | Write system_prompt — structure + API check | **Before writing any content, establish the four canonical section headers:** `PROJECT CONTEXT:` → `YOUR OUTPUT FORMAT — follow this exactly:` → `LESSONS FROM PRIOR FAILURES — APPLY THESE AUTOMATICALLY:` → `RULES:` → `WEB TOOLS:` (see `config/agents/backend_specialist.yaml` for the reference layout). **First content rule (API contract check):** Compare the backend FastAPI route response models and Pydantic schemas against the TypeScript API call signatures in the frontend. Flag any mismatch in field names, types, or URL paths as CRITICAL | `[ ]` |
| T01.4 | Write system_prompt (traceability) | Second section: "For every REQ-XXX item in the PRD, check the test report for at least one test case line that says 'Traces To: US-XXX'. Any REQ-XXX with zero test coverage = HIGH finding." | `[ ]` |
| T01.5 | Write system_prompt (lessons compliance) | Third section: "Read docs/agent-lessons-learned.md. For each lesson, check if its Signature text appears anywhere in the provided code or reports. If a known failure pattern is repeated = HIGH finding." | `[ ]` |
| T01.6 | Write system_prompt (output format) | Final section: "Output: (a) a findings table, (b) `Risk: low/medium/high` on its own line, (c) `Verdict: APPROVED` or `Verdict: ESCALATED` with one-line reason." | `[ ]` |
| T01.7 | Set tools | Add: `tools: [file_read, code_analysis, web_search]` | `[ ]` |
| T01.8 | Add responsibilities | In the `responsibilities:` block, write IDs **`QG-001` through `QG-006`** (no `-R-` infix). Copy descriptions from PRD §4.11. Category: `quality` for all entries. Example format: `- id: QG-001` / `  description: "Verify frontend TypeScript API calls match backend Pydantic response models"` / `  category: quality` | `[ ]` |
| T01.9 | Add `delegation:` block | Add: `delegation:` / `  can_delegate_to: []` / `  max_concurrent_tasks: 3`. Required even for leaf agents | `[ ]` |
| T01.10 | Add `quality_gates:` block | Add `quality_gates: []`. The quality guardian's verdict is evaluated by the workflow runner reading its output text — it does not own YAML-level quality gates on itself. Explicit empty list required | `[ ]` |
| T01.11 | Add `metadata:` block | Add: `metadata:` / `  created: "2026-05-24"` / `  version: "1.0"` | `[ ]` |
| T01.12 | ✅ Config validation | Run config validator | `[ ]` |

---

### AE3-T02 — Update `config/workflows.yaml` for parallel review

**What:** Restructure the `review` stage so `code_reviewer`, `quality_guardian` (and later `architecture_reviewer`) all run at the same time rather than sequentially.
**File to edit:** `config/workflows.yaml`
**Effort:** M

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T02.1 | Back up current review stage | Before editing, copy the existing `review:` block into a comment above it so you have a reference | `[ ]` |
| T02.2 | Convert to parallel block | Replace the flat `review` stage with a parallel block — use the `parallel:` keyword (same structure as the `development` stage). Create two sub-stages: `code_review` (agents: [code_reviewer]) and `quality_check` (agents: [quality_guardian]) | `[ ]` |
| T02.3 | Set quality_check inputs | The `quality_check` sub-stage needs: `inputs: [prd_document, user_stories, backend_code, frontend_code, test_report, security_report]`, `outputs: [quality_report]` | `[ ]` |
| T02.4 | Add quality gate | Keep the existing `review_approval` gate. Add a new gate: `quality_guardian_approval` with `required: true` | `[ ]` |
| T02.5 | ✅ Verify YAML parses | Run `docker compose exec backend python -c "from src.config.loader import ConfigLoader; c=ConfigLoader().load_all(); print('review stage type:', type(c.workflows['feature_development'].stages['review']).__name__)"`. Should show it is recognized as a parallel stage | `[ ]` |

---

### AE3-T03 — Add `quality_guardian_approval` gate evaluator

**File to edit:** `src/workflows/runner.py`
**Effort:** S

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T03.1 | Add evaluator | In the gate evaluation section of `runner.py`, add a case for `quality_guardian_approval`: search the quality_guardian's output for `Verdict: APPROVED` (pass) or `Verdict: ESCALATED` (fail) | `[ ]` |
| T03.2 | Add to thresholds.yaml | Add `quality_guardian_approval: true` to `config/thresholds.yaml` | `[ ]` |
| T03.3 | ✅ Unit test | Run `docker compose exec backend pytest tests/test_workflow_engine.py -xvs`. Existing tests must still pass | `[ ]` |

---

### AE3-T04 — Feed quality risk rating into the supervisor judge prompt

**What:** The deployment supervisor's judge LLM currently decides the deployment strategy (full/staging-only/hold) based only on commit metadata. Now it should also consider the quality_guardian's risk assessment.
**File to edit:** `supervisor/deploy_supervisor.py`
**Effort:** M

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T04.1 | Find judge prompt builder | Search `supervisor/deploy_supervisor.py` for where the LLM judge prompt is assembled (look for a string building block that mentions "strategy" or "risk") | `[ ]` |
| T04.2 | Fetch quality report | Before building the prompt, query the SQLite DB for the latest `quality_report` document for this `request_id`: `SELECT content FROM documents WHERE request_id=? AND document_type='quality_report' ORDER BY created_at DESC LIMIT 1` | `[ ]` |
| T04.3 | Extract risk field | Parse the quality report content for the line `Risk: low`, `Risk: medium`, or `Risk: high` using a simple regex or string search | `[ ]` |
| T04.4 | Inject into prompt | Add to the judge prompt: `"The quality_guardian assessed this commit as {risk} risk. If risk is HIGH, prefer deploy_staging_only. If any CRITICAL finding remains unresolved, prefer hold."` | `[ ]` |
| T04.5 | ✅ Smoke test | Run a full request cycle with the supervisor running. Check `supervisor.log` — the judge's strategy reasoning should now mention the quality risk level | `[ ]` |

---

### AE3-T05 — Register team membership and save quality_report document

**Files to edit:** `config/teams.yaml`, `src/core/orchestrator.py`
**Effort:** S

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T05.1 | Add to teams.yaml | Add `- quality_guardian` to the delivery team members list in `config/teams.yaml` | `[ ]` |
| T05.2 | Save quality_report | In `src/core/orchestrator.py`, add the same document-save pattern as AE1-T09 but for `document_type = "quality_report"` when the quality_check sub-stage completes | `[ ]` |
| T05.3 | ✅ Config validation | Run config validator | `[ ]` |

---

### AE3-T06 — Write pytest tests for the quality_guardian

**File to create:** `tests/test_quality_guardian.py`
**Effort:** M

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T06.1 | Create file | Create `tests/test_quality_guardian.py` | `[ ]` |
| T06.2 | Test: API mismatch detected | Write `test_api_mismatch_triggers_escalated()`. Provide a mock backend output defining `{user_id: int, email: str}` and a mock frontend output calling `.data.userId` and `.data.emailAddress`. Build a fake quality_guardian prompt + response cycle (or test the gate evaluator parsing logic). Assert the output contains `Verdict: ESCALATED` | `[ ]` |
| T06.3 | Test: missing traceability | Write `test_missing_req_traceability_triggers_high_finding()`. Provide a PRD mentioning `REQ-003` and a test_report with no "Traces To" mention of `REQ-003`. Assert the response contains a HIGH finding | `[ ]` |
| T06.4 | Test: clean outputs approve | Write `test_clean_outputs_result_in_approved()`. Feed matching BE/FE outputs and full test coverage. Assert `Verdict: APPROVED` | `[ ]` |
| T06.5 | ✅ Run all | Run `docker compose exec backend pytest tests/test_quality_guardian.py -xvs` | `[ ]` |

---

## Phase AE-4 — Ops/Heal Agent (`ops_heal_agent`)

> **What this phase does:** Adds a post-deployment watchdog. After every successful
> deploy the ops_heal_agent runs for 10 minutes, checking health endpoints and
> container status. If something breaks it either fixes it automatically (restart)
> or escalates to rollback. A System Health pill is added to the Command Center.
>
> **Why it matters:** Today, broken containers after a deploy are discovered by
> accident. This phase reduces MTTR from "whenever someone notices" to ~2 minutes.

---

### AE4-T01 — Create the `ops_check` tool (Python backend)

**What:** A tool that lets the ops agent interact with the running Docker stack — check health, inspect containers, restart services, read logs.
**File to create:** `src/tools/ops_check.py`
**Effort:** M

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T01.1 | Create file | Create `src/tools/ops_check.py` | `[ ]` |
| T01.2 | Add imports | Add: `import urllib.request`, `import subprocess`, `import json`, `import time` | `[ ]` |
| T01.3 | Create class | Add `class OpsCheckTool:` | `[ ]` |
| T01.4 | Add `check_health()` | Add `async def check_health(self, url: str = "http://localhost:8000/api/v1/health") -> dict`. Use `urllib.request.urlopen(url, timeout=5)` (NOT curl — curl doesn't exist on Windows). Record start time before the call, end time after, compute `latency_ms = (end - start) * 1000`. Return `{"status": "healthy"/"unhealthy", "latency_ms": N, "response_code": 200}`. On exception (timeout, connection refused) return `{"status": "unreachable", "error": str(e)}` | `[ ]` |
| T01.5 | Add `get_container_status()` | Add `async def get_container_status(self) -> list`. Run `subprocess.run(["docker", "ps", "--format", "{{json .}}"], capture_output=True, text=True)`. Parse each line as JSON. Return a list of dicts: `[{"name": "...", "status": "Up 2 hours (healthy)", "is_healthy": True, "restart_count": 0}]`. Parse `(unhealthy)` and `(Restarting)` from the status string | `[ ]` |
| T01.6 | Add `restart_container()` | Add `async def restart_container(self, service: str) -> dict`. Run `subprocess.run(["docker", "compose", "restart", service], capture_output=True, text=True)`. Return `{"success": returncode == 0, "output": result.stdout, "error": result.stderr}` | `[ ]` |
| T01.7 | Add `get_logs()` | Add `async def get_logs(self, service: str, lines: int = 50) -> str`. Run `subprocess.run(["docker", "compose", "logs", "--tail", str(lines), service], capture_output=True, text=True)`. Return `result.stdout` | `[ ]` |
| T01.8 | Add `execute()` | Add `async def execute(self, inputs: dict) -> dict`. Route based on `inputs["action"]`: `"health_check"` → `check_health()`, `"container_status"` → `get_container_status()`, `"restart"` → `restart_container(inputs["service"])`, `"logs"` → `get_logs(inputs["service"])` | `[ ]` |
| T01.9 | ✅ Test with running stack | With the dev stack running (`make dev`), run: `docker compose exec backend python -c "import asyncio; from src.tools.ops_check import OpsCheckTool; t=OpsCheckTool(); print(asyncio.run(t.check_health()))"`. Should return `{"status": "healthy", ...}` | `[ ]` |

---

### AE4-T02 — Register `ops_check` tool and create agent YAML

**Files to create/edit:** `src/tools/executor.py`, `config/agents/ops_heal_agent.yaml`, `config/tools.yaml`, `config/teams.yaml`
**Effort:** M

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T02.1 | Register tool | Import `OpsCheckTool` in the executor file. Add `"ops_check": OpsCheckTool()` to the tool map. Add the Anthropic schema with the 4 actions | `[ ]` |
| T02.2 | Create agent YAML | Create `config/agents/ops_heal_agent.yaml`. Set identity: `agent_id: ops_heal_agent`, `display_name: "Ops/Heal Agent"`, `role: "Ops/Heal Agent"`, `team: delivery`, `reports_to: devops_specialist`, `model: claude-opus-4-7`, `max_iterations: 20` | `[ ]` |
| T02.3 | Write system_prompt | **Structure with canonical headers:** `PROJECT CONTEXT:` → `YOUR OUTPUT FORMAT — follow this exactly:` → `LESSONS FROM PRIOR FAILURES — APPLY THESE AUTOMATICALLY:` → `RULES:` (no `WEB TOOLS:` — this agent has no web tools; see `config/agents/backend_specialist.yaml`). **Content:** (1) call `ops_check` health_check at start; (2) call `ops_check` container_status; (3) if a container is `(unhealthy)` or `(Restarting)`, call `ops_check` restart once; (4) wait 60s, re-check health; (5) if still unhealthy after restart, output `Status: CRITICAL` and recommend rollback; (6) otherwise output `Status: HEALTHY` or `Status: DEGRADED` based on latency | `[ ]` |
| T02.4 | Set tools | Add `tools: [ops_check, file_read]` | `[ ]` |
| T02.5 | Add to tools.yaml | Append: `ops_check:\n  description: "Health endpoint polling, docker ps inspection, container restart, and log tail — post-deploy monitoring tool"\n  category: infrastructure\n  available_to:\n    - ops_heal_agent` | `[ ]` |
| T02.6 | Add to teams.yaml | Add `- ops_heal_agent` to delivery team members | `[ ]` |
| T02.7 | Add to lessons consumers | Open `src/agents/base.py`, add `"ops_heal_agent"` to `_LESSONS_CONSUMER_AGENTS` | `[ ]` |
| T02.8 | Add responsibilities | In the `responsibilities:` block, write IDs **`OPS-001` through `OPS-007`** (no `-R-` infix). Copy descriptions from PRD §4.12. Category: `deployment` for all entries | `[ ]` |
| T02.9 | Add `outputs:` block | Add using block-list style: `outputs:` / `  - name: "Ops Health Report"` / `    format: markdown`. Do not use the flow-dict shorthand | `[ ]` |
| T02.10 | Add `delegation:` block | Add: `delegation:` / `  can_delegate_to: []` / `  max_concurrent_tasks: 3` | `[ ]` |
| T02.11 | Add `quality_gates:` block | Add `quality_gates: []`. Required as an explicit empty list | `[ ]` |
| T02.12 | Add `metadata:` block | Add: `metadata:` / `  created: "2026-05-24"` / `  version: "1.0"` | `[ ]` |
| T02.13 | ✅ Config validation | Run config validator | `[ ]` |

---

### AE4-T03 — Add the post-deploy trigger in the deployment supervisor

**What:** After the supervisor records `completed`, it calls a new backend API endpoint to start the ops monitoring session.
**Files to edit:** `supervisor/deploy_supervisor.py`, `src/api/routes/` (new endpoint), `src/main.py`
**Effort:** L

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T03.1 | Find completion point | Open `supervisor/deploy_supervisor.py`. Find the exact line where `current_step = 'completed'` is written to the DB | `[ ]` |
| T03.2 | Add HTTP call to backend | After the completed step is written, add: `urllib.request.urlopen(f"http://localhost:8000/api/v1/deployments/{deployment_id}/ops-monitor", data=b"{}", timeout=5)`. Wrap in try/except — if the backend is down, log and continue; don't crash the supervisor | `[ ]` |
| T03.3 | Create the API endpoint | In `src/api/routes/releases.py` (or wherever deployment routes live), add: `@router.post("/{deployment_id}/ops-monitor")`. This handler: (1) looks up the `request_id` for the deployment from the DB, (2) starts a background task `asyncio.create_task(orchestrator._trigger_ops_monitor(request_id))`, (3) returns `{"status": "monitoring_started"}` with status code 202 | `[ ]` |
| T03.4 | Create the orchestrator method | In `src/core/orchestrator.py`, add `async def _trigger_ops_monitor(self, request_id: str) -> None`. Inside: build context `{backend_url: "http://localhost:8000"}`, call `await self.executor.execute_agent("ops_heal_agent", request_id, context)`, check the output — if it says `Status: CRITICAL`, emit a `deployment.rollback_requested` event. Wrap all in try/except | `[ ]` |
| T03.5 | Register the route | Open `src/main.py`. Check if the deployments router is already registered. If so, the new endpoint is automatically available. If not, add `app.include_router(deployments_router, prefix="/api/v1/deployments")` | `[ ]` |
| T03.6 | ✅ Test endpoint | Run `docker compose restart backend`. Then: `curl -X POST http://localhost:8000/api/v1/deployments/test-deploy-id/ops-monitor`. You should get a 202 response (not 404 or 422) | `[ ]` |

---

### AE4-T04 — Add `ops.*` event types to EventEmitter

**File to edit:** `src/core/events.py`
**Effort:** S

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T04.1 | Add constants | Add: `OPS_HEALTH_CHECK = "ops.health_check"`, `OPS_SELF_HEAL = "ops.self_heal"`, `OPS_ANOMALY = "ops.anomaly_detected"`, `OPS_ROLLBACK = "ops.rollback_requested"` | `[ ]` |
| T04.2 | Forward via WebSocket | Ensure the WebSocket handler includes `ops.*` in the events it forwards to connected clients | `[ ]` |
| T04.3 | ✅ Event test | Use browser DevTools WebSocket inspector. Trigger a deploy. After `completed`, within 30 seconds you should see an `ops.health_check` event in the WebSocket stream | `[ ]` |

---

### AE4-T05 — Add System Health pill to the Command Center frontend

**What:** A small live indicator in the Command Center header showing whether the platform is healthy right now.
**Files to create/edit:** `frontend/src/components/SystemHealthPill.tsx`, `frontend/src/pages/CommandCenter.tsx`
**Effort:** M

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T05.1 | Add backend events endpoint | In the backend, add a simple `GET /api/v1/events/latest?type=ops.health_check` endpoint that returns the single most recent event of that type from the `events` table. Return `{"event_type": "ops.health_check", "data": {...}, "created_at": "..."}` or `null` if no events yet | `[ ]` |
| T05.2 | Create `SystemHealthPill.tsx` | Create `frontend/src/components/SystemHealthPill.tsx`. It should: (a) fetch `/api/v1/events/latest?type=ops.health_check` on mount; (b) re-fetch every 30 seconds using `setInterval` inside a `useEffect`; (c) render a small pill: 🟢 `Healthy` if status is "healthy", 🟡 `Degraded` if latency > 2000ms or status is "degraded", 🔴 `Critical` if "critical"/"unreachable", ⚪ `No data` if null | `[ ]` |
| T05.3 | Style the pill | Use only Tailwind classes and CSS variables (no hardcoded hex colors). The pill should be a small `<span>` with rounded corners next to the Command Center page title | `[ ]` |
| T05.4 | Add to CommandCenter | Open `frontend/src/pages/CommandCenter.tsx`. Import `SystemHealthPill`. Render `<SystemHealthPill />` next to the page heading — e.g., `<h1>Command Center</h1> <SystemHealthPill />` | `[ ]` |
| T05.5 | Register events endpoint in main.py | If you created a new route file in T05.1, add `app.include_router(...)` in `src/main.py` | `[ ]` |
| T05.6 | ✅ Visual test | Run `docker compose restart backend frontend`. Open the Command Center. You should see the health pill (showing ⚪ No data initially). After a deploy completes and the ops agent runs, it should switch to 🟢 Healthy | `[ ]` |

---

### AE4-T06 — Write pytest tests for ops_check and the trigger

**File to create:** `tests/test_ops_heal_agent.py`
**Effort:** M

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T06.1 | Create file | Create `tests/test_ops_heal_agent.py` | `[ ]` |
| T06.2 | Test: health check shape | Write `test_check_health_returns_correct_shape()`. Mock `urllib.request.urlopen` to return a 200 response. Call `OpsCheckTool().check_health()`. Assert the result has keys `status`, `latency_ms`, `response_code` | `[ ]` |
| T06.3 | Test: unhealthy detection | Write `test_container_status_detects_unhealthy()`. Mock `subprocess.run` to return a `docker ps` line containing `(unhealthy)`. Call `get_container_status()`. Assert one item has `is_healthy == False` | `[ ]` |
| T06.4 | Test: orchestrator calls agent | Write `test_ops_monitor_trigger_calls_agent()`. Mock `executor.execute_agent`. Call `orchestrator._trigger_ops_monitor("REQ-TEST")`. Assert `execute_agent` was called with `"ops_heal_agent"` as the first argument | `[ ]` |
| T06.5 | Test: CRITICAL triggers rollback | Write `test_critical_status_emits_rollback_event()`. Mock the ops_heal_agent to return output containing `Status: CRITICAL`. Call `_trigger_ops_monitor`. Assert that a `deployment.rollback_requested` event was emitted | `[ ]` |
| T06.6 | ✅ Run all | Run `docker compose exec backend pytest tests/test_ops_heal_agent.py -xvs` | `[ ]` |

---

### AE4-T07 — Integration smoke test: full post-deploy monitoring cycle

**What:** End-to-end verification that deploy → ops_heal_agent → health pill all work together.
**Effort:** M

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T07.1 | Start supervisor | Run `make supervisor` in a terminal. Ensure it is picking up new `code_committed` rows | `[ ]` |
| T07.2 | Submit a real request | Submit a simple feature request via the UI. Let it run through the full pipeline (this will take several minutes) | `[ ]` |
| T07.3 | Watch the deploy | When the deploy completes, check `supervisor.log`. You should see the supervisor call `POST /api/v1/deployments/{id}/ops-monitor` | `[ ]` |
| T07.4 | Watch the ops agent | Check `docker compose logs backend`. You should see ops_heal_agent executing and calling `ops_check` | `[ ]` |
| T07.5 | Check the health pill | Open the Command Center in the browser. The System Health pill should show 🟢 Healthy | `[ ]` |
| T07.6 | Check events table | In the SQLite DB: `SELECT event_type, created_at FROM events WHERE event_type LIKE 'ops.%' ORDER BY created_at DESC LIMIT 5;`. Should show `ops.health_check` rows | `[ ]` |

---

## Phase AE-5 — Architecture Reviewer (`architecture_reviewer`)

> **What this phase does:** Adds a read-only agent that checks generated code against the
> project's architectural rules — are new endpoints registered in main.py? Are new pages
> wired into App.tsx? Is anyone accidentally bypassing StateStore to touch the database
> directly? Runs in parallel with the code_reviewer so it adds zero wall-clock time.
>
> **This is the lowest-effort phase — it is almost entirely YAML changes.**

---

### AE5-T01 — Create `config/agents/architecture_reviewer.yaml`

**File to create:** `config/agents/architecture_reviewer.yaml`
**Effort:** M

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T01.1 | Copy template | Copy `_template.yaml` to `architecture_reviewer.yaml` | `[ ]` |
| T01.2 | Set identity | Set: `agent_id: architecture_reviewer`, `display_name: "Architecture Reviewer"`, `role: "Architecture Reviewer"`, `team: development`, `reports_to: code_reviewer`, `model: claude-opus-4-7`, `max_iterations: 15` | `[ ]` |
| T01.3 | Write system_prompt — structure + Layer check | **Establish the canonical headers first** (no `WEB TOOLS:` — this agent has no web tools): `PROJECT CONTEXT:` → `YOUR OUTPUT FORMAT — follow this exactly:` → `LESSONS FROM PRIOR FAILURES — APPLY THESE AUTOMATICALLY:` → `RULES:` (see `config/agents/backend_specialist.yaml`). **First content rule (Layer check):** "If any file under `src/api/routes/` contains `import aiosqlite`, `import sqlite3`, or any direct database driver import, flag as CRITICAL: 'Direct DB import in route — use StateStore instead'" | `[ ]` |
| T01.4 | Write system_prompt — Endpoint registration | Second rule: "For each new `@router.get/post/put/delete` handler you see in `src/api/routes/`, use `file_read` to open `src/main.py` and search for a matching `include_router` call. Missing registration = CRITICAL." | `[ ]` |
| T01.5 | Write system_prompt — Frontend router | Third rule: "For each new `.tsx` file under `frontend/src/pages/`, use `file_read` to open `frontend/src/App.tsx` and search for a matching `<Route path=`. Missing route entry = CRITICAL." | `[ ]` |
| T01.6 | Write system_prompt — Pydantic v2 | Fourth rule: "Search all generated Python files for `@validator` (use `@field_validator`), `orm_mode = True` (use `model_config = ConfigDict(from_attributes=True)`), `.dict()` calls (use `.model_dump()`). Each instance = HIGH finding." | `[ ]` |
| T01.7 | Write system_prompt — Output format | Final instruction: "Output a findings table and one of: `Verdict: APPROVED` (no CRITICAL findings) or `Verdict: ARCH_VIOLATION` (CRITICAL found — with specific fix instructions, e.g., exact line to add to main.py)" | `[ ]` |
| T01.8 | Set tools | Add `tools: [file_read, code_analysis]`. No write tools — this agent is read-only | `[ ]` |
| T01.9 | Add responsibilities | In the `responsibilities:` block, write IDs **`AR-001` through `AR-006`** (no `-R-` infix). Copy descriptions from PRD §4.13. Category: `review` for all entries | `[ ]` |
| T01.10 | Add `delegation:` block | Add: `delegation:` / `  can_delegate_to: []` / `  max_concurrent_tasks: 3`. This agent is read-only but the delegation block is still required by the config schema | `[ ]` |
| T01.11 | Add `quality_gates:` block | Add `quality_gates: []`. Arch review verdict is evaluated by the workflow runner parsing the agent's output text; no agent-owned YAML gates needed | `[ ]` |
| T01.12 | Add `metadata:` block | Add: `metadata:` / `  created: "2026-05-24"` / `  version: "1.0"` | `[ ]` |
| T01.13 | ✅ Config validation | Run config validator | `[ ]` |

---

### AE5-T02 — Update workflows to add `architecture_reviewer` in parallel review

**File to edit:** `config/workflows.yaml`
**Note:** AE3-T02 already converted `review` to a parallel stage. This task adds a third parallel branch to it.
**Effort:** S

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T02.1 | Find the parallel review block | Open `config/workflows.yaml`. Find the `review:` stage's `parallel:` block that already has `code_review` and `quality_check` sub-stages | `[ ]` |
| T02.2 | Add arch_review sub-stage | Add a third entry: `arch_review:\n  agents: [architecture_reviewer]\n  inputs: [backend_code, frontend_code]\n  outputs: [arch_review_report]` | `[ ]` |
| T02.3 | Add quality gate | Add `- gate: arch_review_approval\n  required: true` to the `review` stage's `quality_gates` list | `[ ]` |
| T02.4 | Add gate evaluator | In `src/workflows/runner.py`, add a case for `arch_review_approval`: parse the arch_reviewer output for `Verdict: APPROVED` (pass) or `Verdict: ARCH_VIOLATION` (fail) | `[ ]` |
| T02.5 | Add to thresholds.yaml | Add `arch_review_approval: true` | `[ ]` |
| T02.6 | ✅ Config + unit tests | Run config validator. Run `docker compose exec backend pytest tests/test_workflow_engine.py -xvs`. All passing = OK | `[ ]` |

---

### AE5-T03 — Register team membership and update `code_reviewer` delegation

**Files to edit:** `config/teams.yaml`, `config/agents/code_reviewer.yaml`
**Effort:** S

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T03.1 | Add to teams.yaml | Add `- architecture_reviewer` to the `development` team members | `[ ]` |
| T03.2 | Update code_reviewer delegation | Open `config/agents/code_reviewer.yaml`. Find `can_delegate_to:`. Add `- architecture_reviewer` (code_reviewer is the parent so it can delegate to it) | `[ ]` |
| T03.3 | Add to lessons consumers | Open `src/agents/base.py`. Add `"architecture_reviewer"` to `_LESSONS_CONSUMER_AGENTS` | `[ ]` |
| T03.4 | ✅ Config validation | Run config validator | `[ ]` |

---

### AE5-T04 — Write pytest tests for architecture_reviewer

**File to create:** `tests/test_architecture_reviewer.py`
**Effort:** M

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T04.1 | Create file | Create `tests/test_architecture_reviewer.py` | `[ ]` |
| T04.2 | Test: catches direct DB import | Write `test_arch_reviewer_flags_direct_aiosqlite_import()`. Create a mock backend_code output containing `from src.api.routes.users import router\nimport aiosqlite`. Feed it to the gate evaluator (or a mock agent call). Assert the verdict is `ARCH_VIOLATION` | `[ ]` |
| T04.3 | Test: catches missing router registration | Write `test_arch_reviewer_flags_missing_include_router()`. Provide a mock `src/api/routes/new_widget.py` with a route handler, and a mock `src/main.py` WITHOUT `include_router(new_widget_router)`. Assert ARCH_VIOLATION | `[ ]` |
| T04.4 | Test: catches missing page route | Write `test_arch_reviewer_flags_missing_react_route()`. Provide a mock `frontend/src/pages/NewDashboard.tsx` and a mock `App.tsx` with no `<Route path="/new-dashboard"`. Assert ARCH_VIOLATION | `[ ]` |
| T04.5 | Test: approves clean code | Write `test_arch_reviewer_approves_compliant_code()`. Provide code with proper StateStore usage, registered endpoint, and a wired frontend route. Assert `Verdict: APPROVED` | `[ ]` |
| T04.6 | ✅ Run all | Run `docker compose exec backend pytest tests/test_architecture_reviewer.py -xvs` | `[ ]` |

---

### AE5-T05 — Pre-seed the lessons doc with common architecture violations

**What:** Add two new lessons to `docs/agent-lessons-learned.md` immediately so that even before AE-5 is deployed, the existing code-writing agents know to follow these rules.
**File to edit:** `docs/agent-lessons-learned.md`
**Effort:** S

| # | Sub-task | Description | Status |
|---|----------|-------------|--------|
| T05.1 | Read current lesson count | Open `docs/agent-lessons-learned.md` and note the highest lesson number (e.g., L19) | `[ ]` |
| T05.2 | Add Layer Boundary lesson | Append `## L<N+1> — Direct database import in a route file` with: **Signature:** `import aiosqlite` or `import sqlite3` inside any file under `src/api/routes/`; **Cause:** Agent generated route logic that bypasses the StateStore layer and accesses the DB directly; **Fix:** Replace with a call to the appropriate `StateStore` method (e.g., `await state_store.get_request(id)`) — never import a DB driver in a route file; **Observed in:** Phase AE-5 architectural analysis | `[ ]` |
| T05.3 | Add Registration lesson | Append `## L<N+2> — New FastAPI route handler not registered in main.py` with: **Signature:** New `@router.get/post` handler exists in `src/api/routes/` but returns 404 at runtime; **Cause:** Agent created the route file but did not add `app.include_router(new_router)` to `src/main.py`; **Fix:** Always check `src/main.py` after adding a new route file and add the matching `include_router` call; **Observed in:** Phase AE-5 architectural analysis | `[ ]` |
| T05.4 | ✅ Verify format | Open the doc and visually confirm the two new lessons follow the same format as L01 and L02. Run `docker compose exec backend pytest tests/ -k "lessons" -xvs` if any lesson-related tests exist | `[ ]` |

---

## Post-Phase Verification Checklist

Run this checklist after all 5 phases are complete to confirm the full Phase AE pipeline works end-to-end.

| # | Check | Expected Result | Status |
|---|-------|----------------|--------|
| V01 | Run `python -m src.config.validator` | Zero errors | `[ ]` |
| V02 | Check `feature_development` workflow stage order | `requirements → story_creation → development → review (parallel: code_review ‖ quality_check ‖ arch_review) → testing → security → code_commit → deployment` (security runs **after** testing, not before — per AE1-T06 which changes `testing.next` from `code_commit` to `security`) | `[ ]` |
| V03 | Submit a test feature request | New `security` stage appears in Request Detail timeline | `[ ]` |
| V04 | Submit a request that fails with a new error pattern | `docs/agent-lessons-learned.md` gets a new lesson automatically within a few minutes | `[ ]` |
| V05 | Complete a full deploy with supervisor running | After `completed`, ops_heal_agent runs; System Health pill shows 🟢 | `[ ]` |
| V06 | Check `_LESSONS_CONSUMER_AGENTS` membership | Contains: `security_specialist`, `quality_guardian`, `ops_heal_agent`, `architecture_reviewer`. Does NOT contain: `self_learning_agent` | `[ ]` |
| V07 | Run full test suite | `docker compose exec backend pytest tests/ --no-cov` — all new test files pass | `[ ]` |
| V08 | Run full test suite with coverage | `docker compose exec backend pytest tests/` — coverage ≥ 80% | `[ ]` |

---

## Appendix — Files Created and Modified Summary

| File | Action | Phase |
|------|--------|-------|
| `src/tools/security_scan.py` | **Created** | AE-1 |
| `src/tools/lessons_writer.py` | **Created** | AE-2 |
| `src/tools/ops_check.py` | **Created** | AE-4 |
| `config/agents/security_specialist.yaml` | **Created** | AE-1 |
| `config/agents/self_learning_agent.yaml` | **Created** | AE-2 |
| `config/agents/quality_guardian.yaml` | **Created** | AE-3 |
| `config/agents/ops_heal_agent.yaml` | **Created** | AE-4 |
| `config/agents/architecture_reviewer.yaml` | **Created** | AE-5 |
| `frontend/src/components/SystemHealthPill.tsx` | **Created** | AE-4 |
| `tests/test_security_agent.py` | **Created** | AE-1 |
| `tests/test_self_learning_agent.py` | **Created** | AE-2 |
| `tests/test_quality_guardian.py` | **Created** | AE-3 |
| `tests/test_ops_heal_agent.py` | **Created** | AE-4 |
| `tests/test_architecture_reviewer.py` | **Created** | AE-5 |
| `config/tools.yaml` | **Edited** — 3 new tool entries | AE-1, AE-2, AE-4 |
| `config/teams.yaml` | **Edited** — 5 new agent memberships | AE-1 through AE-5 |
| `config/workflows.yaml` | **Edited** — new security stage, parallel review block | AE-1, AE-3, AE-5 |
| `config/thresholds.yaml` | **Edited** — 4 new gate thresholds | AE-1, AE-3, AE-5 |
| `config/agents/code_reviewer.yaml` | **Edited** — added arch_reviewer to can_delegate_to | AE-5 |
| `src/agents/base.py` | **Edited** — 4 new entries in _LESSONS_CONSUMER_AGENTS | AE-1, AE-3, AE-4, AE-5 |
| `src/tools/executor.py` | **Edited** — 3 new tool registrations | AE-1, AE-2, AE-4 |
| `src/core/orchestrator.py` | **Edited** — 2 new trigger methods + document saves | AE-1, AE-2, AE-3, AE-4 |
| `src/core/events.py` | **Edited** — ops.* and lessons.* event types | AE-2, AE-4 |
| `src/workflows/runner.py` | **Edited** — 4 new gate evaluators | AE-1, AE-3, AE-5 |
| `src/api/routes/*.py` | **Edited** — new /ops-monitor endpoint | AE-4 |
| `src/main.py` | **Edited** — new router registration | AE-4 |
| `supervisor/deploy_supervisor.py` | **Edited** — post-deploy ops trigger + quality risk injection | AE-3, AE-4 |
| `frontend/src/pages/CommandCenter.tsx` | **Edited** — SystemHealthPill added | AE-4 |
| `docs/agent-lessons-learned.md` | **Edited** — 2 new architecture lessons pre-seeded | AE-5 |
