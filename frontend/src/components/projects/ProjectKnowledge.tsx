/**
 * KB-16 — per-project Knowledge tab.
 *
 * A project-scoped view of the Knowledge Base: upload app-specific references
 * (brand guide, domain docs, interviews) into THIS project's isolated
 * `kb_project_<id>` namespace, see everything grounded to the app (incl. the
 * auto-ingested PRD / spec / tasks / research from KB-14), and curate it
 * (approve pending docs, retire stale ones).
 *
 * Self-contained: talks to the `/knowledge` API directly with `project_id` so
 * it never touches the platform-wide knowledge store. Soft-fails to an
 * "offline" note when the KB subsystem is down.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import { UploadCloud, CheckCircle2, RefreshCw, FileText, Sparkles, XCircle } from "lucide-react"
import { api } from "../../lib/api"
import { useAuthStore } from "../../stores/auth"

interface KbDoc {
  doc_id: string
  title: string
  source_type: string
  sensitivity: string
  status: string
  created_at: string | null
  bucket_ids: string[]
}

// KB-28 — a recurring pattern the consolidation job proposed for promotion
// from this app's episodic memory into its citeable KB. Pending review.
interface PromoCandidate {
  candidate_id: string
  kind: string
  summary: string
  occurrences: number
  status: string
  created_at: string | null
}

const STATUS_COLOR: Record<string, string> = {
  approved: "#7dffb0",
  pending: "#ffc24b",
  superseded: "var(--text-muted)",
  retired: "var(--text-muted)",
}

export function ProjectKnowledge({ projectId }: { projectId: string }) {
  const role = useAuthStore((s) => s.user?.role)
  const canWrite = role === "admin" || role === "developer"

  const [available, setAvailable] = useState<boolean | null>(null)
  const [reason, setReason] = useState("")
  const [docs, setDocs] = useState<KbDoc[]>([])
  const [promotions, setPromotions] = useState<PromoCandidate[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const fileRef = useRef<HTMLInputElement>(null)

  const loadDocs = useCallback(async () => {
    try {
      const res = await api.get<{ data: KbDoc[]; meta: { kb_available: boolean } }>(
        `/knowledge/documents?project_id=${encodeURIComponent(projectId)}`,
      )
      setDocs(res.data ?? [])
    } catch (e: any) {
      setError(e?.message || "Failed to load documents")
    }
  }, [projectId])

  const loadPromotions = useCallback(async () => {
    try {
      const res = await api.get<{ data: PromoCandidate[] }>(
        `/knowledge/promotions?project_id=${encodeURIComponent(projectId)}&status=pending`,
      )
      setPromotions(res.data ?? [])
    } catch {
      /* soft-fail: promotions are a secondary panel */
    }
  }, [projectId])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const res = await api.get<{ data: { available: boolean; reason: string } }>(
          "/knowledge",
        )
        if (cancelled) return
        setAvailable(res.data.available)
        setReason(res.data.reason)
        if (res.data.available) {
          await loadDocs()
          await loadPromotions()
        }
      } catch {
        if (!cancelled) setAvailable(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [projectId, loadDocs, loadPromotions])

  const handleFiles = async (files: FileList | null) => {
    if (!files || !files.length) return
    setBusy(true)
    setError("")
    for (const f of Array.from(files)) {
      const fd = new FormData()
      fd.append("file", f)
      fd.append("project_id", projectId)
      try {
        await api.postForm("/knowledge/documents", fd)
      } catch (e: any) {
        setError(e?.message || `Failed to upload ${f.name}`)
      }
    }
    if (fileRef.current) fileRef.current.value = ""
    await loadDocs()
    setBusy(false)
  }

  const act = async (docId: string, action: "approve" | "retire") => {
    try {
      await api.post(
        `/knowledge/documents/${encodeURIComponent(docId)}/${action}`,
        action === "retire" ? { status: "superseded" } : undefined,
      )
      await loadDocs()
    } catch (e: any) {
      setError(e?.message || `Failed to ${action}`)
    }
  }

  const reviewPromotion = async (candidateId: string, action: "approve" | "reject") => {
    try {
      await api.post(`/knowledge/promotions/${encodeURIComponent(candidateId)}/${action}`)
      await loadPromotions()
      if (action === "approve") await loadDocs() // promoted → new approved doc
    } catch (e: any) {
      setError(e?.message || `Failed to ${action} promotion`)
    }
  }

  if (available === null) return null // initial load — render nothing yet

  return (
    <div
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        padding: 20,
        marginTop: 16,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
        <h2 style={{ fontSize: 17, fontWeight: 700, margin: 0, display: "flex", alignItems: "center", gap: 8 }}>
          <FileText size={17} style={{ color: "var(--accent)" }} /> App Knowledge
        </h2>
        {available && (
          <button onClick={loadDocs} title="Refresh" style={ghostBtn}>
            <RefreshCw size={13} /> Refresh
          </button>
        )}
      </div>
      <p style={{ margin: "0 0 14px", color: "var(--text-secondary)", fontSize: 12.5 }}>
        Documents here are grounded <strong>only to this application</strong> — agents working it
        retrieve from this app's knowledge, never another's. Includes auto-ingested PRD, spec,
        tasks &amp; research.
      </p>

      {!available && (
        <div style={{ color: "var(--text-secondary)", fontSize: 13 }}>
          Knowledge base offline — {reason || "subsystem unavailable"}. Uploads disabled.
        </div>
      )}

      {error && (
        <div
          onClick={() => setError("")}
          style={{ color: "var(--danger,#ff5050)", fontSize: 13, marginBottom: 10, cursor: "pointer" }}
        >
          {error}
        </div>
      )}

      {available && (
        <>
          {/* Upload */}
          {canWrite && (
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                border: "1.5px dashed var(--accent)",
                borderRadius: 10,
                padding: "14px 16px",
                cursor: busy ? "not-allowed" : "pointer",
                opacity: busy ? 0.6 : 1,
                background: "var(--accent-subtle)",
                marginBottom: 14,
              }}
            >
              <input
                ref={fileRef}
                type="file"
                multiple
                disabled={busy}
                style={{ display: "none" }}
                onChange={(e) => handleFiles(e.target.files)}
              />
              <UploadCloud size={20} style={{ color: "var(--accent)" }} />
              <span style={{ fontSize: 13 }}>
                {busy ? "Uploading…" : "Upload app refs (brand guide, domain docs, interviews) — md · txt · pdf · docx · xlsx · csv"}
              </span>
            </label>
          )}

          {/* KB-28 — promotion review queue (memory → knowledge gate) */}
          {promotions.length > 0 && (
            <div
              style={{
                border: "1px solid #c9a227",
                background: "rgba(255,194,75,0.06)",
                borderRadius: 10,
                padding: "12px 14px",
                marginBottom: 14,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                <Sparkles size={15} style={{ color: "#ffc24b" }} />
                <span style={{ fontSize: 13.5, fontWeight: 700 }}>
                  Promotion review · {promotions.length} pending
                </span>
              </div>
              <p style={{ margin: "0 0 10px", fontSize: 12, color: "var(--text-secondary)" }}>
                Recurring patterns the platform learned from this app's experience. Approving
                promotes a pattern into the citeable App Knowledge above — the only path from
                unvetted memory to grounded fact.
              </p>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {promotions.map((p) => (
                  <div
                    key={p.candidate_id}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: 10,
                      background: "var(--bg-secondary)",
                      border: "1px solid var(--border)",
                      borderRadius: 8,
                      padding: "9px 12px",
                    }}
                  >
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 13, color: "var(--text-primary)" }}>{p.summary}</div>
                      <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                        {p.kind} · {p.occurrences}× occurrences
                      </div>
                    </div>
                    {canWrite && (
                      <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                        <button onClick={() => reviewPromotion(p.candidate_id, "approve")} style={approveBtn}>
                          <CheckCircle2 size={12} /> Promote
                        </button>
                        <button onClick={() => reviewPromotion(p.candidate_id, "reject")} style={rejectBtn}>
                          <XCircle size={12} /> Reject
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Document list */}
          {docs.length === 0 ? (
            <div style={{ color: "var(--text-secondary)", fontSize: 13, padding: "6px 0" }}>
              No app knowledge yet. Upload references, or finalize a PRD/tasks to auto-ingest.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {docs.map((d) => (
                <div
                  key={d.doc_id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 10,
                    background: "var(--bg-secondary)",
                    border: "1px solid var(--border)",
                    borderRadius: 8,
                    padding: "9px 12px",
                  }}
                >
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {d.title}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                      {d.source_type}
                      {d.sensitivity === "pii" && <span style={{ color: "#ff6b6b" }}> · ⚠ PII</span>}
                    </div>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                    <span
                      style={{
                        fontSize: 10,
                        fontWeight: 700,
                        textTransform: "uppercase",
                        letterSpacing: 0.5,
                        padding: "2px 8px",
                        borderRadius: 999,
                        color: STATUS_COLOR[d.status] ?? "var(--text-muted)",
                        border: `1px solid ${STATUS_COLOR[d.status] ?? "var(--text-muted)"}`,
                      }}
                    >
                      {d.status}
                    </span>
                    {canWrite && d.status === "pending" && (
                      <button onClick={() => act(d.doc_id, "approve")} style={approveBtn}>
                        <CheckCircle2 size={12} /> Approve
                      </button>
                    )}
                    {canWrite && d.status === "approved" && (
                      <button onClick={() => act(d.doc_id, "retire")} style={ghostBtn}>
                        Retire
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

const ghostBtn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  fontSize: 12,
  padding: "5px 10px",
  borderRadius: 8,
  border: "1px solid var(--border)",
  cursor: "pointer",
  color: "var(--text-primary)",
  background: "transparent",
  fontFamily: "var(--font)",
}

const approveBtn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 5,
  fontSize: 11,
  padding: "4px 9px",
  borderRadius: 8,
  cursor: "pointer",
  border: "1px solid #7dffb0",
  color: "#7dffb0",
  background: "transparent",
  fontFamily: "var(--font)",
}

const rejectBtn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 5,
  fontSize: 11,
  padding: "4px 9px",
  borderRadius: 8,
  cursor: "pointer",
  border: "1px solid var(--border)",
  color: "var(--text-secondary)",
  background: "transparent",
  fontFamily: "var(--font)",
}
