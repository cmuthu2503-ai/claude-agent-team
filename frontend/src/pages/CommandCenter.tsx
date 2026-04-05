import { useState, useEffect } from "react"
import { api } from "../lib/api"
import { StatusBadge } from "../components/ui/StatusBadge"
import { PipelineBar } from "../components/ui/PipelineBar"
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

export function CommandCenterPage() {
  const [requests, setRequests] = useState<RequestItem[]>([])
  const [description, setDescription] = useState("")
  const [taskType, setTaskType] = useState("feature_request")
  const [priority, setPriority] = useState("medium")
  const [submitting, setSubmitting] = useState(false)

  const loadRequests = async () => {
    try {
      const res = await api.get("/requests?per_page=20")
      setRequests(res.data)
    } catch {}
  }

  useEffect(() => { loadRequests() }, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!description.trim()) return
    setSubmitting(true)
    try {
      await api.post("/requests", { description, task_type: taskType, priority })
      setDescription("")
      await loadRequests()
    } catch {}
    setSubmitting(false)
  }

  const active = requests.filter((r) => !["completed", "failed"].includes(r.status))
  const completed = requests.filter((r) => ["completed", "failed"].includes(r.status)).slice(0, 5)

  return (
    <div style={{ maxWidth: 1280, margin: "0 auto", padding: 24, display: "flex", flexDirection: "column", gap: 32 }}>
      {/* Input Form */}
      <form
        onSubmit={handleSubmit}
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
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Describe what you want to build..."
          rows={3}
          style={{
            width: "100%",
            background: "var(--bg-input)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            color: "var(--text-primary)",
            fontFamily: "var(--font)",
            padding: "12px 16px",
            fontSize: 14,
            resize: "vertical",
            outline: "none",
            boxSizing: "border-box",
          }}
        />
        <div style={{ marginTop: 12, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", gap: 12 }}>
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
                fontSize: 14,
              }}
            >
              <option value="feature_request">Feature</option>
              <option value="bug_report">Bug Fix</option>
              <option value="doc_request">Docs</option>
              <option value="demo_request">Demo</option>
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
          </div>
          <button
            type="submit"
            disabled={submitting || !description.trim()}
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
              cursor: submitting || !description.trim() ? "not-allowed" : "pointer",
              opacity: submitting || !description.trim() ? 0.5 : 1,
            }}
          >
            <Send size={14} />
            {submitting ? "Submitting..." : "Submit"}
          </button>
        </div>
      </form>

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

      {requests.length === 0 && (
        <div style={{ padding: "80px 0", textAlign: "center", color: "var(--text-muted)" }}>
          <Plus size={48} style={{ margin: "0 auto 16px", opacity: 0.5 }} />
          <p>No requests yet. Submit your first request above.</p>
        </div>
      )}
    </div>
  )
}
