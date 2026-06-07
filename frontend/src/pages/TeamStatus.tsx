import { useState, useEffect, useRef } from "react"
import { ModelSelector } from "../components/ui/ModelSelector"
import { useModelsStore } from "../stores/models"
import { useAuthStore } from "../stores/auth"

// AET-38 — AE event glyphs. Maps each AE-driven event type to a
// compact visual identity so the Active Agents feed can show
// "🛡 security_specialist → BLOCKED on B602" instead of generic
// "in_progress". One source of truth — frontend single file. If the
// backend renames an event type, the L23 mass-grep recipe catches
// this row at update time.
type AeEventStyle = {
  glyph: string
  agentId: string
  agentName: string
  toneVar: string  // CSS var for left border accent
  label: (data: any) => string
}
const AE_EVENT_STYLES: Record<string, AeEventStyle> = {
  "quality.gate.failed": {
    glyph: "🛑", agentId: "quality_guardian", agentName: "Quality Guardian",
    toneVar: "var(--danger)",
    label: (d) => {
      const v = d?.violations?.[0]
      const ruleId = v?.rule_id || "rule"
      const n = (d?.violations || []).length
      return `BLOCKED on ${ruleId}${n > 1 ? ` (+${n - 1} more)` : ""}`
    },
  },
  "quality.gate.passed": {
    glyph: "✅", agentId: "quality_guardian", agentName: "Quality Guardian",
    toneVar: "var(--success)",
    label: (d) => d?.summary?.message || `Gate cleared (${d?.verdict || "PASS"})`,
  },
  "security.gate.failed": {
    glyph: "🛡", agentId: "security_specialist", agentName: "Security Specialist",
    toneVar: "var(--danger)",
    label: (d) => {
      const first = d?.blocking?.[0]
      const ruleId = first ? `${first.tool || "?"}/${first.rule_id || "?"}` : "rule"
      return `BLOCKED ${ruleId} (${d?.blocking_count ?? 0} findings)`
    },
  },
  "security.gate.passed": {
    glyph: "🛡", agentId: "security_specialist", agentName: "Security Specialist",
    toneVar: "var(--success)",
    label: (d) => `Security clean (${d?.non_blocking_count ?? 0} sub-threshold)`,
  },
  "ops.alert.fired": {
    glyph: "⚠", agentId: "ops_heal_agent", agentName: "Ops Heal Agent",
    toneVar: "var(--warning)",
    label: (d) => `${d?.env || "?"} · ${d?.severity || "alert"} — ${d?.slo_summary?.slice(0, 60) || "see logs"}`,
  },
  "ops.rollback.triggered": {
    glyph: "⏪", agentId: "ops_heal_agent", agentName: "Ops Heal Agent",
    toneVar: "var(--danger)",
    label: (d) => `Rollback queued for ${d?.env || "?"} (${d?.request_id || "?"})`,
  },
  "lessons.added": {
    glyph: "📚", agentId: "self_learning_agent", agentName: "Self-Learning Agent",
    toneVar: "var(--accent)",
    label: (d) => `Lesson ${d?.lesson_id || "L?"} added`,
  },
  "lessons.pending_review": {
    glyph: "📝", agentId: "self_learning_agent", agentName: "Self-Learning Agent",
    toneVar: "var(--accent)",
    label: (d) => `Lesson queued for review (${d?.lesson_id || "L?"})`,
  },
  "lessons.duplicate_skipped": {
    glyph: "📚", agentId: "self_learning_agent", agentName: "Self-Learning Agent",
    toneVar: "var(--text-muted)",
    label: (d) => `Duplicate of ${d?.matched_lesson || "existing"} — skipped`,
  },
}
const AE_EVENT_TYPES = Object.keys(AE_EVENT_STYLES)

type AeEventEntry = {
  id: string  // composite for React key
  type: string
  data: any
  receivedAt: number  // epoch ms; events expire after AE_EVENT_TTL_MS
}
const AE_EVENT_TTL_MS = 60_000  // events linger 60s in the feed
const AE_EVENT_MAX = 10          // ring buffer cap

// Each team carries a `weight` that drives the outer grid's
// `grid-template-columns`. Development + Delivery have more agents
// (4-5 each vs Planning's 3 and Research/Content's 1), so they get
// 2× the horizontal space and lay their agent cards out in a nested
// 2-column grid. Light teams keep a single column. Net effect: no
// more empty vertical whitespace under Research / Content while
// Dev/Delivery overflow vertically.
const TEAMS = [
  { id: "planning",    label: "Planning",    color: "var(--accent)",                 weight: 1, cols: 1 },
  { id: "development", label: "Development", color: "var(--info, var(--accent))",    weight: 2, cols: 2 },
  { id: "delivery",    label: "Delivery",    color: "var(--success)",                weight: 2, cols: 2 },
  { id: "research",    label: "Research",    color: "var(--warning)",                weight: 1, cols: 1 },
  { id: "content",     label: "Content",     color: "var(--info, var(--accent))",    weight: 1, cols: 1 },
]

// PAM-19 — `modelBadge` removed; replaced by <ModelSelector>'s
// display_name + tier rendering, sourced from the live catalog
// (/api/v1/models) instead of a hardcoded 2-entry map that fell
// behind as new models were added.

export function TeamStatusPage() {
  // PAM-19 — agents + models now come from `useModelsStore` so the
  // override mutation routes (assignModel / clearOverride / resetAll)
  // can update the same source of truth optimistically. The page
  // still polls /agents every 5s as before — the only change is that
  // the loop now goes through `store.fetchAgents()` instead of a
  // local setState, so a successful PATCH stays put across polls.
  const agents = useModelsStore((s) => s.agents)
  const models = useModelsStore((s) => s.models)
  const error = useModelsStore((s) => s.error)
  const fetchAgents = useModelsStore((s) => s.fetchAgents)
  const fetchModels = useModelsStore((s) => s.fetchModels)
  const assignModel = useModelsStore((s) => s.assignModel)
  const clearOverride = useModelsStore((s) => s.clearOverride)
  const resetAll = useModelsStore((s) => s.resetAll)
  const clearError = useModelsStore((s) => s.clearError)

  // PAM-19 — admin gate. The backend's PATCH/DELETE routes refuse
  // non-admin tokens at the request layer; we use this here to render
  // the read-only chip variant of ModelSelector and to gate the
  // "Reset all" button so non-admins don't see an affordance they
  // can't actuate.
  const isAdmin = useAuthStore((s) => s.user?.role === "admin")

  // Inline status banner — toast lib (sonner) is in deps but not
  // mounted globally; a small two-state banner here is enough for
  // PAM-19 and keeps the page self-contained.
  const [flash, setFlash] = useState<{ kind: "success" | "error"; text: string } | null>(null)
  const [confirmReset, setConfirmReset] = useState(false)

  // AET-38 — ring buffer of recent AE events; rendered alongside live
  // in_progress agents in the Active Agents feed so the page shows
  // both "who's working" AND "what just happened" without leaving.
  const [aeEvents, setAeEvents] = useState<AeEventEntry[]>([])
  const wsRef = useRef<WebSocket | null>(null)

  // Auto-refresh every 5s. Previously this page only fetched on mount,
  // which meant the "IN PROGRESS" pills went stale the moment an agent
  // finished. With polling + the new animated indicators below, the
  // page now reflects live activity.
  useEffect(() => {
    let cancelled = false
    // Pull the model catalog once at mount — it doesn't change at
    // runtime (POST /models/reload is the only path that mutates it,
    // and operators who hit that path can refresh the page).
    fetchModels()
    const load = () => { if (!cancelled) fetchAgents() }
    load()
    const id = window.setInterval(load, 5000)
    return () => { cancelled = true; window.clearInterval(id) }
  }, [fetchModels, fetchAgents])

  // Surface store errors as flash messages, then clear so a re-error
  // re-fires the banner.
  useEffect(() => {
    if (!error) return
    setFlash({ kind: "error", text: error })
    clearError()
  }, [error, clearError])

  // Auto-dismiss success flashes after 3s; errors stay until the
  // next mutation to give the operator time to read them.
  useEffect(() => {
    if (flash?.kind !== "success") return
    const id = window.setTimeout(() => setFlash(null), 3000)
    return () => window.clearTimeout(id)
  }, [flash])

  // Handler wrappers — call the store action, then flash success
  // (errors surface via the effect above).
  const handleAssign = async (agentId: string, modelId: string) => {
    await assignModel(agentId, modelId)
    if (!useModelsStore.getState().error) {
      setFlash({ kind: "success", text: `Assigned ${modelId} to ${agentId}` })
    }
  }
  const handleReset = async (agentId: string) => {
    await clearOverride(agentId)
    if (!useModelsStore.getState().error) {
      setFlash({ kind: "success", text: `Cleared override on ${agentId}` })
    }
  }
  const handleResetAll = async () => {
    setConfirmReset(false)
    await resetAll()
    if (!useModelsStore.getState().error) {
      setFlash({ kind: "success", text: "All model overrides reset to defaults" })
    }
  }

  // AET-38 — subscribe to /ws/activity for AE event types. Maintains
  // a small ring buffer with TTL eviction so the feed doesn't grow
  // unbounded. The 5s polling above keeps the agent grid in sync;
  // this WS only feeds the recent-events strip.
  useEffect(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/activity`)
    wsRef.current = ws
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (!AE_EVENT_TYPES.includes(msg.type)) return
        const entry: AeEventEntry = {
          id: `${msg.type}-${msg.timestamp || Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          type: msg.type,
          data: msg.data || {},
          receivedAt: Date.now(),
        }
        setAeEvents((prev) => {
          // Newest first; cap at AE_EVENT_MAX.
          const next = [entry, ...prev].slice(0, AE_EVENT_MAX)
          return next
        })
      } catch {
        // Malformed WS frame; nothing to do.
      }
    }
    ws.onerror = () => { /* visible in browser console */ }
    return () => {
      ws.close()
      wsRef.current = null
    }
  }, [])

  // TTL eviction — once a second, drop events older than AE_EVENT_TTL_MS.
  // Cheap (in-memory filter) and keeps the strip from showing yesterday's
  // alerts after a tab has been left open.
  useEffect(() => {
    const id = window.setInterval(() => {
      setAeEvents((prev) => {
        const cutoff = Date.now() - AE_EVENT_TTL_MS
        const next = prev.filter((e) => e.receivedAt >= cutoff)
        return next.length === prev.length ? prev : next
      })
    }, 1000)
    return () => window.clearInterval(id)
  }, [])

  const totalAgents = agents.length
  // Live "active" subset drives both the per-card pulse animation
  // (existing) AND the new bottom-of-page activity feed (added below).
  // An agent is active when EITHER the workflow runner has an open
  // subtask for it OR a single_agent_call is in flight (busy map) —
  // both surfaced as `status === "in_progress"` by /agents.
  const activeAgents = agents.filter((a) => a.status === "in_progress")
  const gridTemplate = TEAMS.map((t) => `${t.weight}fr`).join(" ")

  return (
    // Widened from 1400 → 1800 so Dev/Delivery 2-col layouts have
    // enough horizontal room without crowding. On narrower screens
    // the inner grid wraps gracefully via auto-fit nested grids.
    <div style={{ maxWidth: 1800, margin: "0 auto", padding: 24, fontFamily: "var(--font)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>
          Team Status
        </h1>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <span style={{ fontSize: 13, color: "var(--text-muted)" }}>
            {totalAgents} agents across {TEAMS.filter((t) => agents.some((a) => a.team === t.id)).length} teams
            {activeAgents.length > 0 && (
              <span style={{ marginLeft: 10, color: "var(--accent)" }}>
                · {activeAgents.length} active
              </span>
            )}
          </span>
          {/* PAM-19 — admin-only "Reset all model assignments" button.
              Disabled when there's nothing to reset so an idle click
              doesn't fire a wasted DELETE. Two-click confirmation
              (click → confirm/cancel) so a misclick can't wipe the
              override state during an incident. */}
          {isAdmin && (
            agents.some((a) => a.override_active) ? (
              confirmReset ? (
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                    Reset {agents.filter((a) => a.override_active).length} override(s)?
                  </span>
                  <button
                    onClick={handleResetAll}
                    style={dangerBtnStyle}
                    title="Confirm reset"
                  >
                    Yes, reset
                  </button>
                  <button
                    onClick={() => setConfirmReset(false)}
                    style={secondaryBtnStyle}
                  >
                    Cancel
                  </button>
                </span>
              ) : (
                <button
                  onClick={() => setConfirmReset(true)}
                  style={secondaryBtnStyle}
                  title="Clear all per-agent model overrides; agents return to their YAML defaults"
                >
                  Reset all model assignments
                </button>
              )
            ) : (
              <span
                title="No overrides currently set"
                style={{ ...secondaryBtnStyle, opacity: 0.4, cursor: "default" }}
              >
                Reset all model assignments
              </span>
            )
          )}
        </div>
      </div>

      {/* PAM-19 — flash banner: surfaces success / error from the
          override mutations. Errors persist until the next mutation;
          success auto-dismisses after 3s. */}
      {flash && (
        <div
          role="status"
          onClick={() => setFlash(null)}
          style={{
            marginBottom: 16,
            padding: "10px 14px",
            borderRadius: "var(--radius)",
            background: flash.kind === "success"
              ? "var(--success-subtle, var(--accent-subtle))"
              : "var(--danger-subtle, var(--bg-hover))",
            color: flash.kind === "success"
              ? "var(--success, var(--accent))"
              : "var(--danger, #ef4444)",
            border: `1px solid ${flash.kind === "success"
              ? "var(--success, var(--accent))"
              : "var(--danger, #ef4444)"}`,
            fontSize: 12,
            cursor: "pointer",
          }}
        >
          {flash.text}
          <span style={{ float: "right", opacity: 0.6 }}>×</span>
        </div>
      )}

      {/* Columnar layout — asymmetric grid weighted by team size.
          Dev + Delivery get 2fr (their cards lay out in a nested 2-col
          grid); Planning + Research + Content stay at 1fr (single col). */}
      <div style={{ display: "grid", gridTemplateColumns: gridTemplate, gap: 16, alignItems: "start" }}>
        {TEAMS.map((team) => {
          const teamAgents = agents.filter((a) => a.team === team.id)
          return (
            <div key={team.id} style={{ display: "flex", flexDirection: "column", gap: 0 }}>
              {/* Column header */}
              <div
                style={{
                  background: "var(--bg-card)",
                  border: "1px solid var(--border)",
                  borderBottom: `3px solid ${team.color}`,
                  borderRadius: "var(--radius) var(--radius) 0 0",
                  padding: "14px 16px",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ width: 10, height: 10, borderRadius: "50%", background: team.color }} />
                    <span style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>
                      {team.label}
                    </span>
                  </div>
                  <span
                    style={{
                      fontSize: 11,
                      fontWeight: 600,
                      fontFamily: "var(--font-mono)",
                      padding: "2px 8px",
                      borderRadius: 999,
                      background: "var(--bg-hover)",
                      color: "var(--text-muted)",
                    }}
                  >
                    {teamAgents.length}
                  </span>
                </div>
              </div>

              {/* Agent cards — single column for light teams, 2-col
                  grid for Development / Delivery so wider teams fill
                  horizontally instead of overflowing vertically. */}
              <div
                style={{
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border)",
                  borderTop: "none",
                  borderRadius: "0 0 var(--radius) var(--radius)",
                  padding: 8,
                  minHeight: 200,
                  display: "grid",
                  gridTemplateColumns: team.cols === 2 ? "1fr 1fr" : "1fr",
                  gap: 8,
                  alignContent: "start",
                }}
              >
                {teamAgents.length === 0 && (
                  <div style={{
                    padding: 24, textAlign: "center", color: "var(--text-muted)", fontSize: 12,
                    gridColumn: team.cols === 2 ? "1 / -1" : undefined,
                  }}>
                    No agents
                  </div>
                )}
                {teamAgents.map((a) => {
                  return (
                    <div
                      key={a.agent_id}
                      style={{
                        background: "var(--bg-card)",
                        border: "1px solid var(--border)",
                        borderRadius: "var(--radius)",
                        padding: "12px 14px",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                          {a.display_name}
                        </span>
                        <AgentStateIndicator active={a.status === "in_progress"} accent={team.color} />
                      </div>
                      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 8 }}>
                        {a.role}
                      </div>
                      {/* Just the model badge — activity label moved to the
                          dedicated Active Agents feed below the team grid so
                          the per-team cards stay compact and grid-aligned.
                          AET-37 — SCAFFOLD chip when total_subtasks=0 so the
                          page can't project "all N working" when most agents
                          have never actually run. Dashed border + muted color
                          differentiates from the (filled) model badge without
                          competing for attention. */}
                      <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                        {/* PAM-19 — ModelSelector replaces the static
                            badge. Renders read-only chip for viewers,
                            interactive button + portal dropdown for
                            admins. The store-driven optimistic update
                            means the new model is visible immediately,
                            even before the next 5s poll. */}
                        <ModelSelector
                          agentId={a.agent_id}
                          currentModelId={a.assigned_model || a.model}
                          defaultModelId={a.default_model || a.model}
                          overrideActive={!!a.override_active}
                          models={models}
                          isAdmin={isAdmin}
                          onChange={handleAssign}
                          onReset={handleReset}
                        />
                        {(a.total_subtasks ?? 0) === 0 && (
                          <span
                            title="This agent is configured but has never executed a subtask in this database. UI surface only — no workflow has invoked it yet."
                            style={{
                              fontSize: 9,
                              fontWeight: 600,
                              letterSpacing: 0.5,
                              padding: "1px 6px",
                              borderRadius: "var(--radius)",
                              border: "1px dashed var(--text-muted)",
                              color: "var(--text-muted)",
                              background: "transparent",
                              fontFamily: "var(--font-mono)",
                              textTransform: "uppercase",
                            }}
                          >
                            scaffold
                          </span>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>

      {/* ─────────────────── Active Agents Feed ───────────────────
          Shows the live subset of agents currently working — sourced
          from the same /agents endpoint that drives the cards above,
          filtered to status==="in_progress". Each card surfaces:
            - agent name + team color
            - current activity (request_id from workflow runner OR
              the single_agent_call label like "Generating API spec")
            - elapsed seconds for label-driven calls
            - pulse animation so the feed reads as alive
          Empty state shows a clear "all idle" placeholder so the
          panel doesn't collapse and shift the page layout. */}
      <div style={{ marginTop: 32 }}>
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          marginBottom: 14,
        }}>
          <h2 style={{
            fontSize: 15, fontWeight: 700, color: "var(--text-primary)", margin: 0,
            display: "inline-flex", alignItems: "center", gap: 10,
          }}>
            <span style={{
              width: 8, height: 8, borderRadius: "50%",
              background: activeAgents.length > 0 ? "var(--accent)" : "var(--text-muted)",
              boxShadow: activeAgents.length > 0
                ? "0 0 12px var(--accent), 0 0 4px var(--accent)"
                : undefined,
              animation: activeAgents.length > 0
                ? "ts-feed-pulse 1.4s ease-in-out infinite"
                : undefined,
            }} />
            Active Agents
          </h2>
          <span style={{ fontSize: 12, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
            {activeAgents.length === 0
              ? "all idle"
              : `${activeAgents.length} working`}
          </span>
        </div>

        {/* AET-38 — Recent AE events strip. Sits above the live
            agents feed so an alert / rollback / gate decision is
            visible alongside ongoing work. TTL eviction (60s) keeps
            this list short; ring buffer cap (10) prevents an alert
            storm from pushing everything else off the page. */}
        {aeEvents.length > 0 && (
          <div
            data-testid="ae-events-feed"
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 6,
              marginBottom: 12,
            }}
          >
            {aeEvents.map((e) => {
              const style = AE_EVENT_STYLES[e.type]
              if (!style) return null
              const ageS = Math.max(0, Math.floor((Date.now() - e.receivedAt) / 1000))
              return (
                <div
                  key={e.id}
                  className="ts-ae-event"
                  data-event-type={e.type}
                  title={`${e.type} · ${ageS}s ago · ${JSON.stringify(e.data).slice(0, 200)}`}
                  style={{
                    background: "var(--bg-card)",
                    border: "1px solid var(--border)",
                    borderLeft: `3px solid ${style.toneVar}`,
                    borderRadius: "var(--radius)",
                    padding: "8px 12px",
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    fontSize: 12,
                  }}
                >
                  <span
                    aria-hidden="true"
                    style={{
                      fontSize: 14,
                      width: 18,
                      textAlign: "center",
                      lineHeight: 1,
                    }}
                  >
                    {style.glyph}
                  </span>
                  <span style={{
                    fontWeight: 600,
                    color: "var(--text-primary)",
                    whiteSpace: "nowrap",
                  }}>
                    {style.agentName}
                  </span>
                  <span style={{
                    color: style.toneVar,
                    fontFamily: "var(--font-mono)",
                    fontSize: 13,
                    fontWeight: 700,
                    opacity: 0.9,
                  }} aria-hidden="true">
                    →
                  </span>
                  <span style={{
                    flex: 1,
                    color: "var(--text-primary)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}>
                    {style.label(e.data)}
                  </span>
                  <span style={{
                    color: "var(--text-muted)",
                    fontFamily: "var(--font-mono)",
                    fontSize: 11,
                    minWidth: 32,
                    textAlign: "right",
                  }}>
                    {ageS}s
                  </span>
                </div>
              )
            })}
          </div>
        )}

        {activeAgents.length === 0 && aeEvents.length === 0 ? (
          <div style={{
            background: "var(--bg-card)",
            border: "1px dashed var(--border)",
            borderRadius: "var(--radius)",
            padding: "28px 20px",
            textAlign: "center",
            color: "var(--text-muted)",
            fontSize: 13,
          }}>
            No active work right now. Trigger a generation pass or dispatch a task to see live agent activity here.
          </div>
        ) : activeAgents.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--text-muted)", textAlign: "center", padding: "8px 0" }}>
            No live agents — recent AE events shown above.
          </div>
        ) : (
          <div style={{
            // Single-column vertical stack. Each row is a one-line
            // `<Name> → "<activity>" · 27s` entry — reads like a live
            // activity log, scannable top-to-bottom even with many
            // concurrent agents.
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}>
            {activeAgents.map((a) => {
              const team = TEAMS.find((t) => t.id === a.team)
              const accent = team?.color || "var(--accent)"
              const activity = a.current_label || a.current_task || "Working"
              const elapsed = a.elapsed_seconds != null ? `${a.elapsed_seconds}s` : null
              return (
                // Single-line `<AgentName> → "<activity>" · <elapsed>` row.
                // Compact 3-part shape per user spec:
                //   [pulse dot] [Agent Name]  →  "activity quote"  · 27s
                // Reads top-to-bottom like a live activity log instead of
                // a card grid; lets you eyeball "who is doing what" without
                // parsing role / team / model chrome.
                <div
                  key={a.agent_id}
                  className="ts-active-card"
                  style={{
                    position: "relative",
                    background: "var(--bg-card)",
                    border: "1px solid var(--border)",
                    borderLeft: `3px solid ${accent}`,
                    borderRadius: "var(--radius)",
                    padding: "10px 14px",
                    overflow: "hidden",
                    display: "flex", alignItems: "center", gap: 10,
                    ["--ts-c" as any]: accent,
                  }}
                  title={`${a.display_name} → "${activity}"${elapsed ? " · " + elapsed : ""}`}
                >
                  <div className="ts-active-scan" aria-hidden="true" />
                  <AgentStateIndicator active={true} accent={accent} />
                  <span style={{
                    position: "relative", zIndex: 1,
                    fontSize: 13, fontWeight: 600, color: "var(--text-primary)",
                    whiteSpace: "nowrap",
                  }}>
                    {a.display_name}
                  </span>
                  <span style={{
                    position: "relative", zIndex: 1,
                    color: accent, fontFamily: "var(--font-mono)", fontSize: 14,
                    fontWeight: 700, opacity: 0.9,
                  }} aria-hidden="true">
                    →
                  </span>
                  <span style={{
                    position: "relative", zIndex: 1,
                    flex: 1, fontSize: 12, color: "var(--text-primary)",
                    fontStyle: "italic",
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  }}>
                    “{activity}”
                  </span>
                  {elapsed && (
                    <span style={{
                      position: "relative", zIndex: 1,
                      fontFamily: "var(--font-mono)",
                      color: accent,
                      fontSize: 11,
                      whiteSpace: "nowrap",
                    }}>
                      {elapsed}
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Keyframes for the animated agent indicator — scoped via the
          `ts-agent-` class prefix so they don't collide with anything else. */}
      <style>{`
        @keyframes ts-agent-pulse-ring {
          0%   { transform: scale(0.55); opacity: 0.85; }
          80%  { transform: scale(1.6);  opacity: 0;    }
          100% { transform: scale(1.6);  opacity: 0;    }
        }
        @keyframes ts-agent-breath {
          0%, 100% { transform: scale(1);     filter: brightness(1);   }
          50%      { transform: scale(1.18);  filter: brightness(1.6); }
        }
        @keyframes ts-agent-spin {
          to { transform: rotate(360deg); }
        }
        @keyframes ts-agent-glow {
          0%, 100% { box-shadow: 0 0 6px var(--ts-c, currentColor),
                                 0 0 14px var(--ts-c, currentColor); }
          50%      { box-shadow: 0 0 10px var(--ts-c, currentColor),
                                 0 0 22px var(--ts-c, currentColor); }
        }
        @keyframes ts-agent-sweep {
          to { transform: rotate(360deg); }
        }

        /* Active-feed panel animations */
        @keyframes ts-feed-pulse {
          0%, 100% { transform: scale(1);    opacity: 0.85; }
          50%      { transform: scale(1.35); opacity: 1; }
        }
        @keyframes ts-active-scan {
          0%   { transform: translateX(-100%); opacity: 0; }
          20%  { opacity: 1; }
          80%  { opacity: 1; }
          100% { transform: translateX(100%);  opacity: 0; }
        }
        .ts-active-scan {
          position: absolute; inset: 0; pointer-events: none;
          overflow: hidden; z-index: 0;
        }
        .ts-active-scan::before {
          content: "";
          position: absolute; top: 0; bottom: 0; left: 0;
          width: 35%;
          background: linear-gradient(
            90deg,
            transparent 0%,
            color-mix(in srgb, var(--ts-c, var(--accent)) 18%, transparent) 50%,
            transparent 100%
          );
          filter: blur(3px);
          animation: ts-active-scan 2.6s linear infinite;
        }
        .ts-active-card {
          transition: box-shadow 0.3s ease;
        }
        .ts-active-card:hover {
          box-shadow: 0 0 14px color-mix(in srgb, var(--ts-c, var(--accent)) 35%, transparent);
        }

        @media (prefers-reduced-motion: reduce) {
          .ts-active-scan::before { animation: none; }
        }
      `}</style>
    </div>
  )
}

// ── Agent state indicator ───────────────────────────────────────────────
// Replaces the old "IDLE" / "IN PROGRESS" text badges. Active agents get
// a stack of layered CSS animations (pulse rings + rotating sweep + a
// breathing core dot + glow) so the page reads "live" at a glance. Idle
// agents get a static dimmed dot — no animation, no movement. Color is
// driven by the team's accent so each column stays visually coherent.

function AgentStateIndicator({ active, accent }: { active: boolean; accent: string }) {
  const SIZE = 28
  if (!active) {
    // ── Idle: static dim ring + center dot, no animations ──
    return (
      <span
        title="Idle"
        aria-label="Idle"
        style={{
          position: "relative",
          display: "inline-block",
          width: SIZE, height: SIZE,
          flexShrink: 0,
        }}
      >
        <span
          aria-hidden
          style={{
            position: "absolute", inset: 4,
            borderRadius: "50%",
            border: "1px dashed var(--border)",
            opacity: 0.7,
          }}
        />
        <span
          aria-hidden
          style={{
            position: "absolute",
            top: "50%", left: "50%",
            width: 6, height: 6,
            transform: "translate(-50%, -50%)",
            borderRadius: "50%",
            background: "var(--text-muted)",
            opacity: 0.55,
          }}
        />
      </span>
    )
  }

  // ── Active: layered animations driven off the team accent color ──
  // The `--ts-c` custom prop is consumed by the @keyframes above so the
  // glow stays in sync with the team color (planning=cyan,
  // delivery=green, research=yellow, etc.).
  return (
    <span
      title="Active"
      aria-label="Active"
      style={{
        position: "relative",
        display: "inline-block",
        width: SIZE, height: SIZE,
        ["--ts-c" as any]: accent,
        color: accent,
        flexShrink: 0,
      }}
    >
      {/* Layer 1: two pulse rings, offset by half the period so the
          expansion is continuous, never empty. */}
      <span
        aria-hidden
        style={{
          position: "absolute", inset: 0,
          borderRadius: "50%",
          border: `1.5px solid ${accent}`,
          transformOrigin: "center",
          animation: "ts-agent-pulse-ring 1.6s ease-out infinite",
        }}
      />
      <span
        aria-hidden
        style={{
          position: "absolute", inset: 0,
          borderRadius: "50%",
          border: `1.5px solid ${accent}`,
          transformOrigin: "center",
          animation: "ts-agent-pulse-ring 1.6s ease-out infinite",
          animationDelay: "0.8s",
        }}
      />
      {/* Layer 2: rotating conic-gradient sweep — gives a "scanning"
          feel and a continuous motion baseline. */}
      <span
        aria-hidden
        style={{
          position: "absolute", inset: 3,
          borderRadius: "50%",
          background: `conic-gradient(from 0deg,
                       transparent 0deg,
                       transparent 270deg,
                       ${accent} 360deg)`,
          opacity: 0.55,
          animation: "ts-agent-sweep 2.4s linear infinite",
          // Cut the inner disc out so it reads as a ring not a pie.
          WebkitMask: "radial-gradient(circle, transparent 45%, #000 47%)",
          mask: "radial-gradient(circle, transparent 45%, #000 47%)",
        }}
      />
      {/* Layer 3: the breathing core dot — scale + brightness oscillation
          + a layered glow that pulses on its own schedule. */}
      <span
        aria-hidden
        style={{
          position: "absolute",
          top: "50%", left: "50%",
          width: 8, height: 8,
          transform: "translate(-50%, -50%)",
          borderRadius: "50%",
          background: accent,
          animation:
            "ts-agent-breath 1.2s ease-in-out infinite, " +
            "ts-agent-glow 1.6s ease-in-out infinite",
        }}
      />
    </span>
  )
}

// ── PAM-19 — header button styles (kept inline so the file stays
// self-contained with the rest of the page's styling pattern) ─────────

const secondaryBtnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  padding: "5px 10px",
  background: "var(--bg-hover)",
  color: "var(--text-secondary)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  fontSize: 11,
  fontFamily: "var(--font)",
  cursor: "pointer",
  whiteSpace: "nowrap",
}

const dangerBtnStyle: React.CSSProperties = {
  ...secondaryBtnStyle,
  background: "var(--danger, #ef4444)",
  color: "#fff",
  border: "1px solid var(--danger, #ef4444)",
  fontWeight: 600,
}
