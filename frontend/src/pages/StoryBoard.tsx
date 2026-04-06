import { useState, useEffect } from "react"
import { useParams, Link } from "react-router-dom"
import { api } from "../lib/api"
import { StatusBadge } from "../components/ui/StatusBadge"
import { CoverageBar } from "../components/ui/CoverageBar"
import { MarkdownRenderer } from "../components/ui/MarkdownRenderer"
import { ArrowLeft, ChevronDown, ChevronRight } from "lucide-react"

const columns = ["todo", "in_progress", "review", "testing", "done"]
const columnLabels: Record<string, string> = {
  todo: "To Do",
  in_progress: "In Progress",
  review: "Review",
  testing: "Testing",
  done: "Done",
}
const columnColors: Record<string, string> = {
  todo: "var(--text-muted)",
  in_progress: "var(--accent)",
  review: "var(--info, var(--accent))",
  testing: "var(--warning)",
  done: "var(--success)",
}

export function StoryBoardPage() {
  const { requestId } = useParams()
  const [data, setData] = useState<any>(null)
  const [stories, setStories] = useState<any[]>([])
  const [expandedStory, setExpandedStory] = useState<string | null>(null)
  const [polling, setPolling] = useState(true)

  const loadData = async () => {
    if (!requestId) return
    try {
      const res = await api.get(`/requests/${requestId}`)
      setData(res.data)
      setStories(res.data.stories || [])
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

  return (
    <div style={{ maxWidth: 1400, margin: "0 auto", padding: 24, display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <Link to={`/request/${requestId}`} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 13, color: "var(--text-muted)", textDecoration: "none" }}>
            <ArrowLeft size={14} /> Back to {requestId}
          </Link>
          <h1 style={{ fontSize: 20, fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>Story Board</h1>
          {data && <StatusBadge status={data.status} />}
        </div>
      </div>

      {/* Pipeline Summary */}
      <div style={{ display: "flex", gap: 12, padding: "12px 16px", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)" }}>
        {columns.map((col) => {
          const count = stories.filter((s) => s.status === col).length
          return (
            <div key={col} style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ width: 8, height: 8, borderRadius: "50%", background: columnColors[col] }} />
              <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>{columnLabels[col]}</span>
              <span style={{ fontSize: 13, fontWeight: 700, fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>{count}</span>
            </div>
          )
        })}
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--text-muted)" }}>
          {stories.length} total stories
        </span>
      </div>

      {/* Kanban Columns */}
      {stories.length > 0 ? (
        <div style={{ display: "grid", gridTemplateColumns: `repeat(${columns.length}, 1fr)`, gap: 12 }}>
          {columns.map((col) => (
            <div key={col} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "8px 0" }}>
                <span style={{ width: 10, height: 10, borderRadius: "50%", background: columnColors[col] }} />
                <h3 style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>{columnLabels[col]}</h3>
                <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                  {stories.filter((s) => s.status === col).length}
                </span>
              </div>
              <div style={{ minHeight: 120, display: "flex", flexDirection: "column", gap: 8, padding: 8, borderRadius: "var(--radius)", background: "var(--bg-secondary)" }}>
                {stories
                  .filter((s) => s.status === col)
                  .map((s) => {
                    const isExpanded = expandedStory === s.story_id
                    return (
                      <div
                        key={s.story_id}
                        style={{
                          background: "var(--bg-card)",
                          border: "1px solid var(--border)",
                          borderRadius: "var(--radius)",
                          borderLeft: `3px solid ${columnColors[col]}`,
                          overflow: "hidden",
                        }}
                      >
                        {/* Story header */}
                        <div
                          onClick={() => setExpandedStory(isExpanded ? null : s.story_id)}
                          style={{ padding: "10px 12px", cursor: "pointer" }}
                        >
                          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 4 }}>
                            <span style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{s.story_id}</span>
                            {isExpanded ? <ChevronDown size={12} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
                              : <ChevronRight size={12} style={{ color: "var(--text-muted)", flexShrink: 0 }} />}
                          </div>
                          <p style={{ fontSize: 12, fontWeight: 500, color: "var(--text-primary)", margin: "4px 0 0", lineHeight: 1.4 }}>
                            {s.title}
                          </p>
                          {s.assigned_agent && (
                            <span style={{ display: "inline-block", marginTop: 6, fontSize: 9, padding: "2px 6px", borderRadius: "var(--radius)", background: "var(--accent-subtle)", color: "var(--accent)" }}>
                              {s.assigned_agent.replace(/_/g, " ")}
                            </span>
                          )}
                          {s.priority && (
                            <span style={{ display: "inline-block", marginTop: 6, marginLeft: 4, fontSize: 9, padding: "2px 6px", borderRadius: "var(--radius)", background: "var(--bg-hover)", color: "var(--text-muted)", textTransform: "capitalize" }}>
                              {s.priority}
                            </span>
                          )}
                          {s.coverage_pct !== null && s.coverage_pct !== undefined && (
                            <div style={{ marginTop: 6 }}>
                              <CoverageBar value={s.coverage_pct} />
                            </div>
                          )}
                        </div>

                        {/* Expanded description */}
                        {isExpanded && s.description && (
                          <div style={{
                            padding: "0 12px 12px",
                            maxHeight: 300,
                            overflowY: "auto",
                            borderTop: "1px solid var(--border)",
                            paddingTop: 8,
                          }}>
                            <MarkdownRenderer content={s.description} />
                          </div>
                        )}
                      </div>
                    )
                  })}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ padding: "60px 0", textAlign: "center", color: "var(--text-muted)" }}>
          {data?.status === "in_progress" || data?.status === "analyzing"
            ? "Stories will appear here once the User Story Author completes..."
            : "No stories for this request"}
        </div>
      )}
    </div>
  )
}
