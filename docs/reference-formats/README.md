# Reference formats

These two markdown files are the **format templates** that the platform's
PRD generator and Tasks generator agents read at runtime to shape their
output.

| File | What it's used for |
|---|---|
| `prd-template.md` | Sample PRD shown to `prd_specialist` when the user clicks **Generate PRD**. Demonstrates the expected document layout — Document Information table, numbered sections, ID-tagged Functional Requirements tables, ASCII UI mockups, ERD + table-by-table schema, etc. |
| `api-spec-template.md` | Sample enterprise-grade REST API specification shown to `backend_specialist` when the user clicks **Generate API Spec**. Codifies industry conventions: OpenAPI 3.1, RFC 7807 errors, cursor pagination, `X-Request-ID` propagation, idempotency keys, ETag caching, HMAC-signed webhooks, RFC 8594 deprecation. |
| `tasks-template.md` | Sample task list shown to `user_story_author` when the user clicks **Generate Tasks**. Demonstrates the phase-grouped task layout with per-task sub-task tables and Rules references. |

## How they're used

`src/api/routes/projects.py` reads these files on each generate-PRD /
generate-tasks call and injects them into the agent prompt as
"here's an example of the format I want". The agent matches the
structure but produces content for the current project's brief / PRD.

Because they're loaded at request time:
- **Edits propagate immediately** — no code change, no restart needed.
- If a file is missing or unreadable, the prompts fall back to an
  inline structural description (the code does the safe thing — it
  doesn't refuse to generate).

## When to edit

- You decide a new top-level section should appear in every PRD →
  add it to `prd-template.md`. Next generate-PRD call will follow.
- You want sub-tasks to include estimated hours / story points →
  add that column to a sample task's sub-task table in
  `tasks-template.md`. Next generate-tasks call will pick it up.

## When NOT to edit

These are FORMAT references, not content references. Don't fill them
with content specific to one product — keep them generic enough that
any project's PRD/tasks could plausibly look like this.

The original source for these is the Atlas Advisory product
documentation under `C:/ai-projects/tech-advisory-v1/references/`.
That folder is the upstream; this directory is the in-repo snapshot.
