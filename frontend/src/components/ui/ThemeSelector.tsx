import { Palette, Moon, Sun, Check } from "lucide-react"
import { useState, useRef, useEffect } from "react"
import { useThemeStore, THEMES, type ThemeId } from "../../stores/theme"

export function ThemeSelector() {
  const { theme, setTheme } = useThemeStore()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const currentTheme = THEMES.find((t) => t.id === theme)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [])

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        onClick={() => setOpen(!open)}
        title="Change theme"
        style={{
          display: "flex",
          alignItems: "center",
          gap: "6px",
          padding: "6px 10px",
          background: "var(--bg-hover)",
          color: "var(--text-secondary)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          cursor: "pointer",
          fontSize: "12px",
          fontFamily: "var(--font)",
        }}
      >
        {currentTheme?.mode === "dark" ? <Moon size={14} /> : <Sun size={14} />}
        {currentTheme?.name}
      </button>

      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            right: 0,
            width: "240px",
            background: "var(--bg-card)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            boxShadow: "0 8px 24px rgba(0,0,0,0.3)",
            zIndex: 100,
            overflow: "hidden",
          }}
        >
          {/* Dark Themes Section */}
          <div
            style={{
              padding: "8px 12px",
              fontSize: "10px",
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              color: "var(--text-muted)",
              fontFamily: "var(--font)",
              borderBottom: "1px solid var(--border)",
              display: "flex",
              alignItems: "center",
              gap: "6px",
            }}
          >
            <Moon size={11} />
            Dark Themes
          </div>
          {THEMES.filter((t) => t.mode === "dark").map((t) => (
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

          {/* Light Themes Section */}
          <div
            style={{
              padding: "8px 12px",
              fontSize: "10px",
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              color: "var(--text-muted)",
              fontFamily: "var(--font)",
              borderBottom: "1px solid var(--border)",
              borderTop: "1px solid var(--border)",
              display: "flex",
              alignItems: "center",
              gap: "6px",
            }}
          >
            <Sun size={11} />
            Light Themes
          </div>
          {THEMES.filter((t) => t.mode === "light").map((t) => (
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
      )}
    </div>
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
        background: isActive ? "var(--accent-subtle)" : "transparent",
        color: isActive ? "var(--accent)" : "var(--text-primary)",
        border: "none",
        borderBottom: "1px solid var(--border)",
        cursor: "pointer",
        fontFamily: "var(--font)",
        fontSize: "13px",
      }}
      onMouseEnter={(e) => {
        if (!isActive) e.currentTarget.style.background = "var(--bg-hover)"
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = isActive ? "var(--accent-subtle)" : "transparent"
      }}
    >
      <div>
        <div style={{ fontWeight: 600, display: "flex", alignItems: "center", gap: "6px" }}>
          {t.mode === "dark" ? <Moon size={12} /> : <Sun size={12} />}
          {t.name}
        </div>
        <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "2px", marginLeft: "18px" }}>
          {t.description}
        </div>
      </div>
      {isActive && <Check size={16} style={{ flexShrink: 0 }} />}
    </button>
  )
}
