/**
 * LessonsReviewPage — AET-13 human-in-the-loop approval queue for
 * lessons drafted by the self_learning_agent.
 *
 * Auto-generated lessons land in docs/agent-lessons-learned.pending.md
 * (see src/tools/lessons_writer.py::_append_to_pending). This page
 * lists them and lets an admin approve (promote into the canonical doc
 * every code-writing agent reads) or reject (drop).
 *
 * Admin-only — the backend approve/reject endpoints enforce role; we
 * still gate the nav link in Sidebar so non-admins don't see a 403'd
 * page.
 */

import { useEffect, useState } from "react"
import { api } from "../lib/api"

interface PendingLesson {
  lesson_id: string
  request_id: string
  created: string
  body: string
}

export function LessonsReviewPage() {
  const [entries, setEntries] = useState<PendingLesson[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    try {
      const res = await api.get("/lessons/pending")
      setEntries(res.data || [])
      setError(null)
    } catch (e: any) {
      setError(e?.message ?? "Failed to load pending lessons")
    }
  }

  useEffect(() => {
    load()
  }, [])

  const decide = async (lessonId: string, action: "approve" | "reject") => {
    setBusy(lessonId)
    try {
      await api.post(`/lessons/${lessonId}/${action}`, {})
      await load()
    } catch (e: any) {
      setError(`${action} ${lessonId} failed: ${e?.message ?? e}`)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="mx-auto max-w-[1200px] space-y-4 px-9 py-6">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-xl font-bold text-[var(--text-primary)]">Lessons Review</h1>
          <p className="text-sm text-[var(--text-muted)]">
            Auto-generated lessons from the self-learning agent. Approve to add to the canonical
            doc that every code-writing agent reads on every invocation.
          </p>
        </div>
        <button
          onClick={load}
          className="rounded border border-[var(--border)] bg-[var(--bg-secondary)] px-3 py-1 text-xs text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]"
        >
          Refresh
        </button>
      </header>

      {error && (
        <div className="rounded border border-[var(--danger)] bg-[var(--bg-card)] px-3 py-2 text-sm text-[var(--danger)]">
          {error}
        </div>
      )}

      {entries.length === 0 ? (
        <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-4 py-12 text-center text-[var(--text-muted)]">
          No lessons pending review. The self-learning agent will queue new patterns here as
          they are observed.
        </div>
      ) : (
        <ul className="space-y-3">
          {entries.map((e) => (
            <li
              key={e.lesson_id}
              className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4"
            >
              <div className="flex items-center justify-between gap-4 border-b border-[var(--border)] pb-2 text-xs text-[var(--text-muted)]">
                <span>
                  <span className="font-mono font-bold text-[var(--accent)]">{e.lesson_id}</span>
                  {" · from "}
                  <span className="font-mono">{e.request_id}</span>
                  {" · queued "}
                  {e.created}
                </span>
                <div className="flex gap-2">
                  <button
                    disabled={busy === e.lesson_id}
                    onClick={() => decide(e.lesson_id, "approve")}
                    className="rounded bg-[var(--success)] px-3 py-1 text-xs font-semibold text-white disabled:opacity-50"
                  >
                    Approve
                  </button>
                  <button
                    disabled={busy === e.lesson_id}
                    onClick={() => decide(e.lesson_id, "reject")}
                    className="rounded border border-[var(--danger)] px-3 py-1 text-xs font-semibold text-[var(--danger)] disabled:opacity-50"
                  >
                    Reject
                  </button>
                </div>
              </div>
              <pre className="overflow-x-auto whitespace-pre-wrap pt-3 text-xs text-[var(--text-primary)]">
                {e.body}
              </pre>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
