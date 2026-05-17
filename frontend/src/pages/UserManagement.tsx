import { useState, useEffect } from "react"
import { api } from "../lib/api"
import { StatusBadge } from "../components/ui/StatusBadge"

export function UserManagementPage() {
  const [users, setUsers] = useState<any[]>([])

  useEffect(() => {
    api.get("/users").then((res) => setUsers(res.data)).catch(() => {})
  }, [])

  // Theme-aware: hardcoded gray/white Tailwind classes replaced with
  // var(--*) arbitrary-value classes. Same fix pattern as History.tsx.
  // Also switched the active-user status text from "done" to "completed"
  // and inactive from "failed" to "cancelled" so the StatusBadge labels
  // read "completed"/"cancelled" (closer to "Active"/"Inactive" than the
  // old "DONE"/"FAILED").
  return (
    <div className="mx-auto max-w-[1800px] space-y-4 px-9 py-6">
      <h1 className="text-xl font-bold text-[var(--text-primary)]">User Management</h1>
      <div className="overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--bg-card)]">
        <table className="w-full text-sm">
          <thead className="border-b border-[var(--border)] bg-[var(--bg-secondary)] text-left text-xs font-medium text-[var(--text-muted)]">
            <tr>
              <th className="px-4 py-3">Username</th>
              <th className="px-4 py-3">Email</th>
              <th className="px-4 py-3">Role</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Last Login</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--border)]">
            {users.map((u) => (
              <tr key={u.user_id} className="hover:bg-[var(--bg-hover)]">
                <td className="px-4 py-3 font-medium text-[var(--text-primary)]">{u.username}</td>
                <td className="px-4 py-3 text-[var(--text-secondary)]">{u.email}</td>
                <td className="px-4 py-3 capitalize text-[var(--text-secondary)]">{u.role}</td>
                <td className="px-4 py-3">{u.is_active ? <StatusBadge status="completed" /> : <StatusBadge status="cancelled" />}</td>
                <td className="px-4 py-3 text-[var(--text-muted)]">{u.last_login_at ? new Date(u.last_login_at).toLocaleString() : "Never"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
