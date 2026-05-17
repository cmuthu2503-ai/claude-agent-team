import { Palette, Check } from "lucide-react"
import { useState, useRef, useEffect, useCallback } from "react"
import { createPortal } from "react-dom"
import { useThemeStore, THEMES } from "../../stores/theme"

export function ThemeSelector({ iconOnly = false }: { iconOnly?: boolean } = {}) {
  const { theme, mode, setTheme } = useThemeStore()
  const [open, setOpen] = useState(false)
  // Positioning supports both "open downward right-aligned" (original top-bar
   // navbar usage) and "open upward left-aligned" (sidebar usage where the
   // button sits low and on the left). We pick whichever fits the viewport,
   // so the dropdown is never clipped or offscreen.
  const [pos, setPos] = useState<{
    top?: number
    bottom?: number
    left?: number
    right?: number
  } | null>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const currentTheme = THEMES.find((t) => t.id === theme)

  const updatePosition = useCallback(() => {
    if (!buttonRef.current) return
    const rect = buttonRef.current.getBoundingClientRect()
    const DROPDOWN_HEIGHT_ESTIMATE = 180 // conservative — 1 header + 2 theme rows
    const spaceBelow = window.innerHeight - rect.bottom
    const openUp = spaceBelow < DROPDOWN_HEIGHT_ESTIMATE
    const spaceRight = window.innerWidth - rect.right
    const leftAnchor = spaceRight > 260 // dropdown is 240 wide; 260 = 240 + gap
    setPos({
      ...(openUp
        ? { bottom: window.innerHeight - rect.top + 4 }
        : { top: rect.bottom + 4 }),
      ...(leftAnchor
        ? { left: rect.left }
        : { right: window.innerWidth - rect.right }),
    })
  }, [])

  useEffect(() => {
    if (!open) return
    updatePosition()
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Node
      if (
        buttonRef.current && !buttonRef.current.contains(target) &&
        dropdownRef.current && !dropdownRef.current.contains(target)
      ) {
        setOpen(false)
      }
    }
    const handleScroll = () => updatePosition()
    const handleResize = () => updatePosition()
    document.addEventListener("mousedown", handleClickOutside)
    window.addEventListener("scroll", handleScroll, true)
    window.addEventListener("resize", handleResize)
    return () => {
      document.removeEventListener("mousedown", handleClickOutside)
      window.removeEventListener("scroll", handleScroll, true)
      window.removeEventListener("resize", handleResize)
    }
  }, [open, updatePosition])

  // The portal target is document.body, which is OUTSIDE the app's
  // [data-theme][data-mode] scope. We re-apply those attributes on a wrapper
  // so CSS variables (--bg-card, --border, etc.) cascade correctly into the
  // dropdown. Without this, var(--bg-card) is undefined → transparent bg,
  // and page content bleeds through.
  const dropdown = open && pos ? createPortal(
    <div data-theme={theme} data-mode={mode}>
      <div
        ref={dropdownRef}
        style={{
          position: "fixed",
          ...(pos.top != null ? { top: pos.top } : {}),
          ...(pos.bottom != null ? { bottom: pos.bottom } : {}),
          ...(pos.left != null ? { left: pos.left } : {}),
          ...(pos.right != null ? { right: pos.right } : {}),
          width: 240,
          background: "var(--bg-card)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          boxShadow: "0 8px 24px rgba(0,0,0,0.3)",
          zIndex: 10000,
          overflow: "hidden",
          fontFamily: "var(--font)",
          color: "var(--text-primary)",
          isolation: "isolate",
        }}
      >
        <div
          style={{
            padding: "8px 12px",
            fontSize: 10,
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            color: "var(--text-muted)",
            borderBottom: "1px solid var(--border)",
            background: "var(--bg-card)",
          }}
        >
          Select Theme
        </div>
        {THEMES.map((t) => (
          <ThemeOption
            key={t.id}
            theme={t}
            isActive={theme === t.id}
            onSelect={() => {
              setTheme(t.id)
              setOpen(false)
            }}
          />
        ))}
      </div>
    </div>,
    document.body,
  ) : null

  return (
    <>
      <button
        ref={buttonRef}
        onClick={() => setOpen(!open)}
        title={`Change theme (current: ${currentTheme?.label ?? "—"})`}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: iconOnly ? 0 : 6,
          padding: iconOnly ? 6 : "6px 10px",
          background: "var(--bg-hover)",
          color: "var(--text-secondary)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          cursor: "pointer",
          fontSize: 12,
          fontFamily: "var(--font)",
          whiteSpace: "nowrap",
          flex: "0 0 auto",
        }}
      >
        <Palette size={14} />
        {!iconOnly && currentTheme?.label}
      </button>
      {dropdown}
    </>
  )
}

function ThemeOption({
  theme: t,
  isActive,
  onSelect,
}: {
  theme: (typeof THEMES)[number]
  isActive: boolean
  onSelect: () => void
}) {
  return (
    <button
      onClick={onSelect}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        width: "100%",
        textAlign: "left",
        padding: "10px 12px",
        background: isActive ? "var(--accent-subtle)" : "var(--bg-card)",
        color: isActive ? "var(--accent)" : "var(--text-primary)",
        border: "none",
        borderBottom: "1px solid var(--border)",
        cursor: "pointer",
        fontFamily: "var(--font)",
        fontSize: 13,
      }}
      onMouseEnter={(e) => {
        if (!isActive) e.currentTarget.style.background = "var(--bg-hover)"
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = isActive ? "var(--accent-subtle)" : "var(--bg-card)"
      }}
    >
      <div>
        <div style={{ fontWeight: 600 }}>{t.label}</div>
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
          {t.description}
        </div>
      </div>
      {isActive && <Check size={16} style={{ flexShrink: 0 }} />}
    </button>
  )
}
