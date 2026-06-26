import { useState, useEffect, useCallback, useRef } from "react"
import { api } from "../lib/api"
import { MarkdownRenderer } from "../components/ui/MarkdownRenderer"
import { Code2, FileText, Gavel, ChevronDown, ChevronRight, Loader2, Upload, X, File } from "lucide-react"

// ── Types ───────────────────────────────────────────────────────────

interface CouncilSession {
  council_id: string
  agent_type: "code_reviewer" | "document_reviewer"
  title: string
  review_report: string
  focus_areas: string[]
  created_at: string
  preview?: string
}

interface CouncilResult {
  council_id: string
  agent_type: string
  title: string
  review_report: string
  focus_areas: string[]
  created_at: string
  mock: boolean
  source_filename?: string
}

// ── Constants ───────────────────────────────────────────────────────

const LANGUAGES = ["TypeScript", "Python", "Go", "Java", "Rust", "C#", "Other"]
const DOC_TYPES = ["PRD", "Spec", "Proposal", "Guide", "RFC", "Other"]
const FOCUS_OPTIONS = ["Security", "Performance", "Readability", "Correctness"]

const UPLOAD_ACCEPT = ".pdf,.docx,.xlsx,.md,.txt,.py,.ts,.tsx,.js,.go,.java,.rs,.cs,.json,.yaml,.yml"
const UPLOAD_MAX_MB = 25
const UPLOAD_MAX_BYTES = UPLOAD_MAX_MB * 1024 * 1024
const UPLOAD_ALLOWED_EXTS = new Set(
  UPLOAD_ACCEPT.split(",").map((e) => e.trim().toLowerCase()),
)

// ── Helpers ─────────────────────────────────────────────────────────

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

// ── Page ────────────────────────────────────────────────────────────

export function AgentCouncilPage() {
  // Form state
  const [agentType, setAgentType] = useState<"code_reviewer" | "document_reviewer">("code_reviewer")
  const [inputMode, setInputMode] = useState<"paste" | "upload">("paste")
  const [content, setContent] = useState("")
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState("")
  const [language, setLanguage] = useState("TypeScript")
  const [documentType, setDocumentType] = useState("PRD")
  const [focusAreas, setFocusAreas] = useState<string[]>(["Security", "Performance", "Readability", "Correctness"])
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Submission state
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [result, setResult] = useState<CouncilResult | null>(null)

  // History state
  const [sessions, setSessions] = useState<CouncilSession[]>([])
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [expandedReport, setExpandedReport] = useState<string | null>(null)
  const [expandingId, setExpandingId] = useState<string | null>(null)

  // Load history on mount
  useEffect(() => {
    let cancelled = false
    const fetch = async () => {
      try {
        const res = await api.get<{ data: CouncilSession[] }>("/council")
        if (!cancelled) setSessions(res.data || [])
      } catch {
        // soft-fail — history is non-critical
      }
    }
    fetch()
    return () => { cancelled = true }
  }, [])

  const refreshHistory = useCallback(async () => {
    try {
      const res = await api.get<{ data: CouncilSession[] }>("/council")
      setSessions(res.data || [])
    } catch {
      // soft-fail
    }
  }, [])

  // Toggle focus area
  const toggleFocus = (area: string) => {
    setFocusAreas((prev) =>
      prev.includes(area) ? prev.filter((a) => a !== area) : [...prev, area],
    )
  }

  // Handle file selection + client-side validation
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    setFileError("")
    if (!f) {
      setSelectedFile(null)
      return
    }
    // Extension check
    const ext = "." + (f.name.split(".").pop()?.toLowerCase() ?? "")
    if (!UPLOAD_ALLOWED_EXTS.has(ext)) {
      setFileError(`Unsupported file type (${ext}). Accepted: PDF, DOCX, XLSX, MD, TXT, source code.`)
      setSelectedFile(null)
      return
    }
    // Size check
    if (f.size > UPLOAD_MAX_BYTES) {
      setFileError(`File exceeds ${UPLOAD_MAX_MB} MB limit (${(f.size / 1024 / 1024).toFixed(1)} MB).`)
      setSelectedFile(null)
      return
    }
    setSelectedFile(f)
  }

  const clearFile = () => {
    setSelectedFile(null)
    setFileError("")
    if (fileInputRef.current) fileInputRef.current.value = ""
  }

  // Submit review — branches on paste vs upload
  const handleSubmit = async () => {
    setLoading(true)
    setError("")
    setResult(null)
    try {
      if (inputMode === "upload" && selectedFile) {
        const fd = new FormData()
        fd.append("file", selectedFile)
        fd.append("agent_type", agentType)
        if (agentType === "code_reviewer") fd.append("language", language)
        else fd.append("document_type", documentType)
        fd.append("focus_areas", JSON.stringify(focusAreas))
        const res = await api.postForm<{ data: CouncilResult }>("/council/upload", fd)
        setResult(res.data)
      } else {
        const body: Record<string, unknown> = {
          agent_type: agentType,
          content: content.trim(),
          focus_areas: focusAreas,
        }
        if (agentType === "code_reviewer") body.language = language
        else body.document_type = documentType
        const res = await api.post<{ data: CouncilResult }>("/council", body)
        setResult(res.data)
      }
      await refreshHistory()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error"
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  // Expand a history item
  const handleExpand = async (session: CouncilSession) => {
    if (expandedId === session.council_id) {
      setExpandedId(null)
      setExpandedReport(null)
      return
    }
    setExpandedId(session.council_id)
    setExpandingId(session.council_id)
    try {
      const res = await api.get<{ data: CouncilSession }>(`/council/${session.council_id}`)
      setExpandedReport(res.data.review_report)
    } catch {
      setExpandedReport("(Failed to load review report)")
    } finally {
      setExpandingId(null)
    }
  }

  const canSubmit =
    !loading &&
    (inputMode === "upload" ? selectedFile !== null : content.trim().length > 0)

  // ── Styles ──────────────────────────────────────────────────────

  const sectionStyle: React.CSSProperties = {
    background: "var(--bg-card)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius)",
    padding: 24,
  }

  const btnBase: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 8,
    padding: "10px 18px",
    fontSize: 13,
    fontWeight: 600,
    fontFamily: "var(--font)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius)",
    cursor: "pointer",
    background: "var(--bg-input, var(--bg-card))",
    color: "var(--text-secondary)",
    transition: "background 0.15s, color 0.15s, border-color 0.15s",
  }

  const btnActive: React.CSSProperties = {
    ...btnBase,
    background: "var(--accent-subtle)",
    color: "var(--accent)",
    borderColor: "var(--accent)",
  }

  const selectStyle: React.CSSProperties = {
    padding: "6px 10px",
    fontSize: 13,
    background: "var(--bg-input, var(--bg-card))",
    color: "var(--text-primary)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius)",
    fontFamily: "var(--font)",
    cursor: "pointer",
  }

  return (
    <div style={{ maxWidth: 1200, margin: "0 auto", padding: "24px 36px", display: "flex", flexDirection: "column", gap: 32 }}>
      {/* ── Page Header ── */}
      <div>
        <h1 style={{ color: "var(--text-primary)", fontSize: 22, fontWeight: 700, margin: 0, display: "flex", alignItems: "center", gap: 10 }}>
          <Gavel size={22} />
          Agent Council
        </h1>
        <p style={{ color: "var(--text-secondary)", fontSize: 13, margin: "6px 0 0" }}>
          Ad-hoc, one-shot reviews — outside the workflow pipeline. Paste code or a document, pick a reviewer, get a structured report.
        </p>
      </div>

      {/* ── Reviewer Selector ── */}
      <div style={sectionStyle}>
        <h2 style={{ color: "var(--text-primary)", fontSize: 14, fontWeight: 600, margin: "0 0 12px" }}>Select Reviewer</h2>
        <div style={{ display: "flex", gap: 10 }}>
          <button
            type="button"
            style={agentType === "code_reviewer" ? btnActive : btnBase}
            onClick={() => setAgentType("code_reviewer")}
          >
            <Code2 size={16} />
            Code Quality Reviewer
          </button>
          <button
            type="button"
            style={agentType === "document_reviewer" ? btnActive : btnBase}
            onClick={() => setAgentType("document_reviewer")}
          >
            <FileText size={16} />
            Document Quality Reviewer
          </button>
        </div>
      </div>

      {/* ── Content Input ── */}
      <div style={sectionStyle}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
          <h2 style={{ color: "var(--text-primary)", fontSize: 14, fontWeight: 600, margin: 0 }}>
            {agentType === "code_reviewer" ? "Code to review" : "Document to review"}
          </h2>
          {/* Paste / Upload toggle */}
          <div style={{ display: "flex", borderRadius: "var(--radius)", overflow: "hidden", border: "1px solid var(--border)" }}>
            <button
              type="button"
              onClick={() => { setInputMode("paste"); setSelectedFile(null); setFileError("") }}
              style={{
                padding: "6px 14px", fontSize: 12, fontWeight: 600, fontFamily: "var(--font)",
                border: "none", cursor: "pointer",
                background: inputMode === "paste" ? "var(--accent)" : "var(--bg-input)",
                color: inputMode === "paste" ? "#fff" : "var(--text-secondary)",
                borderRight: "1px solid var(--border)",
                transition: "background 0.15s, color 0.15s",
              }}
            >
              ✏️ Paste
            </button>
            <button
              type="button"
              onClick={() => { setInputMode("upload"); setContent("") }}
              style={{
                padding: "6px 14px", fontSize: 12, fontWeight: 600, fontFamily: "var(--font)",
                border: "none", cursor: "pointer",
                background: inputMode === "upload" ? "var(--accent)" : "var(--bg-input)",
                color: inputMode === "upload" ? "#fff" : "var(--text-secondary)",
                transition: "background 0.15s, color 0.15s",
              }}
            >
              <Upload size={12} style={{ marginRight: 4 }} />
              Upload
            </button>
          </div>
        </div>

        {/* Paste mode */}
        {inputMode === "paste" && (
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder={
              agentType === "code_reviewer"
                ? "Paste the code you want reviewed..."
                : "Paste the document (PRD, spec, proposal, guide...) you want reviewed..."
            }
            style={{
              width: "100%",
              minHeight: 400,
              padding: "14px 16px",
              fontSize: 13,
              fontFamily: "var(--font-mono)",
              background: "var(--bg-primary)",
              color: "var(--text-primary)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              resize: "vertical",
              boxSizing: "border-box",
            }}
          />
        )}

        {/* Upload mode */}
        {inputMode === "upload" && (
          <div>
            <div
              style={{
                border: `2px dashed ${selectedFile ? "var(--accent)" : "var(--border)"}`,
                borderRadius: "var(--radius)",
                padding: "40px 20px",
                textAlign: "center",
                background: selectedFile ? "var(--accent-subtle)" : "var(--bg-primary)",
                transition: "border-color 0.15s, background 0.15s",
                cursor: "pointer",
              }}
              onClick={() => fileInputRef.current?.click()}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept={UPLOAD_ACCEPT}
                onChange={handleFileChange}
                style={{ display: "none" }}
              />
              {selectedFile ? (
                <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10 }}>
                  <File size={20} style={{ color: "var(--accent)" }} />
                  <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
                    {selectedFile.name}
                  </span>
                  <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                    ({(selectedFile.size / 1024).toFixed(1)} KB)
                  </span>
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); clearFile() }}
                    style={{
                      display: "inline-flex", alignItems: "center", gap: 4,
                      padding: "4px 10px", fontSize: 11, fontWeight: 600,
                      fontFamily: "var(--font)", cursor: "pointer",
                      background: "transparent", color: "var(--text-muted)",
                      border: "1px solid var(--border)", borderRadius: "var(--radius)",
                    }}
                  >
                    <X size={12} /> Remove
                  </button>
                </div>
              ) : (
                <div style={{ color: "var(--text-muted)", fontSize: 14 }}>
                  <Upload size={28} style={{ marginBottom: 8, opacity: 0.5 }} />
                  <p style={{ margin: 0 }}>Click to choose a file</p>
                  <p style={{ margin: "4px 0 0", fontSize: 11, color: "var(--text-muted)" }}>
                    Accepted: PDF, DOCX, XLSX, MD, TXT, source code · Max {UPLOAD_MAX_MB} MB
                  </p>
                </div>
              )}
            </div>
            {fileError && (
              <div style={{
                marginTop: 10, padding: "8px 12px",
                borderRadius: "var(--radius)",
                background: "var(--danger-subtle, rgba(255,0,0,0.08))",
                color: "var(--danger)",
                fontSize: 12, border: "1px solid var(--danger)",
              }}>
                {fileError}
              </div>
            )}
          </div>
        )}

        {/* Context fields + focus areas */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 12, flexWrap: "wrap", gap: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            {/* Context field — switches based on agent type */}
            {agentType === "code_reviewer" ? (
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <label style={{ fontSize: 12, color: "var(--text-secondary)", whiteSpace: "nowrap" }}>Language:</label>
                <select value={language} onChange={(e) => setLanguage(e.target.value)} style={selectStyle}>
                  {LANGUAGES.map((l) => <option key={l} value={l}>{l}</option>)}
                </select>
              </div>
            ) : (
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <label style={{ fontSize: 12, color: "var(--text-secondary)", whiteSpace: "nowrap" }}>Document type:</label>
                <select value={documentType} onChange={(e) => setDocumentType(e.target.value)} style={selectStyle}>
                  {DOC_TYPES.map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              </div>
            )}

            {/* Focus areas */}
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>Focus:</span>
            {FOCUS_OPTIONS.map((area) => {
              const active = focusAreas.includes(area)
              return (
                <button
                  key={area}
                  type="button"
                  onClick={() => toggleFocus(area)}
                  style={{
                    borderRadius: 9999,
                    padding: "4px 12px",
                    fontSize: 12,
                    fontWeight: 500,
                    cursor: "pointer",
                    fontFamily: "var(--font)",
                    border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
                    background: active ? "var(--accent-subtle)" : "var(--bg-hover)",
                    color: active ? "var(--accent)" : "var(--text-secondary)",
                    transition: "background 0.15s, color 0.15s",
                  }}
                >
                  {active ? "◉" : "◌"} {area}
                </button>
              )
            })}
          </div>
        </div>

        {/* Error */}
        {error && (
          <div style={{
            marginTop: 12, padding: "10px 14px",
            borderRadius: "var(--radius)",
            background: "var(--danger-subtle, rgba(255,0,0,0.08))",
            color: "var(--danger)",
            fontSize: 13,
            border: "1px solid var(--danger)",
          }}>
            {error}
          </div>
        )}

        {/* Submit */}
        <div style={{ marginTop: 16, display: "flex", alignItems: "center", gap: 12 }}>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!canSubmit}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
              padding: "10px 22px",
              fontSize: 14,
              fontWeight: 600,
              fontFamily: "var(--font)",
              border: "none",
              borderRadius: "var(--radius)",
              background: canSubmit ? "var(--accent)" : "var(--bg-hover)",
              color: canSubmit ? "#fff" : "var(--text-muted)",
              cursor: canSubmit ? "pointer" : "not-allowed",
              opacity: loading ? 0.6 : 1,
              transition: "background 0.15s",
            }}
          >
            {loading ? (
              <>
                <Loader2 size={16} style={{ animation: "spin 1s linear infinite" }} />
                Reviewing…
              </>
            ) : (
              "Submit for Review"
            )}
          </button>
          {loading && (
            <span style={{ fontSize: 13, color: "var(--text-muted)" }}>
              This may take 30–60 seconds…
            </span>
          )}
        </div>
      </div>

      {/* ── Result ── */}
      {result && (
        <div style={sectionStyle}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <h2 style={{ color: "var(--text-primary)", fontSize: 16, fontWeight: 600, margin: 0 }}>
                Review Result
              </h2>
              {result.source_filename && (
                <span style={{
                  fontSize: 11, fontFamily: "var(--font-mono)",
                  color: "var(--accent)", background: "var(--accent-subtle)",
                  padding: "2px 8px", borderRadius: "var(--radius)",
                }}>
                  📄 {result.source_filename}
                </span>
              )}
            </div>
            <span style={{ fontSize: 12, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {result.council_id}
            </span>
          </div>

          {result.mock && (
            <div style={{
              padding: "12px 16px", marginBottom: 16,
              borderRadius: "var(--radius)",
              background: "var(--warning, rgba(245,166,35,0.1))",
              border: "1px solid var(--warning, #f5a623)",
              color: "var(--warning, #f5a623)",
              fontSize: 13,
              fontWeight: 600,
            }}>
              ⚠️ MOCK — this is NOT a real review. The agent system is running in mock mode.
            </div>
          )}

          <MarkdownRenderer content={result.review_report} />
        </div>
      )}

      {/* ── Past Reviews (History) ── */}
      <div>
        <h2 style={{ color: "var(--text-primary)", fontSize: 18, fontWeight: 600, marginBottom: 12 }}>
          <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>02</span>{" "}
          Past Reviews
        </h2>
        <div style={{ height: 2, background: "var(--border)", marginBottom: 16 }} />

        {sessions.length === 0 ? (
          <div style={{ padding: "60px 0", textAlign: "center", color: "var(--text-muted)", fontSize: 14 }}>
            <Gavel size={36} style={{ marginBottom: 12, opacity: 0.4 }} />
            <p>No reviews yet. Submit your first code or document above.</p>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {sessions.map((s) => {
              const isExpanded = expandedId === s.council_id
              const isExpanding = expandingId === s.council_id
              return (
                <div
                  key={s.council_id}
                  style={{
                    background: "var(--bg-card)",
                    border: `1px solid ${isExpanded ? "var(--accent)" : "var(--border)"}`,
                    borderRadius: "var(--radius)",
                    overflow: "hidden",
                    transition: "border-color 0.15s",
                  }}
                >
                  {/* Header row */}
                  <button
                    type="button"
                    onClick={() => handleExpand(s)}
                    style={{
                      width: "100%",
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      padding: "12px 16px",
                      background: "transparent",
                      border: "none",
                      cursor: "pointer",
                      fontFamily: "var(--font)",
                      color: "var(--text-primary)",
                      textAlign: "left" as const,
                    }}
                    onMouseEnter={(e) => {
                      if (!isExpanded) e.currentTarget.style.background = "var(--bg-hover)"
                    }}
                    onMouseLeave={(e) => {
                      if (!isExpanded) e.currentTarget.style.background = "transparent"
                    }}
                  >
                    {s.agent_type === "code_reviewer" ? (
                      <Code2 size={16} style={{ color: "var(--accent)", flexShrink: 0 }} />
                    ) : (
                      <FileText size={16} style={{ color: "var(--accent)", flexShrink: 0 }} />
                    )}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {s.title}
                      </div>
                      {s.preview && !isExpanded && (
                        <div style={{ fontSize: 12, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginTop: 2 }}>
                          {s.preview}
                        </div>
                      )}
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                        {s.focus_areas.join(", ")}
                      </span>
                      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                        {relativeTime(s.created_at)}
                      </span>
                      {isExpanding ? (
                        <Loader2 size={14} style={{ animation: "spin 1s linear infinite", color: "var(--text-muted)" }} />
                      ) : isExpanded ? (
                        <ChevronDown size={14} style={{ color: "var(--text-muted)" }} />
                      ) : (
                        <ChevronRight size={14} style={{ color: "var(--text-muted)" }} />
                      )}
                    </div>
                  </button>

                  {/* Expanded report */}
                  {isExpanded && expandedReport && (
                    <div style={{ padding: "0 16px 16px 42px", borderTop: "1px solid var(--border)" }}>
                      <div style={{ paddingTop: 12 }}>
                        <MarkdownRenderer content={expandedReport} />
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Spinner animation */}
      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  )
}
