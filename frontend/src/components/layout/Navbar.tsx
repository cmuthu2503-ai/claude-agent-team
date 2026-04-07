import { Bell, LayoutDashboard, History, Rocket, Users, DollarSign, Shield, Sun, Moon, Wand2 } from "lucide-react"
import { Link, useLocation } from "react-router-dom"
import { useAuthStore } from "../../stores/auth"
import { useThemeStore } from "../../stores/theme"
import { ThemeSelector } from "../ui/ThemeSelector"

const navItems = [
  { path: "/", label: "Command Center", icon: LayoutDashboard },
  { path: "/prompts", label: "Prompt Studio", icon: Wand2 },
  { path: "/history", label: "History", icon: History },
  { path: "/releases", label: "Releases", icon: Rocket },
  { path: "/team", label: "Team", icon: Users },
  { path: "/cost", label: "Cost", icon: DollarSign },
]

export function Navbar() {
  const location = useLocation()
  const { user, logout } = useAuthStore()
  const { mode, toggleMode } = useThemeStore()

  return (
    <header
      style={{
        background: "var(--bg-secondary)",
        borderBottom: "1px solid var(--border)",
        fontFamily: "var(--font)",
      }}
    >
      <div style={{ maxWidth: "1280px", margin: "0 auto", display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 16px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "28px" }}>
          <Link to="/" style={{ fontSize: "18px", fontWeight: 700, color: "var(--accent)", textDecoration: "none" }}>
            Agent Team
          </Link>
          <nav style={{ display: "flex", gap: "4px" }}>
            {navItems.map(({ path, label, icon: Icon }) => {
              const active = location.pathname === path
              return (
                <Link
                  key={path}
                  to={path}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "6px",
                    padding: "6px 12px",
                    borderRadius: "var(--radius)",
                    fontSize: "13px",
                    fontWeight: 500,
                    textDecoration: "none",
                    color: active ? "var(--accent)" : "var(--text-secondary)",
                    background: active ? "var(--accent-subtle)" : "transparent",
                  }}
                >
                  <Icon size={15} />
                  {label}
                </Link>
              )
            })}
            {user?.role === "admin" && (
              <Link
                to="/users"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  padding: "6px 12px",
                  borderRadius: "var(--radius)",
                  fontSize: "13px",
                  fontWeight: 500,
                  textDecoration: "none",
                  color: location.pathname === "/users" ? "var(--accent)" : "var(--text-secondary)",
                  background: location.pathname === "/users" ? "var(--accent-subtle)" : "transparent",
                }}
              >
                <Shield size={15} />
                Users
              </Link>
            )}
          </nav>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          {/* Light/Dark Mode Toggle */}
          <button
            onClick={toggleMode}
            title={mode === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 36,
              height: 36,
              borderRadius: "var(--radius)",
              background: "var(--bg-hover)",
              border: "1px solid var(--border)",
              color: mode === "dark" ? "var(--warning)" : "var(--accent)",
              cursor: "pointer",
              transition: "all 0.2s",
            }}
          >
            {mode === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          </button>
          <ThemeSelector />
          <button
            style={{
              position: "relative",
              padding: "6px",
              background: "transparent",
              border: "none",
              color: "var(--text-secondary)",
              cursor: "pointer",
            }}
          >
            <Bell size={17} />
            <span
              style={{
                position: "absolute",
                top: "-2px",
                right: "-2px",
                width: "16px",
                height: "16px",
                borderRadius: "999px",
                background: "var(--danger)",
                color: "#fff",
                fontSize: "9px",
                fontWeight: 700,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              0
            </span>
          </button>
          <span style={{ fontSize: "13px", color: "var(--text-secondary)" }}>{user?.username}</span>
          <span
            style={{
              padding: "2px 8px",
              borderRadius: "var(--radius)",
              background: "var(--accent-subtle)",
              color: "var(--accent)",
              fontSize: "11px",
              fontWeight: 500,
            }}
          >
            {user?.role}
          </span>
          <button
            onClick={logout}
            style={{
              fontSize: "12px",
              color: "var(--text-muted)",
              background: "transparent",
              border: "none",
              cursor: "pointer",
            }}
          >
            Logout
          </button>
        </div>
      </div>
    </header>
  )
}
