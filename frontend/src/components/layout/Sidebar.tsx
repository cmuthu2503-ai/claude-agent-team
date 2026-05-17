/**
 * Sidebar — left-rail navigation + user controls footer.
 *
 * Top half: nav items (Command Center, Prompt Studio, ...).
 * Bottom half (pushed to the bottom via margin-top: auto on the footer):
 *   theme mode toggle, theme selector (icon-only), notification bell,
 *   username + role badge, logout button.
 *
 * All controls were previously in the top Navbar — moved here so the top
 * bar is logo-only and the sidebar owns the entire navigation + user
 * surface.
 */

import {
  LayoutDashboard,
  History,
  Rocket,
  Users,
  DollarSign,
  Shield,
  Wand2,
  Workflow,
  LogOut,
} from "lucide-react"
import { Link, useLocation } from "react-router-dom"
import { useAuthStore } from "../../stores/auth"

interface NavItem {
  path: string
  label: string
  icon: any
  adminOnly?: boolean
}

const navItems: NavItem[] = [
  { path: "/", label: "Command Center", icon: LayoutDashboard },
  { path: "/prompts", label: "Prompt Studio", icon: Wand2 },
  { path: "/diagrams", label: "Diagrams", icon: Workflow },
  { path: "/history", label: "History", icon: History },
  { path: "/releases", label: "Releases", icon: Rocket },
  { path: "/team", label: "Team", icon: Users },
  { path: "/cost", label: "Cost", icon: DollarSign },
  { path: "/users", label: "Users", icon: Shield, adminOnly: true },
]

export function Sidebar() {
  const location = useLocation()
  const { user, logout } = useAuthStore()

  const visibleItems = navItems.filter(
    (item) => !item.adminOnly || user?.role === "admin",
  )

  return (
    <nav
      className="app-sidebar"
      aria-label="Primary"
      style={{
        width: 220,
        background: "var(--bg-secondary)",
        borderRight: "1px solid var(--border)",
        display: "flex",
        flexDirection: "column",
        // FIXED positioning (not sticky) so the sidebar is anchored to
        // the viewport regardless of any ancestor overflow / containing-
        // block quirks that were breaking sticky. The Layout in App.tsx
        // adds a matching marginLeft on the main content so it doesn't
        // slide under us.
        position: "fixed",
        top: 52, // matches Navbar minHeight
        left: 0,
        bottom: 0,
        zIndex: 9,
        // No overflow at this level — split into a scrollable nav region
        // + a pinned footer so the footer is ALWAYS visible regardless of
        // how many nav items there are.
        overflow: "hidden",
      }}
    >
      {/* ── Nav items (scrollable if they outgrow available height) ── */}
      <div
        style={{
          flex: "1 1 auto",
          minHeight: 0,
          overflowY: "auto",
          padding: "16px 8px 8px",
          display: "flex",
          flexDirection: "column",
          gap: 4,
        }}
      >
      {visibleItems.map(({ path, label, icon: Icon }) => {
        const active = location.pathname === path
        return (
          <Link
            key={path}
            to={path}
            aria-current={active ? "page" : undefined}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "10px 12px",
              borderRadius: "var(--radius)",
              fontSize: 13,
              fontWeight: 500,
              textDecoration: "none",
              color: active ? "var(--accent)" : "var(--text-secondary)",
              background: active ? "var(--accent-subtle)" : "transparent",
              transition: "background 0.15s, color 0.15s",
              whiteSpace: "nowrap",
            }}
          >
            <Icon size={16} style={{ flexShrink: 0 }} />
            <span>{label}</span>
          </Link>
        )
      })}

      </div>

      {/* ── Footer: user identity + logout. Lives OUTSIDE the scrollable
           nav region as a flex sibling, so it's always pinned at the
           bottom of the sidebar's visible area — no scrolling required
           even if the nav items above overflow. ── */}
      <div
        style={{
          flexShrink: 0,
          padding: "12px 8px 16px",
          borderTop: "1px solid var(--border)",
          background: "var(--bg-secondary)",
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        {/* User identity — username + role badge inline on one row */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "0 4px",
            minWidth: 0,
          }}
        >
          <span
            style={{
              fontSize: 13,
              color: "var(--accent)",
              fontWeight: 600,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              minWidth: 0,
              flex: "1 1 auto",
            }}
            title={user?.username}
          >
            {user?.username}
          </span>
          <span
            style={{
              padding: "1px 6px",
              borderRadius: "var(--radius)",
              background: "var(--accent-subtle)",
              color: "var(--accent)",
              fontSize: 9,
              fontWeight: 700,
              letterSpacing: 1,
              textTransform: "uppercase",
              border: "1px solid var(--accent)",
              flex: "0 0 auto",
              whiteSpace: "nowrap",
            }}
          >
            {user?.role}
          </span>
        </div>

        {/* Logout */}
        <button
          onClick={logout}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 12px",
            background: "transparent",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            color: "var(--accent)",
            fontSize: 12,
            fontWeight: 600,
            letterSpacing: 1,
            textTransform: "uppercase",
            cursor: "pointer",
            fontFamily: "var(--font)",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.borderColor = "var(--danger)"
            e.currentTarget.style.color = "var(--danger)"
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.borderColor = "var(--border)"
            e.currentTarget.style.color = "var(--accent)"
          }}
        >
          <LogOut size={14} />
          Logout
        </button>
      </div>
    </nav>
  )
}
