import { StatusBadge } from "./StatusBadge"

interface AgentCardProps {
  agentId: string
  displayName: string
  role: string
  team: string
  model: string
  status: string
  currentTask?: string | null
}

export function AgentCard({ agentId, displayName, role, team, model, status, currentTask }: AgentCardProps) {
  return (
    <div
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        padding: 16,
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
        <div>
          <h3 style={{ fontWeight: 500, color: "var(--text-primary)", margin: 0 }}>{displayName}</h3>
          <p style={{ fontSize: 12, color: "var(--text-muted)", margin: "2px 0 0" }}>{role}</p>
        </div>
        <StatusBadge status={status} />
      </div>
      <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 8 }}>
        <span
          style={{
            background: "var(--bg-hover)",
            color: "var(--text-secondary)",
            borderRadius: "var(--radius)",
            padding: "2px 6px",
            fontSize: 12,
          }}
        >
          {team}
        </span>
        <span
          style={{
            background: "var(--accent-subtle)",
            color: "var(--accent)",
            borderRadius: "var(--radius)",
            padding: "2px 6px",
            fontSize: 12,
          }}
        >
          {model.replace("claude-", "").replace("-", " ")}
        </span>
      </div>
      {currentTask && (
        <p
          style={{
            marginTop: 8,
            fontSize: 14,
            color: "var(--text-secondary)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {currentTask}
        </p>
      )}
    </div>
  )
}
