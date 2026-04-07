import { useState, useEffect } from "react"
import { useParams, Link } from "react-router-dom"
import { api } from "../lib/api"
import { StatusBadge } from "../components/ui/StatusBadge"
import { ArrowLeft, ChevronDown, ChevronRight, FileText, ExternalLink, Github, FileType, Presentation, FileImage, Code } from "lucide-react"
import { MarkdownRenderer } from "../components/ui/MarkdownRenderer"

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
            <p style={{ marginTop: 8, color: "var(--text-secondary)", fontSize: 14, lineHeight: 1.6 }}>{data.description}</p>
          </div>
          <StatusBadge status={data.status} size="md" />
        </div>
        <div style={{ marginTop: 12, display: "flex", gap: 16, fontSize: 13, color: "var(--text-muted)" }}>
          <span>Type: <span style={{ color: "var(--text-secondary)", textTransform: "capitalize" }}>{data.task_type?.replace("_", " ")}</span></span>
          <span>Priority: <span style={{ color: "var(--text-secondary)", textTransform: "capitalize" }}>{data.priority}</span></span>
          <span>Created: {new Date(data.created_at).toLocaleString()}</span>
          {data.total_cost?.cost_usd > 0 && <span>Cost: ${data.total_cost.cost_usd}</span>}
        </div>
        {data.stories?.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <Link to={`/stories/${requestId}`} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 13, color: "var(--accent)", textDecoration: "none" }}>
              <ExternalLink size={13} /> View Story Board ({data.stories.length} stories)
            </Link>
          </div>
        )}
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
      `}</style>
    </div>
  )
}
