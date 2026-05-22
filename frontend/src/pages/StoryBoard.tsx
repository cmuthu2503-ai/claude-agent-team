import { useState, useEffect } from "react"
import { useParams, Link } from "react-router-dom"
import { api } from "../lib/api"
import { ProjectChip } from "../components/projects/ProjectChip"
import { RefreshButton } from "../components/ui/RefreshButton"

/* ── Color tokens — mapped to theme CSS variables so the Story Board
   picks up whatever theme is active (was hardcoded light-mode colors
   that ignored the theme). Layout/Kanban structure unchanged (frozen
   per project convention). Subtle bg variants use color-mix() with
   transparent so they're correctly tinted regardless of the theme's
   accent/success/danger hue. */
const C = {
  bg: "var(--bg-secondary)",
  white: "var(--bg-card)",
  border: "var(--border)",
  borderHover: "var(--border)",
  text1: "var(--text-primary)",
  text2: "var(--text-primary)",
  text3: "var(--text-secondary)",
  text4: "var(--text-muted)",
  text5: "var(--text-primary)",
  accent: "var(--accent)",
  accentBg: "color-mix(in srgb, var(--accent) 12%, transparent)",
  green: "var(--success)",
  greenBg: "color-mix(in srgb, var(--success) 12%, transparent)",
  greenAgent: "var(--success)",
  greenAgentBg: "color-mix(in srgb, var(--success) 14%, transparent)",
  purple: "var(--info)",
  amber: "var(--warning)",
  amberBg: "color-mix(in srgb, var(--warning) 12%, transparent)",
  amberAgent: "var(--warning)",
  pink: "var(--accent)",
  pinkBg: "color-mix(in srgb, var(--accent) 12%, transparent)",
  red: "var(--danger)",
  redBg: "color-mix(in srgb, var(--danger) 12%, transparent)",
  dimBorder: "var(--border)",
  cardSep: "var(--bg-secondary)",
  colBg: "var(--bg-hover)",
  pendingBg: "var(--bg-hover)",
}

const FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"

/* ── Column config ──────────────────────────────────── */
const COLUMNS = [
  { key: "todo", label: "To Do", dotClass: C.text3, empty: "All stories have been picked up" },
  { key: "in_progress", label: "In Progress", dotClass: C.accent, empty: "" },
  { key: "review", label: "Review", dotClass: C.purple, empty: "" },
  { key: "testing", label: "Testing", dotClass: C.amber, empty: "Stories move here after code review approval" },
  { key: "done", label: "Done", dotClass: C.green, empty: "" },
]

/* ── Agent badge colors ─────────────────────────────── */
const AGENT_STYLES: Record<string, { bg: string; color: string; label: string }> = {
  backend_specialist: { bg: C.greenAgentBg, color: C.greenAgent, label: "Backend Specialist" },
  frontend_specialist: { bg: C.pinkBg, color: C.pink, label: "Frontend Specialist" },
  tester_specialist: { bg: C.amberBg, color: C.amberAgent, label: "Tester Specialist" },
  code_reviewer: { bg: C.accentBg, color: C.accent, label: "Code Reviewer" },
}

/* ── Workflow stage typing ───────────────────────────
   The pipeline bar is now WORKFLOW-DRIVEN: it reads `data.workflow.stages`
   from the backend (see src/api/routes/requests.py::_serialize_workflow)
   instead of hardcoding a 6-stage feature_development shape. This way the
   bar accurately reflects whichever workflow actually ran — bug_fix shows
   its 4 stages, research shows 3, etc.

   Per-stage story counts (devCount/reviewCount/etc.) only apply when the
   workflow also produces story records (i.e. has a user_story_author stage). */
interface WorkflowStage {
  id: string
  label: string
  agents: string[]
  parallel: boolean
  system: boolean   // true if no agents (handled by orchestrator, e.g. code_commit)
}
interface WorkflowDef {
  id: string
  trigger: string
  produces_stories: boolean
  stages: WorkflowStage[]
}

/* ── Inline keyframe styles (injected once) ─────────── */
const STYLE_ID = "storyboard-keyframes"
function ensureKeyframes() {
  if (document.getElementById(STYLE_ID)) return
  const style = document.createElement("style")
  style.id = STYLE_ID
  style.textContent = `
    @keyframes sb-pulse { 0%,100% { box-shadow: 0 0 0 0 rgba(37,99,235,0.3); } 50% { box-shadow: 0 0 0 6px rgba(37,99,235,0); } }
    @keyframes sb-blink { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
    @keyframes sb-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
  `
  document.head.appendChild(style)
}

/* ── Type badge ─────────────────────────────────────── */
const TYPE_STYLES: Record<string, { bg: string; color: string }> = {
  feature_request: { bg: C.accentBg, color: C.accent },
  bug_report: { bg: C.redBg, color: C.red },
  doc_request: { bg: C.pendingBg, color: C.text3 },
  demo_request: { bg: C.amberBg, color: C.amberAgent },
}
const TYPE_LABELS: Record<string, string> = {
  feature_request: "Feature",
  bug_report: "Bug",
  doc_request: "Docs",
  demo_request: "Demo",
}

/* ── Helper: time ago ───────────────────────────────── */
function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 60) return `${mins} minute${mins !== 1 ? "s" : ""} ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs} hour${hrs !== 1 ? "s" : ""} ago`
  const days = Math.floor(hrs / 24)
  return `${days} day${days !== 1 ? "s" : ""} ago`
}

/* ════════════════════════════════════════════════════════
   MAIN COMPONENT
   ════════════════════════════════════════════════════════ */
export function StoryBoardPage() {
  const { requestId } = useParams()
  const [data, setData] = useState<any>(null)
  const [stories, setStories] = useState<any[]>([])
  // Default tab: "board" for story-producing workflows, "timeline" otherwise.
  // The initial value gets corrected by the load-data effect once the workflow
  // info arrives. If the user manually picks a tab we never override their choice.
  const [activeTab, setActiveTab] = useState("board")
  const [userPickedTab, setUserPickedTab] = useState(false)

  useEffect(() => { ensureKeyframes() }, [])

  // Manual refresh flag — toggled only by the Refresh button, not by
  // the 3s polling interval, so the spinner doesn't flash on every
  // background fetch.
  const [refreshing, setRefreshing] = useState(false)

  const loadData = async () => {
    if (!requestId) return
    try {
      const res = await api.get(`/requests/${requestId}`)
      setData(res.data)
      setStories(res.data.stories || [])
    } catch {}
  }

  const handleRefresh = async () => {
    setRefreshing(true)
    try { await loadData() } finally { setRefreshing(false) }
  }

  useEffect(() => {
    loadData()
    const interval = setInterval(loadData, 3000)
    return () => clearInterval(interval)
  }, [requestId])

  // Auto-pick the sensible default tab the first time workflow info
  // arrives. Lives in its own effect (NOT inside loadData) so that the
  // 3s polling interval can't keep resetting the user's tab choice via
  // a stale `userPickedTab` closure — that was the root cause of the
  // Agent Timeline / Outputs tabs snapping back to Story Board every
  // few seconds.
  useEffect(() => {
    if (userPickedTab) return
    if (!data?.workflow) return
    setActiveTab(data.workflow.produces_stories ? "board" : "timeline")
  }, [data?.workflow?.produces_stories, userPickedTab])

  if (!data) return null

  /* ── Compute pipeline stage states (workflow-driven) ──────────────────
     The pipeline bar's stage list comes from the backend's workflow info
     (data.workflow.stages). For each stage, we compute state from the
     subtask statuses of the agents that stage runs.

     Story counts (devCount/reviewCount/testingCount) overlay on top for
     stages whose ID matches the story-status vocabulary. Other stages
     just show ✓/active/empty based on agent run state. */
  const subtasks = data.subtasks || []
  const workflow: WorkflowDef | null = data.workflow || null

  const storyCountByCol: Record<string, number> = {}
  for (const col of COLUMNS) {
    storyCountByCol[col.key] = stories.filter((s: any) => s.status === col.key).length
  }
  const doneCount = storyCountByCol["done"] || 0

  // Helper: aggregate subtask statuses for an agent (a single agent may have
  // multiple subtask rows from rework cycles; "done" means at least one finished).
  function agentRunState(agentId: string): "done" | "active" | "waiting" {
    const rows = subtasks.filter((s: any) => s.agent_id === agentId)
    if (rows.some((s: any) => s.status === "in_progress")) return "active"
    if (rows.some((s: any) => s.status === "completed")) return "done"
    return "waiting"
  }

  type PipeState = "done" | "active" | "waiting" | "failed"

  // Special handling for stages whose ID matches a story-status bucket — when
  // a workflow produces stories, the per-bucket count is more useful than just
  // "agents completed?". Maps stage.id → status bucket and count.
  const stageStoryBucket: Record<string, string> = {
    development: "in_progress",
    review:      "review",
    testing:     "testing",
  }

  // For deployment-shaped stages, the truth is in the supervisor's
  // deployment_states row (data.deployment), not the agent's subtask status.
  // A "completed" devops_specialist subtask doesn't mean a deploy happened —
  // the agent might have just observed the supervisor's "skipped" or
  // "on_hold" decision. Map the supervisor's current_step to pipeline state.
  const deployment = data.deployment as null | {
    current_step?: string
    strategy?: string
  }
  function deploymentDerivedState(): PipeState {
    if (!deployment) return "waiting"
    const step = deployment.current_step || ""
    if (step === "completed" || step === "rolled_back") return "done"
    if (step === "failed" || step === "on_hold") return "done" // terminal, even if bad — render as ✓ and let label/tooltip show why
    if (step === "code_committed") return "waiting"            // supervisor hasn't picked it up
    return "active"                                            // judging / building / staging_deploying / prod_deploying
  }

  function computeStageState(stage: WorkflowStage): PipeState {
    // Deployment/hotfix_deploy stages use the supervisor's real state, NOT
    // the devops_specialist subtask status. Agent's subtask being "completed"
    // just means it wrote a report — the actual deploy decision is in
    // deployment_states.
    if (stage.id === "deployment" || stage.id === "hotfix_deploy") {
      return deploymentDerivedState()
    }
    // System stages (no agents, e.g. code_commit, publish) — derive state from
    // the actual artifact/failure signals on the request, since they have no
    // subtask row to inspect.
    if (stage.system) {
      const artifacts = data.artifacts || {}
      // For code_commit specifically: if the backend persisted a
      // code_commit_error on the request, the commit was rejected (truncation,
      // ruff, tsc, etc.). Render the stage as "failed" so the user can see
      // immediately WHERE the workflow died instead of staring at "tester
      // completed" and wondering why nothing else happened.
      if (stage.id === "code_commit" && (data as any).code_commit_error) {
        return "failed"
      }
      const hasOutput = artifacts.commit_sha || (artifacts.published_files || []).length > 0
      return hasOutput ? "done" : "waiting"
    }
    // Multi-agent stages (parallel or sequential): done iff every listed agent
    // completed; active iff any agent is in_progress; waiting otherwise.
    const states = stage.agents.map(agentRunState)
    if (states.length === 0) return "waiting"
    if (states.every((s) => s === "done")) return "done"
    if (states.some((s) => s === "active")) return "active"
    // Story-flow special case: when no agent is currently active, but the
    // workflow has produced "done" stories, intermediate development/review/
    // testing stages should still read as "done" (stories moved past them).
    const bucket = stageStoryBucket[stage.id]
    if (bucket && (storyCountByCol[bucket] || 0) === 0 && doneCount > 0) return "done"
    return "waiting"
  }

  // Fallback for older API responses where workflow isn't present yet.
  const stages: WorkflowStage[] = workflow?.stages || []
  const pipeStates: PipeState[] = stages.map(computeStageState)

  // Label inside each stage circle. Rule: "done" → ✓; "active" with a story
  // bucket → show the count; otherwise just blank for waiting (cleaner than
  // showing "0" everywhere).
  const pipeCounts: string[] = stages.map((stage, i) => {
    const state = pipeStates[i]
    if (state === "done") return "✓"
    const bucket = stageStoryBucket[stage.id]
    if (bucket && workflow?.produces_stories) {
      return String(storyCountByCol[bucket] || 0)
    }
    if (state === "active") return "…"
    return ""
  })

  /* ── Compute stats ─────────────────────────────── */
  let totalTests = 0, passedTests = 0, totalCoverage = 0, coverageCount = 0
  for (const s of stories) {
    const tcs = s.test_cases || []
    totalTests += tcs.length
    passedTests += tcs.filter((t: any) => t.status === "pass").length
    if (s.coverage_pct != null) { totalCoverage += s.coverage_pct; coverageCount++ }
  }
  const avgCoverage = coverageCount > 0 ? Math.round(totalCoverage / coverageCount) : 0

  /* ── Connector state ───────────────────────────── */
  function connectorState(i: number): PipeState {
    if (pipeStates[i] === "done" && (pipeStates[i + 1] === "done" || pipeStates[i + 1] === "active")) return "done"
    if (pipeStates[i + 1] === "active") return "active"
    return "waiting"
  }

  return (
    <div style={{ fontFamily: FONT, background: C.bg, color: C.text2, minHeight: "calc(100vh - 52px)" }}>

      {/* ── Breadcrumb ────────────────────────────────
          Just shows the location (Command Center > REQ-ID). The full
          description used to be repeated here too, which then appeared
          a second time in the request header below — confusing and
          visually noisy. Trimmed to just the ID. */}
      <div style={{ background: C.white, borderBottom: `1px solid ${C.border}`, padding: "12px 24px", display: "flex", alignItems: "center", gap: 8 }}>
        <Link to="/" style={{ fontSize: 13, color: C.accent, textDecoration: "none" }}>Command Center</Link>
        <span style={{ color: C.dimBorder, fontSize: 12 }}>▸</span>
        <ProjectChip projectId={data.project_id} variant="compact" stopPropagation={false} />
        <span style={{ color: C.dimBorder, fontSize: 12 }}>▸</span>
        <span style={{ fontSize: 13, color: C.text2, fontWeight: 600 }}>
          {data.request_id}
        </span>
        {/* Refresh — right-aligned like the per-project board */}
        <div style={{ marginLeft: "auto" }}>
          <RefreshButton onClick={handleRefresh} refreshing={refreshing} />
        </div>
      </div>

      {/* ── Request header ──────────────────────────── */}
      <div style={{ background: C.white, borderBottom: `1px solid ${C.border}`, padding: "20px 24px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
          <span style={{ fontSize: 13, fontWeight: 700, color: C.accent }}>{data.request_id}</span>
          <span style={{ fontSize: 18, fontWeight: 700, color: C.text1 }}>{data.description}</span>
          <span style={{
            padding: "3px 10px", borderRadius: 6, fontSize: 11, fontWeight: 600,
            background: (TYPE_STYLES[data.task_type] || TYPE_STYLES.feature_request).bg,
            color: (TYPE_STYLES[data.task_type] || TYPE_STYLES.feature_request).color,
          }}>
            {TYPE_LABELS[data.task_type] || "Feature"}
          </span>
        </div>
        <div style={{ fontSize: 12, color: C.text4, display: "flex", gap: 16, marginTop: 4 }}>
          {data.created_by && <span>Submitted by {data.created_by}</span>}
          {data.created_at && <span>{timeAgo(data.created_at)}</span>}
          {data.priority && <span>Priority: {data.priority.charAt(0).toUpperCase() + data.priority.slice(1)}</span>}
        </div>
      </div>

      {/* ── Pipeline overview (workflow-driven) ─────── */}
      <div style={{ background: C.white, borderBottom: `1px solid ${C.border}`, padding: "16px 24px" }}>
        {stages.length === 0 && (
          <div style={{ fontSize: 12, color: C.text3 }}>
            Workflow definition unavailable. Pipeline visualization disabled.
          </div>
        )}
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          {stages.map((stage, i) => {
            const state = pipeStates[i]
            // "failed" gets a red dot with an X — distinct from waiting (gray)
            // and done (green) so the user can see at a glance WHERE the
            // pipeline died.
            // "done" uses var(--info) (cyan in cyberpunk, blue/teal in
            // other themes) instead of the bright matrix-green --success
            // token so completed stages match the page's primary accent
            // hierarchy rather than competing with it.
            const dotBg = state === "failed" ? "#dc2626"
                        : state === "done" ? "var(--info)"
                        : state === "active" ? C.accent
                        : C.border
            const dotColor = state === "waiting" ? C.text4 : "#fff"
            const labelColor = state === "failed" ? "#dc2626"
                             : state === "done" ? "var(--info)"
                             : state === "active" ? C.accent
                             : C.text3
            const labelWeight = (state === "active" || state === "failed") ? 600 : 400
            const dotContent = state === "failed" ? "✗" : pipeCounts[i]
            const tooltip = state === "failed" && stage.id === "code_commit" && (data as any).code_commit_error
              ? `Code commit rejected:\n${(data as any).code_commit_error}`
              : `Agents: ${stage.agents.join(", ") || "(system)"}`
            return (
              <div key={stage.id} style={{ display: "contents" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }} title={tooltip}>
                  <div style={{
                    width: 28, height: 28, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center",
                    fontSize: 12, fontWeight: 700, background: dotBg, color: dotColor,
                    animation: state === "active" ? "sb-pulse 2s ease-in-out infinite" : "none",
                  }}>
                    {dotContent}
                  </div>
                  <span style={{ fontSize: 11, color: labelColor, fontWeight: labelWeight }}>{stage.label}</span>
                </div>
                {i < stages.length - 1 && (
                  <div style={{
                    width: 40, height: 2,
                    background: connectorState(i) === "done" ? "var(--info)" : connectorState(i) === "active" ? C.accent : C.border,
                  }} />
                )}
              </div>
            )
          })}
        </div>

        {/* Code-commit failure banner — show the actual CodeWriter rejection
            reason directly under the pipeline so users don't have to hover the
            ✗ dot to discover what broke. This is the most common cause of a
            request "stuck after testing" — Phase D surfaces it inline. */}
        {(data as any).code_commit_error && (
          <div style={{
            marginTop: 12, padding: "10px 14px", borderRadius: 6,
            background: "#fef2f2", border: "1px solid #fecaca",
          }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: "#991b1b", marginBottom: 4 }}>
              ✗ Code commit rejected
            </div>
            <div style={{
              fontSize: 11, color: "#7f1d1d", fontFamily: "ui-monospace, monospace",
              whiteSpace: "pre-wrap", lineHeight: 1.5,
            }}>
              {(data as any).code_commit_error}
            </div>
          </div>
        )}

        {/* Stats row — story-aware. For workflows that don't produce stories
            (bug_fix, research, content, etc.) we show subtask-based stats
            instead, since "stories: 0" / "coverage: 0%" would be misleading. */}
        <div style={{ display: "flex", gap: 24, marginTop: 12 }}>
          {workflow?.produces_stories ? (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: C.text3 }}>
                Stories: <span style={{ fontWeight: 700, color: C.text1, fontSize: 14 }}>{stories.length}</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: C.text3 }}>
                Tests: <span style={{ fontWeight: 700, color: C.accent, fontSize: 14 }}>{passedTests}/{totalTests}</span> passing
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: C.text3 }}>
                Coverage: <span style={{ fontWeight: 700, color: C.green, fontSize: 14 }}>{avgCoverage}%</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: C.text3 }}>
                PRs: <span style={{ fontWeight: 700, color: C.text1, fontSize: 14 }}>
                  {stories.filter((s: any) => s.github_issue_number).length || stories.filter((s: any) => s.status !== "todo" && s.status !== "done").length}
                </span> open
              </div>
            </>
          ) : (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: C.text3 }}>
                Workflow: <span style={{ fontWeight: 700, color: C.text1, fontSize: 13 }}>{workflow?.id || "—"}</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: C.text3 }}>
                Subtasks: <span style={{ fontWeight: 700, color: C.text1, fontSize: 14 }}>
                  {subtasks.filter((s: any) => s.status === "completed").length}/{subtasks.length}
                </span> completed
              </div>
              <div style={{ fontSize: 12, color: C.text4, fontStyle: "italic" }}>
                This workflow doesn't produce user stories — see the Agent Timeline below.
              </div>
            </>
          )}
        </div>
      </div>

      {/* ── Tab bar ───────────────────────────────────
          "Story Board" tab is hidden when the workflow doesn't produce stories
          (bug_fix, research, content, etc.) — there's nothing for it to show.
          "Agent Timeline" becomes the default in that case. */}
      <div style={{ background: C.white, borderBottom: `1px solid ${C.border}`, padding: "0 24px", display: "flex", gap: 4 }}>
        {[
          ...(workflow?.produces_stories
            ? [{ key: "board", label: "Story Board", count: stories.length }]
            : []),
          { key: "timeline", label: "Agent Timeline", count: subtasks.length },
          { key: "tests", label: "Test Coverage", count: totalTests },
          { key: "outputs", label: "Outputs", count: 0 },
        ].map((tab) => (
          <div
            key={tab.key}
            onClick={() => { setActiveTab(tab.key); setUserPickedTab(true) }}
            style={{
              padding: "12px 16px", fontSize: 13, fontWeight: 500, cursor: "pointer",
              color: activeTab === tab.key ? C.accent : C.text3,
              borderBottom: `2px solid ${activeTab === tab.key ? C.accent : "transparent"}`,
              transition: "all 0.15s",
            }}
          >
            {tab.label}
            {(tab.key === "board" || tab.key === "timeline" || tab.key === "tests") && tab.count > 0 && (
              <span style={{
                background: C.accentBg, color: C.accent, fontSize: 11, fontWeight: 600,
                padding: "1px 6px", borderRadius: 8, marginLeft: 6,
              }}>
                {tab.count}
              </span>
            )}
          </div>
        ))}
      </div>

      {/* ── Outputs tab ─────────────────────────────── */}
      {activeTab === "outputs" && <OutputsTab data={data} />}

      {/* ── Test Coverage tab ──────────────────────── */}
      {activeTab === "tests" && <TestCoverageTab stories={stories} />}

      {/* ── Agent Timeline tab ──────────────────────── */}
      {activeTab === "timeline" && <AgentTimelineTab subtasks={subtasks} workflow={workflow} />}

      {/* ── Kanban board ──────────────────────────────
          Only renders when the workflow produces stories. Otherwise the user
          sees Agent Timeline instead. */}
      {activeTab === "board" && workflow?.produces_stories && (
      <div style={{
        display: "flex", gap: 16, padding: "20px 24px", overflowX: "auto",
        minHeight: "calc(100vh - 340px)", alignItems: "flex-start",
      }}>
        {COLUMNS.map((col) => {
          const colStories = stories.filter((s: any) => s.status === col.key)
          return (
            <div key={col.key} style={{
              minWidth: 280, maxWidth: 320, flex: 1,
              background: C.colBg, borderRadius: 12, border: `1px solid ${C.border}`,
            }}>
              {/* Column header */}
              <div style={{ padding: "14px 16px 10px", display: "flex", alignItems: "center", gap: 8 }}>
                <div style={{ width: 10, height: 10, borderRadius: "50%", background: col.dotClass }} />
                <span style={{ fontSize: 13, fontWeight: 600, color: C.text2 }}>{col.label}</span>
                <span style={{ fontSize: 12, color: C.text4, marginLeft: "auto" }}>{colStories.length}</span>
              </div>

              {/* Column body */}
              <div style={{ padding: "4px 8px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
                {colStories.length === 0 && col.empty && (
                  <div style={{ padding: 20, textAlign: "center", fontSize: 12, color: C.text4 }}>
                    {col.empty}
                  </div>
                )}
                {colStories.map((s: any) => (
                  <StoryCard key={s.story_id} story={s} column={col.key} />
                ))}
              </div>
            </div>
          )
        })}
      </div>
      )}
    </div>
  )
}

/* ════════════════════════════════════════════════════════
   OUTPUTS TAB — list of artifacts produced (no content)
   ════════════════════════════════════════════════════════ */
const DOC_TYPE_LABELS: Record<string, string> = {
  prd: "PRD Document",
  user_stories: "User Stories",
  backend_code: "Backend Code",
  frontend_code: "Frontend Code",
  code_review: "Code Review Report",
  test_report: "Test Report",
  deploy_report: "Deployment Report",
  research_report: "Research Report",
  content_artifact: "Content Artifact",
}

function fileIconChar(path: string): string {
  const ext = path.toLowerCase().split('.').pop() || ''
  if (['md', 'markdown'].includes(ext)) return '📄'
  if (ext === 'pdf') return '📕'
  if (['pptx', 'ppt'].includes(ext)) return '📊'
  if (['png', 'jpg', 'jpeg', 'svg', 'gif'].includes(ext)) return '🖼️'
  if (ext === 'mmd') return '🧩'
  return '📎'
}

function fileLabelOnly(path: string): string {
  return path.split('/').pop() || path
}

/* ── Agent Timeline tab ──────────────────────────────
   Shown by default for workflows that don't produce stories (bug_fix,
   research, content, demo, docs). Lists every subtask in chronological
   order with status + duration. For workflows with stories, this tab is
   still available as an alternative to the Kanban view. */
function AgentTimelineTab({
  subtasks, workflow,
}: { subtasks: any[]; workflow: WorkflowDef | null }) {
  if (!subtasks || subtasks.length === 0) {
    return (
      <div style={{ padding: 32, color: C.text4, fontSize: 13, textAlign: "center" }}>
        No agent activity recorded yet for this request.
      </div>
    )
  }

  // Build a stage lookup for the agent badges: which workflow stage did this
  // agent run as part of? Only used for the small "stage" hint per row.
  const agentToStage: Record<string, string> = {}
  for (const st of workflow?.stages || []) {
    for (const aid of st.agents) {
      // First mention wins — same agent might appear in multiple stages but
      // typically runs once per workflow.
      if (!agentToStage[aid]) agentToStage[aid] = st.label
    }
  }

  // Subtasks come from the API in insertion order. For chronological display
  // we sort by started_at when present (some subtasks haven't started yet).
  const sorted = [...subtasks].sort((a, b) => {
    if (!a.started_at && !b.started_at) return 0
    if (!a.started_at) return 1
    if (!b.started_at) return -1
    return a.started_at.localeCompare(b.started_at)
  })

  return (
    <div style={{ padding: "20px 24px", display: "flex", flexDirection: "column", gap: 8 }}>
      {sorted.map((s: any) => {
        const agentStyle = AGENT_STYLES[s.agent_id] || { bg: C.pendingBg, color: C.text2, label: s.agent_id }
        const stageLabel = agentToStage[s.agent_id]
        const duration = s.started_at && s.completed_at
          ? formatDuration(s.started_at, s.completed_at)
          : null
        const statusStyle = STATUS_STYLES[s.status] || STATUS_STYLES.pending
        return (
          <div
            key={s.subtask_id}
            style={{
              display: "grid",
              gridTemplateColumns: "auto 1fr auto auto",
              alignItems: "center",
              gap: 12,
              padding: "10px 14px",
              background: C.white,
              border: `1px solid ${C.border}`,
              borderRadius: 8,
            }}
          >
            <span style={{
              fontSize: 11, fontWeight: 600, padding: "3px 8px", borderRadius: 4,
              background: agentStyle.bg, color: agentStyle.color, whiteSpace: "nowrap",
            }}>
              {agentStyle.label}
            </span>
            <span style={{ fontSize: 12, color: C.text3 }}>
              {stageLabel ? `Stage: ${stageLabel}` : "—"}
              {s.started_at && (
                <span style={{ marginLeft: 8, color: C.text4 }}>
                  · started {timeAgo(s.started_at)}
                </span>
              )}
            </span>
            <span style={{
              fontSize: 11, fontWeight: 600, padding: "2px 8px", borderRadius: 4,
              background: statusStyle.bg, color: statusStyle.color, whiteSpace: "nowrap",
            }}>
              {s.status.replace(/_/g, " ")}
            </span>
            <span style={{ fontSize: 11, color: C.text4, minWidth: 60, textAlign: "right", fontFamily: "ui-monospace, monospace" }}>
              {duration || "—"}
            </span>
          </div>
        )
      })}
    </div>
  )
}

const STATUS_STYLES: Record<string, { bg: string; color: string }> = {
  pending:     { bg: C.pendingBg, color: C.text3 },
  in_progress: { bg: C.accentBg, color: C.accent },
  completed:   { bg: C.greenBg, color: C.green },
  failed:      { bg: C.redBg, color: C.red },
  cancelled:   { bg: C.pendingBg, color: C.text4 },
}

function formatDuration(startIso: string, endIso: string): string {
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime()
  if (!isFinite(ms) || ms < 0) return "—"
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const rs = s % 60
  return `${m}m${rs > 0 ? ` ${rs}s` : ""}`
}


function OutputsTab({ data }: { data: any }) {
  const artifacts = data?.artifacts || {}
  const documents = artifacts.documents || []
  const publishedFiles = artifacts.published_files || []
  const commitUrl = artifacts.commit_url
  const commitSha = artifacts.commit_sha

  const hasAnything = documents.length > 0 || publishedFiles.length > 0

  return (
    <div style={{ padding: "20px 24px", maxWidth: 960, margin: "0 auto" }}>
      {!hasAnything && (
        <div style={{
          padding: 40, textAlign: "center", fontSize: 13, color: C.text4,
          background: C.colBg, borderRadius: 12, border: `1px solid ${C.border}`,
        }}>
          No artifacts produced yet. The pipeline must complete first.
        </div>
      )}

      {/* Files Published to GitHub */}
      {publishedFiles.length > 0 && (
        <div style={{
          background: C.white, borderRadius: 12, border: `1px solid ${C.border}`,
          padding: 20, marginBottom: 16,
        }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
            <h3 style={{ fontSize: 14, fontWeight: 700, color: C.text1, margin: 0, textTransform: "uppercase", letterSpacing: 0.5 }}>
              Files Published ({publishedFiles.length})
            </h3>
            {commitUrl && (
              <a
                href={commitUrl}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  display: "inline-flex", alignItems: "center", gap: 6,
                  fontSize: 12, color: C.accent, textDecoration: "none",
                  padding: "5px 12px", borderRadius: 6,
                  background: C.accentBg, fontWeight: 500,
                }}
              >
                ⌥ {commitSha || "view commit"} ↗
              </a>
            )}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {publishedFiles.map((path: string) => (
              <div
                key={path}
                style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "10px 14px", borderRadius: 8,
                  background: C.colBg, border: `1px solid ${C.border}`,
                  fontSize: 13,
                }}
              >
                <span style={{ fontSize: 16 }}>{fileIconChar(path)}</span>
                <span style={{ color: C.text1, fontWeight: 500 }}>
                  {fileLabelOnly(path)}
                </span>
                <span style={{
                  color: C.text4, fontSize: 11, marginLeft: "auto",
                  fontFamily: "ui-monospace, monospace",
                }}>
                  {path}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Documents produced by agents */}
      {documents.length > 0 && (
        <div style={{
          background: C.white, borderRadius: 12, border: `1px solid ${C.border}`,
          padding: 20,
        }}>
          <h3 style={{
            fontSize: 14, fontWeight: 700, color: C.text1, margin: "0 0 14px 0",
            textTransform: "uppercase", letterSpacing: 0.5,
          }}>
            Documents ({documents.length})
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {documents.map((doc: any) => (
              <div
                key={doc.document_id}
                style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "10px 14px", borderRadius: 8,
                  background: C.colBg, border: `1px solid ${C.border}`,
                  fontSize: 13,
                }}
              >
                <span style={{ fontSize: 16 }}>📄</span>
                <span style={{ color: C.text1, fontWeight: 500 }}>
                  {DOC_TYPE_LABELS[doc.doc_type] || doc.doc_type}
                </span>
                <span style={{
                  fontSize: 10, padding: "2px 8px", borderRadius: 6,
                  background: C.accentBg, color: C.accent, fontWeight: 500,
                }}>
                  {doc.agent_id?.replace(/_/g, " ")}
                </span>
                <span style={{
                  color: C.text4, fontSize: 11, marginLeft: "auto",
                  fontFamily: "ui-monospace, monospace",
                }}>
                  v{doc.version}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/* ════════════════════════════════════════════════════════
   STORY CARD
   ════════════════════════════════════════════════════════ */
function StoryCard({ story: s, column }: { story: any; column: string }) {
  const tcs = s.test_cases || []
  const acs = s.acceptance_criteria || []
  const passed = tcs.filter((t: any) => t.status === "pass").length
  const total = tcs.length
  const isDone = column === "done"
  const isInProgress = column === "in_progress"
  const isReview = column === "review"

  const agentStyle = AGENT_STYLES[s.assigned_agent] || null
  const isAgentActive = isInProgress || isReview

  /* Test count badge class */
  let countBg = C.pendingBg, countColor = C.text3
  if (total > 0 && passed === total) { countBg = C.greenBg; countColor = C.green }
  else if (passed > 0) { countBg = C.amberBg; countColor = C.amberAgent }

  /* Coverage bar color */
  const cov = s.coverage_pct
  let covBarColor = C.green, covTextColor = C.green
  if (cov != null && cov < 80) { covBarColor = C.amber; covTextColor = C.amber }
  if (cov != null && cov < 60) { covBarColor = C.red; covTextColor = C.red }

  return (
    <div style={{
      background: C.white, borderRadius: 10, padding: 14,
      boxShadow: "0 1px 3px rgba(0,0,0,0.04)", border: `1px solid ${C.border}`,
      borderLeft: isInProgress ? `3px solid ${C.accent}` : `1px solid ${C.border}`,
      cursor: "pointer", transition: "all 0.15s",
      opacity: isDone ? 0.85 : 1,
    }}
      onMouseEnter={(e) => { e.currentTarget.style.boxShadow = "0 4px 12px rgba(0,0,0,0.08)"; e.currentTarget.style.borderColor = C.borderHover }}
      onMouseLeave={(e) => { e.currentTarget.style.boxShadow = "0 1px 3px rgba(0,0,0,0.04)"; e.currentTarget.style.borderColor = C.border }}
    >
      {/* Story ID */}
      <div style={{ fontSize: 11, fontWeight: 600, color: isDone ? C.green : C.accent, marginBottom: 4 }}>
        {isDone ? "✓ " : ""}{s.story_id}
      </div>

      {/* Title */}
      <div style={{ fontSize: 13, fontWeight: 600, color: C.text1, lineHeight: 1.4, marginBottom: 8 }}>
        {s.title}
      </div>

      {/* Description */}
      {s.description && (
        <div style={{ fontSize: 12, color: C.text3, lineHeight: 1.5, marginBottom: 10 }}>
          {s.description}
        </div>
      )}

      {/* Agent badge */}
      {agentStyle && !isDone && (
        <div style={{
          display: "inline-flex", alignItems: "center", gap: 4,
          padding: "3px 8px", borderRadius: 6, fontSize: 11, fontWeight: 500, marginBottom: 8,
          background: agentStyle.bg, color: agentStyle.color,
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: "50%",
            background: isAgentActive ? "currentColor" : "transparent",
            animation: isAgentActive ? "sb-blink 1.5s ease-in-out infinite" : "none",
          }} />
          {agentStyle.label}
        </div>
      )}

      {/* Separator */}
      <div style={{ height: 1, background: C.cardSep, margin: "8px 0" }} />

      {/* Test cases section */}
      {total > 0 && (
        <div style={{ marginTop: 8 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
            <span style={{ fontSize: 11, fontWeight: 600, color: C.text2, textTransform: "uppercase", letterSpacing: 0.5 }}>
              Test Cases
            </span>
            <span style={{ fontSize: 11, fontWeight: 600, padding: "2px 8px", borderRadius: 6, background: countBg, color: countColor }}>
              {passed}/{total}
            </span>
          </div>
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {tcs.map((tc: any) => {
              const isPassing = tc.status === "pass"
              const isFailing = tc.status === "fail"
              const isRunning = tc.status === "running"
              let iconColor = C.dimBorder
              let icon = "○"
              let nameColor = C.text3
              if (isPassing) { iconColor = C.green; icon = "✓"; nameColor = C.text5 }
              else if (isFailing) { iconColor = C.red; icon = "✗"; nameColor = C.red }
              else if (isRunning) { iconColor = C.accent; icon = "○" }
              return (
                <li key={tc.test_id} style={{ display: "flex", alignItems: "flex-start", gap: 6, padding: "3px 0", fontSize: 11, lineHeight: 1.4 }}>
                  <span style={{
                    flexShrink: 0, marginTop: 1, fontSize: 12, color: iconColor,
                    animation: isRunning ? "sb-spin 1s linear infinite" : "none",
                    display: "inline-block",
                  }}>
                    {icon}
                  </span>
                  <span style={{ color: nameColor }}>{tc.name}</span>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {/* Coverage bar */}
      {cov != null && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
          <span style={{ fontSize: 11, color: C.text3 }}>Coverage</span>
          <div style={{ flex: 1, height: 4, background: C.border, borderRadius: 2, overflow: "hidden" }}>
            <div style={{ height: "100%", borderRadius: 2, background: covBarColor, width: `${cov}%` }} />
          </div>
          <span style={{ fontSize: 11, fontWeight: 600, color: covTextColor }}>{cov}%</span>
        </div>
      )}

      {/* PR badge — uses the real GitHub issue/PR number from the story
          record (s.github_issue_number, populated by the publisher when the
          PR is opened). No fake numbers: if the story doesn't have a real
          PR yet, show "No PR yet" instead of a synthesized one. */}
      {isDone && s.github_issue_number && (
        <div style={{
          display: "inline-flex", alignItems: "center", gap: 4,
          padding: "3px 8px", borderRadius: 6, fontSize: 11, marginTop: 8,
          background: C.greenBg, color: C.green,
        }}>
          ✓ PR #{s.github_issue_number} — Merged
        </div>
      )}
      {isReview && s.github_issue_number && (
        <div style={{
          display: "inline-flex", alignItems: "center", gap: 4,
          padding: "3px 8px", borderRadius: 6, fontSize: 11, marginTop: 8,
          background: C.accentBg, color: C.accent,
        }}>
          🔗 PR #{s.github_issue_number} — Under Review
        </div>
      )}
      {isInProgress && s.github_issue_number && (
        <div style={{
          display: "inline-flex", alignItems: "center", gap: 4,
          padding: "3px 8px", borderRadius: 6, fontSize: 11, marginTop: 8,
          background: C.accentBg, color: C.accent,
        }}>
          🔗 PR #{s.github_issue_number} — Open
        </div>
      )}
      {(isInProgress || isReview || isDone) && !s.github_issue_number && (
        <div style={{
          display: "inline-flex", alignItems: "center", gap: 4,
          padding: "3px 8px", borderRadius: 6, fontSize: 11, marginTop: 8,
          background: C.pendingBg, color: C.text4,
        }}>
          No PR yet
        </div>
      )}

      {/* Reviewer comment — only render if the story actually carries a
          review_comment from the backend. The placeholder string that
          used to live here ("Clean implementation. Checking edge case
          for token refresh...") was demo text shown on every review
          story regardless of what the reviewer agent said. */}
      {isReview && s.review_comment && (
        <>
          <div style={{ height: 1, background: C.cardSep, margin: "8px 0" }} />
          <div style={{ fontSize: 11, color: C.purple, marginTop: 4 }}>
            🔍 Code Reviewer: "{s.review_comment}"
          </div>
        </>
      )}

      {/* Acceptance criteria (Done column only) */}
      {isDone && acs.length > 0 && (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, marginTop: 8 }}>
          {acs.map((ac: any) => (
            <li key={ac.ac_id} style={{ display: "flex", alignItems: "flex-start", gap: 6, padding: "2px 0", fontSize: 11, color: C.text3 }}>
              <span style={{
                width: 14, height: 14, borderRadius: 4, flexShrink: 0, marginTop: 1, fontSize: 9,
                display: "flex", alignItems: "center", justifyContent: "center",
                border: ac.is_met ? `1.5px solid ${C.green}` : `1.5px solid ${C.dimBorder}`,
                background: ac.is_met ? C.green : "transparent",
                color: ac.is_met ? "#fff" : "transparent",
              }}>
                ✓
              </span>
              {ac.criterion_text}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}


/* ── Test Coverage tab ───────────────────────────────
   Aggregates every test_case across every story into a single dashboard:
   top summary cards (total / pass / fail / running+pending / avg coverage),
   pass-rate bar, a per-story breakdown table, and an explicit "Failing
   Tests" callout so regressions are impossible to miss. */
function TestCoverageTab({ stories }: { stories: any[] }) {
  // Flatten every test case across every story, tagging each with its
  // parent story so the failing-tests list can show story context.
  type TC = {
    test_id?: string
    name: string
    status: string  // "pass" | "fail" | "running" | "pending" (others = pending)
    story_id?: string
    story_title?: string
  }
  const allTests: TC[] = []
  for (const s of stories) {
    for (const tc of s.test_cases || []) {
      allTests.push({
        test_id: tc.test_id,
        name: tc.name,
        status: tc.status || "pending",
        story_id: s.story_id,
        story_title: s.title,
      })
    }
  }

  const total = allTests.length
  const passed = allTests.filter((t) => t.status === "pass").length
  const failed = allTests.filter((t) => t.status === "fail").length
  const running = allTests.filter((t) => t.status === "running").length
  const pending = total - passed - failed - running
  const passRate = total > 0 ? Math.round((passed / total) * 100) : 0
  const failRate = total > 0 ? Math.round((failed / total) * 100) : 0

  // Avg coverage from stories that have a coverage_pct (skip nulls so a
  // single 0% on a not-yet-tested story doesn't drag the mean down).
  const withCoverage = stories.filter((s) => s.coverage_pct != null)
  const avgCoverage = withCoverage.length > 0
    ? Math.round(withCoverage.reduce((acc, s) => acc + (s.coverage_pct || 0), 0) / withCoverage.length)
    : 0

  if (total === 0) {
    return (
      <div style={{ padding: "20px 24px", maxWidth: 960, margin: "0 auto" }}>
        <div style={{
          padding: 40, textAlign: "center", fontSize: 13, color: C.text4,
          background: C.colBg, borderRadius: 12, border: `1px solid ${C.border}`,
        }}>
          No test cases yet. They'll appear here once the Tester agent runs and writes test_cases on each story.
        </div>
      </div>
    )
  }

  const failingTests = allTests.filter((t) => t.status === "fail")
  // Group tests by story for the per-story breakdown table.
  const byStory = stories
    .map((s) => {
      const tcs = (s.test_cases || []) as Array<{ status: string }>
      return {
        story_id: s.story_id,
        title: s.title,
        total: tcs.length,
        passed: tcs.filter((t) => t.status === "pass").length,
        failed: tcs.filter((t) => t.status === "fail").length,
        running: tcs.filter((t) => t.status === "running").length,
        coverage_pct: s.coverage_pct,
      }
    })
    .filter((row) => row.total > 0)

  return (
    <div style={{ padding: "20px 24px", maxWidth: 1100, margin: "0 auto", display: "flex", flexDirection: "column", gap: 16 }}>
      {/* ── Summary cards ── */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(5, minmax(0, 1fr))",
        gap: 12,
      }}>
        <StatCard label="Total tests" value={String(total)} color={C.text1} />
        <StatCard label="Passed" value={`${passed}`} sub={`${passRate}%`} color={C.green} />
        <StatCard label="Failed" value={`${failed}`} sub={`${failRate}%`} color={failed > 0 ? C.red : C.text4} />
        <StatCard label="In flight" value={`${running + pending}`} sub={running > 0 ? `${running} running` : "queued"} color={C.accent} />
        <StatCard label="Avg coverage" value={`${avgCoverage}%`} color={avgCoverage >= 80 ? C.green : avgCoverage >= 50 ? C.amber : C.red} />
      </div>

      {/* ── Pass-rate bar ── */}
      <div style={{
        background: C.white, borderRadius: 12, border: `1px solid ${C.border}`,
        padding: "18px 20px",
      }}>
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "baseline",
          marginBottom: 10,
        }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: C.text2, textTransform: "uppercase", letterSpacing: 0.5 }}>
            Pass rate
          </span>
          <span style={{ fontSize: 12, color: C.text3 }}>
            {passed} / {total} passing
          </span>
        </div>
        <div style={{
          height: 10, background: C.colBg, borderRadius: 5, overflow: "hidden",
          display: "flex",
        }}>
          {passed > 0 && <div style={{ width: `${(passed / total) * 100}%`, background: C.green }} />}
          {failed > 0 && <div style={{ width: `${(failed / total) * 100}%`, background: C.red }} />}
          {running > 0 && <div style={{ width: `${(running / total) * 100}%`, background: C.accent }} />}
          {pending > 0 && <div style={{ width: `${(pending / total) * 100}%`, background: C.text4 }} />}
        </div>
        <div style={{ display: "flex", gap: 14, marginTop: 8, fontSize: 11, color: C.text3, flexWrap: "wrap" }}>
          <LegendDot color={C.green} label={`Pass (${passed})`} />
          <LegendDot color={C.red}   label={`Fail (${failed})`} />
          <LegendDot color={C.accent} label={`Running (${running})`} />
          <LegendDot color={C.text4} label={`Pending (${pending})`} />
        </div>
      </div>

      {/* ── Failing tests callout (only if any) ── */}
      {failingTests.length > 0 && (
        <div style={{
          background: C.redBg, borderRadius: 12, border: `1px solid ${C.red}`,
          padding: "16px 18px",
        }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 8, marginBottom: 10,
            fontSize: 13, fontWeight: 700, color: C.red,
          }}>
            ✗ {failingTests.length} failing {failingTests.length === 1 ? "test" : "tests"}
          </div>
          <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
            {failingTests.map((t, i) => (
              <li key={t.test_id || `${t.story_id}-${i}`} style={{
                display: "flex", alignItems: "center", gap: 10,
                padding: "8px 10px", background: C.white, borderRadius: 6,
                border: `1px solid ${C.border}`, fontSize: 12,
              }}>
                <span style={{ color: C.red, fontSize: 13 }}>✗</span>
                <span style={{ color: C.text1, flex: 1, minWidth: 0 }}>{t.name}</span>
                {t.story_title && (
                  <span style={{
                    fontSize: 10, color: C.text3, textTransform: "uppercase", letterSpacing: 0.5,
                    whiteSpace: "nowrap",
                  }}>
                    {t.story_id || t.story_title.slice(0, 40)}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ── Per-story breakdown table ── */}
      <div style={{
        background: C.white, borderRadius: 12, border: `1px solid ${C.border}`,
        overflow: "hidden",
      }}>
        <div style={{
          padding: "14px 18px", borderBottom: `1px solid ${C.border}`,
          fontSize: 12, fontWeight: 700, color: C.text2,
          textTransform: "uppercase", letterSpacing: 0.5,
        }}>
          Per-story breakdown ({byStory.length} {byStory.length === 1 ? "story" : "stories"})
        </div>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ background: C.colBg, color: C.text3, textTransform: "uppercase", letterSpacing: 0.5 }}>
              <th style={cellHead}>Story</th>
              <th style={{ ...cellHead, textAlign: "right" }}>Tests</th>
              <th style={{ ...cellHead, textAlign: "right" }}>Passed</th>
              <th style={{ ...cellHead, textAlign: "right" }}>Failed</th>
              <th style={{ ...cellHead, textAlign: "right" }}>Pass %</th>
              <th style={{ ...cellHead, textAlign: "right" }}>Coverage</th>
            </tr>
          </thead>
          <tbody>
            {byStory.map((row) => {
              const pct = row.total > 0 ? Math.round((row.passed / row.total) * 100) : 0
              return (
                <tr key={row.story_id} style={{ borderTop: `1px solid ${C.border}` }}>
                  <td style={cellBody}>
                    <div style={{ fontSize: 12, color: C.text1, fontWeight: 600 }}>{row.title || row.story_id}</div>
                    {row.story_id && (
                      <div style={{ fontSize: 10, color: C.text4, fontFamily: "ui-monospace, monospace", marginTop: 2 }}>
                        {row.story_id}
                      </div>
                    )}
                  </td>
                  <td style={{ ...cellBody, textAlign: "right", color: C.text2, fontWeight: 600 }}>{row.total}</td>
                  <td style={{ ...cellBody, textAlign: "right", color: C.green, fontWeight: 600 }}>{row.passed}</td>
                  <td style={{ ...cellBody, textAlign: "right", color: row.failed > 0 ? C.red : C.text4, fontWeight: 600 }}>{row.failed}</td>
                  <td style={{ ...cellBody, textAlign: "right", color: pct === 100 ? C.green : pct >= 50 ? C.amber : C.red, fontWeight: 700 }}>{pct}%</td>
                  <td style={{ ...cellBody, textAlign: "right" }}>
                    {row.coverage_pct != null ? (
                      <span style={{
                        color: row.coverage_pct >= 80 ? C.green : row.coverage_pct >= 50 ? C.amber : C.red,
                        fontWeight: 700,
                      }}>
                        {row.coverage_pct}%
                      </span>
                    ) : (
                      <span style={{ color: C.text4 }}>—</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* ── Full test list (collapsed by default would be nicer; for v1
           we just render everything inline grouped by story). ── */}
      <div style={{
        background: C.white, borderRadius: 12, border: `1px solid ${C.border}`,
        padding: 0, overflow: "hidden",
      }}>
        <div style={{
          padding: "14px 18px", borderBottom: `1px solid ${C.border}`,
          fontSize: 12, fontWeight: 700, color: C.text2,
          textTransform: "uppercase", letterSpacing: 0.5,
        }}>
          All test cases ({total})
        </div>
        {stories
          .filter((s) => (s.test_cases || []).length > 0)
          .map((s) => (
            <div key={s.story_id} style={{ padding: "12px 18px", borderTop: `1px solid ${C.border}` }}>
              <div style={{
                fontSize: 12, fontWeight: 600, color: C.text1, marginBottom: 8,
                display: "flex", alignItems: "center", gap: 8,
              }}>
                <span>{s.title || s.story_id}</span>
                <span style={{
                  fontSize: 10, color: C.text4, fontFamily: "ui-monospace, monospace",
                }}>
                  {s.story_id}
                </span>
              </div>
              <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 4 }}>
                {(s.test_cases || []).map((tc: any) => {
                  const tone = tc.status === "pass" ? C.green
                    : tc.status === "fail" ? C.red
                    : tc.status === "running" ? C.accent
                    : C.text4
                  const icon = tc.status === "pass" ? "✓"
                    : tc.status === "fail" ? "✗"
                    : tc.status === "running" ? "↻"
                    : "○"
                  return (
                    <li key={tc.test_id} style={{
                      display: "flex", alignItems: "center", gap: 8,
                      fontSize: 11, lineHeight: 1.4, color: C.text2,
                    }}>
                      <span style={{ color: tone, fontWeight: 700, width: 12 }}>{icon}</span>
                      <span>{tc.name}</span>
                    </li>
                  )
                })}
              </ul>
            </div>
          ))}
      </div>
    </div>
  )
}

/* Small presentational helpers used by TestCoverageTab. */
function StatCard({
  label,
  value,
  sub,
  color,
}: {
  label: string
  value: string
  sub?: string
  color: string
}) {
  return (
    <div style={{
      background: C.white, borderRadius: 12, border: `1px solid ${C.border}`,
      padding: "14px 16px", display: "flex", flexDirection: "column", gap: 4,
    }}>
      <div style={{ fontSize: 10, fontWeight: 700, color: C.text3, textTransform: "uppercase", letterSpacing: 0.8 }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 700, color, lineHeight: 1 }}>
        {value}
      </div>
      {sub && (
        <div style={{ fontSize: 11, color: C.text3 }}>{sub}</div>
      )}
    </div>
  )
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
      <span style={{ width: 8, height: 8, borderRadius: 4, background: color, display: "inline-block" }} />
      {label}
    </span>
  )
}

const cellHead: React.CSSProperties = {
  padding: "10px 14px",
  fontSize: 10,
  fontWeight: 700,
  textAlign: "left",
  letterSpacing: 0.5,
}
const cellBody: React.CSSProperties = {
  padding: "12px 14px",
  verticalAlign: "top",
}

