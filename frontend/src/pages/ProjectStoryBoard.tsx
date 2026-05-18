/**
 * ProjectStoryBoard — Project-driven Build, Phase C (PDB-27 → PDB-31).
 *
 * The Story Board in project mode. Rows = the project's finalized tasks;
 * columns = task_status (Backlog / In Progress / Review / Testing / Deployed
 * / Failed). Cards with a request_id click through to the existing per-request
 * Story Board at /stories/:requestId (the drill-down).
 *
 * Lives at /stories/project/:projectId. The per-request board at
 * /stories/:requestId is untouched.
 */

import { useEffect, useMemo, useState, useRef } from "react"
import { Link, useParams } from "react-router-dom"
import { ArrowLeft, Github, Rocket } from "lucide-react"
import { api } from "../lib/api"
import { ProjectChip } from "../components/projects/ProjectChip"

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

type TaskStatus =
  | "backlog" | "dispatched" | "in_progress"
  | "review" | "testing" | "deployed" | "failed" | "cancelled"

const COLUMNS: { key: TaskStatus | "active" ; label: string; match: (s: TaskStatus) => boolean; accent: string }[] = [
  {
    key: "backlog", label: "Backlog",
    match: (s) => s === "backlog",
    accent: "var(--text-muted)",
  },
  {
    key: "active", label: "In Progress",
    match: (s) => s === "dispatched" || s === "in_progress",
    accent: "var(--accent)",
  },
  {
    key: "review", label: "Review",
    match: (s) => s === "review",
    accent: "var(--info, var(--accent))",
  },
  {
    key: "testing", label: "Testing",
    match: (s) => s === "testing",
    accent: "var(--warning, #f59e0b)",
  },
  {
    key: "deployed", label: "Deployed",
    match: (s) => s === "deployed",
    accent: "var(--success)",
  },
  {
    key: "failed", label: "Failed",
    match: (s) => s === "failed" || s === "cancelled",
    accent: "var(--danger)",
  },
]

export function ProjectStoryBoardPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const [tasks, setTasks] = useState<ProjectTask[] | null>(null)
  const [error, setError] = useState("")
  const wsRef = useRef<WebSocket | null>(null)

  const load = async () => {
    if (!projectId) return
    try {
      const res = await api.get(`/projects/${projectId}/tasks`)
      setTasks((res.data || []) as ProjectTask[])
    } catch (e: any) {
      setError(e?.message || "Failed to load tasks")
    }
  }

  useEffect(() => {
    load()
    // Polling fallback: refresh every 5s in case the WS handler drops
    // an event. The board is read-only; over-polling is harmless.
    const id = window.setInterval(load, 5000)
    return () => window.clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  // Live updates via the existing /ws/activity stream. The PDB-25 handler
  // already writes to project_tasks server-side; we just need to refresh
  // our local view when relevant events fire.
  useEffect(() => {
    if (!projectId) return
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/activity`)
    wsRef.current = ws
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        const t = msg.type || ""
        // Any request-status change OR a task-finalize against THIS project
        // should refresh the board.
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

  if (error) {
    return <div style={{ padding: 24, color: "var(--danger)" }}>{error}</div>
  }
  if (!tasks) {
    return <div style={{ padding: 24, color: "var(--text-muted)" }}>Loading board…</div>
  }

  return (
    <div style={{
      minHeight: "calc(100vh - 52px)",
      display: "flex", flexDirection: "column",
      fontFamily: "var(--font)",
    }}>
      {/* Breadcrumb (BRD-006) */}
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
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ── Column ──────────────────────────────────────────────────────────────

function Column({ label, accent, tasks }: { label: string; accent: string; tasks: ProjectTask[] }) {
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
          tasks.map((t) => <TaskCard key={t.task_id} task={t} accent={accent} />)
        )}
      </div>
    </div>
  )
}

// ── Card ────────────────────────────────────────────────────────────────

function TaskCard({ task, accent }: { task: ProjectTask; accent: string }) {
  const drillTarget = task.request_id ? `/stories/${task.request_id}` : null
  const inner = (
    <div style={{
      padding: "8px 10px",
      background: "var(--bg-hover)",
      border: `1px solid var(--border)`,
      borderLeft: `3px solid ${accent}`,
      borderRadius: "var(--radius)",
      cursor: drillTarget ? "pointer" : "default",
      transition: "border-color 0.15s, background 0.15s",
    }}
      onMouseEnter={(e) => {
        if (drillTarget) {
          e.currentTarget.style.borderColor = "var(--accent)"
          e.currentTarget.style.borderLeftColor = accent
        }
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = "var(--border)"
        e.currentTarget.style.borderLeftColor = accent
      }}
    >
      <div style={{
        display: "flex", alignItems: "center", gap: 4,
        fontSize: 10, fontFamily: "var(--font-mono)",
        color: "var(--text-muted)", marginBottom: 4,
      }}>
        <span>{task.task_id}</span>
        {task.priority === "high" && (
          <span style={{
            marginLeft: "auto", color: "var(--danger)",
            textTransform: "uppercase",
          }}>● high</span>
        )}
      </div>
      <div style={{
        fontSize: 12, fontWeight: 600, color: "var(--text-primary)",
        lineHeight: 1.3, marginBottom: 4,
      }}>
        {task.title}
      </div>
      <div style={{
        display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap",
        fontSize: 10, color: "var(--text-muted)",
      }}>
        {task.estimated_agent && (
          <span style={{
            padding: "1px 5px", borderRadius: 3,
            background: "var(--bg-card)", border: "1px solid var(--border)",
            fontFamily: "var(--font-mono)",
          }}>
            {task.estimated_agent.replace("_specialist", "").replace("_", " ")}
          </span>
        )}
        {task.request_id && (
          <span style={{
            display: "inline-flex", alignItems: "center", gap: 3,
            color: "var(--accent)", fontFamily: "var(--font-mono)",
          }}>
            <Rocket size={9} />
            {task.request_id}
          </span>
        )}
        {task.task_status === "deployed" && (
          <Github size={10} style={{ marginLeft: "auto", color: "var(--success)" }} />
        )}
      </div>
    </div>
  )
  if (drillTarget) {
    return (
      <Link to={drillTarget} style={{ textDecoration: "none", color: "inherit" }}>
        {inner}
      </Link>
    )
  }
  return inner
}
