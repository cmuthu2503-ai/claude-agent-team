import { Bell, LayoutDashboard, History, Rocket, Users, DollarSign, Shield, Sun, Moon, Wand2, Workflow } from "lucide-react"
import { Link, useLocation } from "react-router-dom"
import { useAuthStore } from "../../stores/auth"
import { useThemeStore } from "../../stores/theme"
import { ThemeSelector } from "../ui/ThemeSelector"

interface NavItem {
  path: string
  label: string
  icon: any
  adminOnly?: boolean
}

// Order: daily drivers first, then review, then manage, then admin.
const navItems: NavItem[] = [
  { path: "/",         label: "Command Center", icon: LayoutDashboard },
  { path: "/prompts",  label: "Prompt Studio",  icon: Wand2 },
  { path: "/diagrams", label: "Diagrams",       icon: Workflow },
  { path: "/history",  label: "History",        icon: History },
  { path: "/releases", label: "Releases",       icon: Rocket },
  { path: "/team",     label: "Team",           icon: Users },
  { path: "/cost",     label: "Cost",           icon: DollarSign },
  { path: "/users",    label: "Users",          icon: Shield, adminOnly: true },
]

// Every label wrapped in this <span> has a guaranteed no-wrap constraint,
// independent of any Tailwind preflight or parent flex behavior.
const noWrapSpan = (text: string) => (
  <span style={{ whiteSpace: "nowrap", display: "inline-block" }}>{text}</span>
)

export function Navbar() {
  const location = useLocation()
  const { user, logout } = useAuthStore()
  const { mode, toggleMode } = useThemeStore()

  const visibleItems = navItems.filter((item) => !item.adminOnly || user?.role === "admin")

  return (
    <header
      style={{
        background: "var(--bg-secondary)",
        borderBottom: "1px solid var(--border)",
        fontFamily: "var(--font)",
        // NOTE: no overflow rule here on purpose. `overflow-x: auto` creates a
        // clipping context that would cut off the ThemeSelector dropdown which
        // sits absolutely-positioned below the theme button. The flex items
        // already have flex:"0 0 auto" + whiteSpace:"nowrap" so they can't wrap.
        whiteSpace: "nowrap",
        position: "relative",
        zIndex: 10,
      }}
    >
      <div
        style={{
          display: "flex",
          flexWrap: "nowrap",
          alignItems: "center",
          justifyContent: "space-between",
          maxWidth: 1400,
          margin: "0 auto",
          padding: "10px 20px",
          gap: 16,
          minHeight: 52,
        }}
      >
        {/* ── Left: logo + nav items (all siblings, no nested wrappers) ── */}
        <div
          style={{
            display: "flex",
            flexWrap: "nowrap",
            alignItems: "center",
            flex: "0 0 auto",
          }}
        >
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
              flex: "0 0 auto",
              marginRight: 20,
            }}
          >
            <span style={{ color: "var(--accent)", display: "inline-block" }}>◆</span>
            {noWrapSpan("Agent Team")}
          </Link>

          {visibleItems.map(({ path, label, icon: Icon }) => {
            const active = location.pathname === path
            return (
              <Link
                key={path}
                to={path}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "6px 10px",
                  borderRadius: "var(--radius)",
                  fontSize: 13,
                  fontWeight: 500,
                  textDecoration: "none",
                  whiteSpace: "nowrap",
                  flex: "0 0 auto",
                  color: active ? "var(--accent)" : "var(--text-secondary)",
                  background: active ? "var(--accent-subtle)" : "transparent",
                  transition: "all 0.15s",
                  marginRight: 2,
                }}
              >
                <Icon size={14} style={{ flexShrink: 0 }} />
                {noWrapSpan(label)}
              </Link>
            )
          })}
        </div>

        {/* ── Right: controls ── */}
        <div
          style={{
            display: "flex",
            flexWrap: "nowrap",
            alignItems: "center",
            gap: 10,
            flex: "0 0 auto",
          }}
        >
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
              flex: "0 0 auto",
            }}
          >
            {mode === "dark" ? <Sun size={16} /> : <Moon size={16} />}
          </button>

          <div style={{ flex: "0 0 auto" }}>
            <ThemeSelector />
          </div>

          <button
            style={{
              position: "relative",
              padding: 6,
              background: "transparent",
              border: "none",
              color: "var(--text-secondary)",
              cursor: "pointer",
              flex: "0 0 auto",
            }}
          >
            <Bell size={17} />
            <span
              style={{
                position: "absolute",
                top: -2,
                right: -2,
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

          <span
            style={{
              fontSize: 13,
              color: "var(--text-secondary)",
              whiteSpace: "nowrap",
              flex: "0 0 auto",
              display: "inline-block",
            }}
          >
            {user?.username}
          </span>

          <span
            style={{
              padding: "2px 8px",
              borderRadius: "var(--radius)",
              background: "var(--accent-subtle)",
              color: "var(--accent)",
              fontSize: 11,
              fontWeight: 500,
              whiteSpace: "nowrap",
              flex: "0 0 auto",
              display: "inline-block",
            }}
          >
            {user?.role}
          </span>

          <button
            onClick={logout}
            style={{
              fontSize: 12,
              color: "var(--text-muted)",
              background: "transparent",
              border: "none",
              cursor: "pointer",
              flex: "0 0 auto",
              whiteSpace: "nowrap",
            }}
          >
            Logout
          </button>
        </div>
      </div>
    </header>
  )
}
