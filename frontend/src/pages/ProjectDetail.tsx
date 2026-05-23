/**
 * Project detail page (PM-33/34/35) at /projects/:projectId.
 *
 *   - Header: color/icon/name, description, lead, tags, repo link, target date, status
 *   - 4 stat cards (total / active / completed / cost USD)
 *   - "Next Steps" panel (PRJ-017 / PM-34) — only when project.template_id is set;
 *     renders the template's starter_checklist with click-to-pre-fill behavior
 *   - "Submit Request →" button (PM-35) that lands on Command Center
 *     with the project pre-selected via query param
 *   - Request list (every request in the project)
 *   - Recent Documents panel (latest 10 docs across requests)
 */

import { useEffect, useMemo, useState } from "react"
import { Link, useParams } from "react-router-dom"
import {
  Folder, Rocket, Layers, Code, FlaskConical, Palette, Bug, BookOpen,
  ArrowLeft, ExternalLink, FileText, ListChecks, CheckCircle2, Circle, Github,
  Pencil, Play, Square, Loader2, AlertTriangle, Boxes, Server, Globe, Trash2,
} from "lucide-react"
import { api } from "../lib/api"
import { StatusBadge } from "../components/ui/StatusBadge"
import { EditProjectModal } from "../components/projects/EditProjectModal"
import { BuildWorkspace } from "../components/projects/BuildWorkspace"
import { PopupWindow } from "../components/board/PopupWindow"
import { TaskDrillIn } from "../components/board/TaskDrillIn"
import type { CardData, TaskStatus } from "../components/board/types"
import { DeployJudgePanel } from "../components/projects/DeployJudgePanel"

const ICON_MAP: Record<string, React.ComponentType<{ size?: number; color?: string }>> = {
  folder: Folder, rocket: Rocket, layers: Layers, code: Code,
  "flask-conical": FlaskConical, palette: Palette, bug: Bug, "book-open": BookOpen,
}

// Per-project working tree feature — deploy lifecycle states. Matches
// src/models/base.py::DeployStatus.
type DeployStatus =
  | "stopped" | "pending_deploy" | "deploying"
  | "running" | "pending_stop" | "stopping" | "failed"
type ProjectKind = "web-app" | "api-service" | "frontend-app"

interface ProjectDetail {
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
  created_by: string | null
  created_at: string
  updated_at: string | null
  // Per-project working tree fields — may be null on legacy rows.
  kind: ProjectKind | null
  deploy_backend_port: number | null
  deploy_frontend_port: number | null
  deploy_status: DeployStatus | null
  deploy_url: string
  deploy_last_started_at: string | null
  deploy_error: string | null
  // AI Deploy Judge — Phases 4/7. The first two are surfaced by the
  // backend in _serialize(); the panel reads them from this row
  // instead of doing a separate fetch.
  last_deploy_commit_sha: string | null
  deploy_judge_preferences: string
  deploy_pending_action: string | null
  stats: { total: number; active: number; completed: number; failed: number }
  requests: Array<{
    request_id: string
    description: string
    task_type: string
    priority: string
    status: string
    created_at: string
    completed_at: string | null
  }>
  recent_documents: Array<{
    document_id: string
    request_id: string
    doc_type: string
    title: string
    agent_id: string
    version: number
    created_at: string
  }>
  template: {
    id: string
    name: string
    description: string
    starter_checklist: Array<{ description: string; task_type: string; priority: string }>
  } | null
}

export function ProjectDetailPage() {
  const { projectId } = useParams()
  const [data, setData] = useState<ProjectDetail | null>(null)
  const [error, setError] = useState("")
  const [editOpen, setEditOpen] = useState(false)
  // Click on a request row or document row → open the popup-window
  // drill-in. Keeps users inside the new UX instead of navigating to
  // the legacy /request/:id page.
  const [popupRequestId, setPopupRequestId] = useState<string | null>(null)
  // Per-row delete spinners. Keyed by the id being deleted so two
  // simultaneous clicks don't disable unrelated rows.
  const [deletingRequestId, setDeletingRequestId] = useState<string | null>(null)
  const [deletingDocumentId, setDeletingDocumentId] = useState<string | null>(null)
  // Bulk-delete state. Selection is keyed on request_id (NOT index)
  // so reordering / refetch doesn't desync the checkmarks. Pruned
  // automatically when the data refreshes (see effect below).
  const [selectedRequestIds, setSelectedRequestIds] = useState<Set<string>>(new Set())
  const [bulkDeleting, setBulkDeleting] = useState(false)

  /** DELETE /requests/:id — only allowed in terminal state. */
  const handleDeleteRequest = async (
    requestId: string,
    description: string,
  ) => {
    if (!window.confirm(
      `Permanently delete request ${requestId}?\n\n${description}\n\n` +
      "This removes the request and all its subtasks, stories, " +
      "documents, and cost records. Cannot be undone.",
    )) return
    setDeletingRequestId(requestId)
    try {
      await api.delete(`/requests/${requestId}`)
      // If the popup was open on this request, close it.
      if (popupRequestId === requestId) setPopupRequestId(null)
      await load()
    } catch (e: any) {
      alert(`Delete failed: ${parseDetailMessage(e?.message) || e?.message || "unknown error"}`)
    } finally {
      setDeletingRequestId(null)
    }
  }

  /** Bulk-delete selected requests via POST /requests/bulk_delete.
   * Surfaces partial-success counts (some rows skipped because the
   * backend deemed them non-terminal or forbidden). */
  const handleBulkDeleteRequests = async () => {
    const ids = Array.from(selectedRequestIds)
    if (ids.length === 0) return
    if (!window.confirm(
      `Permanently delete ${ids.length} selected request${ids.length === 1 ? "" : "s"}?\n\n` +
      "Each one's subtasks, stories, documents, and cost rows go with it. " +
      "Rows that aren't in a terminal state (completed/failed/cancelled) " +
      "will be skipped server-side.\n\nThis cannot be undone.",
    )) return
    setBulkDeleting(true)
    try {
      const res = await api.post("/requests/bulk_delete", { request_ids: ids })
      const meta = res?.meta || {}
      const skipped = res?.data?.skipped || []
      // If the popup was open on a deleted row, close it.
      if (popupRequestId && (res?.data?.deleted || []).includes(popupRequestId)) {
        setPopupRequestId(null)
      }
      setSelectedRequestIds(new Set())
      await load()
      if (meta.skipped_count > 0) {
        const reasons = skipped
          .map((s: any) => `${s.request_id} (${s.reason}${s.status ? `: ${s.status}` : ""})`)
          .join("\n  • ")
        window.alert(
          `Deleted ${meta.deleted_count} of ${meta.requested}.\n` +
          `Skipped:\n  • ${reasons}`,
        )
      }
    } catch (e: any) {
      window.alert(`Bulk delete failed: ${parseDetailMessage(e?.message) || e?.message || "unknown error"}`)
    } finally {
      setBulkDeleting(false)
    }
  }

  /** DELETE /documents/:id — single artifact removal. */
  const handleDeleteDocument = async (
    documentId: string,
    title: string,
  ) => {
    if (!window.confirm(
      `Delete document?\n\n${title}\n\n` +
      "The artifact will be removed from the project. Cannot be undone.",
    )) return
    setDeletingDocumentId(documentId)
    try {
      await api.delete(`/documents/${documentId}`)
      await load()
    } catch (e: any) {
      alert(`Delete failed: ${parseDetailMessage(e?.message) || e?.message || "unknown error"}`)
    } finally {
      setDeletingDocumentId(null)
    }
  }

  const load = async () => {
    if (!projectId) return
    try {
      const res = await api.get(`/projects/${projectId}`)
      setData(res.data)
    } catch (e: any) {
      setError(e?.message || "Failed to load project")
    }
  }

  useEffect(() => {
    load()
    const id = window.setInterval(load, 5000)
    return () => window.clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  // Prune the bulk-select set after each data refresh — drop IDs that
  // no longer exist (just deleted) or that flipped to a non-terminal
  // state (someone re-dispatched between checks). Keeps the
  // "Delete Selected (N)" counter honest.
  useEffect(() => {
    if (!data) return
    const terminal = new Set(
      data.requests
        .filter((r) => ["completed", "failed", "cancelled"].includes(r.status))
        .map((r) => r.request_id),
    )
    setSelectedRequestIds((prev) => {
      const next = new Set<string>()
      for (const id of prev) if (terminal.has(id)) next.add(id)
      return next.size === prev.size ? prev : next
    })
  }, [data])

  // Map checklist items → "filed" status by description-prefix match against
  // this project's requests. Approximate (no canonical template-instance
  // tracking yet) but good enough to dim items the user has actioned.
  const checklistMatches = useMemo(() => {
    if (!data?.template?.starter_checklist) return new Map<number, string>()
    const out = new Map<number, string>()
    data.template.starter_checklist.forEach((item, idx) => {
      // Strip the [placeholder] markers from the template description so we
      // can prefix-match the user's filled-in version.
      const prefix = item.description.split("[")[0].trim().toLowerCase()
      if (prefix.length < 5) return
      const match = data.requests.find((r) => r.description.toLowerCase().startsWith(prefix))
      if (match) out.set(idx, match.request_id)
    })
    return out
  }, [data])

  if (error) return <div style={{ padding: 24, color: "var(--danger)" }}>{error}</div>
  if (!data) return <div style={{ padding: 24, color: "var(--text-muted)" }}>Loading…</div>

  const Icon = ICON_MAP[data.icon] || Folder
  const isGithub = data.repo_url && /github\.com\//.test(data.repo_url)
  const targetDt = data.target_date ? new Date(data.target_date) : null
  const overdue = targetDt && targetDt < new Date()

  return (
    <div style={{ maxWidth: 2200, margin: "0 auto", padding: "24px 36px", display: "flex", flexDirection: "column", gap: 20 }}>
      <Link to="/projects" style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 13, color: "var(--text-muted)", textDecoration: "none" }}>
        <ArrowLeft size={14} /> Back to Projects
      </Link>

      {/* ── Header ── */}
      <div style={{
        position: "relative", overflow: "hidden",
        background: "var(--bg-card)", border: "1px solid var(--border)",
        borderRadius: "var(--radius)", padding: "20px 24px 20px 30px",
      }}>
        <div style={{ position: "absolute", top: 0, bottom: 0, left: 0, width: 4, background: data.color }} />
        <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
          <div style={{
            flexShrink: 0, width: 48, height: 48, borderRadius: 8,
            background: "var(--bg-hover)",
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            color: data.color, border: `1px solid ${data.color}`,
          }}>
            <Icon size={24} />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
              <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: "var(--text-primary)" }}>{data.name}</h2>
              <span style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>{data.project_id}</span>
              {data.status === "archived" && (
                <span style={{
                  padding: "2px 8px", borderRadius: 4, fontSize: 10, fontWeight: 700,
                  textTransform: "uppercase", letterSpacing: 1,
                  background: "var(--bg-hover)", color: "var(--text-muted)",
                  border: "1px solid var(--border)",
                }}>archived</span>
              )}
            </div>
            {data.description && (
              <p style={{ marginTop: 8, marginBottom: 0, fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.5 }}>
                {data.description}
              </p>
            )}
            <div style={{
              marginTop: 12, display: "flex", gap: 16, fontSize: 12,
              color: "var(--text-muted)", flexWrap: "wrap",
            }}>
              {data.created_by && <span>Lead: {data.created_by}</span>}
              {data.default_team && <span>Default team: {data.default_team}</span>}
              {targetDt && (
                <span style={{ color: overdue ? "var(--danger)" : undefined }}>
                  Target: {targetDt.toLocaleDateString()} {overdue && "(Overdue)"}
                </span>
              )}
              <span>Created: {new Date(data.created_at).toLocaleDateString()}</span>
              {data.repo_url && (
                <a href={data.repo_url} target="_blank" rel="noopener noreferrer"
                   style={{ display: "inline-flex", alignItems: "center", gap: 4, color: "var(--accent)", textDecoration: "none" }}>
                  {isGithub ? <Github size={12} /> : <ExternalLink size={12} />}
                  {isGithub ? "View on GitHub" : "Repo"}
                  <ExternalLink size={10} />
                </a>
              )}
              {data.project_id !== "proj-unassigned" && (
                <Link
                  to={`/stories/project/${data.project_id}`}
                  style={{ display: "inline-flex", alignItems: "center", gap: 4, color: "var(--accent)", textDecoration: "none" }}
                >
                  <Layers size={12} />
                  View Board
                </Link>
              )}
              {!data.repo_url && data.project_id !== "proj-unassigned" && (
                <CreateRepoButton projectId={data.project_id} onCreated={load} />
              )}
            </div>
            {data.tags.length > 0 && (
              <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 4 }}>
                {data.tags.map((t) => (
                  <span key={t} style={{
                    padding: "2px 8px", borderRadius: 4, fontSize: 10,
                    background: "var(--bg-hover)", color: "var(--text-secondary)",
                    fontFamily: "var(--font-mono)",
                  }}>{t}</span>
                ))}
              </div>
            )}
          </div>
          <div style={{ display: "flex", gap: 8, flexShrink: 0, alignItems: "center" }}>
            {data.project_id !== "proj-unassigned" && (
              <button
                type="button"
                onClick={() => setEditOpen(true)}
                title="Edit project details"
                style={{
                  display: "inline-flex", alignItems: "center", gap: 6,
                  padding: "8px 12px", fontSize: 12, fontWeight: 600,
                  background: "transparent", color: "var(--text-secondary)",
                  border: "1px solid var(--border)", borderRadius: "var(--radius)",
                  cursor: "pointer", whiteSpace: "nowrap", lineHeight: 1,
                  fontFamily: "var(--font)",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = "var(--accent)"
                  e.currentTarget.style.color = "var(--accent)"
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = "var(--border)"
                  e.currentTarget.style.color = "var(--text-secondary)"
                }}
              >
                <Pencil size={12} />
                <span>Edit</span>
              </button>
            )}
            <Link
              to={`/?project_id=${data.project_id}`}
              style={{
                display: "inline-flex", alignItems: "center",
                padding: "8px 14px", fontSize: 12, fontWeight: 700,
                background: "var(--accent)", color: "#0a0014",
                border: "1px solid var(--accent)", borderRadius: "var(--radius)",
                textDecoration: "none",
                whiteSpace: "nowrap", lineHeight: 1,
              }}
            >
              Submit Request →
            </Link>
          </div>
        </div>
      </div>

      {/* ── Stat cards ── */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 12 }}>
        <StatCard label="Total requests" value={String(data.stats.total)} />
        <StatCard label="Active" value={String(data.stats.active)} color="var(--accent)" />
        <StatCard label="Completed" value={String(data.stats.completed)} color="var(--success)" />
        <StatCard label="Failed" value={String(data.stats.failed)} color={data.stats.failed > 0 ? "var(--danger)" : "var(--text-muted)"} />
      </div>

      {/* ── BPD §6.8 — Build Plan rollup chip. Hidden when project
          has no epics (legacy or unassigned). Renders inline so
          the rollup info sits visually adjacent to the stat cards. */}
      {data.project_id !== "proj-unassigned" && (
        <BuildPlanRollupChip projectId={data.project_id} />
      )}

      {/* ── Deployment (per-project working tree feature) ── */}
      {data.project_id !== "proj-unassigned" && (
        <DeploymentCard
          projectId={data.project_id}
          kind={data.kind}
          backendPort={data.deploy_backend_port}
          frontendPort={data.deploy_frontend_port}
          status={data.deploy_status}
          url={data.deploy_url}
          error={data.deploy_error}
          lastStartedAt={data.deploy_last_started_at}
          judgePreferences={data.deploy_judge_preferences || ""}
          onChanged={load}
        />
      )}

      {/* ── Build Workspace (PDB-09 → PDB-12) — Brief → PRD ── */}
      {data.project_id !== "proj-unassigned" && (
        <BuildWorkspace projectId={data.project_id} />
      )}

      {/* ── Next Steps (PM-34) — only when template was selected ── */}
      {data.template && data.template.starter_checklist.length > 0 && (
        <div style={{
          background: "var(--bg-card)", border: "1px solid var(--border)",
          borderRadius: "var(--radius)", padding: 18,
        }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 8,
            fontSize: 13, fontWeight: 600, color: "var(--text-primary)",
            marginBottom: 12,
          }}>
            <ListChecks size={16} color={data.color} />
            Next Steps · template <code style={{ fontSize: 11, color: "var(--text-muted)" }}>{data.template.name}</code>
          </div>
          <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
            {data.template.starter_checklist.map((item, idx) => {
              const matchedReqId = checklistMatches.get(idx)
              const done = !!matchedReqId
              return (
                <li key={idx} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  {done
                    ? <CheckCircle2 size={14} color="var(--success)" />
                    : <Circle size={14} color="var(--text-muted)" />}
                  {done && matchedReqId ? (
                    <button
                      type="button"
                      onClick={() => setPopupRequestId(matchedReqId)}
                      style={{
                        fontSize: 13,
                        color: "var(--text-muted)",
                        textDecoration: "line-through",
                        flex: 1, minWidth: 0,
                        background: "transparent", border: "none",
                        textAlign: "left", padding: 0, cursor: "pointer",
                        fontFamily: "var(--font)",
                      }}
                    >
                      {item.description}
                    </button>
                  ) : (
                    <Link
                      to={`/?project_id=${data.project_id}&prefill=${encodeURIComponent(item.description)}&task_type=${item.task_type}&priority=${item.priority}`}
                      style={{
                        fontSize: 13,
                        color: "var(--text-primary)",
                        textDecoration: "none",
                        flex: 1, minWidth: 0,
                      }}
                    >
                      {item.description}
                    </Link>
                  )}
                  <span style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: 0.5 }}>
                    {item.task_type.replace("_", " ")} · {item.priority}
                  </span>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {/* ── Requests in this project ── */}
      <div style={{
        background: "var(--bg-card)", border: "1px solid var(--border)",
        borderRadius: "var(--radius)", padding: 18,
      }}>
        {/* Header row: title on the left, bulk-action toolbar on the
            right (appears only when at least one row is selected).
            Mirrors the TaskListEditor's bulk-delete pattern so the
            UX feels consistent across "manage many rows" surfaces. */}
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          gap: 8, marginBottom: 12, flexWrap: "wrap",
        }}>
          <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
            Requests in this project ({data.requests.length})
            {selectedRequestIds.size > 0 && (
              <span style={{
                marginLeft: 8, fontSize: 11, color: "var(--text-muted)",
                fontFamily: "var(--font-mono)", fontWeight: 400,
              }}>
                · {selectedRequestIds.size} selected
              </span>
            )}
          </h3>
          {selectedRequestIds.size > 0 && (() => {
            // Compute select-all checkbox state from the terminal-eligible
            // rows so the user can toggle the whole batch in one click.
            const terminalRows = data.requests.filter(
              (r) => ["completed", "failed", "cancelled"].includes(r.status),
            )
            const allSelected = terminalRows.length > 0 &&
              terminalRows.every((r) => selectedRequestIds.has(r.request_id))
            return (
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <button
                  type="button"
                  onClick={() => {
                    if (allSelected) {
                      setSelectedRequestIds(new Set())
                    } else {
                      setSelectedRequestIds(new Set(terminalRows.map((r) => r.request_id)))
                    }
                  }}
                  style={{
                    padding: "4px 10px", fontSize: 11,
                    background: "transparent", color: "var(--text-secondary)",
                    border: "1px solid var(--border)", borderRadius: "var(--radius)",
                    cursor: "pointer", fontFamily: "var(--font)",
                  }}
                >
                  {allSelected ? "Clear selection" : `Select all (${terminalRows.length})`}
                </button>
                <button
                  type="button"
                  onClick={handleBulkDeleteRequests}
                  disabled={bulkDeleting}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 6,
                    padding: "4px 12px", fontSize: 12, fontWeight: 600,
                    background: bulkDeleting
                      ? "var(--bg-hover)"
                      : "color-mix(in srgb, var(--danger) 12%, transparent)",
                    color: bulkDeleting ? "var(--text-muted)" : "var(--danger)",
                    border: "1px solid var(--danger)",
                    borderRadius: "var(--radius)",
                    cursor: bulkDeleting ? "wait" : "pointer",
                    fontFamily: "var(--font)",
                  }}
                >
                  {bulkDeleting ? <Loader2 size={12} className="spin" /> : <Trash2 size={12} />}
                  {bulkDeleting
                    ? "Deleting…"
                    : `Delete Selected (${selectedRequestIds.size})`}
                </button>
              </div>
            )
          })()}
        </div>
        {data.requests.length === 0 ? (
          <div style={{ color: "var(--text-muted)", fontSize: 13 }}>None yet.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {data.requests.map((r) => {
              const terminal = ["completed", "failed", "cancelled"].includes(r.status)
              const busy = deletingRequestId === r.request_id
              const selected = selectedRequestIds.has(r.request_id)
              const toggleSelect = (checked: boolean) => {
                setSelectedRequestIds((prev) => {
                  const next = new Set(prev)
                  if (checked) next.add(r.request_id)
                  else next.delete(r.request_id)
                  return next
                })
              }
              return (
                <div
                  key={r.request_id}
                  style={{
                    display: "flex", alignItems: "stretch", gap: 0,
                    background: selected
                      ? "color-mix(in srgb, var(--accent) 8%, var(--bg-hover))"
                      : "var(--bg-hover)",
                    border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
                    borderRadius: "var(--radius)", overflow: "hidden",
                    transition: "background 0.15s, border-color 0.15s",
                  }}
                >
                  {/* Bulk-select checkbox. Disabled for non-terminal rows
                      so the user can't queue a delete that the server
                      will reject anyway — matches the TaskListEditor's
                      "checkbox disabled when in-flight" pattern. */}
                  <label
                    title={terminal
                      ? "Select for bulk delete"
                      : `Request is ${r.status} — cancel it first to enable bulk delete`}
                    style={{
                      display: "inline-flex", alignItems: "center", justifyContent: "center",
                      width: 36, paddingLeft: 4,
                      borderRight: "1px solid var(--border)",
                      cursor: terminal ? "pointer" : "not-allowed",
                      opacity: terminal ? 1 : 0.35,
                    }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <input
                      type="checkbox"
                      checked={selected}
                      disabled={!terminal}
                      onChange={(e) => toggleSelect(e.target.checked)}
                      style={{ cursor: terminal ? "pointer" : "not-allowed" }}
                    />
                  </label>
                  <button
                    type="button"
                    onClick={() => setPopupRequestId(r.request_id)}
                    style={{
                      flex: 1, minWidth: 0,
                      display: "flex", alignItems: "center", gap: 12,
                      padding: "8px 12px",
                      background: "transparent", border: "none",
                      color: "inherit", fontSize: 13, textAlign: "left",
                      cursor: "pointer", fontFamily: "var(--font)",
                    }}
                  >
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-muted)", flexShrink: 0 }}>
                      {r.request_id}
                    </span>
                    <span style={{
                      flex: 1, color: "var(--text-primary)",
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    }}>
                      {r.description}
                    </span>
                    <span style={{ fontSize: 11, color: "var(--text-muted)", flexShrink: 0 }}>
                      {new Date(r.created_at).toLocaleDateString()}
                    </span>
                    <StatusBadge status={r.status} />
                  </button>
                  <button
                    type="button"
                    title={terminal
                      ? "Delete this request and all its data"
                      : `Request is ${r.status} — cancel it before deleting`}
                    disabled={!terminal || busy}
                    onClick={(e) => {
                      e.stopPropagation()
                      handleDeleteRequest(r.request_id, r.description)
                    }}
                    style={{
                      flexShrink: 0,
                      display: "inline-flex", alignItems: "center", justifyContent: "center",
                      width: 36, padding: 0,
                      background: "transparent",
                      color: terminal ? "var(--text-muted)" : "var(--text-muted)",
                      border: "none", borderLeft: "1px solid var(--border)",
                      cursor: !terminal ? "not-allowed" : busy ? "wait" : "pointer",
                      opacity: !terminal ? 0.35 : busy ? 0.5 : 1,
                      transition: "background 0.15s, color 0.15s",
                      fontFamily: "var(--font)",
                    }}
                    onMouseEnter={(e) => {
                      if (terminal && !busy) {
                        e.currentTarget.style.background = "color-mix(in srgb, var(--danger) 12%, transparent)"
                        e.currentTarget.style.color = "var(--danger)"
                      }
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = "transparent"
                      e.currentTarget.style.color = "var(--text-muted)"
                    }}
                  >
                    {busy
                      ? <Loader2 size={13} className="spin" />
                      : <Trash2 size={13} />}
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </div>

      <EditProjectModal
        open={editOpen}
        initial={{
          project_id: data.project_id,
          name: data.name,
          description: data.description,
          color: data.color,
          icon: data.icon,
          tags: data.tags,
          lead_user_id: data.lead_user_id,
          repo_url: data.repo_url,
          default_team: data.default_team,
          target_date: data.target_date,
        }}
        onClose={() => setEditOpen(false)}
        onSaved={load}
      />

      {/* ── Recent documents ── */}
      {data.recent_documents.length > 0 && (
        <div style={{
          background: "var(--bg-card)", border: "1px solid var(--border)",
          borderRadius: "var(--radius)", padding: 18,
        }}>
          <h3 style={{ margin: "0 0 12px 0", fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
            Recent documents ({data.recent_documents.length})
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {data.recent_documents.map((d) => {
              const busy = deletingDocumentId === d.document_id
              return (
                <div
                  key={d.document_id}
                  style={{
                    display: "flex", alignItems: "stretch",
                    borderRadius: "var(--radius)", overflow: "hidden",
                  }}
                >
                  <button
                    type="button"
                    onClick={() => setPopupRequestId(d.request_id)}
                    style={{
                      flex: 1, minWidth: 0,
                      display: "flex", alignItems: "center", gap: 10,
                      padding: "6px 10px",
                      background: "transparent", border: "none",
                      textAlign: "left", color: "inherit", fontSize: 12,
                      cursor: "pointer", fontFamily: "var(--font)",
                    }}
                  >
                    <FileText size={13} color="var(--accent)" />
                    <span style={{ color: "var(--text-primary)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {d.title}
                    </span>
                    <span style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                      v{d.version} · {d.doc_type}
                    </span>
                    <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                      {new Date(d.created_at).toLocaleDateString()}
                    </span>
                  </button>
                  <button
                    type="button"
                    title="Delete this document"
                    disabled={busy}
                    onClick={(e) => {
                      e.stopPropagation()
                      handleDeleteDocument(d.document_id, d.title)
                    }}
                    style={{
                      flexShrink: 0,
                      display: "inline-flex", alignItems: "center", justifyContent: "center",
                      width: 32, padding: 0,
                      background: "transparent",
                      color: "var(--text-muted)",
                      border: "none",
                      cursor: busy ? "wait" : "pointer",
                      opacity: busy ? 0.5 : 1,
                      transition: "background 0.15s, color 0.15s",
                      fontFamily: "var(--font)",
                    }}
                    onMouseEnter={(e) => {
                      if (!busy) {
                        e.currentTarget.style.background = "color-mix(in srgb, var(--danger) 12%, transparent)"
                        e.currentTarget.style.color = "var(--danger)"
                      }
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = "transparent"
                      e.currentTarget.style.color = "var(--text-muted)"
                    }}
                  >
                    {busy
                      ? <Loader2 size={12} className="spin" />
                      : <Trash2 size={12} />}
                  </button>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* ── Popup drill-in (replaces nav to legacy /request/:id) ── */}
      {popupRequestId && (() => {
        const r = data.requests.find((x) => x.request_id === popupRequestId)
        const card: CardData = {
          id: popupRequestId,
          task_id: null,
          request_id: popupRequestId,
          phase: null,
          title: r?.description || popupRequestId,
          description: r?.description || "",
          type: r?.task_type || null,
          agent: null,
          priority: ((r?.priority || "medium") as "high" | "medium" | "low"),
          status: ((r?.status || "in_progress") as TaskStatus),
          current_stage: null,
        }
        return (
          <PopupWindow
            subtitle={popupRequestId}
            title={card.title}
            onClose={() => setPopupRequestId(null)}
          >
            <TaskDrillIn card={card} />
          </PopupWindow>
        )
      })()}
    </div>
  )
}

// WS-16 — inline backfill action. Shown only when the project lacks a
// repo_url. Hits `POST /projects/:id/create_repo`, which calls the same
// GitHub Trees API path the create-project flow uses.
function CreateRepoButton({ projectId, onCreated }: { projectId: string; onCreated: () => void }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")
  const click = async () => {
    if (!window.confirm("Create a private GitHub repo for this project? Uses the project name to derive the slug.")) return
    setBusy(true); setErr("")
    try {
      await api.post(`/projects/${projectId}/create_repo`, {})
      onCreated()
    } catch (e: any) {
      // Pull the FastAPI `detail` out — could be string or {error, hint, ...}.
      const raw = e?.message || String(e)
      const m = raw.match(/^\d+:\s*(.+)$/)
      try {
        const parsed = m ? JSON.parse(m[1]) : null
        const d = parsed?.detail
        if (d) {
          setErr(typeof d === "string" ? d : (d.hint || d.message || JSON.stringify(d)))
        } else setErr(raw)
      } catch { setErr(raw) }
    } finally { setBusy(false) }
  }
  return (
    <>
      <button
        type="button"
        onClick={click}
        disabled={busy}
        style={{
          display: "inline-flex", alignItems: "center", gap: 4,
          padding: "2px 8px", fontSize: 11, fontFamily: "var(--font)",
          background: "transparent", color: busy ? "var(--text-muted)" : "var(--accent)",
          border: "1px dashed var(--accent)", borderRadius: 3,
          cursor: busy ? "wait" : "pointer",
        }}
        title="Create a private GitHub repo for this project (backfill)"
      >
        <Github size={11} />
        {busy ? "Creating…" : "Create GitHub repo"}
      </button>
      {err && (
        <span style={{ fontSize: 11, color: "var(--danger)" }}>{err}</span>
      )}
    </>
  )
}

function StatCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{
      background: "var(--bg-card)", border: "1px solid var(--border)",
      borderRadius: "var(--radius)", padding: "14px 16px",
      display: "flex", flexDirection: "column", gap: 4,
    }}>
      <div style={{ fontSize: 10, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: 1 }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 700, color: color || "var(--text-primary)" }}>
        {value}
      </div>
    </div>
  )
}


// ── Deployment card (per-project working tree feature) ──────────────
// Renders status + ports + launch URL + Deploy/Stop buttons. Polls
// the project detail endpoint (parent does the polling — we just read
// the `data` prop) so transitions through pending → deploying →
// running happen live without manual refresh.

const KIND_ICON: Record<ProjectKind, typeof Boxes> = {
  "web-app": Boxes,
  "api-service": Server,
  "frontend-app": Globe,
}
const KIND_LABEL: Record<ProjectKind, string> = {
  "web-app": "Web App",
  "api-service": "API Service",
  "frontend-app": "Frontend App",
}

interface DeploymentCardProps {
  projectId: string
  kind: ProjectKind | null
  backendPort: number | null
  frontendPort: number | null
  status: DeployStatus | null
  url: string
  error: string | null
  lastStartedAt: string | null
  // AI Deploy Judge — initial value of the per-project preferences
  // textarea. Empty string by default. The panel manages its own
  // state from here; parent doesn't track edits in flight.
  judgePreferences: string
  onChanged: () => void   // called after a successful deploy/stop request — parent reloads
}

function DeploymentCard({
  projectId, kind, backendPort, frontendPort, status, url, error,
  lastStartedAt, judgePreferences, onChanged,
}: DeploymentCardProps) {
  const [busy, setBusy] = useState<"deploy" | "stop" | null>(null)
  const [clientErr, setClientErr] = useState<string>("")

  if (!kind) {
    // Legacy project — no kind set. Show a gentle hint instead of the
    // deployment controls (no scaffold exists, no ports allocated).
    return (
      <div style={cardWrap}>
        <div style={cardHeader}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <Boxes size={16} color="var(--text-muted)" />
            <span style={{ fontSize: 13, fontWeight: 600 }}>Deployment</span>
          </span>
        </div>
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
          This project was created before per-project deploys were available.
          No scaffold or ports are set. Create a new project to get the deploy UI.
        </div>
      </div>
    )
  }

  const KindIcon = KIND_ICON[kind]
  const kindLabel = KIND_LABEL[kind]
  const effectiveStatus: DeployStatus = status || "stopped"
  const isRunning = effectiveStatus === "running"
  const isFailed = effectiveStatus === "failed"
  // Pending / deploying / stopping all count as "in flight" — disable both
  // buttons and show a spinner.
  const inFlight = (
    effectiveStatus === "pending_deploy" ||
    effectiveStatus === "deploying" ||
    effectiveStatus === "pending_stop" ||
    effectiveStatus === "stopping"
  )

  // The URL we surface to the user. For a web-app + frontend-app, point
  // at the frontend port (where the UI is); for api-service point at
  // the backend port. The server already chose this when it set
  // `deploy_url`, but if status is "stopped" the URL might be empty —
  // synthesize a preview so users can see where it WILL land.
  const previewUrl = url || (
    kind === "api-service"
      ? (backendPort ? `http://localhost:${backendPort}` : "")
      : (frontendPort ? `http://localhost:${frontendPort}` : "")
  )

  const deploy = async () => {
    setBusy("deploy"); setClientErr("")
    try {
      await api.post(`/projects/${projectId}/deploy`, {})
      onChanged()
    } catch (e: any) {
      setClientErr(parseDetailMessage(e?.message) || "Deploy failed")
    } finally {
      setBusy(null)
    }
  }
  const stop = async () => {
    if (!window.confirm("Stop this project's containers?")) return
    setBusy("stop"); setClientErr("")
    try {
      await api.post(`/projects/${projectId}/stop`, {})
      onChanged()
    } catch (e: any) {
      setClientErr(parseDetailMessage(e?.message) || "Stop failed")
    } finally {
      setBusy(null)
    }
  }

  return (
    <div style={cardWrap}>
      <div style={cardHeader}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <KindIcon size={16} color="var(--accent)" />
          <span style={{ fontSize: 13, fontWeight: 600 }}>Deployment</span>
          <span style={{
            fontSize: 10, color: "var(--text-muted)",
            padding: "2px 8px", borderRadius: 3,
            background: "var(--bg-hover)",
            fontFamily: "var(--font-mono)",
          }}>
            {kindLabel}
          </span>
        </span>
        <DeployStatusBadge status={effectiveStatus} />
      </div>

      {/* Body — ports, URL, last-started */}
      <div style={{
        display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
        gap: 12, marginTop: 4,
      }}>
        <Field label="Backend port" value={backendPort ? String(backendPort) : "—"} />
        <Field label="Frontend port" value={frontendPort ? String(frontendPort) : "—"} />
        <Field label="Launch URL" value={
          previewUrl
            ? <a href={previewUrl} target="_blank" rel="noopener noreferrer"
                style={{
                  color: isRunning ? "var(--accent)" : "var(--text-muted)",
                  textDecoration: "none",
                  display: "inline-flex", alignItems: "center", gap: 4,
                  pointerEvents: isRunning ? "auto" : "none",
                }}>
                {previewUrl}
                {isRunning && <ExternalLink size={11} />}
              </a>
            : "—"
        } />
        <Field label="Last deploy" value={
          lastStartedAt ? new Date(lastStartedAt).toLocaleString() : "Never"
        } />
      </div>

      {/* Error banner — only when status=failed AND error message exists */}
      {isFailed && error && (
        <div style={{
          marginTop: 10, padding: "8px 12px", borderRadius: "var(--radius)",
          border: "1px solid var(--danger)",
          background: "rgba(255, 59, 59, 0.08)",
          color: "var(--danger)", fontSize: 11, fontFamily: "var(--font-mono)",
          whiteSpace: "pre-wrap", maxHeight: 120, overflow: "auto",
          display: "flex", gap: 6, alignItems: "flex-start",
        }}>
          <AlertTriangle size={12} style={{ flexShrink: 0, marginTop: 1 }} />
          <div style={{ flex: 1, minWidth: 0 }}>{error}</div>
        </div>
      )}

      {/* Buttons */}
      <div style={{
        marginTop: 14, display: "flex", justifyContent: "space-between",
        alignItems: "center", gap: 12, flexWrap: "wrap",
      }}>
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
          {inFlight && (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <Loader2 size={12} className="spin" />
              {effectiveStatus === "pending_deploy" && "Queued for deploy…"}
              {effectiveStatus === "deploying" && "Building + starting containers…"}
              {effectiveStatus === "pending_stop" && "Queued for stop…"}
              {effectiveStatus === "stopping" && "Stopping containers…"}
              {" "}<em style={{ color: "var(--text-muted)" }}>(supervisor processes ~every 5s)</em>
            </span>
          )}
          {!inFlight && effectiveStatus === "stopped" && "Containers are stopped."}
          {!inFlight && isRunning && "Containers running. Click the URL above to open."}
          {!inFlight && isFailed && "Last operation failed. See error above; click Deploy to retry."}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {isRunning || inFlight ? (
            <button
              type="button" onClick={stop}
              disabled={busy !== null || effectiveStatus === "pending_stop" || effectiveStatus === "stopping"}
              style={dangerBtn(busy !== null || effectiveStatus !== "running")}
              title="Stop the project's containers"
            >
              <Square size={12} />
              {busy === "stop" ? "Stopping…" : "Stop"}
            </button>
          ) : (
            <button
              type="button" onClick={deploy}
              disabled={busy !== null}
              style={primaryBtn(busy !== null)}
              title="Build + start the project's containers"
            >
              <Play size={12} />
              {busy === "deploy" ? "Queuing…" : "Deploy"}
            </button>
          )}
        </div>
      </div>

      {clientErr && (
        <div style={{
          marginTop: 8, fontSize: 11, color: "var(--danger)",
        }}>
          {clientErr}
        </div>
      )}

      {/* AI Deploy Judge panel — drives the smart-route deploy flow
          (see src/core/project_deploy_judge.py). Renders 8 states
          based on drift + judge recommendation. Self-fetches and
          self-polls; parent only notifies it of project state
          changes via the existing onChanged. */}
      <DeployJudgePanel
        projectId={projectId}
        deployStatus={status}
        deployError={error}
        initialPreferences={judgePreferences}
        onProjectChanged={onChanged}
      />

      {/* Inline keyframes for the spinner — keeps the component
          self-contained; no global CSS edits needed. */}
      <style>{`
        @keyframes pd-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        .spin { animation: pd-spin 1s linear infinite; }
      `}</style>
    </div>
  )
}

function DeployStatusBadge({ status }: { status: DeployStatus }) {
  // Color rules match the cyberpunk-ish palette: green = healthy,
  // accent cyan = in-progress, red = failed, muted = stopped.
  const palette: Record<DeployStatus, { bg: string; fg: string; label: string }> = {
    stopped:        { bg: "var(--bg-hover)",                 fg: "var(--text-muted)", label: "Stopped" },
    pending_deploy: { bg: "rgba(0, 240, 255, 0.10)",         fg: "var(--accent)",     label: "Pending deploy" },
    deploying:      { bg: "rgba(0, 240, 255, 0.10)",         fg: "var(--accent)",     label: "Deploying" },
    running:        { bg: "rgba(57, 255, 20, 0.12)",         fg: "var(--success)",    label: "Running" },
    pending_stop:   { bg: "rgba(255, 140, 0, 0.10)",         fg: "var(--warning, #ff8c00)", label: "Pending stop" },
    stopping:       { bg: "rgba(255, 140, 0, 0.10)",         fg: "var(--warning, #ff8c00)", label: "Stopping" },
    failed:         { bg: "rgba(255, 42, 109, 0.12)",        fg: "var(--danger)",     label: "Failed" },
  }
  const p = palette[status]
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      padding: "3px 10px", borderRadius: 4, fontSize: 10, fontWeight: 700,
      textTransform: "uppercase", letterSpacing: 1,
      background: p.bg, color: p.fg,
      border: `1px solid ${p.fg}`,
    }}>
      {p.label}
    </span>
  )
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ fontSize: 10, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: 1 }}>
        {label}
      </span>
      <span style={{ fontSize: 12, color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}>
        {value}
      </span>
    </div>
  )
}

const cardWrap: React.CSSProperties = {
  background: "var(--bg-card)", border: "1px solid var(--border)",
  borderRadius: "var(--radius)", padding: 18,
  display: "flex", flexDirection: "column", gap: 10,
}
const cardHeader: React.CSSProperties = {
  display: "flex", alignItems: "center", justifyContent: "space-between",
}

function primaryBtn(disabled: boolean): React.CSSProperties {
  return {
    display: "inline-flex", alignItems: "center", gap: 6,
    padding: "6px 14px", fontSize: 12, fontWeight: 700,
    background: disabled ? "var(--bg-hover)" : "var(--accent)",
    color: disabled ? "var(--text-muted)" : "#0a0014",
    border: "1px solid " + (disabled ? "var(--border)" : "var(--accent)"),
    borderRadius: "var(--radius)",
    cursor: disabled ? "not-allowed" : "pointer",
    whiteSpace: "nowrap", lineHeight: 1, fontFamily: "var(--font)",
  }
}
function dangerBtn(disabled: boolean): React.CSSProperties {
  return {
    display: "inline-flex", alignItems: "center", gap: 6,
    padding: "6px 14px", fontSize: 12, fontWeight: 700,
    background: "transparent",
    color: disabled ? "var(--text-muted)" : "var(--danger)",
    border: "1px solid " + (disabled ? "var(--border)" : "var(--danger)"),
    borderRadius: "var(--radius)",
    cursor: disabled ? "not-allowed" : "pointer",
    whiteSpace: "nowrap", lineHeight: 1, fontFamily: "var(--font)",
    opacity: disabled ? 0.5 : 1,
  }
}

// FastAPI errors arrive as "<status>: <body>" through the api client.
/** BPD-34 — project header rollup chip. Fetches /build-plan/rollup
 * and renders a compact "N/M epics done · X/Y tasks · Z blocked"
 * strip. Returns null when the project has zero epics (legacy mode)
 * so the existing 4-card stat row stays the only header info. */
function BuildPlanRollupChip({ projectId }: { projectId: string }) {
  const [data, setData] = useState<{
    epics_done: number; epics_total: number;
    tasks_done: number; tasks_total: number;
    tasks_blocked: number; features_total: number;
  } | null>(null)
  useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const res = await api.get(`/projects/${projectId}/build-plan/rollup`)
        if (!cancelled) setData(res?.data || null)
      } catch { /* soft — chip just doesn't render */ }
    }
    void run()
    return () => { cancelled = true }
  }, [projectId])
  if (!data || data.epics_total === 0) return null
  const epicsComplete = data.epics_done === data.epics_total
  return (
    <div style={{
      display: "inline-flex", alignItems: "center", gap: 12,
      padding: "8px 14px", borderRadius: "var(--radius)",
      background: "var(--bg-card)", border: "1px solid var(--border)",
      fontSize: 12, fontFamily: "var(--font-mono)",
      alignSelf: "flex-start",
    }}>
      <span style={{ color: "var(--text-muted)", fontSize: 10, textTransform: "uppercase", letterSpacing: 1, fontWeight: 700 }}>
        Build Plan:
      </span>
      <span style={{ color: epicsComplete ? "var(--success)" : "var(--text-primary)" }}>
        <strong>{data.epics_done}</strong>
        <span style={{ color: "var(--text-muted)" }}>/{data.epics_total} epics</span>
      </span>
      <span style={{ color: "var(--border)" }}>·</span>
      <span style={{ color: data.tasks_done > 0 ? "var(--accent)" : "var(--text-secondary)" }}>
        <strong>{data.tasks_done}</strong>
        <span style={{ color: "var(--text-muted)" }}>/{data.tasks_total} tasks</span>
      </span>
      {data.tasks_blocked > 0 && (
        <>
          <span style={{ color: "var(--border)" }}>·</span>
          <span style={{ color: "var(--warning, #d4a017)" }}>
            <strong>{data.tasks_blocked}</strong>
            <span style={{ color: "var(--text-muted)" }}> blocked</span>
          </span>
        </>
      )}
    </div>
  )
}


// Pull the human-readable hint out of {detail: {error, hint, ...}}.
function parseDetailMessage(msg: string | undefined): string | undefined {
  if (!msg) return undefined
  const colon = msg.indexOf(":")
  if (colon < 0) return msg
  try {
    const parsed = JSON.parse(msg.slice(colon + 1).trim())
    const d = parsed?.detail ?? parsed
    if (typeof d === "string") return d
    if (typeof d === "object" && d) return d.hint || d.message || d.error || JSON.stringify(d)
    return msg
  } catch {
    return msg
  }
}
