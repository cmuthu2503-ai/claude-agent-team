# Agent Lessons Learned

This document is **injected at runtime** into every code-writing agent's
system prompt (see `src/agents/base.py::_build_system_prompt`). It is
the cross-agent canonical memory of failure modes observed in production
and how to avoid them. Add a new section when a new pattern is
identified; the next agent invocation reads it without a restart.

**Audience:** `backend_specialist`, `frontend_specialist`, `code_reviewer`,
`tester_specialist`, `devops_specialist`, and any future code-writing
agent. Non-code agents (PRD, user stories, research, content) don't
load this — their lessons live in their YAML directly.

**Format:** one section per pattern. Each section has:
- The signature (what the failure looked like in logs / errors)
- The cause (what the agent was doing wrong)
- The fix (what to do instead)
- Where it was first observed (request ID for cross-reference)

---

## L01 — Drop-guard rejection on file shrink

**Signature:** `Refusing to overwrite 'X': line count dropped from N to M (P% reduction).`

**Cause:** Emitting a `### Full Source:` block with content much shorter
than the existing file on disk. Common when refactoring a config file
into the modern split pattern (e.g. monolithic `tsconfig.json` →
references + sibling app/node configs), or when delegating CSS theme
tokens from `index.css` to a sibling `themes.css`.

**Fix — pick ONE of three (the rejection message lists them too):**

1. **MERGE** — re-emit the FULL file with your changes applied on top
   of the existing content. The rework prompt now includes a
   `=== CURRENT FILE CONTENT AT EACH CITED LOCATION ===` block showing
   exactly what's on disk; copy the lines you want to keep.

2. **SURGICAL** — switch from `### Full Source:` to `search_replace`
   for the specific edit. `search_replace` is diff-based and bypasses
   the drop guard.

3. **SPLIT** — if you're genuinely refactoring the file into multiple
   sibling files (e.g. `tsconfig.json` → `tsconfig.app.json` +
   `tsconfig.node.json`, monolithic `index.css` → `index.css` +
   `themes.css`), emit ALL the new sibling files **in the same
   response**. The system sees the original lines have moved, not
   vanished.

**Do NOT re-emit the same short version on the next cycle** —
the guard will fire again. This wasted REQ-A9283B's cycle 2 and
REQ-F86080's cycle 2.

**Exempt list (the guard auto-passes these even on >50% shrink):**
`tsconfig.json`, `vite.config.{ts,js}`, `tailwind.config.{ts,js}`,
`postcss.config.{js,cjs}`, `eslint.config.js`, `.prettierrc`,
`index.css`, `main.css`, `.gitignore`, `.dockerignore`,
`.env.example`, `pyproject.toml`, `ruff.toml`.

**Observed in:** REQ-F86080 (tsconfig.json 21→7), REQ-F86080 retry
(index.css 72→3), REQ-5F25E9 cycle 1 (App.tsx 49→23 — recovered on
cycle 2 with the playbook above).

---

## L02 — Line length 100 is handled by the formatter

**Signature:** `Python compilation failed (ruff): E501 Line too long
(92 > 88)`.

**Cause:** Counting columns manually when emitting `.py` code. Used to
fail at 88 chars (ruff default); per-project scaffolds now ship a
`pyproject.toml` with `line-length = 100`.

**Fix — don't count columns at all.** The `file_write` and
`search_replace` tools pipe Python content through `ruff format -`
before persisting, automatically wrapping long lines to the project's
`line-length`. Just write what reads cleanly; the formatter handles
the rest.

**Exceptions where you still need to wrap by hand:** string literals
that contain a single unbreakable token > 100 chars, URLs, and comments
that ruff won't reformat. Break those across lines yourself
(implicit string concatenation or `\\n` joins).

**Observed in:** REQ-D53897.

---

## L03 — "Nothing to do" cycles are valid; emit one line

**Signature:** Workflow exhausts `MAX_REWORK_CYCLES` even though every
agent's output says "no changes needed this cycle" — and the request
ends in `failed` despite reviewer + tester previously verdicting
APPROVED.

**Cause:** A rework cycle where the prior reviewer/tester verdict was
APPROVED and rework_instructions say "None this cycle." The agent
re-emits the same prior output expecting to reaffirm — but each
cycle costs an LLM call and burns from the rework budget.

**Fix — emit a single line:**

> `No changes required this cycle.`

(For tester: `No new code to test this cycle — verdict unchanged:
READY FOR DEPLOYMENT.`)

Don't re-emit prior code. Don't invent edits to fill space. The system
recognises a one-line "no changes" response and proceeds without
mutating state.

**Observed in:** REQ-A9283B (3 redundant cycles before exhausting
budget).

---

## L04 — Per-project tree routing is automatic; don't compensate

**Signature:** Vite or test infrastructure complains about imports
that don't exist (e.g. `Failed to resolve import "./pages/Agents" from
"src/App.tsx"`). Confusingly the file you emitted IS there — just in
the wrong tree.

**Cause:** Previously, `file_write` / `search_replace` resolved paths
against the platform's `/app/` tree even when the task belonged to a
per-project Request. The agent's edit landed in the platform's
`frontend/src/App.tsx` rather than the project's
`C:/ai-projects/<Project>/frontend/src/App.tsx`.

**Fix:** **The system now routes this automatically** based on the
Request's `project_id`. You don't need to specify absolute paths or
prefixes. Emit `frontend/src/App.tsx` as you would normally — it lands
in the correct tree. The `agent_executing` log will show the
`project_root` the agent is bound to.

If you're EVER unsure which tree you're writing to: don't try to
guess from cwd or env vars. The system has already resolved it.
Trust the relative paths.

**Observed in:** REQ-BB30E2 (platform's App.tsx + Sidebar.tsx
clobbered before the fix landed).

---

## L05 — `### Full Source:` blocks are written to disk BEFORE review

**Architecture:** After the `development` stage completes, a new
`materialize` hook (added in `d78de9d`) parses every `### Full Source:`
block from your output and writes it to disk **immediately**. Then it
runs ruff + npm-build + pytest on the diff. Only after that does
review/testing run.

**What this means for you:**

- The reviewer's `file_read` at review stage **sees your actual
  emission**, not the scaffold. Truncation checks and import-resolution
  checks are real verifications now.
- If your emission fails materialize (drop guard, ruff E5xx, npm
  build, pytest), you get an enriched rework prompt with the cited
  file's CURRENT on-disk content embedded — read it before deciding
  how to fix.
- Don't claim "this file was written" without actually emitting it
  (in either `### Full Source:` or `search_replace`). Materialize +
  reviewer's `file_read` will catch the phantom claim.

**Cycle-2 strategy that works (proven in REQ-5F25E9):** combine
`search_replace` for existing files (small edits, bypasses drop
guard) with `### Full Source:` blocks for NEW files. Emit ALL new
sibling files in the same response.

**Observed in:** REQ-BB30E2 cycles 1-3 (phantom-emission rejection
loops). Closed by Fix A in `d78de9d`.

---

## L06 — Scope discipline; don't self-modify or touch unrelated config

**Signature:** `Commit rejected — every emitted file is under a
guarded path that the request didn't explicitly authorize` OR an
out-of-scope edit landing in an unrelated YAML/test file and
contaminating the diff.

**Cause:** The agent decided to "fix while it was in there" — edited
its own agent YAML, modified an unrelated test, added a "helpful"
comment to a file the task didn't mention.

**Fix:** When the rework instructions cite a specific error, address
ONLY that error. Apply the smallest possible change. Do NOT:

- Re-emit your full prior output
- Add new content to files outside the task's named scope
- "Fix" tests / docstrings in unrelated files
- Modify anything under `config/agents/**` unless the task explicitly
  names it (CodeWriter will reject the commit either way)

The rework loop has a budget of `MAX_REWORK_CYCLES = 2`. Out-of-scope
edits consume cycles for nothing.

**Observed in:** REQ-8C3B4F (backend_specialist edited its own YAML),
REQ-D0742A (similar), REQ-D20A12 (test_config_validation.py touched
out of scope).

---

## L07 — `## Files Modified` section is mandatory for search_replace

**Signature:** Your `search_replace` edit lands on disk but doesn't
appear in the commit — `git diff` is empty against the GitHub PR.

**Cause:** `search_replace` writes to disk directly without going
through the agent's text output. The commit step parses `### Full
Source:` blocks (which carry their own path in the heading) AND
parses the `## Files Modified` list at the END of your response to
know which on-disk files to include in the GitHub commit. If you
used `search_replace` but didn't list the file in `## Files
Modified`, the edit is on disk locally but never pushed.

**Fix:** Always include the section at the end of any response that
touched a file:

```
## Files Modified
- path/to/file_a.py
- path/to/file_b.tsx
```

INCLUDE BOTH:
- Files edited via `search_replace`
- Files emitted via `### Full Source:`

Even if your work was 100% `### Full Source:`, list the files. The
parser is tolerant of duplicates — listing it twice is harmless.

**Observed in:** REQ-8C3B4F (search_replace edited file but absent
from `Files Modified`, so commit was empty).

---

## L08 — Per-project scaffold ships ALL config files; don't re-emit blindly

**Context:** When a new project is created (via the platform's
Create Project flow), the scaffolder writes:

- `pyproject.toml` (Python: ruff, black, pytest) — line-length 100,
  matches the platform's ruff config exactly.
- `frontend/package.json` (React + Vite + Tailwind + Zustand + etc.)
- `frontend/tsconfig.json` (modern project-references shape, ~7 lines)
  + `tsconfig.app.json` + `tsconfig.node.json` (siblings)
- `frontend/vite.config.ts` with proxy → backend on the project's
  allocated backend port
- `frontend/index.html`
- `frontend/src/{main.tsx, App.tsx, index.css, themes.css}` placeholders

You **don't need to re-create** these. If your task is to "scaffold
the frontend," start from the existing scaffold; `search_replace`
into `App.tsx` to add routes; `### Full Source:` for NEW files like
`pages/Dashboard.tsx`.

The exempt list (L01) covers the config files where shrinkage IS
expected (you can replace the scaffold's starter with a project-
tailored version freely).

---

## L09 — Code reviewer + tester read files from disk; trust what you see

**For `code_reviewer` and `tester_specialist`:**

After Fix A (`d78de9d`), the on-disk content at review/testing time
IS the agent's emission, NOT the pre-emission scaffold. `file_read`
returns ground truth.

- **Truncation check:** if the emitted file's line count is very
  different from what the agent CLAIMED in their summary, the gap is
  real — flag it. (Pre-Fix-A this check produced false positives
  because file_read returned the scaffold; that's fixed now.)
- **Import-resolution check:** can the emitted file actually compile?
  TypeScript / Python imports → check if the imported sibling files
  exist on disk in the same commit.
- **Style nits:** skip formatting issues. `ruff format` runs on every
  Python write; `prettier` may run on TS. Style nits are pre-resolved.

**For "no changes to review" cycles:** see L03. One-line response is
correct; don't manufacture findings.

---

## L10 — Reading rework_instructions is mandatory; treat it as truth

When you receive `rework_instructions` in your input, the prompt now
includes an enriched section:

```
=== CURRENT FILE CONTENT AT EACH CITED LOCATION ===
(Use this to decide between merging into the existing file, using
`search_replace` for a surgical edit, or re-emitting a full file
that preserves the lines you intended to keep.)

--- CURRENT ON-DISK CONTENT: path/to/file.tsx (N lines) ---
  1  <line 1>
  2  <line 2>
  ...
```

**Read it before deciding what to emit.** It tells you exactly what's
on disk so you can pick the right strategy (MERGE / SURGICAL / SPLIT
per L01).

Don't call `file_read` on the cited file again — it's already
embedded in your prompt. Re-reading wastes a tool turn.

---

## L11 — E501 on docstring / comment alignment tables

**Signature:**
```
E501 Line too long (103 > 100)
  --> backend/tests/test_dashboard.py:17:101
   |
17 | * US-006 null ended_at / unmodified . :func:`test_activity_feed_skips_unended_runs_and_unchanged_tasks`
   |                                                                                                     ^^^
```

**Cause:** You wrote a Sphinx-style coverage docstring or a fixed-width
comment table where one or more rows exceed the project's
`line-length`. `ruff format` does NOT reflow content inside docstrings
or comments — it preserves them verbatim. So the auto-format step on
write let the line through, and the commit-gate `ruff check` flagged it.

**Fix — pick ONE:**

1. **Disable E501 on the file** (preferred for test files):
   the per-project `pyproject.toml` has `per-file-ignores =
   {"tests/**" = ["E501"]}` already; if your file isn't matched (e.g.
   it's under `src/`), add `# noqa: E501` to the end of each long
   docstring line.

2. **Rewrite the table** with two columns or a list comprehension that
   ruff CAN wrap:
   ```python
   COVERAGE = [
       ("US-006", "null ended_at / unmodified", "test_activity_feed_skips_..."),
       ...
   ]
   ```
   Move the prose out of the docstring into a regular Python data
   structure that ruff can format.

3. **Drop the alignment dots** so the line gets shorter:
   ```
   * US-006 null ended_at — :func:`test_activity_feed_skips_unended_runs_and_unchanged_tasks`
   ```

**Do NOT:**
- Just fix the one line ruff cited and re-emit. **All sibling long
  lines in the same docstring will fail next cycle.** See L12.
- Wrap a `:func:` reference across lines — Sphinx requires it on one
  line.

**Observed in:** REQ-B55FB8 (T-029e7cfb) — failed after 3 cycles, each
hitting a different long line in the same `backend/tests/test_dashboard.py`
coverage docstring.

---

## L12 — Fix ALL similar lint issues in one cycle, not just the cited one

**Signature:** Successive rework cycles each cite a different line in
the SAME file with the SAME violation (E501 line 13 → 15 → 17 …).
`MAX_REWORK_CYCLES = 2`, so by cycle 3 you're out of budget and the
Request goes to `failed`.

**Cause:** Ruff (and pytest, tsc) only cite the FIRST violation it hits
on each run. When you fix that one line and re-emit, the next run
finds the next sibling violation. The agent treats each as a separate
unrelated bug.

**Fix:** When the rework prompt cites a lint failure, treat the cite as
a CLASS — scan the whole file for the same violation before re-emitting.
The orchestrator now appends an explicit warning to E501 errors:

```
=== IMPORTANT: SCAN THE WHOLE FILE FOR SIMILAR ISSUES ===
...
MAX_REWORK_CYCLES is 2 — you do not get a 3rd attempt on the same file.
```

When this warning is present, do not return until you've grepped /
visually scanned every line in the affected file for the same
violation pattern. Fix them all in one emission.

**Observed in:** REQ-B55FB8 (T-029e7cfb) — three cycles each cited a
different line in the same coverage docstring.

---

## L13 — Don't burn iterations on read-only exploration; emit code

**Signature:** `agent_max_iterations_reached iterations=25` followed by
`materialize_failed error='No code files were produced by any agent'`.
The trace shows 25 `file_read` / `grep` calls, zero `file_write` or
`search_replace` calls. Cost: typically $20-$40 of LLM time with no
output. Then the rework cycle starts and does the same thing.

**Cause:** You spent the whole iteration budget reading existing files
trying to "understand the codebase" before writing anything. The
per-agent loop is capped at 25 iterations; if you hit that without
emitting, the system has nothing to materialize and the rework cycle
starts from scratch — burning another 25 iterations.

**Fix — budget your reads up front:**

1. **Plan first, read minimally.** Read at most 4-6 files in the first
   few iterations — the ones the task description directly names. Don't
   walk the whole tree.

2. **Emit incrementally.** Write your first file by iteration 5-8. If
   you need to read more context for later files, you can — but the
   first file should be on disk before iteration 10.

3. **Use `### File: path` blocks in a SINGLE response** for related
   files (per L08 — orchestrator merges them per-cycle). Don't spread
   one task across many response turns.

4. **If you genuinely don't have enough context, ASK for it explicitly
   in your output** rather than reading 20 more files. The supervisor
   workflow can route a follow-up read to the orchestrator. (TBD —
   for now, document your read budget in a comment at the top of your
   first emitted file: `# This file was generated after reading: X, Y, Z`.)

**Observed in:** REQ-270B83 (T-afdfc0c5) cycle 1 — 25 iterations, $27
in LLM cost, 1.17M input + 100K output tokens, zero files written.

---

## L14 — Auto-fixable "basic compilation errors" are stripped on write

**Signature:** Recurring failure class where the agent emits a file
with a SAFE, auto-fixable lint violation and gets stuck in the rework
loop because it can't translate "remove that one line" into an actual
re-emission without the line. The canonical case:
```
F401 [*] `re` imported but unused
  --> backend/app/services/crew_orchestrator.py:37:8
F401 [*] `pydantic.ValidationError` imported but unused
  --> backend/app/services/crew_orchestrator.py:42:22
```

**Why these used to die:** REQ-A6A4DB hit this on 3 successive rework
cycles — the rework prompt cited both lines exactly, but the agent
still re-emitted the file with the imports intact each time.
MAX_REWORK_CYCLES=2, so cycle 3 was the death.

**What changed (2026-05-22):** the write-time auto-format pipeline
(`src/tools/file_tools.py::_maybe_ruff_format`) now runs TWO passes
before persisting a `.py` file:

  1. `ruff check --fix --exit-zero -` — applies EVERY `[*]`-marked
     safe auto-fix in the project's configured `select` list. No
     `--select` flag, so the project's pyproject drives it. This
     blanket covers:
       - **F401** — unused imports (REQ-A6A4DB)
       - **F811** — redefined unused names
       - **I001** — unsorted/unformatted import block
       - **UP0xx** — pyupgrade modernizations (`typing.List` → `list`,
         `Optional[X]` → `X | None`, percent-format → f-string, etc.)
       - **W605** — invalid escape sequence
       - **SIM108/SIM118** — safe simplifications
       - …plus any other `[*]` rule the project's selection opted in.
     `--exit-zero` is critical: presence of a NON-fixable lint in the
     same file (e.g. F821 undefined name) would otherwise short-circuit
     the fix pass.

  2. `ruff format -` — whitespace, quoting, line-length reflow (E501).

**What this does NOT touch (deliberate):**
- `--unsafe-fixes` is NOT enabled. Ruff marks some fixes as unsafe
  because of edge-case behaviour risk (e.g. E711 `== None` → `is None`
  could differ if `__eq__` is overridden weirdly). Those stay in the
  emission and surface to the rework loop. Agent has to address them.
- Real semantic errors (F821 undefined name, type mismatches, broken
  syntax). These need the LLM.

**Implication for the agent:**
- Don't waste effort manually removing unused imports — they're gone
  by the time the file lands on disk.
- Don't worry about import ORDER — I001 sorts them deterministically.
- Don't worry about `typing.List` vs `list` — UP rules modernize.
- Re-exports via `__all__` are preserved (F401 respects `__all__`).
- DO worry about real errors: undefined names, type mismatches,
  missing return statements, unhandled exceptions, behaviour bugs.
  Those are what the rework loop is FOR.

**Observed in:** REQ-A6A4DB (T-6144cc94, "Build CrewAI orchestrator
service") died after 3 cycles all citing the same two unused imports.
Closed by 9 pinned behaviours in `tests/test_ruff_autofix.py`,
including verification that:
  - F401 / I001 / UP006 all auto-fix
  - `__all__` re-exports survive
  - non-fixable lint in same file doesn't block the fix pass
  - unsafe fixes (E711) are deliberately NOT applied

---

## L15 — Token-truncated emissions are the root cause of most "stuck task" deaths

**Signature:** Any of these symptoms repeatedly on the same task:
- `materialize_failed error='No code files were produced by any agent'`
  for multiple cycles in a row.
- Ruff invalid-syntax errors at suspiciously-late line numbers
  (e.g. line 306 of a test file with no clear bug at that line).
- A file that ends mid-string-literal, mid-function, or after an
  unclosed brace.
- `code_commit_max_rework_cycles_reached` after the agent appeared
  to "try" each cycle.

**Root cause:** Until 2026-05-22, code-writing agents had a default
`max_tokens=8192`. That's roughly 800-1000 lines of Python output
per response. Realistic feature tasks emit 1500-5000 lines across
multiple files in a single `### File: …` response. The model would
get cut off mid-emission; the response would parse with the early
files intact and the last file truncated (or missing entirely).
Subsequent rework cycles re-hit the same cap → same truncation point
→ same "fix" → same failure. T-6144cc94 (CrewAI orchestrator)
died this way **four times in a row** before the cap was raised.

**The fix (already deployed):**
- Code-writing agents now default to `max_tokens=32_000` (Opus 4.7's
  actual ceiling). Engages the streaming path. Gives ~3-5K LOC of
  headroom per response.
- `stop_reason="max_tokens"` is now logged at WARN with the agent_id,
  iteration, and token counts so future truncation is debuggable
  from the trace, not just inferable.

**Implication for the agent:**

1. **You have room.** Don't pre-emptively truncate or split your
   emission across multiple turns just because it feels long. 32K
   tokens covers the realistic worst case for one task.

2. **But if your task scope is genuinely huge** (>3K LOC across
   files), split your emission across multiple **tool calls** within
   the same dispatch — call `file_write` once per file rather than
   stuffing everything into one final `### File:` block response.
   Each tool call is a separate response with its own token budget.

3. **If you see "Your previous response was truncated at max_tokens"
   in your rework prompt, you must change strategy** — emit fewer
   files this cycle, or move to incremental `file_write` /
   `search_replace` tool calls. Re-emitting the same content guarantees
   the same truncation.

4. **A "No code files produced" warning means your response had no
   `### File:` blocks at all.** Either you spent the whole response
   on prose explanation (don't — emit code) OR your response was
   truncated BEFORE you got to any file blocks. Restructure so
   `### File:` blocks come EARLY in your response, not at the end.

**Observed in:** T-6144cc94 (Phase 4: CrewAI orchestrator service)
killed across 4 dispatches (REQ-A6A4DB, REQ-DD8610, REQ-34CAD2, plus
an earlier attempt). Each cycle truncated at a different line of an
8,260-line emission target. Closed by `tests/test_agent_max_tokens.py`
(10 pinned behaviors verifying 32K for code agents, 8K otherwise).

---

## L16 — Transient network / API blips are now retried with backoff (not your fault)

**Signature:** A subtask fails with one of these error messages even
though your code/output was fine:

- `peer closed connection without sending complete message body (incomplete chunked read)`
- `Connection error.`
- `[Errno -2] Name or service not known`
- `Connection reset by peer` / `Connection refused`
- `Read timed out` / `Broken pipe`
- **`{'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'Overloaded'}, ...}`**  ← Anthropic HTTP 529 throttle (added after REQ-FC2425)
- **`anthropic.APIError: Overloaded`** / `Service Unavailable` / `Internal Server Error`

These are **not agent errors**. They're transient host-side network
failures OR transient Anthropic API throttles (HTTP 529 Overloaded
during peak hours, 5xx server errors) that previously crashed the
agent's subtask immediately and consumed a rework cycle for nothing.

**What changed (2026-05-22, after REQ-E3A10E / T-acb5ab46):**

`BaseAgent._call_anthropic`'s retry loop now classifies these as
**transient network errors** and retries with backoff `5s → 15s →
30s → 60s → 120s` (~4-minute cumulative budget across 5 attempts).
Real-world blips of ~1-2 minutes are now invisible to the workflow.

**Implication for the agent:**

- **If you see one of these errors in your `error_message` field on
  a previous subtask in the rework prompt, ignore it.** It's a
  network blip, not something your code did wrong. Don't try to
  "fix" the imports or rewrite the file because of a connection
  error — the previous cycle never even sent your output, the
  network just gave up.

- **You can't cause these errors.** Nothing in your emission strategy
  affects whether the host's DNS resolves or whether the TLS handshake
  succeeds. Focus your rework attention on the actual code / lint
  errors, not connection errors.

- **If multiple cycles all show connection errors,** the host has a
  bigger problem (extended outage). The workflow runner will eventually
  give up; don't burn budget trying to compensate.

**Observed in:** REQ-E3A10E (T-acb5ab46, "Build app shell with
navigation and theme tokens"). A ~1-2 min network outage at
18:17:48-18:19:26 caused:
  - code_reviewer: peer-closed-connection (Anthropic stream died)
  - tester_specialist: Connection error.
  - GitHub publish: Name or service not known (DNS)
  - frontend_specialist rework: Connection error.
  - code_reviewer rework: Connection error.

Tester eventually completed at 18:19:26 once the network recovered,
but rework cycles had been consumed by then and the request was
marked failed. Closed by `tests/test_transient_network_retry.py`
(13 pinned behaviors verifying all observed transient patterns are
classified retryable, AND that validation / auth / rate-limit /
compile errors are NOT misclassified).

---

## L17 — Re-emitting the same shrunken file is a guaranteed loop death

**Signature:** Drop-guard rejections that all look identical across cycles:
```
materialize_failed error="Refusing to overwrite
'frontend/src/components/projects/ProjectFormModal.tsx': line count
dropped from 764 to 275 (64% reduction). ..."
```
…repeated 2-3 times for the SAME file with the SAME numbers. The
rework prompt cited the issue, listed the three fixes (MERGE / SURGICAL
/ SPLIT), the agent re-emitted byte-identical content, drop guard
rejected again. T-103e9025 died this way after 3 cycles all emitting
the exact same 275-line shrink.

**What changed (2026-05-22, after REQ-FEC71B):**

`CodeWriter` now **fingerprints every drop-guard rejection** with a
sha256 of the rejected emission per `(request_id, file_path)`. When
the SAME content comes back on the next cycle, the validator raises
a **much louder, much more actionable** error message:

```
🚨 DROP-GUARD LOOP for 'X': you submitted BYTE-IDENTICAL content
twice in a row that shrinks the file from N → M lines.
The previous cycle's rejection message already explained the fix —
re-emitting the same bytes will fail again every cycle until you run
out of budget.

YOU MUST CHANGE STRATEGY THIS TURN. Pick exactly ONE:
  (A) Use search_replace (not ### File: blocks) for the specific
      edit you want. search_replace is diff-based and bypasses
      this guard entirely.
  (B) Re-read the existing file via file_read FIRST, then emit a
      FULL rewrite that includes every line you don't intend to
      delete. The current file has {N} lines; your last two
      emissions had {M}. That gap is what needs to disappear.

Identical content on the next cycle will be treated as a permanent
failure (no further rework granted).
```

The cache is per-request and self-clears on successful materialize,
so re-dispatching the same task is unaffected.

**Implication for the agent:**

1. **If you see the LOOP message in your rework prompt, you are
   one cycle from permanent failure.** Switch strategy immediately —
   the path you've tried twice does not work.

2. **`search_replace` is your safest bet for a partial edit.**
   It's diff-based and the drop guard doesn't apply. Use it when
   you want to change a function body, fix a few imports, or
   inject a new method into an existing class.

3. **For a true full rewrite, `file_read` the existing file FIRST**
   and reconcile your new emission against it. The most common cause
   of this loop is the agent emitting what it THINKS the file should
   look like without ever reading what's actually on disk.

4. **Do NOT add "rest unchanged" / "rest stays the same" comments
   to a `### File:` block** — the marker check catches those and
   you'll fail for a different reason. CodeWriter does whole-file
   replacement; partial content silently deletes the rest.

**Bonus fix (data integrity):** `delete_request()` now also nulls
`project_tasks.request_id` for any task that pointed at the deleted
request. Previously a DELETE left dangling pointers; the task popup
would load but fail to render review/test/commit data because the
referenced request no longer existed. This isn't an agent issue —
it's a backend cleanup — but if you see a task whose popup is blank
where it should show history, the back-link is the culprit.

**Observed in:** T-103e9025 (Phase 7: Projects Management UI —
Build projects list page). 3 cycles all emitted 275-line ProjectFormModal.tsx
(file was 764 lines on disk). Each cycle's rework prompt cited the
drop-guard rejection. The agent's response titles across cycles all
referenced "Looking at this conversation, I can see that…" suggesting
context confusion. Closed by `tests/test_drop_guard_loop.py` (6 pinned
behaviors verifying the escalation triggers ONLY on byte-identical
re-emissions for the same request_id+file_path) and
`tests/test_delete_request_cascade.py` (3 tests verifying the
back-link nulling cascade).

---

## L18 — Test-case UPSERT (deterministic TC-XXX IDs across cycles are safe)

**Signature:** Look at the trace for a failed task. The tester subtask
completes successfully (you can see `output_text` filled with the test
report table), but the orchestrator logs:
```
test_case_parsing_failed error='UNIQUE constraint failed: test_cases.test_id'
combined_gate_failed review_passed=... test_passed=False
combined_gate_failed_reworking cycle=1 max_cycles=2
```
…and the SAME message repeats on cycle 2. Out of cycles → REQUEST FAILED.

The most-recent cluster: T-b4954195 and T-3e1303b3 (Phase 11 quality
& polish tasks) BOTH died on 2026-05-22 with this exact pattern —
the tester correctly emitted `| TC-001 | … |` rows, but the backend
crashed the entire parse batch on cycle 2 because TC-001 already
existed from cycle 1.

**What changed (2026-05-22):**

1. `SQLiteStateStore.create_test_case` switched from plain INSERT to
   `INSERT INTO test_cases (…) VALUES (…) ON CONFLICT(test_id) DO
   UPDATE SET story_id=excluded.story_id, name=excluded.name,
   status=excluded.status, last_run_at=excluded.last_run_at`. So the
   second emission of TC-001 updates the row instead of failing.
2. The orchestrator's `_parse_and_save_test_cases` loop now wraps
   EACH `create_test_case` call in its own try/except. One bad row
   no longer aborts the whole batch — the rest persist and the
   combined gate gets a real test_passed reading.

**Implication for the agent:**

- **It's safe to emit the same TC-XXX IDs across rework cycles.** The
  underlying backend now UPSERTs them. Don't try to invent new IDs
  on a rework cycle just to "avoid duplicates" — that would be
  WORSE: the dashboard would show stale TC-001 alongside fresh TC-099
  for the same actual test.
- **Status changes (pass → fail, or vice versa) are honored on
  re-emit.** If a previously-passing test now fails, just emit the
  same TC-XXX with the new status and the row will update.

**Backend integrity note** (for me / future debuggers, not for the
agent's behavior): the underlying problem here was a backend bug
masquerading as an agent failure. The agent was emitting valid output;
the persistence layer's plain INSERT raised UNIQUE; the orchestrator's
broad `except Exception` swallowed the row error AND took down the
whole batch's coverage stats. Three things had to go right for this
to fail: (a) plain INSERT, (b) broad except, (c) gate consumer
reading the (now-empty) coverage dict. Fixing (a) was sufficient
because UPSERT can't raise UNIQUE; but I also fixed (b) so any
future "single-row constraint we haven't anticipated" failure won't
recreate the same cascade. Defense in depth.

**Observed in:** T-b4954195 ("Add frontend component and integration
tests") + T-3e1303b3 ("Add backend unit and integration tests"). Both
hit the same UNIQUE constraint repeatedly on cycles 1 + 2 and timed
out the rework budget. Same signature also visible in REQ-FC2425's
trace (T-103e9025) where it compounded with the Anthropic overload
error. Closed by `tests/test_test_case_upsert.py` (4 pinned
behaviors: UPSERT overwrites, supports story_id relink, repeated
inserts don't grow the table, per-row failure isolation).

---

## L19 — Atomic-task contract (one file, one acceptance test, 50-300 LOC)

**Signature:** Tasks dispatched after Build Plan Decomposition (BPD
§6.8) shipped on 2026-05-23 carry a different shape than legacy tasks.
Their input includes:

```
primary_file: backend/app/api/v1/dashboard.py
acceptance_test: GET /dashboard/summary returns 200 with {kpis, recent_projects}
expected_loc: ~120
depends_on: [T-x42 (deployed), T-x44 (deployed)]
```

The contract is intentionally narrow — one primary file, one
sentence acceptance test, ~50-300 LOC of expected output, all
dependencies satisfied before dispatch.

**What this means for the agent's emission:**

1. **Focus on the primary_file.** ≤ 2 additional files touched (e.g.
   the test for it). Don't emit a whole subsystem — emit one cohesive
   unit. If the task feels too big to do in one response, that's a
   signal the decomposition was too coarse; surface a one-line note
   in your response and the user can split it.

2. **The acceptance_test IS the contract.** Read it literally before
   designing. If the test says "GET /X returns Y", don't add unrelated
   side effects; if the test says "the modal renders", don't also
   wire up the form submission (that's a sibling task).

3. **Trust the dependency chain.** If a task is dispatched, every
   row in its `depends_on` is `deployed`. The files / endpoints /
   schemas your inputs reference ARE in the working tree. Don't
   re-emit them defensively.

4. **Cross-feature deps look like `T-XXX` task_ids in your inputs.**
   The depends_on list may reference tasks in OTHER features /
   epics. Treat them the same — if a row is in your depends_on, it's
   deployed.

**Defensive checks (still required):**

- L05 still applies: emit `### File:` blocks for any file you create
  or modify. The system materializes them to disk before review.
- L17 still applies: don't emit byte-identical shrunken content if
  you got a drop-guard rejection on the previous cycle.
- L18 still applies: emit deterministic TC-XXX test IDs.

**Legacy tasks (created BEFORE BPD shipped) have no BPD fields.**
Their input looks unchanged from prior cycles. Treat them as before —
the contract above only applies when `primary_file` / `acceptance_test`
are present in the input.

**Observed: BPD shipped 2026-05-23 across phases A-E
(commits c01265f → ac5bb2e). 34 of 45 BPD tasks done; remaining 3
in-progress UI polish surfaces and 8 not-started phase-E
verification tasks ship in a follow-up.**

---

## L20 — Verify the wire format, not just the DB and not just the component logic

**Signature:** A UI list / tree shows empty. You check the DB and the
data is there. You check the component render path and the logic looks
right. You restart, you refresh, you blame caching — and the symptom
persists across multiple debug sessions. The actual gap is the API
serializer in the middle: it never put the field on the wire.

**Concrete instance (2026-05-23, BuildPlanView "0 BPD tasks" bug):**

| Layer | What it said | What I assumed |
|---|---|---|
| **DB** | `SELECT COUNT(*) FROM project_tasks WHERE feature_id IS NOT NULL` → 348/348 | "Data is fine, must be a frontend bug" |
| **Component** | `BuildPlanView.tasksByFeature` groups by `t.feature_id`, skips falsy. Code reads correctly. | "Must be auto-refresh not firing" |
| **Restart + refresh + hard-cache-bust** | Same symptom every time | "Vite HMR on Windows bind mount is flaky again" |
| **The actual culprit** | `_task_to_dict` in `src/api/routes/projects.py` returned 16 fields. None of them were `feature_id`, `depends_on`, `primary_file`, `expected_loc`, `acceptance_test`. The 5 BPD fields were silently absent from every `/projects/{pid}/tasks` response since BPD shipped. | (only found by curling the live endpoint and counting keys) |

The DB had the data. The component would have grouped it correctly.
The serializer in the middle dropped it on the floor. Every "auto-
refresh failing", "cascade NULLing feature_id", and "stale Vite
bundle" diagnosis was looking at the right rooms but the wrong floor.

**The fix took 5 minutes once located.** Three days of intermittent
debugging targeted the wrong layer because no one looked at the wire.

**What to do when you see this symptom pattern:**

1. **First debug cycle, before assuming anything**: curl (or hit via
   the in-container Python httpx client) the actual API endpoint the
   component fetches. Count the keys in `response.data[0]`. Compare
   against the component's TypeScript interface. **If a field the
   component reads isn't on the wire, that IS the bug** — skip the
   rest of the analysis.

2. **When adding a new column to a Pydantic model** (e.g. the 5 BPD
   fields on `ProjectTask`), there are THREE places that need updating
   in this codebase:
   - The model itself (`src/models/base.py`)
   - The SQL CREATE TABLE + INSERT in `src/state/sqlite_store.py`
   - **Every `_X_to_dict()` serializer** in `src/api/routes/*.py`
     that hands the row to the frontend
   The third is the one that gets forgotten because there are usually
   multiple serializers per model (one per endpoint shape) and adding
   a column doesn't make any of them fail loudly.

3. **Component-side defence**: when you write a grouping/filtering
   useMemo like `tasksByFeature`, add a one-line dev-mode warning
   when N input rows produce 0 grouped rows. The presence of input
   rows + absence of output rows is the canary that the input rows
   are missing the grouping key.

**Examples of the warning pattern:**

```tsx
const tasksByFeature = useMemo(() => {
  const m = new Map<string, Task[]>()
  for (const t of tasks) {
    if (!t.feature_id) continue
    if (!m.has(t.feature_id)) m.set(t.feature_id, [])
    m.get(t.feature_id)!.push(t)
  }
  if (process.env.NODE_ENV !== "production" && tasks.length > 0 && m.size === 0) {
    console.warn(
      "[tasksByFeature] received", tasks.length, "tasks but grouped 0 —",
      "first task keys:", Object.keys(tasks[0] || {}),
    )
  }
  return m
}, [tasks])
```

This converts a 3-session debugging spiral into a 5-second console
glance: "the response has no feature_id key, fix the serializer".

**Related lessons:**

- L05 (emit `### File:` blocks) — same family: silent serialization
  gap between what the agent emitted and what the materializer saw.
- L11 (whole-file scan) — same family: checked one layer (lint output)
  not the layer where the symptom actually was (the source file).

**Observed: 2026-05-23. The BuildPlanView tree had been silently
broken for everyone since the BPD shipped on 2026-05-23 itself.
The bug surfaced as "tasks list empty" three times across two days
before the wire-format check finally located it.**

---

## L21 — Direct database import in a route file

**Signature:** `import aiosqlite` or `import sqlite3` (or any other DB driver import) appears inside any file under `src/api/routes/`.

**Cause:** Agent generated route-handler logic that accesses the database directly, bypassing the `StateStore` abstraction layer in `src/state/base.py`. This breaks the layered architecture: routes → state store → DB.

**Fix:** Replace the direct DB call with the appropriate `StateStore` method (e.g., `await state_store.get_request(id)`, `await state_store.save_document(...)`). The `state_store` instance lives on `request.app.state.state_store`. **Never import a database driver in a route file.**

```python
# ❌ Wrong — route bypasses StateStore
import aiosqlite

@router.get("/items")
async def get_items():
    async with aiosqlite.connect("data/agent_team.db") as db:
        rows = await db.execute("SELECT * FROM items")

# ✅ Correct — access through StateStore
from fastapi import Request

@router.get("/items")
async def get_items(request: Request):
    state_store = request.app.state.state_store
    return await state_store.get_items()
```

**Quick check:** `grep -r "import aiosqlite\|import sqlite3" src/api/routes/` should return nothing. Any hit is a violation.

**Observed in:** Phase AE-5 architectural analysis (2026-05-25).

---

## L22 — New FastAPI route handler not registered in main.py

**Signature:** A new `@router.get/post/put/delete` handler exists in `src/api/routes/<module>.py` but the endpoint returns `404 Not Found` at runtime despite the server running cleanly.

**Cause:** Agent created the route file (or added a new `router = APIRouter()` to an existing file) but did not add the matching `app.include_router(new_router, prefix="/api/v1/...")` call in `src/main.py`. FastAPI only serves routes that are explicitly registered — creating the file is not enough.

**Fix:** After adding any new route file or a new router object, immediately open `src/main.py` and add the `include_router` call alongside the existing registrations. **Both the handler file AND the registration in `main.py` are required.** One without the other = unreachable endpoint.

```python
# After creating src/api/routes/widgets.py with router = APIRouter():
# src/main.py — add this line:
from src.api.routes import widgets
app.include_router(widgets.router, prefix="/api/v1/widgets", tags=["widgets"])
```

**Quick check after every backend change:** Count `grep -c "include_router" src/main.py` and compare against the number of non-`__init__` files in `src/api/routes/`. A mismatch means a registration is missing.

**Observed in:** Phase AE-5 architectural analysis (2026-05-25).

---

## How to add a new lesson

When a new failure pattern is observed in production:

1. Add a section to this file (L11, L12, etc.) following the same
   shape.
2. Include the request ID and the verbatim error signature.
3. The next agent invocation picks up the new lesson automatically
   — no code change, no restart needed.
4. Commit this file to the repo so the history of "what we've learned"
   stays version-controlled.

**Do NOT:**

- Edit existing lessons after the fact unless the system behaviour
  changed (e.g. an exempt list expanded). Append a "Update YYYY-MM-DD"
  note instead so the audit trail is preserved.
- Add lessons for one-off bugs that don't represent a pattern — this
  doc is for recurring failure classes, not unique incidents.
- Add UI-only or product-design lessons here — those belong in
  per-agent YAML.

---

## Maintenance log

- **2026-05-21** — Doc created. Initial population from session-long
  debugging of REQ-D53897 → REQ-A9283B → REQ-F86080 → REQ-BB30E2 →
  REQ-5F25E9 (the first end-to-end success after the materialize
  hook landed in commit `d78de9d`).
- **2026-05-22** — Added L11 (docstring E501), L12 (multi-line lint
  failures need whole-file scan), L13 (don't burn 25 iterations on
  exploration). Observed in REQ-B55FB8 (T-029e7cfb death) and
  REQ-270B83 (T-afdfc0c5 stuck after iteration-25 burn).
  Scaffold `pyproject.toml` updated with `per-file-ignores` for tests.
- **2026-05-22 (later)** — Added L14 (unused imports auto-stripped on
  write). Auto-format pipeline now runs `ruff check --fix --select
  F401,F811,I001` BEFORE `ruff format`. Closes REQ-A6A4DB's failure
  class (3 cycles all citing the same F401 imports). Regression test
  in `tests/test_ruff_autofix.py`.
- **2026-05-22 (even later)** — Broadened L14: dropped the `--select`
  narrow list. Auto-fix pass now applies EVERY `[*]` safe fix in the
  project's configured ruff selection (F + I + UP + SIM + most E/W).
  "Basic compilation errors" (unused imports, stale typing,
  out-of-order imports, etc.) are now stripped at write-time
  unconditionally. Agents only see the rework loop for REAL semantic
  errors. Test count: 6 → 9.
- **2026-05-22 (deep RCA)** — Added L15 (token truncation is the root
  cause of most "stuck task" deaths). T-6144cc94 was failing across
  4 dispatches with shifting surface errors (F401 cycles 1-3 of
  REQ-A6A4DB, "no code produced" on REQ-DD8610 cycles 0-1, broken
  syntax on REQ-34CAD2 cycle 2). Common cause: 8K default max_tokens
  truncated the multi-file emission. Code-writing agents now default
  to 32K (engages streaming). `stop_reason='max_tokens'` is logged
  explicitly so future truncations are debuggable from the trace.
- **2026-05-22 (network resilience)** — Added L16 (transient network
  blips retried with backoff). REQ-E3A10E (T-acb5ab46) failed
  during a ~1-2 min host-side network outage that cascaded through
  review + test + commit. Retry loop in `_call_anthropic` now
  classifies "peer closed connection", "Connection error.", DNS
  failures, etc. as retryable and waits 5/15/30/60/120s between
  attempts (~4 min cumulative). Test: `test_transient_network_retry.py`
  (13 patterns).
- **2026-05-22 (drop-guard loop kill)** — Added L17 (same-shrunk-file
  re-emission). T-103e9025 (REQ-FEC71B) died after 3 cycles each
  emitting byte-identical 275-line shrink of a 764-line file. The
  drop guard now sha256-fingerprints rejections per (request_id,
  file_path) and escalates with a much louder error on a repeat.
  Also fixed `delete_request()` to null the project_tasks.request_id
  back-link — the DELETE was leaving dangling pointers that broke
  the task popup. Tests: `test_drop_guard_loop.py` (6) +
  `test_delete_request_cascade.py` (3).
- **2026-05-22 (anthropic overload)** — L16 classifier extended to
  catch Anthropic's HTTP 529 `overloaded_error` envelope (and bare
  `Overloaded`, `Service Unavailable`, `Internal Server Error`).
  REQ-FC2425 (T-103e9025 re-dispatch) had its cycle-0 frontend
  agent fail with `{'type':'overloaded_error','message':'Overloaded'}`;
  the old classifier didn't match, so the subtask was marked
  permanently failed and the downstream cascade burned the remaining
  rework cycles. Test coverage: `test_transient_network_retry.py`
  expanded 13 → 17 (added overloaded-envelope + bare-Overloaded +
  503 + 500 patterns).
- **2026-05-22 (test-case UPSERT)** — Added L18 (TC-XXX deterministic
  IDs across cycles are now safe). T-b4954195 + T-3e1303b3 both died
  on 2026-05-22 with `UNIQUE constraint failed: test_cases.test_id`
  on cycle 1+2 — the tester correctly emitted the same IDs each
  cycle (deterministic on purpose), but the backend's plain INSERT
  raised, the orchestrator's broad except killed the whole batch's
  coverage stats, and the combined gate flipped test_passed=False.
  Two fixes: (a) SQL is now UPSERT on test_id; (b) orchestrator
  parse loop isolates per-row failures. Test:
  `test_test_case_upsert.py` (4 pinned behaviors).
- **2026-05-23 (BPD shipped)** — Added L19 (atomic-task contract).
  Build Plan Decomposition (PRD §6.8) shipped across phases A-E
  on 2026-05-23. Tasks emitted under Pass 3 carry `primary_file`,
  `acceptance_test`, `expected_loc`, and `depends_on`. Legacy tasks
  (no BPD fields) continue to function unchanged. The lesson tells
  agents how to read and act on the new fields.
- **2026-05-25 (Phase AE-5 arch pre-seed)** — Added L21 (direct DB
  import in route file) and L22 (new FastAPI route not registered in
  `main.py`). Both patterns were identified during the Phase AE-5
  architectural analysis. Pre-seeded so all 10 existing agents benefit
  immediately — before the `architecture_reviewer` agent is live.
- **2026-05-23 (wire-format gap)** — Added L20 (verify wire format,
  not just DB / not just component). The "BuildPlanView shows 0 BPD
  tasks despite 348 tasks in the DB" bug surfaced three times across
  two days. Each time it was diagnosed as something else (auto-
  refresh, cascade NULLing, Vite HMR cache) until someone finally
  hit the live endpoint and counted the keys: `_task_to_dict` had
  never serialized the 5 BPD fields (`feature_id`, `depends_on`,
  `primary_file`, `expected_loc`, `acceptance_test`) — the columns
  existed in the DB, were correctly read into the Pydantic model,
  but the dict serializer at the API boundary dropped them. Fix
  (one-line addition to the dict) took 5 minutes once located. The
  lesson teaches: when DB is fine + component is fine + UI is empty,
  the third option is the API serializer in the middle. Also includes
  a useMemo dev-warning pattern that converts the future occurrence
  of this class into a single console line.
