import { Bell, Moon, Sun } from "lucide-react"
import { Link } from "react-router-dom"
import { useThemeStore } from "../../stores/theme"
import { ThemeSelector } from "../ui/ThemeSelector"

// Top header: logo on the left, three icon controls on the right
// (mode toggle, theme selector, notification bell). User identity +
// logout live in the Sidebar footer.
const noWrapSpan = (text: string) => (
  <span style={{ whiteSpace: "nowrap", display: "inline-block" }}>{text}</span>
)

export function Navbar() {
  const { mode, toggleMode } = useThemeStore()

  return (
    <header
      style={{
        background: "var(--bg-secondary)",
        borderBottom: "1px solid var(--border)",
        fontFamily: "var(--font)",
        whiteSpace: "nowrap",
        position: "relative",
        zIndex: 10,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          maxWidth: 1800,
          margin: "0 auto",
          padding: "10px 20px",
          minHeight: 52,
        }}
      >
        {/* ── Left: logo ── */}
        <Link
          to="/"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            fontSize: 17,
            fontWeight: 700,
            color: "var(--accent)",
            textDecoration: "none",
            whiteSpace: "nowrap",
          }}
        >
          <span style={{ color: "var(--accent)", display: "inline-block" }}>◆</span>
          {noWrapSpan("Agent Team")}
        </Link>

        {/* ── Right: theme mode toggle, theme selector (icon-only),
             notification bell. ── */}
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button
            onClick={toggleMode}
            title={mode === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 30,
              height: 30,
              borderRadius: "var(--radius)",
              background: "var(--bg-hover)",
              border: "1px solid var(--border)",
              color: mode === "dark" ? "var(--warning)" : "var(--accent)",
              cursor: "pointer",
            }}
          >
            {mode === "dark" ? <Sun size={16} /> : <Moon size={16} />}
          </button>

          <ThemeSelector iconOnly />

          <button
            title="Notifications"
            style={{
              position: "relative",
              padding: 6,
              background: "transparent",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              color: "var(--accent)",
              cursor: "pointer",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 30,
              height: 30,
            }}
          >
            <Bell size={15} />
            <span
              style={{
                position: "absolute",
                top: -4,
                right: -4,
                width: 16,
                height: 16,
                borderRadius: 999,
                background: "var(--danger)",
                color: "#fff",
                fontSize: 9,
                fontWeight: 700,
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              0
            </span>
          </button>
        </div>
      </div>
    </header>
  )
}
