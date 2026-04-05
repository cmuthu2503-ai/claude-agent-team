import { useState, useEffect } from "react"
import { api } from "../lib/api"
import { AgentCard } from "../components/ui/AgentCard"

export function TeamStatusPage() {
  const [agents, setAgents] = useState<any[]>([])

  useEffect(() => {
    api.get("/agents").then((res) => setAgents(res.data)).catch(() => {})
  }, [])

  const teams = ["planning", "development", "delivery"]
  const teamLabels: Record<string, string> = {
    planning: "Planning Team",
    development: "Development Team",
    delivery: "Delivery Team",
  }

  return (
    <div className="mx-auto max-w-5xl space-y-8 p-6">
      <h1 className="text-xl font-bold text-gray-900">Team Status</h1>

      {/* Engineering Lead */}
      {agents.filter((a) => a.team === "engineering").map((a) => (
        <AgentCard key={a.agent_id} agentId={a.agent_id} displayName={a.display_name} role={a.role} team={a.team} model={a.model} status={a.status} currentTask={a.current_task} />
      ))}

      {teams.map((team) => {
        const teamAgents = agents.filter((a) => a.team === team)
        if (teamAgents.length === 0) return null
        return (
          <section key={team}>
            <h2 className="mb-3 text-lg font-semibold text-gray-800">{teamLabels[team]}</h2>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {teamAgents.map((a) => (
                <AgentCard key={a.agent_id} agentId={a.agent_id} displayName={a.display_name} role={a.role} team={a.team} model={a.model} status={a.status} currentTask={a.current_task} />
              ))}
            </div>
          </section>
        )
      })}
    </div>
  )
}
