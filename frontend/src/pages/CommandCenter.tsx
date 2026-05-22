import { useState, useEffect, useRef } from "react"
import { api } from "../lib/api"
import { StatusBadge } from "../components/ui/StatusBadge"
import { RichTextInput, type RichTextInputHandle } from "../components/ui/RichTextInput"
import { useSearchParams } from "react-router-dom"
import { Plus, Send, X, Trash2 } from "lucide-react"
import { RefreshButton } from "../components/ui/RefreshButton"
import { useAuthStore } from "../stores/auth"
import { CreateProjectModal, type CreatedProject } from "../components/projects/CreateProjectModal"
import { ProjectChip } from "../components/projects/ProjectChip"
import { PopupWindow } from "../components/board/PopupWindow"
import { TaskDrillIn } from "../components/board/TaskDrillIn"
import type { CardData, TaskStatus } from "../components/board/types"

// Lightweight Project shape for the dropdown — we only need the fields
// that affect the New Request form (id, name, default_team).
interface ProjectOption {
  project_id: string
  name: string
  color: string
  icon: string
  default_team: string | null
}
const NEW_PROJECT_SENTINEL = "__new__"
const LAST_PROJECT_LS_KEY = "agent-team:last-project-id"

// Terminal states where a request is safe to hard-delete (nothing will be
// writing to its rows in the background).
const TERMINAL_STATUSES = ["completed", "failed", "cancelled"] as const
function isTerminal(status: string): boolean {
  return (TERMINAL_STATUSES as readonly string[]).includes(status)
}

interface RequestItem {
  request_id: string
  description: string
  task_type: string
  priority: string
  status: string
  created_at: string
  created_by?: string
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
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState("")
  const [attachCount, setAttachCount] = useState(0)
  const [similarDocs, setSimilarDocs] = useState<any[]>([])
  const [activity, setActivity] = useState<ActivityEvent[]>([])
  const editorRef = useRef<RichTextInputHandle>(null)
  const wsRef = useRef<WebSocket | null>(null)

  // Project assignment (PM-38) — required dropdown above the team selector.
  // Defaults to user's last-used project (localStorage), or whatever the
  // ?project_id= query param specifies (deep link from Project detail page).
  const [searchParams] = useSearchParams()
  const [projects, setProjects] = useState<ProjectOption[]>([])
  const [projectId, setProjectId] = useState<string>("")  // empty until projects load
  const [projectModalOpen, setProjectModalOpen] = useState(false)

  // Load the active projects list, then pick the default project:
  //  1. ?project_id= query param if set (deep link from ProjectDetail)
  //  2. localStorage last-used project if still active
  //  3. The first project (likely Unassigned)
  const loadProjects = async () => {
    try {
      const res = await api.get("/projects")
      const list: ProjectOption[] = (res.data || []).map((p: any) => ({
        project_id: p.project_id,
        name: p.name,
        color: p.color,
        icon: p.icon,
        default_team: p.default_team,
      }))
      setProjects(list)

      // Resolve default project
      const fromQuery = searchParams.get("project_id")
      const fromStorage = localStorage.getItem(LAST_PROJECT_LS_KEY)
      const isActive = (id: string | null) => !!id && list.some((p) => p.project_id === id)
      let pick: string = ""
      if (isActive(fromQuery)) pick = fromQuery!
      else if (isActive(fromStorage)) pick = fromStorage!
      else if (list.length > 0) pick = list[0].project_id
      setProjectId(pick)

      // Auto-fill team from project's default_team
      const proj = list.find((p) => p.project_id === pick)
      if (proj?.default_team) {
        setSelectedTeam(proj.default_team)
        const taskDefaults: Record<string, string> = {
          engineering: "feature_request",
          research: "research_request",
          content: "content_request",
        }
        setTaskType(taskDefaults[proj.default_team] || "feature_request")
      }
    } catch {}
  }
  // Re-fetch projects when the modal closes (in case the user just created one).
  // Also runs once on initial mount.
  useEffect(() => {
    if (!projectModalOpen) loadProjects()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectModalOpen])

  // Apply prefill from deep link (ProjectDetail "Next Steps" click). Runs once
  // after projects load — pre-fills the description/type/priority for the user
  // but doesn't submit. They confirm by clicking Dispatch.
  useEffect(() => {
    const prefill = searchParams.get("prefill")
    const tt = searchParams.get("task_type")
    const pr = searchParams.get("priority")
    if (prefill && editorRef.current) {
      editorRef.current.setContent?.(prefill)
    }
    if (tt) setTaskType(tt)
    if (pr) setPriority(pr)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
    if (!projectId) {
      setSubmitError("Pick a project before dispatching.")
      return
    }
    setSubmitting(true)
    setSubmitError("")
    try {
      const formData = new FormData()
      formData.append("description", content.text)
      formData.append("task_type", taskType)
      formData.append("priority", priority)
      formData.append("project_id", projectId)
      for (const file of content.files) {
        formData.append("screenshots", file)
      }
      await api.postForm("/requests", formData)
      // Remember this project for next time
      localStorage.setItem(LAST_PROJECT_LS_KEY, projectId)
      editorRef.current?.clear()
      setAttachCount(0)
      await loadRequests()
    } catch (err: any) {
      console.error("Submit failed:", err)
      setSubmitError(err?.message || "Submit failed")
    }
    setSubmitting(false)
  }

  // Track per-request in-flight cancel/delete so buttons disable while action runs
  const [busyId, setBusyId] = useState<string | null>(null)
  // Manual refresh affordance. The page already polls via WebSocket events,
  // but server-side state can change without an event (e.g. supervisor
  // flipped a deploy status, or an external script manually mutated a row)
  // — explicit Refresh gives the user a way to force a re-fetch without
  // a full page reload.
  const [refreshing, setRefreshing] = useState(false)
  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      await Promise.all([loadRequests(), loadProjects()])
    } finally {
      setRefreshing(false)
    }
  }
  // Selected request for the popup-window drill-in. Clicking a row in
  // either the "In Flight" or "Recently Completed" sections opens this.
  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(null)
  const currentUser = useAuthStore((s) => s.user)

  /** Map a one-off request into the unified CardData shape consumed by
   * TaskDrillIn. CommandCenter rows don't have a task_id; we pass the
   * request_id as both `id` and the popup subtitle. */
  const requestToCard = (r: RequestItem): CardData => ({
    id: r.request_id,
    request_id: r.request_id,
    task_id: null,
    phase: null,
    title: r.description,
    description: r.description,
    type: r.task_type?.replace("_request", ""),
    agent: null,
    priority: ((r.priority as "high" | "medium" | "low") || "medium"),
    status: r.status as TaskStatus,
    current_stage: null,  // drill-in derives stage from subtasks
  })
  const selectedCard: CardData | null = selectedRequestId
    ? (requests.find((x) => x.request_id === selectedRequestId)
        ? requestToCard(requests.find((x) => x.request_id === selectedRequestId)!)
        : null)
    : null

  const canMutate = (r: RequestItem): boolean => {
    // Matches backend permission: admin OR creator (owner) can cancel/delete.
    // If created_by is missing from the list payload, fall back to allowing —
    // backend will still enforce 403 if the user isn't allowed.
    if (!currentUser) return false
    if (currentUser.role === "admin") return true
    const owner = (r as any).created_by
    if (!owner) return true
    return owner === currentUser.username
  }

  const handleCancel = async (requestId: string) => {
    if (!window.confirm(`Cancel request ${requestId}?\n\nThe workflow will be stopped and marked as cancelled. You can delete it afterward.`)) return
    setBusyId(requestId)
    try {
      await api.post(`/requests/${requestId}/cancel`)
      await loadRequests()
    } catch (err: any) {
      alert(`Cancel failed: ${err?.message || err}`)
    } finally {
      setBusyId(null)
    }
  }

  const handleDelete = async (requestId: string) => {
    if (!window.confirm(`Permanently delete request ${requestId}?\n\nThis removes the request and all its subtasks, stories, documents, and cost records from the database. This cannot be undone.`)) return
    setBusyId(requestId)
    try {
      await api.delete(`/requests/${requestId}`)
      await loadRequests()
    } catch (err: any) {
      alert(`Delete failed: ${err?.message || err}`)
    } finally {
      setBusyId(null)
    }
  }

  const active = requests.filter((r) => !isTerminal(r.status))
  const completed = requests.filter((r) => isTerminal(r.status)).slice(0, 5)

  return (
    <div style={{ maxWidth: 2200, margin: "0 auto", padding: "24px 36px", display: "flex", flexDirection: "column", gap: 32 }}>
      {/* Input Form */}
      <div
        style={{
          background: "var(--bg-card)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          padding: 24,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
          <h2 style={{ color: "var(--text-primary)", fontSize: 18, fontWeight: 600, margin: 0 }}>
            New_Request.init
          </h2>
          <RefreshButton onClick={handleRefresh} refreshing={refreshing} />
        </div>

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

        {/* Project Selector (PM-38) — required dropdown above the team
            selector. The "+ New project..." sentinel option opens the
            CreateProjectModal (PM-39). When a project with a default_team
            is picked, we pre-fill the team selector. */}
        <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 8 }}>
          <label style={{ fontSize: 12, color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
            Project <span style={{ color: "var(--danger)" }}>*</span>
          </label>
          <select
            value={projectId}
            onChange={(e) => {
              const v = e.target.value
              if (v === NEW_PROJECT_SENTINEL) {
                setProjectModalOpen(true)
                return
              }
              setProjectId(v)
              const proj = projects.find((p) => p.project_id === v)
              if (proj?.default_team) {
                setSelectedTeam(proj.default_team)
                const taskDefaults: Record<string, string> = {
                  engineering: "feature_request",
                  research: "research_request",
                  content: "content_request",
                }
                setTaskType(taskDefaults[proj.default_team] || "feature_request")
              }
            }}
            style={{
              flex: 1, maxWidth: 380,
              padding: "6px 10px", fontSize: 13,
              background: "var(--bg-input, var(--bg-card))",
              color: "var(--text-primary)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              fontFamily: "var(--font)",
              cursor: "pointer",
            }}
          >
            {projects.length === 0 && <option value="">Loading…</option>}
            {projects.map((p) => (
              <option key={p.project_id} value={p.project_id}>
                {p.name}
              </option>
            ))}
            <option disabled>──────────</option>
            <option value={NEW_PROJECT_SENTINEL}>+ New project…</option>
          </select>
        </div>

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
            className="ch-submit-btn"
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
            <span className="ch-submit-btn-text">
              {submitting ? "Dispatching..." : "Dispatch"}
            </span>
          </button>
        </div>
      </div>

      {/* Active Requests */}
      {active.length > 0 && (
        <section>
          <h2 style={{ color: "var(--text-primary)", fontSize: 18, fontWeight: 600, marginBottom: 12 }}>
            <span className="ch-section-idx" aria-hidden="true">01</span>
            Active Requests
            <span className="ch-section-meta" aria-hidden="true">
              · <span className="ch-live">●</span> LIVE · {active.length} {active.length === 1 ? "proc" : "procs"}
            </span>
          </h2>
          <div className="ch-section-underline" aria-hidden="true" />
          {/* PM-PHASE4: bumped min card width 300 → 380 so the new ProjectChip
              in the header strip doesn't get crowded against the StatusBadge +
              Cancel button at typical 2-cards-per-row layouts. Cards drop to
              1 per row only on very narrow viewports.
              2026-05-22: auto-fill → auto-fit so a single in-flight request
              stretches across the full available width instead of leaving
              2-3 empty grid tracks to its right. With multiple cards the
              behaviour is identical (still wraps at minmax-380px). */}
          <div style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(380px, 1fr))" }}>
            {active.map((r) => (
              <div
                key={r.request_id}
                style={{
                  background: "var(--bg-card)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius)",
                  transition: "border-color 0.15s, box-shadow 0.15s",
                  overflow: "hidden",
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
                {/* Top header strip — request_id on the left, status + cancel as
                    flex siblings on the right. NOT inside the Link so they don't
                    overlap and stopPropagation isn't needed.
                    PM-PHASE4: flexWrap added so when the card is squeezed
                    (e.g., narrow viewport, 100% zoom with 2 cards/row), the
                    StatusBadge + Cancel button drop to a second line instead
                    of overlapping the ProjectChip. */}
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    flexWrap: "wrap",
                    gap: 8,
                    rowGap: 6,
                    padding: "12px 16px 0",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0, flexWrap: "wrap", rowGap: 4 }}>
                    <span className={`ch-card-dot ch-card-dot-${r.status}`} aria-hidden="true" />
                    {/* Plain span — the parent card body owns the click and
                        opens the popup drill-in. Old Link to /request/:id
                        was removed; we no longer navigate to the legacy
                        per-request page. */}
                    <span
                      className="ch-request-id"
                      style={{
                        fontSize: 12,
                        fontFamily: "var(--font-mono)",
                        color: "var(--text-muted)",
                      }}
                    >
                      {r.request_id}
                    </span>
                    <ProjectChip projectId={(r as any).project_id} />
                    {/* Removed redundant "· ● <status>" text — the StatusBadge
                        on the right already shows it, and dropping it gives
                        the row room to fit on a single line at 100% zoom with
                        2 cards-per-row (was previously wrapping). */}
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                    <StatusBadge status={r.status} />
                    {canMutate(r) && (
                      <button
                        type="button"
                        title="Cancel this request"
                        disabled={busyId === r.request_id}
                        onClick={() => handleCancel(r.request_id)}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 3,
                          padding: "2px 7px",
                          fontSize: 11,
                          fontWeight: 500,
                          fontFamily: "var(--font)",
                          background: "transparent",
                          color: "var(--text-muted)",
                          border: "1px solid var(--border)",
                          borderRadius: "var(--radius)",
                          cursor: busyId === r.request_id ? "wait" : "pointer",
                          opacity: busyId === r.request_id ? 0.5 : 1,
                          transition: "background 0.15s, color 0.15s, border-color 0.15s",
                        }}
                        onMouseEnter={(e) => {
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
                        <X size={10} />
                        {busyId === r.request_id ? "..." : "Cancel"}
                      </button>
                    )}
                  </div>
                </div>

                {/* Body — description + meta. Click opens the popup-window
                    drill-in (no navigation away from Command Center). The
                    Cancel button has stopPropagation so it doesn't open the
                    popup. */}
                <div
                  onClick={() => setSelectedRequestId(r.request_id)}
                  style={{
                    display: "block",
                    padding: "8px 16px 16px",
                    cursor: "pointer",
                    color: "inherit",
                  }}
                >
                  <p style={{ fontSize: 14, color: "var(--text-secondary)", overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", margin: 0 }}>
                    {r.description}
                  </p>
                  <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text-muted)" }}>
                    <span style={{ textTransform: "capitalize" }}>{r.task_type.replace("_", " ")}</span>
                    <span>·</span>
                    <span style={{ textTransform: "capitalize" }}>{r.priority}</span>
                  </div>
                </div>
                <div className={`ch-progress ch-progress-${r.status}`} aria-hidden="true">
                  <div className="ch-progress-bar" />
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Recently Completed */}
      {completed.length > 0 && (
        <section>
          <h2 style={{ color: "var(--text-primary)", fontSize: 18, fontWeight: 600, marginBottom: 12 }}>
            <span className="ch-section-idx" aria-hidden="true">02</span>
            Recently Completed
            <span className="ch-section-meta" aria-hidden="true">
              · {completed.length} of {requests.length} today
            </span>
          </h2>
          <div className="ch-section-underline" aria-hidden="true" />
          <div
            style={{
              background: "var(--bg-card)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              overflow: "hidden",
            }}
          >
            {completed.map((r, i) => (
              <div
                key={r.request_id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 12,
                  padding: "12px 16px",
                  borderTop: i > 0 ? "1px solid var(--border)" : "none",
                  transition: "background 0.15s",
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-hover)" }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent" }}
              >
                <div
                  onClick={() => setSelectedRequestId(r.request_id)}
                  style={{
                    flex: 1,
                    minWidth: 0,
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                    cursor: "pointer",
                  }}
                >
                  <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--text-muted)", flexShrink: 0 }}>
                    {r.request_id}
                  </span>
                  <ProjectChip projectId={(r as any).project_id} />
                  <span style={{ fontSize: 14, color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {r.description}
                  </span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                  <StatusBadge status={r.status} />
                  {canMutate(r) && (
                    <button
                      type="button"
                      title="Delete this request permanently"
                      disabled={busyId === r.request_id}
                      onClick={(e) => {
                        e.preventDefault()
                        e.stopPropagation()
                        handleDelete(r.request_id)
                      }}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 3,
                        padding: "3px 7px",
                        fontSize: 11,
                        fontWeight: 500,
                        fontFamily: "var(--font)",
                        background: "transparent",
                        color: "var(--text-muted)",
                        border: "1px solid var(--border)",
                        borderRadius: "var(--radius)",
                        cursor: busyId === r.request_id ? "wait" : "pointer",
                        opacity: busyId === r.request_id ? 0.5 : 1,
                        transition: "background 0.15s, color 0.15s, border-color 0.15s",
                      }}
                      onMouseEnter={(e) => {
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
                      {busyId === r.request_id ? "..." : "Delete"}
                    </button>
                  )}
                </div>
              </div>
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

      {/* PM-39 — inline "+ New project" modal. On create, select the new
          project in the dropdown so the user can immediately dispatch
          against it. Effect on projectModalOpen reloads the project list. */}
      <CreateProjectModal
        open={projectModalOpen}
        onClose={() => setProjectModalOpen(false)}
        onCreated={(p: CreatedProject) => {
          setProjectId(p.project_id)
          if (p.default_team) {
            setSelectedTeam(p.default_team)
            const td: Record<string, string> = {
              engineering: "feature_request",
              research: "research_request",
              content: "content_request",
            }
            setTaskType(td[p.default_team] || "feature_request")
          }
        }}
      />

      {/* Floating popup-window drill-in for one-off requests. Opens on
          click of any row in either the In Flight or Recently Completed
          sections. Non-modal — the page stays interactive behind it. */}
      {selectedCard && (
        <PopupWindow
          subtitle={selectedCard.request_id}
          title={selectedCard.title}
          onClose={() => setSelectedRequestId(null)}
        >
          <TaskDrillIn card={selectedCard} />
        </PopupWindow>
      )}
    </div>
  )
}

function _eventMessage(type: string, data: any): string {
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
