/**
 * KB-10 — Knowledge Base page.
 *
 * Builds the FROZEN design (docs/mockups/kb-buckets-mockup.html) as a real,
 * themed React page: a 4-tab flow —
 *   01 Upload         · drop files → parse/chunk/PII/embed → pending
 *   02 Tag & Bucket   · many-to-many doc ↔ bucket membership
 *   03 Buckets        · grounding scopes (create / inspect / delete)
 *   04 Ground a Task  · pick bucket(s) → dispatch a hard-sealed request
 *
 * The visual intent of the freeze (tabbed flow, pipeline status chips,
 * glowing bucket cards, the grounded/blocked scope nodes) is preserved,
 * rendered against the app theme variables so it lives inside the platform
 * chrome rather than as a standalone neon mock.
 *
 * Soft-fail: when the KB subsystem is down (no Voyage key / Postgres), the
 * page shows an "offline" banner and disables write actions — it never errors
 * the user out.
 */

import { useEffect, useMemo, useState } from "react"
import { useNavigate } from "react-router-dom"
import {
  UploadCloud,
  Tags,
  Boxes,
  Zap,
  CheckCircle2,
  Trash2,
  Lock,
  RefreshCw,
  Plus,
  X,
  Link2,
  ClipboardPaste,
  Search,
  ExternalLink,
} from "lucide-react"
import { api } from "../lib/api"
import { useAuthStore } from "../stores/auth"
import {
  useKnowledgeStore,
  type KbBucket,
  type KbDocument,
  type KbSearchResult,
  type KbIngestResult,
} from "../stores/knowledge"

type TabKey = "up" | "tag" | "buk" | "grd" | "add" | "search"

const TABS: { key: TabKey; n: string; label: string; icon: any }[] = [
  { key: "up", n: "01", label: "Upload", icon: UploadCloud },
  { key: "tag", n: "02", label: "Tag & Bucket", icon: Tags },
  { key: "buk", n: "03", label: "Buckets", icon: Boxes },
  { key: "grd", n: "04", label: "Ground a Task", icon: Zap },
  { key: "add", n: "05", label: "Add Article", icon: Link2 },
  { key: "search", n: "06", label: "Search Library", icon: Search },
]

// Bucket accent colors — cycle by index; system bucket is always the lime.
const BUCKET_COLORS = ["#34e7ff", "#ff45d0", "#9b7bff", "#ffc24b", "#7dffb0"]
function bucketColor(b: KbBucket, idx: number): string {
  if (b.is_system) return "#7dffb0"
  return BUCKET_COLORS[idx % BUCKET_COLORS.length]
}

function StatusPill({ status }: { status: string }) {
  const map: Record<string, { c: string; t: string }> = {
    approved: { c: "#7dffb0", t: "approved" },
    pending: { c: "#ffc24b", t: "pending review" },
    superseded: { c: "var(--text-secondary)", t: "retired" },
    retired: { c: "var(--text-secondary)", t: "retired" },
  }
  const s = map[status] ?? { c: "var(--text-secondary)", t: status }
  return (
    <span
      style={{
        fontSize: 10,
        fontWeight: 700,
        padding: "2px 8px",
        borderRadius: 999,
        color: s.c,
        border: `1px solid ${s.c}`,
        textTransform: "uppercase",
        letterSpacing: 0.5,
        whiteSpace: "nowrap",
      }}
    >
      {s.t}
    </span>
  )
}

function BucketDot({ color }: { color: string }) {
  return (
    <span
      style={{
        width: 9,
        height: 9,
        borderRadius: "50%",
        background: color,
        boxShadow: `0 0 10px ${color}`,
        display: "inline-block",
        flexShrink: 0,
      }}
    />
  )
}

export function KnowledgeBasePage() {
  const role = useAuthStore((s) => s.user?.role)
  const isAdmin = role === "admin"
  const canWrite = role === "admin" || role === "developer"
  const navigate = useNavigate()

  const {
    status,
    buckets,
    documents,
    error,
    fetchStatus,
    fetchBuckets,
    fetchDocuments,
    createBucket,
    deleteBucket,
    uploadDocument,
    approveDocument,
    retireDocument,
    purgeDocument,
    setDocumentBuckets,
    reindexPlatform,
    ingestUrl,
    ingestText,
    searchLibrary,
    clearError,
  } = useKnowledgeStore()

  const [tab, setTab] = useState<TabKey>("up")
  const available = status?.available ?? false

  // ── Scope: Platform corpus (default) or a specific project's KB ──────────
  const [projects, setProjects] = useState<{ project_id: string; name: string }[]>([])
  const [scope, setScope] = useState<string>("") // "" = Platform; else project_id

  useEffect(() => {
    fetchStatus()
    fetchBuckets()
    ;(async () => {
      try {
        const res = await api.get<{ data: { project_id: string; name: string }[] }>("/projects")
        setProjects(res.data ?? [])
      } catch {
        /* projects optional — page still works as platform-only */
      }
    })()
  }, [fetchStatus, fetchBuckets])

  // Refetch the document list whenever the scope changes ("" → platform).
  useEffect(() => {
    fetchDocuments(scope ? { projectId: scope } : undefined)
  }, [scope, fetchDocuments])

  // Upload into the selected scope (project namespace, or platform buckets).
  const handleUpload = (file: File, bucketIds: string[]) =>
    uploadDocument(file, bucketIds, scope || undefined)

  const colorFor = useMemo(() => {
    const map = new Map<string, string>()
    buckets.forEach((b, i) => map.set(b.bucket_id, bucketColor(b, i)))
    return (id: string) => map.get(id) ?? "var(--accent)"
  }, [buckets])
  const nameFor = useMemo(() => {
    const map = new Map<string, string>()
    buckets.forEach((b) => map.set(b.bucket_id, b.name))
    return (id: string) => map.get(id) ?? id.slice(0, 8)
  }, [buckets])

  return (
    <div style={{ padding: "24px 28px 80px", maxWidth: 1100, margin: "0 auto" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 18,
          flexWrap: "wrap",
          gap: 12,
        }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800 }}>Knowledge Base</h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-secondary)", fontSize: 12.5 }}>
            agentic · grounded · auditable
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          {/* Scope selector — browse/curate the Platform corpus or a project's KB */}
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
            <span style={{ color: "var(--text-secondary)" }}>Scope</span>
            <select
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              style={{
                fontFamily: "var(--font)",
                fontSize: 12.5,
                padding: "6px 10px",
                borderRadius: 9,
                background: "var(--bg-secondary)",
                color: "var(--text-primary)",
                border: "1px solid var(--border)",
                cursor: "pointer",
              }}
            >
              <option value="">Platform (shared corpus)</option>
              {projects.map((p) => (
                <option key={p.project_id} value={p.project_id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <KbStatusBadge
            available={available}
            namespace={scope ? `kb_project_${scope}` : status?.namespace}
            reason={status?.reason}
          />
        </div>
      </div>

      {error && (
        <div
          onClick={clearError}
          style={{
            background: "var(--danger-subtle, rgba(255,80,80,.12))",
            border: "1px solid var(--danger, #ff5050)",
            color: "var(--danger, #ff5050)",
            padding: "10px 14px",
            borderRadius: 10,
            marginBottom: 14,
            fontSize: 13,
            cursor: "pointer",
          }}
          title="Click to dismiss"
        >
          {error}
        </div>
      )}

      {!available && (
        <div
          style={{
            background: "var(--bg-secondary)",
            border: "1px solid var(--border)",
            borderRadius: 12,
            padding: 16,
            marginBottom: 18,
            color: "var(--text-secondary)",
            fontSize: 13,
          }}
        >
          <strong style={{ color: "var(--text-primary)" }}>Knowledge base offline.</strong>{" "}
          {status?.reason || "Subsystem not initialized."} — the platform runs normally;
          agents simply work without retrieval. Set a Voyage API key and start Postgres to
          enable ingest + grounded retrieval.
        </div>
      )}

      {/* Tab bar */}
      <div style={{ display: "flex", gap: 8, marginBottom: 20, flexWrap: "wrap" }}>
        {TABS.map((t) => {
          const on = tab === t.key
          const Icon = t.icon
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                fontSize: 12.5,
                padding: "9px 15px",
                borderRadius: 11,
                cursor: "pointer",
                fontFamily: "var(--font)",
                fontWeight: on ? 700 : 500,
                color: on ? "var(--accent)" : "var(--text-secondary)",
                background: on ? "var(--accent-subtle)" : "transparent",
                border: `1px solid ${on ? "var(--accent)" : "var(--border)"}`,
              }}
            >
              <span style={{ opacity: 0.6, fontSize: 11 }}>{t.n}</span>
              <Icon size={15} />
              {t.label}
            </button>
          )
        })}
      </div>

      {tab === "up" && (
        <UploadScreen
          buckets={buckets}
          documents={documents}
          available={available}
          canWrite={canWrite}
          colorFor={colorFor}
          onUpload={handleUpload}
          onApprove={approveDocument}
          scopeLabel={scope ? (projects.find((p) => p.project_id === scope)?.name ?? scope) : null}
        />
      )}
      {tab === "tag" && (
        <TagScreen
          buckets={buckets}
          documents={documents}
          available={available}
          canWrite={canWrite}
          colorFor={colorFor}
          onSetBuckets={setDocumentBuckets}
          onApprove={approveDocument}
          onRetire={retireDocument}
          onPurge={isAdmin ? purgeDocument : undefined}
        />
      )}
      {tab === "buk" && (
        <BucketsScreen
          buckets={buckets}
          available={available}
          canWrite={canWrite}
          isAdmin={isAdmin}
          colorFor={colorFor}
          onCreate={createBucket}
          onDelete={deleteBucket}
          onReindex={reindexPlatform}
        />
      )}
      {tab === "grd" && (
        <GroundScreen
          buckets={buckets}
          available={available}
          canWrite={canWrite}
          colorFor={colorFor}
          nameFor={nameFor}
          navigate={navigate}
        />
      )}
      {tab === "add" && (
        <AddArticleScreen
          buckets={buckets}
          documents={documents}
          available={available}
          canWrite={canWrite}
          colorFor={colorFor}
          onIngestUrl={ingestUrl}
          onIngestText={ingestText}
        />
      )}
      {tab === "search" && (
        <SearchLibraryScreen
          buckets={buckets}
          available={available}
          colorFor={colorFor}
          nameFor={nameFor}
          onSearch={searchLibrary}
        />
      )}
    </div>
  )
}

function KbStatusBadge({
  available,
  namespace,
  reason,
}: {
  available: boolean
  namespace?: string | null
  reason?: string
}) {
  const c = available ? "#7dffb0" : "var(--text-secondary)"
  return (
    <div
      title={reason}
      style={{
        fontSize: 11,
        fontFamily: "monospace",
        color: c,
        border: `1px solid ${c}`,
        padding: "5px 11px",
        borderRadius: 999,
        whiteSpace: "nowrap",
      }}
    >
      ● {available ? `${namespace} · pgvector online` : "offline"}
    </div>
  )
}

const panelStyle: React.CSSProperties = {
  background: "var(--bg-secondary)",
  border: "1px solid var(--border)",
  borderRadius: 16,
  padding: 22,
}

// ── Screen 01: Upload ────────────────────────────────────────────────────

function UploadScreen({
  buckets,
  documents,
  available,
  canWrite,
  colorFor,
  onUpload,
  onApprove,
  scopeLabel,
}: {
  buckets: KbBucket[]
  documents: KbDocument[]
  available: boolean
  canWrite: boolean
  colorFor: (id: string) => string
  onUpload: (file: File, bucketIds: string[]) => Promise<KbDocument | null>
  onApprove: (docId: string) => Promise<void>
  scopeLabel: string | null
}) {
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const disabled = !available || !canWrite || busy

  const toggle = (id: string) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))

  const handleFiles = async (files: FileList | null) => {
    if (!files || !files.length) return
    setBusy(true)
    for (const f of Array.from(files)) {
      await onUpload(f, selected)
    }
    setBusy(false)
  }

  const recent = documents.slice(0, 8)

  return (
    <div style={panelStyle}>
      <h2 style={{ margin: "0 0 4px", fontSize: 16 }}>Add documents to the knowledge base</h2>
      <p style={{ margin: "0 0 12px", color: "var(--text-secondary)", fontSize: 12.5 }}>
        Each file is parsed → chunked → PII-scanned → embedded, then held for approval.
      </p>
      {/* Scope hint — where these uploads (and the list below) land */}
      <div
        style={{
          margin: "0 0 16px",
          padding: "8px 12px",
          borderRadius: 9,
          fontSize: 12,
          border: "1px solid var(--border)",
          background: "var(--bg-card)",
          color: "var(--text-secondary)",
        }}
      >
        {scopeLabel ? (
          <>Scoped to <strong style={{ color: "var(--accent)" }}>{scopeLabel}</strong> — uploads
          land in this app's isolated knowledge (only its agents retrieve them).</>
        ) : (
          <>Scoped to the <strong style={{ color: "var(--accent)" }}>Platform</strong> shared
          corpus — craft/method knowledge available to every project. Switch scope in the header
          to curate a specific app's knowledge.</>
        )}
      </div>

      {/* Dropzone */}
      <label
        style={{
          display: "block",
          border: "1.5px dashed var(--accent)",
          borderRadius: 12,
          padding: "36px 20px",
          textAlign: "center",
          cursor: disabled ? "not-allowed" : "pointer",
          opacity: disabled ? 0.5 : 1,
          background: "var(--accent-subtle)",
        }}
      >
        <input
          type="file"
          multiple
          disabled={disabled}
          style={{ display: "none" }}
          onChange={(e) => handleFiles(e.target.files)}
        />
        <UploadCloud size={40} style={{ color: "var(--accent)" }} />
        <h3 style={{ margin: "10px 0 4px", fontSize: 15 }}>
          {busy ? "Uploading…" : "Click to browse, or drop files here"}
        </h3>
        <p style={{ margin: 0, color: "var(--text-secondary)", fontSize: 12, fontFamily: "monospace" }}>
          md · txt · pdf · docx · xlsx · csv · source code · max 25 MB / file
        </p>
      </label>

      {/* Bucket assignment bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "16px 0 4px", flexWrap: "wrap" }}>
        <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>Assign to bucket(s):</span>
        {buckets.length === 0 && (
          <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
            No buckets yet — create one in the Buckets tab.
          </span>
        )}
        {buckets.map((b) => {
          const on = selected.includes(b.bucket_id)
          const c = colorFor(b.bucket_id)
          return (
            <button
              key={b.bucket_id}
              onClick={() => toggle(b.bucket_id)}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 7,
                fontSize: 11.5,
                padding: "6px 11px",
                borderRadius: 999,
                cursor: "pointer",
                fontFamily: "monospace",
                color: "var(--text-primary)",
                border: `1px solid ${on ? c : "var(--border)"}`,
                background: on ? `${c}22` : "transparent",
              }}
            >
              <BucketDot color={c} /> {b.name}
            </button>
          )
        })}
      </div>

      {/* Recent uploads queue */}
      <div style={{ marginTop: 18, display: "flex", flexDirection: "column", gap: 10 }}>
        {recent.length === 0 && (
          <div style={{ color: "var(--text-secondary)", fontSize: 13, padding: "8px 0" }}>
            No documents yet.
          </div>
        )}
        {recent.map((d) => (
          <div
            key={d.doc_id}
            style={{
              display: "grid",
              gridTemplateColumns: "36px 1fr auto",
              gap: 13,
              alignItems: "center",
              background: "var(--bg-primary)",
              border: "1px solid var(--border)",
              borderRadius: 12,
              padding: "12px 14px",
            }}
          >
            <div
              style={{
                width: 36,
                height: 36,
                borderRadius: 8,
                display: "grid",
                placeItems: "center",
                fontSize: 10,
                fontFamily: "monospace",
                background: "var(--accent-subtle)",
                color: "var(--accent)",
                textTransform: "uppercase",
              }}
            >
              {d.source_type.slice(0, 4)}
            </div>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600 }}>
                {d.title}
                {d.version > 1 && (
                  <span style={{ fontSize: 10.5, color: "var(--text-muted)", marginLeft: 6, fontFamily: "monospace" }}>
                    v{d.version}
                  </span>
                )}
              </div>
              {/* Metadata chip row — real, per-doc facts (no more identical pipeline chips) */}
              <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 6, flexWrap: "wrap" }}>
                <MetaChip color={sourceColor(d.source_type)}>{sourceLabel(d.source_type)}</MetaChip>
                <MetaChip>📦 {d.chunk_count ?? 0} chunk{(d.chunk_count ?? 0) === 1 ? "" : "s"}</MetaChip>
                {d.created_at && <MetaChip>🕑 {relTime(d.created_at)}</MetaChip>}
                {d.sensitivity === "pii" && <MetaChip color="#ff6b6b">⚠ PII</MetaChip>}
                {d.bucket_ids.map((id) => (
                  <MetaChip key={id} color={colorFor(id)}>
                    <BucketDot color={colorFor(id)} /> {nameOf(buckets, id)}
                  </MetaChip>
                ))}
                {d.curated_by && (
                  <MetaChip>✓ {d.curated_by}{d.approved_at ? ` · ${relTime(d.approved_at)}` : ""}</MetaChip>
                )}
              </div>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-end" }}>
              <StatusPill status={d.status} />
              {d.status === "pending" && canWrite && (
                <button
                  onClick={() => onApprove(d.doc_id)}
                  style={approveBtnStyle}
                >
                  <CheckCircle2 size={12} /> Approve
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function nameOf(buckets: KbBucket[], id: string): string {
  return buckets.find((b) => b.bucket_id === id)?.name ?? id.slice(0, 8)
}

// ── Document-row metadata helpers (information scent over flat chips) ────────

function MetaChip({ children, color }: { children: React.ReactNode; color?: string }) {
  const c = color ?? "var(--text-muted)"
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontSize: 10,
        fontFamily: "var(--font-mono)",
        padding: "2px 7px",
        borderRadius: 999,
        color: c,
        border: `1px solid ${c}55`,
        background: `${c}11`,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  )
}

// Source-type → human label + a stable accent (provenance at a glance).
const _SOURCE_META: Record<string, [string, string]> = {
  upload: ["upload", "#7da2ff"],
  prd: ["PRD", "#c792ea"],
  api_spec: ["API spec", "#c792ea"],
  tasks: ["tasks", "#82aaff"],
  code: ["code", "#7dffb0"],
  research_output: ["research", "#ffc24b"],
  memory_promotion: ["promoted", "#ff9ed2"],
  repo_doc: ["repo doc", "#7da2ff"],
}
function sourceLabel(t: string): string {
  return _SOURCE_META[t]?.[0] ?? t.replace(/_/g, " ")
}
function sourceColor(t: string): string {
  return _SOURCE_META[t]?.[1] ?? "var(--accent)"
}

// Compact relative time ("3d ago", "2h ago", "just now").
function relTime(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ""
  const s = Math.max(0, Math.floor((Date.now() - then) / 1000))
  if (s < 60) return "just now"
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  if (d < 30) return `${d}d ago`
  const mo = Math.floor(d / 30)
  if (mo < 12) return `${mo}mo ago`
  return `${Math.floor(mo / 12)}y ago`
}

const approveBtnStyle: React.CSSProperties = {
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

// ── Screen 02: Tag & Bucket ──────────────────────────────────────────────

function TagScreen({
  buckets,
  documents,
  available,
  canWrite,
  colorFor,
  onSetBuckets,
  onApprove,
  onRetire,
  onPurge,
}: {
  buckets: KbBucket[]
  documents: KbDocument[]
  available: boolean
  canWrite: boolean
  colorFor: (id: string) => string
  onSetBuckets: (docId: string, bucketIds: string[]) => Promise<void>
  onApprove: (docId: string) => Promise<void>
  onRetire: (docId: string) => Promise<void>
  onPurge?: (docId: string) => Promise<void>
}) {
  const [selectedDoc, setSelectedDoc] = useState<string | null>(null)
  const [draft, setDraft] = useState<string[]>([])

  const doc = documents.find((d) => d.doc_id === selectedDoc) ?? null
  useEffect(() => {
    setDraft(doc ? [...doc.bucket_ids] : [])
  }, [selectedDoc]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!available) {
    return <div style={panelStyle}>Knowledge base offline — tagging unavailable.</div>
  }

  const toggle = (id: string) =>
    setDraft((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))
  const dirty = doc
    ? JSON.stringify([...draft].sort()) !== JSON.stringify([...doc.bucket_ids].sort())
    : false

  return (
    <div style={panelStyle}>
      <h2 style={{ margin: "0 0 4px", fontSize: 16 }}>Tag & organize documents</h2>
      <p style={{ margin: "0 0 16px", color: "var(--text-secondary)", fontSize: 12.5 }}>
        A document can live in several buckets. Buckets are the grounding scope agents are
        locked to.
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        {/* Doc list */}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {documents.length === 0 && (
            <div style={{ color: "var(--text-secondary)", fontSize: 13 }}>No documents.</div>
          )}
          {documents.map((d) => {
            const on = d.doc_id === selectedDoc
            return (
              <button
                key={d.doc_id}
                onClick={() => setSelectedDoc(d.doc_id)}
                style={{
                  textAlign: "left",
                  background: on ? "var(--accent-subtle)" : "var(--bg-primary)",
                  border: `1px solid ${on ? "var(--accent)" : "var(--border)"}`,
                  borderRadius: 10,
                  padding: "10px 12px",
                  cursor: "pointer",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                    {d.title}
                  </span>
                  <StatusPill status={d.status} />
                </div>
                <div style={{ display: "flex", gap: 5, marginTop: 6, flexWrap: "wrap" }}>
                  {d.bucket_ids.length === 0 && (
                    <span style={{ fontSize: 10.5, color: "var(--text-secondary)" }}>untagged</span>
                  )}
                  {d.bucket_ids.map((id) => (
                    <span
                      key={id}
                      style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 10.5 }}
                    >
                      <BucketDot color={colorFor(id)} /> {nameOf(buckets, id)}
                    </span>
                  ))}
                </div>
              </button>
            )
          })}
        </div>

        {/* Bucket picker for selected doc */}
        <div style={{ border: "1px solid var(--border)", borderRadius: 12, padding: 14, background: "var(--bg-primary)" }}>
          {!doc ? (
            <div style={{ color: "var(--text-secondary)", fontSize: 13 }}>
              Select a document to manage its buckets, status, and lifecycle.
            </div>
          ) : (
            <>
              <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>{doc.title}</div>
              <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 12 }}>
                {doc.source_type} · {doc.uri || doc.doc_id}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 8 }}>
                Pick bucket(s) — many-to-many · click to toggle
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                {buckets.map((b) => {
                  const on = draft.includes(b.bucket_id)
                  const c = colorFor(b.bucket_id)
                  return (
                    <button
                      key={b.bucket_id}
                      onClick={() => toggle(b.bucket_id)}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        gap: 8,
                        border: `1px solid ${on ? c : "var(--border)"}`,
                        background: on ? `${c}1a` : "transparent",
                        borderRadius: 10,
                        padding: "9px 11px",
                        cursor: "pointer",
                      }}
                    >
                      <span style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12 }}>
                        <BucketDot color={c} /> {b.name}
                      </span>
                      {on && <CheckCircle2 size={14} style={{ color: c }} />}
                    </button>
                  )
                })}
              </div>

              <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
                <button
                  disabled={!dirty || !canWrite}
                  onClick={() => onSetBuckets(doc.doc_id, draft)}
                  style={{ ...primaryBtn, opacity: !dirty || !canWrite ? 0.5 : 1 }}
                >
                  Save buckets
                </button>
                {doc.status === "pending" && canWrite && (
                  <button onClick={() => onApprove(doc.doc_id)} style={approveBtnStyle}>
                    <CheckCircle2 size={12} /> Approve
                  </button>
                )}
                {doc.status === "approved" && canWrite && (
                  <button onClick={() => onRetire(doc.doc_id)} style={ghostBtn}>
                    Retire
                  </button>
                )}
                {onPurge && (
                  <button
                    onClick={() => onPurge(doc.doc_id)}
                    style={{ ...ghostBtn, borderColor: "var(--danger,#ff5050)", color: "var(--danger,#ff5050)" }}
                  >
                    <Trash2 size={12} /> Purge
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Screen 03: Buckets ───────────────────────────────────────────────────

function BucketsScreen({
  buckets,
  available,
  canWrite,
  isAdmin,
  colorFor,
  onCreate,
  onDelete,
  onReindex,
}: {
  buckets: KbBucket[]
  available: boolean
  canWrite: boolean
  isAdmin: boolean
  colorFor: (id: string) => string
  onCreate: (name: string, description?: string) => Promise<KbBucket | null>
  onDelete: (bucketId: string) => Promise<void>
  onReindex: () => Promise<Record<string, unknown> | null>
}) {
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState("")
  const [desc, setDesc] = useState("")
  const [reindexing, setReindexing] = useState(false)

  const submit = async () => {
    if (!name.trim()) return
    const b = await onCreate(name.trim(), desc.trim())
    if (b) {
      setName("")
      setDesc("")
      setCreating(false)
    }
  }

  return (
    <div style={panelStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4, gap: 12, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>Knowledge Buckets</h2>
        {isAdmin && available && (
          <button
            disabled={reindexing}
            onClick={async () => {
              setReindexing(true)
              await onReindex()
              setReindexing(false)
            }}
            style={ghostBtn}
            title="Re-ingest the platform corpus into the Platform bucket"
          >
            <RefreshCw size={13} style={reindexing ? { animation: "spin 1s linear infinite" } : undefined} />
            {reindexing ? "Reindexing…" : "Reindex platform"}
          </button>
        )}
      </div>
      <p style={{ margin: "0 0 16px", color: "var(--text-secondary)", fontSize: 12.5 }}>
        Each bucket is a grounding scope. Point a task at a bucket and the agents are sealed to it.
      </p>

      {!available && <div style={{ color: "var(--text-secondary)", fontSize: 13 }}>Knowledge base offline.</div>}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))",
          gap: 14,
        }}
      >
        {buckets.map((b) => {
          const c = colorFor(b.bucket_id)
          return (
            <div
              key={b.bucket_id}
              style={{
                position: "relative",
                border: "1px solid var(--border)",
                borderRadius: 14,
                padding: 16,
                background: `linear-gradient(160deg, ${c}14, var(--bg-primary))`,
              }}
            >
              {b.is_system && (
                <span
                  style={{
                    position: "absolute",
                    top: 12,
                    right: 12,
                    fontSize: 9,
                    fontFamily: "monospace",
                    color: "#7dffb0",
                    border: "1px solid rgba(125,255,176,.4)",
                    padding: "2px 7px",
                    borderRadius: 999,
                  }}
                >
                  SYSTEM
                </span>
              )}
              <h3 style={{ margin: "0 0 4px", fontSize: 14, display: "flex", alignItems: "center", gap: 8 }}>
                <BucketDot color={c} /> {b.name}
              </h3>
              <div style={{ fontSize: 11.5, color: "var(--text-secondary)", minHeight: 30 }}>
                {b.description || "No description."}
              </div>
              <div
                style={{
                  display: "flex",
                  gap: 16,
                  marginTop: 12,
                  paddingTop: 11,
                  borderTop: "1px solid var(--border)",
                }}
              >
                <Stat v={b.doc_count} k="docs" />
                <Stat v={b.chunk_count} k="chunks" />
              </div>
              {isAdmin && !b.is_system && (
                <button
                  onClick={() => onDelete(b.bucket_id)}
                  title="Delete bucket"
                  style={{
                    position: "absolute",
                    bottom: 12,
                    right: 12,
                    border: "none",
                    background: "transparent",
                    color: "var(--text-secondary)",
                    cursor: "pointer",
                  }}
                >
                  <Trash2 size={14} />
                </button>
              )}
            </div>
          )
        })}

        {/* New bucket card */}
        {canWrite && available && (
          <div
            style={{
              border: "1.5px dashed var(--accent)",
              borderRadius: 14,
              padding: 16,
              display: "grid",
              placeItems: creating ? "stretch" : "center",
              minHeight: 140,
            }}
          >
            {creating ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <input
                  autoFocus
                  placeholder="Bucket name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  style={inputStyle}
                />
                <input
                  placeholder="Description (optional)"
                  value={desc}
                  onChange={(e) => setDesc(e.target.value)}
                  style={inputStyle}
                />
                <div style={{ display: "flex", gap: 8 }}>
                  <button onClick={submit} style={primaryBtn}>
                    Create
                  </button>
                  <button onClick={() => setCreating(false)} style={ghostBtn}>
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <button
                onClick={() => setCreating(true)}
                style={{
                  border: "none",
                  background: "transparent",
                  color: "var(--accent)",
                  cursor: "pointer",
                  display: "grid",
                  placeItems: "center",
                  gap: 6,
                }}
              >
                <Plus size={30} />
                <span style={{ fontSize: 12 }}>New bucket</span>
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function Stat({ v, k }: { v: number; k: string }) {
  return (
    <div style={{ fontFamily: "monospace" }}>
      <div style={{ fontSize: 18, color: "var(--text-primary)" }}>{v}</div>
      <div style={{ fontSize: 9.5, color: "var(--text-secondary)", textTransform: "uppercase", letterSpacing: 0.5 }}>
        {k}
      </div>
    </div>
  )
}

// ── Screen 04: Ground a Task ─────────────────────────────────────────────

interface ProjectOption {
  project_id: string
  name: string
}

function GroundScreen({
  buckets,
  available,
  canWrite,
  colorFor,
  nameFor,
  navigate,
}: {
  buckets: KbBucket[]
  available: boolean
  canWrite: boolean
  colorFor: (id: string) => string
  nameFor: (id: string) => string
  navigate: (path: string) => void
}) {
  const [text, setText] = useState("")
  const [selected, setSelected] = useState<string[]>([])
  const [projects, setProjects] = useState<ProjectOption[]>([])
  const [projectId, setProjectId] = useState("")
  const [taskType, setTaskType] = useState("research")
  const [dispatching, setDispatching] = useState(false)
  const [err, setErr] = useState("")

  useEffect(() => {
    api
      .get<{ data: any[] }>("/projects")
      .then((res) => {
        const opts = (res.data ?? []).map((p: any) => ({ project_id: p.project_id, name: p.name }))
        setProjects(opts)
        if (opts.length) setProjectId(opts[0].project_id)
      })
      .catch(() => setProjects([]))
  }, [])

  const toggle = (id: string) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))

  const dispatch = async () => {
    if (!text.trim()) {
      setErr("Describe the task first.")
      return
    }
    if (!projectId) {
      setErr("Pick a project.")
      return
    }
    setDispatching(true)
    setErr("")
    try {
      const fd = new FormData()
      fd.append("description", text)
      fd.append("task_type", taskType)
      fd.append("priority", "medium")
      fd.append("project_id", projectId)
      fd.append("bucket_ids", JSON.stringify(selected))
      const res = await api.postForm<{ data: { request_id: string } }>("/requests", fd)
      navigate(`/request/${res.data.request_id}`)
    } catch (e: any) {
      setErr(e?.message || "Dispatch failed")
      setDispatching(false)
    }
  }

  return (
    <div style={panelStyle}>
      <h2 style={{ margin: "0 0 4px", fontSize: 16 }}>New request · ground the agents</h2>
      <p style={{ margin: "0 0 16px", color: "var(--text-secondary)", fontSize: 12.5 }}>
        Pick the bucket(s) for this task. Agents retrieve only from these — structurally sealed.
      </p>

      <div style={{ border: "1px solid var(--border)", borderRadius: 12, padding: 16, background: "var(--bg-primary)" }}>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Describe the task to ground… e.g. Write a launch one-pager, on-brand and grounded in our latest market research."
          style={{
            width: "100%",
            minHeight: 84,
            background: "transparent",
            border: "none",
            resize: "vertical",
            outline: "none",
            color: "var(--text-primary)",
            fontFamily: "var(--font)",
            fontSize: 14,
            lineHeight: 1.5,
          }}
        />
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            marginTop: 14,
            paddingTop: 14,
            borderTop: "1px solid var(--border)",
            flexWrap: "wrap",
          }}
        >
          <span style={{ fontSize: 12, color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: 6 }}>
            <Lock size={13} style={{ color: "var(--accent)" }} /> Ground in bucket(s):
          </span>
          {buckets.map((b) => {
            const on = selected.includes(b.bucket_id)
            const c = colorFor(b.bucket_id)
            return (
              <button
                key={b.bucket_id}
                onClick={() => toggle(b.bucket_id)}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 7,
                  fontSize: 11.5,
                  padding: "6px 11px",
                  borderRadius: 999,
                  cursor: "pointer",
                  fontFamily: "monospace",
                  color: "var(--text-primary)",
                  border: `1px solid ${on ? c : "var(--border)"}`,
                  background: on ? `${c}22` : "transparent",
                }}
              >
                <BucketDot color={c} /> {b.name}
                {on && <X size={11} />}
              </button>
            )
          })}
          {selected.length === 0 && (
            <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>
              none → platform knowledge only
            </span>
          )}
        </div>
      </div>

      {/* Scope viz — grounded vs blocked */}
      <div style={{ marginTop: 18, border: "1px solid var(--border)", borderRadius: 12, padding: 16, background: "var(--bg-primary)" }}>
        <div
          style={{
            fontSize: 11,
            color: "var(--text-secondary)",
            fontFamily: "monospace",
            marginBottom: 14,
            display: "flex",
            justifyContent: "space-between",
          }}
        >
          <span>RETRIEVAL SCOPE — hard-sealed to selected buckets</span>
          <span style={{ color: "#7dffb0" }}>● isolation enforced</span>
        </div>
        <div style={{ display: "flex", gap: 18, flexWrap: "wrap", justifyContent: "center" }}>
          {buckets.map((b) => {
            const grounded = selected.includes(b.bucket_id)
            const c = colorFor(b.bucket_id)
            return (
              <div key={b.bucket_id} style={{ width: 110, textAlign: "center", opacity: grounded ? 1 : 0.45 }}>
                <div
                  style={{
                    width: 60,
                    height: 60,
                    borderRadius: "50%",
                    margin: "0 auto 8px",
                    display: "grid",
                    placeItems: "center",
                    border: `1px solid ${grounded ? c : "var(--border)"}`,
                    boxShadow: grounded ? `0 0 20px ${c}66` : "none",
                    color: grounded ? c : "var(--text-secondary)",
                    fontSize: 18,
                  }}
                >
                  ◈
                </div>
                <div style={{ fontSize: 11, color: "var(--text-primary)" }}>{b.name}</div>
                <div style={{ fontSize: 9, fontFamily: "monospace", marginTop: 2, color: grounded ? "#7dffb0" : "#ff6b6b" }}>
                  {grounded ? "▸ grounded" : "✕ blocked"}
                </div>
              </div>
            )
          })}
        </div>
        <div
          style={{
            marginTop: 16,
            fontSize: 12,
            color: "var(--text-secondary)",
            fontFamily: "monospace",
            padding: "11px 13px",
            border: "1px solid var(--border)",
            borderRadius: 10,
            background: "var(--accent-subtle)",
          }}
        >
          retrieval locked to {selected.length} bucket(s) · claims cite sources or get flagged ·
          agent cannot widen scope
        </div>
      </div>

      {err && <div style={{ color: "var(--danger,#ff5050)", fontSize: 13, marginTop: 12 }}>{err}</div>}

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 16, alignItems: "center", flexWrap: "wrap" }}>
        <select value={taskType} onChange={(e) => setTaskType(e.target.value)} style={selectStyle}>
          <option value="research">Research</option>
          <option value="content_creation">Content</option>
          <option value="feature_request">Feature</option>
        </select>
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)} style={selectStyle}>
          {projects.length === 0 && <option value="">No projects</option>}
          {projects.map((p) => (
            <option key={p.project_id} value={p.project_id}>
              {p.name}
            </option>
          ))}
        </select>
        <button
          disabled={!canWrite || dispatching || !available}
          onClick={dispatch}
          style={{ ...primaryBtn, padding: "11px 20px", opacity: !canWrite || dispatching || !available ? 0.5 : 1 }}
          title={!available ? "Knowledge base offline — task will run without retrieval" : undefined}
        >
          <Zap size={14} /> {dispatching ? "Dispatching…" : "Dispatch grounded task"}
        </button>
      </div>
      {selected.length > 0 && (
        <div style={{ textAlign: "right", marginTop: 8, fontSize: 11, color: "var(--text-secondary)" }}>
          grounding: {selected.map(nameFor).join(" · ")}
        </div>
      )}
    </div>
  )
}

// ── Shared button styles ─────────────────────────────────────────────────

const primaryBtn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 7,
  fontSize: 13,
  padding: "9px 16px",
  borderRadius: 10,
  border: "none",
  cursor: "pointer",
  color: "var(--bg-primary)",
  background: "var(--accent)",
  fontWeight: 700,
  fontFamily: "var(--font)",
}

const ghostBtn: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  fontSize: 12.5,
  padding: "8px 14px",
  borderRadius: 10,
  border: "1px solid var(--border)",
  cursor: "pointer",
  color: "var(--text-primary)",
  background: "transparent",
  fontFamily: "var(--font)",
}

const inputStyle: React.CSSProperties = {
  background: "var(--bg-secondary)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  padding: "8px 10px",
  color: "var(--text-primary)",
  fontSize: 13,
  fontFamily: "var(--font)",
}

const selectStyle: React.CSSProperties = {
  background: "var(--bg-secondary)",
  border: "1px solid var(--border)",
  borderRadius: 10,
  padding: "9px 12px",
  color: "var(--text-primary)",
  fontSize: 13,
  fontFamily: "var(--font)",
  cursor: "pointer",
}

// ── Screen 05: Add Article (KB-PL · A1) ───────────────────────────────────
// Ingest an external article by URL (via Firecrawl) or by pasting text (the
// ToS-safe LinkedIn path), tagged into topic buckets. Lands in the personal
// library; auto-approved so it's searchable immediately.

function AddArticleScreen({
  buckets,
  documents,
  available,
  canWrite,
  colorFor,
  onIngestUrl,
  onIngestText,
}: {
  buckets: KbBucket[]
  documents: KbDocument[]
  available: boolean
  canWrite: boolean
  colorFor: (id: string) => string
  onIngestUrl: (url: string, bucketIds: string[], title?: string) => Promise<KbIngestResult | null>
  onIngestText: (
    text: string, title: string, bucketIds: string[], sourceUrl?: string,
  ) => Promise<KbIngestResult | null>
}) {
  const [mode, setMode] = useState<"url" | "paste">("url")
  const [url, setUrl] = useState("")
  const [text, setText] = useState("")
  const [title, setTitle] = useState("")
  const [sourceUrl, setSourceUrl] = useState("")
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<KbIngestResult | null>(null)
  const disabled = !available || !canWrite || busy

  const toggle = (id: string) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))

  const submit = async () => {
    setBusy(true)
    setResult(null)
    let r: KbIngestResult | null = null
    if (mode === "url") {
      if (!url.trim()) {
        setBusy(false)
        return
      }
      r = await onIngestUrl(url.trim(), selected, title.trim() || undefined)
      if (r) {
        setUrl("")
        setTitle("")
      }
    } else {
      if (!text.trim() || !title.trim()) {
        setBusy(false)
        return
      }
      r = await onIngestText(text.trim(), title.trim(), selected, sourceUrl.trim() || undefined)
      if (r) {
        setText("")
        setTitle("")
        setSourceUrl("")
      }
    }
    setResult(r)
    setBusy(false)
  }

  const recent = documents
    .filter((d) => d.source_type === "web" || d.source_type === "paste")
    .slice(0, 8)

  return (
    <div style={panelStyle}>
      <h2 style={{ margin: "0 0 4px", fontSize: 16 }}>Add an article to your library</h2>
      <p style={{ margin: "0 0 16px", color: "var(--text-secondary)", fontSize: 12.5 }}>
        Fetch a web page by URL, or paste text (e.g. a LinkedIn post). It is cleaned, chunked,
        embedded, and — being your own library — made searchable immediately.
      </p>

      {/* Mode toggle */}
      <div
        style={{
          display: "inline-flex",
          border: "1px solid var(--border)",
          borderRadius: 10,
          overflow: "hidden",
          marginBottom: 18,
        }}
      >
        {([["url", "From URL", Link2], ["paste", "Paste text", ClipboardPaste]] as const).map(
          ([m, label, Icon]) => {
            const on = mode === m
            return (
              <button
                key={m}
                onClick={() => {
                  setMode(m)
                  setResult(null)
                }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 7,
                  fontSize: 12.5,
                  padding: "9px 16px",
                  cursor: "pointer",
                  fontFamily: "var(--font)",
                  border: "none",
                  color: on ? "var(--accent)" : "var(--text-secondary)",
                  background: on ? "var(--accent-subtle)" : "transparent",
                  fontWeight: on ? 700 : 500,
                }}
              >
                <Icon size={15} /> {label}
              </button>
            )
          },
        )}
      </div>

      {mode === "url" ? (
        <>
          <label style={fieldLabel}>Article URL</label>
          <div style={{ display: "flex", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com/article-on-agentic-ai"
              disabled={disabled}
              style={{ ...inputStyle, flex: 1, minWidth: 240, padding: "12px 14px" }}
            />
            <button
              onClick={submit}
              disabled={disabled || !url.trim()}
              style={kbplBtn(disabled || !url.trim())}
            >
              {busy ? "Fetching…" : "Fetch & Save"}
            </button>
          </div>
          <label style={fieldLabel}>Title (optional — auto-detected from the page)</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Leave blank to use the page title"
            disabled={disabled}
            style={{ ...inputStyle, width: "100%", padding: "12px 14px", marginBottom: 16 }}
          />
        </>
      ) : (
        <>
          <label style={fieldLabel}>Title</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Multi-Agent Orchestration for Loan Underwriting"
            disabled={disabled}
            style={{ ...inputStyle, width: "100%", padding: "12px 14px", marginBottom: 12 }}
          />
          <label style={fieldLabel}>Article text</label>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste the full text of the article or post here…"
            disabled={disabled}
            style={{
              ...inputStyle,
              width: "100%",
              minHeight: 140,
              padding: "12px 14px",
              resize: "vertical",
              fontFamily: "monospace",
              fontSize: 12.5,
              lineHeight: 1.5,
              marginBottom: 12,
            }}
          />
          <label style={fieldLabel}>Source link (optional)</label>
          <div style={{ display: "flex", gap: 10, marginBottom: 16, flexWrap: "wrap" }}>
            <input
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              placeholder="https://linkedin.com/posts/…"
              disabled={disabled}
              style={{ ...inputStyle, flex: 1, minWidth: 240, padding: "12px 14px" }}
            />
            <button
              onClick={submit}
              disabled={disabled || !text.trim() || !title.trim()}
              style={kbplBtn(disabled || !text.trim() || !title.trim())}
            >
              {busy ? "Saving…" : "Save to Library"}
            </button>
          </div>
        </>
      )}

      {/* Bucket assignment */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          margin: "4px 0 6px",
          flexWrap: "wrap",
        }}
      >
        <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>Add to topic bucket(s):</span>
        {buckets.length === 0 && (
          <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
            No buckets yet — create one in the Buckets tab.
          </span>
        )}
        {buckets.map((b) => {
          const on = selected.includes(b.bucket_id)
          const c = colorFor(b.bucket_id)
          return (
            <button
              key={b.bucket_id}
              onClick={() => toggle(b.bucket_id)}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 7,
                fontSize: 11.5,
                padding: "6px 11px",
                borderRadius: 999,
                cursor: "pointer",
                fontFamily: "monospace",
                color: "var(--text-primary)",
                border: `1px solid ${on ? c : "var(--border)"}`,
                background: on ? `${c}22` : "transparent",
              }}
            >
              <BucketDot color={c} /> {b.name}
            </button>
          )
        })}
      </div>

      {/* Result banner */}
      {result && (
        <div
          style={{
            marginTop: 16,
            padding: "12px 14px",
            borderRadius: 10,
            fontSize: 12.5,
            border: `1px solid ${result.skipped ? "var(--border)" : "#7dffb0"}`,
            background: result.skipped ? "var(--bg-card)" : "rgba(125,255,176,.08)",
            color: "var(--text-primary)",
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
        >
          <CheckCircle2
            size={16}
            style={{ color: result.skipped ? "var(--text-secondary)" : "#7dffb0" }}
          />
          {result.skipped ? (
            <span>
              Already in your library — <strong>{result.title}</strong> (no duplicate created).
            </span>
          ) : (
            <span>
              Saved <strong>{result.title}</strong> — {result.chunks} chunk
              {result.chunks === 1 ? "" : "s"} embedded · <StatusPill status={result.status} /> · now
              searchable.
            </span>
          )}
        </div>
      )}

      {/* Recently added from web/paste */}
      {recent.length > 0 && (
        <div style={{ marginTop: 22 }}>
          <h3 style={{ fontSize: 13, margin: "0 0 10px", color: "var(--text-secondary)" }}>
            Recently added to your library
          </h3>
          {recent.map((d) => (
            <div
              key={d.doc_id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "9px 12px",
                border: "1px solid var(--border)",
                borderRadius: 10,
                marginBottom: 8,
                background: "var(--bg-card)",
                fontSize: 12.5,
              }}
            >
              <span style={{ ...sourcePill(d.source_type) }}>{d.source_type}</span>
              <span style={{ flex: 1, color: "var(--text-primary)" }}>{d.title}</span>
              {d.uri && (
                <a
                  href={d.uri}
                  target="_blank"
                  rel="noreferrer"
                  style={{
                    color: "var(--accent)",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    fontSize: 11.5,
                  }}
                >
                  <ExternalLink size={12} /> source
                </a>
              )}
              <StatusPill status={d.status} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Screen 06: Search Library (KB-PL · B1) ─────────────────────────────────
// Human search over the personal library: query box + bucket filters, ranked
// result cards each carrying the original source link (give-me-references).

function SearchLibraryScreen({
  buckets,
  available,
  colorFor,
  nameFor,
  onSearch,
}: {
  buckets: KbBucket[]
  available: boolean
  colorFor: (id: string) => string
  nameFor: (id: string) => string
  onSearch: (query: string, bucketIds?: string[], topK?: number) => Promise<KbSearchResult[]>
}) {
  void nameFor
  const [query, setQuery] = useState("")
  const [filter, setFilter] = useState<string[]>([])
  const [results, setResults] = useState<KbSearchResult[]>([])
  const [busy, setBusy] = useState(false)
  const [searched, setSearched] = useState(false)

  const toggleFilter = (id: string) =>
    setFilter((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))

  const run = async () => {
    if (!query.trim()) return
    setBusy(true)
    const r = await onSearch(query.trim(), filter.length ? filter : undefined, 10)
    setResults(r)
    setSearched(true)
    setBusy(false)
  }

  const maxScore = results.length ? Math.max(...results.map((r) => r.score)) || 1 : 1

  return (
    <div style={panelStyle}>
      <h2 style={{ margin: "0 0 4px", fontSize: 16 }}>Search your knowledge library</h2>
      <p style={{ margin: "0 0 16px", color: "var(--text-secondary)", fontSize: 12.5 }}>
        Semantic + keyword search across everything you have saved. Results link back to the
        original source.
      </p>

      <div style={{ display: "flex", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") run()
          }}
          placeholder="e.g. Agentic AI Architecture in Banking Industry"
          disabled={!available}
          style={{ ...inputStyle, flex: 1, minWidth: 260, padding: "12px 14px" }}
        />
        <button
          onClick={run}
          disabled={!available || busy || !query.trim()}
          style={kbplBtn(!available || busy || !query.trim())}
        >
          {busy ? "Searching…" : "Search"}
        </button>
      </div>

      {/* Bucket filters */}
      {buckets.length > 0 && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            marginBottom: 20,
            flexWrap: "wrap",
          }}
        >
          <span style={{ fontSize: 12, color: "var(--text-secondary)", marginRight: 2 }}>
            Filter:
          </span>
          {buckets.map((b) => {
            const on = filter.includes(b.bucket_id)
            const c = colorFor(b.bucket_id)
            return (
              <button
                key={b.bucket_id}
                onClick={() => toggleFilter(b.bucket_id)}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  fontSize: 11.5,
                  padding: "5px 10px",
                  borderRadius: 999,
                  cursor: "pointer",
                  fontFamily: "monospace",
                  color: "var(--text-primary)",
                  border: `1px solid ${on ? c : "var(--border)"}`,
                  background: on ? `${c}22` : "transparent",
                }}
              >
                <BucketDot color={c} /> {b.name}
              </button>
            )
          })}
        </div>
      )}

      {/* Results */}
      {searched && results.length === 0 && (
        <div
          style={{
            padding: "24px",
            textAlign: "center",
            color: "var(--text-secondary)",
            fontSize: 13,
          }}
        >
          No results. Try a different query{filter.length ? " or clear the bucket filter" : ""}.
        </div>
      )}
      {results.map((r) => (
        <div
          key={r.doc_id}
          style={{
            border: "1px solid var(--border)",
            borderRadius: 12,
            padding: "15px 16px",
            marginBottom: 11,
            background: "var(--bg-card)",
          }}
        >
          <h4 style={{ margin: "0 0 5px", fontSize: 14, color: "var(--text-primary)" }}>
            {r.title}
          </h4>
          <p
            style={{
              margin: "0 0 10px",
              color: "var(--text-secondary)",
              fontSize: 12.5,
              lineHeight: 1.5,
            }}
          >
            {r.snippet}
          </p>
          <div
            style={{
              display: "flex",
              gap: 14,
              alignItems: "center",
              flexWrap: "wrap",
              fontFamily: "monospace",
              fontSize: 11,
            }}
          >
            <span style={{ color: "#7dffb0" }}>{r.score.toFixed(3)}</span>
            <span
              style={{
                flex: "0 1 120px",
                height: 5,
                background: "rgba(255,255,255,.08)",
                borderRadius: 3,
                overflow: "hidden",
              }}
            >
              <span
                style={{
                  display: "block",
                  height: "100%",
                  width: `${Math.round((r.score / maxScore) * 100)}%`,
                  background: "linear-gradient(90deg,var(--accent),#7dffb0)",
                }}
              />
            </span>
            {r.uri && (
              <a
                href={r.uri}
                target="_blank"
                rel="noreferrer"
                style={{
                  color: "var(--accent)",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  textDecoration: "none",
                }}
              >
                <ExternalLink size={12} /> {linkLabel(r.uri)}
              </a>
            )}
            {!!r.more_matches && r.more_matches > 0 && (
              <span style={{ color: "var(--text-secondary)" }}>
                +{r.more_matches} more match{r.more_matches === 1 ? "" : "es"} in this doc
              </span>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Shared helpers for the KB-PL screens ───────────────────────────────────

const fieldLabel: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  color: "var(--text-secondary)",
  fontFamily: "monospace",
  margin: "0 0 6px",
  textTransform: "uppercase",
  letterSpacing: 0.5,
}

function kbplBtn(disabled: boolean): React.CSSProperties {
  return {
    fontFamily: "var(--font)",
    fontSize: 12.5,
    fontWeight: 700,
    borderRadius: 10,
    padding: "12px 18px",
    cursor: disabled ? "not-allowed" : "pointer",
    border: "1px solid var(--accent)",
    color: "var(--accent)",
    background: "var(--accent-subtle)",
    opacity: disabled ? 0.5 : 1,
    whiteSpace: "nowrap",
  }
}

function sourcePill(sourceType: string): React.CSSProperties {
  const c =
    sourceType === "web"
      ? "#34e7ff"
      : sourceType === "paste"
        ? "#ff45d0"
        : "var(--text-secondary)"
  return {
    fontFamily: "monospace",
    fontSize: 10,
    padding: "3px 8px",
    borderRadius: 6,
    color: c,
    border: `1px solid ${c}`,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  }
}

function linkLabel(uri: string): string {
  try {
    const u = new URL(uri)
    return u.hostname.replace(/^www\./, "")
  } catch {
    return "source"
  }
}
