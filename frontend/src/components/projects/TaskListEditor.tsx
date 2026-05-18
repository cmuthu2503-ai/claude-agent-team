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

import { useCallback, useEffect, useState } from "react"
import { Link } from "react-router-dom"
import { ListChecks, Plus, RefreshCw, CheckCircle2, AlertTriangle, Rocket, ExternalLink } from "lucide-react"
import { api } from "../../lib/api"

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
}

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
      const res = await api.post(`/projects/${projectId}/tasks/generate`, {})
      const mode = res?.meta?.parse_mode
      if (mode === "markdown" || mode === "json_malformed_used_markdown") {
        setParseWarning(
          "Agent output had to be reparsed from markdown — review tasks carefully. (Regenerate may fix it.)"
        )
      }
      await load()
    } catch (e: any) {
      setError(parseDetail(e?.message) || "Task generation failed")
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
    if (!window.confirm(`Finalize ${tasks.length} task${tasks.length === 1 ? "" : "s"}? You can still amend via chat in Phase D, but downstream dispatch will use this version.`)) return
    setBusy("finalizing")
    setError("")
    try {
      await api.post(`/projects/${projectId}/tasks/finalize`, {})
      onFinalized()
    } catch (e: any) {
      setError(parseDetail(e?.message) || "Finalize failed")
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
          {!isFinalized && (
            <button type="button" onClick={generate} disabled={busy === "generating"} style={secondaryBtn(busy === "generating")}>
              <RefreshCw size={12} />
              <span>{busy === "generating" ? "Regenerating…" : "Regenerate"}</span>
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
              <Link
                to={`/stories/project/${projectId}`}
                style={{ ...secondaryBtn(false), textDecoration: "none" }}
              >
                <ExternalLink size={12} />
                <span>View Board →</span>
              </Link>
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
                <span>{busy === "generating" ? "Archiving & regenerating…" : "Archive & Regenerate"}</span>
              </button>
            </>
          )}
        </div>
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

      <div style={{
        overflow: "auto", border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
      }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ background: "var(--bg-hover)", borderBottom: "1px solid var(--border)" }}>
              <Th width={50}>#</Th>
              <Th>Title</Th>
              <Th width={140}>Type</Th>
              <Th width={100}>Priority</Th>
              <Th width={170}>Agent</Th>
              <Th width={100}>Status</Th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((t) => (
              <tr key={t.task_id} style={{ borderBottom: "1px solid var(--border)" }}>
                <Td width={50}>
                  <span style={{ color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                    {t.ordinal}
                  </span>
                </Td>
                <Td>
                  {isFinalized ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                      <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{t.title}</span>
                      <span style={{ color: "var(--text-muted)", fontSize: 11 }}>{t.description}</span>
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
                      already-dispatched rows show a link to /stories/:requestId. */}
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
                    <Link
                      to={`/stories/${t.request_id}`}
                      style={{
                        display: "inline-flex", alignItems: "center", gap: 4,
                        fontSize: 10, fontFamily: "var(--font-mono)",
                        color: "var(--accent)", textDecoration: "none",
                        padding: "2px 6px", borderRadius: 3,
                        background: "var(--bg-card)", border: "1px solid var(--border)",
                      }}
                      title="Open per-request Story Board"
                    >
                      {t.task_status} · {t.request_id}
                    </Link>
                  )}
                  {(!isFinalized || (!t.request_id && t.task_status !== "backlog")) && (
                    <Badge>{t.task_status}</Badge>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {isFinalized && (
        <div style={{
          marginTop: 10, padding: "8px 12px",
          background: "var(--bg-hover)", border: "1px dashed var(--border)",
          borderRadius: "var(--radius)", fontSize: 12, color: "var(--text-muted)",
        }}>
          Phase D (chat) lands next. For now, dispatch tasks individually or all at once — each becomes a Request that runs the per-task workflow. Open the Build Board for a live Kanban view.
        </div>
      )}

      {error && <ErrorBanner>{error}</ErrorBanner>}
    </Stub>
  )
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
