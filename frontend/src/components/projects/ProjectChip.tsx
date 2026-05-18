/**
 * Small clickable chip that surfaces a request's parent project everywhere
 * a request is rendered (Command Center cards, History rows, RequestDetail
 * header, StoryBoard breadcrumb). Color + icon + name + link to /projects/<id>.
 *
 * If the project_id is missing or unknown, falls back to "Unassigned" with
 * the gray placeholder color (PM-47).
 */

import { Link } from "react-router-dom"
import {
  Folder, Rocket, Layers, Code, FlaskConical, Palette, Bug, BookOpen,
} from "lucide-react"
import { useProjectsCache } from "../../hooks/useProjectsCache"

const ICON_MAP: Record<string, React.ComponentType<{ size?: number; color?: string }>> = {
  folder: Folder, rocket: Rocket, layers: Layers, code: Code,
  "flask-conical": FlaskConical, palette: Palette, bug: Bug, "book-open": BookOpen,
}

interface Props {
  projectId: string | null | undefined
  /** "compact" (default) shows icon + truncated name; "full" shows full name
   *  with no truncation, used in places with more horizontal space. */
  variant?: "compact" | "full"
  /** Stop link click from bubbling — useful when the chip is inside a
   *  parent <Link>. */
  stopPropagation?: boolean
}

export function ProjectChip({ projectId, variant = "compact", stopPropagation = true }: Props) {
  const { byId } = useProjectsCache()
  const p = byId(projectId)
  const Icon = ICON_MAP[p.icon] || Folder

  const maxName = variant === "full" ? 50 : 18
  const displayName = p.name.length > maxName ? p.name.slice(0, maxName - 1) + "…" : p.name

  return (
    <Link
      to={`/projects/${p.project_id}`}
      title={p.name}
      onClick={(e) => stopPropagation && e.stopPropagation()}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 6px 2px 4px",
        fontSize: 11,
        lineHeight: 1.2,
        fontFamily: "var(--font)",
        textDecoration: "none",
        color: p.color,
        background: "var(--bg-hover)",
        border: `1px solid ${p.color}`,
        borderRadius: 3,
        whiteSpace: "nowrap",
        flexShrink: 0,
      }}
    >
      <Icon size={11} />
      <span>{displayName}</span>
    </Link>
  )
}
