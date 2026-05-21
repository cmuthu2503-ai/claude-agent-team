/**
 * EnrichedTaskCard — the unified task / request card used on the
 * Build Board (per-project) and the Command Center recent-requests
 * list.
 *
 * Shows (when data is available):
 *   - Task ID + priority pill
 *   - Phase prefix + title
 *   - Live status block for non-backlog cards:
 *       agent badge + cycle counter
 *       workflow stage strip (PRD ✓ Stories ✓ Dev ⏳ Rev · Test · Commit · Deploy)
 *       elapsed time + $ spent
 *       success: ✓ Done · N files · commit-sha
 *       failure: ❌ error snippet + Retry button
 *   - Footer: type chip + request_id link
 *
 * Designed to gracefully degrade — missing fields are simply not
 * rendered, so this works equally for in-flight tasks (rich data)
 * and pure-backlog tasks (just ID + title).
 */

import {
  AlertTriangle, CheckCircle2, Clock, Bot, Rocket,
} from "lucide-react"
import type { CardData, WorkflowStage, TaskStatus } from "./types"

const STAGES: WorkflowStage[] = [
  "prd", "stories", "development", "review", "testing", "code_commit", "deploy",
]
const STAGE_SHORT: Record<WorkflowStage, string> = {
  prd: "PRD", stories: "Stories", development: "Dev", review: "Review",
  testing: "Test", code_commit: "Commit", deploy: "Deploy",
}

interface Props {
  card: CardData
  isSelected: boolean
  onClick: () => void
  /** Called when the user clicks "Retry" on a failed card. Optional —
   * not all hosts (Command Center) currently support retry. */
  onRetry?: () => void
}

export function EnrichedTaskCard({ card, isSelected, onClick, onRetry }: Props) {
  const priorityColor =
    card.priority === "high"   ? "var(--danger)"
    : card.priority === "low"  ? "var(--text-muted)"
    : "var(--accent)"

  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        textAlign: "left", width: "100%",
        padding: "10px 12px",
        background: isSelected ? "var(--bg-hover)" : "var(--bg-card)",
        border: "1px solid " + (isSelected ? "var(--accent)" : "var(--border)"),
        borderRadius: "var(--radius)",
        display: "flex", flexDirection: "column", gap: 6,
        cursor: "pointer", fontFamily: "var(--font)",
        boxShadow: isSelected ? "0 0 0 1px var(--accent)" : "none",
      }}
    >
      {/* Top line: card ID + priority pill */}
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        fontSize: 10, fontFamily: "var(--font-mono)",
      }}>
        <span style={{ color: "var(--text-muted)" }}>{card.task_id || card.id}</span>
        <span style={{
          color: priorityColor, fontWeight: 700, textTransform: "uppercase",
        }}>
          ● {card.priority}
        </span>
      </div>

      {/* Phase + title */}
      <div style={{
        fontSize: 12, fontWeight: 600,
        color: "var(--text-primary)", lineHeight: 1.35,
      }}>
        {card.phase && (
          <>
            <span style={{ color: "var(--text-muted)", fontWeight: 500 }}>{card.phase}</span>
            <br />
          </>
        )}
        {card.title}
      </div>

      {/* Live status block — only when not in pure-backlog state */}
      {isInFlightOrDone(card.status) && (
        <div style={{
          marginTop: 4, padding: "6px 8px",
          background: "var(--bg-hover)",
          border: "1px solid var(--border)",
          borderRadius: 4,
          display: "flex", flexDirection: "column", gap: 4,
        }}>
          {/* Agent + cycle */}
          {card.agent && card.cycle != null && (
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
              <Bot size={11} color="var(--accent)" />
              <span style={{ fontWeight: 600 }}>{card.agent}</span>
              {card.max_cycles != null && (
                <span style={{ color: "var(--text-muted)" }}>· cycle {card.cycle}/{card.max_cycles}</span>
              )}
            </div>
          )}

          {/* Workflow stage strip (in-flight only) */}
          {card.current_stage && isActive(card.status) && (
            <StageStrip currentStage={card.current_stage} />
          )}

          {/* Elapsed + cost */}
          {(card.elapsed_seconds != null || card.cost_usd != null) && (
            <div style={{
              display: "flex", alignItems: "center", gap: 10,
              fontSize: 10, color: "var(--text-muted)",
            }}>
              {card.elapsed_seconds != null && (
                <span style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>
                  <Clock size={10} />
                  {formatElapsed(card.elapsed_seconds)}
                </span>
              )}
              {card.cost_usd != null && card.cost_usd > 0 && (
                <span>· ${card.cost_usd.toFixed(2)}</span>
              )}
            </div>
          )}

          {/* Deployed summary */}
          {card.status === "deployed" && (
            <div style={{
              display: "flex", alignItems: "center", gap: 8,
              fontSize: 11, color: "var(--success)",
            }}>
              <CheckCircle2 size={11} />
              <span>
                Done
                {card.files_count != null ? ` · ${card.files_count} file${card.files_count === 1 ? "" : "s"}` : ""}
              </span>
              {card.commit_sha && (
                <>
                  <span style={{ color: "var(--text-muted)" }}>·</span>
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 10 }}>{card.commit_sha.slice(0, 8)}</span>
                </>
              )}
            </div>
          )}

          {/* Failed summary + retry */}
          {(card.status === "failed" || card.status === "cancelled") && card.error_summary && (
            <>
              <div style={{
                fontSize: 11, color: "var(--danger)",
                display: "flex", alignItems: "flex-start", gap: 4,
              }}>
                <AlertTriangle size={11} style={{ flexShrink: 0, marginTop: 1 }} />
                <span
                  style={{
                    whiteSpace: "nowrap", overflow: "hidden",
                    textOverflow: "ellipsis", maxWidth: "100%",
                  }}
                  title={card.error_summary}
                >
                  {card.error_summary}
                </span>
              </div>
              {onRetry && (
                <div style={{ display: "flex", gap: 6 }}>
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); onRetry() }}
                    style={{
                      padding: "2px 8px", fontSize: 10, fontWeight: 700,
                      background: "var(--accent)", color: "#0a0014",
                      border: "1px solid var(--accent)", borderRadius: 3,
                      cursor: "pointer", fontFamily: "var(--font)",
                    }}
                  >
                    Retry
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Footer — type chip + request id */}
      {(card.type || card.request_id) && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10 }}>
          {card.type && (
            <span style={{
              padding: "1px 6px", borderRadius: 3,
              background: "var(--bg-hover)", color: "var(--text-secondary)",
              fontFamily: "var(--font-mono)",
            }}>
              {card.type}
            </span>
          )}
          {card.request_id && (
            <span style={{
              color: "var(--accent)", fontFamily: "var(--font-mono)",
              display: "inline-flex", alignItems: "center", gap: 2,
            }}>
              <Rocket size={9} />
              {card.request_id}
            </span>
          )}
        </div>
      )}
    </button>
  )
}

// ── Helpers ──────────────────────────────────────────────────────────

function isInFlightOrDone(s: TaskStatus): boolean {
  return s !== "backlog" && s !== "pending"
}

function isActive(s: TaskStatus): boolean {
  return s === "dispatched" || s === "in_progress"
       || s === "review"     || s === "testing"
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60), s = seconds % 60
  if (m < 60) return `${m}m ${s}s`
  const h = Math.floor(m / 60), rm = m % 60
  return `${h}h ${rm}m`
}

function StageStrip({ currentStage }: { currentStage: WorkflowStage }) {
  const currentIdx = STAGES.indexOf(currentStage)
  return (
    <div style={{ display: "flex", gap: 2, fontSize: 9 }}>
      {STAGES.map((s, i) => {
        const isPast = i < currentIdx
        const isCurrent = i === currentIdx
        const color =
          isPast ? "var(--success)"
          : isCurrent ? "var(--accent)"
          : "var(--text-muted)"
        return (
          <span
            key={s}
            title={STAGE_SHORT[s]}
            style={{
              flex: 1, padding: "2px 0", textAlign: "center",
              background: isCurrent ? "color-mix(in srgb, var(--accent) 14%, transparent)" : "transparent",
              border: "1px solid " + (isPast ? "var(--success)" : isCurrent ? "var(--accent)" : "var(--border)"),
              borderRadius: 2, color,
              fontWeight: isCurrent ? 700 : 500,
              minWidth: 0, overflow: "hidden", textOverflow: "ellipsis",
            }}
          >
            {isPast ? "✓" : STAGE_SHORT[s][0]}
          </span>
        )
      })}
    </div>
  )
}
