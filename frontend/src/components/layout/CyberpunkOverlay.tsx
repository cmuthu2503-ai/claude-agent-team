/**
 * CyberpunkOverlay — DOM elements that the CSS-only theme layer can't
 * produce: matrix rain columns, floating particles, scrolling data ticker,
 * radar sweep widget. Mounted at App root only when the active theme is
 * `cyberpunk-hyperdrive`; renders null otherwise (no DOM cost for other
 * themes).
 *
 * Every element is positioned `fixed` with `pointerEvents: none` so it
 * sits over the page without intercepting clicks or shifting layout.
 *
 * The ticker is wired to real app state via `useLiveTicker()` — polls
 * the same endpoints the dashboard uses (agents, cost, requests) so the
 * feed actually reflects what the system is doing. Falls back to neutral
 * strings when an endpoint fails (e.g., no auth yet) so the bar never
 * looks broken.
 */

import { useEffect, useState } from "react"
import { api } from "../../lib/api"
import { useAuthStore } from "../../stores/auth"
import { useThemeStore } from "../../stores/theme"

const MATRIX_GLYPHS =
  "01101001アエカサナハマ10110011カケコサシスセソ"

interface LiveTickerData {
  uplinkOk: boolean
  agentsOnline: number
  agentsTotal: number
  latestRequest: { id: string; status: string } | null
  recentCommitId: string | null
  /** Cost in USD spent today, from `data.today.total_cost_usd`. */
  costTodayUsd: number
  /** Tokens consumed today (input + output), from
   *  `data.today.total_input_tokens + total_output_tokens`. Paired with
   *  `costTodayUsd` so [COST] and [TOKENS] share the same time window
   *  in the ticker — earlier this read all-time totals, which mixed
   *  scopes and made the bar misleading. */
  tokensToday: number
}

function useLiveTicker(): LiveTickerData {
  // Gate polling on auth state — on the login page (no token), every API
  // call returns 401, which the api client handles via `window.location.href
  // = "/login"`. That's a full page reload. The overlay remounts, polls
  // again, gets 401, reloads, ... — an infinite reload loop that flickers
  // the login screen and prevents the user from typing into it. Pausing
  // the poll until authenticated stops the loop entirely.
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const [data, setData] = useState<LiveTickerData>({
    uplinkOk: false,
    agentsOnline: 0,
    agentsTotal: 0,
    latestRequest: null,
    recentCommitId: null,
    costTodayUsd: 0,
    tokensToday: 0,
  })

  useEffect(() => {
    if (!isAuthenticated) return
    let mounted = true
    const poll = async () => {
      // Run all four fetches in parallel; settle independently so one
      // failure (e.g. agents endpoint hiccup) doesn't blank the rest.
      const [agents, requests, cost] = await Promise.allSettled([
        api.get("/agents"),
        api.get("/requests?per_page=10"),
        api.get("/cost/dashboard"),
      ])
      if (!mounted) return

      const next: LiveTickerData = {
        uplinkOk:
          agents.status === "fulfilled" ||
          requests.status === "fulfilled" ||
          cost.status === "fulfilled",
        agentsOnline: 0,
        agentsTotal: 0,
        latestRequest: null,
        recentCommitId: null,
        costTodayUsd: 0,
        tokensToday: 0,
      }

      if (agents.status === "fulfilled") {
        const list = (agents.value?.data ?? []) as Array<{ status: string }>
        next.agentsTotal = list.length
        // "Online" = not idle (anything in_progress, analyzing, etc.)
        next.agentsOnline = list.filter((a) => a.status !== "idle").length
      }

      if (requests.status === "fulfilled") {
        const list = (requests.value?.data ?? requests.value ?? []) as Array<{
          request_id: string
          status: string
        }>
        // Pick the most recent in-flight; fall back to most recent overall
        const inFlight = list.find(
          (r) =>
            !["completed", "failed", "cancelled"].includes(r.status),
        )
        const newest = inFlight ?? list[0]
        if (newest) {
          next.latestRequest = {
            id: newest.request_id,
            status: newest.status,
          }
        }
        // Most recent completed → "github push" entry
        const completed = list.find((r) => r.status === "completed")
        if (completed) next.recentCommitId = completed.request_id
      }

      if (cost.status === "fulfilled") {
        // Pair today's cost with today's token count so the ticker shows
        // a consistent time window. Both fields come from `data.today.*`
        // on /cost/dashboard.
        const today = cost.value?.data?.today ?? {}
        next.costTodayUsd = today.total_cost_usd ?? 0
        next.tokensToday =
          (today.total_input_tokens ?? 0) + (today.total_output_tokens ?? 0)
      }

      setData(next)
    }

    poll()
    const interval = window.setInterval(poll, 10000)
    return () => {
      mounted = false
      window.clearInterval(interval)
    }
  }, [isAuthenticated])

  return data
}

function MatrixColumns() {
  return (
    <>
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className={`ch-matrix-col ch-mc-${i + 1}`}>
          {MATRIX_GLYPHS}
        </div>
      ))}
    </>
  )
}

function Particles() {
  return (
    <>
      {Array.from({ length: 7 }).map((_, i) => (
        <div key={i} className={`ch-particle ch-p-${i + 1}`} />
      ))}
    </>
  )
}

function Radar() {
  return (
    <div className="ch-radar" aria-hidden="true">
      <div className="ch-radar-blip ch-blip-1" />
      <div className="ch-radar-blip ch-blip-2" />
      <div className="ch-radar-blip ch-blip-3" />
    </div>
  )
}

function formatUsd(n: number): string {
  if (n >= 100) return `$${n.toFixed(0)}`
  if (n >= 1) return `$${n.toFixed(2)}`
  // Sub-dollar amounts: show 4 decimal places so a fresh day reading
  // $0.0023 doesn't get rounded to "$0.00".
  return `$${n.toFixed(4)}`
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

/** A repeatable block of ticker spans — rendered twice so the
 *  -50% scroll loop is seamless without a visible gap. */
function TickerSlots({ data }: { data: LiveTickerData }) {
  const statusClass = (status: string) => {
    if (status === "completed") return "ch-ok"
    if (["failed", "cancelled"].includes(status)) return "ch-alert"
    if (["analyzing", "review", "testing"].includes(status)) return "ch-warn"
    return "ch-alert" // in_progress / received / delegated
  }
  const reqLabel = data.latestRequest
    ? `${data.latestRequest.id} ${data.latestRequest.status.replace(/_/g, " ")}`
    : "no active requests"
  return (
    <>
      <span className={data.uplinkOk ? "ch-ok" : "ch-alert"}>[SYS]</span>
      <span>{data.uplinkOk ? "uplink stable" : "uplink offline"}</span>
      <span className="ch-sep">·</span>
      <span className="ch-ok">[NET]</span>
      <span>
        {data.agentsTotal > 0
          ? `${data.agentsOnline} of ${data.agentsTotal} agents active`
          : "agents pending"}
      </span>
      <span className="ch-sep">·</span>
      <span
        className={
          data.latestRequest
            ? statusClass(data.latestRequest.status)
            : "ch-warn"
        }
      >
        [REQ]
      </span>
      <span>{reqLabel}</span>
      <span className="ch-sep">·</span>
      <span className="ch-warn">[INFO]</span>
      <span>supervisor heartbeat 200ms</span>
      <span className="ch-sep">·</span>
      <span className="ch-ok">[OK]</span>
      <span>
        {data.recentCommitId
          ? `github push ${data.recentCommitId} → origin/main`
          : "no recent commits"}
      </span>
      <span className="ch-sep">·</span>
      <span className="ch-ok">[COST]</span>
      <span>today {formatUsd(data.costTodayUsd)}</span>
      <span className="ch-sep">·</span>
      <span className="ch-ok">[TOKENS]</span>
      <span>today {formatTokens(data.tokensToday)}</span>
      <span className="ch-sep">·</span>
      <span className="ch-ok">[SYS]</span>
      <span>claude-opus-4-7 ready</span>
      <span className="ch-sep">·</span>
    </>
  )
}

function Ticker() {
  const data = useLiveTicker()
  return (
    <div className="ch-ticker" aria-hidden="true">
      <div className="ch-ticker-label">▮ FEED</div>
      <div className="ch-ticker-track">
        <div className="ch-ticker-content">
          <TickerSlots data={data} />
          {/* Duplicate run keeps the -50% scroll loop seamless */}
          <TickerSlots data={data} />
        </div>
      </div>
    </div>
  )
}

export function CyberpunkOverlay() {
  const theme = useThemeStore((s) => s.theme)
  if (theme !== "cyberpunk-hyperdrive") return null
  return (
    <>
      <Ticker />
      <MatrixColumns />
      <Particles />
      <Radar />
    </>
  )
}
