import { useState, useEffect } from "react"
import { api } from "../lib/api"
import { StatusBadge } from "../components/ui/StatusBadge"
import { Link } from "react-router-dom"
import { Trash2, X } from "lucide-react"
import { useAuthStore } from "../stores/auth"

const TERMINAL_STATUSES = ["completed", "failed", "cancelled"]
function isTerminal(status: string): boolean {
  return TERMINAL_STATUSES.includes(status)
}

export function HistoryPage() {
  const [requests, setRequests] = useState<any[]>([])
  const [statusFilter, setStatusFilter] = useState("")
  const [busyId, setBusyId] = useState<string | null>(null)
  const currentUser = useAuthStore((s) => s.user)

  const load = () => {
    const params = statusFilter ? `?status=${statusFilter}&per_page=50` : "?per_page=50"
    api.get(`/requests${params}`).then((res) => setRequests(res.data)).catch(() => {})
  }

  useEffect(load, [statusFilter])

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

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">History</h1>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm"
        >
          <option value="">All Statuses</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
          <option value="in_progress">In Progress</option>
        </select>
      </div>
      <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
        <table className="w-full text-sm">
          <thead className="border-b bg-gray-50 text-left text-xs font-medium text-gray-500">
            <tr>
              <th className="px-4 py-3">ID</th>
              <th className="px-4 py-3">Description</th>
              <th className="px-4 py-3">Type</th>
              <th className="px-4 py-3">Priority</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Created</th>
              <th className="px-4 py-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {requests.map((r) => {
              const terminal = isTerminal(r.status)
              const allowed = canMutate(r)
              const busy = busyId === r.request_id
              return (
                <tr key={r.request_id} className="hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <Link to={`/request/${r.request_id}`} className="font-mono text-blue-600 hover:underline">
                      {r.request_id}
                    </Link>
                  </td>
                  <td className="max-w-xs truncate px-4 py-3 text-gray-700">{r.description}</td>
                  <td className="px-4 py-3 capitalize text-gray-500">{r.task_type?.replace("_", " ")}</td>
                  <td className="px-4 py-3 capitalize text-gray-500">{r.priority}</td>
                  <td className="px-4 py-3"><StatusBadge status={r.status} /></td>
                  <td className="px-4 py-3 text-gray-400">{new Date(r.created_at).toLocaleDateString()}</td>
                  <td className="px-4 py-3 text-right">
                    {allowed && !terminal && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => handleCancel(r.request_id)}
                        className="inline-flex items-center gap-1 rounded-md border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:border-red-400 hover:bg-red-50 hover:text-red-600 disabled:opacity-50"
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
                        className="inline-flex items-center gap-1 rounded-md border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:border-red-400 hover:bg-red-50 hover:text-red-600 disabled:opacity-50"
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
          <div className="py-12 text-center text-gray-400">No requests found</div>
        )}
      </div>
    </div>
  )
}
