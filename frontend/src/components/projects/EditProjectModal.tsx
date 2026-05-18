/**
 * EditProjectModal — opens from the Project Detail page header so users
 * can update any field that wasn't possible to set, or was mistyped, at
 * creation time. Most common case: adding the GitHub repo URL after the
 * fact. Also covers name, description, color, icon, tags, lead, default
 * team, target date.
 *
 * PATCHes /api/v1/projects/{id}. Server-side validation rules from
 * src/core/project_validation.py are the source of truth; this modal
 * only does cheap client-side caps (length / count) to keep the UX snappy.
 *
 * Template + status changes are deliberately NOT here:
 *   - template_id: re-templating a live project would be confusing —
 *     starter checklist matches by description-prefix, so swapping the
 *     template would silently re-flag historical requests.
 *   - status (archive): handled by a separate admin action; archiving
 *     has different consequences (hidden from default listings, blocks
 *     new request submissions) and deserves its own confirm flow.
 */

import { useEffect, useState } from "react"
import {
  Folder, Rocket, Layers, Code, FlaskConical, Palette, Bug, BookOpen,
  X,
} from "lucide-react"
import { api } from "../../lib/api"
import { invalidateProjectsCache } from "../../hooks/useProjectsCache"

const COLORS = [
  "#00f0ff", "#ff2a6d", "#39ff14", "#f9f871",
  "#ff8c00", "#b026ff", "#0070f3", "#8080a0",
]
const ICON_DEFS = [
  { id: "folder", Comp: Folder },
  { id: "rocket", Comp: Rocket },
  { id: "layers", Comp: Layers },
  { id: "code", Comp: Code },
  { id: "flask-conical", Comp: FlaskConical },
  { id: "palette", Comp: Palette },
  { id: "bug", Comp: Bug },
  { id: "book-open", Comp: BookOpen },
] as const

const MAX_NAME = 80
const MAX_DESC = 500
const MAX_TAG = 25
const MAX_TAGS = 10

interface UserOption { user_id: string; username: string; role: string }

export interface EditableProject {
  project_id: string
  name: string
  description: string
  color: string
  icon: string
  tags: string[]
  lead_user_id: string | null
  repo_url: string
  default_team: string | null
  target_date: string | null
}

interface Props {
  open: boolean
  initial: EditableProject
  onClose: () => void
  onSaved: () => void
}

export function EditProjectModal({ open, initial, onClose, onSaved }: Props) {
  const [name, setName] = useState(initial.name)
  const [description, setDescription] = useState(initial.description || "")
  const [color, setColor] = useState(initial.color || COLORS[0])
  const [icon, setIcon] = useState<string>(initial.icon || ICON_DEFS[0].id)
  const [tags, setTags] = useState<string[]>(initial.tags || [])
  const [tagDraft, setTagDraft] = useState("")
  const [leadUserId, setLeadUserId] = useState<string>(initial.lead_user_id || "")
  const [repoUrl, setRepoUrl] = useState(initial.repo_url || "")
  const [defaultTeam, setDefaultTeam] = useState<string>(initial.default_team || "")
  // The detail payload has an ISO datetime — chop to YYYY-MM-DD for <input type=date>.
  const [targetDate, setTargetDate] = useState(
    initial.target_date ? initial.target_date.slice(0, 10) : ""
  )

  const [users, setUsers] = useState<UserOption[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")

  // Re-seed the form ONLY when the modal opens (or when the user opens it
  // for a different project_id). The parent page polls every 5s and hands
  // us a brand-new `initial` object on each poll — depending on that
  // reference here would wipe whatever the user has typed mid-edit, and
  // then submit() would PATCH the wiped values (= "URL not saved" bug).
  // Keying on `open` + `initial.project_id` ignores the polling churn.
  useEffect(() => {
    if (!open) return
    setName(initial.name)
    setDescription(initial.description || "")
    setColor(initial.color || COLORS[0])
    setIcon(initial.icon || ICON_DEFS[0].id)
    setTags(initial.tags || [])
    setTagDraft("")
    setLeadUserId(initial.lead_user_id || "")
    setRepoUrl(initial.repo_url || "")
    setDefaultTeam(initial.default_team || "")
    setTargetDate(initial.target_date ? initial.target_date.slice(0, 10) : "")
    setError("")
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initial.project_id])

  // Lazy-load the user list so we can populate the lead-user picker.
  useEffect(() => {
    if (!open) return
    api.get("/users")
      .then((res) => setUsers(res.data || []))
      .catch(() => {})
  }, [open])

  if (!open) return null

  const addTag = () => {
    const t = tagDraft.trim().toLowerCase()
    if (!t) return
    if (tags.includes(t)) { setTagDraft(""); return }
    if (tags.length >= MAX_TAGS) return
    if (t.length > MAX_TAG) return
    setTags([...tags, t])
    setTagDraft("")
  }

  const removeTag = (t: string) => setTags(tags.filter((x) => x !== t))

  const submit = async () => {
    if (!name.trim()) {
      setError("Name is required.")
      return
    }
    setSubmitting(true)
    setError("")
    try {
      // Only send fields the user can actually edit through this modal;
      // PATCH treats any omitted field as "leave alone."
      const body: Record<string, unknown> = {
        name: name.trim(),
        description: description.trim(),
        color,
        icon,
        tags,
        lead_user_id: leadUserId || null,
        repo_url: repoUrl.trim(),
        default_team: defaultTeam || null,
        target_date: targetDate || null,
      }
      await api.patch(`/projects/${initial.project_id}`, body)
      invalidateProjectsCache()
      onSaved()
      onClose()
    } catch (e: any) {
      // Mirror the error-parsing trick from Projects.tsx delete handler —
      // FastAPI 4xx detail comes through the api client as "<status>: <body>".
      let msg = e?.message || "Save failed"
      try {
        const colon = msg.indexOf(":")
        if (colon >= 0) {
          const parsed = JSON.parse(msg.slice(colon + 1).trim())
          const detail = parsed?.detail ?? parsed
          msg = typeof detail === "object"
            ? `${detail.error || "Cannot save"} — ${detail.hint || JSON.stringify(detail)}`.trim()
            : String(detail)
        }
      } catch { /* keep raw */ }
      setError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div style={overlayStyle} onClick={onClose}>
      <div style={modalStyle} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>
            Edit Project
          </h3>
          <button onClick={onClose} style={{
            background: "transparent", border: "none", color: "var(--text-muted)",
            cursor: "pointer", padding: 4,
          }}>
            <X size={16} />
          </button>
        </div>

        <Group title="Identity">
          <Field label="Name *" hint={`${name.length}/${MAX_NAME}`}>
            <input
              type="text" value={name}
              onChange={(e) => setName(e.target.value.slice(0, MAX_NAME))}
              style={inputStyle}
            />
          </Field>
          <Field label="Description" hint={`${description.length}/${MAX_DESC}`}>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value.slice(0, MAX_DESC))}
              rows={2}
              style={{ ...inputStyle, resize: "vertical", minHeight: 50 }}
            />
          </Field>
          <Field label="Color">
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {COLORS.map((c) => (
                <button key={c} type="button" onClick={() => setColor(c)}
                  style={{
                    width: 26, height: 26, borderRadius: 4,
                    background: c, cursor: "pointer",
                    border: color === c ? "2px solid var(--text-primary)" : "1px solid var(--border)",
                  }}
                  title={c}
                />
              ))}
            </div>
          </Field>
          <Field label="Icon">
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {ICON_DEFS.map(({ id, Comp }) => (
                <button key={id} type="button" onClick={() => setIcon(id)}
                  style={{
                    width: 32, height: 32, borderRadius: 4,
                    display: "inline-flex", alignItems: "center", justifyContent: "center",
                    background: icon === id ? "var(--bg-hover)" : "transparent",
                    border: icon === id ? `2px solid ${color}` : "1px solid var(--border)",
                    color: icon === id ? color : "var(--text-secondary)",
                    cursor: "pointer",
                  }}
                  title={id}
                >
                  <Comp size={16} />
                </button>
              ))}
            </div>
          </Field>
          <Field label="Tags" hint={`${tags.length}/${MAX_TAGS}`}>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 6 }}>
              {tags.map((t) => (
                <span key={t} style={{
                  display: "inline-flex", alignItems: "center", gap: 4,
                  padding: "2px 6px 2px 8px", borderRadius: 4, fontSize: 11,
                  background: "var(--bg-hover)", color: "var(--text-secondary)",
                  fontFamily: "var(--font-mono)",
                }}>
                  {t}
                  <button type="button" onClick={() => removeTag(t)} style={{
                    background: "transparent", border: "none", color: "var(--text-muted)",
                    cursor: "pointer", padding: 0, display: "inline-flex",
                  }}>
                    <X size={10} />
                  </button>
                </span>
              ))}
            </div>
            <input
              type="text" value={tagDraft} placeholder="Type a tag, press Enter"
              onChange={(e) => setTagDraft(e.target.value.slice(0, MAX_TAG))}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addTag() }
              }}
              style={inputStyle}
              disabled={tags.length >= MAX_TAGS}
            />
          </Field>
        </Group>

        <Group title="Ownership & Integration">
          <Field label="Lead user">
            <select
              value={leadUserId}
              onChange={(e) => setLeadUserId(e.target.value)}
              style={inputStyle}
            >
              <option value="">(none)</option>
              {users.map((u) => (
                <option key={u.user_id} value={u.user_id}>
                  {u.username} ({u.role})
                </option>
              ))}
            </select>
          </Field>
          <Field label="Repository URL" hint="GitHub URL or any HTTPS link">
            <input
              type="url" value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
              placeholder="https://github.com/org/repo"
              style={inputStyle}
            />
          </Field>
        </Group>

        <Group title="Workflow Defaults">
          <Field label="Default team" hint="Pre-fills the team picker on the New Request form when this project is selected">
            <select value={defaultTeam} onChange={(e) => setDefaultTeam(e.target.value)} style={inputStyle}>
              <option value="">(no default)</option>
              <option value="engineering">engineering</option>
              <option value="research">research</option>
              <option value="content">content</option>
            </select>
          </Field>
          <Field label="Target date" hint="Optional — UI uses this for the 'Overdue' badge">
            <input
              type="date" value={targetDate}
              onChange={(e) => setTargetDate(e.target.value)}
              style={inputStyle}
            />
          </Field>
        </Group>

        {error && (
          <div style={{
            marginTop: 12, padding: "8px 10px", borderRadius: "var(--radius)",
            fontSize: 12, color: "var(--danger)",
            background: "var(--danger-subtle)", border: "1px solid var(--danger)",
          }}>
            {error}
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
          <button type="button" onClick={onClose} style={btnSecondary}>
            Cancel
          </button>
          <button type="button" onClick={submit} disabled={submitting || !name.trim()} style={btnPrimary}>
            <span>{submitting ? "Saving…" : "Save Changes"}</span>
          </button>
        </div>
      </div>
    </div>
  )
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{
        fontSize: 10, fontWeight: 700, color: "var(--text-muted)",
        textTransform: "uppercase", letterSpacing: 1, marginBottom: 8,
      }}>{title}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {children}
      </div>
    </div>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
        <label style={{ fontSize: 12, color: "var(--text-secondary)" }}>{label}</label>
        {hint && <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{hint}</span>}
      </div>
      {children}
    </div>
  )
}

const overlayStyle: React.CSSProperties = {
  position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)",
  display: "flex", alignItems: "flex-start", justifyContent: "center",
  zIndex: 1000, padding: "60px 20px 20px", overflowY: "auto",
}
const modalStyle: React.CSSProperties = {
  background: "var(--bg-card)", border: "1px solid var(--border)",
  borderRadius: "var(--radius)", padding: 24, width: "100%", maxWidth: 560,
  fontFamily: "var(--font)",
}
const inputStyle: React.CSSProperties = {
  width: "100%", padding: "6px 10px", fontSize: 13,
  background: "var(--bg-input, var(--bg-hover))",
  color: "var(--text-primary)",
  border: "1px solid var(--border)", borderRadius: "var(--radius)",
  fontFamily: "inherit", boxSizing: "border-box",
  outline: "none",
}
const btnSecondary: React.CSSProperties = {
  padding: "6px 14px", fontSize: 12, fontWeight: 600,
  background: "transparent", color: "var(--text-secondary)",
  border: "1px solid var(--border)", borderRadius: "var(--radius)",
  cursor: "pointer", fontFamily: "inherit",
}
const btnPrimary: React.CSSProperties = {
  padding: "6px 14px", fontSize: 12, fontWeight: 700,
  background: "var(--accent)", color: "#0a0014",
  border: "1px solid var(--accent)", borderRadius: "var(--radius)",
  cursor: "pointer", fontFamily: "inherit",
  display: "inline-flex", alignItems: "center", gap: 6,
  whiteSpace: "nowrap", lineHeight: 1,
}
