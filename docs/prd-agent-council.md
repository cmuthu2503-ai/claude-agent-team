# Product Requirements Document (PRD)
# Agent Council — Ad-Hoc AI Review Panel

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 2.1 |
| Created Date | 2026-06-26 |
| Last Updated | 2026-06-26 |
| v2.1 change | Added **document/file upload** as an alternative to paste (§2.1 G8, §3.8, §4.5 AC-060..AC-066, §4.3 AC-033/AC-036, §6, §7, §8). Reuses the existing `src/knowledge/loaders.py::load_text` extractor — no new deps. |
| Status | **Draft — pending review** |
| Product Owner | Chandramouli |
| Task Prefix | `AC` (Agent Council) |
| Source / companion | [docs/tasks-agent-council.md](tasks-agent-council.md) |
| Reviewed by | Atlas (Architecture Review) — v1 drafts found defective; this is the corrected spec |

---

## 1. Executive Summary

### 1.1 Product Vision

The **Agent Council** is a dedicated page in the Agent Team dashboard where a user submits **ad-hoc, one-shot review requests** to specialist reviewer agents — *outside* the normal orchestration pipeline (PRD → stories → code → review → deploy). Where the **Command Center** dispatches a full multi-stage workflow, the Council is a **direct ask → structured answer** surface: paste content, pick a reviewer, get a structured Markdown report back, persisted for later reference.

### 1.2 Problem Statement

Today **every** Agent Team interaction is a Request that runs the full workflow runner (`src/workflows/runner.py`) — PRD, stories, backend, frontend, review, test, deploy stages with rework loops. There is no lightweight surface for the two highest-frequency "just give me an expert opinion" asks:

1. **Code Quality Review** — a developer wants a senior reviewer's eye on a snippet or file before opening a PR, without triggering a `feature_development` workflow.
2. **Document Quality Review** — a PM/TL has a PRD, spec, or proposal and wants a gap analysis (completeness, contradictions, testability, clarity) without spinning up the research/content teams.

Both are **one-shot, read-evaluate-report** tasks. The reasoning capability already exists (`CodeReviewerAgent`), but it is locked behind the workflow runner. The Council exposes it directly through the existing one-shot execution path (`AgentSystemExecutor.single_agent_call`).

### 1.3 Target Users

- **Developers** wanting a pre-PR code review.
- **PMs / Tech Leads** wanting a PRD/spec reviewed for completeness and testability.
- **Anyone** who wants a specialist agent's opinion without the overhead of a full workflow.

### 1.4 Scope of v1

Ship **two** reviewer agents — Code Quality Reviewer and Document Quality Reviewer — behind one page. Architect the page and API so a third reviewer is a small additive change (one agent class + one YAML + one selector entry), not a refactor.

---

## 2. Goals & Non-Goals

### 2.1 Goals

- **G1** — A dedicated `/council` page reachable from the sidebar, distinct from the Command Center, available to every authenticated user.
- **G2** — **Code Quality Reviewer**: paste code → structured report (correctness, security, error-handling, maintainability) with a clear verdict.
- **G3** — **Document Quality Reviewer**: paste a document → gap analysis with specific, actionable refinement suggestions and a verdict.
- **G4** — Results are one-shot: a single LLM call via `executor.single_agent_call()`. No subtasks, no workflow stages, no rework loop.
- **G5** — Council reviews are **persisted across restarts** and listed as history; a review opens to its full report.
- **G6** — The page is native: same theme tokens, sidebar, auth, API envelope, and Markdown renderer as the rest of the dashboard.
- **G7** — Every review **records token usage and cost** through the same `TokenTracker` path as all other LLM calls, so Council spend is visible on the Cost Dashboard.
- **G8** — A user can **upload a document/file** (PDF, DOCX, XLSX, Markdown, `.txt`, or a source-code file) as an alternative to pasting. The file is extracted to text **server-side** and reviewed exactly like pasted content. Paste and upload are mutually exclusive per submission.

### 2.2 Non-Goals (v1)

- Streaming responses (return the full report in one response).
- Multi-agent council sessions (e.g. code + architecture reviewer together).
- Approval gates / human-in-the-loop (Council is directly user-initiated).
- Tool-augmented review (no `file_read`, `web_search`, CVE lookup, or KB retrieval — see §7, this is the most important deliberate limitation).
- Persisting the **submitted content or the uploaded file** (we persist only the report — see §6.3 and the security rationale in §8). The uploaded file is extracted in-memory and discarded; it is never written to disk or the DB.
- OCR of scanned/image-only PDFs (text-layer extraction only — see §7).
- Multi-file upload in a single review (one file per submission in v1).

---

## 3. Architecture & Grounding

> This section is written against the **actual code**, not an idealized model. Citations are to real files/lines so the implementer and reviewer can verify every claim.

### 3.1 Execution path — use the executor, not the agent

Routes do **not** hold agent instances. They hold the executor on `app.state`:

- `src/main.py` stashes `app.state.agent_executor = agent_executor` (lifespan).
- The Prompt Studio route reaches it via `request.app.state.orchestrator._agent_executor` (`src/api/routes/prompts.py::_get_executor`, returns **503** when unavailable).

The correct one-shot entry point is **`AgentSystemExecutor.single_agent_call(agent_id, prompt, max_tokens=..., label=...)`** (`src/agents/executor.py:686`). It:

1. resolves the model per-call via the 5-layer `ModelResolver` chain;
2. marks the agent **busy** so the Team Status page reflects in-flight Council work;
3. calls `agent.single_call(prompt, ...)` — **one LLM call, no tool-use loop**;
4. **records `TokenUsage`** (input/output tokens + computed `cost_usd`) so Cost Dashboard picks it up.

> ⚠️ The v1 drafts said "call `agent.single_call(prompt)`" directly from the route. That bypasses model resolution **and** cost attribution and is not reachable from a route (no agent handle). The Council route MUST go through `executor.single_agent_call`.

`single_call()` applies the agent's system prompt via `_build_system_prompt()` (`src/agents/base.py:672` sets `system=self._build_system_prompt()`), which is `date_header + knowledge_block + self.system_prompt`. **Therefore the agent's system prompt is the entire quality contract** — if the YAML system prompt is empty, the review is unstructured garbage.

### 3.2 Agent instantiation — YAML is mandatory, the class map is not enough

- `ConfigLoader._load_agents()` (`src/config/loader.py:38`) builds the agents dict **purely by globbing `config/agents/*.yaml`** (skipping `_`-prefixed templates).
- `AgentFactory.create_all()` (`src/agents/factory.py:43`) iterates **that dict** and instantiates the class from `AGENT_CLASS_MAP` (falling back to `_GenericAgent` for an unknown id).

**Consequence (the #1 defect in the v1 drafts):** adding `"document_reviewer": DocumentReviewerAgent` to `AGENT_CLASS_MAP` (already done, uncommitted) is **necessary but not sufficient**. Without `config/agents/document_reviewer.yaml`, the agent is **never created or registered**, so `single_agent_call("document_reviewer", …)` returns `{"error": "agent_not_found"}` (`executor.py:716`) and the would-be agent has **no system prompt**. The YAML is the deliverable that makes the agent real.

> The existing `code_reviewer` already has a YAML (`config/agents/code_reviewer.yaml`) with a strict report format, so the **Code Quality Reviewer half works today**. The **Document Quality Reviewer half is currently dead code** until its YAML lands.

### 3.3 Mock mode is a real, reachable state

When no LLM credentials are configured, `single_call()` returns a **mock stub string** (`base.py:318`: `"(mock {agent_id} output for prompt: …)"`) and `app.state.agent_mode == "mock"` (`main.py::resolve_agent_mode`). The Council route MUST detect mock mode and surface it as a labelled, non-authoritative result — never present a mock stub as a real review. (Staging/production refuse to boot in mock mode, so this is a local-dev/demo concern, but it is still a correctness requirement.)

### 3.4 Persistence — reuse the `Document` infrastructure

The repo already persists agent outputs as `Document` rows (`src/models/base.py:297`), keyed by `doc_type` (`prd | user_stories | backend_code | code_review | test_report | …`), with `save_document` / `get_document` / `search_documents` / `get_documents_for_request` in the `StateStore` (`src/state/base.py:209`, `sqlite_store.py:1980`) and a `delete_document` route with RBAC (`src/api/routes/documents.py:105`).

Council reviews ARE "documents produced by an agent." v1 persists them via this existing path with two new `doc_type` values:

- `council_code_review`
- `council_doc_review`

This buys persistence-across-restart, history, detail, search, and RBAC delete **with zero schema migration**. `Document.request_id` is required but `""` is an accepted sentinel in this codebase (the executor records token usage with `request_id=""`). Council-specific metadata (agent type, language/doc type, focus areas) is stored in `Document.tags`.

> ⚠️ The v1 drafts chose an in-memory `app.state.council_sessions = {}` dict. That **loses all history on every `docker compose restart backend`** — which CLAUDE.md mandates after *every* `src/` edit — and is not shared across workers. Rejected.

### 3.5 Frontend wiring

- Routes are declared once in `frontend/src/App.tsx` (inside `RequireAuth`/`Layout`).
- Sidebar nav is a `navItems` array in `frontend/src/components/layout/Sidebar.tsx` (`adminOnly` flag controls visibility; Council is **not** adminOnly).
- API calls go through the `api` singleton in `frontend/src/lib/api.ts` (`api.get/post/delete`, Bearer token, 401 handling, envelope `{data, meta, error}`).
- Markdown is rendered by `frontend/src/components/ui/MarkdownRenderer.tsx`.
- Types in `frontend/src/api/schema.d.ts` are **generated** from the live OpenAPI (`npm run generate-types`); new endpoints require regeneration.

### 3.6 System diagram (corrected)

```
┌──────────────────────────── Agent Team Dashboard ────────────────────────────┐
│  Sidebar nav "Agent Council" (Gavel)  ->  /council  (RequireAuth + Layout)    │
│                                                                               │
│   AgentCouncil.tsx                                                            │
│    |- reviewer selector  [Code Quality] [Document Quality]                    │
│    |- <textarea> content (monospace)   + context fields + focus areas         │
│    |- Submit  -- api.post("/council", {agent_type, content, ...}) --+         │
│    |- result (MarkdownRenderer)                                     |         │
│    `- history (api.get("/council"))  -> expand (api.get("/council/{id}"))     │
└──────────────────────────────────────────────────────────────────-+----------┘
                                                                      |
                    ┌──────────────────────────────────────────────────v─────────┐
                    │ src/api/routes/council.py  (mounted in src/main.py)         │
                    │  POST /api/v1/council                                       │
                    │   1. auth (get_current_user)                                │
                    │   2. validate agent_type in {code_reviewer,document_reviewer}│
                    │   3. validate content non-empty & <= MAX_CONTENT_CHARS      │
                    │   4. build review prompt (context + focus + content)        │
                    │   5. executor = orchestrator._agent_executor (503 if none)  │
                    │   6. if agent_mode == "mock": return mock-labelled result   │
                    │   7. executor.single_agent_call(agent_type, prompt,         │
                    │         max_tokens, label)   -> resolves model, records cost│
                    │   8. save_document(doc_type=council_*, request_id="",       │
                    │         content=report, tags=[...])                         │
                    │   9. return {council_id, agent_type, review_report, ...}     │
                    │  GET  /api/v1/council          -> search_documents(doc_type) │
                    │  GET  /api/v1/council/{id}      -> get_document              │
                    │  DELETE /api/v1/council/{id}    -> delete_document (RBAC)    │
                    └─────────────────────────────────────────────────────────────┘
```

### 3.7 How it differs from Command Center

| Aspect | Command Center | Agent Council |
|--------|----------------|---------------|
| Purpose | Dispatch full workflows | One-shot review |
| Pipeline | Multi-stage DAG + rework loops | Single `single_agent_call` |
| Agents | Whole team orchestrated | One reviewer agent |
| Tools | Full tool grants (file_read, git, web…) | **None** (no tool loop in `single_call`) |
| KB grounding | Yes (agentic/forced retrieval) | **No** (v1) |
| Output | Deployed feature + artifacts | Markdown review report |
| State | Requests, subtasks, stories, deploys | One `Document` row |
| Persistence | SQLite (many tables) | SQLite `documents` table (reused) |

### 3.8 Document upload — reuse the KB text extractor (no new infrastructure)

Verified in the repo: the upload path is **reuse, not new infrastructure**.

- **Parsing already exists.** `src/knowledge/loaders.py::load_text(filename, data) -> (text, source_type)` extracts text from PDF (PyMuPDF/`fitz`), DOCX (python-docx), XLSX (openpyxl), and decodes Markdown/text/source-code. It raises `UnsupportedFileTypeError` (→ 415) for unknown extensions and `LoaderUnavailableError` (→ 503) when an optional lib is missing. `SUPPORTED_EXTENSIONS` is the authoritative allow-list.
- **Dependencies already present** (`pyproject.toml`): `pymupdf>=1.24.0`, `python-docx>=1.1.0`, `python-multipart>=0.0.9`. (`openpyxl` powers XLSX in the KB path.) **No new dependency is added by this feature.**
- **Multipart already wired.** FastAPI `UploadFile`/`File`/`Form` are used by `src/api/routes/knowledge.py::upload_document` and `requests.py`; the frontend `api.postForm(path, FormData)` client method already exists (`frontend/src/lib/api.ts`) and is used by Knowledge Base + Command Center.
- **Size cap precedent.** KB upload caps at `25 * 1024 * 1024` (25 MB) and returns **413** over the limit. Council adopts the same default (`COUNCIL_MAX_UPLOAD_BYTES`, env-overridable).

**Flow:** the new `POST /api/v1/council/upload` accepts a multipart `file` + the same context form fields, reads bytes (413 if over cap), calls `load_text()` (415 unsupported / 503 lib-unavailable), enforces the **extracted text** against the existing `MAX_CONTENT_CHARS` cap (400 if empty-after-extraction or oversize), then funnels into the **exact same** prompt-build → `single_agent_call` → `save_document` path as the paste endpoint. The uploaded bytes are held only in memory for extraction and then discarded — never written to disk or DB (§8).

> ⚠️ Design choice: extraction happens **server-side**, not in the browser. The reviewer agents consume text only (`single_call` has no file tools), and the parsers already live on the backend. A browser-side parse would duplicate logic and ship megabytes of WASM for no benefit.

---

## 4. Functional Requirements

Each requirement is atomic, uniquely IDed, and has explicit acceptance criteria (AC). Priority: **Critical** (blocks v1) / **High** / **Medium**.

### 4.1 Backend — Agents & Config

| ID | Requirement | Priority | Acceptance Criteria |
|----|-------------|----------|---------------------|
| AC-001 | `DocumentReviewerAgent` class exists in `src/agents/implementations.py`, extends `BaseAgent`, `_parse_output(text)` returns `{"review_report": text}`, `_extract_artifacts` returns `[]`. | Critical | Class present (already added); `from src.agents.implementations import DocumentReviewerAgent` succeeds. |
| AC-002 | `"document_reviewer": DocumentReviewerAgent` registered in `AGENT_CLASS_MAP` (`src/agents/factory.py`). | Critical | Present (already added). |
| **AC-003** | **`config/agents/document_reviewer.yaml` created** with `agent_id: document_reviewer`, `model` matching `code_reviewer.yaml`, and the full Document Quality Reviewer `system_prompt` from §5.2. **No `tools:`** (single_call runs no tool loop). `retrieval` omitted. | **Critical** | After boot, `config.agents["document_reviewer"]` exists; `executor.registry.get("document_reviewer")` is non-None; its `system_prompt` is non-empty. **This is the gap that makes the doc reviewer functional.** |
| AC-004 | Reuse existing `code_reviewer` agent for code reviews — no new code-reviewer agent. The Council route maps `agent_type="code_reviewer"` to it. | Critical | `single_agent_call("code_reviewer", …)` returns a structured report following `code_reviewer.yaml`'s format. |
| AC-005 | The Document Reviewer `system_prompt` is self-sufficient (severity model, verdict line, structured sections) because `single_call` gives it no tools and (as a non-code agent) no lessons/KB grounding. | Critical | A doc review returns a report with a verdict line and severity-tagged findings (see §5.2 contract). |

### 4.2 Backend — Council API

All routes are mounted under `/api/v1/council` in a new `src/api/routes/council.py`, registered in `src/main.py`'s router list, and follow the `{data, meta, error}` envelope convention.

| ID | Requirement | Priority | Acceptance Criteria |
|----|-------------|----------|---------------------|
| AC-010 | `POST /api/v1/council` accepts `{agent_type, content, language?, document_type?, focus_areas?}`; requires auth (`get_current_user`). | Critical | Unauthn request → 401. Valid request → 200 with the response body in AC-014. |
| AC-011 | `agent_type` validated against `{"code_reviewer","document_reviewer"}`; anything else → **400** with a clear message. | Critical | `agent_type="foo"` → 400; body explains allowed values. |
| AC-012 | `content` required and non-empty (after strip) and ≤ `MAX_CONTENT_CHARS` (default 100_000, env-overridable) → else **400**. | Critical | Empty content → 400; oversize content → 400 with size message. |
| AC-013 | Route resolves the executor via `orchestrator._agent_executor`; if missing → **503** (mirror `prompts.py::_get_executor`). If `app.state.agent_mode == "mock"`, return a **200 result explicitly flagged `"mock": true`** with a labelled placeholder report — never present a mock stub as a real review. | High | With no LLM client and `ALLOW_MOCK_MODE=true`, POST returns `mock: true` and a clearly-labelled body. |
| AC-014 | On success, build the review prompt (context fields + focus areas + content), call `executor.single_agent_call(agent_type, prompt, max_tokens=COUNCIL_MAX_TOKENS, label="council:<agent_type>")`, persist via `save_document`, and return `{council_id, agent_type, review_report, created_at, mock}`. | Critical | Response contains a non-empty `review_report` and a `council_id` that resolves via AC-016. |
| AC-015 | `GET /api/v1/council` returns past Council reviews (both types), newest-first, each `{council_id, agent_type, title, preview, focus_areas, created_at}`; supports `?agent_type=` filter and a `limit` (default 20). | Critical | Returns only `doc_type ∈ {council_code_review, council_doc_review}` rows; respects filter + limit. |
| AC-016 | `GET /api/v1/council/{council_id}` returns the full review `{council_id, agent_type, review_report, focus_areas, created_at}`; **404** if not found. | High | Known id → full report; unknown id → 404. |
| AC-017 | `DELETE /api/v1/council/{council_id}` deletes a review; gated `require_role("developer","admin")` (mirrors `documents.py`); **404** if missing, **403** for viewer. | Medium | viewer → 403; developer → 204; unknown id → 404. |
| AC-018 | The submitted **content is not persisted**; only the generated report is stored (see §8 security rationale). | High | The stored `Document.content` is the report, not the pasted source. |
| AC-019 | Token usage + cost are recorded for every real (non-mock) review via the `single_agent_call` path (no extra work — inherited). | High | A real review creates a `token_usage` row attributable to the reviewer agent; visible on Cost Dashboard. |

### 4.3 Frontend — Agent Council page

| ID | Requirement | Priority | Acceptance Criteria |
|----|-------------|----------|---------------------|
| AC-030 | New route `/council` in `App.tsx` inside `RequireAuth`/`Layout`, rendering `AgentCouncilPage`. | Critical | Navigating to `/council` while authed renders the page; while unauthed redirects to `/login`. |
| AC-031 | Sidebar nav item `{ path: "/council", label: "Agent Council", icon: Gavel }` (not adminOnly), `Gavel` imported from `lucide-react`. | Critical | All authenticated roles see the nav item; it highlights when active. |
| AC-032 | Reviewer selector: two options — "Code Quality Reviewer" (`Code2` icon → `agent_type=code_reviewer`) and "Document Quality Reviewer" (`FileText` icon → `agent_type=document_reviewer`). | Critical | Toggling changes `agent_type` and the context fields (AC-034). |
| AC-033 | Content input offers **two modes** via a toggle — **Paste** (large monospace `<textarea>`, min-height 400px) and **Upload** (file picker / drag-drop zone). The two modes are mutually exclusive; switching clears the other. | Critical | Renders monospace textarea in Paste mode; renders a file picker accepting the allow-list in Upload mode. |
| AC-034 | Context fields switch on reviewer: **code** → Language (TypeScript, Python, Go, Java, Rust, C#, Other); **document** → Document type (PRD, Spec, Proposal, Guide, RFC, Other). | Medium | Code reviewer shows Language only; doc reviewer shows Document type only. |
| AC-035 | Focus-area checkboxes: Security, Performance, Readability, Correctness (default: all checked). | Medium | Selections are sent in `focus_areas`. |
| AC-036 | "Submit for Review" button: disabled when there is no input (empty textarea **in Paste mode**, or no file chosen **in Upload mode**) or a request is in flight; routes to `POST /council` (paste) or `POST /council/upload` (multipart, via `api.postForm`); shows a spinner + "Reviewing…" during the call. | Critical | Empty input → button disabled. In-flight → spinner, button disabled. Upload mode sends multipart. |
| AC-042 | Upload mode UI: show the chosen filename + size; show the accepted-types hint (PDF, DOCX, XLSX, MD, TXT, code); client-side pre-check rejects an extension not in the allow-list and a file over the size cap with an inline message (server still enforces — AC-062/AC-063). | High | Picking a `.exe` → inline "unsupported type" without a network call; oversize file → inline size error. |
| AC-037 | Result panel renders `review_report` via `MarkdownRenderer` below the form; a `mock: true` result shows a visible "MOCK — not a real review" banner. | Critical | Verdict/headings render as Markdown; mock results are unmistakably labelled. |
| AC-038 | History section lists past reviews (`GET /council`) with reviewer icon, title/preview, focus, relative timestamp, newest-first; empty state when none. | High | After a submit, the new review appears at the top of history without a full reload. |
| AC-039 | Clicking a history item expands the full report (`GET /council/{id}`) rendered as Markdown. | High | Expanding shows the complete stored report. |
| AC-040 | Network/timeout/validation errors surface a non-blocking error message (not a raw stack/500). | High | A 400/503 returns a readable inline error; the page stays usable. |
| AC-041 | All UI uses `var(--*)` theme tokens; the page renders correctly across the theme set (parity with other pages). | Medium | Spot-check 3+ themes incl. light/dark; no hard-coded colors. |

### 4.4 Cross-cutting

| ID | Requirement | Priority | Acceptance Criteria |
|----|-------------|----------|---------------------|
| AC-050 | After backend edits, `docker compose restart backend`; after frontend edits, `docker compose restart frontend`, then confirm `(healthy)` before testing (CLAUDE.md mandate). | High | Verified in task steps. |
| AC-051 | Regenerate `frontend/src/api/schema.d.ts` via `npm run generate-types` after the new endpoints are live. | Medium | Schema includes the `/council` paths. |
| AC-052 | Backend tests for council route (happy path mock, agent_type validation, empty/oversize content, agent-not-found, persistence round-trip) + a frontend smoke test; keep coverage ≥ 80% gate. | High | `pytest tests/test_council.py` passes; coverage gate not regressed. |

### 4.5 Backend — Document upload (new in v2.1)

New endpoint `POST /api/v1/council/upload` (multipart). It shares the prompt-build, `single_agent_call`, persistence, and mock-mode behavior of `POST /api/v1/council` — only the **input acquisition** differs (extract-from-file vs. read-from-JSON).

| ID | Requirement | Priority | Acceptance Criteria |
|----|-------------|----------|---------------------|
| AC-060 | `POST /api/v1/council/upload` accepts multipart: `file: UploadFile`, plus `agent_type`, `language?`, `document_type?`, `focus_areas?` as `Form` fields (mirrors `knowledge.py::upload_document`). Requires auth (`get_current_user`). | Critical | Unauthn → 401; missing `file` → 422 (FastAPI). |
| AC-061 | Read bytes; if `len(data) > COUNCIL_MAX_UPLOAD_BYTES` (default `25*1024*1024`, env-overridable) → **413** with a size message. Missing/empty filename → **400**. | High | 26 MB file → 413; no filename → 400. |
| AC-062 | Extract text via `src/knowledge/loaders.py::load_text(filename, data)`. `UnsupportedFileTypeError` → **415** (message lists `SUPPORTED_EXTENSIONS`); `LoaderUnavailableError` → **503** (optional parser lib missing). | Critical | `.exe` → 415; a `.pdf` when PyMuPDF is absent → 503 (not 500). |
| AC-063 | Treat the **extracted text** as `content`: if empty after extraction (e.g. scanned image-only PDF with no text layer) → **400** "no extractable text found"; if `len(text) > MAX_CONTENT_CHARS` → **400** (same cap as paste). | Critical | Image-only PDF → 400 with the no-text message; a 200k-char extraction → 400 oversize. |
| AC-064 | After successful extraction, the request follows the **identical** path as `POST /council`: build review prompt → `executor.single_agent_call(...)` → `save_document(...)` → return `{council_id, agent_type, review_report, created_at, mock, source_filename}`. Mock mode flagged exactly as AC-013. | Critical | Same response shape as paste, plus `source_filename`. |
| AC-065 | The uploaded **file bytes and the extracted text are never persisted** — only the report (`Document.content`). `source_filename` is stored in `Document.tags` for display. | High | DB row holds the report; no file on disk; filename present in tags. |
| AC-066 | Token/cost recording is inherited from `single_agent_call` (no extra work), identical to the paste path. | High | A real upload review creates a `token_usage` row on the Cost Dashboard. |

---

## 5. Agent Output Contracts (System Prompts)

### 5.1 Code Quality Reviewer (reuse existing)

`config/agents/code_reviewer.yaml` already defines a strict format (Summary + Verdict line, Files Reviewed table, Findings by File with `[CRITICAL]/[WARNING]` + fixes, Compilation Check, Requirements Traceability, Quality Scores, decisive re-review rules). The Council reuses it as-is. The route prepends a thin ad-hoc framing so the agent reviews **only the pasted content** (it has no tools to fetch anything else):

```
You are performing an AD-HOC, one-shot code review. You have NO tools and NO
repository access — review ONLY the code pasted below. Do not assume files you
cannot see. If context is missing, state the assumption explicitly.

Language/framework: {language}
Focus areas: {focus_areas}

--- BEGIN CODE ---
{content}
--- END CODE ---
```

### 5.2 Document Quality Reviewer (new — full system prompt)

Because `single_call` gives this non-code agent no tools and no lessons/KB grounding, the YAML `system_prompt` is the entire contract. It MUST encode the dimensions, severity model, and a verdict line. Target prompt (final wording lands in `config/agents/document_reviewer.yaml`):

```
You are the Document Quality Reviewer for the Agent Team platform — a senior
technical reviewer who evaluates PRDs, specs, proposals, RFCs, and guides for
completeness, clarity, testability, and internal consistency.

You have NO tools and review ONLY the document text provided. Do not invent
facts about systems you cannot see; where the document omits something needed,
flag the omission rather than guessing.

REVIEW DIMENSIONS:
- Problem & goal clarity (is the problem, user, and measurable outcome stated?)
- Completeness (all expected sections present for the stated document type?)
- Requirements quality (atomic, uniquely identifiable, and TESTABLE; each has
  observable acceptance criteria?)
- Scope boundaries (in-scope AND out-of-scope both stated?)
- Non-functional requirements (perf/SLA, scale, security/authz, availability,
  observability, data retention/privacy — the most commonly missing section)
- Edge cases, error states, empty/zero/failure paths
- Internal consistency (contradictions, ambiguity)
- Dependencies & assumptions
- Traceability hooks (IDs a plan/code review can trace back to)

SEVERITY MODEL (do not inflate):
- [CRITICAL] missing/contradictory content that makes the doc unusable or unsafe
  to build from (e.g. no acceptance criteria, undefined data contract).
- [HIGH] serious gap or ambiguity that should be fixed before build.
- [MEDIUM] real improvement worth making; non-blocking.
- [NIT] cosmetic/preference.

OUTPUT FORMAT — follow exactly:

## Document Review Report
### Summary
[1-2 sentence assessment] | **Verdict: APPROVED / APPROVED WITH NITS / CHANGES REQUESTED**
### Findings
Grouped by severity. Each finding: [SEVERITY] section/location — problem -> Fix: <specific action>.
### Completeness Checklist
| Expected section | Present? | Notes |
### Requirements Testability
Sample of requirements with: testable? has acceptance criteria? -> fix if not.
### Verdict
**APPROVED / APPROVED WITH NITS / CHANGES REQUESTED** — one-line reason.

RULES:
- Lead with the verdict; justify after.
- Every finding cites a specific location and gives a concrete fix. "This is
  unclear" is not acceptable; name what is unclear and how to fix it.
- Tables and bullets over prose. Be concise. Do not ask questions — review what
  is provided and state assumptions where the document is silent.
```

---

## 6. Data & API Contract

### 6.1 `POST /api/v1/council` — request

```jsonc
{
  "agent_type": "code_reviewer | document_reviewer",   // required, validated
  "content": "string",                                  // required, 1..MAX_CONTENT_CHARS
  "language": "TypeScript",                             // optional (code reviews)
  "document_type": "PRD",                               // optional (doc reviews)
  "focus_areas": ["Security", "Correctness"]            // optional; default = all
}
```

### 6.2 `POST /api/v1/council` — response (envelope `data`)

```jsonc
{
  "council_id": "doc-ab12cd34",   // = persisted Document.document_id
  "agent_type": "document_reviewer",
  "review_report": "## Document Review Report ...",
  "focus_areas": ["Security", "Correctness"],
  "created_at": "2026-06-26T12:00:00Z",
  "mock": false                    // true when agent_mode == "mock"
}
```

### 6.2b `POST /api/v1/council/upload` — request (multipart/form-data) — new in v2.1

```
file:          <binary>          // required; .pdf .docx .xlsx .md .txt or a source-code ext
agent_type:    code_reviewer | document_reviewer   // Form field, required
language:      TypeScript         // Form field, optional (code reviews)
document_type: PRD                // Form field, optional (doc reviews)
focus_areas:   ["Security"]       // Form field, optional JSON-array string (default = all)
```

Response: same shape as §6.2 **plus** `"source_filename": "spec.pdf"`. Errors: 413 (too large), 415 (unsupported type), 503 (parser lib unavailable), 400 (no extractable text / extracted text over `MAX_CONTENT_CHARS`).

### 6.3 Persistence mapping (reuse `Document`)

| `Document` field | Council value |
|------------------|---------------|
| `document_id` | generated `doc-<hex>` → exposed as `council_id` |
| `request_id` | `""` (no Request; accepted sentinel) |
| `doc_type` | `council_code_review` or `council_doc_review` |
| `title` | `"Code Review — {language}"` / `"Document Review — {document_type}"` + short timestamp |
| `content` | the **review report** (NOT the submitted source) |
| `agent_id` | `code_reviewer` / `document_reviewer` |
| `tags` | `["council", agent_type, language|document_type, *focus_areas]` + `source_filename` when the review came from an upload |
| `created_at` | now |

> Upload and paste persist to the **same** `doc_type`s (`council_code_review` / `council_doc_review`); the only difference is that an uploaded review carries the original filename in `tags`. History/detail/delete are mode-agnostic.

### 6.4 Constants

- `COUNCIL_MAX_TOKENS` — output cap for the report (default **8192**, env `COUNCIL_MAX_TOKENS`). Stays on the non-streaming Messages path (`_STREAMING_MAX_TOKENS_THRESHOLD` is 16_000 in `base.py`), so a report renders fast without the 15-minute streaming ceiling.
- `MAX_CONTENT_CHARS` — input cap (default **100_000**, env `COUNCIL_MAX_CONTENT_CHARS`). Applies to pasted content **and** to extracted-from-file text.
- `COUNCIL_MAX_UPLOAD_BYTES` — uploaded-file size cap (default **26_214_400** = 25 MB, env `COUNCIL_MAX_UPLOAD_BYTES`); mirrors the KB upload cap.

---

## 7. Known Limitations (v1) — stated explicitly

1. **No tools / no repository access.** `single_call()` runs no tool-use loop. The Code Quality Reviewer cannot `file_read` referenced files, run `web_search`/CVE lookups, or invoke static analysis — it reviews **only the pasted text**. The framing prompt instructs it to say so. A tool-augmented "deep review" is a future enhancement (would require routing through `process_task` with a scoped tool grant, or a dedicated council workflow).
2. **No KB grounding.** Unlike the workflow path, Council reviews do not pull prior reviews or app conventions from the Knowledge Base. The doc reviewer is a non-code agent and gets no lessons-learned injection either.
3. **Submitted content not persisted** (history shows the report only). Deliberate — see §8. Uploaded files are extracted in-memory and discarded; neither the bytes nor the extracted text are stored.
4. **No re-run from history** in v1 (we don't store the input). A "re-review" requires re-pasting or re-uploading.
5. **Single model, single call.** No multi-pass or self-critique.
6. **Upload is text-extraction only — no OCR.** A scanned or image-only PDF (no text layer) yields no text and is rejected with a 400 (AC-063). DOCX/XLSX extraction is text/paragraph/cell-level; embedded images, complex tables, and tracked-changes markup are not reconstructed. One file per submission.

---

## 8. Security & Privacy

- **AuthN/AuthZ:** all endpoints require `get_current_user`. Delete is gated to `developer`/`admin` (mirrors `documents.py`); viewers are read-only.
- **Input bounds:** `MAX_CONTENT_CHARS` caps text size and `COUNCIL_MAX_UPLOAD_BYTES` caps upload size (413 before extraction) to prevent oversized-payload abuse; the global `BodySizeLimitMiddleware` currently only guards `/auth/*`, so the route enforces its own caps.
- **Upload type allow-list:** only `SUPPORTED_EXTENSIONS` are accepted (415 otherwise); the server is the source of truth even though the client pre-checks. Bytes are parsed in-memory and never written to disk, so there is no upload-directory traversal or stored-file surface. Parsing runs in the backend process — acceptable for v1 given trusted, authenticated users; a future hardening step could sandbox parsing.
- **No content/file persistence (privacy upside):** pasted code/docs or uploaded files may contain secrets or sensitive IP. v1 stores **only the generated report**, not the submission and not the file — minimizing the blast radius of a DB leak and avoiding a new exfiltration surface. (If product later wants re-run, store the input encrypted-at-rest in a dedicated table with retention, not in `documents`.)
- **No secret echo:** the report is model output; the reviewer prompt does not instruct the model to reproduce the full input verbatim.
- **Mock safety:** a mock-mode result is explicitly flagged so a fake "review" can never be mistaken for a real one (staging/prod refuse mock at boot regardless).

---

## 9. UI Mockups

### 9.1 Empty state

```
+------------------------------------------------------------------+
|  [gavel]  Agent Council                                          |
|  Ad-hoc, one-shot reviews -- outside the workflow pipeline.      |
|                                                                  |
|  Select reviewer                                                 |
|  [ </>  Code Quality Reviewer ]  [ doc  Document Quality Reviewer ]|
|                                                                  |
|  Input:  ( o ) Paste     ( ) Upload file                         |
|  +-------------------------------------------------------------+ |
|  | (monospace textarea, min 400px)                             | |
|  +-------------------------------------------------------------+ |
|  ...or in Upload mode:                                           |
|  +-------------------------------------------------------------+ |
|  |  [ Choose file ]  drag & drop                               | |
|  |  Accepted: PDF, DOCX, XLSX, MD, TXT, code   (<= 25 MB)       | |
|  |  chosen: spec.pdf (412 KB)                       [ x ]      | |
|  +-------------------------------------------------------------+ |
|  Language: [TypeScript v]  Focus: [x]Security [x]Perf [x]Read   |
|                                              [ Submit for Review ]|
|                                                                  |
|  --- Past Reviews --------------------------------------------- |
|  No reviews yet. Submit your first code or document above.       |
+------------------------------------------------------------------+
```

### 9.2 With result + history

```
+------------------------------------------------------------------+
|  [form, collapsible after submit]                                |
|  -- Result ----------------------------------------------------- |
|  ## Document Review Report                                       |
|  ### Summary  ...  | Verdict: CHANGES REQUESTED                  |
|  ### Findings  [CRITICAL] sec.4.2 ...  -> Fix: ...              |
|                                                                  |
|  --- Past Reviews --------------------------------------------- |
|  doc  Document Review - PRD - 2m ago - Focus: Completeness [open]|
|  </>  Code Review - TypeScript - 1h ago - Focus: All      [open]|
+------------------------------------------------------------------+
```

---

## 10. Success Metrics

- A user navigates to `/council`, pastes code, and receives a structured report (real LLM) typically in well under a minute.
- A user pastes a PRD and receives a gap analysis with a verdict and specific, located, actionable fixes.
- History survives a `docker compose restart backend` (persistence proof).
- Council spend is attributable on the Cost Dashboard.
- The page is visually native across the theme set.

---

## 11. Open Questions

1. Should Council history be **per-user** or shared across the workspace? v1 = shared (documents are workspace-scoped today). Flag if per-user isolation is required.
2. Do we want Council reviews to appear in the existing **Documents** list, or be filtered out of it? v1 they will appear (same table); add a `doc_type` exclusion in the Documents route if that's noisy.
3. Future third reviewer (Architecture? Security?) — confirm the next one so the selector/labels are forward-compatible.
