import { useState, useEffect, useRef } from "react"
import { api } from "../lib/api"
import { Wand2, Copy, Check, ChevronDown, ChevronRight, Sparkles, RefreshCw, History as HistoryIcon } from "lucide-react"

interface Template {
  template_id: string
  name: string
  description: string
  category: string
  use_case: string
  target_audience: string
  desired_output: string
  tone: string
  constraints: string
}

interface Variant {
  variant_id: string
  session_id: string
  iteration: number
  variant_index: number
  approach: string
  prompt_text: string
  techniques: string[]
  feedback_applied: string
  generated_at: string
}

interface Session {
  session_id: string
  user_id: string
  created_at: string
  use_case: string
  target_audience: string
  desired_output: string
  tone: string
  constraints: string
  options: Record<string, any>
  provider: string
  template_id: string | null
  selected_variant_id: string | null
  variants?: Variant[]
}

const TONES = ["Formal", "Conversational", "Technical", "Friendly", "Professional neutral"]
const TARGET_MODELS = ["generic", "claude", "gpt", "gemini"]
const OUTPUT_FORMATS = ["freeform", "json", "markdown", "table", "xml"]
const LENGTHS = ["concise", "standard", "comprehensive"]
const CATEGORIES = ["general", "coding", "writing", "research", "analysis", "customer_service"]

export function PromptStudioPage() {
  const [activeTab, setActiveTab] = useState<"generator" | "history">("generator")
  const [provider, setProvider] = useState<string>(() => localStorage.getItem("llm_provider") || "anthropic")

  // Form state
  const [templates, setTemplates] = useState<Template[]>([])
  const [selectedTemplateId, setSelectedTemplateId] = useState<string>("")
  const [useCase, setUseCase] = useState("")
  const [targetAudience, setTargetAudience] = useState("")
  const [desiredOutput, setDesiredOutput] = useState("")
  const [tone, setTone] = useState("")
  const [constraints, setConstraints] = useState("")

  // Advanced options
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [optTargetModel, setOptTargetModel] = useState("generic")
  const [optOutputFormat, setOptOutputFormat] = useState("freeform")
  const [optFewShot, setOptFewShot] = useState(false)
  const [optCoT, setOptCoT] = useState(false)
  const [optLength, setOptLength] = useState("standard")
  const [optCategory, setOptCategory] = useState("general")

  // Generation state
  const [generating, setGenerating] = useState(false)
  const [generateError, setGenerateError] = useState("")
  const [currentSession, setCurrentSession] = useState<Session | null>(null)

  // Selection + refinement
  const [selectedVariantId, setSelectedVariantId] = useState<string>("")
  const [refineFeedback, setRefineFeedback] = useState("")
  const [refining, setRefining] = useState(false)
  const [copiedVariantId, setCopiedVariantId] = useState<string>("")
  const [expandedTechniques, setExpandedTechniques] = useState<Set<string>>(new Set())

  // History state
  const [historySessions, setHistorySessions] = useState<Session[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)

  const variantsEndRef = useRef<HTMLDivElement>(null)

  // Load templates once
  useEffect(() => {
    api.get("/prompts/templates")
      .then((res) => setTemplates(res.data || []))
      .catch(() => {})
  }, [])

  // Load history when tab opens
  useEffect(() => {
    if (activeTab === "history") {
      setHistoryLoading(true)
      api.get("/prompts?per_page=30")
        .then((res) => setHistorySessions(res.data || []))
        .catch(() => {})
        .finally(() => setHistoryLoading(false))
    }
  }, [activeTab])

  const applyTemplate = (templateId: string) => {
    setSelectedTemplateId(templateId)
    if (!templateId) return
    const t = templates.find((x) => x.template_id === templateId)
    if (!t) return
    setUseCase(t.use_case || "")
    setTargetAudience(t.target_audience || "")
    setDesiredOutput(t.desired_output || "")
    setTone(t.tone || "")
    setConstraints(t.constraints || "")
  }

  const handleGenerate = async () => {
    if (!useCase.trim()) {
      setGenerateError("Use case is required")
      return
    }
    setGenerateError("")
    setGenerating(true)
    setCurrentSession(null)
    setSelectedVariantId("")
    try {
      const res = await api.post("/prompts/generate", {
        use_case: useCase,
        target_audience: targetAudience,
        desired_output: desiredOutput,
        tone,
        constraints,
        options: {
          target_model: optTargetModel,
          output_format: optOutputFormat,
          few_shot: optFewShot,
          cot: optCoT,
          length: optLength,
          category: optCategory,
        },
        provider,
        template_id: selectedTemplateId || null,
      })
      setCurrentSession(res.data)
      setTimeout(() => variantsEndRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 100)
    } catch (e: any) {
      setGenerateError(e?.message || "Generation failed")
    } finally {
      setGenerating(false)
    }
  }

  const handleSelect = async (variant: Variant) => {
    if (!currentSession) return
    try {
      await api.put(`/prompts/${currentSession.session_id}/select`, { variant_id: variant.variant_id })
      setSelectedVariantId(variant.variant_id)
    } catch (e: any) {
      setGenerateError(e?.message || "Selection failed")
    }
  }

  const handleRefine = async () => {
    if (!currentSession || !selectedVariantId || !refineFeedback.trim()) return
    setRefining(true)
    setGenerateError("")
    try {
      const res = await api.post(`/prompts/${currentSession.session_id}/refine`, {
        feedback: refineFeedback,
        provider,
      })
      setCurrentSession(res.data)
      setRefineFeedback("")
      setSelectedVariantId("")
      setTimeout(() => variantsEndRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 100)
    } catch (e: any) {
      setGenerateError(e?.message || "Refinement failed")
    } finally {
      setRefining(false)
    }
  }

  const handleCopy = (variant: Variant) => {
    navigator.clipboard.writeText(variant.prompt_text).then(() => {
      setCopiedVariantId(variant.variant_id)
      setTimeout(() => setCopiedVariantId(""), 1500)
    })
  }

  const toggleTechniques = (variantId: string) => {
    setExpandedTechniques((prev) => {
      const next = new Set(prev)
      if (next.has(variantId)) next.delete(variantId)
      else next.add(variantId)
      return next
    })
  }

  const loadHistorySession = async (sessionId: string) => {
    try {
      const res = await api.get(`/prompts/${sessionId}`)
      setCurrentSession(res.data)
      setUseCase(res.data.use_case)
      setTargetAudience(res.data.target_audience)
      setDesiredOutput(res.data.desired_output)
      setTone(res.data.tone)
      setConstraints(res.data.constraints)
      setSelectedVariantId(res.data.selected_variant_id || "")
      setActiveTab("generator")
      setTimeout(() => variantsEndRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 100)
    } catch {}
  }

  const inputStyle: React.CSSProperties = {
    width: "100%",
    background: "var(--bg-input)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius)",
    color: "var(--text-primary)",
    fontFamily: "var(--font)",
    padding: "8px 12px",
    fontSize: 13,
    outline: "none",
    boxSizing: "border-box",
  }

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: 12,
    fontWeight: 600,
    color: "var(--text-secondary)",
    marginBottom: 4,
  }

  // Group variants by iteration for display
  const variantsByIteration: Map<number, Variant[]> = new Map()
  if (currentSession?.variants) {
    for (const v of currentSession.variants) {
      if (!variantsByIteration.has(v.iteration)) variantsByIteration.set(v.iteration, [])
      variantsByIteration.get(v.iteration)!.push(v)
    }
    // Sort by variant_index within each iteration
    for (const arr of variantsByIteration.values()) {
      arr.sort((a, b) => a.variant_index - b.variant_index)
    }
  }
  const sortedIterations = Array.from(variantsByIteration.keys()).sort((a, b) => a - b)

  return (
    <div style={{ maxWidth: 1400, margin: "0 auto", padding: 24, fontFamily: "var(--font)" }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Wand2 size={24} style={{ color: "var(--accent)" }} />
          <h1 style={{ fontSize: 22, fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>Prompt Studio</h1>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            Transform requirements into production-grade AI prompts
          </span>
        </div>
        {/* Provider toggle */}
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Model:</span>
          <div style={{ display: "flex", borderRadius: "var(--radius)", overflow: "hidden", border: "1px solid var(--border)" }}>
            {[
              { id: "anthropic", label: "Claude" },
              { id: "bedrock", label: "Bedrock" },
            ].map((opt) => (
              <button
                key={opt.id}
                type="button"
                onClick={() => { setProvider(opt.id); localStorage.setItem("llm_provider", opt.id) }}
                style={{
                  padding: "5px 12px", fontSize: 12, fontWeight: 500,
                  border: "none", cursor: "pointer", fontFamily: "var(--font)",
                  background: provider === opt.id ? "var(--accent)" : "var(--bg-input)",
                  color: provider === opt.id ? "#fff" : "var(--text-secondary)",
                  borderRight: opt.id === "anthropic" ? "1px solid var(--border)" : "none",
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Tab bar */}
      <div style={{ display: "flex", gap: 4, marginBottom: 16, borderBottom: "1px solid var(--border)" }}>
        {[
          { key: "generator", label: "Generator", icon: Sparkles },
          { key: "history", label: "History", icon: HistoryIcon },
        ].map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setActiveTab(key as any)}
            style={{
              padding: "10px 16px", fontSize: 13, fontWeight: 500,
              background: "transparent", border: "none", cursor: "pointer",
              color: activeTab === key ? "var(--accent)" : "var(--text-secondary)",
              borderBottom: activeTab === key ? "2px solid var(--accent)" : "2px solid transparent",
              display: "flex", alignItems: "center", gap: 6, fontFamily: "var(--font)",
            }}
          >
            <Icon size={14} /> {label}
          </button>
        ))}
      </div>

      {/* ── Generator Tab ─────────────────────────────── */}
      {activeTab === "generator" && (
        <>
          <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 20, marginBottom: 16 }}>
            {/* Template picker */}
            <div style={{ marginBottom: 16 }}>
              <label style={labelStyle}>Start from a template (optional)</label>
              <select
                value={selectedTemplateId}
                onChange={(e) => applyTemplate(e.target.value)}
                style={inputStyle}
              >
                <option value="">— Start from scratch —</option>
                {templates.map((t) => (
                  <option key={t.template_id} value={t.template_id}>
                    {t.name} — {t.description}
                  </option>
                ))}
              </select>
            </div>

            {/* Structured inputs */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
              <div style={{ gridColumn: "1 / -1" }}>
                <label style={labelStyle}>Use case <span style={{ color: "var(--danger)" }}>*</span></label>
                <textarea
                  value={useCase}
                  onChange={(e) => setUseCase(e.target.value)}
                  placeholder="Describe what you want the AI to do..."
                  rows={4}
                  style={{ ...inputStyle, resize: "vertical", fontFamily: "var(--font)" }}
                />
              </div>
              <div>
                <label style={labelStyle}>Target audience</label>
                <input
                  type="text"
                  value={targetAudience}
                  onChange={(e) => setTargetAudience(e.target.value)}
                  placeholder="e.g. Senior Python developer"
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Tone</label>
                <select value={tone} onChange={(e) => setTone(e.target.value)} style={inputStyle}>
                  <option value="">— Default —</option>
                  {TONES.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div style={{ gridColumn: "1 / -1" }}>
                <label style={labelStyle}>Desired output</label>
                <textarea
                  value={desiredOutput}
                  onChange={(e) => setDesiredOutput(e.target.value)}
                  placeholder="Describe the structure, format, or content of the expected output..."
                  rows={2}
                  style={{ ...inputStyle, resize: "vertical", fontFamily: "var(--font)" }}
                />
              </div>
              <div style={{ gridColumn: "1 / -1" }}>
                <label style={labelStyle}>Constraints</label>
                <textarea
                  value={constraints}
                  onChange={(e) => setConstraints(e.target.value)}
                  placeholder="Length limits, forbidden content, style requirements..."
                  rows={2}
                  style={{ ...inputStyle, resize: "vertical", fontFamily: "var(--font)" }}
                />
              </div>
            </div>

            {/* Advanced options */}
            <div style={{ marginBottom: 16 }}>
              <button
                type="button"
                onClick={() => setAdvancedOpen(!advancedOpen)}
                style={{
                  display: "flex", alignItems: "center", gap: 6,
                  fontSize: 12, fontWeight: 600, color: "var(--text-secondary)",
                  background: "transparent", border: "none", cursor: "pointer",
                  padding: "4px 0", fontFamily: "var(--font)",
                }}
              >
                {advancedOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                Advanced options
              </button>
              {advancedOpen && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12, marginTop: 10, padding: 12, background: "var(--bg-input)", borderRadius: "var(--radius)" }}>
                  <div>
                    <label style={{ ...labelStyle, fontSize: 11 }}>Target LLM</label>
                    <select value={optTargetModel} onChange={(e) => setOptTargetModel(e.target.value)} style={{ ...inputStyle, padding: "6px 10px", fontSize: 12 }}>
                      {TARGET_MODELS.map((m) => <option key={m} value={m}>{m}</option>)}
                    </select>
                  </div>
                  <div>
                    <label style={{ ...labelStyle, fontSize: 11 }}>Output format</label>
                    <select value={optOutputFormat} onChange={(e) => setOptOutputFormat(e.target.value)} style={{ ...inputStyle, padding: "6px 10px", fontSize: 12 }}>
                      {OUTPUT_FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}
                    </select>
                  </div>
                  <div>
                    <label style={{ ...labelStyle, fontSize: 11 }}>Length</label>
                    <select value={optLength} onChange={(e) => setOptLength(e.target.value)} style={{ ...inputStyle, padding: "6px 10px", fontSize: 12 }}>
                      {LENGTHS.map((l) => <option key={l} value={l}>{l}</option>)}
                    </select>
                  </div>
                  <div>
                    <label style={{ ...labelStyle, fontSize: 11 }}>Category</label>
                    <select value={optCategory} onChange={(e) => setOptCategory(e.target.value)} style={{ ...inputStyle, padding: "6px 10px", fontSize: 12 }}>
                      {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 18 }}>
                    <input type="checkbox" id="fewshot" checked={optFewShot} onChange={(e) => setOptFewShot(e.target.checked)} />
                    <label htmlFor="fewshot" style={{ fontSize: 12, color: "var(--text-secondary)", cursor: "pointer" }}>
                      Include few-shot examples
                    </label>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 18 }}>
                    <input type="checkbox" id="cot" checked={optCoT} onChange={(e) => setOptCoT(e.target.checked)} />
                    <label htmlFor="cot" style={{ fontSize: 12, color: "var(--text-secondary)", cursor: "pointer" }}>
                      Include chain-of-thought
                    </label>
                  </div>
                </div>
              )}
            </div>

            {/* Error message */}
            {generateError && (
              <div style={{
                padding: "8px 12px", marginBottom: 12,
                background: "var(--danger-subtle)", color: "var(--danger)",
                fontSize: 12, borderRadius: "var(--radius)",
              }}>
                {generateError}
              </div>
            )}

            {/* Generate button */}
            <button
              type="button"
              onClick={handleGenerate}
              disabled={generating || !useCase.trim()}
              style={{
                width: "100%", padding: "12px 20px", fontSize: 14, fontWeight: 600,
                background: generating || !useCase.trim() ? "var(--bg-hover)" : "var(--accent)",
                color: generating || !useCase.trim() ? "var(--text-muted)" : "#fff",
                border: "none", borderRadius: "var(--radius)",
                cursor: generating || !useCase.trim() ? "not-allowed" : "pointer",
                display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
                fontFamily: "var(--font)",
              }}
            >
              {generating ? (
                <>
                  <RefreshCw size={16} style={{ animation: "ps-spin 1s linear infinite" }} />
                  Generating 3 variants...
                </>
              ) : (
                <>
                  <Sparkles size={16} />
                  Generate 3 Variants
                </>
              )}
            </button>
          </div>

          {/* Variants display */}
          <div ref={variantsEndRef} />
          {currentSession && sortedIterations.map((iteration) => {
            const iterVariants = variantsByIteration.get(iteration) || []
            const isRefinement = iteration > 0
            return (
              <div key={iteration} style={{ marginBottom: 24 }}>
                {isRefinement && (
                  <div style={{
                    padding: "8px 14px", marginBottom: 12,
                    background: "var(--accent-subtle)", borderRadius: "var(--radius)",
                    fontSize: 12, color: "var(--accent)", fontWeight: 500,
                  }}>
                    🔄 Refinement iteration #{iteration}: {iterVariants[0]?.feedback_applied}
                  </div>
                )}
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
                  {iterVariants.map((variant) => {
                    const isSelected = selectedVariantId === variant.variant_id
                    const isCopied = copiedVariantId === variant.variant_id
                    const techniquesOpen = expandedTechniques.has(variant.variant_id)
                    return (
                      <div
                        key={variant.variant_id}
                        style={{
                          background: "var(--bg-card)",
                          border: `2px solid ${isSelected ? "var(--accent)" : "var(--border)"}`,
                          borderRadius: "var(--radius)",
                          padding: 16,
                          display: "flex", flexDirection: "column",
                        }}
                      >
                        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                          <div>
                            <div style={{ fontSize: 10, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: 0.5 }}>
                              Variant {variant.variant_index}
                            </div>
                            <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
                              {variant.approach}
                            </div>
                          </div>
                          {isSelected && (
                            <span style={{
                              fontSize: 10, padding: "2px 8px", borderRadius: "var(--radius)",
                              background: "var(--accent)", color: "#fff", fontWeight: 600,
                            }}>
                              SELECTED
                            </span>
                          )}
                        </div>
                        <div style={{
                          background: "var(--bg-input)", border: "1px solid var(--border)",
                          borderRadius: "var(--radius)", padding: 10, fontSize: 11,
                          fontFamily: "ui-monospace, monospace", color: "var(--text-primary)",
                          whiteSpace: "pre-wrap", maxHeight: 320, overflowY: "auto",
                          marginBottom: 10, lineHeight: 1.45,
                        }}>
                          {variant.prompt_text}
                        </div>
                        <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 8 }}>
                          {variant.prompt_text.length} chars
                        </div>
                        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
                          <button
                            onClick={() => handleCopy(variant)}
                            style={{
                              flex: 1, padding: "6px 10px", fontSize: 11, fontWeight: 500,
                              background: isCopied ? "var(--success-subtle)" : "var(--bg-hover)",
                              color: isCopied ? "var(--success)" : "var(--text-secondary)",
                              border: "1px solid var(--border)", borderRadius: "var(--radius)",
                              cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: 4,
                              fontFamily: "var(--font)",
                            }}
                          >
                            {isCopied ? <><Check size={12} /> Copied</> : <><Copy size={12} /> Copy</>}
                          </button>
                          <button
                            onClick={() => handleSelect(variant)}
                            disabled={isSelected}
                            style={{
                              flex: 1, padding: "6px 10px", fontSize: 11, fontWeight: 500,
                              background: isSelected ? "var(--accent-subtle)" : "var(--accent)",
                              color: isSelected ? "var(--accent)" : "#fff",
                              border: "none", borderRadius: "var(--radius)",
                              cursor: isSelected ? "default" : "pointer",
                              fontFamily: "var(--font)",
                            }}
                          >
                            {isSelected ? "Selected" : "Select"}
                          </button>
                        </div>
                        <button
                          onClick={() => toggleTechniques(variant.variant_id)}
                          style={{
                            display: "flex", alignItems: "center", gap: 4,
                            fontSize: 11, color: "var(--text-muted)",
                            background: "transparent", border: "none", cursor: "pointer",
                            padding: "4px 0", fontFamily: "var(--font)",
                          }}
                        >
                          {techniquesOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                          Techniques applied ({variant.techniques.length})
                        </button>
                        {techniquesOpen && (
                          <ul style={{ listStyle: "none", margin: "4px 0 0 16px", padding: 0 }}>
                            {variant.techniques.map((t, i) => (
                              <li key={i} style={{ fontSize: 11, color: "var(--text-secondary)", padding: "2px 0" }}>
                                ✓ {t}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}

          {/* Refinement panel — shown after selection */}
          {currentSession && selectedVariantId && (
            <div style={{
              background: "var(--bg-card)", border: "1px solid var(--border)",
              borderRadius: "var(--radius)", padding: 16, marginTop: 16,
            }}>
              <label style={labelStyle}>Refine selected variant</label>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  type="text"
                  value={refineFeedback}
                  onChange={(e) => setRefineFeedback(e.target.value)}
                  placeholder="e.g. make it more concise, add JSON schema output, target beginners..."
                  style={{ ...inputStyle, flex: 1 }}
                  onKeyDown={(e) => e.key === "Enter" && handleRefine()}
                />
                <button
                  onClick={handleRefine}
                  disabled={refining || !refineFeedback.trim()}
                  style={{
                    padding: "8px 16px", fontSize: 13, fontWeight: 500,
                    background: refining || !refineFeedback.trim() ? "var(--bg-hover)" : "var(--accent)",
                    color: refining || !refineFeedback.trim() ? "var(--text-muted)" : "#fff",
                    border: "none", borderRadius: "var(--radius)",
                    cursor: refining || !refineFeedback.trim() ? "not-allowed" : "pointer",
                    display: "flex", alignItems: "center", gap: 6, fontFamily: "var(--font)",
                  }}
                >
                  {refining ? <RefreshCw size={14} style={{ animation: "ps-spin 1s linear infinite" }} /> : <RefreshCw size={14} />}
                  Refine
                </button>
              </div>
            </div>
          )}
        </>
      )}

      {/* ── History Tab ───────────────────────────────── */}
      {activeTab === "history" && (
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 20 }}>
          <h2 style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)", margin: "0 0 16px 0" }}>
            Previous Sessions
          </h2>
          {historyLoading && <div style={{ fontSize: 13, color: "var(--text-muted)" }}>Loading...</div>}
          {!historyLoading && historySessions.length === 0 && (
            <div style={{ fontSize: 13, color: "var(--text-muted)", padding: 20, textAlign: "center" }}>
              No previous sessions. Generate your first prompt in the Generator tab.
            </div>
          )}
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {historySessions.map((s) => (
              <div
                key={s.session_id}
                onClick={() => loadHistorySession(s.session_id)}
                style={{
                  padding: 12, borderRadius: "var(--radius)",
                  background: "var(--bg-input)", border: "1px solid var(--border)",
                  cursor: "pointer", transition: "all 0.15s",
                }}
                onMouseEnter={(e) => e.currentTarget.style.borderColor = "var(--accent)"}
                onMouseLeave={(e) => e.currentTarget.style.borderColor = "var(--border)"}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12, fontFamily: "ui-monospace, monospace", color: "var(--text-muted)", marginBottom: 4 }}>
                      {s.session_id}
                    </div>
                    <div style={{
                      fontSize: 13, color: "var(--text-primary)", fontWeight: 500,
                      whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                    }}>
                      {s.use_case.split("\n")[0].slice(0, 120)}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                      {new Date(s.created_at).toLocaleString()}
                      {s.template_id && ` · template: ${s.template_id}`}
                      {" · "}
                      {s.provider}
                    </div>
                  </div>
                  <ChevronRight size={16} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <style>{`
        @keyframes ps-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
    </div>
  )
}
