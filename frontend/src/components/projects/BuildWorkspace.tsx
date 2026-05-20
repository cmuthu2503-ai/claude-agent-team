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
  updated_at: string | null
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
      <p style={{ margin: "6px 0 10px 0", fontSize: 12, color: "var(--text-muted)" }}>
        Write a short brief (1–3 paragraphs) so the agent has context to draft a PRD.
      </p>
      {/* 3-step roadmap so first-time users see the whole arc upfront,
          not just the textarea in front of them. Step 1 is the active one
          (this view); 2 and 3 are dimmed previews. */}
      <div style={{
        display: "flex", gap: 6, marginBottom: 14, flexWrap: "wrap",
        fontSize: 11, fontFamily: "var(--font-mono)",
      }}>
        <RoadmapStep n={1} label="Write Brief" active />
        <RoadmapArrow />
        <RoadmapStep n={2} label="Generate PRD" />
        <RoadmapArrow />
        <RoadmapStep n={3} label="Generate Tasks" />
        <RoadmapArrow />
        <RoadmapStep n={4} label="Dispatch + Chat" />
      </div>
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
  const [busy, setBusy] = useState<"saving" | "regen" | "finalizing" | "deleting" | null>(null)
  const [tab, setTab] = useState<"edit" | "preview" | "split">("split")
  const [err, setErr] = useState("")
  // Review comments for the next regeneration. Transient — cleared on
  // successful regen and never persisted between page loads (per spec).
  const [reviewComments, setReviewComments] = useState("")

  // Pick up new content if the parent re-fetched (e.g. after regen).
  // Don't overwrite local edits — only resync when the artifact_id changes
  // (i.e. a regen produced a new version row).
  useEffect(() => {
    setContent(prd.content)
    setDirty(false)
    setReviewComments("")
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
      // Send review comments along when present. Empty string is treated
      // the same as omitted on the server.
      const body = reviewComments.trim()
        ? { review_comments: reviewComments.trim() }
        : {}
      await api.post(`/projects/${projectId}/prd/generate`, body)
      setReviewComments("")  // clear on success; useEffect would also clear on artifact_id change
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
    if (!window.confirm("Finalize this PRD? You can still regenerate later, but downstream stages (task list) will use this version as input.\n\nThe PRD will also be written to C:/ai-projects/<ProjectName>/docs/PRD.md and pushed to your project's GitHub repo (if configured).")) return
    setBusy("finalizing")
    setErr("")
    try {
      const res = await api.patch(`/projects/${projectId}/prd`, { status: "finalized" })
      // Surface where the file landed + whether the GitHub push went
      // through. The backend returns `meta.host_write` and
      // `meta.github_push` regardless of success — soft-fail by design.
      const meta = (res as any)?.meta || {}
      const hw = meta.host_write
      const gh = meta.github_push
      const lines: string[] = ["PRD finalized."]
      if (hw?.ok) lines.push(`Saved to: ${hw.path}`)
      else if (hw?.error) lines.push(`Local write failed: ${hw.error}`)
      if (gh?.ok) lines.push(`Pushed to ${gh.repo} @ ${gh.short_sha}`)
      else if (gh?.skipped === "no_repo_url") lines.push("(No GitHub repo configured for this project — skipped push)")
      else if (gh?.error) lines.push(`GitHub push failed: ${gh.error}`)
      // Use alert for now — a toast component would be nicer but the
      // current codebase doesn't have one and this gives users
      // immediate, dismissible feedback.
      window.alert(lines.join("\n"))
      onUpdated()
    } catch (e: any) {
      setErr(parseDetail(e?.message) || "Finalize failed")
    } finally {
      setBusy(null)
    }
  }

  // Hard-delete the PRD — all versions + host file. Confirm twice when
  // the current version is finalized (since downstream tasks may
  // reference it). Tasks themselves aren't touched.
  const deletePrd = async () => {
    const isFinal = prd.status === "finalized"
    const msg = isFinal
      ? "DELETE this PRD entirely?\n\nThis removes ALL versions from the database AND deletes C:/ai-projects/<ProjectName>/docs/PRD.md from your disk.\n\nTask rows already generated from this PRD will stay, but their parent PRD will be gone. The project will return to the 'Generate PRD' starting state."
      : "DELETE this PRD draft and all prior versions?\n\nThis removes every PRD row from the database and deletes C:/ai-projects/<ProjectName>/docs/PRD.md from your disk."
    if (!window.confirm(msg)) return
    setBusy("deleting")
    setErr("")
    try {
      const res = await api.delete(`/projects/${projectId}/prd`)
      const meta = (res as any)?.meta || {}
      const lines: string[] = [`Deleted ${meta.deleted_versions ?? 0} PRD version(s) from the database.`]
      const hd = meta.host_delete
      if (hd?.ok && hd?.path) lines.push(`Removed file: ${hd.path}`)
      else if (hd && !hd.ok) lines.push(`Local file delete failed: ${hd.error}`)
      window.alert(lines.join("\n"))
      onUpdated()
    } catch (e: any) {
      setErr(parseDetail(e?.message) || "Delete failed")
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

      {/* Review comments for the next regeneration. Sits between the
          editor and the toolbar so it's visually anchored to "Regenerate".
          Transient — clears on successful regen, never persisted. */}
      <div style={{
        marginTop: 12, padding: "10px 12px",
        background: "var(--bg-hover)", border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
      }}>
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          marginBottom: 6, fontSize: 11, color: "var(--text-muted)",
        }}>
          <span>
            Review comments for next regeneration <em>(optional)</em>
          </span>
          <span style={{
            fontFamily: "var(--font-mono)",
            color: reviewComments.length > 2000 ? "var(--danger)" : "var(--text-muted)",
          }}>
            {reviewComments.length}/2000
          </span>
        </div>
        <textarea
          value={reviewComments}
          onChange={(e) => setReviewComments(e.target.value.slice(0, 2000))}
          placeholder={
            'e.g. "Remove section 9. Add rate limiting under Phase 5. ' +
            'Shorten the data model to just the new tables."'
          }
          rows={3}
          disabled={busy !== null}
          style={{
            width: "100%", padding: "8px 10px", fontSize: 12,
            fontFamily: "var(--font)", lineHeight: 1.4,
            color: "var(--text-primary)", background: "var(--bg-card)",
            border: "1px solid var(--border)", borderRadius: "var(--radius)",
            resize: "vertical", minHeight: 60, outline: "none",
            boxSizing: "border-box",
          }}
        />
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center", flexWrap: "wrap" }}>
        <button
          type="button"
          onClick={regenerate}
          disabled={busy !== null}
          style={secondaryBtn(busy !== null)}
          title={
            reviewComments.trim()
              ? "Regenerate the PRD using your review comments — keeps unaffected sections intact."
              : "Regenerate a fresh PRD from the brief (discards all current content)."
          }
        >
          {busy === "regen"
            ? "Regenerating…"
            : reviewComments.trim()
              ? "Regenerate with comments"
              : "Regenerate"}
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
          onClick={deletePrd}
          disabled={busy !== null}
          style={dangerBtn(busy !== null)}
          title="Delete this PRD entirely (all versions) and remove the host-side PRD.md file. Returns the project to the 'Generate PRD' starting state."
        >
          {busy === "deleting" ? "Deleting…" : "Delete PRD"}
        </button>
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
  prd, brief, projectId, reload,
}: { prd: Artifact; brief: Artifact; projectId: string; reload: () => void }) {
  const [collapsed, setCollapsed] = useState(true)
  const [deleting, setDeleting] = useState(false)
  const [deleteErr, setDeleteErr] = useState("")

  // Hard-delete the finalized PRD. Heavier confirmation than the
  // draft version because there may be downstream tasks / dispatches
  // already referencing this PRD's contents.
  const deletePrd = async () => {
    const ok = window.confirm(
      "DELETE this FINALIZED PRD entirely?\n\n" +
      "• Removes ALL versions (drafts + finalized + archived) from the database\n" +
      "• Deletes C:/ai-projects/<ProjectName>/docs/PRD.md from your disk\n" +
      "• Tasks already generated from this PRD will remain but lose their parent\n" +
      "• The project returns to the 'Generate PRD' starting state\n\n" +
      "This cannot be undone."
    )
    if (!ok) return
    setDeleting(true)
    setDeleteErr("")
    try {
      const res = await api.delete(`/projects/${projectId}/prd`)
      const meta = (res as any)?.meta || {}
      const lines: string[] = [`Deleted ${meta.deleted_versions ?? 0} PRD version(s) from the database.`]
      const hd = meta.host_delete
      if (hd?.ok && hd?.path) lines.push(`Removed file: ${hd.path}`)
      else if (hd && !hd.ok) lines.push(`Local file delete failed: ${hd.error}`)
      window.alert(lines.join("\n"))
      reload()
    } catch (e: any) {
      const msg = parseDetail(e?.message) || "Delete failed"
      setDeleteErr(msg)
    } finally {
      setDeleting(false)
    }
  }
  // PDB-43: brief-changed-since-PRD-finalized banner. Fires only when both
  // timestamps exist AND the brief was touched strictly later than the
  // finalize timestamp. Non-blocking — just a hint to regenerate.
  const briefStale =
    brief.updated_at != null &&
    prd.finalized_at != null &&
    new Date(brief.updated_at) > new Date(prd.finalized_at)
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {briefStale && (
        <div style={{
          padding: "10px 14px", borderRadius: "var(--radius)",
          background: "var(--warning-subtle, rgba(255, 200, 0, 0.1))",
          border: "1px solid var(--warning, #d4a017)",
          color: "var(--warning, #d4a017)",
          display: "flex", alignItems: "center", gap: 10, fontSize: 12,
        }}>
          <Sparkles size={14} />
          <span style={{ flex: 1, color: "var(--text-primary)" }}>
            The brief has been edited since this PRD was finalized — the PRD may be stale.
          </span>
          <a
            href={`/projects/${projectId}`}
            onClick={(e) => { e.preventDefault(); window.location.reload() }}
            style={{
              fontSize: 11, color: "var(--accent)", textDecoration: "none",
              padding: "3px 10px", border: "1px solid var(--accent)",
              borderRadius: "var(--radius)",
            }}
          >
            Refresh
          </a>
        </div>
      )}
      {/* Build Chat comes first — talking to the orchestrator is the
          primary action on this page. PRD + tasks are reference /
          editable context below it. */}
      <BuildChatPanel projectId={projectId} />
      {/* PRD section (full-width, collapsible). */}
      <Card>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <Heading icon={<CheckCircle2 size={16} color="var(--success)" />}>
            Build Workspace · PRD Finalized (v{prd.version})
          </Heading>
          <div style={{ display: "inline-flex", gap: 6 }}>
            <button
              type="button"
              onClick={deletePrd}
              disabled={deleting}
              style={{
                display: "inline-flex", alignItems: "center", gap: 4,
                padding: "4px 10px", fontSize: 11,
                background: "transparent",
                color: deleting ? "var(--text-muted)" : "var(--danger)",
                border: "1px solid " + (deleting ? "var(--border)" : "var(--danger)"),
                borderRadius: "var(--radius)",
                cursor: deleting ? "not-allowed" : "pointer",
                fontFamily: "var(--font)",
                opacity: deleting ? 0.6 : 1,
              }}
              title="Delete this PRD entirely (DB + host file). Lets you regenerate from a clean slate."
            >
              {deleting ? "Deleting…" : "Delete PRD"}
            </button>
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
        </div>
        {deleteErr && (
          <div style={{
            marginTop: 10, padding: "8px 12px", borderRadius: "var(--radius)",
            border: "1px solid var(--danger)",
            background: "rgba(255, 59, 59, 0.08)",
            color: "var(--danger)", fontSize: 12,
          }}>
            {deleteErr}
          </div>
        )}
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

      {/* Tasks list comes last. Stacked layout (previously a 2-column
          chat-on-left / tasks-on-right grid) per user-requested order:
          Deploy → Build Chat → PRD → Tasks → Requests. TaskListEditor
          renders its own card chrome, so no wrapping <Card> here. */}
      <TaskListEditor projectId={projectId} onFinalized={reload} />
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

function RoadmapStep({ n, label, active }: { n: number; label: string; active?: boolean }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 6,
      padding: "4px 9px", borderRadius: 12,
      background: active ? "var(--accent-subtle)" : "transparent",
      color: active ? "var(--accent)" : "var(--text-muted)",
      border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
      fontWeight: active ? 700 : 500,
    }}>
      <span style={{
        width: 16, height: 16, borderRadius: "50%",
        background: active ? "var(--accent)" : "var(--bg-hover)",
        color: active ? "#0a0014" : "var(--text-muted)",
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        fontSize: 9, fontWeight: 800,
      }}>{n}</span>
      <span>{label}</span>
    </span>
  )
}

function RoadmapArrow() {
  return (
    <span style={{
      color: "var(--text-muted)", fontSize: 10,
      display: "inline-flex", alignItems: "center",
    }}>→</span>
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

// Danger styling for destructive actions (Delete PRD). Bordered red,
// no background fill — keeps it visually distinct from the primary
// "Finalize" CTA without competing for emphasis.
function dangerBtn(disabled: boolean): React.CSSProperties {
  return {
    display: "inline-flex", alignItems: "center", gap: 6,
    padding: "6px 14px", fontSize: 12, fontWeight: 600,
    background: "transparent",
    color: disabled ? "var(--text-muted)" : "var(--danger)",
    border: "1px solid " + (disabled ? "var(--border)" : "var(--danger)"),
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
