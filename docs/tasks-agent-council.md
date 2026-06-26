# Implementation Task List
# Agent Council — Ad-Hoc AI Review Panel (AC)

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 2.1 |
| Created Date | 2026-06-26 |
| Last Updated | 2026-06-26 |
| v2.1 change | Added **document/file upload** tasks: AC-15 (backend `POST /council/upload`), AC-25 (frontend Paste/Upload toggle), AC-34 (upload tests). Reuses `src/knowledge/loaders.py::load_text` — no new deps. |
| Status | **Draft — ready for implementation** |
| Product Owner | Chandramouli |
| Task Prefix | `AC` |
| Source PRD | [docs/prd-agent-council.md](prd-agent-council.md) |

---

## How to Use This Document

- Each task has a unique ID: `AC-<NN>`.
- Tasks are grouped into 4 phases (P0 → P1 → P2 → P3).
- A task cannot start until all listed dependencies are done.
- Effort: **S** = hours, **M** = 1–2 days, **L** = 3–5 days.
- **Traces PRD**: the PRD requirement(s) the task implements.
- Every task that produces code follows TDD where practical and ends with the
  **mandatory restart-and-verify** step (CLAUDE.md: never trust uvicorn reload /
  Vite HMR in this repo).

### Status Legend

| Status | Meaning |
|--------|---------|
| `[ ]` | Not started |
| `[~]` | In progress |
| `[x]` | Completed |
| `[!]` | Blocked |
| `[-]` | Skipped / Deferred |

---

## Current State of the Repo (verified, read before starting)

Two changes are **already present, uncommitted** in the working tree (`git diff`):

- `src/agents/implementations.py` — `DocumentReviewerAgent` class added (AC-01 done).
- `src/agents/factory.py` — `"document_reviewer": DocumentReviewerAgent` added to `AGENT_CLASS_MAP` (AC-02 done).

> **CRITICAL — why the doc reviewer is currently dead code:** `ConfigLoader._load_agents()`
> (`src/config/loader.py:38`) builds the agent set by globbing `config/agents/*.yaml`.
> `AgentFactory.create_all()` only instantiates agents in that set. There is **no
> `config/agents/document_reviewer.yaml`**, so `document_reviewer` is never created
> or registered, and `executor.single_agent_call("document_reviewer", …)` returns
> `{"error": "agent_not_found"}` with an empty system prompt. **AC-03 (the YAML) is
> the task that makes the agent real** — the class-map entry alone does nothing.

The Code Quality Reviewer path works today: `code_reviewer` has a full YAML.

---

## Progress Summary

| Phase | Theme | Total | Done | In Progress | Not Started |
|-------|-------|-------|------|-------------|-------------|
| P0 | Backend Agent + Config | 4 | 2 | 0 | 2 |
| P1 | Council API + Persistence | 6 | 0 | 0 | 6 |
| P2 | Frontend | 6 | 0 | 0 | 6 |
| P3 | Tests, Polish & Integration | 5 | 0 | 0 | 5 |
| **Total** | | **21** | **2** | **0** | **19** |

---

## Phase P0 — Backend Agent + Config

Goal: `document_reviewer` is a real, registered agent with a complete system prompt; `code_reviewer` reused for code reviews.

| ID | Task | Effort | Depends On | Traces PRD | Status |
|----|------|--------|-----------|-----------|--------|
| AC-01 | `DocumentReviewerAgent` class in `src/agents/implementations.py` (extends `BaseAgent`, `_parse_output` → `{"review_report": text}`, `_extract_artifacts` → `[]`). | S | — | AC-001 | `[x]` |
| AC-02 | Register `"document_reviewer": DocumentReviewerAgent` in `AGENT_CLASS_MAP` + import (`src/agents/factory.py`). | S | AC-01 | AC-002 | `[x]` |
| **AC-03** | **Create `config/agents/document_reviewer.yaml`** — the missing piece. See AC-03 detail below. | **M** | AC-02 | **AC-003, AC-005** | `[ ]` |
| AC-04 | Verify `code_reviewer` is reused for code reviews (no new agent). Confirm `single_agent_call("code_reviewer", …)` returns a structured report. | S | — | AC-004 | `[ ]` |

### AC-03 detail — `config/agents/document_reviewer.yaml`

**Files:** Create `config/agents/document_reviewer.yaml`.

**Step 1 — write the YAML.** Mirror the *shape* of `code_reviewer.yaml`, but **omit `tools:` and `retrieval:`** (the Council uses `single_call`, which runs no tool loop and applies no KB grounding). Keep `model` identical to `code_reviewer.yaml` (`claude-opus-4-8`). Paste the full Document Quality Reviewer system prompt from PRD §5.2.

```yaml
agent_id: document_reviewer
display_name: "Document Quality Reviewer"
role: "Document Quality Reviewer"
team: planning
reports_to: business_analyst

model: claude-opus-4-8

# Non-code agent used ONLY via single_call() from the Agent Council. No tool
# loop, so no `tools:` and no `retrieval:` block — the system prompt is the
# entire quality contract.

system_prompt: |
  <PASTE the full prompt from PRD §5.2 verbatim>

responsibilities:
  - id: DR-001
    description: "Review documents (PRD/spec/proposal/RFC/guide) for completeness, clarity, testability, and consistency"
    category: review
  - id: DR-002
    description: "Produce a structured report with a verdict line and severity-tagged, located, actionable findings"
    category: review

outputs:
  - name: "Document Review Report"
    format: markdown

metadata:
  created: "2026-06-26"
  version: "1.0"
```

> Note: `business_analyst` and `planning` are confirmed valid (`config/agents/business_analyst.yaml`, `config/teams.yaml`). `reports_to`/`team` don't affect single_call execution but keep the YAML schema-consistent with siblings.

**Step 2 — restart + verify the agent is registered:**

```bash
docker compose restart backend && sleep 6 && docker ps   # confirm (healthy)
docker compose exec backend python -c "from src.config.loader import ConfigLoader; c=ConfigLoader(); c.load_all(); print('document_reviewer' in c.agents, bool(c.agents['document_reviewer']['system_prompt']))"
# Expected: True True
```

Expected: `True True` (agent loaded, system prompt non-empty). If the first is `False`, the YAML filename or `agent_id` is wrong; if the second is `False`, the prompt didn't paste.

---

## Phase P1 — Council API + Persistence

Goal: `POST/GET/DELETE /api/v1/council` live, going through `executor.single_agent_call`, persisting via the existing `Document` store.

| ID | Task | Effort | Depends On | Traces PRD | Status |
|----|------|--------|-----------|-----------|--------|
| AC-10 | Create `src/api/routes/council.py` skeleton: `APIRouter(prefix="/api/v1/council")`, pydantic `CouncilRequest` model, `_envelope()` helper, `_get_executor(request)` (copy the 503 pattern from `prompts.py`). Define constants `ALLOWED_AGENT_TYPES`, `MAX_CONTENT_CHARS` (env `COUNCIL_MAX_CONTENT_CHARS`, default 100_000), `COUNCIL_MAX_TOKENS` (env, default 8192), `DOC_TYPE_BY_AGENT`. | M | AC-03 | AC-010 | `[ ]` |
| AC-11 | `POST /api/v1/council` — auth + validation: `get_current_user`; `agent_type ∈ ALLOWED_AGENT_TYPES` else 400; `content` stripped non-empty and ≤ MAX_CONTENT_CHARS else 400. Build the review prompt (framing from PRD §5.1/§5.2 + context + focus + content). | M | AC-10 | AC-010, AC-011, AC-012 | `[ ]` |
| AC-12 | `POST` execution: resolve executor (503 if none). If `request.app.state.agent_mode == "mock"` → return `{… "mock": true, "review_report": "<labelled placeholder>"}` WITHOUT persisting. Else `await executor.single_agent_call(agent_type, prompt, max_tokens=COUNCIL_MAX_TOKENS, label=f"council:{agent_type}")`; read `result["text"]`; if `result.get("error")` → 502 with a clear message. | M | AC-11 | AC-013, AC-014, AC-019 | `[ ]` |
| AC-13 | `POST` persistence: build a `Document` (`document_id=f"doc-{uuid4().hex[:12]}"`, `request_id=""`, `doc_type=DOC_TYPE_BY_AGENT[agent_type]`, `title`, `content=review_report`, `agent_id=agent_type`, `tags=["council", agent_type, language|document_type, *focus_areas]`); `await state.save_document(doc)`. Return `{council_id, agent_type, review_report, focus_areas, created_at, mock:false}`. **Persist report only, never the submitted content (AC-018).** | M | AC-12 | AC-014, AC-018 | `[ ]` |
| AC-14 | `GET /api/v1/council` (list, newest-first, `?agent_type=`, `limit=20`) + `GET /api/v1/council/{id}` (full, 404 if missing) + `DELETE /api/v1/council/{id}` (`require_role("developer","admin")`, 404 if missing). List/detail map `Document` → council shape; recover `agent_type`/`focus_areas` from `doc_type`/`tags`. Register the router in `src/main.py` (import `council`, `app.include_router(council.router)`). | M | AC-13 | AC-015, AC-016, AC-017 | `[ ]` |
| AC-15 | **`POST /api/v1/council/upload`** (multipart) — see AC-15 detail below. Accepts `file: UploadFile` + `agent_type`/`language`/`document_type`/`focus_areas` as `Form` fields; size-caps (413); extracts text via `load_text` (415/503); applies empty/oversize checks on extracted text (400); then calls a **shared `_run_review(...)` helper** (refactored out of AC-11..AC-13) so paste and upload share prompt-build → execute → persist. Returns the §6.2 body + `source_filename`. | M | AC-13 | AC-060..AC-066 | `[ ]` |

### Implementation notes for P1 (grounded in real code)

- **Executor handle:** `request.app.state.orchestrator._agent_executor` (same as `prompts.py::_get_executor`). 503 when falsy.
- **Mock detection:** `request.app.state.agent_mode` is set in `main.py` lifespan to `"real_llm"` or `"mock"`. Prefer this over inspecting the client.
- **`single_agent_call` return shape:** `{text, input_tokens, output_tokens, model[, stop_reason][, error]}` (`executor.py:686`, `base.py:285`). The report is in `text`. Cost/token recording is automatic inside `single_agent_call` — do **not** re-record.
- **Listing by type:** `StateStore.search_documents(query="", doc_type=..., limit=...)` exists (`sqlite_store.py:2019`); call it once per council `doc_type` and merge+sort by `created_at` desc when no `agent_type` filter is given (there are only two types).
- **Envelope:** return `{"data": …, "meta": …, "error": None}` to match the dashboard convention.
- **No new SQLite table / no migration** — reuse `documents`.
- **Refactor for reuse (AC-15):** factor the prompt-build → `single_agent_call` → mock-check → `save_document` body into a private `async def _run_review(request, agent_type, content, *, language, document_type, focus_areas, source_filename=None) -> dict`. Both `POST /council` (AC-11..13) and `POST /council/upload` (AC-15) call it. Do this refactor as part of AC-15 so there is exactly one execution path.

### AC-15 detail — `POST /api/v1/council/upload`

**Pattern source:** mirror `src/api/routes/knowledge.py::upload_document` (lines ~192-267) — it is the working precedent for `UploadFile` + `Form` + size cap + `load_text` error mapping.

```python
from fastapi import File, Form, UploadFile
from src.knowledge.loaders import (
    load_text, UnsupportedFileTypeError, LoaderUnavailableError,
)

COUNCIL_MAX_UPLOAD_BYTES = int(os.getenv("COUNCIL_MAX_UPLOAD_BYTES", 25 * 1024 * 1024))

@router.post("/upload")
async def council_upload(
    request: Request,
    file: UploadFile = File(...),
    agent_type: str = Form(...),
    language: str | None = Form(None),
    document_type: str | None = Form(None),
    focus_areas: str = Form("[]"),          # JSON-array string, like knowledge.py
    user: dict = Depends(get_current_user),
):
    if agent_type not in ALLOWED_AGENT_TYPES:
        raise HTTPException(400, "invalid agent_type ...")
    if not file.filename:
        raise HTTPException(400, "No filename provided.")
    data = await file.read()
    if len(data) > COUNCIL_MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds {COUNCIL_MAX_UPLOAD_BYTES // (1024*1024)} MB limit.")
    try:
        text, _src_type = load_text(file.filename, data)
    except UnsupportedFileTypeError as e:
        raise HTTPException(415, str(e)) from e
    except LoaderUnavailableError as e:
        raise HTTPException(503, str(e)) from e
    if not text.strip():
        raise HTTPException(400, "No extractable text found (scanned/image-only file?).")
    if len(text) > MAX_CONTENT_CHARS:
        raise HTTPException(400, f"Extracted text exceeds {MAX_CONTENT_CHARS} chars.")
    focus = _parse_focus(focus_areas)               # same helper used by paste path
    return await _run_review(
        request, agent_type, text,
        language=language, document_type=document_type,
        focus_areas=focus, source_filename=file.filename,
    )
```

**Verify after restart:**

```bash
docker compose restart backend && sleep 6 && docker ps   # (healthy)
# Put a dev JWT in $AUTH so the literal header never breaks quoting:
AUTH="Authorization: Bearer <DEV_JWT>"
printf 'def add(a,b): return a-b\n' > /tmp/bug.py

# happy path -> JSON with review_report + source_filename=bug.py (or mock:true in mock mode)
curl -s -H "$AUTH" -F file=@/tmp/bug.py -F agent_type=code_reviewer \
     http://localhost:8000/api/v1/council/upload | head -c 400

# status-only check -> 200
curl -s -o /dev/null -w "%{http_code}\n" -H "$AUTH" \
     -F file=@/tmp/bug.py -F agent_type=code_reviewer \
     http://localhost:8000/api/v1/council/upload

# unsupported type -> 415
printf 'x' > /tmp/x.exe
curl -s -o /dev/null -w "%{http_code}\n" -H "$AUTH" \
     -F file=@/tmp/x.exe -F agent_type=code_reviewer \
     http://localhost:8000/api/v1/council/upload
```

---

## Phase P2 — Frontend

Goal: `/council` page navigable from the sidebar, functional submit, result + history.

| ID | Task | Effort | Depends On | Traces PRD | Status |
|----|------|--------|-----------|-----------|--------|
| AC-20 | Create `frontend/src/pages/AgentCouncil.tsx`: page shell using `var(--*)` tokens; export `AgentCouncilPage`. Reviewer selector (two buttons: Code `Code2`/`code_reviewer`, Document `FileText`/`document_reviewer`). State: `agentType`, `content`, `language`, `documentType`, `focusAreas`, `loading`, `result`, `error`. | M | AC-14 | AC-032 | `[ ]` |
| AC-21 | Form body: monospace `<textarea>` (min-height 400px); context field switches on reviewer (Language vs Document type); focus-area checkboxes (default all). "Submit for Review" disabled when `!content.trim() || loading`; on submit `api.post("/council", body)` then render result. | M | AC-20 | AC-033, AC-034, AC-035, AC-036 | `[ ]` |
| AC-22 | Result panel: render `data.review_report` via `MarkdownRenderer`; spinner + "Reviewing…" while loading; if `data.mock` show a prominent "MOCK — not a real review" banner; errors render inline (read message from thrown `Error`). | M | AC-21 | AC-037, AC-040 | `[ ]` |
| AC-23 | History panel: on mount + after each submit, `api.get("/council")`; list newest-first with reviewer icon, title/preview, focus, relative time; empty state when none; click → `api.get("/council/{id}")` → expand full report via `MarkdownRenderer`. | M | AC-21 | AC-038, AC-039 | `[ ]` |
| AC-24 | Wire navigation: add `<Route path="/council" element={<AgentCouncilPage />} />` in `frontend/src/App.tsx` (inside the authenticated `<Routes>`), import the page; add `{ path: "/council", label: "Agent Council", icon: Gavel }` to `navItems` in `Sidebar.tsx` and import `Gavel` from `lucide-react` (NOT adminOnly). | S | AC-20 | AC-030, AC-031 | `[ ]` |
| AC-25 | **Paste/Upload toggle** in `AgentCouncil.tsx`: add `inputMode: "paste" | "upload"` state + a radio/segmented toggle. **Paste** shows the textarea (AC-21). **Upload** shows a file `<input type="file" accept=".pdf,.docx,.xlsx,.md,.txt,.py,.ts,.tsx,.js,.go,.java,.rs,.cs,.json,.yaml,.yml">` + chosen filename/size + clear (`x`). Client pre-check: reject extension outside the allow-list and file > 25 MB with an inline message (AC-042). Submit branches: paste → `api.post("/council", body)`; upload → build `FormData` (`file`, `agent_type`, `language`/`document_type`, `focus_areas` as JSON string) and `api.postForm("/council/upload", fd)`. Switching modes clears the other input. Render `source_filename` in the result header when present. | M | AC-21, AC-22 | AC-033, AC-036, AC-042 | `[ ]` |

### Implementation notes for P2

- **API client:** `import { api } from "../lib/api"` → `api.post`, `api.get`, `api.delete`. Responses are `{data, meta, error}`; read `res.data`.
- **File upload client:** `api.postForm(path, formData)` already exists (`frontend/src/lib/api.ts:51`) — sends `FormData` with the auth header and no JSON content-type. Used by KB upload + Command Center; reuse it verbatim for `/council/upload`.
- **Markdown:** `import { MarkdownRenderer } from "../components/ui/MarkdownRenderer"`.
- **Frontend restart:** after editing `frontend/src/`, `docker compose restart frontend`, wait ~5s, confirm `(healthy)` before testing (HMR is unreliable here).

---

## Phase P3 — Tests, Polish & Integration

Goal: tests pass, coverage gate held, page feels native, edges handled.

| ID | Task | Effort | Depends On | Traces PRD | Status |
|----|------|--------|-----------|-----------|--------|
| AC-30 | Backend tests `tests/test_council.py`: (a) invalid `agent_type` → 400; (b) empty content → 400; (c) oversize content → 400; (d) mock mode → 200 with `mock:true`, nothing persisted; (e) happy path (monkeypatch executor's `single_agent_call` to return a canned `{text}`) → 200, report present, `Document` persisted with right `doc_type`/`tags`; (f) list returns only council docs newest-first; (g) detail 404 on unknown id; (h) delete RBAC (viewer 403 / developer 204). | L | AC-14 | AC-052 | `[ ]` |
| AC-34 | Upload tests in `tests/test_council.py`: (a) `.txt`/`.md` upload happy path (mock executor) → 200 + `source_filename` + persisted; (b) unsupported `.exe` → 415; (c) oversize file (> cap, monkeypatch a small `COUNCIL_MAX_UPLOAD_BYTES`) → 413; (d) empty-after-extraction (zero-byte / whitespace file) → 400; (e) `load_text` raising `LoaderUnavailableError` (monkeypatch) → 503; (f) shared-path proof: assert upload and paste produce the same `doc_type`/persistence shape. Use FastAPI `TestClient` multipart (`files={"file": (...)}, data={...}`). | M | AC-15 | AC-060..AC-066 | `[ ]` |
| AC-31 | Frontend smoke test (`frontend/src/tests/AgentCouncil.test.tsx`, vitest): renders, reviewer toggle switches context field, submit disabled on empty content, mock result shows the MOCK banner (mock `api`). | M | AC-22 | AC-052 | `[ ]` |
| AC-32 | Run the full gate: `docker compose exec backend pytest tests/test_council.py -v`, then `docker compose exec backend pytest` (coverage ≥ 80%), `ruff check src tests`, `mypy src`; frontend `npm run lint` + `npm run build`. Fix anything red. | M | AC-30, AC-34, AC-31 | AC-052 | `[ ]` |
| AC-33 | Integration polish + schema regen: regenerate `frontend/src/api/schema.d.ts` via `npm run generate-types` (backend running); end-to-end manual pass for BOTH reviewers AND **both input modes** (paste + upload a real PDF/DOCX), real LLM if creds present else mock-labelled; confirm history survives `docker compose restart backend` (persistence proof); spot-check 3+ themes incl. light/dark. | M | AC-32 | AC-041, AC-050, AC-051 | `[ ]` |

---

## Dependency Graph

```
AC-01 [x] ─ AC-02 [x] ─ AC-03 (YAML — makes doc reviewer real)
                              └─ AC-10 (route skeleton)
                                   └─ AC-11 (validate) ─ AC-12 (execute) ─ AC-13 (persist)
                                                                              ├─ AC-14 (GET/DELETE + main.py wiring)
                                                                              └─ AC-15 (POST /upload — extract via load_text, shared _run_review)
                                                                                   └─ AC-20 (page shell)
                                                                                        ├─ AC-21 (form) ─ AC-22 (result)
                                                                                        │                   ├─ AC-23 (history)
                                                                                        │                   └─ AC-25 (Paste/Upload toggle)
                                                                                        └─ AC-24 (route + sidebar)
AC-04 (verify code_reviewer reuse) ── independent, anytime
AC-14 ── AC-30 (backend tests)
AC-15 ── AC-34 (upload tests)
AC-22 ── AC-31 (frontend test)
AC-30 + AC-34 + AC-31 ── AC-32 (gate) ── AC-33 (integration polish)
```

The critical path's first link is **AC-03** — without the YAML, the entire document-review half is non-functional regardless of route/UI work.

---

## Files Changed

| File | Action | Tasks |
|------|--------|-------|
| `src/agents/implementations.py` | Modify (done) | AC-01 |
| `src/agents/factory.py` | Modify (done) | AC-02 |
| `config/agents/document_reviewer.yaml` | **NEW** | AC-03 |
| `src/api/routes/council.py` | **NEW** | AC-10..AC-15 |
| `src/main.py` | Modify (router import + include) | AC-14 |
| `tests/test_council.py` | **NEW** | AC-30, AC-34 |
| `frontend/src/pages/AgentCouncil.tsx` | **NEW** | AC-20..AC-23, AC-25 |
| `frontend/src/App.tsx` | Modify (route) | AC-24 |
| `frontend/src/components/layout/Sidebar.tsx` | Modify (nav item) | AC-24 |
| `frontend/src/tests/AgentCouncil.test.tsx` | **NEW** | AC-31 |
| `frontend/src/api/schema.d.ts` | Regenerate | AC-33 |

---

## Completion Checklist (acceptance proof)

Backend:
- [ ] `config/agents/document_reviewer.yaml` loads; `document_reviewer` registered with a non-empty system prompt (AC-03 verify command returns `True True`)
- [ ] `POST /api/v1/council` `agent_type=code_reviewer` → review report
- [ ] `POST /api/v1/council` `agent_type=document_reviewer` → review report
- [ ] Invalid `agent_type` → 400
- [ ] Empty content → 400; oversize content → 400
- [ ] Mock mode → 200 `mock:true`, nothing persisted
- [ ] `GET /api/v1/council` → council reviews only, newest-first
- [ ] `GET /api/v1/council/{id}` → full report; unknown id → 404
- [ ] `DELETE /api/v1/council/{id}` → viewer 403, developer 204
- [ ] A real review records a `token_usage` row (cost visible on Cost Dashboard)
- [ ] Stored `Document.content` is the report, NOT the submitted source
- [ ] `POST /api/v1/council/upload` with a `.txt`/`.md`/`.pdf` → review report + `source_filename`
- [ ] Upload: unsupported type → 415; oversize file → 413; no extractable text → 400; parser lib missing → 503
- [ ] Uploaded file bytes/extracted text NOT persisted (only the report)

Frontend:
- [ ] `/council` loads from the sidebar for all authenticated roles
- [ ] Reviewer toggle switches context fields
- [ ] Paste/Upload toggle switches input mode and clears the other input
- [ ] Upload mode shows filename/size + accepted-types hint; client rejects bad type/oversize inline
- [ ] Submit disabled on empty input (no text in Paste / no file in Upload) / while loading
- [ ] Upload submit sends multipart via `api.postForm` and renders `source_filename`
- [ ] Result renders as Markdown; mock result shows the MOCK banner
- [ ] History lists past reviews (paste + upload); click expands the full report
- [ ] Page renders correctly across 3+ themes (incl. light/dark)

Gate:
- [ ] `pytest tests/test_council.py` passes; overall coverage ≥ 80%
- [ ] `ruff check src tests`, `mypy src`, frontend `npm run lint` + `npm run build` clean
- [ ] `schema.d.ts` regenerated with the `/council` paths
- [ ] History survives `docker compose restart backend`
- [ ] Containers `(healthy)` after each restart before verifying
