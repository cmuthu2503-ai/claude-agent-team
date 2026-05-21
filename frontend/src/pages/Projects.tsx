/**
 * Projects list page (PM-30/31/32) at /projects.
 *
 * Lists every project (active by default, archived togglable) with:
 *   - color stripe + icon + name + tags + lead avatar
 *   - request count, active count, total cost, target date, last activity
 *   - status badge (active/archived)
 *
 * Top toolbar: "+ New Project" button (opens CreateProjectModal) +
 * status filter dropdown (Active / Archived / All) + sort dropdown.
 *
 * Polls /projects every 10s so the list stays fresh when someone creates
 * a project elsewhere.
 */

import { useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import {
  Folder, Rocket, Layers, Code, FlaskConical, Palette, Bug, BookOpen,
  Plus, User as UserIcon, Trash2,
} from "lucide-react"
import { api } from "../lib/api"
import { CreateProjectModal, type CreatedProject } from "../components/projects/CreateProjectModal"
import { invalidateProjectsCache } from "../hooks/useProjectsCache"
import { useAuthStore } from "../stores/auth"

const ICON_MAP: Record<string, React.ComponentType<{ size?: number; color?: string }>> = {
  folder: Folder,
  rocket: Rocket,
  layers: Layers,
  code: Code,
  "flask-conical": FlaskConical,
  palette: Palette,
  bug: Bug,
  "book-open": BookOpen,
}

interface ProjectRow {
  project_id: string
  name: string
  description: string
  status: string
  color: string
  icon: string
  tags: string[]
  lead_user_id: string | null
  repo_url: string
  default_team: string | null
  target_date: string | null
  template_id: string | null
  created_at: string
  updated_at: string | null
  stats: { total: number; active: number; completed: number; failed: number }
}

type SortKey = "activity" | "name" | "created"
type StatusFilter = "active" | "archived" | "all"

export function ProjectsPage() {
  const [projects, setProjects] = useState<ProjectRow[] | null>(null)
  const [error, setError] = useState("")
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("active")
  const [sortKey, setSortKey] = useState<SortKey>("activity")
  const [modalOpen, setModalOpen] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const currentUser = useAuthStore((s) => s.user)
  const navigate = useNavigate()
  const isAdmin = currentUser?.role === "admin"

  const load = async () => {
    try {
      const path = statusFilter === "active"
        ? "/projects"
        : "/projects?include_archived=true"
      const res = await api.get(path)
      let rows: ProjectRow[] = res.data || []
      if (statusFilter === "archived") {
        rows = rows.filter((p) => p.status === "archived")
      }
      setProjects(rows)
    } catch (e: any) {
      setError(e?.message || "Failed to load projects")
    }
  }

  useEffect(() => {
    load()
    const id = window.setInterval(load, 10000)
    return () => window.clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter])

  // PM-46: live updates — listen for project.* lifecycle events emitted by
  // the backend (project.created, project.updated, project.archived,
  // project.deleted) plus request lifecycle events that change rollup stats.
  // On any of these, refetch the list and invalidate the shared cache so
  // every ProjectChip elsewhere refreshes too.
  useEffect(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/activity`)
    wsRef.current = ws

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        const t = msg.type || ""
        if (t.startsWith("project.")) {
          invalidateProjectsCache()
          void load()
        } else if (["request.created", "request.completed", "request.failed", "request.status_changed"].includes(t)) {
          // Rollup stats may have shifted — refresh just this list, no need
          // to invalidate the chip cache (project metadata unchanged).
          void load()
        }
      } catch {}
    }

    return () => {
      ws.close()
      wsRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const sorted = useMemo(() => {
    if (!projects) return null
    const out = [...projects]
    if (sortKey === "name") {
      out.sort((a, b) => a.name.localeCompare(b.name))
    } else if (sortKey === "created") {
      out.sort((a, b) => b.created_at.localeCompare(a.created_at))
    } else {
      // "activity" — fallback to created_at when updated_at is null
      out.sort((a, b) => (b.updated_at || b.created_at).localeCompare(a.updated_at || a.created_at))
    }
    return out
  }, [projects, sortKey])

  return (
    <div style={{ maxWidth: 2200, margin: "0 auto", padding: "24px 36px", display: "flex", flexDirection: "column", gap: 20 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h2 style={{ color: "var(--text-primary)", fontSize: 18, fontWeight: 600, margin: 0 }}>Projects</h2>
          <p style={{ color: "var(--text-secondary)", fontSize: 13, margin: "6px 0 0 0" }}>
            Group requests under a project so cost, status, and outputs roll up in one place.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
            style={selectStyle}
          >
            <option value="active">Active</option>
            <option value="archived">Archived</option>
            <option value="all">All</option>
          </select>
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            style={selectStyle}
          >
            <option value="activity">Sort: Last activity</option>
            <option value="name">Sort: Name</option>
            <option value="created">Sort: Created</option>
          </select>
          <button onClick={() => setModalOpen(true)} style={primaryBtnStyle}>
            <Plus size={14} />
            <span>New Project</span>
          </button>
        </div>
      </div>

      {error && (
        <div style={{ color: "var(--danger)", fontSize: 13 }}>{error}</div>
      )}

      {sorted === null && !error && (
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>Loading…</div>
      )}

      {sorted && sorted.length === 0 && (
        <div style={{
          padding: 40, borderRadius: "var(--radius)", textAlign: "center",
          background: "var(--bg-card)", border: "1px dashed var(--border)",
          color: "var(--text-muted)", fontSize: 14,
        }}>
          No projects yet. Click "+ New Project" to create one, or file a request without picking a project — it'll land in "Unassigned".
        </div>
      )}

      {sorted && sorted.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {sorted.map((p) => (
            <ProjectRowCard
              key={p.project_id}
              project={p}
              canDelete={isAdmin && p.project_id !== "proj-unassigned"}
              busy={busyId === p.project_id}
              onDelete={async () => {
                const reqCount = p.stats?.total ?? 0
                // Cascade-delete: wipes the project row, all child
                // requests (with their subtasks/stories/documents/cost
                // rows), and the local working tree. GitHub repo is
                // intentionally OUT of scope — delete it manually from
                // the GitHub UI.
                const reqLine = reqCount > 0
                  ? `  • ${reqCount} request${reqCount === 1 ? "" : "s"} (and all subtasks, stories, documents, cost rows)\n`
                  : ""
                const repoReminder = p.repo_url
                  ? `\nReminder: the GitHub repo at\n  ${p.repo_url}\nis NOT deleted by this action — remove it manually from GitHub if you want it gone.\n`
                  : ""
                const message =
                  `Permanently delete "${p.name}" (${p.project_id})?\n\n` +
                  `This will remove:\n` +
                  `  • The project row from the database\n` +
                  reqLine +
                  `  • The local working tree at C:/ai-projects/${p.name}/\n` +
                  repoReminder +
                  `\nCannot be undone.`
                if (!window.confirm(message)) return
                setBusyId(p.project_id)
                try {
                  const res: any = await api.delete(`/projects/${p.project_id}?cascade=true`)
                  invalidateProjectsCache()
                  await load()

                  // Surface a non-blocking summary if the local FS
                  // cleanup soft-failed (mount missing, permission denied,
                  // etc.) so the admin knows to clean up by hand.
                  const data = res?.data
                  if (data?.filesystem && !data.filesystem.ok) {
                    alert(
                      `Project "${p.name}" deleted, but local FS cleanup failed:\n\n` +
                      `  • ${data.filesystem.error || "unknown error"}\n\n` +
                      `You may need to remove C:/ai-projects/${p.name}/ manually.`,
                    )
                  }
                } catch (e: any) {
                  // The api client throws Error("<status>: <raw body>").
                  // Try to parse the body as JSON and extract the FastAPI
                  // `detail` payload — 409 returns a structured object,
                  // 4xx generally returns a string. Fall back to raw msg.
                  let msg = e?.message || "Delete failed"
                  try {
                    const colon = msg.indexOf(":")
                    if (colon >= 0) {
                      const body = JSON.parse(msg.slice(colon + 1).trim())
                      const detail = body?.detail ?? body
                      msg = typeof detail === "object"
                        ? `${detail.error || "Cannot delete"} — ${detail.hint || JSON.stringify(detail)}`.trim()
                        : String(detail)
                    }
                  } catch { /* keep msg as-is */ }
                  alert(msg)
                } finally {
                  setBusyId(null)
                }
              }}
            />
          ))}
        </div>
      )}

      <CreateProjectModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onCreated={(p: CreatedProject) => {
          // Land the user on the new project's detail page so they
          // immediately see the Build Workspace (brief textarea →
          // Generate PRD → Generate Tasks). Without this, the modal
          // just closes and the user has to find the new card in the
          // list and click into it — they often miss the workspace
          // entirely.
          navigate(`/projects/${p.project_id}`)
        }}
      />
    </div>
  )
}

function ProjectRowCard({
  project: p,
  canDelete,
  busy,
  onDelete,
}: {
  project: ProjectRow
  canDelete: boolean
  busy: boolean
  onDelete: () => void | Promise<void>
}) {
  const Icon = ICON_MAP[p.icon] || Folder
  return (
    <Link
      to={`/projects/${p.project_id}`}
      style={{
        position: "relative",
        display: "block", textDecoration: "none", color: "inherit",
        background: "var(--bg-card)", border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        padding: "14px 50px 14px 26px",  // right pad reserves space for the delete icon
        overflow: "hidden",
      }}
    >
      {/* Color stripe on the left edge */}
      <div style={{
        position: "absolute", top: 0, bottom: 0, left: 0, width: 4,
        background: p.color,
      }} />

      {/* Admin-only delete button, top-right. Sits outside the inner flex
          row so it never collides with the name/stats area. Stops navigation
          since the whole card is wrapped in <Link>. */}
      {canDelete && (
        <button
          type="button"
          title={`Delete ${p.name}`}
          aria-label={`Delete project ${p.name}`}
          disabled={busy}
          onClick={(e) => {
            e.preventDefault()
            e.stopPropagation()
            void onDelete()
          }}
          style={{
            position: "absolute",
            top: 10,
            right: 10,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 28,
            height: 28,
            padding: 0,
            background: "transparent",
            color: "var(--text-muted)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            cursor: busy ? "wait" : "pointer",
            opacity: busy ? 0.5 : 1,
            transition: "background 0.15s, color 0.15s, border-color 0.15s",
          }}
          onMouseEnter={(e) => {
            if (busy) return
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
          <Trash2 size={13} />
        </button>
      )}
      <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
        <div style={{
          flexShrink: 0,
          width: 36, height: 36, borderRadius: 6,
          background: "var(--bg-hover)",
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          color: p.color, border: `1px solid ${p.color}`,
        }}>
          <Icon size={18} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
            <span style={{ fontSize: 15, fontWeight: 600, color: "var(--text-primary)" }}>{p.name}</span>
            <span style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>{p.project_id}</span>
            {p.status === "archived" && (
              <span style={{
                padding: "1px 8px", borderRadius: 4, fontSize: 10, fontWeight: 700,
                textTransform: "uppercase", letterSpacing: 1,
                background: "var(--bg-hover)", color: "var(--text-muted)",
                border: "1px solid var(--border)",
              }}>archived</span>
            )}
          </div>
          {p.description && (
            <p style={{
              marginTop: 4, marginBottom: 0, fontSize: 13,
              color: "var(--text-secondary)", lineHeight: 1.4,
              display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden",
            }}>
              {p.description}
            </p>
          )}
          {p.tags.length > 0 && (
            <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>
              {p.tags.slice(0, 6).map((t) => (
                <span key={t} style={{
                  padding: "1px 6px", borderRadius: 4, fontSize: 10,
                  background: "var(--bg-hover)", color: "var(--text-secondary)",
                  fontFamily: "var(--font-mono)",
                }}>{t}</span>
              ))}
            </div>
          )}
          <div style={{
            marginTop: 10, display: "flex", gap: 16, fontSize: 12,
            color: "var(--text-muted)", flexWrap: "wrap",
          }}>
            <span>{p.stats.total} {p.stats.total === 1 ? "request" : "requests"}</span>
            {p.stats.active > 0 && <span style={{ color: "var(--accent)" }}>{p.stats.active} active</span>}
            {p.target_date && <span>Target: {new Date(p.target_date).toLocaleDateString()}</span>}
            <span>Last activity: {new Date(p.updated_at || p.created_at).toLocaleString()}</span>
            {p.lead_user_id && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                <UserIcon size={11} /> lead
              </span>
            )}
          </div>
        </div>
      </div>
    </Link>
  )
}

const selectStyle: React.CSSProperties = {
  padding: "6px 10px",
  fontSize: 12,
  background: "var(--bg-card)",
  color: "var(--text-primary)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  fontFamily: "var(--font)",
  cursor: "pointer",
}
const primaryBtnStyle: React.CSSProperties = {
  padding: "6px 14px",
  fontSize: 12,
  fontWeight: 700,
  background: "var(--accent)",
  color: "#0a0014",
  border: "1px solid var(--accent)",
  borderRadius: "var(--radius)",
  fontFamily: "var(--font)",
  cursor: "pointer",
  // Keep icon + label on a single inline line — cyberpunk button styling
  // adds enough horizontal padding/glow that the text was wrapping under
  // the icon at the default flow-layout.
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  whiteSpace: "nowrap",
  lineHeight: 1,
}
