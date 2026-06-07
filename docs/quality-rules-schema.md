# Quality Rules Schema — `config/quality-rules.yaml`

| Document | Status |
|---|---|
| Owner | Phase AE (Agentic Engineering Enhancements) |
| Tracks | AET-01 (schema design), AET-02 (initial ruleset), AET-03 (`policy_check` tool) |
| Consumed by | `quality_guardian` agent via the `policy_check` tool |
| Authoritative location | `config/quality-rules.yaml` (loaded at agent init) |
| Version | 1 |
| Created | 2026-05-26 |

## 1. Purpose

Until Phase AE-3 ships, the rules in `docs/agent-lessons-learned.md`
(L11–L21) live as **soft suggestions in the system prompt** — the
agent reads them at request time and is expected to remember. When the
agent forgets (which it does, that's why the lessons exist), nothing
catches the miss until the next downstream stage fails or a human
notices in review.

This file documents the schema for a declarative rule catalog that
flips those lessons from **soft suggestion** to **enforced runtime
check**. The `quality_guardian` agent calls `policy_check` against every
non-trivial agent output; rules with `severity: enforce` BLOCK the
workflow at the `quality_guardian_approval` gate; rules with `severity:
warn` annotate the output but let the workflow advance.

## 2. Design goals

| Goal | How the schema achieves it |
|---|---|
| **Declarative** | Rules live in YAML, version-pinned, not in code or prompts. New rules ship via a config change + agent restart — no Python edit, no model retraining. |
| **Type-safe at load time** | Pydantic model validates the file at startup; a malformed rule fails the boot, not the first request that triggers it. |
| **Multiple matcher kinds** | Discriminated union covers content regex, file metadata, emission metadata, and (future) python-eval expressions. |
| **Scoped precisely** | `applies_to` accepts file globs, agent IDs, and tool names so a rule that should ONLY fire on `backend_specialist`'s `file_write` calls says exactly that. |
| **Auditable** | Every rule carries `rationale` + optional `lesson_ref` → traceable back to the L11-L21 incident that motivated it. |
| **Extensible** | New matcher types can be added without breaking existing rules — `matcher.type` is a discriminator. |
| **Versioned** | Top-level `version:` field lets `policy_check` reject configs from a newer schema it doesn't understand. |

## 3. File structure

```yaml
# config/quality-rules.yaml
version: 1                              # required, integer; policy_check rejects unknown values

defaults:                               # optional; per-severity fallback behavior
  enforce_blocks: true                  # severity=enforce halts the workflow at quality gate
  warn_annotates: true                  # severity=warn appends to quality_report but doesn't block
  info_logs_only: true                  # severity=info logs to structlog, no UI surface

rules:
  - id: QR-001                          # required, unique, format QR-NNN
    name: snake_case_identifier         # required, unique
    severity: enforce | warn | info     # required
    enabled: true                       # optional, default true; lets you disable without deleting
    matcher: { ... }                    # required, see §4
    applies_to: { ... }                 # required, see §5
    rationale: |                        # required, multi-line description
      Human-readable explanation of why this rule exists. Audit trail.
    lesson_ref: L15                     # optional, L01-L99 back-link to agent-lessons-learned.md
    fix_hint: |                         # optional, multi-line suggestion surfaced in rework input
      When this rule fires, the rework prompt includes this text so the
      next iteration of the agent has actionable guidance.
    introduced_in: "2026-05-26"         # optional ISO date; useful for audit
```

## 4. Matchers

Discriminated union on `matcher.type`. Each type has its own required fields.

### 4.1 `type: regex` — content pattern match

Matches a regex against the textual content of a file the agent
emitted. The default backend is Python's `re` module.

```yaml
matcher:
  type: regex
  pattern: '\bprint\s*\('              # required; raw regex string
  flags: ['ignorecase', 'multiline']    # optional; subset of [ignorecase, multiline, dotall, verbose]
  max_matches: 1                        # optional; rule fires if matches >= this count, default 1
```

| Field | Type | Notes |
|---|---|---|
| `pattern` | string | Standard Python regex. Test with `pytest -k test_quality_rule_QR_001`. |
| `flags` | list | Maps to `re.IGNORECASE`, `re.MULTILINE`, `re.DOTALL`, `re.VERBOSE`. |
| `max_matches` | int | Rule does NOT fire until match count ≥ this. Default 1 (first match triggers). |

**Use for:** L11 (no print()), L17 (byte-identical re-emission detection
on hash), L18 (test_case ID format).

### 4.2 `type: file_metadata` — file shape predicate

Evaluates a property of the emitted file (line count, byte size,
import count) against an operator + threshold. No regex, just numeric
comparison.

```yaml
matcher:
  type: file_metadata
  property: line_count                  # required; one of: line_count | byte_size | import_count | function_count
  operator: '>'                         # required; one of: > | >= | < | <= | == | !=
  threshold: 470                        # required; int
```

**Use for:** L15 (files >470 lines must use search_replace), L19
(atomic-task primary_file LOC bounds 50-300).

### 4.3 `type: emission_metadata` — agent action predicate

Inspects the agent's tool-call shape (which tool, which agent, target
path) rather than the file content. Fires before the file is even
written.

```yaml
matcher:
  type: emission_metadata
  field: tool_name                      # required; tool_name | agent_id | target_path | rework_cycle
  operator: equals | matches | in       # required
  value: file_write                     # required; type depends on operator
```

**Use for:** L13 (block re-dispatch after N rework cycles on same task),
guarding L15 ("if tool_name == file_write AND target file_size > 470,
escalate to search_replace").

### 4.4 `type: composite` — boolean combination

AND/OR of two or more matchers. Lets a rule express "regex match X
AND file_metadata Y" without authoring two rules.

```yaml
matcher:
  type: composite
  op: AND                               # required; AND | OR
  children:
    - type: file_metadata
      property: line_count
      operator: '>'
      threshold: 470
    - type: emission_metadata
      field: tool_name
      operator: equals
      value: file_write
```

Recursion depth capped at 3 to keep evaluations predictable.

### 4.5 `type: python_eval` (deferred to v2)

Reserved for future arbitrary Python expression evaluation. **Not in v1**
because it opens a sandboxing surface — every rule author would
become a security boundary. Re-evaluate once v1 ships and a real rule
needs it.

## 5. Scoping — `applies_to`

A rule only fires when ALL of the present scoping criteria match.
Missing fields = unscoped (matches anything).

```yaml
applies_to:
  files:                                # optional; glob list; matches against target_path
    - 'src/**/*.py'
    - 'frontend/src/**/*.tsx'
  exclude_files:                        # optional; takes precedence over files
    - 'tests/**'
    - '**/__pycache__/**'
  agents:                               # optional; agent_id list
    - backend_specialist
    - frontend_specialist
  tools:                                # optional; tool_name list from config/tools.yaml
    - file_write
    - search_replace
  rework_cycle:                         # optional; only fire on / after specific cycles
    min: 2                              # e.g., warn on cycle 2+ when L13 says drop guard kicks in
```

Evaluation: a rule's `applies_to` is satisfied when EVERY present
field passes. So a rule that lists `files` AND `agents` requires both
the file glob AND the agent id to match.

## 6. Severity behavior

| Severity | Workflow effect | UI surface | Logged |
|---|---|---|---|
| `enforce` | BLOCKS at `quality_guardian_approval` gate; routes back to `development` with the rule's `fix_hint` in the rework input | Red chip on Request detail header: `🛑 BLOCKED: QUALITY GATE · N violations` | Yes |
| `warn` | Workflow advances; violations appended to `quality_report` output | Yellow chip on Request detail: `⚠ QUALITY WARNINGS · N` | Yes |
| `info` | Workflow advances; no output mutation | None (background telemetry only) | Yes |

`enforce` violations are the only ones that block. They're also the
only ones that route back to the producing stage with rework context.

## 7. Loading + validation

`policy_check` loads `config/quality-rules.yaml` once at tool init
(equivalent of agent-startup time). Failures are loud:

| Failure | Result |
|---|---|
| File missing | `policy_check.config_missing` ERROR; tool refuses to instantiate; `quality_guardian` agent fails to start. Boot-time check, not request-time. |
| Schema version unknown | Same — `policy_check.unknown_version`. |
| Pydantic validation fails on any rule | Same — `policy_check.rule_invalid` with the offending rule's `id`. |
| Duplicate rule `id` or `name` | Same — `policy_check.duplicate_rule_id`. |
| Regex compile fails | Same — `policy_check.bad_regex` with the rule's `id`. |

All five are caught at boot, not at request time. A bad rule never
silently turns into "agent always approves" — it fails the agent boot
so the operator notices immediately.

## 8. Reload behavior

Rule changes do NOT live-reload. After editing `config/quality-rules.yaml`:

```bash
docker compose restart backend
```

This matches the existing `config/agents/*.yaml` reload model
(restart-to-pick-up) per CLAUDE.md's "restart containers after code
changes" convention. A future enhancement could watch the file with
watchdog, but v1 keeps it explicit.

## 9. Versioning + migration

`version: 1` in the file header. `policy_check` accepts:

| Config version | Tool version | Result |
|---|---|---|
| 1 | 1 | OK |
| 1 | 2 | OK with deprecation warning if v1 fields removed |
| 2 | 1 | REJECT at boot — `policy_check.config_too_new` |

When the schema grows (e.g., AET-V2 adds `python_eval`), the version
bumps to 2 and migration is documented in this file under a new
section. v1 configs continue to work; v2 fields default to absent.

## 10. Worked examples (3 rules derived from L11–L21)

### 10.1 No print() in src/ — derived from L11

```yaml
- id: QR-001
  name: no_print_in_production
  severity: enforce
  matcher:
    type: regex
    pattern: '\bprint\s*\('
  applies_to:
    files: ['src/**/*.py']
    exclude_files: ['tests/**', 'scripts/**']
  rationale: |
    Production code must use structlog (configured globally) rather
    than print() so logs are captured in the supervisor pipeline and
    cost/token observability. print() bypasses the logger and is
    invisible in production debugging.
  lesson_ref: L11
  fix_hint: |
    Replace `print(...)` with `logger.info(...)`. The structlog
    logger is set up at module level in every src/ file; if you need
    one, add `logger = structlog.get_logger()` at the top of the file.
```

### 10.2 Large files must use search_replace — derived from L15

```yaml
- id: QR-015
  name: large_file_requires_search_replace
  severity: enforce
  matcher:
    type: composite
    op: AND
    children:
      - type: file_metadata
        property: line_count
        operator: '>'
        threshold: 470
      - type: emission_metadata
        field: tool_name
        operator: equals
        value: file_write
  applies_to:
    agents: [backend_specialist, frontend_specialist]
  rationale: |
    file_write of long content was the root cause of REQ-7F2E07's
    3-cycle response-length truncation. Files >470 lines must be
    modified via search_replace (which only sends the surgical diff)
    to stay under the model's response budget.
  lesson_ref: L15
  fix_hint: |
    Switch from file_write to search_replace. Provide the unique
    anchor text from the existing file (≥3 lines surrounding context)
    + the replacement block. Read the file first if you need to
    confirm the anchor.
```

### 10.3 Test-case IDs must be deterministic — derived from L18

```yaml
- id: QR-018
  name: test_case_ids_deterministic
  severity: warn
  matcher:
    type: regex
    pattern: 'TC-[0-9a-f]{8}'             # uuid-style IDs (BAD); deterministic is TC-001..TC-NNN
  applies_to:
    files: ['tests/**/test_*.py']
    agents: [tester_specialist]
  rationale: |
    Test cases need deterministic IDs across rework cycles so the
    UPSERT path in src/state/sqlite_store.py::create_test_case works
    correctly. UUID-based IDs would create new rows on every cycle
    instead of updating existing ones, breaking coverage stats.
  lesson_ref: L18
  fix_hint: |
    Use TC-001, TC-002, ..., TC-NNN sequential IDs scoped per request.
    See L18 in docs/agent-lessons-learned.md for the schema fix that
    made deterministic IDs safe (UPSERT on test_id).
```

## 11. Pydantic model sketch (informative, not normative)

The actual Pydantic model lives in `src/tools/policy_check.py` per
AET-03. This is the shape it'll take:

```python
class MatcherRegex(BaseModel):
    type: Literal["regex"]
    pattern: str
    flags: list[Literal["ignorecase", "multiline", "dotall", "verbose"]] = []
    max_matches: int = 1

class MatcherFileMeta(BaseModel):
    type: Literal["file_metadata"]
    property: Literal["line_count", "byte_size", "import_count", "function_count"]
    operator: Literal[">", ">=", "<", "<=", "==", "!="]
    threshold: int

class MatcherEmissionMeta(BaseModel):
    type: Literal["emission_metadata"]
    field: Literal["tool_name", "agent_id", "target_path", "rework_cycle"]
    operator: Literal["equals", "matches", "in"]
    value: str | int | list[str | int]

class MatcherComposite(BaseModel):
    type: Literal["composite"]
    op: Literal["AND", "OR"]
    children: list["Matcher"]  # max recursion depth 3, validated post-load

Matcher = Annotated[
    MatcherRegex | MatcherFileMeta | MatcherEmissionMeta | MatcherComposite,
    Field(discriminator="type"),
]

class AppliesTo(BaseModel):
    files: list[str] = []
    exclude_files: list[str] = []
    agents: list[str] = []
    tools: list[str] = []
    rework_cycle: dict[str, int] | None = None  # {min: int}

class QualityRule(BaseModel):
    id: str = Field(pattern=r"^QR-\d{3}$")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    severity: Literal["enforce", "warn", "info"]
    enabled: bool = True
    matcher: Matcher
    applies_to: AppliesTo
    rationale: str
    lesson_ref: str | None = Field(default=None, pattern=r"^L\d{2}$")
    fix_hint: str | None = None
    introduced_in: str | None = None

class QualityRulesConfig(BaseModel):
    version: Literal[1]
    defaults: dict | None = None
    rules: list[QualityRule]
```

## 12. What ships in subsequent AET tasks

| Task | Deliverable | Builds on this doc |
|---|---|---|
| **AET-02** | `config/quality-rules.yaml` populated with 10-15 rules derived from L11-L21 | Uses §3 file structure + §10 worked examples as templates |
| **AET-03** | `src/tools/policy_check.py` — the Pydantic model + evaluator | Implements §11 sketch; enforces §7 boot-time validation |
| **AET-04** | Wire `policy_check` to `quality_guardian` in `config/tools.yaml` | Tool grant only — no schema work |
| **AET-05** | `quality.gate.failed` / `quality.gate.passed` events | Reads `severity` field per §6 |
| **AET-06** | Workflow runner enforces the gate | Reads `enforce_blocks` from §3 `defaults` |
| **AET-07** | UI surface for blocked-by-quality-gate state | Renders the chip + violation list per §6 |
| **AET-08** | End-to-end smoke test | Asserts a known violation from §10 fires correctly |

## 13. Open questions to revisit after AET-02

These came up during schema design — flagged so AET-02 doesn't
re-derive them, but no answer is required to start AET-01:

1. **Should `python_eval` ship in v2 or stay deferred?** Depends on whether AET-02 hits a rule it can't express with regex/metadata/composite. Predicted yes for "did the agent emit a duplicate test_id within the same request" — needs cross-row reasoning. Could be handled by a new `cross_emission` matcher type instead.
2. **Per-project rule overrides?** Currently rules apply globally. A web-app project might want a stricter "no console.log" rule than a research project. v1 says no; v2 could add `applies_to.project_id` or per-project `config/quality-rules.<project>.yaml`.
3. **Rule deprecation lifecycle?** When a lesson stops being relevant (e.g., the underlying bug was fixed structurally), the rule should age out. Proposal: add `deprecated_at: <date>` field; `policy_check` logs at WARN if it loads a deprecated rule and skips evaluation.

---

*Schema version 1 · last updated 2026-05-26 · authoritative location: this file*
