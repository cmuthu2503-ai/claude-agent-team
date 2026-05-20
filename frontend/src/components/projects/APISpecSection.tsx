/**
 * API Spec section of the Build Workspace.
 *
 * Sits between the PRD card and the Task List. Same lifecycle as the
 * PRD: Draft → Finalized → Archived. Generate button is gated on the
 * PRD being finalized (the backend rejects with 409 otherwise).
 *
 * States rendered:
 *   1. PRD not finalized            → empty placeholder, hint to finalize PRD first
 *   2. PRD finalized, no spec yet   → "Generate API Spec" CTA
 *   3. Spec draft                   → markdown editor + Save / Regenerate / Finalize / Delete
 *   4. Spec finalized               → collapsible read-only view + Delete
 *
 * The agent uses the backend_specialist role and the
 * docs/reference-formats/api-spec-template.md reference, which the
 * route loads at request time.
 */

import { useEffect, useState } from "react"
import {
  FileText, Sparkles, ChevronRight, ChevronDown, CheckCircle2,
} from "lucide-react"
import { api } from "../../lib/api"
import { MarkdownRenderer } from "../markdown/MarkdownRenderer"

interface APISpec {
  artifact_id: string
  version: number
  status: "draft" | "finalized" | "archived"
  content: string
  created_at: string
  updated_at: string | null
  finalized_at: string | null
  finalized_by: string | null
  review_input: string | null
}

interface Props {
  projectId: string
  // PRD must be finalized before we can generate — passed in so we
  // can show the right empty state instead of round-tripping a 409.
  prdFinalized: boolean
}

export function APISpecSection({ projectId, prdFinalized }: Props) {
  const [spec, setSpec] = useState<APISpec | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState("")

  const load = async () => {
    try {
      const res = await api.get<{ data: APISpec }>(`/projects/${projectId}/api-spec`)
      setSpec(res.data)
      setErr("")
    } catch (e: any) {
      // 404 = no spec yet (normal state); anything else = real error
      const msg = String(e?.message || e)
      if (msg.startsWith("404")) {
        setSpec(null)
        setErr("")
      } else {
        setErr(parseDetail(msg) || "Failed to load API spec")
      }
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  if (loading) {
    return (
      <Card>
        <Heading icon={<FileText size={16} />}>API Specification</Heading>
        <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 6 }}>
          Loading…
        </p>
      </Card>
    )
  }

  // State 1: PRD not finalized yet
  if (!prdFinalized) {
    return (
      <Card>
        <Heading icon={<FileText size={16} />}>API Specification</Heading>
        <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 6 }}>
          Finalize the PRD first, then generate the API specification from it.
        </p>
      </Card>
    )
  }

  // State 2: PRD finalized, no spec yet
  if (!spec) {
    return <APISpecEmpty projectId={projectId} onCreated={load} err={err} />
  }

  // State 3: draft  /  State 4: finalized
  if (spec.status === "finalized") {
    return <APISpecFinalized projectId={projectId} spec={spec} reload={load} />
  }
  return <APISpecDraft projectId={projectId} spec={spec} reload={load} />
}


// ── State 2: Empty (PRD finalized, no spec yet) ────────────────────────

function APISpecEmpty({
  projectId, onCreated, err,
}: { projectId: string; onCreated: () => void; err: string }) {
  const [busy, setBusy] = useState(false)
  const [localErr, setLocalErr] = useState(err)

  const generate = async () => {
    if (!window.confirm(
      "Generate the API Specification from the finalized PRD?\n\n" +
      "This runs the backend_specialist agent and may take 30–90 seconds. " +
      "The output follows the enterprise REST spec format (OpenAPI 3.1 + " +
      "RFC 7807 errors + cursor pagination + rate limits + idempotency)."
    )) return
    setBusy(true); setLocalErr("")
    try {
      await api.post(`/projects/${projectId}/api-spec/generate`, {})
      onCreated()
    } catch (e: any) {
      setLocalErr(parseDetail(e?.message) || "API spec generation failed")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <Heading icon={<FileText size={16} />}>API Specification</Heading>
        <button
          type="button"
          onClick={generate}
          disabled={busy}
          style={primaryBtn(busy)}
          title="Run backend_specialist on the finalized PRD"
        >
          <Sparkles size={14} />
          {busy ? "Generating…" : "Generate API Spec"}
        </button>
      </div>
      <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 10, marginBottom: 0, lineHeight: 1.5 }}>
        No API specification yet. Generate one to lock in the REST surface
        (endpoints, schemas, auth, error envelope, OpenAPI YAML) before
        the task list is created — generated tasks will reference real
        endpoints from this spec.
      </p>
      {localErr && <ErrorBanner>{localErr}</ErrorBanner>}
    </Card>
  )
}


// ── State 3: Draft (editable + actions) ────────────────────────────────

function APISpecDraft({
  projectId, spec, reload,
}: { projectId: string; spec: APISpec; reload: () => void }) {
  const [content, setContent] = useState(spec.content)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState<"saving" | "regen" | "finalizing" | "deleting" | null>(null)
  const [err, setErr] = useState("")
  const [tab, setTab] = useState<"edit" | "preview" | "split">("split")
  const [reviewComments, setReviewComments] = useState("")

  useEffect(() => {
    setContent(spec.content)
    setDirty(false)
    setReviewComments("")
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec.artifact_id])

  const save = async () => {
    setBusy("saving"); setErr("")
    try {
      await api.patch(`/projects/${projectId}/api-spec`, { content })
      setDirty(false)
      reload()
    } catch (e: any) {
      setErr(parseDetail(e?.message) || "Save failed")
    } finally { setBusy(null) }
  }

  const regenerate = async () => {
    if (dirty && !window.confirm("Regenerate will discard your unsaved edits. Continue?")) return
    setBusy("regen"); setErr("")
    try {
      const body = reviewComments.trim() ? { review_comments: reviewComments.trim() } : {}
      await api.post(`/projects/${projectId}/api-spec/generate`, body)
      setReviewComments("")
      reload()
    } catch (e: any) {
      setErr(parseDetail(e?.message) || "Regenerate failed")
    } finally { setBusy(null) }
  }

  const finalize = async () => {
    if (dirty) {
      if (!window.confirm("You have unsaved edits. Save and finalize?")) return
      try {
        await api.patch(`/projects/${projectId}/api-spec`, { content })
      } catch (e: any) {
        setErr(parseDetail(e?.message) || "Save failed before finalize")
        return
      }
    }
    if (!window.confirm(
      "Finalize this API specification?\n\n" +
      "The spec will be written to C:/ai-projects/<ProjectName>/docs/api-spec.md " +
      "and pushed to the project's GitHub repo. " +
      "Downstream task generation will reference this version."
    )) return
    setBusy("finalizing"); setErr("")
    try {
      const res = await api.patch(`/projects/${projectId}/api-spec`, { status: "finalized" })
      const meta = (res as any)?.meta || {}
      const hw = meta.host_write
      const gh = meta.github_push
      const lines: string[] = ["API spec finalized."]
      if (hw?.ok) lines.push(`Saved to: ${hw.path}`)
      else if (hw?.error) lines.push(`Local write failed: ${hw.error}`)
      if (gh?.ok) lines.push(`Pushed to ${gh.repo} @ ${gh.short_sha}`)
      else if (gh?.skipped === "no_repo_url") lines.push("(No GitHub repo configured — skipped push)")
      else if (gh?.error) lines.push(`GitHub push failed: ${gh.error}`)
      window.alert(lines.join("\n"))
      reload()
    } catch (e: any) {
      setErr(parseDetail(e?.message) || "Finalize failed")
    } finally { setBusy(null) }
  }

  const deleteSpec = async () => {
    if (!window.confirm(
      "DELETE this API specification draft and all prior versions?\n\n" +
      "Removes every API spec row from the database and deletes " +
      "C:/ai-projects/<ProjectName>/docs/api-spec.md from your disk.\n\n" +
      "This cannot be undone."
    )) return
    setBusy("deleting"); setErr("")
    try {
      const res = await api.delete(`/projects/${projectId}/api-spec`)
      const meta = (res as any)?.meta || {}
      window.alert(`Deleted ${meta.deleted_versions ?? 0} API spec version(s) from the database.`)
      reload()
    } catch (e: any) {
      setErr(parseDetail(e?.message) || "Delete failed")
    } finally { setBusy(null) }
  }

  const onContentChange = (v: string) => { setContent(v); setDirty(true) }

  return (
    <Card>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <Heading icon={<FileText size={16} />}>
          API Specification · Draft (v{spec.version})
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
        gap: 10,
      }}>
        {(tab === "edit" || tab === "split") && (
          <textarea
            value={content}
            onChange={(e) => onContentChange(e.target.value)}
            style={{
              width: "100%",
              minHeight: 380,
              padding: 10,
              fontFamily: "var(--font-mono)", fontSize: 12, lineHeight: 1.5,
              background: "var(--bg-hover)", color: "var(--text-primary)",
              border: "1px solid var(--border)", borderRadius: "var(--radius)",
              resize: "vertical",
              outline: "none",
            }}
          />
        )}
        {(tab === "preview" || tab === "split") && (
          <div style={{
            minHeight: 380, padding: "12px 16px",
            background: "var(--bg-hover)", border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            maxHeight: 600, overflow: "auto",
          }}>
            <MarkdownRenderer content={content} />
          </div>
        )}
      </div>

      <div style={{ marginTop: 12 }}>
        <label style={{ fontSize: 11, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>
          Review comments (optional — for next regeneration)
        </label>
        <textarea
          value={reviewComments}
          onChange={(e) => setReviewComments(e.target.value)}
          placeholder={`e.g. "Add a /webhooks resource with HMAC signing. Use cursor pagination for /users. Add 403 to /admin/*."`}
          maxLength={2000}
          rows={2}
          style={{
            width: "100%",
            padding: 8,
            fontSize: 12, fontFamily: "var(--font)",
            background: "var(--bg-hover)", color: "var(--text-primary)",
            border: "1px solid var(--border)", borderRadius: "var(--radius)",
            resize: "vertical",
            outline: "none",
          }}
        />
      </div>

      <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          type="button"
          onClick={regenerate}
          disabled={busy !== null}
          style={secondaryBtn(busy !== null)}
        >
          {busy === "regen"
            ? "Regenerating…"
            : reviewComments.trim() ? "Regenerate with comments" : "Regenerate"}
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
          onClick={deleteSpec}
          disabled={busy !== null}
          style={dangerBtn(busy !== null)}
          title="Delete this API spec entirely (DB + host file)"
        >
          {busy === "deleting" ? "Deleting…" : "Delete API Spec"}
        </button>
        <button
          type="button"
          onClick={finalize}
          disabled={busy !== null || content.trim().length === 0}
          style={primaryBtn(busy !== null || content.trim().length === 0)}
        >
          {busy === "finalizing" ? "Finalizing…" : "Finalize API Spec"}
        </button>
      </div>

      {err && <ErrorBanner>{err}</ErrorBanner>}
    </Card>
  )
}


// ── State 4: Finalized (read-only, collapsible) ────────────────────────

function APISpecFinalized({
  projectId, spec, reload,
}: { projectId: string; spec: APISpec; reload: () => void }) {
  const [collapsed, setCollapsed] = useState(true)
  const [deleting, setDeleting] = useState(false)
  const [err, setErr] = useState("")

  const deleteSpec = async () => {
    if (!window.confirm(
      "DELETE this FINALIZED API specification?\n\n" +
      "• Removes ALL versions from the database\n" +
      "• Deletes C:/ai-projects/<ProjectName>/docs/api-spec.md\n" +
      "• Tasks already generated against this spec are NOT modified\n\n" +
      "This cannot be undone."
    )) return
    setDeleting(true); setErr("")
    try {
      const res = await api.delete(`/projects/${projectId}/api-spec`)
      const meta = (res as any)?.meta || {}
      window.alert(`Deleted ${meta.deleted_versions ?? 0} API spec version(s).`)
      reload()
    } catch (e: any) {
      setErr(parseDetail(e?.message) || "Delete failed")
    } finally { setDeleting(false) }
  }

  return (
    <Card>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <Heading icon={<CheckCircle2 size={16} color="var(--success)" />}>
          API Specification · Finalized (v{spec.version})
        </Heading>
        <div style={{ display: "inline-flex", gap: 6 }}>
          <button
            type="button"
            onClick={deleteSpec}
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
            title="Delete this API spec entirely (DB + host file). Lets you regenerate from a clean slate."
          >
            {deleting ? "Deleting…" : "Delete API Spec"}
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
            {collapsed ? "Show API Spec" : "Hide API Spec"}
          </button>
        </div>
      </div>
      {err && <ErrorBanner>{err}</ErrorBanner>}
      {!collapsed && (
        <div style={{
          marginTop: 10, padding: "12px 16px",
          background: "var(--bg-hover)", border: "1px solid var(--border)",
          borderRadius: "var(--radius)", maxHeight: 600, overflow: "auto",
        }}>
          <MarkdownRenderer content={spec.content} />
        </div>
      )}
    </Card>
  )
}


// ── Shared primitives (intentionally duplicated from BuildWorkspace
//    rather than exported — keeps the API spec self-contained.) ───────

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

function Heading({ icon, children }: { icon?: React.ReactNode; children: React.ReactNode }) {
  return (
    <h3 style={{
      margin: 0, fontSize: 14, fontWeight: 700,
      color: "var(--text-primary)",
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
      marginTop: 10, padding: "8px 12px", borderRadius: "var(--radius)",
      border: "1px solid var(--danger)",
      background: "rgba(255, 59, 59, 0.08)",
      color: "var(--danger)", fontSize: 12,
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
    border: "1px solid var(--border)", borderRadius: "var(--radius)",
    cursor: disabled ? "not-allowed" : "pointer",
    whiteSpace: "nowrap", lineHeight: 1, fontFamily: "var(--font)",
    opacity: disabled ? 0.6 : 1,
  }
}

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
    opacity: disabled ? 0.5 : 1,
  }
}

function parseDetail(msg: string | undefined): string | undefined {
  if (!msg) return undefined
  const colon = msg.indexOf(":")
  if (colon < 0) return msg
  try {
    const parsed = JSON.parse(msg.slice(colon + 1).trim())
    const d = parsed?.detail ?? parsed
    if (typeof d === "string") return d
    if (typeof d === "object" && d) return d.hint || d.message || d.error || JSON.stringify(d)
    return msg
  } catch {
    return msg
  }
}
