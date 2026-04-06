import { useState, useEffect } from "react"
import { api } from "../lib/api"

export function CostDashboardPage() {
  const [data, setData] = useState<any>(null)

  useEffect(() => {
    api.get("/cost/dashboard").then((res) => setData(res.data)).catch(() => {})
  }, [])

  const fmt = (n: number) => (n ?? 0).toFixed(4)
  const fmtTokens = (n: number) => (n ?? 0).toLocaleString()

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: 24, display: "flex", flexDirection: "column", gap: 24, fontFamily: "var(--font)" }}>
      <h1 style={{ fontSize: 20, fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>Cost Dashboard</h1>

      {/* Summary Cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16 }}>
        {[
          { label: "Today", value: `$${fmt(data?.today?.total_cost_usd)}` },
          { label: "This Month", value: `$${fmt(data?.this_month?.total_cost_usd)}` },
          { label: "All Time", value: `$${fmt(data?.totals?.total_cost_usd)}` },
          { label: "Total API Calls", value: fmtTokens(data?.totals?.total_calls) },
        ].map((card) => (
          <div key={card.label} style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 20 }}>
            <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 4 }}>{card.label}</div>
            <div style={{ fontSize: 24, fontWeight: 700, color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}>{card.value}</div>
          </div>
        ))}
      </div>

      {/* Token Totals */}
      {data?.totals && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 16 }}>
          <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 20 }}>
            <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 4 }}>Input Tokens</div>
            <div style={{ fontSize: 20, fontWeight: 600, color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}>{fmtTokens(data.totals.total_input_tokens)}</div>
          </div>
          <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 20 }}>
            <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 4 }}>Output Tokens</div>
            <div style={{ fontSize: 20, fontWeight: 600, color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}>{fmtTokens(data.totals.total_output_tokens)}</div>
          </div>
        </div>
      )}

      {/* By Model */}
      {data?.by_model?.length > 0 && (
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 24 }}>
          <h2 style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)", margin: "0 0 12px" }}>Cost by Model</h2>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)" }}>
                <th style={{ textAlign: "left", padding: "8px 12px", fontSize: 11, textTransform: "uppercase", color: "var(--text-muted)" }}>Model</th>
                <th style={{ textAlign: "right", padding: "8px 12px", fontSize: 11, textTransform: "uppercase", color: "var(--text-muted)" }}>Input Tokens</th>
                <th style={{ textAlign: "right", padding: "8px 12px", fontSize: 11, textTransform: "uppercase", color: "var(--text-muted)" }}>Output Tokens</th>
                <th style={{ textAlign: "right", padding: "8px 12px", fontSize: 11, textTransform: "uppercase", color: "var(--text-muted)" }}>Cost</th>
              </tr>
            </thead>
            <tbody>
              {data.by_model.map((m: any) => (
                <tr key={m.model} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: "8px 12px", fontFamily: "var(--font-mono)", color: "var(--accent)" }}>{m.model}</td>
                  <td style={{ padding: "8px 12px", textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>{fmtTokens(m.input_tokens)}</td>
                  <td style={{ padding: "8px 12px", textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>{fmtTokens(m.output_tokens)}</td>
                  <td style={{ padding: "8px 12px", textAlign: "right", fontWeight: 600, fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>${fmt(m.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* By Agent */}
      {data?.by_agent?.length > 0 && (
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 24 }}>
          <h2 style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)", margin: "0 0 12px" }}>Cost by Agent</h2>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)" }}>
                <th style={{ textAlign: "left", padding: "8px 12px", fontSize: 11, textTransform: "uppercase", color: "var(--text-muted)" }}>Agent</th>
                <th style={{ textAlign: "right", padding: "8px 12px", fontSize: 11, textTransform: "uppercase", color: "var(--text-muted)" }}>Calls</th>
                <th style={{ textAlign: "right", padding: "8px 12px", fontSize: 11, textTransform: "uppercase", color: "var(--text-muted)" }}>Input</th>
                <th style={{ textAlign: "right", padding: "8px 12px", fontSize: 11, textTransform: "uppercase", color: "var(--text-muted)" }}>Output</th>
                <th style={{ textAlign: "right", padding: "8px 12px", fontSize: 11, textTransform: "uppercase", color: "var(--text-muted)" }}>Cost</th>
              </tr>
            </thead>
            <tbody>
              {data.by_agent.map((a: any) => (
                <tr key={a.agent_id} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: "8px 12px", color: "var(--text-primary)" }}>{a.agent_id.replace(/_/g, " ")}</td>
                  <td style={{ padding: "8px 12px", textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{a.calls}</td>
                  <td style={{ padding: "8px 12px", textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>{fmtTokens(a.input_tokens)}</td>
                  <td style={{ padding: "8px 12px", textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>{fmtTokens(a.output_tokens)}</td>
                  <td style={{ padding: "8px 12px", textAlign: "right", fontWeight: 600, fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>${fmt(a.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* By Request */}
      {data?.by_request?.length > 0 && (
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 24 }}>
          <h2 style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)", margin: "0 0 12px" }}>Cost by Request (Top 10)</h2>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)" }}>
                <th style={{ textAlign: "left", padding: "8px 12px", fontSize: 11, textTransform: "uppercase", color: "var(--text-muted)" }}>Request</th>
                <th style={{ textAlign: "right", padding: "8px 12px", fontSize: 11, textTransform: "uppercase", color: "var(--text-muted)" }}>Agent Calls</th>
                <th style={{ textAlign: "right", padding: "8px 12px", fontSize: 11, textTransform: "uppercase", color: "var(--text-muted)" }}>Tokens</th>
                <th style={{ textAlign: "right", padding: "8px 12px", fontSize: 11, textTransform: "uppercase", color: "var(--text-muted)" }}>Cost</th>
              </tr>
            </thead>
            <tbody>
              {data.by_request.map((r: any) => (
                <tr key={r.request_id} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: "8px 12px", fontFamily: "var(--font-mono)", color: "var(--accent)" }}>{r.request_id}</td>
                  <td style={{ padding: "8px 12px", textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{r.calls}</td>
                  <td style={{ padding: "8px 12px", textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>{fmtTokens(r.input_tokens + r.output_tokens)}</td>
                  <td style={{ padding: "8px 12px", textAlign: "right", fontWeight: 600, fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>${fmt(r.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!data?.by_agent?.length && (
        <div style={{ padding: 60, textAlign: "center", color: "var(--text-muted)" }}>
          No API usage recorded yet. Submit a request to see cost data.
        </div>
      )}
    </div>
  )
}
