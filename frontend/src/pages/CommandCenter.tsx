import { useState, useEffect, useRef } from "react"
import { api } from "../lib/api"
import { StatusBadge } from "../components/ui/StatusBadge"
import { RichTextInput, type RichTextInputHandle } from "../components/ui/RichTextInput"
import { Link } from "react-router-dom"
import { Plus, Send } from "lucide-react"

interface RequestItem {
  request_id: string
  description: string
  task_type: string
  priority: string
  status: string
  created_at: string
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
    return localStorage.getItem("llm_provider") || "anthropic"
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

  const active = requests.filter((r) => !["completed", "failed"].includes(r.status))
  const completed = requests.filter((r) => ["completed", "failed"].includes(r.status)).slice(0, 5)

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
                {[
                  { id: "anthropic", label: "Claude" },
                  { id: "bedrock", label: "Amazon Bedrock" },
                ].map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
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
                      borderRight: opt.id === "anthropic" ? "1px solid var(--border)" : "none",
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
              <Link
                key={r.request_id}
                to={`/request/${r.request_id}`}
                style={{
                  display: "block",
                  background: "var(--bg-card)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius)",
                  padding: 16,
                  textDecoration: "none",
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
                <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
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
              <Link
                key={r.request_id}
                to={`/request/${r.request_id}`}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "12px 16px",
                  textDecoration: "none",
                  borderTop: i > 0 ? "1px solid var(--border)" : "none",
                  transition: "background 0.15s",
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-hover)" }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent" }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                    {r.request_id}
                  </span>
                  <span style={{ fontSize: 14, color: "var(--text-secondary)" }}>{r.description}</span>
                </div>
                <StatusBadge status={r.status} />
              </Link>
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
