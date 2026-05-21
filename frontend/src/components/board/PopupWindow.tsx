/**
 * PopupWindow — floating, draggable, non-modal detail viewer for the
 * Build Board + Command Center.
 *
 * Chosen design (over inline-expand / side-panel / full-page modal)
 * because users wanted to keep the board interactive while reading
 * task detail — see the BoardPreview comparison page for the trade-off
 * analysis.
 *
 * Behavior:
 *   - Fixed-positioned over the page (z-index 9999)
 *   - Drag the title bar to reposition; cursor flips to grabbing
 *   - Minimize collapses to just the title bar; restore expands
 *   - Close dismisses; ESC dismisses too
 *   - No backdrop — the board behind stays clickable
 *   - Initial position is centered horizontally near the top
 *
 * Children render in the body region; the caller decides what to
 * stuff in there (TaskDrillIn is the typical choice, but any content
 * works — keeps this component reusable).
 */

import { useEffect, useRef, useState } from "react"
import { X, Move, Minus } from "lucide-react"

interface Props {
  /** Title shown on the left side of the window's title bar. Long titles
   * truncate with ellipsis to leave room for the controls. */
  title: string
  /** Optional subtitle / ID rendered just before the title in muted text. */
  subtitle?: string | null
  /** Called when the user clicks the close button OR presses Esc. */
  onClose: () => void
  /** Window body. */
  children: React.ReactNode
  /** Override the default width (520px). */
  width?: number
  /** Override the default max height (75vh). */
  maxHeight?: string | number
}

export function PopupWindow({
  title, subtitle, onClose, children, width = 520, maxHeight = "75vh",
}: Props) {
  // Center horizontally near the top of the viewport on first open.
  const [pos, setPos] = useState(() => ({
    x: Math.max(20, (window.innerWidth - width) / 2),
    y: 120,
  }))
  const [minimized, setMinimized] = useState(false)
  const [dragging, setDragging] = useState(false)
  const dragOffset = useRef<{ x: number; y: number } | null>(null)

  const onMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    dragOffset.current = { x: e.clientX - pos.x, y: e.clientY - pos.y }
    setDragging(true)
    e.preventDefault()
  }

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragOffset.current) return
      // Clamp position so the title bar never leaves the viewport entirely
      // (we want at least 40px on screen so the user can grab it back).
      const minX = -width + 80
      const maxX = window.innerWidth - 80
      const minY = 0
      const maxY = window.innerHeight - 40
      const newX = Math.min(maxX, Math.max(minX, e.clientX - dragOffset.current.x))
      const newY = Math.min(maxY, Math.max(minY, e.clientY - dragOffset.current.y))
      setPos({ x: newX, y: newY })
    }
    const onUp = () => {
      dragOffset.current = null
      setDragging(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("mousemove", onMove)
    window.addEventListener("mouseup", onUp)
    window.addEventListener("keydown", onKey)
    return () => {
      window.removeEventListener("mousemove", onMove)
      window.removeEventListener("mouseup", onUp)
      window.removeEventListener("keydown", onKey)
    }
  }, [width, onClose])

  return (
    <div style={{
      position: "fixed",
      top: pos.y, left: pos.x,
      width,
      maxHeight: minimized ? 40 : maxHeight,
      background: "var(--bg-card)",
      border: "1px solid var(--accent)",
      borderRadius: "var(--radius)",
      boxShadow: "0 10px 40px rgba(0, 0, 0, 0.5)",
      display: "flex", flexDirection: "column",
      overflow: "hidden",
      zIndex: 9999,
      fontFamily: "var(--font)",
    }}>
      {/* Title bar (drag handle) */}
      <div
        onMouseDown={onMouseDown}
        style={{
          padding: "8px 12px",
          background: "var(--bg-hover)",
          borderBottom: "1px solid var(--border)",
          display: "flex", alignItems: "center", justifyContent: "space-between",
          cursor: dragging ? "grabbing" : "grab",
          userSelect: "none",
          flexShrink: 0,
        }}
      >
        <span style={{
          display: "inline-flex", alignItems: "center", gap: 8,
          minWidth: 0, flex: 1,
        }}>
          <Move size={12} color="var(--text-muted)" />
          {subtitle && (
            <span style={{
              fontSize: 10, fontFamily: "var(--font-mono)",
              color: "var(--text-muted)", flexShrink: 0,
            }}>
              {subtitle}
            </span>
          )}
          <span style={{
            fontSize: 13, fontWeight: 700, color: "var(--text-primary)",
            whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
            minWidth: 0,
          }}>
            {title}
          </span>
        </span>
        <span style={{ display: "inline-flex", gap: 2, flexShrink: 0 }}>
          <button
            type="button"
            onClick={() => setMinimized((m) => !m)}
            title={minimized ? "Restore" : "Minimize"}
            style={controlBtn}
          >
            <Minus size={12} />
          </button>
          <button
            type="button"
            onClick={onClose}
            title="Close (Esc)"
            style={controlBtn}
          >
            <X size={12} />
          </button>
        </span>
      </div>

      {!minimized && (
        <div style={{
          padding: 14,
          overflow: "auto",
          flex: 1,
          fontSize: 12,
          color: "var(--text-primary)",
        }}>
          {children}
        </div>
      )}
    </div>
  )
}

const controlBtn: React.CSSProperties = {
  width: 22, height: 22, padding: 0, borderRadius: 3,
  background: "transparent", color: "var(--text-muted)",
  border: "none", cursor: "pointer",
  display: "inline-flex", alignItems: "center", justifyContent: "center",
}
