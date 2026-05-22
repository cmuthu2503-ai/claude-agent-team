/**
 * RefreshButton — small icon button for manual page-data refresh.
 *
 * Used at the top of pages that have polling / WebSocket-driven data
 * (Command Center, Build Board, Story Board). The page already
 * receives most updates passively, but server-side state can shift
 * without an event (supervisor flips a deploy status, an admin runs
 * a script, a row gets manually edited) — Refresh gives the user a
 * deterministic way to re-fetch without a full page reload.
 *
 * The icon spins (CSS `animation: spin`) while `refreshing` is true,
 * disabling clicks until the in-flight fetch resolves. The keyframes
 * are injected once into <head> on first mount so a single page can
 * have multiple buttons (one per section) without duplicating the
 * @keyframes block.
 */

import { useEffect } from "react"
import { RefreshCw } from "lucide-react"

interface Props {
  onClick: () => void | Promise<void>
  refreshing?: boolean
  /** Override the button label. Defaults to "Refresh". */
  label?: string
  /** Override the tooltip. Defaults to "Refresh data". */
  title?: string
  /** Compact mode — icon only, no label. */
  iconOnly?: boolean
}

const KEYFRAME_ID = "refresh-button-spin-keyframes"

function ensureSpinKeyframes(): void {
  if (typeof document === "undefined") return
  if (document.getElementById(KEYFRAME_ID)) return
  const style = document.createElement("style")
  style.id = KEYFRAME_ID
  style.textContent = `
    @keyframes ${KEYFRAME_ID} {
      from { transform: rotate(0deg); }
      to   { transform: rotate(360deg); }
    }
  `
  document.head.appendChild(style)
}

export function RefreshButton({
  onClick, refreshing = false, label = "Refresh",
  title = "Refresh data", iconOnly = false,
}: Props) {
  useEffect(() => { ensureSpinKeyframes() }, [])

  return (
    <button
      type="button"
      onClick={() => { if (!refreshing) void onClick() }}
      disabled={refreshing}
      title={title}
      style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        padding: iconOnly ? "6px 8px" : "6px 12px",
        fontSize: 12, fontWeight: 600,
        background: "transparent",
        color: refreshing ? "var(--text-muted)" : "var(--text-secondary)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        cursor: refreshing ? "wait" : "pointer",
        fontFamily: "var(--font)",
        lineHeight: 1, whiteSpace: "nowrap",
        transition: "background 0.15s, color 0.15s, border-color 0.15s",
      }}
      onMouseEnter={(e) => {
        if (refreshing) return
        e.currentTarget.style.background = "var(--bg-hover)"
        e.currentTarget.style.color = "var(--accent)"
        e.currentTarget.style.borderColor = "var(--accent)"
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "transparent"
        e.currentTarget.style.color = refreshing ? "var(--text-muted)" : "var(--text-secondary)"
        e.currentTarget.style.borderColor = "var(--border)"
      }}
    >
      <RefreshCw
        size={12}
        style={{
          animation: refreshing ? `${KEYFRAME_ID} 0.9s linear infinite` : "none",
        }}
      />
      {!iconOnly && <span>{refreshing ? "Refreshing…" : label}</span>}
    </button>
  )
}
