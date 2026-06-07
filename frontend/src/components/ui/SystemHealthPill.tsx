/**
 * SystemHealthPill — live post-deploy health status badge.
 *
 * Shows the verdict from the latest ops_heal_agent run as a colour-coded pill:
 *   ✅ HEALTHY      green   — all services operational
 *   ⚠️ DEGRADED     amber   — minor issue, likely self-healing
 *   ❌ UNHEALTHY    red     — manual intervention required
 *   ⏳ MONITORING   blue    — agent is running health checks right now
 *   — unknown       grey    — no deployment yet, or pill not initialized
 *
 * Data sources (in priority order):
 *  1. WebSocket `ops.*` events — real-time verdict as the agent runs.
 *  2. REST GET /api/v1/ops/latest — initial value on mount (derived from the
 *     deployment_states table).
 *
 * Clicking the pill opens a tooltip with the last summary text.
 */

import { useState, useEffect, useRef } from "react"
import { api } from "../../lib/api"

type Verdict = "HEALTHY" | "DEGRADED" | "UNHEALTHY" | "MONITORING" | "unknown"

interface OpsEvent {
  verdict?: string
  summary?: string
  deployment_id?: string
  request_id?: string
  error?: string
}

const VERDICT_CONFIG: Record<
  Verdict,
  { label: string; icon: string; bg: string; text: string; border: string }
> = {
  HEALTHY: {
    label: "Healthy",
    icon: "✅",
    bg: "rgba(16, 185, 129, 0.12)",
    text: "#10b981",
    border: "rgba(16, 185, 129, 0.35)",
  },
  DEGRADED: {
    label: "Degraded",
    icon: "⚠️",
    bg: "rgba(245, 158, 11, 0.12)",
    text: "#f59e0b",
    border: "rgba(245, 158, 11, 0.35)",
  },
  UNHEALTHY: {
    label: "Unhealthy",
    icon: "❌",
    bg: "rgba(239, 68, 68, 0.12)",
    text: "#ef4444",
    border: "rgba(239, 68, 68, 0.35)",
  },
  MONITORING: {
    label: "Monitoring…",
    icon: "⏳",
    bg: "rgba(99, 102, 241, 0.12)",
    text: "#6366f1",
    border: "rgba(99, 102, 241, 0.35)",
  },
  unknown: {
    label: "No data",
    icon: "—",
    bg: "rgba(100, 116, 139, 0.08)",
    text: "var(--text-muted)",
    border: "rgba(100, 116, 139, 0.2)",
  },
}

function toVerdict(raw: string | undefined): Verdict {
  if (!raw) return "unknown"
  const upper = raw.toUpperCase()
  if (upper.includes("HEALTHY") && !upper.includes("UN")) return "HEALTHY"
  if (upper.includes("UNHEALTHY")) return "UNHEALTHY"
  if (upper.includes("DEGRADED")) return "DEGRADED"
  if (upper.includes("MONITORING")) return "MONITORING"
  return "unknown"
}

interface Props {
  /** Optional WebSocket ref so the pill can subscribe to ops.* events. */
  wsRef?: React.RefObject<WebSocket | null>
}

export function SystemHealthPill({ wsRef }: Props) {
  const [verdict, setVerdict] = useState<Verdict>("unknown")
  const [summary, setSummary] = useState<string>("")
  const [deploymentId, setDeploymentId] = useState<string>("")
  const [tooltipOpen, setTooltipOpen] = useState(false)
  const pillRef = useRef<HTMLButtonElement>(null)

  // Fetch initial verdict from the REST endpoint
  useEffect(() => {
    api
      .get("/ops/latest")
      .then((res) => {
        const data = res.data as {
          verdict?: string
          current_step?: string
          deployment_id?: string
        }
        setVerdict(toVerdict(data.verdict || data.current_step))
        setDeploymentId(data.deployment_id || "")
      })
      .catch(() => {
        // Backend may not have this endpoint yet — stay on "unknown"
      })
  }, [])

  // Subscribe to ops.* WebSocket events for real-time updates
  useEffect(() => {
    if (!wsRef) return

    const handleMessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data as string)
        const eventType: string = msg.type || ""
        const data: OpsEvent = msg.data || {}

        if (eventType === "ops.monitoring_started") {
          setVerdict("MONITORING")
          setSummary("Running health checks…")
          return
        }
        if (eventType === "ops.healthy") {
          setVerdict(toVerdict(data.verdict || "HEALTHY"))
          setSummary(data.summary || "")
          setDeploymentId(data.deployment_id || "")
          return
        }
        if (eventType === "ops.issue_detected") {
          setVerdict(toVerdict(data.verdict || "DEGRADED"))
          setSummary(data.summary || "")
          setDeploymentId(data.deployment_id || "")
          return
        }
        if (eventType === "ops.error") {
          setVerdict("DEGRADED")
          setSummary(`Ops monitor error: ${data.error || "unknown"}`)
          return
        }
      } catch {
        // ignore parse errors
      }
    }

    // Attach listener to the current WebSocket if one exists
    const ws = wsRef.current
    if (ws) {
      ws.addEventListener("message", handleMessage)
      return () => ws.removeEventListener("message", handleMessage)
    }
  }, [wsRef])

  // Close tooltip on outside click
  useEffect(() => {
    if (!tooltipOpen) return
    const handler = (e: MouseEvent) => {
      if (pillRef.current && !pillRef.current.contains(e.target as Node)) {
        setTooltipOpen(false)
      }
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [tooltipOpen])

  const cfg = VERDICT_CONFIG[verdict]

  return (
    <div style={{ position: "relative", display: "inline-flex" }}>
      <button
        ref={pillRef}
        onClick={() => setTooltipOpen((v) => !v)}
        title="Post-deploy stack health (click for details)"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 5,
          padding: "3px 10px",
          borderRadius: 999,
          border: `1px solid ${cfg.border}`,
          background: cfg.bg,
          color: cfg.text,
          fontSize: 11,
          fontWeight: 600,
          cursor: "pointer",
          letterSpacing: "0.02em",
          transition: "opacity 0.15s",
          whiteSpace: "nowrap",
        }}
      >
        <span style={{ fontSize: 12 }}>{cfg.icon}</span>
        {cfg.label}
      </button>

      {tooltipOpen && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 8px)",
            right: 0,
            zIndex: 1000,
            minWidth: 240,
            maxWidth: 380,
            background: "var(--bg-card)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            boxShadow: "0 8px 24px rgba(0,0,0,0.18)",
            padding: 14,
          }}
        >
          <div
            style={{
              fontWeight: 600,
              fontSize: 12,
              color: cfg.text,
              marginBottom: 6,
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <span>{cfg.icon}</span> Stack Health: {cfg.label}
          </div>
          {deploymentId && (
            <div
              style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 6, fontFamily: "monospace" }}
            >
              dep: {deploymentId.slice(0, 12)}…
            </div>
          )}
          {summary ? (
            <div
              style={{
                fontSize: 11,
                color: "var(--text-secondary)",
                whiteSpace: "pre-wrap",
                lineHeight: 1.5,
                maxHeight: 200,
                overflowY: "auto",
              }}
            >
              {summary.slice(0, 600)}
            </div>
          ) : (
            <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
              {verdict === "unknown"
                ? "No deployments yet — health data will appear here after the first deployment."
                : "No details available."}
            </div>
          )}
          <div style={{ marginTop: 8, fontSize: 10, color: "var(--text-muted)" }}>
            Updates live via WebSocket · ops_heal_agent
          </div>
        </div>
      )}
    </div>
  )
}
