import { useState, useEffect } from "react"
import { useParams, Link } from "react-router-dom"
import { api } from "../lib/api"

/* ── Exact color tokens from mockup ─────────────────── */
const C = {
  bg: "#f3f4f6",
  white: "#fff",
  border: "#e5e7eb",
  borderHover: "#d1d5db",
  text1: "#111827",
  text2: "#374151",
  text3: "#6b7280",
  text4: "#9ca3af",
  text5: "#4b5563",
  accent: "#2563eb",
  accentBg: "#eff6ff",
  green: "#16a34a",
  greenBg: "#f0fdf4",
  greenAgent: "#059669",
  greenAgentBg: "#ecfdf5",
  purple: "#8b5cf6",
  amber: "#f59e0b",
  amberBg: "#fffbeb",
  amberAgent: "#d97706",
  pink: "#db2777",
  pinkBg: "#fdf2f8",
  red: "#dc2626",
  redBg: "#fef2f2",
  dimBorder: "#d1d5db",
  cardSep: "#f3f4f6",
  colBg: "#f9fafb",
  pendingBg: "#f3f4f6",
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

/* ── Pipeline stages ────────────────────────────────── */
const PIPELINE_STAGES = [
  { key: "prd", label: "PRD" },
  { key: "stories", label: "Stories" },
  { key: "development", label: "Development" },
  { key: "review", label: "Review" },
  { key: "testing", label: "Testing" },
  { key: "done", label: "Done" },
]

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
  const [activeTab, setActiveTab] = useState("board")

  useEffect(() => { ensureKeyframes() }, [])

  const loadData = async () => {
    if (!requestId) return
    try {
      const res = await api.get(`/requests/${requestId}`)
      setData(res.data)
      setStories(res.data.stories || [])
    } catch {}
  }

  useEffect(() => {
    loadData()
    const interval = setInterval(loadData, 3000)
    return () => clearInterval(interval)
  }, [requestId])

  if (!data) return null

  /* ── Compute pipeline stage states ──────────────── */
  const subtasks = data.subtasks || []
  const storyCountByCol: Record<string, number> = {}
  for (const col of COLUMNS) storyCountByCol[col.key] = stories.filter((s: any) => s.status === col.key).length

  function agentDone(id: string) { return subtasks.some((s: any) => s.agent_id === id && s.status === "completed") }
  function agentActive(id: string) { return subtasks.some((s: any) => s.agent_id === id && s.status === "in_progress") }

  const prdDone = agentDone("prd_specialist")
  const storiesDone = agentDone("user_story_author")
  const devActive = agentActive("backend_specialist") || agentActive("frontend_specialist")
  const devCount = storyCountByCol["in_progress"] || 0
  const reviewCount = storyCountByCol["review"] || 0
  const reviewActive = reviewCount > 0
  const testingCount = storyCountByCol["testing"] || 0
  const doneCount = storyCountByCol["done"] || 0

  type PipeState = "done" | "active" | "waiting"
  const pipeStates: PipeState[] = [
    prdDone ? "done" : "waiting",
    storiesDone ? "done" : "waiting",
    devActive || devCount > 0 ? "active" : (devCount === 0 && doneCount > 0 ? "done" : "waiting"),
    reviewActive ? "active" : (reviewCount === 0 && doneCount > 0 ? "done" : "waiting"),
    testingCount > 0 ? "active" : "waiting",
    doneCount > 0 ? "done" : "waiting",
  ]
  const pipeCounts = ["✓", "✓", String(devCount), String(reviewCount), String(testingCount), String(doneCount)]

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

      {/* ── Breadcrumb ──────────────────────────────── */}
      <div style={{ background: C.white, borderBottom: `1px solid ${C.border}`, padding: "12px 24px", display: "flex", alignItems: "center", gap: 8 }}>
        <Link to="/" style={{ fontSize: 13, color: C.accent, textDecoration: "none" }}>Command Center</Link>
        <span style={{ color: C.dimBorder, fontSize: 12 }}>▸</span>
        <span style={{ fontSize: 13, color: C.text2, fontWeight: 600 }}>
          {data.request_id}: {data.description}
        </span>
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

      {/* ── Pipeline overview ───────────────────────── */}
      <div style={{ background: C.white, borderBottom: `1px solid ${C.border}`, padding: "16px 24px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {PIPELINE_STAGES.map((stage, i) => {
            const state = pipeStates[i]
            const dotBg = state === "done" ? C.green : state === "active" ? C.accent : C.border
            const dotColor = state === "waiting" ? C.text4 : "#fff"
            const labelColor = state === "done" ? C.green : state === "active" ? C.accent : C.text3
            const labelWeight = state === "active" ? 600 : 400
            return (
              <div key={stage.key} style={{ display: "contents" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <div style={{
                    width: 28, height: 28, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center",
                    fontSize: 12, fontWeight: 700, background: dotBg, color: dotColor,
                    animation: state === "active" ? "sb-pulse 2s ease-in-out infinite" : "none",
                  }}>
                    {pipeCounts[i]}
                  </div>
                  <span style={{ fontSize: 11, color: labelColor, fontWeight: labelWeight }}>{stage.label}</span>
                </div>
                {i < PIPELINE_STAGES.length - 1 && (
                  <div style={{
                    width: 40, height: 2,
                    background: connectorState(i) === "done" ? C.green : connectorState(i) === "active" ? C.accent : C.border,
                  }} />
                )}
              </div>
            )
          })}
        </div>

        {/* Stats row */}
        <div style={{ display: "flex", gap: 24, marginTop: 12 }}>
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
        </div>
      </div>

      {/* ── Tab bar ─────────────────────────────────── */}
      <div style={{ background: C.white, borderBottom: `1px solid ${C.border}`, padding: "0 24px", display: "flex", gap: 4 }}>
        {[
          { key: "board", label: "Story Board", count: stories.length },
          { key: "timeline", label: "Agent Timeline", count: 0 },
          { key: "outputs", label: "Outputs", count: 0 },
          { key: "tests", label: "Test Report", count: 0 },
        ].map((tab) => (
          <div
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            style={{
              padding: "12px 16px", fontSize: 13, fontWeight: 500, cursor: "pointer",
              color: activeTab === tab.key ? C.accent : C.text3,
              borderBottom: `2px solid ${activeTab === tab.key ? C.accent : "transparent"}`,
              transition: "all 0.15s",
            }}
          >
            {tab.label}
            {tab.key === "board" && (
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

      {/* ── Kanban board ────────────────────────────── */}
      {activeTab === "board" && (
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

      {/* PR badge */}
      {isDone && (
        <div style={{
          display: "inline-flex", alignItems: "center", gap: 4,
          padding: "3px 8px", borderRadius: 6, fontSize: 11, marginTop: 8,
          background: C.greenBg, color: C.green,
        }}>
          ✓ PR #43 — Merged
        </div>
      )}
      {isReview && (
        <div style={{
          display: "inline-flex", alignItems: "center", gap: 4,
          padding: "3px 8px", borderRadius: 6, fontSize: 11, marginTop: 8,
          background: C.accentBg, color: C.accent,
        }}>
          🔗 PR #46 — Under Review
        </div>
      )}
      {isInProgress && s.coverage_pct != null && (
        <div style={{
          display: "inline-flex", alignItems: "center", gap: 4,
          padding: "3px 8px", borderRadius: 6, fontSize: 11, marginTop: 8,
          background: C.accentBg, color: C.accent,
        }}>
          🔗 PR #{43 + stories_pr_offset(s.story_id)} — Open
        </div>
      )}
      {isInProgress && s.coverage_pct == null && (
        <div style={{
          display: "inline-flex", alignItems: "center", gap: 4,
          padding: "3px 8px", borderRadius: 6, fontSize: 11, marginTop: 8,
          background: C.pendingBg, color: C.text4,
        }}>
          No PR yet
        </div>
      )}

      {/* Reviewer comment (Review column only) */}
      {isReview && (
        <>
          <div style={{ height: 1, background: C.cardSep, margin: "8px 0" }} />
          <div style={{ fontSize: 11, color: C.purple, marginTop: 4 }}>
            🔍 Code Reviewer: "Clean implementation. Checking edge case for token refresh..."
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

/* Simple PR number offset based on story ID */
function stories_pr_offset(storyId: string): number {
  const match = storyId.match(/(\d+)$/)
  return match ? parseInt(match[1], 10) : 1
}
