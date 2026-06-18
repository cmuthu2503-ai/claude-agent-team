# Design Note: Personal Knowledge Library — Phase 2 UI (KB-PL-UI)

| Field | Value |
|-------|-------|
| Version | 1.0 |
| Date | 2026-06-18 |
| Status | **Approved — A1 + B1 selected by owner** |
| Relates to | `docs/prd-personal-knowledge-library.md` (the API/feature), `docs/mockups/kb-phase2-personal-library-mock.html` (the options explored) |
| Backs | the three live endpoints: `POST /knowledge/ingest-url`, `POST /knowledge/ingest-text`, `POST /knowledge/search` |

## Decision

The owner reviewed the option mock and selected **A1 (ingestion) + B1 (search)**:

- **A1 — "Add Article" tab.** A new fifth tab on the existing Knowledge Base screen (alongside Upload · Tag & Bucket · Buckets · Ground a Task). Contains a **From URL / Paste text** toggle, topic-bucket chips, and a live ingest pipeline indicator. Reuses the frozen tabbed layout exactly — lowest risk, most consistent.
- **B1 — "Search Library" tab.** A new sixth tab: a dedicated search screen with a query box, bucket filter chips, and ranked result cards each carrying the original **source link** (the "give me links & references" requirement). Doc-level de-duplicated (best chunk per article).

Rejected (kept as future enhancements, not built now): A2 unified box, A3 global quick-add bar, B2 command palette.

## Why A1 + B1
Lowest-effort, most consistent with the frozen KB design; cleanest 1:1 map to the already-built-and-tested API endpoints. Ingestion and search live as two new tabs on the same page the user already uses to manage knowledge.

## Build scope (frontend only — API already done)

| Piece | File | Change |
|---|---|---|
| Store actions + types | `frontend/src/stores/knowledge.ts` | add `ingestUrl`, `ingestText`, `searchLibrary` + `KbSearchResult` type |
| A1 screen | `frontend/src/pages/KnowledgeBase.tsx` | new `AddArticleScreen` component + `add` tab |
| B1 screen | `frontend/src/pages/KnowledgeBase.tsx` | new `SearchLibraryScreen` component + `search` tab |
| Tab wiring | `frontend/src/pages/KnowledgeBase.tsx` | extend `TabKey` + `TABS` |

## Conventions followed
- Theme CSS variables (`--accent`, `--bg-secondary`, `--border`, `--text-secondary`), no hard-coded neon — matches the in-app rendering of the frozen mock.
- Zustand store + `api` helper (`api.post`), soft-fail on `kb_available=false`.
- RBAC: ingestion gated to developer/admin (mirrors upload); search available to any authenticated user.
- Personal articles target the `kb_personal` namespace with `personal_auto_approve` (no curator step for solo use).

## Out of scope (this note)
Autonomous web discovery, command-palette search, global quick-add bar, Obsidian export. Tracked for later phases.
