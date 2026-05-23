/**
 * BuildPlanView — three-level collapsible Epic → Feature → Task tree.
 *
 * Renders alongside the legacy flat TaskListEditor when epics exist.
 * For projects with no epics (legacy / freshly-created), this returns
 * null and the existing flat editor remains the primary surface.
 *
 * Default state: epics collapsed, showing per-epic status rollups.
 * Click expands to features; click again to tasks. Each level has
 * per-row actions matching BPD-30 (Generate Features / Generate Tasks /
 * Dispatch Feature / Dispatch Epic).
 *
 * Task cards open the existing TaskDrillIn popup via setPopupTaskId.
 */

import { useCallback, useEffect, useMemo, useState } from "react"
import {
  ChevronRight, ChevronDown, Layers, Box,
} from "lucide-react"
import { api } from "../../lib/api"
import { PopupWindow } from "../board/PopupWindow"
import { TaskDrillIn } from "../board/TaskDrillIn"
import type { CardData, TaskStatus, WorkflowStage } from "../board/types"

interface Epic {
  epic_id: string
  project_id: string
  title: string
  description: string
  acceptance_criteria: string
  list_status: string
  ordinal: number
}

interface Feature {
  feature_id: string
  epic_id: string
  project_id: string
  title: string
  description: string
  acceptance_criteria: string
  depends_on: string[]
  list_status: string
  ordinal: number
}

interface Task {
  task_id: string
  project_id: string
  feature_id: string | null
  ordinal: number
  title: string
  description: string
  task_status: string
  list_status: string
  request_id: string | null
  depends_on: string[]
  primary_file: string | null
  expected_loc: number | null
  acceptance_test: string | null
  estimated_agent: string | null
  priority: string
}

interface Props {
  projectId: string
}

export function BuildPlanView({ projectId }: Props) {
  const [epics, setEpics] = useState<Epic[]>([])
  const [features, setFeatures] = useState<Feature[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [expandedEpics, setExpandedEpics] = useState<Set<string>>(new Set())
  const [expandedFeatures, setExpandedFeatures] = useState<Set<string>>(new Set())
  const [busyId, setBusyId] = useState<string | null>(null)
  const [popupTaskId, setPopupTaskId] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [e, f, t] = await Promise.all([
        api.get(`/projects/${projectId}/epics`),
        api.get(`/projects/${projectId}/features`),
        api.get(`/projects/${projectId}/tasks`),
      ])
      setEpics(e?.data || [])
      setFeatures(f?.data || [])
      setTasks(t?.data || [])
    } catch {/* soft */}
  }, [projectId])

  useEffect(() => { void load() }, [load])

  const tasksByFeature = useMemo(() => {
    const m = new Map<string, Task[]>()
    for (const t of tasks) {
      if (!t.feature_id) continue
      if (!m.has(t.feature_id)) m.set(t.feature_id, [])
      m.get(t.feature_id)!.push(t)
    }
    return m
  }, [tasks])

  const featuresByEpic = useMemo(() => {
    const m = new Map<string, Feature[]>()
    for (const f of features) {
      if (!m.has(f.epic_id)) m.set(f.epic_id, [])
      m.get(f.epic_id)!.push(f)
    }
    return m
  }, [features])

  // No epics → nothing to render here; the legacy flat editor is the
  // primary view for this project.
  if (epics.length === 0) return null

  const toggleEpic = (id: string) => {
    setExpandedEpics((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }
  const toggleFeature = (id: string) => {
    setExpandedFeatures((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const generateFeatures = async (epicId: string) => {
    setBusyId(epicId)
    try {
      await api.post(`/projects/${projectId}/epics/${epicId}/features/generate`, {})
      await load()
    } catch (e: any) {
      window.alert(`Generate features failed: ${e?.message || e}`)
    } finally { setBusyId(null) }
  }
  const generateTasks = async (featureId: string) => {
    setBusyId(featureId)
    try {
      await api.post(`/projects/${projectId}/features/${featureId}/tasks/generate`, {})
      await load()
    } catch (e: any) {
      window.alert(`Generate tasks failed: ${e?.message || e}`)
    } finally { setBusyId(null) }
  }
  const dispatchEpic = async (epicId: string) => {
    setBusyId(epicId)
    try {
      const res = await api.post(`/projects/${projectId}/build/dispatch-epic/${epicId}`, {})
      const m = res?.meta || {}
      window.alert(`Epic dispatched: ${m.dispatched_count} fired, ${m.blocked_count} blocked, ${m.skipped_count} skipped.`)
      await load()
    } catch (e: any) {
      window.alert(`Dispatch epic failed: ${e?.message || e}`)
    } finally { setBusyId(null) }
  }
  const dispatchFeature = async (featureId: string) => {
    setBusyId(featureId)
    try {
      const res = await api.post(`/projects/${projectId}/build/dispatch-feature/${featureId}`, {})
      const m = res?.meta || {}
      window.alert(`Feature dispatched: ${m.dispatched_count} fired, ${m.blocked_count} blocked.`)
      await load()
    } catch (e: any) {
      window.alert(`Dispatch feature failed: ${e?.message || e}`)
    } finally { setBusyId(null) }
  }

  const selectedTask = popupTaskId
    ? tasks.find((t) => t.task_id === popupTaskId) ?? null
    : null
  // Resolve depends_on task_ids → {task_id, title, status} for the
  // popup's BPD chip section. Dangling refs (deleted blockers) get a
  // synthetic "(missing)" entry so the user sees the broken edge.
  const tasksById = useMemo(() => {
    const m = new Map<string, Task>()
    for (const t of tasks) m.set(t.task_id, t)
    return m
  }, [tasks])
  let selectedFeature: Feature | null = null
  let selectedEpic: Epic | null = null
  if (selectedTask && selectedTask.feature_id) {
    selectedFeature = features.find((f) => f.feature_id === selectedTask.feature_id) || null
    if (selectedFeature) {
      selectedEpic = epics.find((e) => e.epic_id === selectedFeature!.epic_id) || null
    }
  }
  const selectedCard: CardData | null = selectedTask
    ? {
        id: selectedTask.task_id,
        task_id: selectedTask.task_id,
        request_id: selectedTask.request_id,
        phase: null,
        title: selectedTask.title,
        description: selectedTask.description,
        type: selectedTask.estimated_agent?.replace("_specialist", "") || null,
        agent: selectedTask.estimated_agent,
        priority: (selectedTask.priority as "high" | "medium" | "low") || "medium",
        status: selectedTask.task_status as TaskStatus,
        current_stage: null as WorkflowStage | null,
        bpd: {
          epic_id: selectedEpic?.epic_id ?? null,
          epic_title: selectedEpic?.title ?? null,
          feature_id: selectedFeature?.feature_id ?? null,
          feature_title: selectedFeature?.title ?? null,
          primary_file: selectedTask.primary_file,
          expected_loc: selectedTask.expected_loc,
          acceptance_test: selectedTask.acceptance_test,
          depends_on: (selectedTask.depends_on || []).map((depId) => {
            const dep = tasksById.get(depId)
            return dep
              ? { task_id: depId, title: dep.title, status: dep.task_status }
              : { task_id: depId, title: "(missing — deleted task)", status: "missing" }
          }),
        },
      }
    : null

  // Status rollup helpers
  const featureComplete = (fid: string): boolean => {
    const ft = tasksByFeature.get(fid) || []
    return ft.length > 0 && ft.every((t) => t.task_status === "deployed")
  }
  const epicRollup = (eid: string) => {
    const eFeatures = featuresByEpic.get(eid) || []
    const featuresDone = eFeatures.filter((f) => featureComplete(f.feature_id)).length
    const eTasks = eFeatures.flatMap((f) => tasksByFeature.get(f.feature_id) || [])
    const tasksDone = eTasks.filter((t) => t.task_status === "deployed").length
    const blockedSet = new Set(
      eTasks.filter((t) =>
        t.task_status === "backlog" && t.depends_on && t.depends_on.length > 0
      ).map((t) => t.task_id),
    )
    return {
      featureCount: eFeatures.length,
      featuresDone,
      taskCount: eTasks.length,
      tasksDone,
      blocked: blockedSet.size,
    }
  }

  return (
    <div style={{
      marginTop: 16, padding: 14,
      background: "var(--bg-card)", border: "1px solid var(--border)",
      borderRadius: "var(--radius)",
    }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 8, marginBottom: 12,
      }}>
        <Layers size={14} color="var(--accent)" />
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>
          Build Plan · Epic → Feature → Task
        </h3>
        <span style={{
          marginLeft: "auto", fontSize: 11, color: "var(--text-muted)",
          fontFamily: "var(--font-mono)",
        }}>
          {epics.length} epics · {features.length} features · {tasks.filter((t) => t.feature_id).length} BPD tasks
        </span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {epics.map((e) => {
          const exp = expandedEpics.has(e.epic_id)
          const r = epicRollup(e.epic_id)
          const isComplete = r.featureCount > 0 && r.featuresDone === r.featureCount
          return (
            <div
              key={e.epic_id}
              style={{
                background: "var(--bg-hover)",
                border: "1px solid var(--border)",
                borderLeft: `3px solid ${isComplete ? "var(--success)" : "var(--info, #b026ff)"}`,
                borderRadius: 3,
                overflow: "hidden",
              }}
            >
              <div
                onClick={() => toggleEpic(e.epic_id)}
                style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "8px 10px", cursor: "pointer",
                }}
              >
                {exp ? <ChevronDown size={12} color="var(--text-muted)" /> : <ChevronRight size={12} color="var(--text-muted)" />}
                <span style={{
                  padding: "1px 6px", borderRadius: 2,
                  fontSize: 10, fontFamily: "var(--font-mono)",
                  background: "color-mix(in srgb, var(--info, #b026ff) 15%, transparent)",
                  color: "var(--info, #b026ff)",
                  border: "1px solid var(--info, #b026ff)",
                  textTransform: "uppercase", letterSpacing: 0.5,
                }}>
                  Epic
                </span>
                <span style={{ fontWeight: 600, fontSize: 13, color: "var(--text-primary)" }}>
                  {e.title}
                </span>
                <span style={{ marginLeft: "auto", display: "inline-flex", gap: 6, fontSize: 10, fontFamily: "var(--font-mono)" }}>
                  <Seg label={`${r.featuresDone}/${r.featureCount} features`} tone={isComplete ? "good" : "muted"} />
                  <Seg label={`${r.tasksDone}/${r.taskCount} tasks`} tone="muted" />
                  {r.blocked > 0 && <Seg label={`${r.blocked} blocked`} tone="warn" />}
                </span>
                <ActionBtn
                  onClick={(ev) => { ev.stopPropagation(); void generateFeatures(e.epic_id) }}
                  busy={busyId === e.epic_id}
                  label="+ Features"
                  title="Generate features under this epic"
                />
                <ActionBtn
                  onClick={(ev) => { ev.stopPropagation(); void dispatchEpic(e.epic_id) }}
                  busy={busyId === e.epic_id}
                  label="🚀 Dispatch"
                  title="Dispatch every unblocked task in this epic"
                  primary
                />
              </div>

              {exp && (
                <div style={{ padding: "0 10px 8px 28px" }}>
                  {(featuresByEpic.get(e.epic_id) || []).map((f) => {
                    const fExp = expandedFeatures.has(f.feature_id)
                    const ft = tasksByFeature.get(f.feature_id) || []
                    const ftDone = ft.filter((t) => t.task_status === "deployed").length
                    const fComplete = ft.length > 0 && ftDone === ft.length
                    return (
                      <div
                        key={f.feature_id}
                        style={{
                          background: "var(--bg-card)",
                          border: "1px solid var(--border)",
                          borderLeft: `2px solid ${fComplete ? "var(--success)" : "var(--accent)"}`,
                          borderRadius: 3, marginTop: 4, overflow: "hidden",
                        }}
                      >
                        <div
                          onClick={() => toggleFeature(f.feature_id)}
                          style={{
                            display: "flex", alignItems: "center", gap: 8,
                            padding: "6px 10px", cursor: "pointer",
                          }}
                        >
                          {fExp ? <ChevronDown size={11} color="var(--text-muted)" /> : <ChevronRight size={11} color="var(--text-muted)" />}
                          <span style={{
                            padding: "1px 5px", borderRadius: 2,
                            fontSize: 9, fontFamily: "var(--font-mono)",
                            background: fComplete
                              ? "color-mix(in srgb, var(--success) 14%, transparent)"
                              : "color-mix(in srgb, var(--accent) 14%, transparent)",
                            color: fComplete ? "var(--success)" : "var(--accent)",
                            border: `1px solid ${fComplete ? "var(--success)" : "var(--accent)"}`,
                            textTransform: "uppercase", letterSpacing: 0.5,
                          }}>
                            Feature
                          </span>
                          <span style={{ fontWeight: 500, fontSize: 12, color: "var(--text-primary)" }}>
                            {f.title}
                          </span>
                          <span style={{ marginLeft: "auto", fontSize: 10, fontFamily: "var(--font-mono)", color: fComplete ? "var(--success)" : "var(--text-muted)" }}>
                            {ftDone}/{ft.length}{fComplete ? " ✓" : ""}
                          </span>
                          <ActionBtn
                            onClick={(ev) => { ev.stopPropagation(); void generateTasks(f.feature_id) }}
                            busy={busyId === f.feature_id}
                            label="+ Tasks"
                            title="Generate atomic tasks under this feature"
                          />
                          <ActionBtn
                            onClick={(ev) => { ev.stopPropagation(); void dispatchFeature(f.feature_id) }}
                            busy={busyId === f.feature_id}
                            label="🚀"
                            title="Dispatch unblocked tasks in this feature"
                            primary
                          />
                        </div>
                        {fExp && (
                          <div style={{ padding: "0 10px 8px 24px", display: "flex", flexDirection: "column", gap: 4 }}>
                            {ft.length === 0 && (
                              <div style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic", padding: "4px 0" }}>
                                No tasks yet. Click "+ Tasks" to generate.
                              </div>
                            )}
                            {ft.map((t) => (
                              <TaskRow
                                key={t.task_id}
                                task={t}
                                onClick={() => setPopupTaskId(t.task_id)}
                              />
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })}
                  {(featuresByEpic.get(e.epic_id) || []).length === 0 && (
                    <div style={{ fontSize: 11, color: "var(--text-muted)", fontStyle: "italic", padding: "4px 0" }}>
                      No features yet. Click "+ Features" to generate.
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {selectedCard && (
        <PopupWindow
          subtitle={selectedCard.task_id || ""}
          title={selectedCard.title}
          onClose={() => setPopupTaskId(null)}
          width={640}
          maxHeight="80vh"
        >
          <TaskDrillIn card={selectedCard} />
        </PopupWindow>
      )}
    </div>
  )
}

function TaskRow({ task, onClick }: { task: Task; onClick: () => void }) {
  const statusColor =
    task.task_status === "deployed" ? "var(--success)" :
    task.task_status === "failed" || task.task_status === "cancelled" ? "var(--danger)" :
    task.task_status === "in_progress" || task.task_status === "review" || task.task_status === "testing" ? "var(--accent)" :
    "var(--text-muted)"
  const blocked = task.task_status === "backlog" && task.depends_on && task.depends_on.length > 0
  return (
    <div
      onClick={onClick}
      title="Click to view full task detail"
      style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "4px 8px", fontSize: 11,
        background: "var(--bg-hover)", border: "1px solid var(--border)",
        borderRadius: 2, cursor: "pointer",
      }}
    >
      <Box size={10} color="var(--text-muted)" />
      <span style={{
        fontFamily: "var(--font-mono)", fontSize: 10,
        padding: "1px 6px", borderRadius: 2,
        background: "color-mix(in srgb, var(--accent) 10%, transparent)",
        color: "var(--accent)", border: "1px solid var(--accent)",
        whiteSpace: "nowrap",
      }}>
        {task.task_id}
      </span>
      <span style={{ color: "var(--text-primary)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {task.title}
      </span>
      {task.primary_file && (
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--text-muted)", whiteSpace: "nowrap" }}>
          {task.primary_file}
        </span>
      )}
      {blocked && (
        <span style={{
          fontSize: 9, padding: "1px 5px", borderRadius: 2,
          background: "color-mix(in srgb, var(--warning, #d4a017) 14%, transparent)",
          color: "var(--warning, #d4a017)", border: "1px solid var(--warning, #d4a017)",
          fontFamily: "var(--font-mono)",
        }}>
          🔗 {task.depends_on.length}
        </span>
      )}
      <span style={{
        fontSize: 9, padding: "1px 6px", borderRadius: 2,
        color: statusColor, border: `1px solid ${statusColor}`,
        background: "color-mix(in srgb, " + statusColor + " 12%, transparent)",
        fontFamily: "var(--font-mono)", textTransform: "uppercase", letterSpacing: 0.5,
      }}>
        {task.task_status}
      </span>
    </div>
  )
}

function ActionBtn({
  onClick, busy, label, title, primary,
}: {
  onClick: (e: React.MouseEvent) => void
  busy: boolean
  label: string
  title: string
  primary?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      title={title}
      style={{
        padding: "2px 8px", fontSize: 10,
        background: primary
          ? "color-mix(in srgb, var(--accent) 14%, transparent)"
          : "transparent",
        color: primary ? "var(--accent)" : "var(--text-secondary)",
        border: `1px solid ${primary ? "var(--accent)" : "var(--border)"}`,
        borderRadius: 2, cursor: busy ? "wait" : "pointer",
        fontFamily: "var(--font)", whiteSpace: "nowrap",
        opacity: busy ? 0.6 : 1,
      }}
    >
      {busy ? "…" : label}
    </button>
  )
}

function Seg({ label, tone }: { label: string; tone: "good" | "warn" | "muted" }) {
  const color =
    tone === "good" ? "var(--success)" :
    tone === "warn" ? "var(--warning, #d4a017)" :
    "var(--text-secondary)"
  return (
    <span style={{
      padding: "1px 6px", borderRadius: 2,
      background: "var(--bg-card)", border: `1px solid ${tone === "muted" ? "var(--border)" : color}`,
      color,
    }}>
      {label}
    </span>
  )
}
