/**
 * ProjectStoryBoard — the consolidated per-project Build Board.
 *
 * Lives at /stories/project/:projectId. Rows = the project's finalized
 * tasks; columns = task_status (Backlog / In Progress / Review /
 * Testing / Deployed / Failed).
 *
 * Click any card → opens a floating, draggable PopupWindow with the
 * task detail (workflow stage strip, description, agent timeline,
 * outputs, errors). Board stays clickable behind the popup so you
 * can compare or open another task without closing the current one.
 *
 * The per-request Story Board at /stories/:requestId stays accessible
 * via a deep-link footer inside the popup — no longer the primary
 * navigation choice.
 */

import { useEffect, useMemo, useRef, useState } from "react"
import { Link, useParams } from "react-router-dom"
import { ArrowLeft } from "lucide-react"
import { api } from "../lib/api"
import { ProjectChip } from "../components/projects/ProjectChip"
import { RefreshButton } from "../components/ui/RefreshButton"
import { EnrichedTaskCard } from "../components/board/EnrichedTaskCard"
import { PopupWindow } from "../components/board/PopupWindow"
import { TaskDrillIn } from "../components/board/TaskDrillIn"
import type { CardData, TaskStatus, WorkflowStage } from "../components/board/types"

interface ProjectTask {
  task_id: string
  project_id: string
  list_version: number
  list_status: string
  ordinal: number
  title: string
  description: string
  task_type: string
  priority: string
  estimated_agent: string | null
  task_status: TaskStatus
  request_id: string | null
}

const COLUMNS: {
  key: TaskStatus | "active"
  label: string
  match: (s: TaskStatus) => boolean
  accent: string
}[] = [
  { key: "backlog",  label: "Backlog",     match: (s) => s === "backlog",                                 accent: "var(--text-muted)" },
  { key: "active",   label: "In Progress", match: (s) => s === "dispatched" || s === "in_progress",        accent: "var(--accent)" },
  { key: "review",   label: "Review",      match: (s) => s === "review",                                  accent: "#b026ff" },
  { key: "testing",  label: "Testing",     match: (s) => s === "testing",                                 accent: "var(--warning, #f59e0b)" },
  { key: "deployed", label: "Deployed",    match: (s) => s === "deployed",                                accent: "var(--success)" },
  { key: "failed",   label: "Failed",      match: (s) => s === "failed" || s === "cancelled",             accent: "var(--danger)" },
]

// Map task_status → the workflow stage the agent is most likely
// currently working in. Approximate — the card's stage strip is a
// visual hint; the popup fetches the real subtask history.
function statusToStage(s: TaskStatus): WorkflowStage | null {
  switch (s) {
    case "dispatched":
    case "in_progress": return "development"
    case "review":      return "review"
    case "testing":     return "testing"
    case "deployed":    return "deploy"
    case "failed":
    case "cancelled":   return "code_commit"  // most failures happen here
    default:            return null
  }
}

function projectTaskToCard(t: ProjectTask): CardData {
  // Extract phase prefix from title: "Phase 1: Foundation — Build X" →
  // phase = "Phase 1: Foundation", title = "Build X".
  let phase: string | null = null
  let title = t.title
  const m = t.title.match(/^(Phase\s+\d+:\s*[^—\-]+?)\s*[—\-]\s*(.+)$/)
  if (m) {
    phase = m[1].trim()
    title = m[2].trim()
  }
  return {
    id: t.task_id,
    task_id: t.task_id,
    request_id: t.request_id,
    phase,
    title,
    description: t.description,
    type: t.estimated_agent?.replace("_specialist", "").replace("_", " ") || null,
    agent: t.estimated_agent,
    priority: (t.priority as "high" | "medium" | "low") || "medium",
    status: t.task_status,
    current_stage: statusToStage(t.task_status),
  }
}

export function ProjectStoryBoardPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const [tasks, setTasks] = useState<ProjectTask[] | null>(null)
  const [error, setError] = useState("")
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)

  const load = async () => {
    if (!projectId) return
    try {
      const res = await api.get<{ data: ProjectTask[] }>(`/projects/${projectId}/tasks`)
      setTasks((res.data || []) as ProjectTask[])
    } catch (e: any) {
      setError(e?.message || "Failed to load tasks")
    }
  }

  // Manual refresh handler. Wraps `load()` with a `refreshing` flag so
  // the button can show the spinner — the background 5s polling
  // interval doesn't toggle this, only explicit clicks.
  const handleRefresh = async () => {
    setRefreshing(true)
    try { await load() } finally { setRefreshing(false) }
  }

  useEffect(() => {
    void load()
    const id = window.setInterval(load, 5000)
    return () => window.clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  // Live updates via WebSocket — refresh on relevant events.
  useEffect(() => {
    if (!projectId) return
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/activity`)
    wsRef.current = ws
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        const t = msg.type || ""
        if (
          t === "request.status_changed" ||
          t === "request.completed" ||
          t === "request.failed" ||
          t === "request.cancelled" ||
          (t === "project.tasks_finalized" && msg.data?.project_id === projectId)
        ) {
          void load()
        }
      } catch {}
    }
    return () => {
      ws.close()
      wsRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  const byColumn = useMemo(() => {
    const map = new Map<string, ProjectTask[]>()
    COLUMNS.forEach((c) => map.set(c.key, []))
    if (tasks) {
      for (const t of tasks) {
        const col = COLUMNS.find((c) => c.match(t.task_status))
        if (col) map.get(col.key)!.push(t)
      }
    }
    return map
  }, [tasks])

  const selectedCard = useMemo<CardData | null>(() => {
    if (!selectedTaskId || !tasks) return null
    const t = tasks.find((x) => x.task_id === selectedTaskId)
    return t ? projectTaskToCard(t) : null
  }, [selectedTaskId, tasks])

  if (error) return <div style={{ padding: 24, color: "var(--danger)" }}>{error}</div>
  if (!tasks) return <div style={{ padding: 24, color: "var(--text-muted)" }}>Loading board…</div>

  return (
    <div style={{
      minHeight: "calc(100vh - 52px)",
      display: "flex", flexDirection: "column",
      fontFamily: "var(--font)",
    }}>
      {/* Breadcrumb */}
      <div style={{
        padding: "12px 24px",
        background: "var(--bg-card)",
        borderBottom: "1px solid var(--border)",
        display: "flex", alignItems: "center", gap: 8,
        flexShrink: 0,
      }}>
        <Link to="/" style={{
          display: "inline-flex", alignItems: "center", gap: 4,
          fontSize: 13, color: "var(--text-muted)", textDecoration: "none",
        }}>
          <ArrowLeft size={13} /> Command Center
        </Link>
        <span style={{ color: "var(--border)", fontSize: 12 }}>▸</span>
        <ProjectChip projectId={projectId} stopPropagation={false} />
        <span style={{ color: "var(--border)", fontSize: 12 }}>▸</span>
        <span style={{ fontSize: 13, color: "var(--text-primary)", fontWeight: 600 }}>Build Board</span>
        {/* Refresh sits immediately adjacent to "Build Board" (parent
            row uses gap:8). Previously this was right-aligned via
            marginLeft:auto, but the user's eye lands on the label, so
            the affordance reads more naturally next to it. */}
        <RefreshButton onClick={handleRefresh} refreshing={refreshing} />
      </div>

      {/* Empty state */}
      {tasks.length === 0 && (
        <div style={{
          margin: "60px auto", padding: 40, maxWidth: 600,
          textAlign: "center", color: "var(--text-muted)",
          background: "var(--bg-card)", border: "1px dashed var(--border)",
          borderRadius: "var(--radius)",
        }}>
          <div style={{ fontSize: 14, marginBottom: 6 }}>
            No task list yet for this project.
          </div>
          <Link
            to={`/projects/${projectId}`}
            style={{ fontSize: 12, color: "var(--accent)", textDecoration: "none" }}
          >
            Open the Project to generate one →
          </Link>
        </div>
      )}

      {/* Kanban */}
      {tasks.length > 0 && (
        <div style={{
          flex: 1, display: "grid",
          gridTemplateColumns: `repeat(${COLUMNS.length}, minmax(220px, 1fr))`,
          gap: 12, padding: 20, overflow: "auto",
        }}>
          {COLUMNS.map((col) => (
            <Column
              key={col.key}
              label={col.label}
              accent={col.accent}
              tasks={byColumn.get(col.key) || []}
              selectedTaskId={selectedTaskId}
              onSelect={(id) =>
                setSelectedTaskId(id === selectedTaskId ? null : id)
              }
            />
          ))}
        </div>
      )}

      {/* Popup window — opens over the board, draggable, non-modal */}
      {selectedCard && (
        <PopupWindow
          subtitle={selectedCard.task_id}
          title={selectedCard.title}
          onClose={() => setSelectedTaskId(null)}
        >
          <TaskDrillIn card={selectedCard} />
        </PopupWindow>
      )}
    </div>
  )
}

// ── Column ──────────────────────────────────────────────────────────────

function Column({
  label, accent, tasks, selectedTaskId, onSelect,
}: {
  label: string
  accent: string
  tasks: ProjectTask[]
  selectedTaskId: string | null
  onSelect: (taskId: string) => void
}) {
  return (
    <div style={{
      background: "var(--bg-card)",
      border: "1px solid var(--border)",
      borderRadius: "var(--radius)",
      display: "flex", flexDirection: "column",
      minWidth: 0,
    }}>
      <div style={{
        padding: "10px 12px",
        borderBottom: "1px solid var(--border)",
        display: "flex", alignItems: "center", gap: 8,
        fontSize: 11, fontWeight: 700, textTransform: "uppercase",
        letterSpacing: 1, color: "var(--text-secondary)",
      }}>
        <span style={{
          width: 6, height: 6, borderRadius: "50%",
          background: accent, flexShrink: 0,
        }} />
        {label}
        <span style={{
          marginLeft: "auto", color: "var(--text-muted)",
          fontFamily: "var(--font-mono)", fontSize: 11,
        }}>
          {tasks.length}
        </span>
      </div>

      <div style={{
        padding: 8, display: "flex", flexDirection: "column", gap: 8,
        minHeight: 100, flex: 1, overflowY: "auto",
      }}>
        {tasks.length === 0 ? (
          <div style={{
            color: "var(--text-muted)", fontSize: 11, fontStyle: "italic",
            textAlign: "center", padding: "20px 8px",
          }}>(empty)</div>
        ) : (
          tasks.map((t) => (
            <EnrichedTaskCard
              key={t.task_id}
              card={projectTaskToCard(t)}
              isSelected={t.task_id === selectedTaskId}
              onClick={() => onSelect(t.task_id)}
            />
          ))
        )}
      </div>
    </div>
  )
}
