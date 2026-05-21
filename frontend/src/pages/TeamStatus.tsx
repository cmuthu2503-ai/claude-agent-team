import { useState, useEffect } from "react"
import { api } from "../lib/api"

const TEAMS = [
  { id: "planning", label: "Planning", color: "var(--accent)" },
  { id: "development", label: "Development", color: "var(--info, var(--accent))" },
  { id: "delivery", label: "Delivery", color: "var(--success)" },
  { id: "research", label: "Research", color: "var(--warning)" },
  { id: "content", label: "Content", color: "var(--info, var(--accent))" },
]

const modelBadge: Record<string, { bg: string; label: string }> = {
  "claude-opus-4-6": { bg: "var(--accent-subtle)", label: "Opus" },
  "claude-sonnet-4-6": { bg: "var(--success-subtle)", label: "Sonnet" },
}

export function TeamStatusPage() {
  const [agents, setAgents] = useState<any[]>([])

  // Auto-refresh every 5s. Previously this page only fetched on mount,
  // which meant the "IN PROGRESS" pills went stale the moment an agent
  // finished. With polling + the new animated indicators below, the
  // page now reflects live activity.
  useEffect(() => {
    let cancelled = false
    const load = () =>
      api.get("/agents")
        .then((res) => { if (!cancelled) setAgents(res.data) })
        .catch(() => {})
    load()
    const id = window.setInterval(load, 5000)
    return () => { cancelled = true; window.clearInterval(id) }
  }, [])

  const totalAgents = agents.length

  return (
    <div style={{ maxWidth: 1400, margin: "0 auto", padding: 24, fontFamily: "var(--font)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>
          Team Status
        </h1>
        <span style={{ fontSize: 13, color: "var(--text-muted)" }}>
          {totalAgents} agents across {TEAMS.filter((t) => agents.some((a) => a.team === t.id)).length} teams
        </span>
      </div>

      {/* Columnar layout — one column per team */}
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${TEAMS.length}, 1fr)`, gap: 16, alignItems: "start" }}>
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

              {/* Agent cards stacked vertically */}
              <div
                style={{
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border)",
                  borderTop: "none",
                  borderRadius: "0 0 var(--radius) var(--radius)",
                  padding: 8,
                  minHeight: 200,
                  display: "flex",
                  flexDirection: "column",
                  gap: 8,
                }}
              >
                {teamAgents.length === 0 && (
                  <div style={{ padding: 24, textAlign: "center", color: "var(--text-muted)", fontSize: 12 }}>
                    No agents
                  </div>
                )}
                {teamAgents.map((a) => {
                  const mb = modelBadge[a.model] || { bg: "var(--bg-hover)", label: a.model }
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
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <span
                          style={{
                            fontSize: 10,
                            fontWeight: 500,
                            padding: "2px 7px",
                            borderRadius: "var(--radius)",
                            background: mb.bg,
                            color: "var(--text-secondary)",
                          }}
                        >
                          {mb.label}
                        </span>
                        {a.current_task && (
                          <span style={{ fontSize: 10, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {a.current_task}
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
