/**
 * TaskDrillIn — the body content shown inside a PopupWindow when the
 * user clicks an EnrichedTaskCard.
 *
 * Loads detail data on mount:
 *   - GET /requests/:id          → status, commit_sha, error, cost
 *   - GET /requests/:id/subtasks → agent timeline + outputs (we
 *                                  derive stories/tests indirectly)
 *
 * Renders:
 *   - Workflow stage strip (full width)
 *   - Description (from the parent card)
 *   - Agent timeline (subtasks chronological)
 *   - Commit + deploy outputs when present
 *   - Error block when failed
 *
 * Gracefully degrades when the card has no request_id (pure-backlog
 * task — only description shown).
 */

import { useEffect, useState } from "react"
import {
  ExternalLink, GitCommit, AlertTriangle,
} from "lucide-react"
import { api } from "../../lib/api"
import type { CardData, WorkflowStage } from "./types"

const STAGES: WorkflowStage[] = [
  "prd", "stories", "development", "review", "testing", "code_commit", "deploy",
]
const STAGE_SHORT: Record<WorkflowStage, string> = {
  prd: "PRD", stories: "Stories", development: "Dev", review: "Review",
  testing: "Test", code_commit: "Commit", deploy: "Deploy",
}

interface SubtaskRow {
  subtask_id: string
  agent_id: string
  status: string
  started_at: string | null
  completed_at: string | null
}

interface RequestDetail {
  request_id: string
  status: string
  commit_sha?: string | null
  commit_url?: string | null
  published_files?: string[] | null
  code_commit_error?: string | null
}

export function TaskDrillIn({ card }: { card: CardData }) {
  const [loading, setLoading] = useState<boolean>(!!card.request_id)
  const [error, setError] = useState<string>("")
  const [subtasks, setSubtasks] = useState<SubtaskRow[]>([])
  const [detail, setDetail] = useState<RequestDetail | null>(null)

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      if (!card.request_id) {
        setLoading(false)
        return
      }
      setLoading(true)
      setError("")
      try {
        // Two parallel fetches: request meta + subtask history.
        const [reqRes, subRes] = await Promise.all([
          api.get<{ data: RequestDetail }>(`/requests/${card.request_id}`).catch(() => null),
          api.get<{ data: SubtaskRow[] }>(`/requests/${card.request_id}/subtasks`).catch(() => null),
        ])
        if (cancelled) return
        if (reqRes?.data) setDetail(reqRes.data)
        if (subRes?.data) setSubtasks(subRes.data)
      } catch (e: any) {
        if (!cancelled) setError(parseDetail(e?.message) || "Failed to load detail")
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void run()
    return () => { cancelled = true }
  }, [card.request_id])

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* Workflow stage strip — wide horizontal */}
      {card.current_stage && (
        <Section label="Workflow">
          <FullStageStrip
            currentStage={card.current_stage}
            terminalGood={card.status === "deployed"}
            terminalBad={card.status === "failed" || card.status === "cancelled"}
          />
        </Section>
      )}

      {/* Description */}
      {card.description && (
        <Section label="Description">
          <p style={{
            margin: 0, fontSize: 12, color: "var(--text-secondary)",
            lineHeight: 1.5, whiteSpace: "pre-wrap",
          }}>
            {card.description}
          </p>
        </Section>
      )}

      {/* Loading / error states for fetched detail */}
      {card.request_id && loading && (
        <p style={{ fontSize: 12, color: "var(--text-muted)", margin: 0 }}>
          Loading request detail…
        </p>
      )}
      {error && (
        <p style={{ fontSize: 12, color: "var(--danger)", margin: 0 }}>
          {error}
        </p>
      )}

      {/* Agent timeline */}
      {subtasks.length > 0 && (
        <Section label="Agent timeline" count={subtasks.length}>
          <div style={{
            display: "flex", flexDirection: "column", gap: 4,
            color: "var(--text-secondary)",
          }}>
            {subtasks.map((s) => (
              <div key={s.subtask_id} style={{
                display: "flex", gap: 8, fontSize: 11,
                alignItems: "center",
              }}>
                <span style={{
                  fontFamily: "var(--font-mono)", color: "var(--text-muted)",
                  minWidth: 56, fontSize: 10,
                }}>
                  {formatTs(s.started_at) || "—"}
                </span>
                <span style={{ fontWeight: 600, minWidth: 130 }}>{s.agent_id}</span>
                <StatusChip status={s.status} />
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Outputs — commit + deploy */}
      {(detail?.commit_sha || (detail?.published_files?.length ?? 0) > 0) && (
        <Section label="Outputs">
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {detail?.commit_sha && (
              <div style={{ display: "flex", gap: 8, fontSize: 11, alignItems: "center" }}>
                <GitCommit size={12} color="var(--accent)" />
                <span style={{ fontFamily: "var(--font-mono)" }}>{detail.commit_sha}</span>
                {detail.commit_url && (
                  <a
                    href={detail.commit_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      color: "var(--accent)", textDecoration: "none",
                      display: "inline-flex", alignItems: "center", gap: 2,
                    }}
                  >
                    open <ExternalLink size={10} />
                  </a>
                )}
              </div>
            )}
            {detail?.published_files && detail.published_files.length > 0 && (
              <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                <span style={{ color: "var(--text-muted)" }}>Files: </span>
                {detail.published_files.length} committed
              </div>
            )}
          </div>
        </Section>
      )}

      {/* Error block */}
      {(card.error_summary || detail?.code_commit_error) && (
        <Section label="Error">
          <div style={{
            padding: 8, borderRadius: 3,
            background: "color-mix(in srgb, var(--danger) 8%, transparent)",
            border: "1px solid var(--danger)",
            color: "var(--danger)", fontSize: 11, fontFamily: "var(--font-mono)",
            whiteSpace: "pre-wrap",
            display: "flex", gap: 6, alignItems: "flex-start",
          }}>
            <AlertTriangle size={12} style={{ flexShrink: 0, marginTop: 1 }} />
            <span style={{ flex: 1, minWidth: 0 }}>
              {card.error_summary || detail?.code_commit_error}
            </span>
          </div>
        </Section>
      )}

      {!card.request_id && !card.description && (
        <p style={{
          margin: 0, fontSize: 12, color: "var(--text-muted)",
          fontStyle: "italic",
        }}>
          This task hasn't been dispatched yet — no agent activity or outputs to show.
        </p>
      )}
    </div>
  )
}

// ── Helpers ──────────────────────────────────────────────────────────

function Section({
  label, count, children,
}: { label: string; count?: number; children: React.ReactNode }) {
  return (
    <div>
      <div style={{
        fontSize: 10, fontWeight: 700, letterSpacing: 1,
        textTransform: "uppercase", color: "var(--text-muted)",
        marginBottom: 4,
      }}>
        {label}{count !== undefined ? ` · ${count}` : ""}
      </div>
      {children}
    </div>
  )
}

function FullStageStrip({
  currentStage, terminalGood, terminalBad,
}: { currentStage: WorkflowStage; terminalGood: boolean; terminalBad: boolean }) {
  const currentIdx = STAGES.indexOf(currentStage)
  return (
    <div style={{ display: "flex", gap: 4 }}>
      {STAGES.map((s, i) => {
        const isPast = terminalGood || i < currentIdx
        const isCurrent = i === currentIdx && !terminalGood
        const isFailed = i === currentIdx && terminalBad
        const color =
          isFailed ? "var(--danger)" :
          isPast   ? "var(--success)" :
          isCurrent ? "var(--accent)" :
          "var(--text-muted)"
        return (
          <span
            key={s}
            style={{
              flex: 1, padding: "6px 4px", textAlign: "center",
              fontSize: 10, fontWeight: 600,
              background: isCurrent
                ? "color-mix(in srgb, var(--accent) 14%, transparent)"
                : isFailed
                  ? "color-mix(in srgb, var(--danger) 14%, transparent)"
                  : "var(--bg-hover)",
              border: "1px solid " + color,
              borderRadius: 3, color,
            }}
          >
            {isPast ? "✓ " : ""}{STAGE_SHORT[s]}
          </span>
        )
      })}
    </div>
  )
}

function StatusChip({ status }: { status: string }) {
  const color =
    status === "completed" || status === "deployed" ? "var(--success)" :
    status === "failed" || status === "cancelled"   ? "var(--danger)" :
    status === "in_progress" || status === "running" ? "var(--accent)" :
    "var(--text-muted)"
  return (
    <span style={{
      fontSize: 10, padding: "1px 6px", borderRadius: 2,
      color,
      background: "color-mix(in srgb, " + color + " 12%, transparent)",
      border: "1px solid " + color,
      fontFamily: "var(--font-mono)",
    }}>
      {status}
    </span>
  )
}

function formatTs(iso: string | null): string {
  if (!iso) return ""
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" })
  } catch {
    return iso.slice(11, 19)
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
    if (typeof d === "object" && d) return d.hint || d.message || d.error || JSON.stringify(d)
    return msg
  } catch {
    return msg
  }
}
