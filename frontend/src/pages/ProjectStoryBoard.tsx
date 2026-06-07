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
import { EpicDetail } from "../components/board/EpicDetail"
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
  // BPD-32 — three fields the API exposes that drive parent chips +
  // blocker chain on cards. The API has populated these since BPD-003;
  // the page just didn't read them.
  feature_id: string | null
  depends_on: string[]
  primary_file: string | null
}

// Lightweight rows for the epic + feature dropdowns. The dropdowns
// don't need the full models; just enough to render the option labels
// and lookup parent titles when building cards.
interface EpicRow { epic_id: string; title: string }
interface FeatureRow { feature_id: string; epic_id: string; title: string }

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

function projectTaskToCard(
  t: ProjectTask,
  ctx?: {
    epicByFeatureId: Map<string, EpicRow>
    featureById: Map<string, FeatureRow>
    taskById: Map<string, ProjectTask>
  },
): CardData {
  // Extract phase prefix from title: "Phase 1: Foundation — Build X" →
  // phase = "Phase 1: Foundation", title = "Build X".
  let phase: string | null = null
  let title = t.title
  const m = t.title.match(/^(Phase\s+\d+:\s*[^—\-]+?)\s*[—\-]\s*(.+)$/)
  if (m) {
    phase = m[1].trim()
    title = m[2].trim()
  }

  // BPD-32 — populate card.bpd when we have lookup context. Resolves
  // depends_on task IDs to {title, status} so the chain tooltip can
  // tell the operator WHY a card is blocked, not just THAT it is.
  let bpd: CardData["bpd"] = undefined
  if (ctx) {
    const feature = t.feature_id ? ctx.featureById.get(t.feature_id) : undefined
    const epic = t.feature_id ? ctx.epicByFeatureId.get(t.feature_id) : undefined
    const resolved = (t.depends_on || []).map((depId) => {
      const dep = ctx.taskById.get(depId)
      return {
        task_id: depId,
        title: dep?.title || "(unknown task)",
        status: dep?.task_status || "unknown",
      }
    })
    if (epic || feature || resolved.length > 0) {
      bpd = {
        epic_id: epic?.epic_id ?? null,
        epic_title: epic?.title ?? null,
        feature_id: feature?.feature_id ?? null,
        feature_title: feature?.title ?? null,
        primary_file: t.primary_file ?? null,
        depends_on: resolved,
      }
    }
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
    bpd,
  }
}

export function ProjectStoryBoardPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const [tasks, setTasks] = useState<ProjectTask[] | null>(null)
  // BPD-32 — epic + feature lookup data drives both the filter
  // dropdowns AND the parent-chip rendering on cards.
  const [epics, setEpics] = useState<EpicRow[]>([])
  const [features, setFeatures] = useState<FeatureRow[]>([])
  const [filterEpicId, setFilterEpicId] = useState<string>("")     // "" = all
  const [filterFeatureId, setFilterFeatureId] = useState<string>("") // "" = all
  const [error, setError] = useState("")
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  // BPD-33 — when set, opens an epic-detail popup parallel to the
  // task popup. The two popups co-exist (each rendered by its own
  // PopupWindow, both draggable) so an operator can read a task's
  // drill-in WHILE inspecting its parent epic's rollup.
  const [selectedEpicId, setSelectedEpicId] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)

  const load = async () => {
    if (!projectId) return
    try {
      // Three parallel fetches — tasks are the only required one; epics
      // and features degrade gracefully (empty arrays → no filters,
      // no parent chips, cards still render). Promise.all so one slow
      // request doesn't serialize the others.
      const [tasksRes, epicsRes, featuresRes] = await Promise.all([
        api.get<{ data: ProjectTask[] }>(`/projects/${projectId}/tasks`),
        api.get<{ data: EpicRow[] }>(`/projects/${projectId}/epics`).catch(() => ({ data: [] as EpicRow[] })),
        api.get<{ data: FeatureRow[] }>(`/projects/${projectId}/features`).catch(() => ({ data: [] as FeatureRow[] })),
      ])
      setTasks((tasksRes.data || []) as ProjectTask[])
      setEpics((epicsRes.data || []) as EpicRow[])
      setFeatures((featuresRes.data || []) as FeatureRow[])
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

  // BPD-32 — single source of truth for the three lookup maps the
  // card renderer + filter logic share. Recomputed on tasks /
  // epics / features change; everything downstream reads from these.
  const lookups = useMemo(() => {
    const taskById = new Map<string, ProjectTask>()
    for (const t of tasks || []) taskById.set(t.task_id, t)
    const featureById = new Map<string, FeatureRow>()
    for (const f of features) featureById.set(f.feature_id, f)
    const epicById = new Map<string, EpicRow>()
    for (const e of epics) epicById.set(e.epic_id, e)
    // Convenience: epic looked up by feature_id (the card has feature_id,
    // not epic_id; this saves a two-step lookup at render time).
    const epicByFeatureId = new Map<string, EpicRow>()
    for (const f of features) {
      const e = epicById.get(f.epic_id)
      if (e) epicByFeatureId.set(f.feature_id, e)
    }
    return { taskById, featureById, epicById, epicByFeatureId }
  }, [tasks, epics, features])

  // BPD-32 — filtered task set drives both the column bins and the
  // visible-count summary. Filters are AND-combined (epic narrows
  // features; feature is the strongest filter and supersedes epic
  // when set). Tasks without a feature_id (e.g. legacy from
  // pre-BPD) match only when both filters are unset.
  const filteredTasks = useMemo(() => {
    if (!tasks) return null
    if (!filterEpicId && !filterFeatureId) return tasks
    return tasks.filter((t) => {
      if (filterFeatureId) return t.feature_id === filterFeatureId
      // epic filter only: include any task whose feature rolls up to
      // this epic
      const f = t.feature_id ? lookups.featureById.get(t.feature_id) : null
      return f?.epic_id === filterEpicId
    })
  }, [tasks, filterEpicId, filterFeatureId, lookups])

  // Available-feature list narrows when an epic is selected so the
  // operator can't pick a feature/epic mismatch that yields zero rows.
  const featureOptionsForFilter = useMemo(() => {
    if (!filterEpicId) return features
    return features.filter((f) => f.epic_id === filterEpicId)
  }, [features, filterEpicId])

  const byColumn = useMemo(() => {
    const map = new Map<string, ProjectTask[]>()
    COLUMNS.forEach((c) => map.set(c.key, []))
    if (filteredTasks) {
      for (const t of filteredTasks) {
        const col = COLUMNS.find((c) => c.match(t.task_status))
        if (col) map.get(col.key)!.push(t)
      }
    }
    return map
  }, [filteredTasks])

  const selectedCard = useMemo<CardData | null>(() => {
    if (!selectedTaskId || !tasks) return null
    const t = tasks.find((x) => x.task_id === selectedTaskId)
    return t ? projectTaskToCard(t, lookups) : null
  }, [selectedTaskId, tasks, lookups])

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

      {/* BPD-32 — Filter bar (epic + feature dropdowns). Only renders
          when there's something to filter (epics OR features non-empty);
          legacy projects with no BPD structure don't get the chrome.
          Selecting an epic narrows the feature dropdown; selecting a
          feature alone supersedes epic. "Clear" resets both. */}
      {tasks.length > 0 && (epics.length > 0 || features.length > 0) && (
        <div
          data-testid="board-filter-bar"
          style={{
            padding: "10px 24px",
            borderBottom: "1px solid var(--border)",
            display: "flex", alignItems: "center", gap: 12,
            background: "var(--bg-card)",
            fontSize: 12,
          }}
        >
          <span style={{
            color: "var(--text-muted)", fontWeight: 600,
            textTransform: "uppercase", letterSpacing: 0.5, fontSize: 10,
          }}>Filter:</span>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            <span style={{ color: "var(--text-secondary)" }}>Epic</span>
            <select
              data-testid="filter-epic"
              value={filterEpicId}
              onChange={(e) => {
                setFilterEpicId(e.target.value)
                // Reset feature filter if it no longer belongs to the
                // newly-selected epic — prevents an empty board.
                if (e.target.value && filterFeatureId) {
                  const f = lookups.featureById.get(filterFeatureId)
                  if (!f || f.epic_id !== e.target.value) setFilterFeatureId("")
                }
              }}
              style={{
                padding: "3px 6px",
                border: "1px solid var(--border)",
                borderRadius: 4,
                background: "var(--bg-card)",
                color: "var(--text-primary)",
                fontFamily: "var(--font)", fontSize: 12,
                maxWidth: 220,
              }}
            >
              <option value="">All epics ({epics.length})</option>
              {epics.map((e) => (
                <option key={e.epic_id} value={e.epic_id}>{e.title}</option>
              ))}
            </select>
          </label>
          <label style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            <span style={{ color: "var(--text-secondary)" }}>Feature</span>
            <select
              data-testid="filter-feature"
              value={filterFeatureId}
              onChange={(e) => setFilterFeatureId(e.target.value)}
              style={{
                padding: "3px 6px",
                border: "1px solid var(--border)",
                borderRadius: 4,
                background: "var(--bg-card)",
                color: "var(--text-primary)",
                fontFamily: "var(--font)", fontSize: 12,
                maxWidth: 220,
              }}
            >
              <option value="">
                All features ({featureOptionsForFilter.length}{filterEpicId ? " · scoped" : ""})
              </option>
              {featureOptionsForFilter.map((f) => (
                <option key={f.feature_id} value={f.feature_id}>{f.title}</option>
              ))}
            </select>
          </label>
          {(filterEpicId || filterFeatureId) && (
            <>
              <button
                data-testid="filter-clear"
                type="button"
                onClick={() => { setFilterEpicId(""); setFilterFeatureId("") }}
                style={{
                  padding: "3px 10px",
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  background: "transparent",
                  color: "var(--text-secondary)",
                  fontSize: 11, cursor: "pointer",
                }}
              >
                Clear
              </button>
              <span style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)", fontSize: 11 }}>
                {filteredTasks?.length ?? 0} / {tasks.length} tasks
              </span>
            </>
          )}
          {/* BPD-33 — one-click "open epic" drill-in for the currently-
              filtered epic (or the epic of the filtered feature). Hidden
              when no epic context is resolvable from the current filters. */}
          {(() => {
            const targetEpicId = filterEpicId
              || (filterFeatureId
                && lookups.featureById.get(filterFeatureId)?.epic_id)
              || ""
            if (!targetEpicId) return null
            return (
              <button
                data-testid="open-epic-detail"
                type="button"
                onClick={() => setSelectedEpicId(targetEpicId)}
                style={{
                  marginLeft: "auto",
                  padding: "3px 10px",
                  border: "1px solid var(--accent)",
                  borderRadius: 4,
                  background: "transparent",
                  color: "var(--accent)",
                  fontSize: 11, cursor: "pointer", fontWeight: 600,
                }}
              >
                Open epic details →
              </button>
            )
          })()}
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
              lookups={lookups}
              selectedTaskId={selectedTaskId}
              onSelect={(id) =>
                setSelectedTaskId(id === selectedTaskId ? null : id)
              }
              onEpicClick={(epicId) => setSelectedEpicId(epicId)}
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

      {/* BPD-33 — epic-detail popup. Co-exists with the task popup so
          an operator can compare a task's drill-in against its parent
          epic's rollup. Triggered by clicking an epic chip on any
          card (see EnrichedTaskCard's onEpicClick prop) OR by
          selecting the "Open epic" button on the filter bar (one-click
          drill-in for the currently-filtered epic). */}
      {selectedEpicId && projectId && (() => {
        const epic = lookups.epicById.get(selectedEpicId)
        return (
          <PopupWindow
            subtitle={selectedEpicId}
            title={epic?.title ? `Epic · ${epic.title}` : "Epic"}
            onClose={() => setSelectedEpicId(null)}
          >
            <EpicDetail projectId={projectId} epicId={selectedEpicId} />
          </PopupWindow>
        )
      })()}
    </div>
  )
}

// ── Column ──────────────────────────────────────────────────────────────

function Column({
  label, accent, tasks, lookups, selectedTaskId, onSelect, onEpicClick,
}: {
  label: string
  accent: string
  tasks: ProjectTask[]
  // BPD-32 — lookup context for parent-chip + blocker resolution.
  // Optional because tests / future callers may render bare cards.
  lookups?: {
    taskById: Map<string, ProjectTask>
    featureById: Map<string, FeatureRow>
    epicById: Map<string, EpicRow>
    epicByFeatureId: Map<string, EpicRow>
  }
  selectedTaskId: string | null
  onSelect: (taskId: string) => void
  // BPD-33 — fires when the user clicks the epic chip on a card.
  // Page wires this to open the EpicDetail popup.
  onEpicClick?: (epicId: string) => void
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
              card={projectTaskToCard(t, lookups)}
              isSelected={t.task_id === selectedTaskId}
              onClick={() => onSelect(t.task_id)}
              onEpicClick={onEpicClick}
            />
          ))
        )}
      </div>
    </div>
  )
}
