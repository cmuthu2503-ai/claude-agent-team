/**
 * BuildWorkspace — Project-driven Build, Phase A (PDB-09 → PDB-12).
 *
 * State-driven panel that lives on the Project Detail page above the
 * existing Requests list. Renders one of five sub-views depending on
 * which artifacts exist for this project:
 *
 *   1. No brief                 → brief textarea + "Generate PRD" (disabled until ≥50 chars)
 *   2. Brief exists, no PRD     → brief read-only + Edit Brief + Generate PRD
 *   3. PRD draft                → markdown editor (textarea + preview) + Regenerate / Save Draft / Finalize PRD
 *   4. PRD finalized, no tasks  → PRD read-only (collapsible) + "Generate Task List" placeholder
 *   5. Tasks present            → placeholder for Phase B
 *
 * State transitions are atomic: re-fetch the artifacts from the server
 * after every write, render whichever sub-view matches.
 */

import { useEffect, useState } from "react"
import { ChevronDown, ChevronRight, FileText, Sparkles, CheckCircle2 } from "lucide-react"
import { api } from "../../lib/api"
import { MarkdownRenderer } from "../ui/MarkdownRenderer"
import { TaskListEditor } from "./TaskListEditor"
import { BuildChatPanel } from "./BuildChatPanel"

interface Artifact {
  artifact_id: string
  project_id: string
  kind: "brief" | "prd"
  version: number
  status: "draft" | "finalized" | "archived"
  content: string
  created_at: string
  finalized_at: string | null
}

interface Props {
  projectId: string
}

const BRIEF_MIN = 50
const BRIEF_MAX = 4000

export function BuildWorkspace({ projectId }: Props) {
  const [brief, setBrief] = useState<Artifact | null>(null)
  const [prd, setPrd] = useState<Artifact | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  const load = async () => {
    try {
      // Both calls 404 cleanly when the artifact doesn't exist yet; we
      // tolerate that so the panel can render "no brief" / "no PRD" states.
      const [briefRes, prdRes] = await Promise.all([
        api.get(`/projects/${projectId}/brief`).catch(() => null),
        api.get(`/projects/${projectId}/prd`).catch(() => null),
      ])
      setBrief(briefRes?.data || null)
      setPrd(prdRes?.data || null)
      setLoading(false)
    } catch (e: any) {
      setError(e?.message || "Failed to load workspace")
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  if (loading) {
    return (
      <Card>
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>Loading workspace…</div>
      </Card>
    )
  }
  if (error) {
    return (
      <Card>
        <div style={{ color: "var(--danger)", fontSize: 13 }}>{error}</div>
      </Card>
    )
  }

  // ── State router ──────────────────────────────────────────────────
  if (!brief) {
    return <BriefEditor projectId={projectId} initial="" onSaved={load} />
  }
  if (!prd) {
    return <BriefReady brief={brief} projectId={projectId} onPRDGenerated={load} onEdit={() => setBrief({ ...brief, content: brief.content })} reload={load} />
  }
  if (prd.status === "draft") {
    return <PRDEditor prd={prd} projectId={projectId} onUpdated={load} />
  }
  if (prd.status === "finalized") {
    return <PRDFinalized prd={prd} brief={brief} projectId={projectId} reload={load} />
  }
  // Archived PRD with no current draft — shouldn't normally happen since we
  // always create a new draft when re-generating; show a regen affordance.
  return <PRDFinalized prd={prd} brief={brief} projectId={projectId} reload={load} />
}

// ── Sub-view 1: brief editor (no brief yet OR explicit "Edit Brief") ──

function BriefEditor({
  projectId, initial, onSaved,
}: { projectId: string; initial: string; onSaved: () => void }) {
  const [content, setContent] = useState(initial)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState("")
  const len = content.trim().length
  const tooShort = len < BRIEF_MIN
  const tooLong = len > BRIEF_MAX

  const save = async () => {
    setSaving(true)
    setErr("")
    try {
      await api.put(`/projects/${projectId}/brief`, { content: content.trim() })
      onSaved()
    } catch (e: any) {
      setErr(parseDetail(e?.message) || "Save failed")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <Heading icon={<Sparkles size={16} />}>Build Workspace · Project Brief</Heading>
      <p style={{ margin: "6px 0 12px 0", fontSize: 12, color: "var(--text-muted)" }}>
        Write a short brief (1–3 paragraphs) so the agent has context to draft a PRD.
      </p>
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value.slice(0, BRIEF_MAX))}
        placeholder="Describe what this project is for, who uses it, the rough shape of the build, and any constraints. The PRD agent will turn this into a full requirements doc."
        rows={6}
        style={{
          width: "100%",
          padding: "10px 12px",
          fontSize: 13,
          fontFamily: "var(--font)",
          color: "var(--text-primary)",
          background: "var(--bg-hover)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          resize: "vertical",
          minHeight: 120,
          outline: "none",
          boxSizing: "border-box",
        }}
      />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 8 }}>
        <span style={{ fontSize: 11, color: tooShort || tooLong ? "var(--danger)" : "var(--text-muted)" }}>
          {len}/{BRIEF_MAX} characters · minimum {BRIEF_MIN}
        </span>
        <button
          type="button"
          onClick={save}
          disabled={saving || tooShort || tooLong}
          style={primaryBtn(saving || tooShort || tooLong)}
        >
          {saving ? "Saving…" : "Save Brief"}
        </button>
      </div>
      {err && <ErrorBanner>{err}</ErrorBanner>}
    </Card>
  )
}

// ── Sub-view 2: brief exists, no PRD yet ─────────────────────────────

function BriefReady({
  brief, projectId, onPRDGenerated, reload,
}: {
  brief: Artifact
  projectId: string
  onPRDGenerated: () => void
  onEdit: () => void
  reload: () => void
}) {
  const [generating, setGenerating] = useState(false)
  const [err, setErr] = useState("")
  const [editingBrief, setEditingBrief] = useState(false)

  const generate = async () => {
    setGenerating(true)
    setErr("")
    try {
      await api.post(`/projects/${projectId}/prd/generate`, {})
      onPRDGenerated()
    } catch (e: any) {
      setErr(parseDetail(e?.message) || "PRD generation failed")
    } finally {
      setGenerating(false)
    }
  }

  if (editingBrief) {
    return (
      <BriefEditor
        projectId={projectId}
        initial={brief.content}
        onSaved={() => { setEditingBrief(false); reload() }}
      />
    )
  }

  return (
    <Card>
      <Heading icon={<FileText size={16} />}>Build Workspace · Brief Ready</Heading>
      <div style={{
        marginTop: 8, padding: "10px 12px",
        background: "var(--bg-hover)", border: "1px solid var(--border)",
        borderRadius: "var(--radius)", fontSize: 13, color: "var(--text-secondary)",
        whiteSpace: "pre-wrap", lineHeight: 1.5,
      }}>
        {brief.content}
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button type="button" onClick={() => setEditingBrief(true)} style={secondaryBtn(false)}>
          Edit Brief
        </button>
        <button type="button" onClick={generate} disabled={generating} style={primaryBtn(generating)}>
          {generating ? "Generating PRD… (up to 90s)" : "Generate PRD →"}
        </button>
      </div>
      {err && <ErrorBanner>{err}</ErrorBanner>}
    </Card>
  )
}

// ── Sub-view 3: PRD draft (markdown editor + preview) ────────────────

function PRDEditor({
  prd, projectId, onUpdated,
}: { prd: Artifact; projectId: string; onUpdated: () => void }) {
  const [content, setContent] = useState(prd.content)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState<"saving" | "regen" | "finalizing" | null>(null)
  const [tab, setTab] = useState<"edit" | "preview" | "split">("split")
  const [err, setErr] = useState("")

  // Pick up new content if the parent re-fetched (e.g. after regen).
  // Don't overwrite local edits — only resync when the artifact_id changes
  // (i.e. a regen produced a new version row).
  useEffect(() => {
    setContent(prd.content)
    setDirty(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prd.artifact_id])

  const save = async () => {
    setBusy("saving")
    setErr("")
    try {
      await api.patch(`/projects/${projectId}/prd`, { content })
      setDirty(false)
      onUpdated()
    } catch (e: any) {
      setErr(parseDetail(e?.message) || "Save failed")
    } finally {
      setBusy(null)
    }
  }

  const regenerate = async () => {
    if (dirty && !window.confirm("Regenerate will discard your unsaved edits. Continue?")) return
    setBusy("regen")
    setErr("")
    try {
      await api.post(`/projects/${projectId}/prd/generate`, {})
      onUpdated()
    } catch (e: any) {
      setErr(parseDetail(e?.message) || "Regenerate failed")
    } finally {
      setBusy(null)
    }
  }

  const finalize = async () => {
    if (dirty) {
      if (!window.confirm("You have unsaved edits. Save and finalize?")) return
      try {
        await api.patch(`/projects/${projectId}/prd`, { content })
      } catch (e: any) {
        setErr(parseDetail(e?.message) || "Save failed before finalize")
        return
      }
    }
    if (!window.confirm("Finalize this PRD? You can still regenerate later, but downstream stages (task list) will use this version as input.")) return
    setBusy("finalizing")
    setErr("")
    try {
      await api.patch(`/projects/${projectId}/prd`, { status: "finalized" })
      onUpdated()
    } catch (e: any) {
      setErr(parseDetail(e?.message) || "Finalize failed")
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <Heading icon={<FileText size={16} />}>
          Build Workspace · PRD Draft (v{prd.version})
        </Heading>
        <div style={{ display: "flex", gap: 4, padding: 2, background: "var(--bg-hover)", borderRadius: "var(--radius)" }}>
          {(["edit", "split", "preview"] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              style={{
                padding: "3px 10px", fontSize: 11, fontWeight: 600,
                background: tab === t ? "var(--bg-card)" : "transparent",
                color: tab === t ? "var(--text-primary)" : "var(--text-muted)",
                border: "1px solid " + (tab === t ? "var(--border)" : "transparent"),
                borderRadius: "var(--radius)",
                cursor: "pointer", textTransform: "capitalize",
                fontFamily: "var(--font)",
              }}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      <div style={{
        marginTop: 10,
        display: tab === "split" ? "grid" : "block",
        gridTemplateColumns: tab === "split" ? "1fr 1fr" : undefined,
        gap: 12,
        minHeight: 400,
      }}>
        {(tab === "edit" || tab === "split") && (
          <textarea
            value={content}
            onChange={(e) => { setContent(e.target.value); setDirty(true) }}
            placeholder="(empty PRD — regenerate or paste markdown here)"
            spellCheck={false}
            style={{
              width: "100%", minHeight: 400, padding: "10px 12px",
              fontSize: 12, fontFamily: "var(--font-mono)",
              color: "var(--text-primary)", background: "var(--bg-hover)",
              border: "1px solid var(--border)", borderRadius: "var(--radius)",
              resize: "vertical", outline: "none", boxSizing: "border-box",
              lineHeight: 1.5,
            }}
          />
        )}
        {(tab === "preview" || tab === "split") && (
          <div style={{
            minHeight: 400, padding: "12px 16px",
            background: "var(--bg-hover)", border: "1px solid var(--border)",
            borderRadius: "var(--radius)", overflow: "auto",
            maxHeight: 600,
          }}>
            {content.trim()
              ? <MarkdownRenderer content={content} />
              : <span style={{ color: "var(--text-muted)", fontSize: 12 }}>(nothing to preview)</span>}
          </div>
        )}
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center", flexWrap: "wrap" }}>
        <button
          type="button"
          onClick={regenerate}
          disabled={busy !== null}
          style={secondaryBtn(busy !== null)}
        >
          {busy === "regen" ? "Regenerating…" : "Regenerate"}
        </button>
        <button
          type="button"
          onClick={save}
          disabled={busy !== null || !dirty}
          style={secondaryBtn(busy !== null || !dirty)}
        >
          {busy === "saving" ? "Saving…" : dirty ? "Save Draft" : "Saved"}
        </button>
        <div style={{ flex: 1 }} />
        <button
          type="button"
          onClick={finalize}
          disabled={busy !== null || content.trim().length === 0}
          style={primaryBtn(busy !== null || content.trim().length === 0)}
        >
          {busy === "finalizing" ? "Finalizing…" : "Finalize PRD"}
        </button>
      </div>
      {err && <ErrorBanner>{err}</ErrorBanner>}
    </Card>
  )
}

// ── Sub-view 4: PRD finalized — read-only + placeholder for Phase B ──

function PRDFinalized({
  prd, brief: _brief, projectId, reload,
}: { prd: Artifact; brief: Artifact; projectId: string; reload: () => void }) {
  const [collapsed, setCollapsed] = useState(true)
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* PRD section (full-width, collapsible). */}
      <Card>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <Heading icon={<CheckCircle2 size={16} color="var(--success)" />}>
            Build Workspace · PRD Finalized (v{prd.version})
          </Heading>
          <button
            type="button"
            onClick={() => setCollapsed(!collapsed)}
            style={{
              display: "inline-flex", alignItems: "center", gap: 4,
              padding: "4px 10px", fontSize: 11,
              background: "transparent", color: "var(--text-muted)",
              border: "1px solid var(--border)", borderRadius: "var(--radius)",
              cursor: "pointer", fontFamily: "var(--font)",
            }}
          >
            {collapsed ? <ChevronRight size={11} /> : <ChevronDown size={11} />}
            {collapsed ? "Show PRD" : "Hide PRD"}
          </button>
        </div>
        {!collapsed && (
          <div style={{
            marginTop: 10, padding: "12px 16px",
            background: "var(--bg-hover)", border: "1px solid var(--border)",
            borderRadius: "var(--radius)", maxHeight: 500, overflow: "auto",
          }}>
            <MarkdownRenderer content={prd.content} />
          </div>
        )}
      </Card>

      {/* PDB-41 — two-column layout: chat (~40%) on the left, task list
          (~60%) on the right. Grid collapses to a single column under
          ~900px so narrow viewports stack cleanly. */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 2fr) minmax(0, 3fr)",
        gap: 12,
      }} className="pdb-workspace-grid">
        <div style={{ minWidth: 0 }}>
          <BuildChatPanel projectId={projectId} />
        </div>
        <div style={{ minWidth: 0 }}>
          {/* TaskListEditor renders its own card chrome (the inner Stub),
              so we don't wrap it in another Card. */}
          <TaskListEditor projectId={projectId} onFinalized={reload} />
        </div>
      </div>
      {/* Stack on narrow viewports. Inline <style> keeps the rule local to
          this surface so it doesn't bleed into other pages. */}
      <style>{`
        @media (max-width: 900px) {
          .pdb-workspace-grid { grid-template-columns: 1fr !important; }
        }
      `}</style>
    </div>
  )
}

// ── Shared primitives ────────────────────────────────────────────────

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      background: "var(--bg-card)", border: "1px solid var(--border)",
      borderRadius: "var(--radius)", padding: 18,
    }}>
      {children}
    </div>
  )
}

function Heading({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <h3 style={{
      margin: 0, fontSize: 14, fontWeight: 600, color: "var(--text-primary)",
      display: "inline-flex", alignItems: "center", gap: 6,
    }}>
      {icon}
      {children}
    </h3>
  )
}

function ErrorBanner({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      marginTop: 10, padding: "8px 10px", borderRadius: "var(--radius)",
      fontSize: 12, color: "var(--danger)",
      background: "var(--danger-subtle)", border: "1px solid var(--danger)",
    }}>
      {children}
    </div>
  )
}

function primaryBtn(disabled: boolean): React.CSSProperties {
  return {
    display: "inline-flex", alignItems: "center", gap: 6,
    padding: "6px 14px", fontSize: 12, fontWeight: 700,
    background: disabled ? "var(--bg-hover)" : "var(--accent)",
    color: disabled ? "var(--text-muted)" : "#0a0014",
    border: "1px solid " + (disabled ? "var(--border)" : "var(--accent)"),
    borderRadius: "var(--radius)",
    cursor: disabled ? "not-allowed" : "pointer",
    whiteSpace: "nowrap", lineHeight: 1, fontFamily: "var(--font)",
  }
}

function secondaryBtn(disabled: boolean): React.CSSProperties {
  return {
    display: "inline-flex", alignItems: "center", gap: 6,
    padding: "6px 14px", fontSize: 12, fontWeight: 600,
    background: "transparent",
    color: disabled ? "var(--text-muted)" : "var(--text-secondary)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius)",
    cursor: disabled ? "not-allowed" : "pointer",
    whiteSpace: "nowrap", lineHeight: 1, fontFamily: "var(--font)",
    opacity: disabled ? 0.6 : 1,
  }
}

// FastAPI errors arrive through the api client as Error("<status>: <body>").
// Pull the `detail` out so users see "Brief must be at least 50 characters"
// instead of `400: {"detail":"..."}`.
function parseDetail(msg: string | undefined): string | undefined {
  if (!msg) return undefined
  const colon = msg.indexOf(":")
  if (colon < 0) return msg
  try {
    const parsed = JSON.parse(msg.slice(colon + 1).trim())
    const d = parsed?.detail ?? parsed
    if (typeof d === "string") return d
    if (typeof d === "object" && d) return d.error || d.message || JSON.stringify(d)
    return msg
  } catch {
    return msg
  }
}
