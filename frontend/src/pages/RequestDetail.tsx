import { useState, useEffect, useRef } from "react"
import { useParams, Link } from "react-router-dom"
import { api } from "../lib/api"
import { StatusBadge } from "../components/ui/StatusBadge"
import { ArrowLeft, ChevronDown, ChevronRight, FileText, ExternalLink, Github, FileType, Presentation, FileImage, Code, ShieldAlert, ShieldCheck } from "lucide-react"
import { MarkdownRenderer } from "../components/ui/MarkdownRenderer"
import { ProjectChip } from "../components/projects/ProjectChip"

// AET-07 — shape of the latest quality.gate.* event the backend emits
// when the workflow runner evaluates the quality_guardian_approval
// gate via policy_check. Mirrors the contract in src/core/quality_gate.py.
interface QualityGateViolation {
  rule_id: string
  rule_name: string
  severity: "enforce" | "warn" | "info"
  target_path: string | null
  agent_id: string | null
  snippet: string
  rationale: string
  fix_hint: string | null
  lesson_ref: string | null
}
interface QualityGateState {
  verdict: "BLOCK" | "PASS_WITH_WARNINGS" | "PASS"
  violations: QualityGateViolation[]
  summary: {
    enforce_count?: number
    warn_count?: number
    info_count?: number
    total_emissions_checked?: number
  }
  stage?: string
  rework_cycle?: number
  /** ISO timestamp populated when we receive the event. Used to age out
   *  stale blocks if the request transitions to completed/failed. */
  received_at: string
}

// Map filename extension → icon component
function fileIcon(path: string) {
  const ext = path.toLowerCase().split('.').pop() || ''
  if (['md', 'markdown'].includes(ext)) return FileText
  if (ext === 'pdf') return FileType
  if (['pptx', 'ppt'].includes(ext)) return Presentation
  if (['png', 'jpg', 'jpeg', 'svg', 'gif'].includes(ext)) return FileImage
  if (['py', 'ts', 'tsx', 'js', 'jsx', 'java', 'go', 'rs'].includes(ext)) return Code
  return FileText
}

// Friendly label for a published-file path: extract just the filename
function fileLabel(path: string) {
  return path.split('/').pop() || path
}

// Map doc_type → human-readable label
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

export function RequestDetailPage() {
  const { requestId } = useParams()
  const [data, setData] = useState<any>(null)
  const [expandedAgents, setExpandedAgents] = useState<Set<string>>(new Set())
  const [polling, setPolling] = useState(true)
  // AET-07 — latest quality.gate.* state for this request, captured via
  // the WebSocket activity stream. v1 limitation: state is session-only
  // (not persisted in the request row). On page refresh the chip starts
  // empty until the next gate event fires; for an in-flight request
  // currently in rework, that's usually within a few minutes. A future
  // refinement could persist the latest gate decision on the requests
  // table so reloads recover the chip immediately.
  const [qualityGate, setQualityGate] = useState<QualityGateState | null>(null)
  const [gateExpanded, setGateExpanded] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)

  const loadData = async () => {
    if (!requestId) return
    try {
      const res = await api.get(`/requests/${requestId}`)
      setData(res.data)
      if (["completed", "failed"].includes(res.data.status)) {
        setPolling(false)
      }
    } catch {}
  }

  useEffect(() => {
    loadData()
    const interval = setInterval(() => {
      if (polling) loadData()
    }, 3000)
    return () => clearInterval(interval)
  }, [requestId, polling])

  // AET-07 — subscribe to the global activity WebSocket to catch
  // quality.gate.* events as they fire on the workflow runner. The
  // event payload's request_id field gates which events we keep.
  // The backend emits quality.gate.failed when policy_check returns
  // verdict='BLOCK' and quality.gate.passed for PASS / PASS_WITH_WARNINGS.
  // Updating the local state replaces the previous gate snapshot —
  // if a rework cycle's re-evaluation passes, the chip goes green
  // (or disappears) immediately without needing a manual refresh.
  useEffect(() => {
    if (!requestId) return
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/activity`)
    wsRef.current = ws
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (
          (msg.type === "quality.gate.failed" || msg.type === "quality.gate.passed") &&
          msg.data?.request_id === requestId
        ) {
          setQualityGate({
            verdict: msg.data.verdict,
            violations: msg.data.violations || [],
            summary: msg.data.summary || {},
            stage: msg.data.stage,
            rework_cycle: msg.data.rework_cycle,
            received_at: msg.timestamp || new Date().toISOString(),
          })
        }
      } catch {
        // Malformed event; nothing we can do. Other subscribers
        // (BuildChatPanel, SystemHealthPill) catch the same way.
      }
    }
    ws.onerror = () => { /* visible in browser console */ }
    return () => {
      ws.close()
      wsRef.current = null
    }
  }, [requestId])

  // Clear the gate banner when the request reaches a terminal state.
  // A BLOCK that's no longer relevant (request completed or was
  // cancelled) shouldn't keep a red chip up forever; the violations
  // are still in the agent traces below for audit.
  useEffect(() => {
    if (!data) return
    if (["completed", "cancelled"].includes(data.status) && qualityGate?.verdict === "BLOCK") {
      // Don't auto-clear failed-status: if the request failed AT
      // the gate, the chip is the diagnostic.
      if (data.status === "completed") setQualityGate(null)
    }
  }, [data?.status])

  const toggleAgent = (id: string) => {
    setExpandedAgents((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const expandAll = () => {
    if (data?.subtasks) {
      setExpandedAgents(new Set(data.subtasks.map((s: any) => s.subtask_id)))
    }
  }

  const collapseAll = () => setExpandedAgents(new Set())

  if (!data) return <div style={{ padding: 24, color: "var(--text-muted)" }}>Loading...</div>

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: 24, display: "flex", flexDirection: "column", gap: 24 }}>
      <Link to="/" style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 13, color: "var(--text-muted)", textDecoration: "none" }}>
        <ArrowLeft size={14} /> Back to Command Center
      </Link>

      {/* Request Header */}
      <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 24 }}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
          <div>
            <h1 style={{ fontSize: 20, fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>{data.request_id}</h1>
            <div style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-muted)" }}>
              <span>Project:</span>
              <ProjectChip projectId={data.project_id} variant="full" />
            </div>
            <p style={{ marginTop: 8, color: "var(--text-secondary)", fontSize: 14, lineHeight: 1.6 }}>{data.description}</p>
          </div>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8 }}>
            <StatusBadge status={data.status} size="md" />
            {/* AET-07 — Quality gate chip. Renders when the workflow's
                quality_guardian_approval gate emitted a quality.gate.*
                event for this request. Red when BLOCK (with violation
                count); yellow when PASS_WITH_WARNINGS; green when PASS.
                Click to expand the violations panel below the header. */}
            {qualityGate && (
              <QualityGateChip
                state={qualityGate}
                expanded={gateExpanded}
                onClick={() => setGateExpanded(!gateExpanded)}
              />
            )}
          </div>
        </div>
        <div style={{ marginTop: 12, display: "flex", gap: 16, fontSize: 13, color: "var(--text-muted)" }}>
          <span>Type: <span style={{ color: "var(--text-secondary)", textTransform: "capitalize" }}>{data.task_type?.replace("_", " ")}</span></span>
          <span>Priority: <span style={{ color: "var(--text-secondary)", textTransform: "capitalize" }}>{data.priority}</span></span>
          <span>Created: {new Date(data.created_at).toLocaleString()}</span>
          {data.total_cost?.cost_usd > 0 && <span>Cost: ${data.total_cost.cost_usd}</span>}
        </div>
        {/* Expandable violations panel — only mounted when the chip
            is in expanded state. Shows each violation's rule_id +
            severity + file + L11-L21 reference + snippet + fix_hint. */}
        {qualityGate && gateExpanded && (
          <QualityGateViolations state={qualityGate} />
        )}
        {/* Workflow/Story Board link — visible for every request type. The
            Story Board page now renders a workflow-driven pipeline view that
            adapts to whichever workflow the request actually ran (feature,
            bug fix, research, etc.). Label adapts per task_type so users
            know what to expect on the other side. */}
        <div style={{ marginTop: 12 }}>
          <Link to={`/stories/${requestId}`} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 13, color: "var(--accent)", textDecoration: "none" }}>
            <ExternalLink size={13} />
            {" "}
            {data.task_type === "feature_request" ? (
              data.stories?.length > 0
                ? `View Story Board (${data.stories.length} ${data.stories.length === 1 ? "story" : "stories"})`
                : "View Story Board (no stories parsed)"
            ) : (
              `View Workflow Pipeline (${data.subtasks?.length || 0} ${(data.subtasks?.length || 0) === 1 ? "agent run" : "agent runs"})`
            )}
          </Link>
        </div>
      </div>

      {/* Artifacts Panel */}
      {data.artifacts && (data.artifacts.documents?.length > 0 || data.artifacts.published_files?.length > 0) && (
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 24 }}>
          <h2 style={{ fontSize: 18, fontWeight: 600, color: "var(--text-primary)", margin: "0 0 16px 0" }}>
            Artifacts Produced
          </h2>

          {/* Files committed to GitHub */}
          {data.artifacts.published_files?.length > 0 && (
            <div style={{ marginBottom: data.artifacts.documents?.length > 0 ? 20 : 0 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                <h3 style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: 0.5, margin: 0 }}>
                  Files Published ({data.artifacts.published_files.length})
                </h3>
                {data.artifacts.commit_url && (
                  <a
                    href={data.artifacts.commit_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      display: "inline-flex", alignItems: "center", gap: 6,
                      fontSize: 12, color: "var(--accent)", textDecoration: "none",
                      padding: "4px 10px", borderRadius: "var(--radius)",
                      background: "var(--accent-subtle)",
                    }}
                  >
                    <Github size={13} />
                    {data.artifacts.commit_sha || 'view commit'}
                    <ExternalLink size={11} />
                  </a>
                )}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {data.artifacts.published_files.map((path: string) => {
                  const Icon = fileIcon(path)
                  return (
                    <div
                      key={path}
                      style={{
                        display: "flex", alignItems: "center", gap: 8,
                        padding: "8px 12px", borderRadius: "var(--radius)",
                        background: "var(--bg-input)", border: "1px solid var(--border)",
                        fontSize: 13,
                      }}
                    >
                      <Icon size={14} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
                      <span style={{ color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}>
                        {fileLabel(path)}
                      </span>
                      <span style={{ color: "var(--text-muted)", fontSize: 11, marginLeft: "auto", fontFamily: "var(--font-mono)" }}>
                        {path}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Documents produced by agents */}
          {data.artifacts.documents?.length > 0 && (
            <div>
              <h3 style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: 0.5, margin: "0 0 10px 0" }}>
                Documents ({data.artifacts.documents.length})
              </h3>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {data.artifacts.documents.map((doc: any) => (
                  <div
                    key={doc.document_id}
                    style={{
                      display: "flex", alignItems: "center", gap: 8,
                      padding: "8px 12px", borderRadius: "var(--radius)",
                      background: "var(--bg-input)", border: "1px solid var(--border)",
                      fontSize: 13,
                    }}
                  >
                    <FileText size={14} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
                    <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>
                      {DOC_TYPE_LABELS[doc.doc_type] || doc.doc_type}
                    </span>
                    <span style={{
                      fontSize: 10, padding: "2px 6px", borderRadius: "var(--radius)",
                      background: "var(--accent-subtle)", color: "var(--accent)",
                    }}>
                      {doc.agent_id?.replace(/_/g, " ")}
                    </span>
                    <span style={{ color: "var(--text-muted)", fontSize: 11, marginLeft: "auto", fontFamily: "var(--font-mono)" }}>
                      v{doc.version}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Agent Pipeline */}
      {data.subtasks?.length > 0 && (
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 24 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
            <h2 style={{ fontSize: 18, fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>Agent Pipeline</h2>
            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={expandAll} style={{ fontSize: 11, color: "var(--accent)", background: "transparent", border: "none", cursor: "pointer" }}>Expand All</button>
              <button onClick={collapseAll} style={{ fontSize: 11, color: "var(--text-muted)", background: "transparent", border: "none", cursor: "pointer" }}>Collapse All</button>
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {(() => {
              // Deduplicate: keep the version with content for each agent, or the first one
              const seen = new Map<string, any>()
              for (const s of data.subtasks) {
                const existing = seen.get(s.agent_id)
                if (!existing) {
                  seen.set(s.agent_id, s)
                } else {
                  // Prefer the one with output_text
                  const existingHas = existing.output_text && existing.output_text.trim().length > 0
                  const newHas = s.output_text && s.output_text.trim().length > 0
                  if (newHas && !existingHas) {
                    seen.set(s.agent_id, s)
                  }
                }
              }
              return Array.from(seen.values())
            })().map((s: any, i: number) => {
              const isExpanded = expandedAgents.has(s.subtask_id)
              const hasOutput = s.output_text && s.output_text.trim().length > 0
              const duration = s.started_at && s.completed_at
                ? Math.round((new Date(s.completed_at).getTime() - new Date(s.started_at).getTime()) / 1000)
                : null
              return (
                <div key={s.subtask_id}>
                  {/* Agent Header Row */}
                  <div
                    onClick={() => (hasOutput || s.error_message) && toggleAgent(s.subtask_id)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      padding: "10px 12px",
                      borderRadius: "var(--radius)",
                      background: isExpanded ? "var(--accent-subtle)" : "transparent",
                      cursor: hasOutput || s.error_message ? "pointer" : "default",
                      transition: "background 0.15s",
                    }}
                    onMouseEnter={(e) => { if ((hasOutput || s.error_message) && !isExpanded) e.currentTarget.style.background = "var(--bg-hover)" }}
                    onMouseLeave={(e) => { if (!isExpanded) e.currentTarget.style.background = "transparent" }}
                  >
                    <span style={{
                      width: 24, height: 24, borderRadius: "50%",
                      background: s.status === "completed" ? "var(--success)" : s.status === "failed" ? "var(--danger)" : s.status === "in_progress" ? "var(--accent)" : "var(--text-muted)",
                      color: "#fff", fontSize: 11, fontWeight: 700,
                      display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
                      animation: s.status === "in_progress" ? "pulse 1.5s infinite" : "none",
                    }}>
                      {i + 1}
                    </span>

                    {hasOutput || s.error_message ? (
                      isExpanded ? <ChevronDown size={14} style={{ color: "var(--accent)", flexShrink: 0 }} />
                        : <ChevronRight size={14} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
                    ) : <span style={{ width: 14 }} />}

                    <span style={{ fontWeight: 600, color: "var(--text-primary)", minWidth: 160 }}>
                      {s.display_name || s.agent_id.replace(/_/g, " ")}
                    </span>
                    <StatusBadge status={s.status} />

                    {duration !== null && (
                      <span style={{ fontSize: 11, color: "var(--text-muted)", marginLeft: "auto" }}>
                        {duration < 60 ? `${duration}s` : `${Math.floor(duration / 60)}m ${duration % 60}s`}
                      </span>
                    )}

                    {hasOutput && !isExpanded && (
                      <FileText size={13} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
                    )}
                  </div>

                  {/* Expanded Output — Option B: collapsible "View raw output" */}
                  {isExpanded && hasOutput && (
                    <div style={{
                      margin: "4px 0 8px 48px",
                      padding: "8px 12px",
                      background: "var(--bg-input)",
                      border: "1px solid var(--border)",
                      borderRadius: "var(--radius)",
                      fontSize: 12,
                    }}>
                      <div style={{
                        display: "flex", alignItems: "center", justifyContent: "space-between",
                        color: "var(--text-muted)", marginBottom: 6,
                      }}>
                        <span>Raw agent output ({Math.round(s.output_text.length / 1000)} KB)</span>
                        <span style={{ fontSize: 10, fontStyle: "italic" }}>
                          (Option B: shown on expand for debugging)
                        </span>
                      </div>
                      <div style={{
                        maxHeight: 500, overflowY: "auto",
                        padding: 12, background: "var(--bg-card)",
                        border: "1px solid var(--border)", borderRadius: "var(--radius)",
                      }}>
                        <MarkdownRenderer content={s.output_text} />
                      </div>
                    </div>
                  )}

                  {/* Error message */}
                  {isExpanded && s.error_message && (
                    <div style={{
                      margin: "4px 0 8px 48px",
                      padding: "8px 12px",
                      background: "var(--danger-subtle)",
                      borderRadius: "var(--radius)",
                      fontSize: 12,
                      color: "var(--danger)",
                      fontFamily: "var(--font-mono)",
                      whiteSpace: "pre-wrap",
                    }}>
                      {s.error_message}
                    </div>
                  )}

                  {i < data.subtasks.length - 1 && (
                    <div style={{ marginLeft: 23, width: 2, height: 8, background: "var(--border)" }} />
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Stories Summary */}
      {data.stories?.length > 0 && (
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 24 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
            <h2 style={{ fontSize: 18, fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>
              User Stories ({data.stories.length})
            </h2>
            <Link to={`/stories/${requestId}`} style={{ fontSize: 12, color: "var(--accent)", textDecoration: "none" }}>
              Open Story Board →
            </Link>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {data.stories.map((st: any) => (
              <div key={st.story_id} style={{ padding: 12, background: "var(--bg-input)", border: "1px solid var(--border)", borderRadius: "var(--radius)" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{st.story_id}</span>
                    <span style={{ fontSize: 14, fontWeight: 500, color: "var(--text-primary)" }}>{st.title}</span>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    {st.assigned_agent && (
                      <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: "var(--radius)", background: "var(--accent-subtle)", color: "var(--accent)" }}>
                        {st.assigned_agent.replace(/_/g, " ")}
                      </span>
                    )}
                    <StatusBadge status={st.status} />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
        @keyframes qg-pulse {
          0%, 100% {
            box-shadow: 0 0 6px color-mix(in srgb, var(--danger) 30%, transparent);
          }
          50% {
            box-shadow: 0 0 14px color-mix(in srgb, var(--danger) 60%, transparent);
          }
        }
      `}</style>
    </div>
  )
}


// ── AET-07 — Quality Gate chip + expandable violations panel ──────────────

function QualityGateChip({
  state, expanded, onClick,
}: {
  state: QualityGateState
  expanded: boolean
  onClick: () => void
}) {
  const isBlock = state.verdict === "BLOCK"
  const isWarn = state.verdict === "PASS_WITH_WARNINGS"
  const color = isBlock ? "var(--danger)" : isWarn ? "var(--warning, #d4a017)" : "var(--success)"
  const bg = isBlock
    ? "color-mix(in srgb, var(--danger) 14%, transparent)"
    : isWarn
      ? "color-mix(in srgb, var(--warning, #d4a017) 14%, transparent)"
      : "color-mix(in srgb, var(--success) 14%, transparent)"
  const enforce = state.summary.enforce_count ?? 0
  const warn = state.summary.warn_count ?? 0
  const totalShown = isBlock ? enforce : warn
  const label = isBlock
    ? `🛑 BLOCKED · QUALITY GATE · ${enforce} violation${enforce === 1 ? "" : "s"}`
    : isWarn
      ? `⚠ QUALITY WARNINGS · ${warn}`
      : `✓ QUALITY GATE PASSED`
  const Icon = isBlock ? ShieldAlert : ShieldCheck
  return (
    <button
      type="button"
      onClick={onClick}
      title={
        state.stage
          ? `${state.verdict} at ${state.stage}${state.rework_cycle != null ? ` (rework cycle ${state.rework_cycle})` : ""}`
          : state.verdict
      }
      style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        padding: "4px 10px", borderRadius: "var(--radius)",
        fontSize: 11, fontWeight: 700, fontFamily: "var(--font-mono)",
        letterSpacing: 0.5,
        background: bg,
        color,
        border: `1px solid ${color}`,
        cursor: "pointer",
        textTransform: "uppercase",
        animation: isBlock ? "qg-pulse 1.6s ease-in-out infinite" : undefined,
      }}
    >
      <Icon size={11} />
      <span>{label}</span>
      {totalShown > 0 && (
        expanded
          ? <ChevronDown size={11} />
          : <ChevronRight size={11} />
      )}
    </button>
  )
}

function QualityGateViolations({ state }: { state: QualityGateState }) {
  // Sort: enforce > warn > info so the most critical fixes float to top.
  const order: Record<string, number> = { enforce: 0, warn: 1, info: 2 }
  const sorted = [...state.violations].sort(
    (a, b) => (order[a.severity] ?? 9) - (order[b.severity] ?? 9),
  )
  if (sorted.length === 0) {
    return (
      <div style={{
        marginTop: 12, padding: "10px 14px",
        background: "var(--bg-hover)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        fontSize: 12, color: "var(--text-muted)",
      }}>
        No violations to show.
      </div>
    )
  }
  return (
    <div style={{
      marginTop: 12, padding: 14,
      background: "var(--bg-hover)",
      border: "1px solid var(--border)",
      borderRadius: "var(--radius)",
      display: "flex", flexDirection: "column", gap: 10,
    }}>
      <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
        policy_check evaluated {state.summary.total_emissions_checked ?? "?"} emission(s) at {state.stage || "quality_guardian_approval"}
        {state.rework_cycle != null ? ` · rework cycle ${state.rework_cycle}` : ""}
      </div>
      {sorted.map((v, i) => {
        const sevColor =
          v.severity === "enforce" ? "var(--danger)" :
          v.severity === "warn" ? "var(--warning, #d4a017)" :
          "var(--text-muted)"
        return (
          <div key={`${v.rule_id}-${i}`} style={{
            background: "var(--bg-card)",
            border: `1px solid ${sevColor}`,
            borderLeft: `3px solid ${sevColor}`,
            borderRadius: "var(--radius)",
            padding: "10px 12px",
            display: "flex", flexDirection: "column", gap: 6,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{
                fontFamily: "var(--font-mono)", fontSize: 11, fontWeight: 700,
                color: sevColor,
                textTransform: "uppercase", letterSpacing: 0.5,
              }}>
                [{v.rule_id}] {v.severity}
              </span>
              {v.lesson_ref && (
                <span style={{
                  padding: "1px 6px", borderRadius: 2,
                  background: "var(--bg-hover)",
                  color: "var(--text-secondary)",
                  border: "1px solid var(--border)",
                  fontFamily: "var(--font-mono)", fontSize: 10,
                }}>
                  {v.lesson_ref}
                </span>
              )}
              <span style={{ fontSize: 12, color: "var(--text-primary)", fontWeight: 600 }}>
                {v.rule_name}
              </span>
            </div>
            {v.target_path && (
              <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                📄 {v.target_path}
                {v.agent_id && <span style={{ marginLeft: 8, opacity: 0.7 }}>· {v.agent_id}</span>}
              </div>
            )}
            {v.snippet && (
              <pre style={{
                margin: 0, padding: "6px 10px",
                background: "var(--bg-secondary)",
                border: "1px solid var(--border)",
                borderRadius: 2,
                fontSize: 11, fontFamily: "var(--font-mono)",
                color: "var(--text-secondary)",
                whiteSpace: "pre-wrap", wordBreak: "break-word",
                maxHeight: 80, overflow: "auto",
              }}>
                {v.snippet}
              </pre>
            )}
            {v.rationale && (
              <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.4 }}>
                <strong style={{ color: "var(--text-primary)" }}>Why: </strong>
                {v.rationale}
              </div>
            )}
            {v.fix_hint && (
              <div style={{
                fontSize: 11, color: "var(--text-primary)", lineHeight: 1.4,
                padding: "6px 10px",
                background: "color-mix(in srgb, var(--accent) 8%, transparent)",
                border: "1px solid color-mix(in srgb, var(--accent) 25%, transparent)",
                borderRadius: 2,
              }}>
                <strong style={{ color: "var(--accent)" }}>Fix: </strong>
                {v.fix_hint}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
