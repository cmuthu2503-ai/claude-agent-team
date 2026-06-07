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
  ChevronRight, ChevronDown, Layers, Box, Trash2, Lock, Loader2,
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
  /** Bumped by the parent BuildPlanGenerator after each successful pass
   *  (epics/features/tasks). Reloads the tree so the user sees new rows
   *  immediately instead of having to refresh the page. Also re-runs
   *  after bulk-delete / dispatch actions for the same reason. */
  refreshNonce?: number
}

export function BuildPlanView({ projectId, refreshNonce = 0 }: Props) {
  const [epics, setEpics] = useState<Epic[]>([])
  const [features, setFeatures] = useState<Feature[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [expandedEpics, setExpandedEpics] = useState<Set<string>>(new Set())
  const [expandedFeatures, setExpandedFeatures] = useState<Set<string>>(new Set())
  const [busyId, setBusyId] = useState<string | null>(null)
  // Human-readable description of what the busy row is doing. Powers
  // the "BUSY · <action>" chip + tooltip on the animated overlay so
  // the user knows whether a 90s wait is "generating features" or
  // "generating 6 features × tasks" instead of just seeing a spinner.
  const [busyLabel, setBusyLabel] = useState<string>("")
  const [popupTaskId, setPopupTaskId] = useState<string | null>(null)

  // Centralized helpers so every action sets BOTH busyId AND a label
  // in lockstep — prevents the overlay from showing a stale label when
  // a different action takes over the same row mid-flight.
  const startBusy = (id: string, label: string) => {
    setBusyId(id)
    setBusyLabel(label)
  }
  const stopBusy = () => {
    setBusyId(null)
    setBusyLabel("")
  }
  // Multi-select state for bulk delete. Kept as a Set so toggling is
  // O(1) and the dependency graph for the action buttons stays cheap.
  // Pruned in a useEffect whenever `epics` changes so stale selections
  // (e.g. after a successful bulk delete) don't keep the action bar
  // showing phantom counts.
  const [selectedEpicIds, setSelectedEpicIds] = useState<Set<string>>(new Set())
  const [bulkDeleting, setBulkDeleting] = useState(false)

  // BPD-51 — readiness gate shared with BuildPlanGenerator. Per-row
  // "+ Features" / "+ Tasks" action buttons get disabled when PRD or
  // API Spec is missing, with the same tooltip as the top-level
  // generator. Without this, the user could sidestep the BuildPlanGenerator
  // gate by clicking the per-row buttons in the tree below.
  const [readinessLocked, setReadinessLocked] = useState(false)
  const [gateMessage, setGateMessage] = useState("")

  const load = useCallback(async () => {
    // Per-promise catch (matching BuildPlanGenerator) so a transient
    // failure on any one endpoint doesn't blank the whole tree. The
    // earlier `Promise.all + single try/catch` shape made the view
    // render null whenever /features or /tasks hiccuped, even when
    // /epics had data — caller saw the epics:N pill update on the
    // generator above but the tree below stayed invisible.
    const [e, f, t, prd, spec] = await Promise.all([
      api.get(`/projects/${projectId}/epics`).catch(() => ({ data: [] })),
      api.get(`/projects/${projectId}/features`).catch(() => ({ data: [] })),
      api.get(`/projects/${projectId}/tasks`).catch(() => ({ data: [] })),
      api.get(`/projects/${projectId}/prd`).catch(() => ({ data: null })),
      api.get(`/projects/${projectId}/api-spec`).catch(() => ({ data: null })),
    ])
    setEpics(e?.data || [])
    setFeatures(f?.data || [])
    setTasks(t?.data || [])
    // Same defensive status check as BuildPlanGenerator — lowercase +
    // tolerate the enum-object shape — so the per-row "+ Features" /
    // "+ Tasks" buttons unlock consistently with the top-level gate.
    const prdFinal = String(prd?.data?.status ?? "").toLowerCase() === "finalized"
    const specFinal = String(spec?.data?.status ?? "").toLowerCase() === "finalized"
    const missing: string[] = []
    if (!prdFinal) missing.push("PRD")
    if (!specFinal) missing.push("API Specification")
    setReadinessLocked(missing.length > 0)
    setGateMessage(
      missing.length === 0
        ? ""
        : `Finalize the ${missing.join(" and ")} before generating epics, features, or tasks.`,
    )
  }, [projectId])

  // Reload on mount, on projectId change, AND whenever the parent bumps
  // refreshNonce (after a successful BPD pass / bulk-delete / dispatch).
  // `load` is a stable useCallback keyed on projectId so the only ways
  // this effect re-fires are projectId switches and nonce bumps.
  useEffect(() => { void load() }, [load, refreshNonce])

  // Prune selection state whenever the epic list changes (e.g. after a
  // successful bulk delete or a refetch from another tab). Without this,
  // the "N selected" badge would keep referencing epic_ids that no
  // longer exist, and the next bulk_delete call would hit `not_found`
  // on every row.
  useEffect(() => {
    setSelectedEpicIds((prev) => {
      const alive = new Set(epics.map((e) => e.epic_id))
      const next = new Set<string>()
      for (const id of prev) if (alive.has(id)) next.add(id)
      return next.size === prev.size ? prev : next
    })
  }, [epics])

  const tasksByFeature = useMemo(() => {
    const m = new Map<string, Task[]>()
    for (const t of tasks) {
      if (!t.feature_id) continue
      if (!m.has(t.feature_id)) m.set(t.feature_id, [])
      m.get(t.feature_id)!.push(t)
    }
    // L20 canary — N tasks in but 0 grouped out means feature_id is
    // missing from the wire format (the serializer dropped it again).
    // One-line console glance beats a 3-session debugging spiral. Dev
    // mode only so we don't spam users' consoles in production.
    if (import.meta.env.DEV && tasks.length > 0 && m.size === 0) {
      // eslint-disable-next-line no-console
      console.warn(
        "[BuildPlanView] received", tasks.length, "tasks but grouped 0 features.",
        "Check the /tasks response has feature_id on each row.",
        "First task keys:", Object.keys(tasks[0] || {}),
      )
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

  // Legacy tasks = tasks with feature_id IS NULL (BPD-402). Render
  // them under a synthetic "Legacy" pseudo-epic at the bottom of the
  // tree so the user can see at a glance "these N tasks predate the
  // BPD decomposition." No actions on the legacy group itself — its
  // tasks are still dispatched via the existing TaskListEditor.
  const legacyTasks = useMemo(
    () => tasks.filter((t) => !t.feature_id),
    [tasks],
  )
  const hasLegacy = legacyTasks.length > 0

  // Pre-compute task_id → Task lookup map so the popup-detail
  // renderer (below the early return) can resolve depends_on chips
  // without a fresh scan per chip. Declared BEFORE the early return
  // to satisfy the Rules of Hooks — moving it after the conditional
  // null-return caused "Rendered more hooks than during the previous
  // render" the first time the project transitioned from empty to
  // having data.
  const tasksById = useMemo(() => {
    const m = new Map<string, Task>()
    for (const t of tasks) m.set(t.task_id, t)
    return m
  }, [tasks])

  // No epics AND no legacy → nothing to render here; the legacy flat
  // editor is the primary view for this project.
  if (epics.length === 0 && !hasLegacy) return null

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

  // Busy labels mirror the backend's `single_agent_call(label=...)`
  // strings (see src/api/routes/projects.py generate_features /
  // generate_tasks_for_feature). Keeping the two strings aligned by
  // construction prevents the row-chip vs Active-Agents-feed mismatch
  // the user reported (frontend said "Generating tasks", backend said
  // "BPD Pass 3 · Generating Atomic Tasks for <feature>"). Both
  // surfaces now display the same canonical descriptor.
  const generateFeatures = async (epicId: string, epicTitle: string) => {
    startBusy(epicId, `BPD Pass 2 · Generating Features for ${epicTitle.slice(0, 40)}`)
    try {
      await api.post(`/projects/${projectId}/epics/${epicId}/features/generate`, {})
      await load()
    } catch (e: any) {
      window.alert(`Generate features failed: ${e?.message || e}`)
    } finally { stopBusy() }
  }
  const generateTasks = async (featureId: string, featureTitle: string) => {
    startBusy(featureId, `BPD Pass 3 · Generating Atomic Tasks for ${featureTitle.slice(0, 40)}`)
    try {
      await api.post(`/projects/${projectId}/features/${featureId}/tasks/generate`, {})
      await load()
    } catch (e: any) {
      window.alert(`Generate tasks failed: ${e?.message || e}`)
    } finally { stopBusy() }
  }
  // Finalize one epic + its features + tasks (cascading subtree).
  // Backend is idempotent — already-finalized rows stay finalized.
  // Quick + lightweight; no LLM cost so no confirmation gate beyond a
  // simple ack.
  const finalizeEpic = async (epicId: string, epicTitle: string) => {
    if (!window.confirm(
      `Finalize epic "${epicTitle}" and every feature + task under it?\n\n` +
      `This locks in the structure — generation passes still work but ` +
      `regenerating these rows requires archiving first. Task statuses ` +
      `(backlog / dispatched / etc.) are NOT changed.`,
    )) return
    startBusy(epicId, "Finalizing epic")
    try {
      const res = await api.post(`/projects/${projectId}/epics/${epicId}/finalize`, {})
      const d = res?.data || {}
      window.alert(
        `Finalized: ${d.epics_finalized || 0} epic, ` +
        `${d.features_finalized || 0} features, ${d.tasks_finalized || 0} tasks.`,
      )
      await load()
    } catch (e: any) {
      window.alert(`Finalize epic failed: ${e?.message || e}`)
    } finally { stopBusy() }
  }
  // Finalize one feature + its tasks (no cascade upward to the epic).
  const finalizeFeature = async (featureId: string, featureTitle: string) => {
    if (!window.confirm(
      `Finalize feature "${featureTitle}" and its tasks?\n\n` +
      `Locks in this feature's spec + task structure. The parent epic ` +
      `stays draft if it isn't already finalized.`,
    )) return
    startBusy(featureId, "Finalizing feature")
    try {
      const res = await api.post(`/projects/${projectId}/features/${featureId}/finalize`, {})
      const d = res?.data || {}
      window.alert(
        `Finalized: ${d.features_finalized || 0} feature, ${d.tasks_finalized || 0} tasks.`,
      )
      await load()
    } catch (e: any) {
      window.alert(`Finalize feature failed: ${e?.message || e}`)
    } finally { stopBusy() }
  }
  // Inverse of finalizeEpic — flip an epic subtree back to draft.
  // No LLM cost, just a row-status flip; lightweight ack confirmation
  // so re-clicks aren't silent but also aren't punishing.
  const unfinalizeEpic = async (epicId: string, epicTitle: string) => {
    if (!window.confirm(
      `Unfinalize epic "${epicTitle}" and every feature + task under it?\n\n` +
      `Flips them back to draft so they can be regenerated or edited. ` +
      `Task work history (deployed status, request_id) is untouched.`,
    )) return
    startBusy(epicId, "Unfinalizing epic")
    try {
      const res = await api.post(`/projects/${projectId}/epics/${epicId}/unfinalize`, {})
      const d = res?.data || {}
      window.alert(
        `Unfinalized: ${d.epics_unfinalized || 0} epic, ` +
        `${d.features_unfinalized || 0} features, ${d.tasks_unfinalized || 0} tasks.`,
      )
      await load()
    } catch (e: any) {
      window.alert(`Unfinalize epic failed: ${e?.message || e}`)
    } finally { stopBusy() }
  }
  const unfinalizeFeature = async (featureId: string, featureTitle: string) => {
    if (!window.confirm(
      `Unfinalize feature "${featureTitle}" and its tasks?\n\n` +
      `Flips them back to draft. The parent epic's status is unchanged.`,
    )) return
    startBusy(featureId, "Unfinalizing feature")
    try {
      const res = await api.post(`/projects/${projectId}/features/${featureId}/unfinalize`, {})
      const d = res?.data || {}
      window.alert(
        `Unfinalized: ${d.features_unfinalized || 0} feature, ${d.tasks_unfinalized || 0} tasks.`,
      )
      await load()
    } catch (e: any) {
      window.alert(`Unfinalize feature failed: ${e?.message || e}`)
    } finally { stopBusy() }
  }
  // Batch Pass 3 for every feature under one epic. ~3-5 min for a
  // typical 5-7-feature epic vs ~24 min for the whole-project batch.
  // Confirms first because LLM cost is non-trivial (one call per
  // feature, ~30s each) and there's no in-flight cancel today.
  const generateTasksForEpic = async (epicId: string, epicTitle: string) => {
    const featureCount = featuresByEpic.get(epicId)?.length ?? 0
    if (featureCount === 0) {
      window.alert(`No features under "${epicTitle}" yet. Run "+ Features" first.`)
      return
    }
    if (!window.confirm(
      `Generate atomic tasks for all ${featureCount} feature${featureCount === 1 ? "" : "s"} under "${epicTitle}"?\n\n` +
      `Wall time: roughly ${featureCount * 30}s (one LLM call per feature, ~30s each).\n\n` +
      `This replaces any existing draft tasks under this epic's features.`,
    )) return
    startBusy(epicId, `Generating tasks for ${featureCount} features (~${featureCount * 30}s)`)
    try {
      const res = await api.post(
        `/projects/${projectId}/epics/${epicId}/tasks/generate-all`, {},
      )
      const m = res?.meta || {}
      if (res?.error === "partial") {
        window.alert(
          `Partial completion: stopped at feature ${m.failed_at_feature}.\n\n` +
          `Got: ${JSON.stringify(res?.data?.task_counts ?? {})}\n` +
          `Inner error: ${JSON.stringify(m.inner)}`,
        )
      } else {
        window.alert(
          `Generated ${m.total_tasks ?? 0} tasks across ${m.feature_count ?? featureCount} features under "${m.epic_title ?? epicTitle}".`,
        )
      }
      await load()
    } catch (e: any) {
      window.alert(`Generate tasks for epic failed: ${e?.message || e}`)
    } finally { stopBusy() }
  }
  const dispatchEpic = async (epicId: string) => {
    startBusy(epicId, "Dispatching epic")
    try {
      const res = await api.post(`/projects/${projectId}/build/dispatch-epic/${epicId}`, {})
      const m = res?.meta || {}
      window.alert(`Epic dispatched: ${m.dispatched_count} fired, ${m.blocked_count} blocked, ${m.skipped_count} skipped.`)
      await load()
    } catch (e: any) {
      window.alert(`Dispatch epic failed: ${e?.message || e}`)
    } finally { stopBusy() }
  }
  const dispatchFeature = async (featureId: string) => {
    startBusy(featureId, "Dispatching feature")
    try {
      const res = await api.post(`/projects/${projectId}/build/dispatch-feature/${featureId}`, {})
      const m = res?.meta || {}
      window.alert(`Feature dispatched: ${m.dispatched_count} fired, ${m.blocked_count} blocked.`)
      await load()
    } catch (e: any) {
      window.alert(`Dispatch feature failed: ${e?.message || e}`)
    } finally { stopBusy() }
  }

  // ── Selection + bulk delete ─────────────────────────────────────────
  const toggleEpicSelected = (id: string) => {
    setSelectedEpicIds((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }
  const selectAllEpics = () => {
    setSelectedEpicIds(new Set(epics.map((e) => e.epic_id)))
  }
  const clearEpicSelection = () => setSelectedEpicIds(new Set())
  const bulkDeleteEpics = async () => {
    const ids = Array.from(selectedEpicIds)
    if (ids.length === 0) return
    // Heavy confirmation — deletion cascades to features and NULLs every
    // task's feature_id back-link. Quote the count + warn about the
    // cascade so the user can't blow away a project's plan by accident.
    const ok = window.confirm(
      `DELETE ${ids.length} epic${ids.length === 1 ? "" : "s"}?\n\n` +
      "This also removes every feature under those epics and unlinks any tasks " +
      "from them (tasks themselves survive but become 'Legacy' rows).\n\n" +
      "This cannot be undone."
    )
    if (!ok) return
    setBulkDeleting(true)
    try {
      const res = await api.post(
        `/projects/${projectId}/epics/bulk_delete`,
        { epic_ids: ids },
      )
      const m = res?.meta || {}
      const skipped = res?.data?.skipped || []
      const lines = [
        `Deleted ${m.deleted_count}/${m.requested} epic${m.requested === 1 ? "" : "s"}.`,
      ]
      if (m.cascaded_features > 0) lines.push(`Cascaded: ${m.cascaded_features} features removed.`)
      if (m.cascaded_tasks_unlinked > 0) lines.push(`Unlinked: ${m.cascaded_tasks_unlinked} tasks (now Legacy).`)
      if (skipped.length > 0) {
        const reasons = skipped.slice(0, 5).map((s: any) => `  • ${s.epic_id}: ${s.reason}`).join("\n")
        lines.push(`\nSkipped:\n${reasons}${skipped.length > 5 ? `\n  …and ${skipped.length - 5} more` : ""}`)
      }
      window.alert(lines.join("\n"))
      setSelectedEpicIds(new Set())
      await load()
    } catch (e: any) {
      window.alert(`Bulk delete failed: ${e?.message || e}`)
    } finally {
      setBulkDeleting(false)
    }
  }

  const selectedTask = popupTaskId
    ? tasks.find((t) => t.task_id === popupTaskId) ?? null
    : null
  // tasksById is declared above the early-return so the hook count
  // is stable across renders. The depends_on chip resolution below
  // reads it directly. Dangling refs (deleted blockers) get a
  // synthetic "(missing)" entry so the user sees the broken edge.
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
        flexWrap: "wrap",
      }}>
        <Layers size={14} color="var(--accent)" />
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>
          Build Plan · Epic → Feature → Task
        </h3>
        {selectedEpicIds.size > 0 && (
          <span style={{
            padding: "2px 8px", borderRadius: 2,
            background: "color-mix(in srgb, var(--accent) 14%, transparent)",
            color: "var(--accent)", border: "1px solid var(--accent)",
            fontSize: 10, fontFamily: "var(--font-mono)",
            textTransform: "uppercase", letterSpacing: 0.5,
          }}>
            {selectedEpicIds.size} selected
          </span>
        )}
        <span style={{
          marginLeft: "auto", fontSize: 11, color: "var(--text-muted)",
          fontFamily: "var(--font-mono)",
        }}>
          {epics.length} epics · {features.length} features · {tasks.filter((t) => t.feature_id).length} BPD tasks
        </span>
        {epics.length > 0 && (
          <div style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
            {selectedEpicIds.size < epics.length ? (
              <button
                type="button"
                onClick={selectAllEpics}
                style={selectActionBtn}
                title="Select all epics for bulk delete"
              >
                Select all ({epics.length})
              </button>
            ) : (
              <button
                type="button"
                onClick={clearEpicSelection}
                style={selectActionBtn}
                title="Clear selection"
              >
                Clear
              </button>
            )}
            <button
              type="button"
              onClick={bulkDeleteEpics}
              disabled={selectedEpicIds.size === 0 || bulkDeleting}
              style={{
                ...selectActionBtn,
                // Switch from inline-block to inline-flex so the trash
                // icon and the text share a baseline instead of the icon
                // floating ~1px above the cap-line. `verticalAlign: middle`
                // alone didn't fix it because the parent style is
                // inline-block + lineHeight 1.4 — flex with center alignment
                // is the only reliable way to share a baseline across an
                // SVG and text in the same chip.
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
                color: selectedEpicIds.size === 0 || bulkDeleting
                  ? "var(--text-muted)" : "var(--danger)",
                borderColor: selectedEpicIds.size === 0 || bulkDeleting
                  ? "var(--border)" : "var(--danger)",
                cursor: selectedEpicIds.size === 0 || bulkDeleting
                  ? "not-allowed" : "pointer",
              }}
              title="Hard-delete the selected epics + cascade features/tasks"
            >
              <Trash2 size={11} />
              <span>{bulkDeleting ? "Deleting…" : `Delete Selected (${selectedEpicIds.size})`}</span>
            </button>
          </div>
        )}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {epics.map((e) => {
          const exp = expandedEpics.has(e.epic_id)
          const r = epicRollup(e.epic_id)
          const isComplete = r.featureCount > 0 && r.featuresDone === r.featureCount
          const isBusy = busyId === e.epic_id
          return (
            <div
              key={e.epic_id}
              className={isBusy ? "bpv-row-busy" : undefined}
              style={{
                position: "relative",
                background: "var(--bg-hover)",
                border: "1px solid var(--border)",
                borderLeft: `3px solid ${isComplete ? "var(--success)" : "var(--info, #b026ff)"}`,
                borderRadius: 3,
                overflow: "hidden",
              }}
            >
              {/* Busy overlay — pointer-events:none so it doesn't
                  block the row's click handler. Layered animations:
                  (1) scanning shimmer line moving L→R across the row
                  (2) soft accent tint pulsing in/out of the bg
                  Border pulse + button glow are handled by sibling
                  styles. The BUSY · <label> chip lives in the header
                  row below so it shows even when the row is collapsed. */}
              {isBusy && (
                <>
                  <div className="bpv-scan-line" aria-hidden="true" />
                  <div className="bpv-busy-tint" aria-hidden="true" />
                </>
              )}
              <div
                onClick={() => toggleEpic(e.epic_id)}
                style={{
                  position: "relative", zIndex: 1,
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "8px 10px", cursor: "pointer",
                }}
              >
                {/* Selection checkbox. stopPropagation so clicking it
                    doesn't also expand/collapse the epic. */}
                <input
                  type="checkbox"
                  checked={selectedEpicIds.has(e.epic_id)}
                  onChange={() => toggleEpicSelected(e.epic_id)}
                  onClick={(ev) => ev.stopPropagation()}
                  aria-label={`Select epic ${e.title}`}
                  style={{
                    cursor: "pointer", margin: 0,
                    accentColor: "var(--accent)",
                    width: 14, height: 14,
                  }}
                />
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
                {/* FINALIZED badge — appears next to the title when the
                    epic's list_status is finalized. Visually distinct
                    from the green "complete" border (which means tasks
                    are deployed) so the user can tell "plan locked"
                    from "work shipped" at a glance. */}
                {String(e.list_status).toLowerCase() === "finalized" && !isBusy && (
                  <span style={{
                    display: "inline-flex", alignItems: "center", gap: 3,
                    padding: "1px 6px", borderRadius: 2,
                    fontSize: 9, fontFamily: "var(--font-mono)",
                    background: "color-mix(in srgb, var(--success) 14%, transparent)",
                    color: "var(--success)",
                    border: "1px solid var(--success)",
                    textTransform: "uppercase", letterSpacing: 0.5,
                  }}
                  title="Epic structure finalized — regenerate requires archive">
                    <Lock size={9} />
                    Finalized
                  </span>
                )}
                {/* BUSY chip — supplants the Finalized badge while
                    an action is in flight so the user always sees the
                    most urgent state. Inner spinner + breathing pulse
                    + animated dot trio drive home that work is live. */}
                {isBusy && (
                  <span className="bpv-busy-chip" title={busyLabel || "Working…"}>
                    <Loader2 size={10} className="bpv-spin" />
                    <span>BUSY</span>
                    <span className="bpv-busy-sep">·</span>
                    <span style={{ fontWeight: 600 }}>{busyLabel || "Working"}</span>
                    <span className="bpv-busy-dots">
                      <span className="bpv-dot bpv-dot-1" />
                      <span className="bpv-dot bpv-dot-2" />
                      <span className="bpv-dot bpv-dot-3" />
                    </span>
                  </span>
                )}
                <span style={{ marginLeft: "auto", display: "inline-flex", gap: 6, fontSize: 10, fontFamily: "var(--font-mono)" }}>
                  <Seg label={`${r.featuresDone}/${r.featureCount} features`} tone={isComplete ? "good" : "muted"} />
                  <Seg label={`${r.tasksDone}/${r.taskCount} tasks`} tone="muted" />
                  {r.blocked > 0 && <Seg label={`${r.blocked} blocked`} tone="warn" />}
                </span>
                <ActionBtn
                  onClick={(ev) => { ev.stopPropagation(); void generateFeatures(e.epic_id, e.title) }}
                  busy={busyId === e.epic_id}
                  disabled={readinessLocked}
                  label="+ Features"
                  title={readinessLocked ? gateMessage : "Generate features under this epic"}
                />
                {/* Batch Pass 3 scoped to this epic — generates tasks
                    for every feature under it. Disabled until features
                    exist (would 404 the endpoint anyway) and while the
                    epic is otherwise busy. Tooltip carries the wall-
                    time estimate so the user can plan around it. */}
                <ActionBtn
                  onClick={(ev) => { ev.stopPropagation(); void generateTasksForEpic(e.epic_id, e.title) }}
                  busy={busyId === e.epic_id}
                  disabled={readinessLocked || r.featureCount === 0}
                  label="+ Tasks"
                  title={
                    readinessLocked
                      ? gateMessage
                      : r.featureCount === 0
                        ? "Generate features for this epic first"
                        : `Generate atomic tasks for all ${r.featureCount} feature${r.featureCount === 1 ? "" : "s"} under this epic (~${r.featureCount * 30}s)`
                  }
                />
                {/* Finalize / Unfinalize toggle: same button slot, swaps
                    label + handler based on the row's list_status.
                    Finalize cascades down (epic + features + tasks);
                    Unfinalize is the exact inverse. Backend is
                    idempotent in both directions so re-clicks are safe. */}
                {String(e.list_status).toLowerCase() === "finalized" ? (
                  <ActionBtn
                    onClick={(ev) => { ev.stopPropagation(); void unfinalizeEpic(e.epic_id, e.title) }}
                    busy={busyId === e.epic_id}
                    label="🔓 Unfinalize"
                    title={`Flip this epic + its ${r.featureCount} features + ${r.taskCount} tasks back to draft`}
                  />
                ) : (
                  <ActionBtn
                    onClick={(ev) => { ev.stopPropagation(); void finalizeEpic(e.epic_id, e.title) }}
                    busy={busyId === e.epic_id}
                    label="✓ Finalize"
                    title={`Lock in this epic + its ${r.featureCount} features + ${r.taskCount} tasks`}
                  />
                )}
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
                    const fBusy = busyId === f.feature_id
                    return (
                      <div
                        key={f.feature_id}
                        className={fBusy ? "bpv-row-busy" : undefined}
                        style={{
                          position: "relative",
                          background: "var(--bg-card)",
                          border: "1px solid var(--border)",
                          borderLeft: `2px solid ${fComplete ? "var(--success)" : "var(--accent)"}`,
                          borderRadius: 3, marginTop: 4, overflow: "hidden",
                        }}
                      >
                        {fBusy && (
                          <>
                            <div className="bpv-scan-line" aria-hidden="true" />
                            <div className="bpv-busy-tint" aria-hidden="true" />
                          </>
                        )}
                        <div
                          onClick={() => toggleFeature(f.feature_id)}
                          style={{
                            position: "relative", zIndex: 1,
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
                          {String(f.list_status).toLowerCase() === "finalized" && !fBusy && (
                            <span style={{
                              display: "inline-flex", alignItems: "center", gap: 3,
                              padding: "1px 5px", borderRadius: 2,
                              fontSize: 9, fontFamily: "var(--font-mono)",
                              background: "color-mix(in srgb, var(--success) 14%, transparent)",
                              color: "var(--success)",
                              border: "1px solid var(--success)",
                              textTransform: "uppercase", letterSpacing: 0.5,
                            }}
                            title="Feature structure finalized">
                              <Lock size={8} />
                              Finalized
                            </span>
                          )}
                          {fBusy && (
                            <span className="bpv-busy-chip" title={busyLabel || "Working…"}>
                              <Loader2 size={9} className="bpv-spin" />
                              <span>BUSY</span>
                              <span className="bpv-busy-sep">·</span>
                              <span style={{ fontWeight: 600 }}>{busyLabel || "Working"}</span>
                              <span className="bpv-busy-dots">
                                <span className="bpv-dot bpv-dot-1" />
                                <span className="bpv-dot bpv-dot-2" />
                                <span className="bpv-dot bpv-dot-3" />
                              </span>
                            </span>
                          )}
                          <span style={{ marginLeft: "auto", fontSize: 10, fontFamily: "var(--font-mono)", color: fComplete ? "var(--success)" : "var(--text-muted)" }}>
                            {ftDone}/{ft.length}{fComplete ? " ✓" : ""}
                          </span>
                          <ActionBtn
                            onClick={(ev) => { ev.stopPropagation(); void generateTasks(f.feature_id, f.title) }}
                            busy={busyId === f.feature_id}
                            disabled={readinessLocked}
                            label="+ Tasks"
                            title={readinessLocked ? gateMessage : "Generate atomic tasks under this feature"}
                          />
                          {String(f.list_status).toLowerCase() === "finalized" ? (
                            <ActionBtn
                              onClick={(ev) => { ev.stopPropagation(); void unfinalizeFeature(f.feature_id, f.title) }}
                              busy={busyId === f.feature_id}
                              label="🔓 Unfinalize"
                              title={`Flip this feature + its ${ft.length} task${ft.length === 1 ? "" : "s"} back to draft`}
                            />
                          ) : (
                            <ActionBtn
                              onClick={(ev) => { ev.stopPropagation(); void finalizeFeature(f.feature_id, f.title) }}
                              busy={busyId === f.feature_id}
                              label="✓ Finalize"
                              title={`Lock in this feature + its ${ft.length} task${ft.length === 1 ? "" : "s"}`}
                            />
                          )}
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

        {/* BPD-38 — Legacy pseudo-epic. Renders any tasks that were
            created BEFORE the BPD decomposition shipped (feature_id IS
            NULL). They appear at the bottom of the tree with a muted
            border so it's visually clear they're not part of the new
            hierarchy. No per-row action buttons here — these tasks
            are still managed via the legacy flat TaskListEditor below. */}
        {hasLegacy && (
          <div style={{
            background: "var(--bg-hover)",
            border: "1px solid var(--border)",
            borderLeft: "3px solid var(--text-muted)",
            borderRadius: 3, overflow: "hidden", opacity: 0.85,
            marginTop: 4,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px" }}>
              <span style={{ width: 12 }} />
              <span style={{
                padding: "1px 6px", borderRadius: 2,
                fontSize: 10, fontFamily: "var(--font-mono)",
                background: "var(--bg-card)", color: "var(--text-muted)",
                border: "1px solid var(--border)",
                textTransform: "uppercase", letterSpacing: 0.5,
              }}>
                Legacy
              </span>
              <span style={{ fontWeight: 600, fontSize: 13, color: "var(--text-secondary)" }}>
                Tasks predating the Build Plan Decomposition
              </span>
              <span style={{ marginLeft: "auto", fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                {legacyTasks.length} task{legacyTasks.length === 1 ? "" : "s"} · feature_id=NULL
              </span>
            </div>
            <div style={{
              padding: "0 10px 8px 28px", display: "flex", flexDirection: "column", gap: 4,
            }}>
              {legacyTasks.slice(0, 20).map((t) => (
                <TaskRow key={t.task_id} task={t} onClick={() => setPopupTaskId(t.task_id)} />
              ))}
              {legacyTasks.length > 20 && (
                <div style={{ fontSize: 10, color: "var(--text-muted)", padding: "4px 0" }}>
                  …and {legacyTasks.length - 20} more. Use the flat
                  Task List editor below for full editing.
                </div>
              )}
            </div>
          </div>
        )}
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

      {/* ─── Busy-state animations ────────────────────────────────────
          Stacked CSS effects per the project's UI dynamics convention:
          a busy row gets simultaneously
            (1) border-color breath (left border pulses brightness)
            (2) outer accent halo glow (box-shadow pulse)
            (3) horizontal scan line sweeping L→R across the row
            (4) soft accent-color tint fading in/out of the bg
            (5) spinner icon + animated dot trio in the BUSY chip
            (6) inner accent glow on every busy chip button
          All scoped via the `bpv-` class prefix to avoid colliding with
          other component animations. `prefers-reduced-motion` collapses
          to a static-but-still-distinct state for accessibility. */}
      <style>{`
        @keyframes bpv-spin {
          from { transform: rotate(0deg); }
          to   { transform: rotate(360deg); }
        }
        .bpv-spin { animation: bpv-spin 0.9s linear infinite; }

        /* Row-level breathing border + halo */
        @keyframes bpv-row-breath {
          0%, 100% {
            border-left-color: var(--accent);
            box-shadow: 0 0 0 0 color-mix(in srgb, var(--accent) 30%, transparent);
          }
          50% {
            border-left-color: color-mix(in srgb, var(--accent) 60%, white 40%);
            box-shadow:
              0 0 14px 2px color-mix(in srgb, var(--accent) 45%, transparent),
              inset 0 0 24px color-mix(in srgb, var(--accent) 6%, transparent);
          }
        }
        .bpv-row-busy {
          animation: bpv-row-breath 1.6s ease-in-out infinite;
        }

        /* Scanning shimmer line — a thin angled accent gradient sweeps
           left to right across the row every 2.2s. */
        @keyframes bpv-scan {
          0%   { transform: translateX(-100%); opacity: 0; }
          15%  { opacity: 1; }
          85%  { opacity: 1; }
          100% { transform: translateX(100%);  opacity: 0; }
        }
        .bpv-scan-line {
          position: absolute; inset: 0;
          pointer-events: none; overflow: hidden;
          z-index: 0;
        }
        .bpv-scan-line::before {
          content: "";
          position: absolute; top: 0; bottom: 0; left: 0;
          width: 28%;
          background: linear-gradient(
            90deg,
            transparent 0%,
            color-mix(in srgb, var(--accent) 0%, transparent) 0%,
            color-mix(in srgb, var(--accent) 28%, transparent) 50%,
            transparent 100%
          );
          filter: blur(2px);
          animation: bpv-scan 2.2s linear infinite;
        }

        /* Subtle background tint that fades in and out under everything */
        @keyframes bpv-tint {
          0%, 100% { opacity: 0.18; }
          50%      { opacity: 0.42; }
        }
        .bpv-busy-tint {
          position: absolute; inset: 0; pointer-events: none;
          background: linear-gradient(
            135deg,
            color-mix(in srgb, var(--accent) 12%, transparent),
            color-mix(in srgb, var(--info, #b026ff) 8%, transparent)
          );
          animation: bpv-tint 2.0s ease-in-out infinite;
          z-index: 0;
        }

        /* BUSY chip — bordered accent capsule with internal glow pulse */
        @keyframes bpv-chip-glow {
          0%, 100% {
            box-shadow: 0 0 6px color-mix(in srgb, var(--accent) 30%, transparent);
            border-color: var(--accent);
          }
          50% {
            box-shadow: 0 0 14px color-mix(in srgb, var(--accent) 60%, transparent);
            border-color: color-mix(in srgb, var(--accent) 70%, white 30%);
          }
        }
        .bpv-busy-chip {
          display: inline-flex; align-items: center; gap: 5px;
          padding: 2px 8px; border-radius: 2px;
          font-size: 9px; font-family: var(--font-mono);
          text-transform: uppercase; letter-spacing: 0.6px;
          background: color-mix(in srgb, var(--accent) 14%, transparent);
          color: var(--accent);
          border: 1px solid var(--accent);
          animation: bpv-chip-glow 1.4s ease-in-out infinite;
          white-space: nowrap;
        }
        .bpv-busy-sep { opacity: 0.55; }

        /* Three-dot loader (the "…" that breathes) */
        @keyframes bpv-dot {
          0%, 100% { opacity: 0.25; transform: scale(0.85); }
          50%      { opacity: 1;    transform: scale(1.15); }
        }
        .bpv-busy-dots {
          display: inline-flex; gap: 2px; margin-left: 2px;
        }
        .bpv-dot {
          width: 3px; height: 3px; border-radius: 50%;
          background: var(--accent);
          display: inline-block;
          animation: bpv-dot 1.0s ease-in-out infinite;
        }
        .bpv-dot-1 { animation-delay: 0s; }
        .bpv-dot-2 { animation-delay: 0.15s; }
        .bpv-dot-3 { animation-delay: 0.3s; }

        /* Respect prefers-reduced-motion: keep the visual distinction
           (border color + chip) but stop the motion. */
        @media (prefers-reduced-motion: reduce) {
          .bpv-spin,
          .bpv-row-busy,
          .bpv-scan-line::before,
          .bpv-busy-tint,
          .bpv-busy-chip,
          .bpv-dot { animation: none; }
          .bpv-row-busy {
            border-left-color: var(--accent);
            box-shadow: 0 0 8px color-mix(in srgb, var(--accent) 35%, transparent);
          }
          .bpv-busy-chip {
            border-color: var(--accent);
            box-shadow: 0 0 6px color-mix(in srgb, var(--accent) 30%, transparent);
          }
          .bpv-dot { opacity: 0.7; }
        }
      `}</style>
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
  onClick, busy, label, title, primary, disabled,
}: {
  onClick: (e: React.MouseEvent) => void
  busy: boolean
  label: string
  title: string
  primary?: boolean
  /** When true, the button renders disabled with `title` as the
   *  hover-tooltip explanation. Used by BPD-51 to lock per-row
   *  "+ Features" / "+ Tasks" until PRD + API Spec are finalized. */
  disabled?: boolean
}) {
  const isInert = busy || disabled
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={isInert}
      title={title}
      style={{
        display: "inline-flex", alignItems: "center", gap: 4,
        padding: "2px 8px", fontSize: 10,
        background: busy
          // Busy state gets a faint accent wash so it's visually
          // distinct from "disabled because gated" (dashed border).
          ? "color-mix(in srgb, var(--accent) 18%, transparent)"
          : disabled
            ? "transparent"
            : primary
              ? "color-mix(in srgb, var(--accent) 14%, transparent)"
              : "transparent",
        color: busy
          ? "var(--accent)"
          : disabled
            ? "var(--text-muted)"
            : primary ? "var(--accent)" : "var(--text-secondary)",
        border: `1px ${disabled && !busy ? "dashed" : "solid"} ${
          busy ? "var(--accent)"
            : disabled ? "var(--border)"
              : (primary ? "var(--accent)" : "var(--border)")
        }`,
        borderRadius: 2, cursor: isInert ? (disabled ? "not-allowed" : "wait") : "pointer",
        fontFamily: "var(--font)", whiteSpace: "nowrap",
        opacity: disabled && !busy ? 0.5 : 1,
        // Busy buttons get a soft accent glow so the eye lands on the
        // active row from across the page.
        boxShadow: busy
          ? "0 0 8px color-mix(in srgb, var(--accent) 40%, transparent)"
          : undefined,
      }}
    >
      {busy ? <Loader2 size={10} className="bpv-spin" /> : null}
      <span>{label}</span>
    </button>
  )
}

// Style used by the header's "Select all / Clear / Delete Selected"
// chip-buttons. Bordered, transparent fill, mono-font for the count —
// matches the visual weight of the rollup pills so the action bar
// reads as part of the header strip, not a competing UI.
// inline-flex + center alignment + matching height keep these chips
// on the same baseline as the adjacent rollup `<span>` (the "9 epics
// · 45 features · 0 BPD tasks" text) instead of riding slightly higher.
const selectActionBtn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  height: 22,
  padding: "0 9px",
  fontSize: 10,
  background: "transparent",
  color: "var(--text-secondary)",
  border: "1px solid var(--border)",
  borderRadius: 2,
  cursor: "pointer",
  fontFamily: "var(--font-mono)",
  whiteSpace: "nowrap",
  lineHeight: 1,
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
