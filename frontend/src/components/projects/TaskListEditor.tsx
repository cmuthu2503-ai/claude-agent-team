/**
 * TaskListEditor — Project-driven Build, Phase B (PDB-19 → PDB-22).
 *
 * Renders the project's current task list (draft or finalized) as an
 * editable table. Reads /projects/:id/tasks, supports inline edits via
 * PATCH per-task, plus Finalize / Regenerate / Archive actions.
 *
 * The component owns its own loading state and refetches after every
 * mutation. The parent BuildWorkspace only mounts this once a PRD is
 * finalized (since the backend rejects generate before that).
 */

import { useCallback, useEffect, useMemo, useState } from "react"
import {
  ListChecks, Plus, RefreshCw, CheckCircle2, AlertTriangle, Rocket, Trash2,
  ChevronRight, ChevronDown,
} from "lucide-react"
import { api } from "../../lib/api"
import { PopupWindow } from "../board/PopupWindow"
import { validateDepends, type DepIssue as _DepIssue } from "./_depValidation"

interface Task {
  task_id: string
  project_id: string
  list_version: number
  list_status: "draft" | "finalized" | "archived"
  ordinal: number
  title: string
  description: string
  task_type: string
  priority: string
  estimated_agent: string | null
  task_status: string
  request_id: string | null
  // BPD-36 — depends_on drives the inline validation chip. The API has
  // emitted this field since BPD-003; the editor just wasn't reading it.
  depends_on?: string[]
}

// BPD-36 — DepIssue type re-exported from the pure validator module
// (_depValidation.ts). Single source of truth.
type DepIssue = _DepIssue

interface Props {
  projectId: string
  /** Called after Finalize succeeds so the parent can swap its sub-view. */
  onFinalized: () => void
}

const TASK_TYPES = [
  "feature_request", "bug_report", "doc_request",
  "demo_request", "research_request", "content_request",
] as const
const PRIORITIES = ["low", "medium", "high"] as const
const AGENTS = [
  "backend_specialist", "frontend_specialist", "tester_specialist",
  "code_reviewer", "devops_specialist", "content_creator",
  "research_specialist",
] as const

export function TaskListEditor({ projectId, onFinalized }: Props) {
  const [tasks, setTasks] = useState<Task[] | null>(null)
  const [busy, setBusy] = useState<"loading" | "generating" | "finalizing" | "archiving" | "dispatching" | null>("loading")
  const [error, setError] = useState("")
  const [parseWarning, setParseWarning] = useState<string | null>(null)
  const [dispatchingTaskId, setDispatchingTaskId] = useState<string | null>(null)
  const [deletingTaskId, setDeletingTaskId] = useState<string | null>(null)
  // Review comments for the next regeneration. Transient — cleared on
  // successful regen. Mirrors the PRDEditor pattern.
  const [reviewComments, setReviewComments] = useState("")
  // Multi-select state for bulk-delete. A Set lets us toggle efficiently
  // and re-key off task_id rather than ordinal (which can shift).
  // Pruned automatically when the underlying tasks list refreshes —
  // see the useEffect below.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  // Collapse the table body (mirrors the PRD / API Spec "Show/Hide"
  // pattern). Header bar + toolbar stay visible so Dispatch All etc.
  // are still reachable. Defaults to expanded.
  const [collapsed, setCollapsed] = useState(false)
  // Selected task for the click-to-expand popup. Renders a draggable
  // PopupWindow with the full title + description + metadata when set.
  // Rows show only the first line of title/description in the table
  // (Atlas-style task descriptions are multi-paragraph markdown blocks
  // that would otherwise blow the row height).
  const [popupTaskId, setPopupTaskId] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await api.get(`/projects/${projectId}/tasks`)
      setTasks(res.data || [])
      setBusy(null)
    } catch (e: any) {
      // 404 isn't expected here — tasks endpoint returns empty array when
      // no rows. Surface other errors so the user can retry.
      setError(parseDetail(e?.message) || "Failed to load tasks")
      setBusy(null)
    }
  }, [projectId])

  useEffect(() => {
    void load()
  }, [load])

  const generate = async () => {
    if (tasks && tasks.length > 0) {
      const finalized = tasks.some((t) => t.list_status === "finalized")
      if (finalized) {
        if (!window.confirm("A finalized task list exists. Archive it and generate a new one?")) return
        try {
          await api.post(`/projects/${projectId}/tasks/archive`, {})
        } catch (e: any) {
          setError(parseDetail(e?.message) || "Archive failed")
          return
        }
      } else if (!window.confirm("Regenerate will discard the current draft tasks. Continue?")) {
        return
      }
    }
    setBusy("generating")
    setError("")
    setParseWarning(null)
    try {
      const body = reviewComments.trim()
        ? { review_comments: reviewComments.trim() }
        : {}
      const res = await api.post(`/projects/${projectId}/tasks/generate`, body)
      const mode = res?.meta?.parse_mode
      if (mode === "markdown" || mode === "json_malformed_used_markdown") {
        setParseWarning(
          "Agent output had to be reparsed from markdown — review tasks carefully. (Regenerate may fix it.)"
        )
      }
      setReviewComments("")  // clear on success
      await load()           // also sets busy=null inside; finally below is belt-and-suspenders
    } catch (e: any) {
      setError(parseDetail(e?.message) || "Task generation failed")
    } finally {
      // Belt-and-suspenders — if `load()` itself throws AFTER api.post
      // succeeded, busy would otherwise leak. `load()` already sets busy=null
      // in both its branches; this is the safety net for the rest of the
      // function body.
      setBusy(null)
    }
  }

  const patch = async (task_id: string, field: keyof Task, value: any) => {
    // Optimistic update so the cell feels snappy. If the server rejects
    // we reload to bring the truth back.
    setTasks((prev) =>
      prev ? prev.map((t) => (t.task_id === task_id ? { ...t, [field]: value } : t)) : prev
    )
    try {
      await api.patch(`/projects/${projectId}/tasks/${task_id}`, { [field]: value })
    } catch (e: any) {
      setError(parseDetail(e?.message) || "Update failed")
      void load()
    }
  }

  const finalize = async () => {
    if (!tasks || tasks.length === 0) return
    if (!window.confirm(`Finalize ${tasks.length} task${tasks.length === 1 ? "" : "s"}? You can still amend via chat in Phase D, but downstream dispatch will use this version.\n\nThe list will also be written to C:/ai-projects/<ProjectName>/docs/tasks.md and pushed to the project's GitHub repo (if configured).`)) return
    setBusy("finalizing")
    setError("")
    try {
      const res = await api.post(`/projects/${projectId}/tasks/finalize`, {})
      // Refresh our OWN task state — the parent's onFinalized() only
      // refetches brief+PRD on the workspace, not the task rows. Without
      // this, the table stays on the draft rows and the toolbar never
      // flips to the finalized view (View Board / Dispatch All).
      await load()
      // Surface host-write + GitHub-push results (mirrors the PRD
      // finalize handler in BuildWorkspace.tsx). Both are soft-fail
      // server-side, so we always have a meta object on 200.
      const meta = (res as any)?.meta || {}
      const hw = meta.host_write
      const gh = meta.github_push
      const lines: string[] = [`Finalized ${meta.count ?? tasks.length} task${(meta.count ?? tasks.length) === 1 ? "" : "s"}.`]
      if (hw?.ok) lines.push(`Saved to: ${hw.path}`)
      else if (hw?.error) lines.push(`Local write failed: ${hw.error}`)
      if (gh?.ok) lines.push(`Pushed to ${gh.repo} @ ${gh.short_sha}`)
      else if (gh?.skipped === "no_repo_url") lines.push("(No GitHub repo configured for this project — skipped push)")
      else if (gh?.error) lines.push(`GitHub push failed: ${gh.error}`)
      window.alert(lines.join("\n"))
      onFinalized()
    } catch (e: any) {
      setError(parseDetail(e?.message) || "Finalize failed")
    } finally {
      // Always reset busy. The previous version only reset on error so
      // a successful finalize left the button stuck on "Finalizing…".
      setBusy(null)
    }
  }

  const dispatchOne = async (task_id: string) => {
    setDispatchingTaskId(task_id)
    setError("")
    try {
      await api.post(`/projects/${projectId}/build/dispatch`, { task_ids: [task_id] })
      await load()
    } catch (e: any) {
      setError(parseDetail(e?.message) || "Dispatch failed")
    } finally {
      setDispatchingTaskId(null)
    }
  }

  const deleteOne = async (task: Task) => {
    // Friendly warning for tasks that already produced a Request. The
    // server keeps the Request alive but the back-link to this task
    // disappears, which can be surprising.
    const hasRequest = !!task.request_id
    const warning = hasRequest
      ? `\n\nThis task is linked to ${task.request_id} (status: ${task.task_status}). The Request will remain in History but lose its back-link to this task.`
      : ""
    if (!window.confirm(`Permanently delete task ${task.task_id} — "${task.title}"?${warning}\n\nThis cannot be undone.`)) return
    setDeletingTaskId(task.task_id)
    setError("")
    try {
      await api.delete(`/projects/${projectId}/tasks/${task.task_id}`)
      await load()
    } catch (e: any) {
      setError(parseDetail(e?.message) || "Delete failed")
    } finally {
      setDeletingTaskId(null)
    }
  }

  // Server rejects deletes on in-flight statuses; mirror that rule here so
  // the row's checkbox can be disabled (and select-all skips them) instead
  // of letting the user check rows that will be quietly skipped.
  const _IN_FLIGHT = ["dispatched", "in_progress", "review", "testing"]
  const isDeletable = (t: Task) => !_IN_FLIGHT.includes(t.task_status)

  // Prune the selection set when the tasks list refreshes — drop any IDs
  // that no longer exist (e.g. just got deleted), or that became
  // un-deletable (status flipped to in_flight in the background). Keeps
  // the "Delete Selected (N)" counter honest.
  useEffect(() => {
    if (!tasks) return
    const valid = new Set(tasks.filter(isDeletable).map((t) => t.task_id))
    setSelectedIds((prev) => {
      const next = new Set<string>()
      for (const id of prev) if (valid.has(id)) next.add(id)
      return next.size === prev.size ? prev : next
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasks])

  // Computed: which rows are currently deletable, for the select-all logic.
  const deletableTasks = useMemo(
    () => (tasks ?? []).filter(isDeletable),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tasks],
  )

  // Computed: render order. Successfully-deployed tasks fall to the
  // bottom so the "what still needs work?" rows stay at the top of the
  // table. Stable sort (preserves ordinal order WITHIN each bucket) so
  // the original list ordering is intact for everything-not-deployed
  // AND for everything-deployed. The selection set is keyed on task_id,
  // so reordering rows can't desync the bulk-delete / dispatch-selected
  // counters. Only applied to finalized lists — draft lists are for
  // editing, where ordinal order is the contract.
  const displayTasks = useMemo(() => {
    if (!tasks) return tasks
    const isFinal = tasks.every((t) => t.list_status === "finalized")
    if (!isFinal) return tasks
    return [...tasks].sort((a, b) => {
      const aDeployed = a.task_status === "deployed" ? 1 : 0
      const bDeployed = b.task_status === "deployed" ? 1 : 0
      if (aDeployed !== bDeployed) return aDeployed - bDeployed
      return a.ordinal - b.ordinal
    })
  }, [tasks])
  // BPD-36 — dependency-validation issue map. Pure validator extracted
  // to `_depValidation.ts` so it's testable in isolation. Recomputed on
  // every tasks-list change; output drives the inline DepIssueChip
  // beside each task_id cell.
  const depIssuesByTaskId = useMemo(
    () => validateDepends(tasks || []),
    [tasks],
  )

  const allSelected =
    deletableTasks.length > 0 && deletableTasks.every((t) => selectedIds.has(t.task_id))
  const someSelected = selectedIds.size > 0 && !allSelected

  const toggleOne = (taskId: string, checked: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (checked) next.add(taskId)
      else next.delete(taskId)
      return next
    })
  }

  const toggleSelectAll = () => {
    if (allSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(deletableTasks.map((t) => t.task_id)))
    }
  }

  const bulkDelete = async () => {
    const ids = Array.from(selectedIds)
    if (ids.length === 0) return
    if (!window.confirm(
      `Permanently delete ${ids.length} selected task${ids.length === 1 ? "" : "s"}?\n\n` +
      "Linked Requests stay in History; they just lose their back-link to these tasks. " +
      "This cannot be undone."
    )) return
    setBusy("dispatching")  // reuse the disabling state; nothing here cares about the literal string
    setError("")
    try {
      const res = await api.post(`/projects/${projectId}/tasks/bulk_delete`, { task_ids: ids })
      const meta = res?.meta || {}
      // Surface partial-success info so the user knows if any were skipped.
      if (meta.skipped > 0) {
        const reasons = (res?.data?.skipped || [])
          .map((s: any) => `${s.task_id} (${s.reason}${s.task_status ? `: ${s.task_status}` : ""})`)
          .join(", ")
        setError(
          `Deleted ${meta.deleted}/${meta.requested}. Skipped: ${reasons}`,
        )
      }
      setSelectedIds(new Set())
      await load()
    } catch (e: any) {
      setError(parseDetail(e?.message) || "Bulk delete failed")
    } finally {
      setBusy(null)
    }
  }

  const dispatchAllBacklog = async () => {
    if (!tasks) return
    const backlogIds = tasks
      .filter((t) => t.task_status === "backlog" && t.list_status === "finalized")
      .map((t) => t.task_id)
    if (backlogIds.length === 0) return
    if (!window.confirm(`Dispatch all ${backlogIds.length} backlog task${backlogIds.length === 1 ? "" : "s"}? Each becomes a Request — the workflows will run in parallel.`)) return
    setBusy("dispatching")
    setError("")
    try {
      await api.post(`/projects/${projectId}/build/dispatch`, { task_ids: backlogIds })
      await load()
    } catch (e: any) {
      setError(parseDetail(e?.message) || "Dispatch failed")
    } finally {
      setBusy(null)
    }
  }

  /** Dispatch just the rows the user has check-selected.
   *
   * Eligibility (matches the backend's BLD-004 logic):
   *   - backlog            → dispatch as a new Request
   *   - failed / cancelled → re-dispatch (gets a fresh request_id;
   *                          old request stays in History)
   *   - dispatched / in_progress / review / testing → server returns
   *                          "already_dispatched" no-op; we list these
   *                          as "running" in the confirm message
   *   - deployed           → server returns "already_dispatched" no-op
   *
   * Any rows in non-finalized lists are 400'd by the server up front
   * (shouldn't be reachable from the UI since the table is only
   * mounted on a finalized list, but we still surface the error). */
  const dispatchSelected = async () => {
    if (!tasks) return
    const selectedTasks = tasks.filter((t) => selectedIds.has(t.task_id))
    const dispatchable = selectedTasks.filter(
      (t) =>
        t.list_status === "finalized" &&
        (t.task_status === "backlog" ||
          t.task_status === "failed" ||
          t.task_status === "cancelled"),
    )
    const running = selectedTasks.filter((t) =>
      ["dispatched", "in_progress", "review", "testing"].includes(t.task_status),
    )
    const done = selectedTasks.filter((t) => t.task_status === "deployed")
    const fresh = dispatchable.filter((t) => t.task_status === "backlog").length
    const retry = dispatchable.length - fresh
    if (dispatchable.length === 0) {
      setError(
        `None of the ${selectedTasks.length} selected row(s) can be dispatched. ` +
        (running.length > 0
          ? `${running.length} already running, `
          : "") +
        (done.length > 0 ? `${done.length} already deployed. ` : "") +
        "Select backlog / failed / cancelled rows to dispatch.",
      )
      return
    }
    const parts: string[] = []
    if (fresh > 0) parts.push(`${fresh} fresh dispatch${fresh === 1 ? "" : "es"}`)
    if (retry > 0) parts.push(`${retry} retry${retry === 1 ? "" : " retries"} (failed/cancelled)`)
    if (running.length > 0) parts.push(`${running.length} skipped (running)`)
    if (done.length > 0) parts.push(`${done.length} skipped (deployed)`)
    if (!window.confirm(
      `Dispatch ${dispatchable.length} of ${selectedTasks.length} selected task` +
      `${selectedTasks.length === 1 ? "" : "s"}?\n\n` +
      "  • " + parts.join("\n  • ") + "\n\n" +
      "Each new dispatch becomes a Request — workflows run in parallel.",
    )) return
    setBusy("dispatching")
    setError("")
    try {
      await api.post(`/projects/${projectId}/build/dispatch`, {
        task_ids: dispatchable.map((t) => t.task_id),
      })
      setSelectedIds(new Set())
      await load()
    } catch (e: any) {
      setError(parseDetail(e?.message) || "Dispatch failed")
    } finally {
      setBusy(null)
    }
  }

  // ── Empty / loading / error states ──────────────────────────────────
  if (busy === "loading") {
    return <Stub><span style={{ color: "var(--text-muted)", fontSize: 13 }}>Loading tasks…</span></Stub>
  }

  if (!tasks || tasks.length === 0) {
    return (
      <Stub>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
            <ListChecks size={16} />
            Task List
          </div>
          <p style={{ margin: 0, fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.5 }}>
            No tasks yet. Click below to ask <code>user_story_author</code> to break the finalized PRD into a flat list of buildable tasks. You'll be able to edit each row before finalizing.
          </p>
          <div>
            <button type="button" onClick={generate} disabled={busy === "generating"} style={primaryBtn(busy === "generating")}>
              <Plus size={13} />
              <span>{busy === "generating" ? "Generating tasks… (up to 90s)" : "Generate Task List"}</span>
            </button>
          </div>
          {error && <ErrorBanner>{error}</ErrorBanner>}
        </div>
      </Stub>
    )
  }

  const isFinalized = tasks.every((t) => t.list_status === "finalized")
  const backlogCount = tasks.filter((t) => t.task_status === "backlog").length

  // ── Table ────────────────────────────────────────────────────────────
  return (
    <Stub>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
        <div style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
          {isFinalized ? <CheckCircle2 size={16} color="var(--success)" /> : <ListChecks size={16} />}
          Task List {isFinalized ? "(Finalized)" : "(Draft)"} · v{tasks[0]?.list_version} · {tasks.length} task{tasks.length === 1 ? "" : "s"}
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {selectedIds.size > 0 && isFinalized && (() => {
            // Always shown when something is selected on a finalized list.
            // Mirrors the "Delete Selected (N)" pattern — N is the total
            // selection count. The handler itself breaks the selection
            // into dispatch / retry / skip buckets and surfaces a clear
            // confirm so the user knows exactly what will fire.
            // Tooltip gives a per-status preview without opening confirm.
            const dispatchable = tasks!.filter(
              (t) =>
                selectedIds.has(t.task_id) &&
                (t.task_status === "backlog" ||
                  t.task_status === "failed" ||
                  t.task_status === "cancelled"),
            ).length
            const titleText = dispatchable === selectedIds.size
              ? `Dispatch ${selectedIds.size} selected task${selectedIds.size === 1 ? "" : "s"}`
              : dispatchable === 0
                ? `None of the ${selectedIds.size} selected row${selectedIds.size === 1 ? "" : "s"} can be dispatched (all are running or deployed). Click for details.`
                : `${dispatchable} of ${selectedIds.size} selected row${selectedIds.size === 1 ? "" : "s"} will dispatch; the rest are running/deployed and will be skipped.`
            return (
              <button
                type="button"
                onClick={dispatchSelected}
                disabled={busy === "dispatching"}
                style={primaryBtn(busy === "dispatching")}
                title={titleText}
              >
                <Rocket size={12} />
                <span>
                  {busy === "dispatching"
                    ? "Dispatching…"
                    : `Dispatch Selected (${selectedIds.size})`}
                </span>
              </button>
            )
          })()}
          {selectedIds.size > 0 && (
            <button
              type="button"
              onClick={bulkDelete}
              disabled={busy === "dispatching"}
              style={dangerBtn(busy === "dispatching")}
              title={`Delete ${selectedIds.size} selected task${selectedIds.size === 1 ? "" : "s"} (in-flight rows are skipped server-side)`}
            >
              <Trash2 size={12} />
              <span>Delete Selected ({selectedIds.size})</span>
            </button>
          )}
          {!isFinalized && (
            <button type="button" onClick={generate} disabled={busy === "generating"} style={secondaryBtn(busy === "generating")}>
              <RefreshCw size={12} />
              <span>
                {busy === "generating"
                  ? "Regenerating…"
                  : reviewComments.trim()
                    ? "Regenerate with comments"
                    : "Regenerate"}
              </span>
            </button>
          )}
          {!isFinalized && (
            <button type="button" onClick={finalize} disabled={busy === "finalizing"} style={primaryBtn(busy === "finalizing")}>
              <CheckCircle2 size={12} />
              <span>{busy === "finalizing" ? "Finalizing…" : "Finalize Tasks"}</span>
            </button>
          )}
          {isFinalized && (
            <>
              {/* View Board link moved to the project header (next to
                  "View on GitHub") — it's a project-level navigation
                  concern, not a task-list-editor toolbar action. */}
              {backlogCount > 0 && (
                <button
                  type="button"
                  onClick={dispatchAllBacklog}
                  disabled={busy === "dispatching"}
                  style={primaryBtn(busy === "dispatching")}
                >
                  <Rocket size={12} />
                  <span>
                    {busy === "dispatching"
                      ? "Dispatching…"
                      : `Dispatch All (${backlogCount})`}
                  </span>
                </button>
              )}
              <button type="button" onClick={generate} disabled={busy === "generating"} style={secondaryBtn(busy === "generating")}>
                <RefreshCw size={12} />
                <span>
                  {busy === "generating"
                    ? "Archiving & regenerating…"
                    : reviewComments.trim()
                      ? "Archive & Regenerate (with comments)"
                      : "Archive & Regenerate"}
                </span>
              </button>
              {/* Show/Hide Tasks — mirrors the PRD / API Spec collapse
                  pattern so all three sections behave the same way. The
                  table body collapses; the toolbar and selection
                  affordances stay visible so the user can keep
                  driving the list from the header. */}
              <button
                type="button"
                onClick={() => setCollapsed((v) => !v)}
                style={{
                  display: "inline-flex", alignItems: "center", gap: 4,
                  padding: "4px 10px", fontSize: 11,
                  background: "transparent", color: "var(--text-muted)",
                  border: "1px solid var(--border)", borderRadius: "var(--radius)",
                  cursor: "pointer", fontFamily: "var(--font)",
                }}
              >
                {collapsed ? <ChevronRight size={11} /> : <ChevronDown size={11} />}
                {collapsed ? "Show Tasks" : "Hide Tasks"}
              </button>
            </>
          )}
        </div>
      </div>

      {/* Review comments for the next regeneration. Mirrors the PRDEditor
          pattern — transient, cleared on successful regen. The label
          adapts to whether the list is finalized (archive-and-regen
          flow) or still a draft. */}
      <div style={{
        marginBottom: 10, padding: "10px 12px",
        background: "var(--bg-card)", border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
      }}>
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          marginBottom: 6, fontSize: 11, color: "var(--text-muted)",
        }}>
          <span>
            Review comments for next regeneration <em>(optional)</em>
          </span>
          <span style={{
            fontFamily: "var(--font-mono)",
            color: reviewComments.length > 2000 ? "var(--danger)" : "var(--text-muted)",
          }}>
            {reviewComments.length}/2000
          </span>
        </div>
        <textarea
          value={reviewComments}
          onChange={(e) => setReviewComments(e.target.value.slice(0, 2000))}
          placeholder={
            'e.g. "Drop T-3 and T-7 — out of scope. Add a task for cost ' +
            'dashboard wiring. Mark T-2 high priority."'
          }
          rows={2}
          disabled={busy === "generating"}
          style={{
            width: "100%", padding: "6px 10px", fontSize: 12,
            fontFamily: "var(--font)", lineHeight: 1.4,
            color: "var(--text-primary)", background: "var(--bg-hover)",
            border: "1px solid var(--border)", borderRadius: "var(--radius)",
            resize: "vertical", minHeight: 44, outline: "none",
            boxSizing: "border-box",
          }}
        />
      </div>

      {parseWarning && (
        <div style={{
          padding: "6px 10px", marginBottom: 8,
          borderRadius: "var(--radius)", fontSize: 11,
          background: "var(--warning-subtle, rgba(255, 200, 0, 0.1))",
          color: "var(--warning, #d4a017)",
          border: "1px solid var(--warning, #d4a017)",
          display: "inline-flex", alignItems: "center", gap: 6,
        }}>
          <AlertTriangle size={11} />
          {parseWarning}
        </div>
      )}

      {/* The table body is the part Show/Hide Tasks toggles. Everything
          above (toolbar, review-comments textarea, parse-warning
          banner) stays visible when collapsed so the user can keep
          driving the list. Collapse is only available on finalized
          lists — in draft, the whole purpose is to edit the rows. */}
      {(!isFinalized || !collapsed) && (
      <div style={{
        overflow: "auto", border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
      }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ background: "var(--bg-hover)", borderBottom: "1px solid var(--border)" }}>
              <Th width={32}>
                <input
                  type="checkbox"
                  checked={allSelected}
                  // Indeterminate state when some-but-not-all selected; React
                  // doesn't have a prop for this, so set it imperatively.
                  ref={(el) => { if (el) el.indeterminate = someSelected }}
                  onChange={toggleSelectAll}
                  disabled={deletableTasks.length === 0}
                  title={
                    allSelected
                      ? "Deselect all"
                      : `Select all ${deletableTasks.length} deletable task${deletableTasks.length === 1 ? "" : "s"}`
                  }
                  style={{ cursor: deletableTasks.length === 0 ? "not-allowed" : "pointer" }}
                />
              </Th>
              <Th width={36}>#</Th>
              <Th width={110}>Task ID</Th>
              <Th>Title</Th>
              <Th width={140}>Type</Th>
              <Th width={100}>Priority</Th>
              <Th width={170}>Agent</Th>
              <Th width={isFinalized ? 170 : 100}>Status</Th>
              <Th width={44}>{" "}</Th>
            </tr>
          </thead>
          <tbody>
            {(displayTasks ?? tasks).map((t) => (
              <tr
                key={t.task_id}
                style={{
                  borderBottom: "1px solid var(--border)",
                  // Subtle highlight when a row is in the selection set
                  // so the user can see what they'll delete.
                  background: selectedIds.has(t.task_id)
                    ? "var(--accent-subtle, color-mix(in srgb, var(--accent) 8%, transparent))"
                    : undefined,
                }}
              >
                <Td width={32}>
                  <input
                    type="checkbox"
                    checked={selectedIds.has(t.task_id)}
                    onChange={(e) => toggleOne(t.task_id, e.target.checked)}
                    disabled={!isDeletable(t)}
                    title={
                      isDeletable(t)
                        ? "Select for bulk delete"
                        : `In-flight (${t.task_status}) — cancel first to enable delete`
                    }
                    style={{ cursor: isDeletable(t) ? "pointer" : "not-allowed" }}
                  />
                </Td>
                <Td width={36}>
                  <span style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                    {t.ordinal}
                  </span>
                </Td>
                <Td width={110}>
                  {/* Click to copy — handy when chatting with the
                      orchestrator ("dispatch T-abc12345"). Visual
                      feedback via title; copy uses the Clipboard
                      API with a fallback for older browsers. */}
                  <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    <span
                      role="button"
                      tabIndex={0}
                      title={`Click to copy ${t.task_id}`}
                      onClick={() => {
                        try {
                          void navigator.clipboard?.writeText(t.task_id)
                        } catch {
                          /* clipboard unavailable — title text still informs */
                        }
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault()
                          try { void navigator.clipboard?.writeText(t.task_id) } catch { /* no-op */ }
                        }
                      }}
                      style={{
                        color: "var(--accent)",
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        cursor: "pointer",
                        padding: "1px 6px",
                        borderRadius: 3,
                        background: "var(--bg-hover)",
                        border: "1px solid var(--border)",
                        userSelect: "all",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {t.task_id}
                    </span>
                    {/* BPD-36 — dependency-validation chip. Sits inline
                        with the task_id so the operator's eye lands on
                        the error in the same cell as the row that owns
                        the bad depends_on. Color: red for errors, amber
                        for warnings (forward_ref). Tooltip lists every
                        issue + Fix hint so the editor pass clears all
                        problems at once. */}
                    <DepIssueChip issues={depIssuesByTaskId.get(t.task_id) || []} />
                  </div>
                </Td>
                <Td>
                  {isFinalized ? (
                    /* Click anywhere on the title cell to open the full
                       detail popup. Atlas-style descriptions can be
                       multi-paragraph markdown — we show only the first
                       line in the row so the table stays scannable, and
                       offload the rest to the popup. */
                    <div
                      role="button"
                      tabIndex={0}
                      onClick={() => setPopupTaskId(t.task_id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault()
                          setPopupTaskId(t.task_id)
                        }
                      }}
                      title="Click to see the full title + description"
                      style={{
                        display: "flex", flexDirection: "column", gap: 2,
                        cursor: "pointer",
                        padding: "2px 4px", margin: "-2px -4px",
                        borderRadius: 3,
                        transition: "background 0.12s",
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.background = "var(--bg-hover)"
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.background = "transparent"
                      }}
                    >
                      <span style={{
                        color: "var(--text-primary)", fontWeight: 500,
                        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                      }}>
                        {firstLine(t.title)}
                      </span>
                      {t.description && (
                        <span style={{
                          color: "var(--text-muted)", fontSize: 11,
                          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                        }}>
                          {firstLine(t.description)}
                        </span>
                      )}
                    </div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                      <CellInput
                        value={t.title}
                        onCommit={(v) => patch(t.task_id, "title", v)}
                        placeholder="title"
                      />
                      <CellInput
                        value={t.description}
                        onCommit={(v) => patch(t.task_id, "description", v)}
                        placeholder="description (optional)"
                        muted
                      />
                    </div>
                  )}
                </Td>
                <Td width={140}>
                  {isFinalized ? (
                    <Badge>{t.task_type.replace("_", " ")}</Badge>
                  ) : (
                    <CellSelect
                      value={t.task_type}
                      options={TASK_TYPES.map((x) => ({ value: x, label: x.replace("_", " ") }))}
                      onCommit={(v) => patch(t.task_id, "task_type", v)}
                    />
                  )}
                </Td>
                <Td width={100}>
                  {isFinalized ? (
                    <Badge priority={t.priority}>{t.priority}</Badge>
                  ) : (
                    <CellSelect
                      value={t.priority}
                      options={PRIORITIES.map((x) => ({ value: x, label: x }))}
                      onCommit={(v) => patch(t.task_id, "priority", v)}
                    />
                  )}
                </Td>
                <Td width={170}>
                  {isFinalized ? (
                    <Badge>{t.estimated_agent?.replace("_", " ") || "—"}</Badge>
                  ) : (
                    <CellSelect
                      value={t.estimated_agent || ""}
                      options={[{ value: "", label: "—" }, ...AGENTS.map((x) => ({ value: x, label: x.replace("_", " ") }))]}
                      onCommit={(v) => patch(t.task_id, "estimated_agent", v || null)}
                    />
                  )}
                </Td>
                <Td width={isFinalized ? 170 : 100}>
                  {/* Backlog rows in a finalized list get a Dispatch chip;
                      already-dispatched rows show a status · request_id
                      badge (no navigation — drill in via the Build Board
                      link at the top of this editor). */}
                  {isFinalized && t.task_status === "backlog" && (
                    <button
                      type="button"
                      onClick={() => dispatchOne(t.task_id)}
                      disabled={dispatchingTaskId === t.task_id}
                      style={dispatchBtn(dispatchingTaskId === t.task_id)}
                      title="Dispatch this task — creates a Request that runs the workflow"
                    >
                      <Rocket size={10} />
                      <span>{dispatchingTaskId === t.task_id ? "…" : "Dispatch"}</span>
                    </button>
                  )}
                  {isFinalized && t.task_status !== "backlog" && t.request_id && (
                    /* Plain badge — old Link to the legacy /stories/:request_id
                       page was removed. Drill in via the Build Board (link
                       at the top of this editor → `/stories/project/:id`). */
                    <span
                      style={{
                        display: "inline-flex", alignItems: "center", gap: 4,
                        fontSize: 10, fontFamily: "var(--font-mono)",
                        color: "var(--text-secondary)",
                        padding: "2px 6px", borderRadius: 3,
                        background: "var(--bg-card)", border: "1px solid var(--border)",
                      }}
                      title={`Open the Build Board to drill into ${t.request_id}`}
                    >
                      {t.task_status} · {t.request_id}
                    </span>
                  )}
                  {(!isFinalized || (!t.request_id && t.task_status !== "backlog")) && (
                    <Badge>{t.task_status}</Badge>
                  )}
                </Td>
                <Td width={44}>
                  {/* Trash icon. Disabled (with tooltip) when the task is in
                      flight — server returns 409 in that case anyway, but the
                      affordance is clearer if we surface it here. */}
                  {(() => {
                    const inFlight = ["dispatched", "in_progress", "review", "testing"].includes(t.task_status)
                    const busy = deletingTaskId === t.task_id
                    return (
                      <button
                        type="button"
                        onClick={() => deleteOne(t)}
                        disabled={inFlight || busy}
                        title={inFlight
                          ? `Task is ${t.task_status} — cancel it before deleting`
                          : "Delete this task"}
                        style={{
                          display: "inline-flex", alignItems: "center", justifyContent: "center",
                          width: 24, height: 24, padding: 0,
                          background: "transparent",
                          color: inFlight ? "var(--text-muted)" : "var(--text-muted)",
                          border: "1px solid var(--border)",
                          borderRadius: 3,
                          cursor: inFlight ? "not-allowed" : busy ? "wait" : "pointer",
                          opacity: inFlight ? 0.4 : busy ? 0.5 : 1,
                          transition: "background 0.15s, color 0.15s, border-color 0.15s",
                        }}
                        onMouseEnter={(e) => {
                          if (inFlight || busy) return
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
                      </button>
                    )
                  })()}
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      )}

      {isFinalized && (
        <div style={{
          marginTop: 10, padding: "8px 12px",
          background: "var(--bg-hover)", border: "1px dashed var(--border)",
          borderRadius: "var(--radius)", fontSize: 12, color: "var(--text-muted)",
        }}>
          Phase D (chat) lands next. For now, dispatch tasks individually or all at once — each becomes a Request that runs the per-task workflow. Open the Build Board for a live Kanban view.
        </div>
      )}

      {/* Click-to-expand popup. Triggered when the user clicks a
          finalized row's title cell. Renders the full title +
          description + all metadata in a draggable, non-modal popup
          so the table stays interactive behind it. */}
      {popupTaskId && tasks && (() => {
        const t = tasks.find((x) => x.task_id === popupTaskId)
        if (!t) return null
        return (
          <PopupWindow
            subtitle={t.task_id}
            title={t.title}
            onClose={() => setPopupTaskId(null)}
            width={640}
            maxHeight="80vh"
          >
            <TaskDetailBody task={t} />
          </PopupWindow>
        )
      })()}

      {error && <ErrorBanner>{error}</ErrorBanner>}
    </Stub>
  )
}

// ── Popup body ─────────────────────────────────────────────────────────

/** Full task detail rendered inside PopupWindow. Mirrors the metadata
 * row chips from the table but in a vertical grid + full title +
 * full description (whitespace preserved). Kept presentational —
 * no buttons here; the table row owns dispatch / delete. */
function TaskDetailBody({ task }: { task: Task }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Metadata grid */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "auto 1fr auto 1fr",
        gap: "6px 14px",
        fontSize: 11,
        padding: "10px 12px",
        background: "var(--bg-hover)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
      }}>
        <span style={{ color: "var(--text-muted)" }}>Task ID</span>
        <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>
          {task.task_id}
        </span>
        <span style={{ color: "var(--text-muted)" }}>Status</span>
        <span style={{ color: "var(--text-primary)" }}>{task.task_status}</span>

        <span style={{ color: "var(--text-muted)" }}>Type</span>
        <span style={{ color: "var(--text-primary)" }}>
          {task.task_type.replace(/_/g, " ")}
        </span>
        <span style={{ color: "var(--text-muted)" }}>Priority</span>
        <span style={{ color: "var(--text-primary)" }}>{task.priority}</span>

        <span style={{ color: "var(--text-muted)" }}>Agent</span>
        <span style={{ color: "var(--text-primary)" }}>
          {task.estimated_agent?.replace(/_/g, " ") || "—"}
        </span>
        <span style={{ color: "var(--text-muted)" }}>Request</span>
        <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>
          {task.request_id || "—"}
        </span>
      </div>

      {/* Full title */}
      <div>
        <div style={{
          fontSize: 10, textTransform: "uppercase", letterSpacing: 1,
          color: "var(--text-muted)", marginBottom: 4, fontWeight: 700,
        }}>
          Title
        </div>
        <div style={{
          fontSize: 13, color: "var(--text-primary)", lineHeight: 1.45,
          whiteSpace: "pre-wrap", wordBreak: "break-word",
        }}>
          {task.title}
        </div>
      </div>

      {/* Full description (whitespace preserved for markdown bullets) */}
      {task.description && (
        <div>
          <div style={{
            fontSize: 10, textTransform: "uppercase", letterSpacing: 1,
            color: "var(--text-muted)", marginBottom: 4, fontWeight: 700,
          }}>
            Description
          </div>
          <div style={{
            fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5,
            whiteSpace: "pre-wrap", wordBreak: "break-word",
          }}>
            {task.description}
          </div>
        </div>
      )}
    </div>
  )
}

/** Return the first non-empty line of a string, trimmed of trailing
 * whitespace. Empty string in → empty string out. Used to keep
 * multi-line task descriptions to one row in the table. */
function firstLine(s: string): string {
  if (!s) return ""
  // Split on the FIRST newline so we don't allocate the whole array.
  const nl = s.indexOf("\n")
  return (nl >= 0 ? s.slice(0, nl) : s).trimEnd()
}

// ── Cell primitives ────────────────────────────────────────────────────

function CellInput({
  value, onCommit, placeholder, muted,
}: { value: string; onCommit: (v: string) => void; placeholder?: string; muted?: boolean }) {
  const [local, setLocal] = useState(value)
  useEffect(() => { setLocal(value) }, [value])
  const commit = () => { if (local !== value) onCommit(local) }
  return (
    <input
      type="text"
      value={local}
      placeholder={placeholder}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur() }}
      style={{
        width: "100%", padding: "3px 6px", fontSize: muted ? 11 : 12,
        color: muted ? "var(--text-muted)" : "var(--text-primary)",
        background: "transparent", border: "1px solid transparent",
        borderRadius: 3, outline: "none", fontFamily: "var(--font)",
      }}
      onFocus={(e) => { e.currentTarget.style.borderColor = "var(--accent)" }}
      onBlurCapture={(e) => { e.currentTarget.style.borderColor = "transparent" }}
    />
  )
}

function CellSelect({
  value, options, onCommit,
}: { value: string; options: { value: string; label: string }[]; onCommit: (v: string) => void }) {
  return (
    <select
      value={value}
      onChange={(e) => onCommit(e.target.value)}
      style={{
        width: "100%", padding: "3px 6px", fontSize: 12,
        color: "var(--text-primary)", background: "transparent",
        border: "1px solid transparent", borderRadius: 3,
        outline: "none", fontFamily: "var(--font)",
      }}
      onFocus={(e) => { e.currentTarget.style.borderColor = "var(--accent)" }}
      onBlur={(e) => { e.currentTarget.style.borderColor = "transparent" }}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  )
}

function Badge({ children, priority }: { children: React.ReactNode; priority?: string }) {
  const color =
    priority === "high" ? "var(--danger)" :
    priority === "low" ? "var(--text-muted)" :
    "var(--accent)"
  return (
    <span style={{
      display: "inline-block", padding: "2px 8px", borderRadius: 3,
      fontSize: 10, fontFamily: "var(--font-mono)", textTransform: "uppercase",
      letterSpacing: 0.5, background: "var(--bg-hover)",
      color: priority ? color : "var(--text-secondary)",
      border: priority ? `1px solid ${color}` : "1px solid var(--border)",
    }}>
      {children}
    </span>
  )
}

function Th({ children, width }: { children: React.ReactNode; width?: number }) {
  return (
    <th style={{
      padding: "8px 10px", textAlign: "left",
      fontSize: 10, fontWeight: 700, textTransform: "uppercase",
      letterSpacing: 1, color: "var(--text-muted)",
      width: width ?? "auto",
    }}>{children}</th>
  )
}

function Td({ children, width }: { children: React.ReactNode; width?: number }) {
  return (
    <td style={{
      padding: "6px 10px", verticalAlign: "middle",
      width: width ?? "auto",
    }}>{children}</td>
  )
}

function Stub({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      marginTop: 12, padding: 14,
      background: "var(--bg-hover)", border: "1px solid var(--border)",
      borderRadius: "var(--radius)",
    }}>
      {children}
    </div>
  )
}

function ErrorBanner({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      marginTop: 10, padding: "8px 10px", borderRadius: "var(--radius)",
      fontSize: 12, color: "var(--danger)",
      background: "var(--danger-subtle)", border: "1px solid var(--danger)",
    }}>
      {children}
    </div>
  )
}

function primaryBtn(disabled: boolean): React.CSSProperties {
  return {
    display: "inline-flex", alignItems: "center", gap: 6,
    padding: "6px 12px", fontSize: 12, fontWeight: 700,
    background: disabled ? "var(--bg-card)" : "var(--accent)",
    color: disabled ? "var(--text-muted)" : "#0a0014",
    border: "1px solid " + (disabled ? "var(--border)" : "var(--accent)"),
    borderRadius: "var(--radius)",
    cursor: disabled ? "not-allowed" : "pointer",
    whiteSpace: "nowrap", lineHeight: 1, fontFamily: "var(--font)",
  }
}

function secondaryBtn(disabled: boolean): React.CSSProperties {
  return {
    display: "inline-flex", alignItems: "center", gap: 6,
    padding: "6px 12px", fontSize: 12, fontWeight: 600,
    background: "transparent",
    color: disabled ? "var(--text-muted)" : "var(--text-secondary)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius)",
    cursor: disabled ? "not-allowed" : "pointer",
    whiteSpace: "nowrap", lineHeight: 1, fontFamily: "var(--font)",
    opacity: disabled ? 0.6 : 1,
  }
}

function dispatchBtn(disabled: boolean): React.CSSProperties {
  return {
    display: "inline-flex", alignItems: "center", gap: 4,
    padding: "3px 8px", fontSize: 10, fontWeight: 700,
    background: disabled ? "var(--bg-card)" : "var(--accent-subtle, color-mix(in srgb, var(--accent) 15%, transparent))",
    color: disabled ? "var(--text-muted)" : "var(--accent)",
    border: `1px solid var(--accent)`,
    borderRadius: 3, cursor: disabled ? "wait" : "pointer",
    whiteSpace: "nowrap", lineHeight: 1, fontFamily: "var(--font-mono)",
    textTransform: "uppercase", letterSpacing: 0.5,
  }
}

function dangerBtn(disabled: boolean): React.CSSProperties {
  return {
    display: "inline-flex", alignItems: "center", gap: 6,
    padding: "6px 12px", fontSize: 12, fontWeight: 600,
    background: disabled
      ? "var(--bg-card)"
      : "var(--danger-subtle, color-mix(in srgb, var(--danger) 12%, transparent))",
    color: disabled ? "var(--text-muted)" : "var(--danger)",
    border: `1px solid var(--danger)`,
    borderRadius: "var(--radius)",
    cursor: disabled ? "wait" : "pointer",
    whiteSpace: "nowrap", lineHeight: 1, fontFamily: "var(--font)",
  }
}

// BPD-36 — inline chip that summarises this task's depends_on issues.
// Renders nothing when issues is empty so clean rows don't get visual
// weight. Multi-line `title` lists every issue + Fix hint so a single
// hover surfaces the whole problem.
function DepIssueChip({ issues }: { issues: DepIssue[] }) {
  if (issues.length === 0) return null
  const hasError = issues.some((i) => i.severity === "error")
  const tone = hasError ? "var(--danger)" : "var(--warning, #f59e0b)"
  const tip = issues
    .map((i) => {
      const label =
        i.kind === "self_reference" ? "Self-reference"
        : i.kind === "dangling"     ? "Dangling reference"
        : i.kind === "forward_ref"  ? "Forward reference"
        : "Cycle"
      const ref = i.bad_ref_title ? `${i.bad_ref} (${i.bad_ref_title})` : i.bad_ref
      return `${label} → ${ref}\n  Fix: ${i.fix_hint}`
    })
    .join("\n\n")
  const count = issues.length
  const label = count === 1
    ? (issues[0].kind === "cycle" ? "cycle"
        : issues[0].kind === "forward_ref" ? "fwd-ref"
        : issues[0].kind === "self_reference" ? "self-ref"
        : "dangling")
    : `${count} dep issues`
  return (
    <span
      data-testid="dep-issue-chip"
      data-severity={hasError ? "error" : "warn"}
      title={tip}
      style={{
        fontSize: 9, fontWeight: 700,
        padding: "1px 6px",
        borderRadius: 3,
        border: `1px solid ${tone}`,
        color: tone,
        background: "transparent",
        fontFamily: "var(--font-mono)",
        textTransform: "uppercase",
        letterSpacing: 0.5,
        cursor: "help",
      }}
    >
      ⚠ {label}
    </span>
  )
}

function parseDetail(msg: string | undefined): string | undefined {
  if (!msg) return undefined
  const colon = msg.indexOf(":")
  if (colon < 0) return msg
  try {
    const parsed = JSON.parse(msg.slice(colon + 1).trim())
    const d = parsed?.detail ?? parsed
    if (typeof d === "string") return d
    if (typeof d === "object" && d) return d.error || d.message || d.hint || JSON.stringify(d)
    return msg
  } catch {
    return msg
  }
}
