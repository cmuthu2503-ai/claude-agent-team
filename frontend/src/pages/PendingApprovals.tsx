/**
 * PendingApprovalsPage — HAI-31 (FR-081) human approval queue for proposals.
 *
 * Every state-changing action a service principal (Hermes) requests lands here
 * as a PENDING proposal (the approval gate, P2). A human Confirm runs the action
 * through the guarded dispatcher; Reject drops it. Nothing executes without an
 * explicit human decision — this page is that decision surface.
 *
 * Reuses the existing proposals API:
 *   GET    /api/v1/proposals?status=pending   — the queue
 *   POST   /api/v1/proposals/{id}/confirm      — human confirm + execute
 *   POST   /api/v1/proposals/{id}/reject       — human reject (optional reason)
 *
 * The one-time channel-approval token (HAI-30) is the OTHER human path (approve
 * from a Hermes channel without a dashboard session); this page is the dashboard
 * path. Both keep human authority absolute.
 */

import { useEffect, useState } from "react"
import { api } from "../lib/api"

interface Proposal {
  proposal_id: string
  action_type: string
  target_ref: string | null
  payload: Record<string, unknown> | null
  status: string
  proposed_by: string
  created_at: string
  decided_by: string | null
  decided_at: string | null
  executed_at: string | null
  ttl_seconds: number
  result_ref: string | null
  error: string | null
}

const STATUS_STYLE: Record<string, string> = {
  pending: "border-[var(--accent)] text-[var(--accent)]",
  confirmed: "border-[var(--info,#3b82f6)] text-[var(--info,#3b82f6)]",
  executed: "border-[var(--success)] text-[var(--success)]",
  failed: "border-[var(--danger)] text-[var(--danger)]",
  rejected: "border-[var(--text-muted)] text-[var(--text-muted)]",
  expired: "border-[var(--text-muted)] text-[var(--text-muted)]",
}

function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_STYLE[status] ?? "border-[var(--border)] text-[var(--text-secondary)]"
  return (
    <span
      className={`rounded border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${cls}`}
    >
      {status}
    </span>
  )
}

export function PendingApprovalsPage() {
  const [items, setItems] = useState<Proposal[]>([])
  const [scope, setScope] = useState<"pending" | "all">("pending")
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = async (s: "pending" | "all" = scope) => {
    setLoading(true)
    try {
      const q = s === "pending" ? "?status=pending" : ""
      const res = await api.get(`/proposals${q}`)
      setItems(res.data || [])
      setError(null)
    } catch (e: any) {
      setError(e?.message ?? "Failed to load proposals")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load(scope)
    // Light polling so the queue reflects newly-proposed actions without a
    // manual refresh. 10s is gentle; the page is low-traffic.
    const t = setInterval(() => load(scope), 10_000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope])

  const confirm = async (p: Proposal) => {
    if (!window.confirm(`Confirm and EXECUTE "${p.action_type}"${p.target_ref ? ` on ${p.target_ref}` : ""}?`))
      return
    setBusy(p.proposal_id)
    try {
      await api.post(`/proposals/${p.proposal_id}/confirm`, {})
      await load(scope)
    } catch (e: any) {
      setError(`Confirm ${p.proposal_id} failed: ${e?.message ?? e}`)
    } finally {
      setBusy(null)
    }
  }

  const reject = async (p: Proposal) => {
    const reason = window.prompt("Reason for rejection (optional):") ?? undefined
    setBusy(p.proposal_id)
    try {
      await api.post(`/proposals/${p.proposal_id}/reject`, { reason })
      await load(scope)
    } catch (e: any) {
      setError(`Reject ${p.proposal_id} failed: ${e?.message ?? e}`)
    } finally {
      setBusy(null)
    }
  }

  const pendingCount = items.filter((p) => p.status === "pending").length

  return (
    <div className="mx-auto max-w-[1200px] space-y-4 px-9 py-6">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-xl font-bold text-[var(--text-primary)]">
            Pending Approvals
            {pendingCount > 0 && (
              <span className="ml-2 rounded-full bg-[var(--accent)] px-2 py-0.5 text-xs font-bold text-white">
                {pendingCount}
              </span>
            )}
          </h1>
          <p className="text-sm text-[var(--text-muted)]">
            State-changing actions requested by agents await your decision here. Nothing runs until
            you Confirm — Reject drops it. This is the human gate on autonomous action.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex overflow-hidden rounded border border-[var(--border)] text-xs">
            {(["pending", "all"] as const).map((s) => (
              <button
                key={s}
                onClick={() => setScope(s)}
                className={`px-3 py-1 capitalize ${
                  scope === s
                    ? "bg-[var(--accent)] font-semibold text-white"
                    : "bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]"
                }`}
              >
                {s}
              </button>
            ))}
          </div>
          <button
            onClick={() => load(scope)}
            className="rounded border border-[var(--border)] bg-[var(--bg-secondary)] px-3 py-1 text-xs text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]"
          >
            {loading ? "…" : "Refresh"}
          </button>
        </div>
      </header>

      {error && (
        <div className="rounded border border-[var(--danger)] bg-[var(--bg-card)] px-3 py-2 text-sm text-[var(--danger)]">
          {error}
        </div>
      )}

      {items.length === 0 ? (
        <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-4 py-12 text-center text-[var(--text-muted)]">
          {scope === "pending"
            ? "No proposals awaiting approval. Agent-requested actions will queue here."
            : "No proposals yet."}
        </div>
      ) : (
        <ul className="space-y-3">
          {items.map((p) => {
            const isPending = p.status === "pending"
            return (
              <li
                key={p.proposal_id}
                className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4"
              >
                <div className="flex items-start justify-between gap-4 border-b border-[var(--border)] pb-2">
                  <div className="min-w-0 space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm font-bold text-[var(--text-primary)]">
                        {p.action_type}
                      </span>
                      <StatusBadge status={p.status} />
                    </div>
                    <div className="text-xs text-[var(--text-muted)]">
                      <span className="font-mono text-[var(--accent)]">{p.proposal_id}</span>
                      {p.target_ref && (
                        <>
                          {" · target "}
                          <span className="font-mono">{p.target_ref}</span>
                        </>
                      )}
                      {" · by "}
                      <span className="font-mono">{p.proposed_by}</span>
                      {" · "}
                      {p.created_at}
                    </div>
                    {p.decided_by && (
                      <div className="text-xs text-[var(--text-muted)]">
                        decided by <span className="font-mono">{p.decided_by}</span>
                        {p.decided_at ? ` · ${p.decided_at}` : ""}
                      </div>
                    )}
                    {p.result_ref && (
                      <div className="text-xs text-[var(--success)]">→ {p.result_ref}</div>
                    )}
                    {p.error && <div className="text-xs text-[var(--danger)]">⚠ {p.error}</div>}
                  </div>
                  {isPending && (
                    <div className="flex flex-shrink-0 gap-2">
                      <button
                        disabled={busy === p.proposal_id}
                        onClick={() => confirm(p)}
                        className="rounded bg-[var(--success)] px-3 py-1 text-xs font-semibold text-white disabled:opacity-50"
                      >
                        Confirm
                      </button>
                      <button
                        disabled={busy === p.proposal_id}
                        onClick={() => reject(p)}
                        className="rounded border border-[var(--danger)] px-3 py-1 text-xs font-semibold text-[var(--danger)] disabled:opacity-50"
                      >
                        Reject
                      </button>
                    </div>
                  )}
                </div>
                {p.payload && Object.keys(p.payload).length > 0 && (
                  <pre className="overflow-x-auto whitespace-pre-wrap pt-3 text-xs text-[var(--text-primary)]">
                    {JSON.stringify(p.payload, null, 2)}
                  </pre>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
