import { useState, useEffect } from "react"
import { api } from "../lib/api"
import { StatusBadge } from "../components/ui/StatusBadge"

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

  useEffect(() => {
    api.get("/agents").then((res) => setAgents(res.data)).catch(() => {})
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
                        <StatusBadge status={a.status} />
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
    </div>
  )
}
