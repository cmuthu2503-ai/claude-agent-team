/**
 * Board Preview — a STANDALONE playground page for evaluating the
 * three click-to-drill-in behaviors before we wire the real Build
 * Board + Command Center.
 *
 * Route: /preview/board (not linked in the sidebar — open via URL)
 *
 * No backend calls. Pure mock fixtures. The fixture covers every
 * task lifecycle state (backlog, in_progress, review, testing,
 * deployed, failed) and every card-level "enriched" field (agent +
 * cycle counter, workflow stage strip, elapsed time, $ spent, commit
 * SHA, error reason snippet).
 *
 * Drill-in content (stories list, agent timeline, test coverage,
 * outputs) is the SAME across all three behaviors — what you're
 * comparing is the DELIVERY (inline / panel / full-page), not the
 * content.
 */

import { useEffect, useRef, useState } from "react"
import { Link } from "react-router-dom"
import {
  X, ChevronDown, ChevronRight, ExternalLink, AlertTriangle,
  CheckCircle2, Clock, Bot, GitCommit, Rocket, Move, Minus,
} from "lucide-react"

// ── Types ────────────────────────────────────────────────────────────

type TaskStatus =
  | "backlog" | "dispatched" | "in_progress" | "review"
  | "testing" | "deployed" | "failed"

type WorkflowStage =
  | "prd" | "stories" | "development" | "review" | "testing"
  | "code_commit" | "deploy"

interface TaskCard {
  task_id: string
  phase: string
  title: string
  type: string
  agent: string | null
  priority: "low" | "medium" | "high"
  status: TaskStatus
  request_id: string | null
  current_stage: WorkflowStage | null
  cycle: number | null                // e.g. 2/3 = rework cycle 2 of max 3
  max_cycles: number | null
  elapsed_seconds: number | null
  cost_usd: number | null
  commit_sha: string | null
  files_count: number | null
  error_summary: string | null
  // Drill-in content
  stories: { story_id: string; title: string; status: string }[]
  timeline: { ts: string; agent: string; event: string }[]
  test_coverage: { passed: number; failed: number; total: number } | null
  outputs: { kind: string; ref: string; url?: string }[]
}

const STAGES: WorkflowStage[] = [
  "prd", "stories", "development", "review", "testing", "code_commit", "deploy",
]
const STAGE_SHORT: Record<WorkflowStage, string> = {
  prd: "PRD", stories: "Stories", development: "Dev", review: "Review",
  testing: "Test", code_commit: "Commit", deploy: "Deploy",
}

// ── Fixture data ─────────────────────────────────────────────────────

const FIXTURE: TaskCard[] = [
  {
    task_id: "T-ac7f52a2", phase: "Phase 2: Database & Persistence Layer",
    title: "Implement SQLite store with schema migrations",
    type: "backend", agent: "backend_specialist", priority: "high",
    status: "backlog", request_id: null, current_stage: null,
    cycle: null, max_cycles: null, elapsed_seconds: null, cost_usd: null,
    commit_sha: null, files_count: null, error_summary: null,
    stories: [], timeline: [], test_coverage: null, outputs: [],
  },
  {
    task_id: "T-27c92a43", phase: "Phase 2: Database & Persistence Layer",
    title: "Seed six default agents on first init",
    type: "backend", agent: "backend_specialist", priority: "high",
    status: "backlog", request_id: null, current_stage: null,
    cycle: null, max_cycles: null, elapsed_seconds: null, cost_usd: null,
    commit_sha: null, files_count: null, error_summary: null,
    stories: [], timeline: [], test_coverage: null, outputs: [],
  },
  {
    task_id: "T-4615781c", phase: "Phase 3: Auth & Cross-Cutting Middleware",
    title: "JWT auth dependency and role enforcement",
    type: "backend", agent: "backend_specialist", priority: "high",
    status: "backlog", request_id: null, current_stage: null,
    cycle: null, max_cycles: null, elapsed_seconds: null, cost_usd: null,
    commit_sha: null, files_count: null, error_summary: null,
    stories: [], timeline: [], test_coverage: null, outputs: [],
  },
  {
    task_id: "T-b02ee9e4", phase: "Phase 1: Foundation & Project Scaffolding",
    title: "Initialize frontend Vite + React 19 project",
    type: "frontend", agent: "frontend_specialist", priority: "high",
    status: "in_progress", request_id: "REQ-03D37E", current_stage: "development",
    cycle: 2, max_cycles: 3, elapsed_seconds: 107, cost_usd: 0.84,
    commit_sha: null, files_count: null, error_summary: null,
    stories: [
      { story_id: "US-001", title: "Bootstrap Vite + React 19 with TypeScript", status: "done" },
      { story_id: "US-002", title: "Configure Vite dev server on port 3000", status: "in_progress" },
      { story_id: "US-003", title: "Wire Tailwind + base theme", status: "to_do" },
    ],
    timeline: [
      { ts: "20:34:44", agent: "prd_specialist", event: "produced PRD" },
      { ts: "20:36:12", agent: "user_story_author", event: "extracted 3 stories" },
      { ts: "20:38:51", agent: "frontend_specialist", event: "cycle 1 — files emitted" },
      { ts: "20:40:22", agent: "code_reviewer", event: "rework requested: missing tailwind.config" },
      { ts: "20:41:55", agent: "frontend_specialist", event: "cycle 2 — files emitted" },
    ],
    test_coverage: null,
    outputs: [],
  },
  {
    task_id: "T-ee1c6c1f", phase: "Phase 4: Projects API",
    title: "CRUD endpoints for projects",
    type: "backend", agent: "backend_specialist", priority: "high",
    status: "in_progress", request_id: "REQ-A4F2C1", current_stage: "review",
    cycle: 1, max_cycles: 3, elapsed_seconds: 64, cost_usd: 0.52,
    commit_sha: null, files_count: null, error_summary: null,
    stories: [
      { story_id: "US-001", title: "GET /projects with pagination", status: "done" },
      { story_id: "US-002", title: "POST /projects with validation", status: "done" },
      { story_id: "US-003", title: "DELETE /projects/:id cascade", status: "done" },
    ],
    timeline: [
      { ts: "20:50:01", agent: "backend_specialist", event: "cycle 1 — files emitted" },
      { ts: "20:51:18", agent: "code_reviewer", event: "reviewing 4 files" },
    ],
    test_coverage: null,
    outputs: [],
  },
  {
    task_id: "T-588e74ca", phase: "Phase 3: Auth & Cross-Cutting Middleware",
    title: "Response envelope, error handling, request ID middleware",
    type: "backend", agent: "backend_specialist", priority: "high",
    status: "review", request_id: "REQ-7B3D11", current_stage: "review",
    cycle: 1, max_cycles: 3, elapsed_seconds: 38, cost_usd: 0.31,
    commit_sha: null, files_count: null, error_summary: null,
    stories: [
      { story_id: "US-001", title: "Wrap responses in {data, meta, error}", status: "in_progress" },
    ],
    timeline: [
      { ts: "20:55:12", agent: "code_reviewer", event: "diff scan started" },
    ],
    test_coverage: null,
    outputs: [],
  },
  {
    task_id: "T-bf5e8287", phase: "Phase 3: Auth & Cross-Cutting Middleware",
    title: "Rate limiting, idempotency, ETag support",
    type: "backend", agent: "backend_specialist", priority: "high",
    status: "testing", request_id: "REQ-2C9F88", current_stage: "testing",
    cycle: 2, max_cycles: 3, elapsed_seconds: 142, cost_usd: 1.07,
    commit_sha: null, files_count: null,
    error_summary: null,
    stories: [
      { story_id: "US-001", title: "Bucket rate-limit per token (60/min viewer, 600/min dev)", status: "done" },
      { story_id: "US-002", title: "Idempotency-Key dedupe with 24h cache", status: "done" },
    ],
    timeline: [
      { ts: "20:52:00", agent: "backend_specialist", event: "cycle 2 — files emitted" },
      { ts: "20:53:18", agent: "code_reviewer", event: "approved" },
      { ts: "20:53:45", agent: "tester_specialist", event: "running pytest..." },
    ],
    test_coverage: { passed: 7, failed: 0, total: 12 },
    outputs: [],
  },
  {
    task_id: "T-df9cbef2", phase: "Phase 1: Foundation & Project Scaffolding",
    title: "Author Docker Compose for local dev",
    type: "devops", agent: "devops_specialist", priority: "high",
    status: "deployed", request_id: "REQ-9C4586", current_stage: "deploy",
    cycle: 2, max_cycles: 3, elapsed_seconds: 192, cost_usd: 2.41,
    commit_sha: "fed3a89", files_count: 6, error_summary: null,
    stories: [
      { story_id: "US-001", title: "Two-service compose (backend + frontend)", status: "done" },
      { story_id: "US-002", title: "Bind-mount source for hot-reload", status: "done" },
      { story_id: "US-003", title: "Healthcheck per service", status: "done" },
    ],
    timeline: [
      { ts: "20:38:00", agent: "devops_specialist", event: "cycle 1 — files emitted" },
      { ts: "20:40:12", agent: "code_reviewer", event: "rework: missing wget in frontend Dockerfile" },
      { ts: "20:41:45", agent: "devops_specialist", event: "cycle 2 — files emitted" },
      { ts: "20:42:50", agent: "code_reviewer", event: "approved" },
      { ts: "20:43:12", agent: "tester_specialist", event: "docker compose config validates" },
      { ts: "20:43:58", agent: "code_writer", event: "committed fed3a89 (6 files)" },
    ],
    test_coverage: { passed: 4, failed: 0, total: 4 },
    outputs: [
      { kind: "commit", ref: "fed3a89", url: "https://github.com/cmuthu2503-ai/AIAgentTeam/commit/fed3a89" },
      { kind: "deploy", ref: "http://localhost:3100", url: "http://localhost:3100" },
    ],
  },
  {
    task_id: "T-12fa70b2", phase: "Phase 1: Foundation & Project Scaffolding",
    title: "Wire shared types package",
    type: "backend", agent: "backend_specialist", priority: "medium",
    status: "deployed", request_id: "REQ-118822", current_stage: "deploy",
    cycle: 1, max_cycles: 3, elapsed_seconds: 88, cost_usd: 0.71,
    commit_sha: "8a1c4d2", files_count: 3, error_summary: null,
    stories: [{ story_id: "US-001", title: "Add packages/shared with TS types", status: "done" }],
    timeline: [
      { ts: "20:30:00", agent: "backend_specialist", event: "cycle 1" },
      { ts: "20:31:15", agent: "code_reviewer", event: "approved" },
      { ts: "20:31:30", agent: "code_writer", event: "committed 8a1c4d2" },
    ],
    test_coverage: { passed: 2, failed: 0, total: 2 },
    outputs: [
      { kind: "commit", ref: "8a1c4d2", url: "https://github.com/cmuthu2503-ai/AIAgentTeam/commit/8a1c4d2" },
    ],
  },
  {
    task_id: "T-478a408a", phase: "Phase 1: Foundation & Project Scaffolding",
    title: "Initialize backend FastAPI project structure",
    type: "backend", agent: "backend_specialist", priority: "high",
    status: "failed", request_id: "REQ-2AAA57", current_stage: "code_commit",
    cycle: 3, max_cycles: 3, elapsed_seconds: 387, cost_usd: 4.32,
    commit_sha: null, files_count: null,
    error_summary: "Refusing to overwrite 'backend/app/main.py': line count dropped from 42 to 17 (60% reduction). CodeWriter snapshot-guard fired.",
    stories: [
      { story_id: "US-001", title: "Create app/main.py with FastAPI app", status: "done" },
      { story_id: "US-002", title: "Wire /health endpoint", status: "done" },
    ],
    timeline: [
      { ts: "20:34:40", agent: "backend_specialist", event: "cycle 1" },
      { ts: "20:36:12", agent: "code_reviewer", event: "rework: docstring missing" },
      { ts: "20:37:50", agent: "backend_specialist", event: "cycle 2" },
      { ts: "20:38:48", agent: "code_reviewer", event: "approved" },
      { ts: "20:39:11", agent: "tester_specialist", event: "tests pass" },
      { ts: "20:39:45", agent: "code_writer", event: "❌ snapshot-guard rejected" },
    ],
    test_coverage: { passed: 3, failed: 0, total: 3 },
    outputs: [],
  },
  {
    task_id: "T-7c44a801", phase: "Phase 5: Frontend shell",
    title: "Implement project list page with cards",
    type: "frontend", agent: "frontend_specialist", priority: "medium",
    status: "failed", request_id: "REQ-883110", current_stage: "review",
    cycle: 3, max_cycles: 3, elapsed_seconds: 412, cost_usd: 5.18,
    commit_sha: null, files_count: null,
    error_summary: "Code review escalated after 3 rework cycles — agent could not satisfy 'no inline styles' rule from /rules/ui.md.",
    stories: [{ story_id: "US-001", title: "Card grid with hover states", status: "in_progress" }],
    timeline: [
      { ts: "20:20:00", agent: "frontend_specialist", event: "cycle 1" },
      { ts: "20:23:00", agent: "code_reviewer", event: "rework" },
      { ts: "20:26:00", agent: "frontend_specialist", event: "cycle 2" },
      { ts: "20:29:00", agent: "code_reviewer", event: "rework" },
      { ts: "20:32:00", agent: "frontend_specialist", event: "cycle 3" },
      { ts: "20:33:48", agent: "code_reviewer", event: "❌ max rework cycles" },
    ],
    test_coverage: null,
    outputs: [],
  },
  {
    task_id: "T-22ee1133", phase: "Phase 5: Frontend shell",
    title: "Settings page with theme toggle",
    type: "frontend", agent: "frontend_specialist", priority: "low",
    status: "backlog", request_id: null, current_stage: null,
    cycle: null, max_cycles: null, elapsed_seconds: null, cost_usd: null,
    commit_sha: null, files_count: null, error_summary: null,
    stories: [], timeline: [], test_coverage: null, outputs: [],
  },
]

const COLUMNS: { key: TaskStatus; label: string; color: string }[] = [
  { key: "backlog", label: "Backlog", color: "var(--text-muted)" },
  { key: "in_progress", label: "In Progress", color: "var(--accent)" },
  { key: "review", label: "Review", color: "#b026ff" },
  { key: "testing", label: "Testing", color: "#f59e0b" },
  { key: "deployed", label: "Deployed", color: "var(--success)" },
  { key: "failed", label: "Failed", color: "var(--danger)" },
]


// ── Click-behavior modes ─────────────────────────────────────────────

type Mode = "inline" | "panel" | "fullpage" | "popup" | "compare"


export function BoardPreviewPage() {
  // Default to "compare" so opening the page shows all 3 side-by-side.
  const [mode, setMode] = useState<Mode>("compare")
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)

  const tasksByStatus = (status: TaskStatus): TaskCard[] =>
    FIXTURE.filter((t) => t.status === status)

  const selected = selectedTaskId
    ? FIXTURE.find((t) => t.task_id === selectedTaskId) ?? null
    : null

  return (
    <div style={{
      padding: "20px 28px",
      display: "flex", flexDirection: "column", gap: 16,
      fontFamily: "var(--font)",
      minHeight: "calc(100vh - 52px)",
    }}>
      {/* Page header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 11, color: "var(--text-muted)", letterSpacing: 1, textTransform: "uppercase", marginBottom: 4 }}>
            Preview · pick the click behavior
          </div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, color: "var(--text-primary)" }}>
            Consolidated Build Board
          </h1>
          <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--text-secondary)" }}>
            Mock fixture data · 12 tasks across all 6 columns. Use "Compare all 3" to see every mode at once, or pick one to test interactively.
          </p>
        </div>
        <ModeSwitcher mode={mode} onChange={(m) => { setMode(m); setSelectedTaskId(null) }} />
      </div>

      {/* ── Compare mode: render all 3 stacked with pre-selected cards ── */}
      {mode === "compare" && <CompareAllThree tasksByStatus={tasksByStatus} />}

      {/* ── Single-mode interactive views ── */}
      {mode !== "compare" && (
        <>
          {/* Project-level summary header (Phase 4) */}
          <ProjectSummary />

          {/* Board layout: 6 columns. When mode=panel and a card is selected,
              the board area shrinks left and a drawer slides in on the right. */}
          <div style={{
            display: "grid",
            gridTemplateColumns:
              mode === "panel" && selected
                ? "minmax(0, 1fr) 480px"
                : "minmax(0, 1fr)",
            gap: 12,
            minHeight: 0,
          }}>
            <Board
              columns={COLUMNS}
              tasksByStatus={tasksByStatus}
              mode={mode}
              selectedTaskId={selectedTaskId}
              onSelect={(id) => setSelectedTaskId(id === selectedTaskId ? null : id)}
            />

            {mode === "panel" && selected && (
              <SidePanel task={selected} onClose={() => setSelectedTaskId(null)} />
            )}
          </div>

          {mode === "fullpage" && selected && (
            <FullPageModal task={selected} onClose={() => setSelectedTaskId(null)} />
          )}

          {mode === "popup" && selected && (
            <PopupWindow task={selected} onClose={() => setSelectedTaskId(null)} />
          )}
        </>
      )}

      {/* Decision helper */}
      <div style={{
        marginTop: 20, padding: "12px 16px",
        background: "var(--bg-card)", border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.6,
      }}>
        <strong style={{ color: "var(--text-primary)" }}>Compare and decide:</strong>
        <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
          <li><b>Inline expand</b> — keeps everything on one page; card grows in place. Best when drill-in content is short.</li>
          <li><b>Side panel</b> — board stays visible on the left while you read details on the right. Best for comparing tasks rapidly.</li>
          <li><b>Full page</b> — modal takes over the screen; most space for content. Best when drill-in is very long.</li>
          <li><b>Popup window</b> — floating draggable card; drag it anywhere; board stays clickable behind it. Best when you want to pin one task's detail open while you work on others.</li>
        </ul>
        <div style={{ marginTop: 8 }}>
          Once you pick, I'll wire the choice into the real <code>ProjectStoryBoard</code> and also apply the same enriched card style to Command Center one-off requests.
        </div>
      </div>
    </div>
  )
}


// ── Compare-all-three view ───────────────────────────────────────────
// Three stacked sections, each showing one mode with the SAME task
// pre-selected (T-b02ee9e4 — frontend in-progress, has the richest
// fixture data: stories, timeline, cycles). User scrolls top-to-bottom
// to compare layouts without toggling.

function CompareAllThree({
  tasksByStatus,
}: { tasksByStatus: (s: TaskStatus) => TaskCard[] }) {
  // Pick the task with the most fixture content for the drill-in demos.
  const DEMO_TASK_ID = "T-b02ee9e4"
  const demoTask = FIXTURE.find((t) => t.task_id === DEMO_TASK_ID)!

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 32 }}>
      {/* ───────── Section 1: SIDE PANEL ───────── */}
      <CompareSection
        index={1}
        label="Side panel"
        tagline="Board stays visible on the left · 480px drawer on the right · best for rapid comparison"
        recommended
      >
        <div style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 1fr) 480px",
          gap: 12,
          minHeight: 0,
        }}>
          <CompactBoard tasksByStatus={tasksByStatus} selectedTaskId={DEMO_TASK_ID} />
          <SidePanel task={demoTask} onClose={() => {}} />
        </div>
      </CompareSection>

      {/* ───────── Section 2: INLINE EXPAND ───────── */}
      <CompareSection
        index={2}
        label="Inline expand"
        tagline="Card grows downward in place · everything stays on one page · best when drill-ins are short"
      >
        <CompactBoard
          tasksByStatus={tasksByStatus}
          selectedTaskId={DEMO_TASK_ID}
          renderMode="inline"
        />
      </CompareSection>

      {/* ───────── Section 4: POPUP WINDOW (floating draggable card) ───────── */}
      <CompareSection
        index={4}
        label="Popup window"
        tagline="Floating draggable card · board stays interactive behind it · multiple can be open at once"
      >
        <div style={{ position: "relative" }}>
          <CompactBoard tasksByStatus={tasksByStatus} selectedTaskId={DEMO_TASK_ID} />
          {/* Render the popup statically (no absolute positioning) so it
              shows up inside this Compare section instead of floating
              over the page. Visual representation only. */}
          <div style={{
            position: "absolute",
            top: 24, right: 24,
            width: 480, maxHeight: 540,
            background: "var(--bg-card)", border: "1px solid var(--accent)",
            borderRadius: "var(--radius)",
            boxShadow: "0 8px 32px rgba(0,0,0,0.45)",
            display: "flex", flexDirection: "column",
            overflow: "hidden",
          }}>
            {/* Title bar (drag handle) */}
            <div style={{
              padding: "8px 12px",
              background: "var(--bg-hover)",
              borderBottom: "1px solid var(--border)",
              display: "flex", alignItems: "center", justifyContent: "space-between",
              cursor: "grab",
            }}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                <Move size={11} color="var(--text-muted)" />
                <span style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{demoTask.task_id}</span>
                <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text-primary)" }}>{demoTask.title.slice(0, 28)}…</span>
              </span>
              <span style={{ display: "inline-flex", gap: 4 }}>
                <span style={{
                  width: 18, height: 18, borderRadius: 3,
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                  color: "var(--text-muted)", background: "transparent",
                }}>
                  <Minus size={12} />
                </span>
                <span style={{
                  width: 18, height: 18, borderRadius: 3,
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                  color: "var(--text-muted)", background: "transparent",
                }}>
                  <X size={12} />
                </span>
              </span>
            </div>
            <div style={{ padding: 12, overflow: "auto", flex: 1 }}>
              <DrillIn task={demoTask} compact />
            </div>
          </div>
        </div>
      </CompareSection>

      {/* ───────── Section 3: FULL PAGE (rendered inline, not as overlay) ───────── */}
      <CompareSection
        index={3}
        label="Full page"
        tagline="Modal takes the screen · most reading space · best for long-form post-mortems"
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ position: "relative" }}>
            <CompactBoard
              tasksByStatus={tasksByStatus}
              selectedTaskId={DEMO_TASK_ID}
              dimmed
            />
            <div style={{
              position: "absolute", inset: 0,
              background: "rgba(0,0,0,0.5)",
              borderRadius: "var(--radius)",
              pointerEvents: "none",
            }} />
          </div>
          {/* Render the modal content inline (not as fixed overlay) so the
              user can see what it would look like without it actually
              covering the rest of the page. */}
          <div style={{
            width: "100%", maxWidth: 960, margin: "0 auto",
            background: "var(--bg-card)", border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
          }}>
            <div style={{
              padding: "14px 20px", borderBottom: "1px solid var(--border)",
              display: "flex", justifyContent: "space-between", alignItems: "center",
            }}>
              <div>
                <div style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{demoTask.task_id}</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: "var(--text-primary)" }}>{demoTask.title}</div>
                <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 2 }}>{demoTask.phase}</div>
              </div>
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>(modal close button)</span>
            </div>
            <div style={{ padding: 20 }}>
              <DrillIn task={demoTask} />
            </div>
          </div>
        </div>
      </CompareSection>
    </div>
  )
}

function CompareSection({
  index, label, tagline, recommended, children,
}: {
  index: number
  label: string
  tagline: string
  recommended?: boolean
  children: React.ReactNode
}) {
  return (
    <section style={{
      padding: "16px 20px",
      background: "var(--bg-secondary, var(--bg-card))",
      border: "1px solid var(--border)",
      borderRadius: "var(--radius)",
      display: "flex", flexDirection: "column", gap: 12,
    }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <span style={{
          fontSize: 11, fontWeight: 800, letterSpacing: 1,
          textTransform: "uppercase",
          color: "var(--accent)",
          padding: "2px 8px",
          background: "color-mix(in srgb, var(--accent) 14%, transparent)",
          border: "1px solid var(--accent)",
          borderRadius: 3,
        }}>
          Option {index}
        </span>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: "var(--text-primary)" }}>{label}</h2>
        {recommended && (
          <span style={{
            fontSize: 10, fontWeight: 700, padding: "2px 8px",
            background: "color-mix(in srgb, var(--success) 14%, transparent)",
            color: "var(--success)", border: "1px solid var(--success)",
            borderRadius: 3, textTransform: "uppercase", letterSpacing: 1,
          }}>
            Recommended
          </span>
        )}
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{tagline}</span>
      </div>
      {children}
    </section>
  )
}

// Compact, non-interactive board used inside the Compare sections.
// `selectedTaskId` highlights one card so the drill-in visualization
// has obvious context. `renderMode="inline"` expands the selected card
// to demonstrate inline mode. `dimmed` greys it out to suggest the
// overlay covering the board in full-page mode.

function CompactBoard({
  tasksByStatus,
  selectedTaskId,
  renderMode,
  dimmed,
}: {
  tasksByStatus: (s: TaskStatus) => TaskCard[]
  selectedTaskId?: string
  renderMode?: "inline"
  dimmed?: boolean
}) {
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: `repeat(${COLUMNS.length}, minmax(0, 1fr))`,
      gap: 6,
      opacity: dimmed ? 0.45 : 1,
      pointerEvents: "none",  // visual-only — don't let users interact with the compact preview
    }}>
      {COLUMNS.map((col) => {
        const tasks = tasksByStatus(col.key)
        return (
          <div key={col.key} style={{
            background: "var(--bg-card)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            display: "flex", flexDirection: "column",
            minHeight: 160,
          }}>
            <div style={{
              padding: "6px 10px", borderBottom: "1px solid var(--border)",
              display: "flex", justifyContent: "space-between", alignItems: "center",
              fontSize: 10, fontWeight: 700, letterSpacing: 1, textTransform: "uppercase",
            }}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 4, color: col.color }}>
                <span style={{ width: 6, height: 6, borderRadius: 3, background: col.color }} />
                {col.label}
              </span>
              <span style={{ color: "var(--text-muted)" }}>{tasks.length}</span>
            </div>
            <div style={{ padding: 6, display: "flex", flexDirection: "column", gap: 6 }}>
              {tasks.length === 0 && (
                <div style={{ fontSize: 10, color: "var(--text-muted)", padding: 8, textAlign: "center" }}>
                  (empty)
                </div>
              )}
              {tasks.map((t) => {
                const isSelected = t.task_id === selectedTaskId
                return (
                  <div key={t.task_id} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    <Card task={t} isSelected={isSelected} onClick={() => {}} />
                    {renderMode === "inline" && isSelected && (
                      <DrillIn task={t} compact />
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}


// ── Mode switcher ────────────────────────────────────────────────────

function ModeSwitcher({
  mode, onChange,
}: { mode: Mode; onChange: (m: Mode) => void }) {
  const opts: { id: Mode; label: string }[] = [
    { id: "compare",  label: "Compare all 4" },
    { id: "inline",   label: "Inline expand" },
    { id: "panel",    label: "Side panel" },
    { id: "fullpage", label: "Full page" },
    { id: "popup",    label: "Popup window" },
  ]
  return (
    <div style={{
      display: "inline-flex", gap: 2, padding: 3,
      background: "var(--bg-hover)", border: "1px solid var(--border)",
      borderRadius: "var(--radius)",
    }}>
      {opts.map((o) => (
        <button
          key={o.id}
          type="button"
          onClick={() => onChange(o.id)}
          style={{
            padding: "6px 14px", fontSize: 12, fontWeight: 700,
            background: mode === o.id ? "var(--accent)" : "transparent",
            color: mode === o.id ? "#0a0014" : "var(--text-secondary)",
            border: "none", borderRadius: "var(--radius)",
            cursor: "pointer", fontFamily: "var(--font)",
          }}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}


// ── Project-level summary (Phase 4 from the plan) ────────────────────

function ProjectSummary() {
  const done = FIXTURE.filter((t) => t.status === "deployed").length
  const total = FIXTURE.length
  const inFlight = FIXTURE.filter((t) =>
    ["in_progress", "review", "testing"].includes(t.status),
  ).length
  const failed = FIXTURE.filter((t) => t.status === "failed").length
  const pct = Math.round((done / total) * 100)

  return (
    <div style={{
      padding: "12px 16px",
      background: "var(--bg-card)", border: "1px solid var(--border)",
      borderRadius: "var(--radius)",
      display: "flex", flexWrap: "wrap", gap: 24, alignItems: "center",
      fontSize: 12, color: "var(--text-secondary)",
    }}>
      <div>
        <div style={{ fontSize: 10, color: "var(--text-muted)", letterSpacing: 1, textTransform: "uppercase" }}>Progress</div>
        <div style={{ marginTop: 4, fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>
          {done} / {total} done ({pct}%)
        </div>
      </div>
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <span style={{ display: "inline-block", width: 200, height: 8, background: "var(--bg-hover)", borderRadius: 4, overflow: "hidden" }}>
          <span style={{ display: "block", width: `${pct}%`, height: "100%", background: "var(--success)" }} />
        </span>
      </div>
      <Counter icon={<Bot size={12} />} label="in flight" value={inFlight} color="var(--accent)" />
      <Counter icon={<AlertTriangle size={12} />} label="failed" value={failed} color="var(--danger)" />
      <div style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-muted)" }}>
        Spent today · <b style={{ color: "var(--text-primary)" }}>$41.41</b>
      </div>
    </div>
  )
}

function Counter({ icon, label, value, color }: { icon: React.ReactNode; label: string; value: number; color: string }) {
  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span style={{ color }}>{icon}</span>
      <span style={{ fontWeight: 700, color: "var(--text-primary)" }}>{value}</span>
      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{label}</span>
    </div>
  )
}


// ── Board (6 columns) ────────────────────────────────────────────────

function Board({
  columns, tasksByStatus, mode, selectedTaskId, onSelect,
}: {
  columns: { key: TaskStatus; label: string; color: string }[]
  tasksByStatus: (s: TaskStatus) => TaskCard[]
  mode: Mode
  selectedTaskId: string | null
  onSelect: (id: string) => void
}) {
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: `repeat(${columns.length}, minmax(0, 1fr))`,
      gap: 8,
      minHeight: 0,
    }}>
      {columns.map((col) => {
        const tasks = tasksByStatus(col.key)
        return (
          <div key={col.key} style={{
            background: "var(--bg-secondary, var(--bg-card))",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            display: "flex", flexDirection: "column",
            minHeight: 200,
          }}>
            <div style={{
              padding: "8px 12px", borderBottom: "1px solid var(--border)",
              display: "flex", justifyContent: "space-between", alignItems: "center",
              fontSize: 11, fontWeight: 700, letterSpacing: 1, textTransform: "uppercase",
            }}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: col.color }}>
                <span style={{ width: 8, height: 8, borderRadius: 4, background: col.color }} />
                {col.label}
              </span>
              <span style={{ color: "var(--text-muted)" }}>{tasks.length}</span>
            </div>
            <div style={{ padding: 8, display: "flex", flexDirection: "column", gap: 8 }}>
              {tasks.length === 0 && (
                <div style={{ fontSize: 11, color: "var(--text-muted)", padding: 12, textAlign: "center" }}>
                  (empty)
                </div>
              )}
              {tasks.map((t) => (
                <CardWrapper
                  key={t.task_id}
                  task={t}
                  mode={mode}
                  isSelected={t.task_id === selectedTaskId}
                  onSelect={() => onSelect(t.task_id)}
                />
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}


// ── Card (the enriched per-task tile) + wrapper that switches mode ──

function CardWrapper({
  task, mode, isSelected, onSelect,
}: { task: TaskCard; mode: Mode; isSelected: boolean; onSelect: () => void }) {
  const card = <Card task={task} isSelected={isSelected} onClick={onSelect} />
  if (mode === "inline" && isSelected) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {card}
        <DrillIn task={task} compact />
      </div>
    )
  }
  return card
}

function Card({
  task, isSelected, onClick,
}: { task: TaskCard; isSelected: boolean; onClick: () => void }) {
  const priorityColor =
    task.priority === "high"   ? "var(--danger)"
    : task.priority === "low"  ? "var(--text-muted)"
    : "var(--accent)"

  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        textAlign: "left",
        padding: "10px 12px",
        background: isSelected ? "var(--bg-hover)" : "var(--bg-card)",
        border: "1px solid " + (isSelected ? "var(--accent)" : "var(--border)"),
        borderRadius: "var(--radius)",
        display: "flex", flexDirection: "column", gap: 6,
        cursor: "pointer", fontFamily: "var(--font)",
        boxShadow: isSelected ? "0 0 0 1px var(--accent)" : "none",
      }}
    >
      {/* Top line: task ID + priority pill */}
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        fontSize: 10, fontFamily: "var(--font-mono)",
      }}>
        <span style={{ color: "var(--text-muted)" }}>{task.task_id}</span>
        <span style={{ color: priorityColor, fontWeight: 700, textTransform: "uppercase" }}>
          ● {task.priority}
        </span>
      </div>

      {/* Phase + title */}
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", lineHeight: 1.35 }}>
        <span style={{ color: "var(--text-muted)", fontWeight: 500 }}>{task.phase}</span>
        <br />
        {task.title}
      </div>

      {/* Live status block (only for non-backlog) */}
      {task.status !== "backlog" && (
        <div style={{
          marginTop: 4, padding: "6px 8px",
          background: "var(--bg-secondary, color-mix(in srgb, var(--accent) 4%, transparent))",
          border: "1px solid var(--border)",
          borderRadius: 4,
          display: "flex", flexDirection: "column", gap: 4,
        }}>
          {/* Agent + cycle */}
          {task.agent && task.cycle && (
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
              <Bot size={11} color="var(--accent)" />
              <span style={{ fontWeight: 600 }}>{task.agent}</span>
              <span style={{ color: "var(--text-muted)" }}>· cycle {task.cycle}/{task.max_cycles}</span>
            </div>
          )}
          {/* Workflow stage strip */}
          {task.current_stage && task.status !== "deployed" && task.status !== "failed" && (
            <StageStrip currentStage={task.current_stage} status={task.status} />
          )}
          {/* Elapsed + cost */}
          {task.elapsed_seconds !== null && (
            <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 10, color: "var(--text-muted)" }}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
                <Clock size={10} />
                {formatElapsed(task.elapsed_seconds)}
              </span>
              {task.cost_usd !== null && (
                <span>· ${task.cost_usd.toFixed(2)}</span>
              )}
            </div>
          )}
          {/* Deployed: commit + files */}
          {task.status === "deployed" && task.commit_sha && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11, color: "var(--success)" }}>
              <CheckCircle2 size={11} />
              <span>Done · {task.files_count} files</span>
              <span style={{ color: "var(--text-muted)" }}>·</span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 10 }}>{task.commit_sha}</span>
            </div>
          )}
          {/* Failed: error snippet + retry */}
          {task.status === "failed" && task.error_summary && (
            <>
              <div style={{
                fontSize: 11, color: "var(--danger)",
                display: "flex", alignItems: "flex-start", gap: 4,
              }}>
                <AlertTriangle size={11} style={{ flexShrink: 0, marginTop: 1 }} />
                <span style={{
                  whiteSpace: "nowrap", overflow: "hidden",
                  textOverflow: "ellipsis", maxWidth: "100%",
                }}
                title={task.error_summary}>
                  {task.error_summary}
                </span>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); alert("(preview) Would re-dispatch this task as a new REQ"); }}
                  style={{
                    padding: "2px 8px", fontSize: 10, fontWeight: 700,
                    background: "var(--accent)", color: "#0a0014",
                    border: "1px solid var(--accent)", borderRadius: 3,
                    cursor: "pointer",
                  }}
                >
                  Retry
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {/* Footer: agent type chip + request link */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10 }}>
        <span style={{
          padding: "1px 6px", borderRadius: 3,
          background: "var(--bg-hover)", color: "var(--text-secondary)",
          fontFamily: "var(--font-mono)",
        }}>
          {task.type}
        </span>
        {task.request_id && (
          <span style={{
            color: "var(--accent)", fontFamily: "var(--font-mono)",
            display: "inline-flex", alignItems: "center", gap: 2,
          }}>
            <Rocket size={9} />
            {task.request_id}
          </span>
        )}
      </div>
    </button>
  )
}

function StageStrip({ currentStage, status }: { currentStage: WorkflowStage; status: TaskStatus }) {
  const currentIdx = STAGES.indexOf(currentStage)
  return (
    <div style={{ display: "flex", gap: 2, fontSize: 9 }}>
      {STAGES.map((s, i) => {
        const isPast = i < currentIdx
        const isCurrent = i === currentIdx
        const color =
          isPast ? "var(--success)"
          : isCurrent ? "var(--accent)"
          : "var(--text-muted)"
        return (
          <span
            key={s}
            style={{
              flex: 1,
              padding: "2px 0", textAlign: "center",
              background: isCurrent ? "color-mix(in srgb, var(--accent) 14%, transparent)" : "transparent",
              border: "1px solid " + (isPast ? "var(--success)" : isCurrent ? "var(--accent)" : "var(--border)"),
              borderRadius: 2, color,
              fontWeight: isCurrent ? 700 : 500,
              minWidth: 0, overflow: "hidden", textOverflow: "ellipsis",
            }}
            title={s}
          >
            {isPast ? "✓" : STAGE_SHORT[s][0]}
          </span>
        )
      })}
    </div>
  )
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60), s = seconds % 60
  return `${m}m ${s}s`
}


// ── Drill-in content (shared across all 3 modes) ─────────────────────

function DrillIn({ task, compact = false }: { task: TaskCard; compact?: boolean }) {
  return (
    <div style={{
      padding: compact ? 10 : 16,
      background: "var(--bg-card)", border: "1px solid var(--accent)",
      borderRadius: "var(--radius)",
      display: "flex", flexDirection: "column", gap: 12,
      fontSize: 12,
    }}>
      {/* Workflow stages — wide horizontal strip */}
      {task.current_stage && (
        <div>
          <SectionHeader label="Workflow" />
          <FullStageStrip currentStage={task.current_stage} terminalGood={task.status === "deployed"} terminalBad={task.status === "failed"} />
        </div>
      )}

      {/* User stories */}
      {task.stories.length > 0 && (
        <div>
          <SectionHeader label="User stories" count={task.stories.length} />
          <ul style={{ margin: 0, paddingLeft: 18, color: "var(--text-secondary)", lineHeight: 1.5 }}>
            {task.stories.map((s) => (
              <li key={s.story_id}>
                <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{s.story_id}</span>
                {" "}{s.title}
                {" "}<span style={{
                  fontSize: 10, padding: "0 6px", borderRadius: 2,
                  background:
                    s.status === "done" ? "color-mix(in srgb, var(--success) 14%, transparent)" :
                    s.status === "in_progress" ? "color-mix(in srgb, var(--accent) 14%, transparent)" :
                    "var(--bg-hover)",
                  color:
                    s.status === "done" ? "var(--success)" :
                    s.status === "in_progress" ? "var(--accent)" :
                    "var(--text-muted)",
                }}>
                  {s.status}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Agent timeline */}
      {task.timeline.length > 0 && (
        <div>
          <SectionHeader label="Agent timeline" count={task.timeline.length} />
          <div style={{ display: "flex", flexDirection: "column", gap: 4, color: "var(--text-secondary)" }}>
            {task.timeline.map((e, i) => (
              <div key={i} style={{ display: "flex", gap: 8, fontSize: 11 }}>
                <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)", minWidth: 56 }}>{e.ts}</span>
                <span style={{ fontWeight: 600, minWidth: 130 }}>{e.agent}</span>
                <span>{e.event}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Test coverage */}
      {task.test_coverage && (
        <div>
          <SectionHeader label="Tests" />
          <div style={{ display: "flex", gap: 12, alignItems: "center", fontSize: 12 }}>
            <span style={{ color: "var(--success)" }}>✓ {task.test_coverage.passed} passed</span>
            {task.test_coverage.failed > 0 && (
              <span style={{ color: "var(--danger)" }}>✗ {task.test_coverage.failed} failed</span>
            )}
            <span style={{ color: "var(--text-muted)" }}>of {task.test_coverage.total} total</span>
          </div>
        </div>
      )}

      {/* Outputs */}
      {task.outputs.length > 0 && (
        <div>
          <SectionHeader label="Outputs" />
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {task.outputs.map((o, i) => (
              <div key={i} style={{ display: "flex", gap: 8, fontSize: 11 }}>
                {o.kind === "commit"
                  ? <GitCommit size={12} color="var(--accent)" />
                  : <Rocket size={12} color="var(--success)" />}
                <span style={{ fontFamily: "var(--font-mono)" }}>{o.ref}</span>
                {o.url && (
                  <a href={o.url} target="_blank" rel="noopener noreferrer"
                     style={{ color: "var(--accent)", textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 2 }}>
                    open <ExternalLink size={10} />
                  </a>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Error block */}
      {task.error_summary && (
        <div>
          <SectionHeader label="Error" />
          <div style={{
            padding: 8, borderRadius: 3,
            background: "color-mix(in srgb, var(--danger) 8%, transparent)",
            border: "1px solid var(--danger)",
            color: "var(--danger)", fontSize: 11, fontFamily: "var(--font-mono)",
            whiteSpace: "pre-wrap",
          }}>
            {task.error_summary}
          </div>
        </div>
      )}

      {!task.stories.length && !task.timeline.length && (
        <div style={{ color: "var(--text-muted)", fontStyle: "italic" }}>
          This task hasn't been dispatched yet — no stories, timeline, or outputs to show.
        </div>
      )}
    </div>
  )
}

function SectionHeader({ label, count }: { label: string; count?: number }) {
  return (
    <div style={{
      fontSize: 10, fontWeight: 700, letterSpacing: 1,
      textTransform: "uppercase", color: "var(--text-muted)",
      marginBottom: 4,
    }}>
      {label}{count !== undefined ? ` · ${count}` : ""}
    </div>
  )
}

function FullStageStrip({ currentStage, terminalGood, terminalBad }: { currentStage: WorkflowStage; terminalGood: boolean; terminalBad: boolean }) {
  const currentIdx = STAGES.indexOf(currentStage)
  return (
    <div style={{ display: "flex", gap: 4 }}>
      {STAGES.map((s, i) => {
        const isPast = i < currentIdx || terminalGood
        const isCurrent = i === currentIdx && !terminalGood
        const isFailed = i === currentIdx && terminalBad
        const color =
          isFailed ? "var(--danger)" :
          isPast ? "var(--success)" :
          isCurrent ? "var(--accent)" :
          "var(--text-muted)"
        return (
          <span key={s} style={{
            flex: 1, padding: "6px 4px", textAlign: "center",
            fontSize: 10, fontWeight: 600,
            background: isCurrent
              ? "color-mix(in srgb, var(--accent) 14%, transparent)"
              : isFailed
                ? "color-mix(in srgb, var(--danger) 14%, transparent)"
                : "var(--bg-hover)",
            border: "1px solid " + color,
            borderRadius: 3, color,
          }}>
            {isPast ? "✓ " : ""}{STAGE_SHORT[s]}
          </span>
        )
      })}
    </div>
  )
}


// ── Side panel (slides in on the right) ──────────────────────────────

function SidePanel({ task, onClose }: { task: TaskCard; onClose: () => void }) {
  return (
    <div style={{
      background: "var(--bg-card)", border: "1px solid var(--border)",
      borderRadius: "var(--radius)",
      display: "flex", flexDirection: "column",
      maxHeight: "calc(100vh - 200px)",
    }}>
      <div style={{
        padding: "10px 14px", borderBottom: "1px solid var(--border)",
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <div>
          <div style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{task.task_id}</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>{task.title}</div>
        </div>
        <button type="button" onClick={onClose} style={iconBtn}><X size={16} /></button>
      </div>
      <div style={{ padding: 0, overflow: "auto", flex: 1 }}>
        <DrillIn task={task} />
      </div>
    </div>
  )
}


// ── Full-page modal ──────────────────────────────────────────────────

function FullPageModal({ task, onClose }: { task: TaskCard; onClose: () => void }) {
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 10001,
        background: "rgba(0,0,0,0.75)",
        display: "flex", alignItems: "flex-start", justifyContent: "center",
        overflowY: "auto", padding: "40px 20px",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "100%", maxWidth: 960,
          background: "var(--bg-card)", border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          display: "flex", flexDirection: "column",
        }}
      >
        <div style={{
          padding: "14px 20px", borderBottom: "1px solid var(--border)",
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}>
          <div>
            <div style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{task.task_id}</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "var(--text-primary)" }}>{task.title}</div>
            <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 2 }}>{task.phase}</div>
          </div>
          <button type="button" onClick={onClose} style={iconBtn}><X size={18} /></button>
        </div>
        <div style={{ padding: 20 }}>
          <DrillIn task={task} />
        </div>
      </div>
    </div>
  )
}


const iconBtn: React.CSSProperties = {
  background: "transparent", border: "none", color: "var(--text-muted)",
  cursor: "pointer", padding: 4,
  display: "inline-flex", alignItems: "center", justifyContent: "center",
}


// ── Popup window (floating, draggable, non-modal) ────────────────────
// Real implementation for the interactive mode. The user can drag the
// title bar to reposition the window anywhere on the screen. The
// board behind stays clickable — multiple popups could theoretically
// be opened (we'd add a window manager for that). Close via X.

function PopupWindow({ task, onClose }: { task: TaskCard; onClose: () => void }) {
  // Initial position: centered horizontally near the top.
  const [pos, setPos] = useState({
    x: Math.max(0, (window.innerWidth - 520) / 2),
    y: 120,
  })
  const [minimized, setMinimized] = useState(false)
  const dragOffset = useRef<{ x: number; y: number } | null>(null)

  // Drag handlers — mouse on title bar starts the drag.
  const onMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    dragOffset.current = { x: e.clientX - pos.x, y: e.clientY - pos.y }
    e.preventDefault()
  }
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragOffset.current) return
      setPos({
        x: e.clientX - dragOffset.current.x,
        y: e.clientY - dragOffset.current.y,
      })
    }
    const onUp = () => { dragOffset.current = null }
    window.addEventListener("mousemove", onMove)
    window.addEventListener("mouseup", onUp)
    return () => {
      window.removeEventListener("mousemove", onMove)
      window.removeEventListener("mouseup", onUp)
    }
  }, [])

  return (
    <div style={{
      position: "fixed",
      top: pos.y, left: pos.x,
      width: 520,
      maxHeight: minimized ? 40 : "75vh",
      background: "var(--bg-card)",
      border: "1px solid var(--accent)",
      borderRadius: "var(--radius)",
      boxShadow: "0 10px 40px rgba(0,0,0,0.5)",
      display: "flex", flexDirection: "column",
      overflow: "hidden",
      zIndex: 9999,
      // Non-modal: no backdrop, the rest of the page stays clickable.
    }}>
      {/* Title bar — drag handle + window controls */}
      <div
        onMouseDown={onMouseDown}
        style={{
          padding: "8px 12px",
          background: "var(--bg-hover)",
          borderBottom: "1px solid var(--border)",
          display: "flex", alignItems: "center", justifyContent: "space-between",
          cursor: dragOffset.current ? "grabbing" : "grab",
          userSelect: "none",
          flexShrink: 0,
        }}
      >
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <Move size={12} color="var(--text-muted)" />
          <span style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{task.task_id}</span>
          <span style={{
            fontSize: 13, fontWeight: 700, color: "var(--text-primary)",
            whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
          }}>
            {task.title}
          </span>
        </span>
        <span style={{ display: "inline-flex", gap: 2, flexShrink: 0 }}>
          <button
            type="button"
            onClick={() => setMinimized((m) => !m)}
            title={minimized ? "Restore" : "Minimize"}
            style={{
              width: 22, height: 22, padding: 0, borderRadius: 3,
              background: "transparent", color: "var(--text-muted)",
              border: "none", cursor: "pointer",
              display: "inline-flex", alignItems: "center", justifyContent: "center",
            }}
          >
            <Minus size={12} />
          </button>
          <button
            type="button"
            onClick={onClose}
            title="Close"
            style={{
              width: 22, height: 22, padding: 0, borderRadius: 3,
              background: "transparent", color: "var(--text-muted)",
              border: "none", cursor: "pointer",
              display: "inline-flex", alignItems: "center", justifyContent: "center",
            }}
          >
            <X size={12} />
          </button>
        </span>
      </div>

      {!minimized && (
        <div style={{ padding: 14, overflow: "auto", flex: 1 }}>
          <DrillIn task={task} compact />
        </div>
      )}
    </div>
  )
}
