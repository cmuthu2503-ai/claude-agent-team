import { useState, useEffect } from "react"
import { api } from "../lib/api"
import { StatusBadge } from "../components/ui/StatusBadge"
import { Trash2, X } from "lucide-react"
import { useAuthStore } from "../stores/auth"
import { useProjectsCache } from "../hooks/useProjectsCache"
import { ProjectChip } from "../components/projects/ProjectChip"
import { PopupWindow } from "../components/board/PopupWindow"
import { TaskDrillIn } from "../components/board/TaskDrillIn"
import type { CardData, TaskStatus } from "../components/board/types"

const TERMINAL_STATUSES = ["completed", "failed", "cancelled"]
function isTerminal(status: string): boolean {
  return TERMINAL_STATUSES.includes(status)
}

export function HistoryPage() {
  const [requests, setRequests] = useState<any[]>([])
  const [statusFilter, setStatusFilter] = useState("")
  const [projectFilter, setProjectFilter] = useState("")  // PM-42
  const [busyId, setBusyId] = useState<string | null>(null)
  // Click an ID → open the popup drill-in instead of navigating to the
  // legacy /request/:id page. Keeps users inside the new UX.
  const [popupRequestId, setPopupRequestId] = useState<string | null>(null)
  const currentUser = useAuthStore((s) => s.user)
  const { all: allProjects } = useProjectsCache()

  const load = () => {
    const parts: string[] = ["per_page=50"]
    if (statusFilter) parts.push(`status=${statusFilter}`)
    if (projectFilter) parts.push(`project_id=${projectFilter}`)
    api.get(`/requests?${parts.join("&")}`).then((res) => setRequests(res.data)).catch(() => {})
  }

  useEffect(load, [statusFilter, projectFilter])

  const canMutate = (r: any): boolean => {
    if (!currentUser) return false
    if (currentUser.role === "admin") return true
    if (!r.created_by) return true
    return r.created_by === currentUser.username
  }

  const handleCancel = async (requestId: string) => {
    if (!window.confirm(`Cancel request ${requestId}?\n\nThe workflow will be stopped and marked as cancelled.`)) return
    setBusyId(requestId)
    try {
      await api.post(`/requests/${requestId}/cancel`)
      load()
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
      load()
    } catch (err: any) {
      alert(`Delete failed: ${err?.message || err}`)
    } finally {
      setBusyId(null)
    }
  }

  // All `gray-XXX` / `bg-white` / `text-blue-600` hardcoded Tailwind colors
  // replaced with arbitrary-value classes that resolve to theme CSS variables,
  // so this page picks up whatever theme is active (was locked to light-mode
  // grays regardless of theme). Layout/structure unchanged.
  return (
    <div className="mx-auto max-w-[1800px] space-y-4 px-9 py-6">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h1 className="text-xl font-bold text-[var(--text-primary)]">History</h1>
        <div className="flex items-center gap-2">
          <select
            value={projectFilter}
            onChange={(e) => setProjectFilter(e.target.value)}
            className="rounded-md border border-[var(--border)] bg-[var(--bg-card)] text-[var(--text-primary)] px-3 py-1.5 text-sm"
          >
            <option value="">All Projects</option>
            {allProjects().map((p) => (
              <option key={p.project_id} value={p.project_id}>{p.name}</option>
            ))}
          </select>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded-md border border-[var(--border)] bg-[var(--bg-card)] text-[var(--text-primary)] px-3 py-1.5 text-sm"
          >
            <option value="">All Statuses</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
            <option value="in_progress">In Progress</option>
          </select>
        </div>
      </div>
      <div className="overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--bg-card)]">
        <table className="w-full text-sm">
          <thead className="border-b border-[var(--border)] bg-[var(--bg-secondary)] text-left text-xs font-medium text-[var(--text-muted)]">
            <tr>
              <th className="px-4 py-3">ID</th>
              <th className="px-4 py-3">Project</th>
              <th className="px-4 py-3">Description</th>
              <th className="px-4 py-3">Type</th>
              <th className="px-4 py-3">Priority</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Created</th>
              <th className="px-4 py-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border)]">
            {requests.map((r) => {
              const terminal = isTerminal(r.status)
              const allowed = canMutate(r)
              const busy = busyId === r.request_id
              return (
                <tr key={r.request_id} className="hover:bg-[var(--bg-hover)]">
                  <td className="px-4 py-3">
                    <button
                      type="button"
                      onClick={() => setPopupRequestId(r.request_id)}
                      className="font-mono text-[var(--accent)] hover:underline bg-transparent border-0 p-0 cursor-pointer"
                    >
                      {r.request_id}
                    </button>
                  </td>
                  <td className="px-4 py-3"><ProjectChip projectId={r.project_id} /></td>
                  <td className="max-w-xs truncate px-4 py-3 text-[var(--text-primary)]">{r.description}</td>
                  <td className="px-4 py-3 capitalize text-[var(--text-secondary)]">{r.task_type?.replace("_", " ")}</td>
                  <td className="px-4 py-3 capitalize text-[var(--text-secondary)]">{r.priority}</td>
                  <td className="px-4 py-3"><StatusBadge status={r.status} /></td>
                  <td className="px-4 py-3 text-[var(--text-muted)]">{new Date(r.created_at).toLocaleDateString()}</td>
                  <td className="px-4 py-3 text-right">
                    {allowed && !terminal && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => handleCancel(r.request_id)}
                        className="inline-flex items-center gap-1 rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-xs text-[var(--text-secondary)] hover:border-[var(--danger)] hover:text-[var(--danger)] disabled:opacity-50"
                        title="Cancel this in-flight request"
                      >
                        <X size={12} />
                        {busy ? "..." : "Cancel"}
                      </button>
                    )}
                    {allowed && terminal && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => handleDelete(r.request_id)}
                        className="inline-flex items-center gap-1 rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-xs text-[var(--text-secondary)] hover:border-[var(--danger)] hover:text-[var(--danger)] disabled:opacity-50"
                        title="Delete this request permanently"
                      >
                        <Trash2 size={12} />
                        {busy ? "..." : "Delete"}
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {requests.length === 0 && (
          <div className="py-12 text-center text-[var(--text-muted)]">No requests found</div>
        )}
      </div>

      {/* ── Popup drill-in (replaces nav to legacy /request/:id) ── */}
      {popupRequestId && (() => {
        const r = requests.find((x) => x.request_id === popupRequestId)
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
