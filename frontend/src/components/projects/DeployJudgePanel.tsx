/**
 * DeployJudgePanel — the per-project AI Deploy Judge UI.
 *
 * Renders inside the Project Detail page's existing Deployment card.
 * Drives the eight states the judge can produce:
 *
 *   1. NO DRIFT       — clean baseline; hidden body, just "Up to date"
 *   2. SKIP           — docs-only / no-op commits; one-click "Mark deployed"
 *   3. LOW-RISK       — restart-X / rebuild-X at risk=low; primary CTA pulses
 *   4. HIGH-RISK      — judge wants caution; no primary CTA, manual choice
 *   5. THINKING       — first fetch in-flight
 *   6. APPLYING       — supervisor is running the chosen action
 *   7. FAILED         — last deploy failed; judge re-evaluates with context
 *   8. OVERRIDE-HINT  — subtle "you've overridden N of 5 recent" badge
 *
 * Lifecycle:
 *   - Mount fetches GET /projects/:id/deploy/judge.
 *   - Polls every 10s while the project is `running` / `stopped` (no
 *     deploy in flight) so the panel reflects fresh commits without
 *     manual refresh.
 *   - While `pending_deploy` / `deploying`, polls every 3s for fast
 *     progress feedback, but does NOT re-call the judge (the
 *     decision is locked-in until the deploy resolves).
 *   - After Apply/Override resolve to a new project state, the parent
 *     re-fetches the project row; the panel notices the
 *     ``deploy_status`` change and either shows the in-progress state
 *     or refetches the judge for the next recommendation.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  Sparkles, AlertTriangle, CheckCircle2, Loader2,
  GitCommit, FileText, Settings2, RefreshCw,
} from "lucide-react"
import { api } from "../../lib/api"

// ── Types matching the backend response shape ──────────────────────────────

interface DriftCommit {
  request_id: string
  commit_sha: string
  description: string
  files: string[]
  file_count: number
  completed_at: string
}

interface ProjectDrift {
  project_id: string
  has_drift: boolean
  commit_count: number
  from_commit_sha: string | null
  to_commit_sha: string | null
  commits: DriftCommit[]
  files_touched: string[]
  over_limit: boolean
}

type DeployAction =
  | "skip"
  | "restart-backend"
  | "restart-frontend"
  | "rebuild-backend"
  | "rebuild-frontend"
  | "rebuild-all"
  | "hold"

type DeployRisk = "low" | "medium" | "high"

interface DeployDecision {
  decision_id: string
  project_id: string
  drift_summary: DriftCommit[]
  from_commit_sha: string | null
  to_commit_sha: string | null
  action: DeployAction
  risk: DeployRisk
  confidence: DeployRisk
  reasoning: string
  from_llm: boolean
  status: "pending" | "applied" | "overridden" | "superseded"
  overridden_action: DeployAction | null
  created_at: string
  applied_at: string | null
}

interface JudgeResponse {
  drift: ProjectDrift
  decision: DeployDecision | null
}

interface Props {
  projectId: string
  /** Live project.deploy_status from the parent — used to pick polling
   *  cadence and decide whether to show the in-progress state.       */
  deployStatus: string | null
  /** Live project.deploy_error — surfaced as State 7 when present.   */
  deployError: string | null
  /** User-editable judge preferences (free text). Empty by default.  */
  initialPreferences: string
  /** Parent reloader — called after any action that flips state.     */
  onProjectChanged: () => void
}

// ── Display helpers ──────────────────────────────────────────────────────

const ACTION_LABEL: Record<DeployAction, string> = {
  "skip":              "Skip (no docker action)",
  "restart-backend":   "Restart backend",
  "restart-frontend":  "Restart frontend",
  "rebuild-backend":   "Rebuild backend",
  "rebuild-frontend":  "Rebuild frontend",
  "rebuild-all":       "Rebuild everything",
  "hold":              "Hold (manual review)",
}

const RISK_COLOR: Record<DeployRisk, string> = {
  low: "var(--success)",
  medium: "var(--warning, #f59e0b)",
  high: "var(--danger)",
}

const ALL_ACTIONS: DeployAction[] = [
  "skip", "restart-backend", "restart-frontend",
  "rebuild-backend", "rebuild-frontend", "rebuild-all", "hold",
]

// ── Main component ──────────────────────────────────────────────────────

export function DeployJudgePanel({
  projectId, deployStatus, deployError, initialPreferences, onProjectChanged,
}: Props) {
  const [judge, setJudge] = useState<JudgeResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [actionBusy, setActionBusy] = useState<DeployAction | "apply" | "preferences" | null>(null)
  const [err, setErr] = useState("")
  const [showAllFiles, setShowAllFiles] = useState(false)
  const [showPrefs, setShowPrefs] = useState(false)
  const [prefs, setPrefs] = useState(initialPreferences)
  // Track if we've shown the override hint this session so we don't
  // re-pop it on every poll.
  const overrideHintSeen = useRef(false)

  const isInFlight = deployStatus === "pending_deploy" || deployStatus === "deploying"
  const isFailed = deployStatus === "failed"

  // ── Fetcher ────────────────────────────────────────────────────────
  const fetchJudge = useCallback(async () => {
    try {
      const res = await api.get<{ data: JudgeResponse }>(`/projects/${projectId}/deploy/judge`)
      setJudge(res.data)
      setErr("")
    } catch (e: any) {
      // Soft-fail — the panel should never crash the project page.
      setErr(parseErr(e))
    } finally {
      setLoading(false)
    }
  }, [projectId])

  // Initial fetch + polling cadence based on deploy_status.
  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      if (cancelled) return
      // Don't re-fetch the judge while a deploy is in progress; the
      // decision is locked in. Parent's project poll handles the
      // pending → deploying → running transition.
      if (!isInFlight) await fetchJudge()
    }
    void tick()
    const interval = isInFlight ? 3000 : 10000
    const id = window.setInterval(tick, interval)
    return () => { cancelled = true; window.clearInterval(id) }
  }, [fetchJudge, isInFlight])

  // ── Actions ────────────────────────────────────────────────────────
  const apply = useCallback(async () => {
    if (!judge?.decision) return
    setActionBusy("apply")
    setErr("")
    try {
      await api.post(`/projects/${projectId}/deploy/judge/apply`, {
        decision_id: judge.decision.decision_id,
      })
      onProjectChanged()
      await fetchJudge()
    } catch (e: any) {
      setErr(parseErr(e))
    } finally {
      setActionBusy(null)
    }
  }, [judge, projectId, fetchJudge, onProjectChanged])

  const override = useCallback(async (action: DeployAction) => {
    if (!judge?.decision) return
    setActionBusy(action)
    setErr("")
    try {
      await api.post(`/projects/${projectId}/deploy/judge/override`, {
        action,
        decision_id: judge.decision.decision_id,
      })
      onProjectChanged()
      await fetchJudge()
    } catch (e: any) {
      setErr(parseErr(e))
    } finally {
      setActionBusy(null)
    }
  }, [judge, projectId, fetchJudge, onProjectChanged])

  const savePrefs = useCallback(async () => {
    setActionBusy("preferences")
    setErr("")
    try {
      await api.put(`/projects/${projectId}/deploy/judge/preferences`, {
        preferences: prefs,
      })
      setShowPrefs(false)
      onProjectChanged()
    } catch (e: any) {
      setErr(parseErr(e))
    } finally {
      setActionBusy(null)
    }
  }, [projectId, prefs, onProjectChanged])

  // ── Override-learning hint (Phase 8 surface) ────────────────────────
  // If the user has overridden 2 of the last 3 recommendations, surface
  // a subtle nudge to write down their preferences. We don't ship this
  // count from the backend yet; for v1 we read it from the visible
  // decision's `from_llm` field as a proxy (if the judge is being
  // bypassed often the recommendations themselves drift). Phase 8 will
  // ship a proper recent-overrides endpoint and we'll wire it here.
  const showOverrideHint = useMemo(() => {
    if (!judge?.decision) return false
    if (overrideHintSeen.current) return false
    if (judge.decision.status === "overridden") {
      overrideHintSeen.current = true
      return !prefs.trim()
    }
    return false
  }, [judge, prefs])

  // ── Render ─────────────────────────────────────────────────────────

  // State 5: Thinking (initial load only)
  if (loading && !judge) {
    return <StateThinking />
  }

  // State 6: Deploy in progress
  if (isInFlight) {
    return (
      <StateApplying
        action={(judge?.decision?.action as DeployAction | undefined) ?? null}
        status={deployStatus}
        commitSha={judge?.drift?.to_commit_sha ?? null}
      />
    )
  }

  // State 7: Deploy failed — surface the error and offer recovery
  if (isFailed) {
    return (
      <StateFailed
        deployError={deployError}
        commitSha={judge?.drift?.to_commit_sha ?? null}
        actionBusy={actionBusy}
        onRetry={() => apply()}
        onRebuild={() => override("rebuild-all")}
        onHold={() => override("hold")}
        canRetry={!!judge?.decision}
      />
    )
  }

  // State 1: No drift
  if (!judge?.drift?.has_drift) {
    return (
      <StateUpToDate
        lastCommit={judge?.drift?.from_commit_sha ?? null}
      />
    )
  }

  // States 2-4: drift present, judge has a recommendation
  const dec = judge.decision
  const drift = judge.drift
  if (!dec) {
    // Shouldn't normally happen — judge always returns SOMETHING when
    // drift exists. Defensive fallback.
    return (
      <StateThinking note="Judge result missing — refreshing…" />
    )
  }

  return (
    <div style={panelOuter}>
      <PanelHeader
        commitCount={drift.commit_count}
        risk={dec.risk}
        confidence={dec.confidence}
        fromLlm={dec.from_llm}
        overLimit={drift.over_limit}
        onRefresh={() => { setLoading(true); fetchJudge() }}
        onTogglePrefs={() => setShowPrefs((v) => !v)}
      />

      <CommitsList
        commits={drift.commits}
        filesTouched={drift.files_touched}
        expanded={showAllFiles}
        onToggle={() => setShowAllFiles((v) => !v)}
      />

      <RecommendationBlock decision={dec} />

      {showOverrideHint && (
        <OverrideHint onOpenPrefs={() => setShowPrefs(true)} />
      )}

      <ActionRow
        decision={dec}
        actionBusy={actionBusy}
        onApply={apply}
        onOverride={override}
      />

      {err && <ErrorBanner msg={err} />}

      {showPrefs && (
        <PreferencesPanel
          value={prefs}
          onChange={setPrefs}
          onSave={savePrefs}
          onCancel={() => { setPrefs(initialPreferences); setShowPrefs(false) }}
          busy={actionBusy === "preferences"}
        />
      )}
    </div>
  )
}

// ─────────────────────── State 1: Up to date ────────────────────────────

function StateUpToDate({ lastCommit }: { lastCommit: string | null }) {
  return (
    <div style={{
      ...panelOuter,
      borderStyle: "dashed",
      borderColor: "var(--border)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text-secondary)" }}>
        <CheckCircle2 size={14} color="var(--success)" />
        <span>Up to date</span>
        {lastCommit && (
          <>
            <span style={{ color: "var(--text-muted)" }}>·</span>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-muted)" }}>
              running {lastCommit.slice(0, 8)}
            </span>
          </>
        )}
        <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--text-muted)" }}>
          AI Deploy Judge will appear here when new commits land.
        </span>
      </div>
    </div>
  )
}

// ─────────────────────── State 5: Thinking ──────────────────────────────

function StateThinking({ note }: { note?: string }) {
  return (
    <div style={panelOuter}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text-muted)" }}>
        <Loader2 size={14} className="spin" />
        <span>{note ?? "Analyzing change set…"}</span>
      </div>
    </div>
  )
}

// ─────────────────────── State 6: Applying ──────────────────────────────

function StateApplying({
  action, status, commitSha,
}: { action: DeployAction | null; status: string | null; commitSha: string | null }) {
  return (
    <div style={{ ...panelOuter, borderColor: "var(--accent)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text-primary)" }}>
        <Loader2 size={14} className="spin" color="var(--accent)" />
        <span>
          {status === "pending_deploy" ? "Queued —" : "Applying"} {action && (
            <strong>{ACTION_LABEL[action]}</strong>
          )}
          {commitSha && (
            <>
              <span style={{ color: "var(--text-muted)" }}> · </span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
                {commitSha.slice(0, 8)}
              </span>
            </>
          )}
        </span>
      </div>
    </div>
  )
}

// ─────────────────────── State 7: Failed ────────────────────────────────

function StateFailed({
  deployError, commitSha, actionBusy, onRetry, onRebuild, onHold, canRetry,
}: {
  deployError: string | null
  commitSha: string | null
  actionBusy: any
  onRetry: () => void
  onRebuild: () => void
  onHold: () => void
  canRetry: boolean
}) {
  return (
    <div style={{ ...panelOuter, borderColor: "var(--danger)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <AlertTriangle size={14} color="var(--danger)" />
        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--danger)" }}>
          Last deploy failed
        </span>
        {commitSha && (
          <span style={{ marginLeft: "auto", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-muted)" }}>
            {commitSha.slice(0, 8)}
          </span>
        )}
      </div>
      {deployError && (
        <pre style={{
          marginTop: 8, padding: 8, fontSize: 11,
          background: "color-mix(in srgb, var(--danger) 8%, transparent)",
          border: "1px solid var(--danger)",
          borderRadius: 3,
          color: "var(--danger)",
          fontFamily: "var(--font-mono)",
          whiteSpace: "pre-wrap", maxHeight: 160, overflow: "auto",
        }}>{deployError}</pre>
      )}
      <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
        {canRetry && (
          <button type="button" onClick={onRetry} disabled={actionBusy === "apply"}
            style={primaryBtn(actionBusy === "apply")}>
            <RefreshCw size={12} />
            <span>{actionBusy === "apply" ? "Re-applying…" : "Re-apply"}</span>
          </button>
        )}
        <button type="button" onClick={onRebuild} disabled={actionBusy === "rebuild-all"}
          style={secondaryBtn(actionBusy === "rebuild-all")}>
          <span>{actionBusy === "rebuild-all" ? "Rebuilding…" : "Rebuild all"}</span>
        </button>
        <button type="button" onClick={onHold} disabled={actionBusy === "hold"}
          style={secondaryBtn(actionBusy === "hold")}>
          <span>Hold</span>
        </button>
      </div>
    </div>
  )
}

// ─────────────────────── Panel sub-blocks ───────────────────────────────

function PanelHeader({
  commitCount, risk, confidence, fromLlm, overLimit, onRefresh, onTogglePrefs,
}: {
  commitCount: number
  risk: DeployRisk
  confidence: DeployRisk
  fromLlm: boolean
  overLimit: boolean
  onRefresh: () => void
  onTogglePrefs: () => void
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
      <Sparkles size={14} color="var(--accent)" />
      <span style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>
        AI Deploy Judge
      </span>
      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>·</span>
      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
        {commitCount} commit{commitCount === 1 ? "" : "s"} since last deploy
        {overLimit && " (cap hit)"}
      </span>
      <span style={{ marginLeft: "auto", display: "inline-flex", gap: 6, alignItems: "center" }}>
        <RiskChip label="risk" value={risk} />
        <RiskChip label="confidence" value={confidence} />
        {!fromLlm && (
          <span title="Judge LLM unavailable — fell back to safe default"
            style={fallbackBadge}>default</span>
        )}
        <button type="button" onClick={onRefresh}
          title="Re-run judge"
          style={iconBtn()}>
          <RefreshCw size={12} />
        </button>
        <button type="button" onClick={onTogglePrefs}
          title="Edit judge preferences for this project"
          style={iconBtn()}>
          <Settings2 size={12} />
        </button>
      </span>
    </div>
  )
}

function RiskChip({ label, value }: { label: string; value: DeployRisk }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      padding: "1px 6px", borderRadius: 3, fontSize: 10,
      background: "color-mix(in srgb, " + RISK_COLOR[value] + " 12%, transparent)",
      color: RISK_COLOR[value],
      border: "1px solid " + RISK_COLOR[value],
      fontFamily: "var(--font-mono)",
    }}>
      {label}: <strong>{value}</strong>
    </span>
  )
}

function CommitsList({
  commits, filesTouched, expanded, onToggle,
}: {
  commits: DriftCommit[]
  filesTouched: string[]
  expanded: boolean
  onToggle: () => void
}) {
  const previewCount = expanded ? commits.length : Math.min(3, commits.length)
  return (
    <div style={{ marginTop: 4, marginBottom: 8 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {commits.slice(0, previewCount).map((c) => (
          <div key={c.commit_sha + c.request_id} style={{
            display: "flex", alignItems: "center", gap: 6, fontSize: 11,
            color: "var(--text-secondary)",
          }}>
            <GitCommit size={11} color="var(--text-muted)" style={{ flexShrink: 0 }} />
            <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
              {c.commit_sha.slice(0, 8)}
            </span>
            <span style={{
              flex: 1, minWidth: 0,
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              color: "var(--text-primary)",
            }} title={c.description}>
              {c.description || "(no description)"}
            </span>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-muted)" }}>
              {c.file_count} file{c.file_count === 1 ? "" : "s"}
            </span>
          </div>
        ))}
      </div>
      {commits.length > 3 && (
        <button type="button" onClick={onToggle}
          style={{
            marginTop: 4, padding: 0, fontSize: 10,
            background: "transparent", border: "none",
            color: "var(--accent)", cursor: "pointer",
            fontFamily: "var(--font)",
          }}>
          {expanded ? "Show fewer" : `Show all ${commits.length} commits`}
        </button>
      )}
      {expanded && filesTouched.length > 0 && (
        <div style={{
          marginTop: 6, padding: "6px 8px",
          background: "var(--bg-hover)", borderRadius: 3,
          fontSize: 10, color: "var(--text-muted)",
          fontFamily: "var(--font-mono)", maxHeight: 100, overflow: "auto",
        }}>
          <div style={{ marginBottom: 4, fontWeight: 600, color: "var(--text-secondary)" }}>
            <FileText size={10} style={{ display: "inline", verticalAlign: "middle", marginRight: 4 }} />
            {filesTouched.length} files touched
          </div>
          {filesTouched.map((f) => <div key={f}>{f}</div>)}
        </div>
      )}
    </div>
  )
}

function RecommendationBlock({ decision }: { decision: DeployDecision }) {
  return (
    <div style={{
      padding: "8px 10px", borderRadius: 3,
      background: "color-mix(in srgb, var(--accent) 6%, transparent)",
      border: "1px solid color-mix(in srgb, var(--accent) 25%, transparent)",
      marginBottom: 8,
    }}>
      <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: 0.5, fontWeight: 600 }}>
        Recommendation
      </div>
      <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)", marginTop: 2 }}>
        {ACTION_LABEL[decision.action]}
      </div>
      <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5, marginTop: 6 }}>
        {decision.reasoning || "(no reasoning provided)"}
      </div>
    </div>
  )
}

function ActionRow({
  decision, actionBusy, onApply, onOverride,
}: {
  decision: DeployDecision
  actionBusy: any
  onApply: () => void
  onOverride: (action: DeployAction) => void
}) {
  // For low-risk + high-confidence, the recommended button is the
  // primary CTA (gently pulses to draw the eye). Otherwise, all the
  // buttons are equal weight so the user picks deliberately.
  const isLowFriction =
    decision.risk === "low" && decision.confidence === "high"
  const isHold = decision.action === "hold"

  // Hold cannot be Applied (semantically: "do nothing"); user must
  // pick a different action via override. Apply is hidden in that case.
  return (
    <div style={{
      display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center",
    }}>
      {!isHold && (
        <button type="button" onClick={onApply}
          disabled={actionBusy === "apply"}
          className={isLowFriction ? "djp-pulse" : undefined}
          style={primaryBtn(actionBusy === "apply")}>
          <span>
            {actionBusy === "apply" ? "Applying…" : `Apply: ${ACTION_LABEL[decision.action]}`}
          </span>
        </button>
      )}
      {ALL_ACTIONS.filter((a) => a !== decision.action).map((a) => (
        <button key={a} type="button" onClick={() => onOverride(a)}
          disabled={actionBusy === a}
          style={secondaryBtn(actionBusy === a)}
          title={`Override — ${ACTION_LABEL[a]}`}>
          <span>{actionBusy === a ? "…" : ACTION_LABEL[a]}</span>
        </button>
      ))}
      <style>{`
        @keyframes djp-pulse-kf {
          0%, 100% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--accent) 60%, transparent); }
          50%      { box-shadow: 0 0 0 6px transparent; }
        }
        .djp-pulse { animation: djp-pulse-kf 1.6s ease-in-out infinite; }
        @keyframes djp-spin { to { transform: rotate(360deg); } }
        .spin { animation: djp-spin 0.9s linear infinite; }
      `}</style>
    </div>
  )
}

function OverrideHint({ onOpenPrefs }: { onOpenPrefs: () => void }) {
  return (
    <div style={{
      padding: "6px 8px", marginBottom: 8,
      background: "color-mix(in srgb, var(--warning, #f59e0b) 10%, transparent)",
      border: "1px solid var(--warning, #f59e0b)",
      borderRadius: 3, fontSize: 11, color: "var(--text-secondary)",
    }}>
      You overrode this recommendation. Tell the judge what to do differently next time —{" "}
      <button type="button" onClick={onOpenPrefs}
        style={{
          background: "transparent", border: "none", padding: 0,
          color: "var(--accent)", cursor: "pointer", textDecoration: "underline",
          fontFamily: "var(--font)", fontSize: 11,
        }}>
        edit preferences
      </button>
      .
    </div>
  )
}

function PreferencesPanel({
  value, onChange, onSave, onCancel, busy,
}: {
  value: string
  onChange: (v: string) => void
  onSave: () => void
  onCancel: () => void
  busy: boolean
}) {
  return (
    <div style={{
      marginTop: 8, padding: 8,
      background: "var(--bg-hover)",
      border: "1px solid var(--border)",
      borderRadius: 3,
    }}>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 4, fontWeight: 600 }}>
        Judge preferences (fed into every future recommendation)
      </div>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value.slice(0, 2000))}
        placeholder={"e.g. 'Treat any change in src/state/* as rebuild-backend' or " +
          "'For this project, tests are slow — prefer restart over rebuild when possible.'"}
        rows={4}
        style={{
          width: "100%", padding: 6, fontSize: 12,
          fontFamily: "var(--font-mono)",
          background: "var(--bg-card)",
          color: "var(--text-primary)",
          border: "1px solid var(--border)",
          borderRadius: 3, resize: "vertical", boxSizing: "border-box",
        }}
      />
      <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
        <button type="button" onClick={onSave} disabled={busy} style={primaryBtn(busy)}>
          <span>{busy ? "Saving…" : "Save preferences"}</span>
        </button>
        <button type="button" onClick={onCancel} style={secondaryBtn(false)}>
          <span>Cancel</span>
        </button>
        <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--text-muted)" }}>
          {value.length}/2000
        </span>
      </div>
    </div>
  )
}

function ErrorBanner({ msg }: { msg: string }) {
  return (
    <div style={{
      marginTop: 8, padding: "6px 8px",
      background: "color-mix(in srgb, var(--danger) 8%, transparent)",
      border: "1px solid var(--danger)",
      borderRadius: 3, fontSize: 11, color: "var(--danger)",
    }}>
      {msg}
    </div>
  )
}

// ── Style primitives ──────────────────────────────────────────────────

const panelOuter: React.CSSProperties = {
  marginTop: 10,
  padding: 10,
  background: "var(--bg-hover)",
  border: "1px solid var(--border)",
  borderRadius: 3,
}

const fallbackBadge: React.CSSProperties = {
  fontSize: 10, padding: "1px 6px", borderRadius: 3,
  background: "var(--bg-hover)", color: "var(--text-muted)",
  border: "1px solid var(--border)",
  fontFamily: "var(--font-mono)",
}

function primaryBtn(disabled: boolean): React.CSSProperties {
  return {
    display: "inline-flex", alignItems: "center", gap: 4,
    padding: "4px 10px", fontSize: 11, fontWeight: 700,
    background: disabled ? "var(--bg-hover)" : "var(--accent)",
    color: disabled ? "var(--text-muted)" : "#0a0014",
    border: "1px solid " + (disabled ? "var(--border)" : "var(--accent)"),
    borderRadius: 3, cursor: disabled ? "wait" : "pointer",
    whiteSpace: "nowrap", fontFamily: "var(--font)",
  }
}

function secondaryBtn(disabled: boolean): React.CSSProperties {
  return {
    display: "inline-flex", alignItems: "center", gap: 4,
    padding: "4px 10px", fontSize: 11, fontWeight: 600,
    background: "transparent",
    color: disabled ? "var(--text-muted)" : "var(--text-secondary)",
    border: "1px solid " + (disabled ? "var(--border)" : "var(--border)"),
    borderRadius: 3, cursor: disabled ? "wait" : "pointer",
    whiteSpace: "nowrap", fontFamily: "var(--font)",
  }
}

function iconBtn(): React.CSSProperties {
  return {
    display: "inline-flex", alignItems: "center", justifyContent: "center",
    width: 22, height: 22, padding: 0,
    background: "transparent",
    color: "var(--text-muted)",
    border: "1px solid var(--border)",
    borderRadius: 3, cursor: "pointer",
  }
}

function parseErr(e: any): string {
  const raw = e?.message || String(e)
  const colon = raw.indexOf(":")
  if (colon < 0) return raw
  try {
    const body = JSON.parse(raw.slice(colon + 1).trim())
    const d = body?.detail
    if (typeof d === "string") return d
    if (typeof d === "object" && d) return d.hint || d.message || d.error || JSON.stringify(d)
  } catch { /* not JSON */ }
  return raw
}
