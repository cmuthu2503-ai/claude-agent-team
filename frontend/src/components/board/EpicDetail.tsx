/**
 * EpicDetail — BPD-33 inner content for the epic-detail popup.
 *
 * Designed to slot inside the same PopupWindow that renders task
 * drill-ins, so an operator can open an epic in one window AND a
 * task in another and compare them side-by-side without losing
 * either.
 *
 * Data source: GET /projects/{pid}/epics/{eid}/status — the existing
 * rollup endpoint extended in BPD-33 with `rollup_stats`
 * (cost_usd, wall_seconds, commit_count, commit_shas, requests_walked)
 * and `title`. Endpoint URL is the only contract this component
 * cares about; failure modes (404, 500, network) all render a
 * compact error row that doesn't bloat the popup.
 *
 * Why not embed inside TaskDrillIn: the operator's mental question
 * for an epic ("how much has it cost? when did its features ship?
 * what commits landed under it?") is different from the per-task
 * stage-walking question TaskDrillIn answers. Different content,
 * different popup — keeps each focused.
 */

import { useEffect, useState } from "react"
import { GitCommit, Clock, DollarSign, CheckCircle2, ListTree } from "lucide-react"
import { api } from "../../lib/api"

interface FeatureSummary {
  feature_id: string
  title: string
  task_count: number
  counts_by_status: Record<string, number>
  complete: boolean
}

interface RollupStats {
  cost_usd: number
  wall_seconds: number
  commit_count: number
  commit_shas: string[]
  requests_walked: number
}

interface EpicStatus {
  epic_id: string
  title?: string
  feature_count: number
  features_complete: number
  task_count: number
  counts_by_status: Record<string, number>
  complete: boolean
  features: FeatureSummary[]
  rollup_stats?: RollupStats
}

function formatDuration(seconds: number): string {
  if (!seconds || seconds <= 0) return "—"
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m`
  return `${seconds}s`
}

function formatCost(usd: number): string {
  if (usd === 0) return "$0.00"
  if (usd < 0.01) return `$${usd.toFixed(4)}`
  return `$${usd.toFixed(2)}`
}

export function EpicDetail({ projectId, epicId }: { projectId: string; epicId: string }) {
  const [data, setData] = useState<EpicStatus | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    let cancelled = false
    api
      .get<{ data: EpicStatus }>(`/projects/${projectId}/epics/${epicId}/status`)
      .then((res) => {
        if (!cancelled) setData(res.data)
      })
      .catch((e) => {
        if (!cancelled) setError(e?.message || "Failed to load epic")
      })
    return () => {
      cancelled = true
    }
  }, [projectId, epicId])

  if (error) {
    return (
      <div style={{
        padding: 14, color: "var(--danger)", fontSize: 12,
      }}>{error}</div>
    )
  }
  if (!data) {
    return (
      <div style={{
        padding: 14, color: "var(--text-muted)", fontSize: 12,
      }}>Loading epic…</div>
    )
  }

  const rs = data.rollup_stats
  const progressPct =
    data.feature_count > 0
      ? Math.round((data.features_complete / data.feature_count) * 100)
      : 0

  return (
    <div style={{
      padding: 14, display: "flex", flexDirection: "column", gap: 14,
      fontSize: 13, fontFamily: "var(--font)",
    }}>

      {/* Rollup stats strip — 3-up grid of cost / wall time / commits.
          Always rendered so the layout doesn't jump if rollup_stats
          is missing (older backend deploy); zeros render with em-dash. */}
      <div
        data-testid="epic-rollup-stats"
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 10,
          padding: 12,
          background: "var(--bg-hover)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
        }}
      >
        <StatTile
          icon={<DollarSign size={14} />}
          label="Cost"
          value={rs ? formatCost(rs.cost_usd) : "—"}
          sub={rs ? `${rs.requests_walked} request${rs.requests_walked === 1 ? "" : "s"}` : undefined}
        />
        <StatTile
          icon={<Clock size={14} />}
          label="Wall time"
          value={rs ? formatDuration(rs.wall_seconds) : "—"}
          sub={rs && rs.wall_seconds > 0 ? "summed across requests" : undefined}
        />
        <StatTile
          icon={<GitCommit size={14} />}
          label="Commits"
          value={rs ? String(rs.commit_count) : "—"}
          sub={rs && rs.commit_count > 0 ? `${rs.commit_shas.length} unique` : undefined}
        />
      </div>

      {/* Epic progress bar — features_complete / feature_count */}
      <div>
        <div style={{
          display: "flex", justifyContent: "space-between",
          fontSize: 11, color: "var(--text-muted)", marginBottom: 4,
        }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            <ListTree size={12} /> {data.features_complete}/{data.feature_count} features complete
          </span>
          <span style={{ fontFamily: "var(--font-mono)" }}>{progressPct}%</span>
        </div>
        <div style={{
          height: 6, background: "var(--bg-card)",
          border: "1px solid var(--border)", borderRadius: 3, overflow: "hidden",
        }}>
          <div style={{
            height: "100%",
            width: `${progressPct}%`,
            background: data.complete ? "var(--success)" : "var(--accent)",
            transition: "width 0.3s ease",
          }} />
        </div>
      </div>

      {/* Per-feature list — one row per feature with status summary.
          Sorted: incomplete first so an operator's eye lands on the
          remaining work, not the green checks. */}
      <div>
        <div style={{
          fontSize: 11, fontWeight: 700, textTransform: "uppercase",
          letterSpacing: 0.5, color: "var(--text-secondary)",
          marginBottom: 6,
        }}>
          Features ({data.feature_count})
        </div>
        {data.features.length === 0 ? (
          <div style={{
            padding: "10px 12px", fontSize: 12, fontStyle: "italic",
            color: "var(--text-muted)", textAlign: "center",
            background: "var(--bg-hover)", borderRadius: 4,
          }}>
            No features under this epic yet.
          </div>
        ) : (
          <div
            data-testid="epic-feature-list"
            style={{ display: "flex", flexDirection: "column", gap: 4 }}
          >
            {[...data.features]
              .sort((a, b) => Number(a.complete) - Number(b.complete))
              .map((f) => (
                <FeatureRow key={f.feature_id} feature={f} />
              ))}
          </div>
        )}
      </div>

      {/* Commit list — only when there's something to show */}
      {rs && rs.commit_shas.length > 0 && (
        <div>
          <div style={{
            fontSize: 11, fontWeight: 700, textTransform: "uppercase",
            letterSpacing: 0.5, color: "var(--text-secondary)",
            marginBottom: 6,
          }}>
            Commits ({rs.commit_shas.length})
          </div>
          <div style={{
            display: "flex", flexWrap: "wrap", gap: 4,
            fontFamily: "var(--font-mono)", fontSize: 10,
          }}>
            {rs.commit_shas.map((sha) => (
              <span
                key={sha}
                title={sha}
                style={{
                  padding: "2px 6px",
                  background: "var(--bg-hover)",
                  border: "1px solid var(--border)",
                  borderRadius: 3,
                  color: "var(--text-secondary)",
                }}
              >
                {sha.slice(0, 7)}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function StatTile({
  icon, label, value, sub,
}: {
  icon: React.ReactNode
  label: string
  value: string
  sub?: string
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <div style={{
        display: "inline-flex", alignItems: "center", gap: 4,
        fontSize: 10, fontWeight: 600, textTransform: "uppercase",
        letterSpacing: 0.5, color: "var(--text-muted)",
      }}>
        {icon} {label}
      </div>
      <div style={{
        fontSize: 18, fontWeight: 700, color: "var(--text-primary)",
        fontFamily: "var(--font-mono)",
      }}>
        {value}
      </div>
      {sub && (
        <div style={{
          fontSize: 10, color: "var(--text-muted)",
          fontFamily: "var(--font-mono)",
        }}>
          {sub}
        </div>
      )}
    </div>
  )
}

function FeatureRow({ feature }: { feature: FeatureSummary }) {
  // Build a compact status summary like "3 deployed · 1 in_progress"
  // — only the non-zero buckets, in lifecycle order.
  const ORDER = [
    "deployed", "in_progress", "dispatched", "review",
    "testing", "backlog", "failed", "cancelled",
  ]
  const parts = ORDER
    .filter((s) => (feature.counts_by_status[s] || 0) > 0)
    .map((s) => `${feature.counts_by_status[s]} ${s}`)
  const summary = parts.length > 0 ? parts.join(" · ") : "no tasks"

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "6px 10px",
      background: "var(--bg-hover)",
      border: "1px solid var(--border)",
      borderLeft: `3px solid ${feature.complete ? "var(--success)" : "var(--accent)"}`,
      borderRadius: 4,
    }}>
      {feature.complete && (
        <CheckCircle2 size={12} color="var(--success)" />
      )}
      <span style={{
        flex: 1, fontSize: 12, fontWeight: 600,
        color: "var(--text-primary)",
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
      }}>
        {feature.title}
      </span>
      <span style={{
        fontSize: 10, color: "var(--text-muted)",
        fontFamily: "var(--font-mono)", whiteSpace: "nowrap",
      }}>
        {feature.task_count} task{feature.task_count === 1 ? "" : "s"} · {summary}
      </span>
    </div>
  )
}
