/**
 * CreateProjectModal — used both from the Projects list page "+ New Project"
 * button (PM-32) and inline from the New Request form's project dropdown
 * (PM-39). Same modal, same fields, same validation.
 *
 * All 10 v1 form fields are here in one file:
 *   - Identity: name *, description, color (8 swatches), icon (8 lucide),
 *     tags (≤10, ≤25 chars each)
 *   - Ownership: lead user dropdown, repo URL
 *   - Workflow defaults: default team (engineering/research/content/none),
 *     target date, template
 *
 * Sub-pickers are inline so the whole modal lives in one file — they're
 * not reusable elsewhere and splitting them into 5 separate files would
 * add noise without value.
 *
 * On submit: POST /api/v1/projects → calls onCreated(project) on success
 * so the caller can update its local state (select in dropdown, refresh
 * list, etc.) without a page reload.
 */

import { useEffect, useState } from "react"
import {
  Folder, Rocket, Layers, Code, FlaskConical, Palette, Bug, BookOpen,
  X, Plus,
} from "lucide-react"
import { api } from "../../lib/api"

// Closed-set palettes — must match src/models/base.py PROJECT_COLOR_PALETTE
// and PROJECT_ICON_SET exactly, or the backend rejects the value.
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

interface ProjectTemplate {
  id: string
  name: string
  description: string
}
interface UserOption { user_id: string; username: string; role: string }

export interface CreatedProject {
  project_id: string
  name: string
  color: string
  icon: string
  default_team: string | null
}

interface Props {
  open: boolean
  onClose: () => void
  onCreated: (project: CreatedProject) => void
}

export function CreateProjectModal({ open, onClose, onCreated }: Props) {
  // Identity
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [color, setColor] = useState(COLORS[0])
  const [icon, setIcon] = useState<string>(ICON_DEFS[0].id)
  const [tags, setTags] = useState<string[]>([])
  const [tagDraft, setTagDraft] = useState("")
  // Ownership
  const [leadUserId, setLeadUserId] = useState<string>("")
  const [repoUrl, setRepoUrl] = useState("")
  // Workflow defaults
  const [defaultTeam, setDefaultTeam] = useState<string>("")  // "" = none
  const [targetDate, setTargetDate] = useState("")  // YYYY-MM-DD
  const [templateId, setTemplateId] = useState<string>("empty")
  // Async data
  const [users, setUsers] = useState<UserOption[]>([])
  const [templates, setTemplates] = useState<ProjectTemplate[]>([])
  // Status
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")

  // Reset everything when modal opens; fetch dropdown data.
  useEffect(() => {
    if (!open) return
    setName("")
    setDescription("")
    setColor(COLORS[0])
    setIcon(ICON_DEFS[0].id)
    setTags([])
    setTagDraft("")
    setLeadUserId("")
    setRepoUrl("")
    setDefaultTeam("")
    setTargetDate("")
    setTemplateId("empty")
    setError("")
    setSubmitting(false)
    // Fetch users and templates in parallel.
    Promise.allSettled([
      api.get("/users").then((r) => setUsers(
        (r.data || []).filter((u: any) => u.role === "developer" || u.role === "admin")
      )),
      api.get("/projects/templates").then((r) => setTemplates(r.data || [])),
    ])
  }, [open])

  if (!open) return null

  const addTag = () => {
    const t = tagDraft.trim().toLowerCase()
    if (!t) return
    if (t.length > MAX_TAG) { setError(`Tag too long (max ${MAX_TAG} chars)`); return }
    if (tags.includes(t)) { setTagDraft(""); return }
    if (tags.length >= MAX_TAGS) { setError(`At most ${MAX_TAGS} tags`); return }
    setTags([...tags, t])
    setTagDraft("")
    setError("")
  }

  const submit = async () => {
    setError("")
    const trimmed = name.trim()
    if (!trimmed) { setError("Name is required"); return }
    if (trimmed.length > MAX_NAME) { setError(`Name too long (max ${MAX_NAME})`); return }
    setSubmitting(true)
    try {
      const body: any = {
        name: trimmed,
        description: description.trim() || undefined,
        color,
        icon,
        tags: tags.length > 0 ? tags : undefined,
        lead_user_id: leadUserId || undefined,
        repo_url: repoUrl.trim() || undefined,
        default_team: defaultTeam || undefined,
        target_date: targetDate || undefined,
        template_id: templateId || undefined,
      }
      const res = await api.post("/projects", body)
      onCreated({
        project_id: res.data.project_id,
        name: res.data.name,
        color: res.data.color,
        icon: res.data.icon,
        default_team: res.data.default_team,
      })
      onClose()
    } catch (e: any) {
      // The backend serializes detail as either a string or {error, ...}.
      const msg = e?.message || String(e)
      const m = msg.match(/^\d+:\s*(.+)$/)
      try {
        const parsed = m ? JSON.parse(m[1]) : null
        if (parsed?.detail) {
          setError(typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail))
        } else {
          setError(msg)
        }
      } catch {
        setError(msg)
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      // Backdrop
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 10001,
        background: "rgba(0,0,0,0.65)",
        display: "flex", alignItems: "flex-start", justifyContent: "center",
        overflowY: "auto", padding: "60px 20px",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "100%", maxWidth: 560,
          background: "var(--bg-card)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          color: "var(--text-primary)",
          fontFamily: "var(--font)",
        }}
      >
        <div style={{
          padding: "14px 20px", borderBottom: "1px solid var(--border)",
          display: "flex", alignItems: "center", justifyContent: "space-between",
        }}>
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>
            New Project
          </h2>
          <button onClick={onClose} aria-label="Close"
            style={{
              background: "transparent", border: "none", cursor: "pointer",
              color: "var(--text-secondary)", padding: 4,
            }}>
            <X size={18} />
          </button>
        </div>

        <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 16 }}>
          {/* ── Identity ── */}
          <Group title="Identity">
            <Field label="Name *" hint={`${name.length} / ${MAX_NAME}`}>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value.slice(0, MAX_NAME))}
                placeholder="Themes UI Redesign"
                style={inputStyle}
              />
            </Field>

            <Field label="Description" hint={`${description.length} / ${MAX_DESC}`}>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value.slice(0, MAX_DESC))}
                placeholder="What is this project about?"
                rows={3}
                style={{ ...inputStyle, resize: "vertical", minHeight: 70 }}
              />
            </Field>

            <Field label="Color">
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {COLORS.map((c) => (
                  <button key={c} type="button" onClick={() => setColor(c)}
                    aria-pressed={color === c}
                    title={c}
                    style={{
                      width: 26, height: 26, borderRadius: 4,
                      background: c, cursor: "pointer",
                      border: color === c ? "2px solid var(--text-primary)" : "1px solid var(--border)",
                      outline: color === c ? "1px solid " + c : "none",
                      outlineOffset: 2,
                    }} />
                ))}
              </div>
            </Field>

            <Field label="Icon">
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {ICON_DEFS.map(({ id, Comp }) => (
                  <button key={id} type="button" onClick={() => setIcon(id)}
                    aria-pressed={icon === id}
                    title={id}
                    style={{
                      width: 32, height: 32, borderRadius: 4,
                      display: "inline-flex", alignItems: "center", justifyContent: "center",
                      background: icon === id ? "var(--bg-hover)" : "transparent",
                      border: icon === id
                        ? `2px solid ${color}`
                        : "1px solid var(--border)",
                      cursor: "pointer", color: "var(--text-primary)",
                    }}>
                    <Comp size={16} />
                  </button>
                ))}
              </div>
            </Field>

            <Field label="Tags" hint={`${tags.length} / ${MAX_TAGS}`}>
              <div style={{
                display: "flex", flexWrap: "wrap", gap: 4, alignItems: "center",
                padding: "6px 8px", border: "1px solid var(--border)",
                background: "var(--bg-input, var(--bg-card))",
                borderRadius: "var(--radius)",
              }}>
                {tags.map((t) => (
                  <span key={t} style={{
                    display: "inline-flex", alignItems: "center", gap: 4,
                    padding: "2px 8px", borderRadius: 4, fontSize: 11,
                    background: "var(--bg-hover)", color: "var(--accent)",
                    border: "1px solid var(--border)",
                  }}>
                    {t}
                    <button type="button"
                      onClick={() => setTags(tags.filter((x) => x !== t))}
                      aria-label={`Remove tag ${t}`}
                      style={{ background: "transparent", border: "none", cursor: "pointer", color: "inherit", padding: 0, display: "inline-flex" }}>
                      <X size={11} />
                    </button>
                  </span>
                ))}
                <input
                  value={tagDraft}
                  onChange={(e) => setTagDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === ",") {
                      e.preventDefault()
                      addTag()
                    } else if (e.key === "Backspace" && !tagDraft && tags.length > 0) {
                      setTags(tags.slice(0, -1))
                    }
                  }}
                  placeholder={tags.length === 0 ? "type, press Enter…" : ""}
                  style={{
                    flex: 1, minWidth: 100, border: "none", outline: "none",
                    background: "transparent", color: "var(--text-primary)",
                    fontFamily: "inherit", fontSize: 12,
                  }}
                />
              </div>
            </Field>
          </Group>

          {/* ── Ownership ── */}
          <Group title="Ownership">
            <Field label="Lead">
              <select value={leadUserId}
                onChange={(e) => setLeadUserId(e.target.value)}
                style={inputStyle}>
                <option value="">— (you, by default) —</option>
                {users.map((u) => (
                  <option key={u.user_id} value={u.user_id}>{u.username} ({u.role})</option>
                ))}
              </select>
            </Field>

            <Field label="GitHub Repo URL" hint="optional">
              <input type="url"
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                placeholder="https://github.com/owner/repo"
                style={inputStyle} />
            </Field>
          </Group>

          {/* ── Workflow defaults ── */}
          <Group title="Workflow defaults">
            <Field label="Default team for requests">
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap", fontSize: 12 }}>
                {[
                  { v: "", label: "No default" },
                  { v: "engineering", label: "Engineering" },
                  { v: "research", label: "Research" },
                  { v: "content", label: "Content" },
                ].map(({ v, label }) => (
                  <label key={v} style={{ display: "inline-flex", alignItems: "center", gap: 4, cursor: "pointer", color: "var(--text-primary)" }}>
                    <input type="radio" name="defaultTeam"
                      value={v} checked={defaultTeam === v}
                      onChange={(e) => setDefaultTeam(e.target.value)} />
                    {label}
                  </label>
                ))}
              </div>
            </Field>

            <Field label="Target completion date" hint="optional">
              <input type="date"
                value={targetDate}
                min={new Date().toISOString().slice(0, 10)}
                onChange={(e) => setTargetDate(e.target.value)}
                style={inputStyle} />
            </Field>

            <Field label="Template">
              <select value={templateId}
                onChange={(e) => setTemplateId(e.target.value)}
                style={inputStyle}>
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name} {t.description ? `— ${t.description}` : ""}
                  </option>
                ))}
              </select>
            </Field>
          </Group>

          {error && (
            <div style={{
              padding: "8px 12px", borderRadius: "var(--radius)",
              border: "1px solid var(--danger)",
              background: "rgba(255, 59, 59, 0.08)",
              color: "var(--danger)", fontSize: 12,
            }}>
              {error}
            </div>
          )}
        </div>

        <div style={{
          padding: "12px 20px", borderTop: "1px solid var(--border)",
          display: "flex", justifyContent: "flex-end", gap: 8,
        }}>
          <button type="button" onClick={onClose} disabled={submitting} style={btnSecondary}>
            Cancel
          </button>
          <button type="button" onClick={submit} disabled={submitting || !name.trim()} style={btnPrimary}>
            <Plus size={14} />
            <span>{submitting ? "Creating…" : "Create"}</span>
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Small layout helpers ─────────────────────────────────────────────────

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{
        fontSize: 10, fontWeight: 700, letterSpacing: 1,
        textTransform: "uppercase", color: "var(--text-muted)",
      }}>
        {title}
      </div>
      {children}
    </div>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "baseline",
        fontSize: 11, color: "var(--text-secondary)",
      }}>
        <span>{label}</span>
        {hint && <span style={{ color: "var(--text-muted)", fontSize: 10 }}>{hint}</span>}
      </div>
      {children}
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "6px 10px",
  fontSize: 13,
  background: "var(--bg-input, var(--bg-card))",
  color: "var(--text-primary)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  fontFamily: "inherit",
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
  // Keep icon + label on a single line — cyberpunk button styling adds
  // enough padding/glow that the text wraps under the icon otherwise.
  display: "inline-flex", alignItems: "center", gap: 6,
  whiteSpace: "nowrap", lineHeight: 1,
}
