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
