import { useState, useEffect, useRef } from "react"
import { api } from "../lib/api"
import { StatusBadge } from "../components/ui/StatusBadge"
import { RichTextInput, type RichTextInputHandle } from "../components/ui/RichTextInput"
import { Link } from "react-router-dom"
import { Plus, Send, X, Trash2 } from "lucide-react"
import { useAuthStore } from "../stores/auth"

// Terminal states where a request is safe to hard-delete (nothing will be
// writing to its rows in the background).
const TERMINAL_STATUSES = ["completed", "failed", "cancelled"] as const
function isTerminal(status: string): boolean {
  return (TERMINAL_STATUSES as readonly string[]).includes(status)
}

// 5-button provider selector. Each button forces ALL agents to a single model
// for the duration of a request (mirrors the existing Bedrock override pattern).
// The two OpenAI buttons point at the latest models on OpenAI's API as of
// 2026-04-08: gpt-5.4 (flagship, 2026-03-05) and o4-mini (latest o-series
// reasoning, 2025-04-16). Override via OPENAI_GPT5_MODEL_ID / OPENAI_O3_MODEL_ID
// in .env without touching this file.
// Keep in sync with PromptStudio.tsx and src/agents/executor.py::VALID_PROVIDERS.
const PROVIDER_OPTIONS: { id: string; label: string; title: string }[] = [
  { id: "anthropic_opus",   label: "Opus",     title: "Claude Opus 4.6 (direct Anthropic API)" },
  { id: "anthropic_sonnet", label: "Sonnet",   title: "Claude Sonnet 4.6 (direct Anthropic API)" },
  { id: "bedrock",          label: "Bedrock",  title: "Claude Sonnet 4 via Amazon Bedrock" },
  { id: "openai_gpt5",      label: "GPT-5.4",  title: "OpenAI GPT-5.4 — latest flagship (2026-03-05)" },
  { id: "openai_o3",        label: "o4-mini",  title: "OpenAI o4-mini — latest reasoning model (2025-04-16)" },
]

interface RequestItem {
  request_id: string
  description: string
  task_type: string
  priority: string
  status: string
  created_at: string
  created_by?: string
}

interface ActivityEvent {
  id: string
  type: string
  agent?: string
  message?: string
  request_id?: string
  progress?: number
  timestamp: string
}

export function CommandCenterPage() {
  const [requests, setRequests] = useState<RequestItem[]>([])
  const [selectedTeam, setSelectedTeam] = useState("engineering")
  const [taskType, setTaskType] = useState("feature_request")
  const [priority, setPriority] = useState("medium")
  const [provider, setProvider] = useState<string>(() => {
    const stored = localStorage.getItem("llm_provider") || "anthropic_sonnet"
    // Migrate legacy "anthropic" value → new "anthropic_sonnet"
    return stored === "anthropic" ? "anthropic_sonnet" : stored
  })
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState("")
  const [attachCount, setAttachCount] = useState(0)
  const [similarDocs, setSimilarDocs] = useState<any[]>([])
  const [activity, setActivity] = useState<ActivityEvent[]>([])
  const editorRef = useRef<RichTextInputHandle>(null)
  const wsRef = useRef<WebSocket | null>(null)

  const loadRequests = async () => {
    try {
      const res = await api.get("/requests?per_page=20")
      setRequests(res.data)
    } catch {}
  }

  useEffect(() => {
    loadRequests()

    // Connect to WebSocket for real-time activity
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/activity`)
    wsRef.current = ws

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        const eventType = data.type || ""
        const eventData = data.data || {}

        // Add to activity feed
        const activityItem: ActivityEvent = {
          id: Math.random().toString(36).slice(2),
          type: eventType,
          agent: eventData.display_name || eventData.agent_id,
          message: eventData.message || _eventMessage(eventType, eventData),
          request_id: eventData.request_id,
          progress: eventData.progress,
          timestamp: data.timestamp || new Date().toISOString(),
        }
        setActivity((prev) => [activityItem, ...prev].slice(0, 30))

        // Refresh request list on status changes
        if (["request.completed", "request.failed", "request.status_changed"].includes(eventType)) {
          loadRequests()
        }
      } catch {}
    }

    ws.onclose = () => {
      // Reconnect after 3 seconds
      setTimeout(() => {
        if (wsRef.current === ws) wsRef.current = null
      }, 3000)
    }

    return () => {
      ws.close()
      wsRef.current = null
    }
  }, [])

  const handleSubmit = async () => {
    const content = editorRef.current?.getContent()
    if (!content || !content.text.trim()) return
    setSubmitting(true)
    setSubmitError("")
    try {
      const formData = new FormData()
      formData.append("description", content.text)
      formData.append("task_type", taskType)
      formData.append("priority", priority)
      formData.append("provider", provider)
      for (const file of content.files) {
        formData.append("screenshots", file)
      }
      await api.postForm("/requests", formData)
      editorRef.current?.clear()
      setAttachCount(0)
      await loadRequests()
    } catch (err: any) {
      console.error("Submit failed:", err)
      setSubmitError(err?.message || "Submit failed")
    }
    setSubmitting(false)
  }

  // Track per-request in-flight cancel/delete so buttons disable while action runs
  const [busyId, setBusyId] = useState<string | null>(null)
  const currentUser = useAuthStore((s) => s.user)

  const canMutate = (r: RequestItem): boolean => {
    // Matches backend permission: admin OR creator (owner) can cancel/delete.
    // If created_by is missing from the list payload, fall back to allowing —
    // backend will still enforce 403 if the user isn't allowed.
    if (!currentUser) return false
    if (currentUser.role === "admin") return true
    const owner = (r as any).created_by
    if (!owner) return true
    return owner === currentUser.username
  }

  const handleCancel = async (requestId: string) => {
    if (!window.confirm(`Cancel request ${requestId}?\n\nThe workflow will be stopped and marked as cancelled. You can delete it afterward.`)) return
    setBusyId(requestId)
    try {
      await api.post(`/requests/${requestId}/cancel`)
      await loadRequests()
    } catch (err: any) {
      alert(`Cancel failed: ${err?.message || err}`)
    } finally {
      setBusyId(null)
    }
  }

  const handleDelete = async (requestId: string) => {
    if (!window.confirm(`Permanently delete request ${requestId}?\n\nThis removes the request and all its subtasks, stories, documents, and cost records from the database. This cannot be undone.`)) return
    setBusyId(requestId)
    try {
      await api.delete(`/requests/${requestId}`)
      await loadRequests()
    } catch (err: any) {
      alert(`Delete failed: ${err?.message || err}`)
    } finally {
      setBusyId(null)
    }
  }

  const active = requests.filter((r) => !isTerminal(r.status))
  const completed = requests.filter((r) => isTerminal(r.status)).slice(0, 5)

  return (
    <div style={{ maxWidth: 1280, margin: "0 auto", padding: 24, display: "flex", flexDirection: "column", gap: 32 }}>
      {/* Input Form */}
      <div
        style={{
          background: "var(--bg-card)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          padding: 24,
        }}
      >
        <h2 style={{ color: "var(--text-primary)", fontSize: 18, fontWeight: 600, marginBottom: 16 }}>
          New Request
        </h2>

        <RichTextInput
          ref={editorRef}
          placeholder="Describe what you want to build... (paste or drag screenshots directly here)"
          onFilesChange={setAttachCount}
          onTextChange={async (text: string) => {
            // Search for similar documents when user types enough
            if (text.length > 20) {
              try {
                const words = text.split(/\s+/).slice(0, 5).join(" ")
                const res = await api.get(`/documents/search?q=${encodeURIComponent(words)}&doc_type=prd&limit=3`)
                setSimilarDocs(res.data || [])
              } catch { setSimilarDocs([]) }
            } else {
              setSimilarDocs([])
            }
          }}
        />

        {/* Similar documents found */}
        {similarDocs.length > 0 && (
          <div style={{
            marginTop: 8, padding: "10px 14px",
            borderRadius: "var(--radius)",
            background: "var(--accent-subtle)",
            border: "1px solid var(--accent)",
          }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: "var(--accent)", marginBottom: 6 }}>
              Similar PRDs found — pipeline may reuse existing documents:
            </div>
            {similarDocs.map((d: any) => (
              <div key={d.document_id} style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 2 }}>
                • <strong>{d.request_id}</strong>: {d.title?.slice(0, 80)}
              </div>
            ))}
          </div>
        )}

        {submitError && (
          <div style={{ marginTop: 8, padding: "8px 12px", borderRadius: "var(--radius)", background: "var(--danger-subtle)", color: "var(--danger)", fontSize: 13 }}>
            {submitError}
          </div>
        )}

        <div style={{ marginTop: 12, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            {/* Team Selector */}
            <div style={{ display: "flex", borderRadius: "var(--radius)", overflow: "hidden", border: "1px solid var(--border)" }}>
              {[
                { id: "engineering", label: "Engineering", icon: "⚙️" },
                { id: "research", label: "Research", icon: "🔍" },
                { id: "content", label: "Content", icon: "📝" },
              ].map((team) => (
                <button
                  key={team.id}
                  type="button"
                  onClick={() => {
                    setSelectedTeam(team.id)
                    // Auto-select first task type for the team
                    const defaults: Record<string, string> = {
                      engineering: "feature_request",
                      research: "research_request",
                      content: "content_request",
                    }
                    setTaskType(defaults[team.id] || "feature_request")
                  }}
                  style={{
                    padding: "6px 14px",
                    fontSize: 13,
                    fontWeight: 500,
                    border: "none",
                    cursor: "pointer",
                    fontFamily: "var(--font)",
                    background: selectedTeam === team.id ? "var(--accent)" : "var(--bg-input)",
                    color: selectedTeam === team.id ? "#fff" : "var(--text-secondary)",
                    borderRight: "1px solid var(--border)",
                  }}
                >
                  {team.icon} {team.label}
                </button>
              ))}
            </div>

            {/* Task Type — filtered by selected team */}
            <select
              value={taskType}
              onChange={(e) => setTaskType(e.target.value)}
              style={{
                background: "var(--bg-input)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius)",
                color: "var(--text-primary)",
                fontFamily: "var(--font)",
                padding: "6px 12px",
                fontSize: 13,
              }}
            >
              {selectedTeam === "engineering" && (
                <>
                  <option value="feature_request">Feature</option>
                  <option value="bug_report">Bug Fix</option>
                  <option value="doc_request">Docs</option>
                  <option value="demo_request">Demo</option>
                </>
              )}
              {selectedTeam === "research" && (
                <option value="research_request">Research Assessment</option>
              )}
              {selectedTeam === "content" && (
                <option value="content_request">Create Content</option>
              )}
            </select>
            <div style={{ display: "flex", gap: 4 }}>
              {["high", "medium", "low"].map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setPriority(p)}
                  style={{
                    borderRadius: 9999,
                    padding: "4px 12px",
                    fontSize: 12,
                    fontWeight: 500,
                    textTransform: "capitalize" as const,
                    border: "none",
                    cursor: "pointer",
                    background: priority === p ? "var(--accent-subtle)" : "var(--bg-hover)",
                    color: priority === p ? "var(--accent)" : "var(--text-secondary)",
                  }}
                >
                  {p}
                </button>
              ))}
            </div>

            {/* Model Provider Selector */}
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Model:</span>
              <div style={{ display: "flex", borderRadius: "var(--radius)", overflow: "hidden", border: "1px solid var(--border)" }}>
                {PROVIDER_OPTIONS.map((opt, i) => (
                  <button
                    key={opt.id}
                    type="button"
                    title={opt.title}
                    onClick={() => {
                      setProvider(opt.id)
                      localStorage.setItem("llm_provider", opt.id)
                    }}
                    style={{
                      padding: "5px 12px",
                      fontSize: 12,
                      fontWeight: 500,
                      border: "none",
                      cursor: "pointer",
                      fontFamily: "var(--font)",
                      background: provider === opt.id ? "var(--accent)" : "var(--bg-input)",
                      color: provider === opt.id ? "#fff" : "var(--text-secondary)",
                      borderRight: i < PROVIDER_OPTIONS.length - 1 ? "1px solid var(--border)" : "none",
                    }}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
            {attachCount > 0 && (
              <span style={{ fontSize: 12, color: "var(--accent)" }}>
                📎 {attachCount} screenshot{attachCount > 1 ? "s" : ""} attached
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              borderRadius: "var(--radius)",
              background: "var(--accent)",
              color: "#fff",
              padding: "8px 16px",
              fontSize: 14,
              fontWeight: 500,
              border: "none",
              cursor: submitting ? "not-allowed" : "pointer",
              opacity: submitting ? 0.5 : 1,
            }}
          >
            <Send size={14} />
            {submitting ? "Submitting..." : "Submit"}
          </button>
        </div>
      </div>

      {/* Active Requests */}
      {active.length > 0 && (
        <section>
          <h2 style={{ color: "var(--text-primary)", fontSize: 18, fontWeight: 600, marginBottom: 12 }}>
            Active Requests
          </h2>
          <div style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))" }}>
            {active.map((r) => (
              <div
                key={r.request_id}
                style={{
                  position: "relative",
                  background: "var(--bg-card)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius)",
                  transition: "border-color 0.15s, box-shadow 0.15s",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = "var(--accent)"
                  e.currentTarget.style.boxShadow = "var(--shadow)"
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = "var(--border)"
                  e.currentTarget.style.boxShadow = "none"
                }}
              >
                <Link
                  to={`/request/${r.request_id}`}
                  style={{ display: "block", padding: 16, textDecoration: "none", color: "inherit" }}
                >
                  <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8 }}>
                    <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                      {r.request_id}
                    </span>
                    <StatusBadge status={r.status} />
                  </div>
                  <p style={{ marginTop: 8, fontSize: 14, color: "var(--text-secondary)", overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
                    {r.description}
                  </p>
                  <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text-muted)" }}>
                    <span style={{ textTransform: "capitalize" }}>{r.task_type.replace("_", " ")}</span>
                    <span>·</span>
                    <span style={{ textTransform: "capitalize" }}>{r.priority}</span>
                  </div>
                </Link>
                {canMutate(r) && (
                  <div style={{ position: "absolute", top: 12, right: 56, display: "flex", gap: 4 }}>
                    <button
                      type="button"
                      title="Cancel this request"
                      disabled={busyId === r.request_id}
                      onClick={(e) => {
                        e.preventDefault()
                        e.stopPropagation()
                        handleCancel(r.request_id)
                      }}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 3,
                        padding: "2px 6px",
                        fontSize: 10,
                        fontWeight: 500,
                        fontFamily: "var(--font)",
                        background: "transparent",
                        color: "var(--text-muted)",
                        border: "1px solid var(--border)",
                        borderRadius: "var(--radius)",
                        cursor: busyId === r.request_id ? "wait" : "pointer",
                        opacity: busyId === r.request_id ? 0.5 : 1,
                        transition: "background 0.15s, color 0.15s, border-color 0.15s",
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.background = "var(--danger-subtle)"
                        e.currentTarget.style.color = "var(--danger)"
                        e.currentTarget.style.borderColor = "var(--danger)"
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.background = "transparent"
                        e.currentTarget.style.color = "var(--text-muted)"
                        e.currentTarget.style.borderColor = "var(--border)"
                      }}
                    >
                      <X size={10} />
                      {busyId === r.request_id ? "..." : "Cancel"}
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Recently Completed */}
      {completed.length > 0 && (
        <section>
          <h2 style={{ color: "var(--text-primary)", fontSize: 18, fontWeight: 600, marginBottom: 12 }}>
            Recently Completed
          </h2>
          <div
            style={{
              background: "var(--bg-card)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              overflow: "hidden",
            }}
          >
            {completed.map((r, i) => (
              <div
                key={r.request_id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 12,
                  padding: "12px 16px",
                  borderTop: i > 0 ? "1px solid var(--border)" : "none",
                  transition: "background 0.15s",
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-hover)" }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent" }}
              >
                <Link
                  to={`/request/${r.request_id}`}
                  style={{
                    flex: 1,
                    minWidth: 0,
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                    textDecoration: "none",
                  }}
                >
                  <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-muted)", flexShrink: 0 }}>
                    {r.request_id}
                  </span>
                  <span style={{ fontSize: 14, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {r.description}
                  </span>
                </Link>
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                  <StatusBadge status={r.status} />
                  {canMutate(r) && (
                    <button
                      type="button"
                      title="Delete this request permanently"
                      disabled={busyId === r.request_id}
                      onClick={(e) => {
                        e.preventDefault()
                        e.stopPropagation()
                        handleDelete(r.request_id)
                      }}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 3,
                        padding: "3px 7px",
                        fontSize: 11,
                        fontWeight: 500,
                        fontFamily: "var(--font)",
                        background: "transparent",
                        color: "var(--text-muted)",
                        border: "1px solid var(--border)",
                        borderRadius: "var(--radius)",
                        cursor: busyId === r.request_id ? "wait" : "pointer",
                        opacity: busyId === r.request_id ? 0.5 : 1,
                        transition: "background 0.15s, color 0.15s, border-color 0.15s",
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.background = "var(--danger-subtle)"
                        e.currentTarget.style.color = "var(--danger)"
                        e.currentTarget.style.borderColor = "var(--danger)"
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.background = "transparent"
                        e.currentTarget.style.color = "var(--text-muted)"
                        e.currentTarget.style.borderColor = "var(--border)"
                      }}
                    >
                      <Trash2 size={11} />
                      {busyId === r.request_id ? "..." : "Delete"}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Live Activity Feed */}
      {activity.length > 0 && (
        <section>
          <h2 style={{ color: "var(--text-primary)", fontSize: 18, fontWeight: 600, marginBottom: 12 }}>
            Live Activity
          </h2>
          <div
            style={{
              background: "var(--bg-card)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              maxHeight: 300,
              overflowY: "auto",
            }}
          >
            {activity.map((a, i) => (
              <div
                key={a.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "8px 16px",
                  borderTop: i > 0 ? "1px solid var(--border)" : "none",
                  fontSize: 13,
                }}
              >
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    flexShrink: 0,
                    background: a.type.includes("completed") || a.type.includes("progress")
                      ? "var(--success)"
                      : a.type.includes("failed")
                        ? "var(--danger)"
                        : a.type.includes("started")
                          ? "var(--accent)"
                          : "var(--text-muted)",
                    animation: a.type.includes("started") || a.type.includes("progress")
                      ? "pulse 1.5s infinite"
                      : "none",
                  }}
                />
                {a.agent && (
                  <span style={{ fontWeight: 600, color: "var(--text-primary)", minWidth: 120 }}>
                    {a.agent}
                  </span>
                )}
                <span style={{ color: "var(--text-secondary)", flex: 1 }}>
                  {a.message}
                </span>
                {a.progress !== undefined && a.progress < 100 && (
                  <div style={{ width: 60, height: 4, borderRadius: 2, background: "var(--bg-hover)", overflow: "hidden" }}>
                    <div
                      style={{
                        width: `${a.progress}%`,
                        height: "100%",
                        background: "var(--accent)",
                        borderRadius: 2,
                        transition: "width 0.3s",
                      }}
                    />
                  </div>
                )}
                <span style={{ fontSize: 10, color: "var(--text-muted)", flexShrink: 0 }}>
                  {new Date(a.timestamp).toLocaleTimeString()}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      {requests.length === 0 && activity.length === 0 && (
        <div style={{ padding: "80px 0", textAlign: "center", color: "var(--text-muted)" }}>
          <Plus size={48} style={{ margin: "0 auto 16px", opacity: 0.5 }} />
          <p>No requests yet. Submit your first request above.</p>
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

function _eventMessage(type: string, data: any): string {
  const name = data.display_name || data.agent_id || ""
  switch (type) {
    case "request.created":
      return `Request ${data.request_id} submitted`
    case "request.status_changed":
      return `Request ${data.request_id} → ${data.status?.replace("_", " ")}`
    case "request.completed":
      return `Request ${data.request_id} completed successfully`
    case "request.failed":
      return `Request ${data.request_id} failed: ${data.error || "unknown"}`
    case "agent.started":
      return `Started working on ${data.request_id}`
    case "agent.progress":
      return data.message || `Working... ${data.progress}%`
    case "agent.completed":
      return `Finished work on ${data.request_id}`
    case "agent.failed":
      return `Failed: ${data.error || "unknown error"}`
    default:
      return type.replace(/\./g, " ")
  }
}
