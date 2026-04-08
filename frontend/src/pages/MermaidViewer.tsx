import { useEffect, useRef, useState, useCallback } from "react"
import { Workflow, Upload, FolderOpen, X, Download } from "lucide-react"
import { useThemeStore } from "../stores/theme"

// Mermaid is lazy-loaded from a CDN on first visit so it doesn't add ~2 MB to
// the main bundle. The script attaches a global `window.mermaid` once loaded;
// we cache the in-flight promise on window so concurrent renders share it.
declare global {
  interface Window {
    mermaid?: any
    __mermaidPromise?: Promise<any>
  }
}

const MERMAID_SRC = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"

function loadMermaid(theme: "default" | "dark"): Promise<any> {
  if (window.mermaid) {
    window.mermaid.initialize({
      startOnLoad: false,
      theme,
      securityLevel: "loose",
      flowchart: { useMaxWidth: true, htmlLabels: true },
    })
    return Promise.resolve(window.mermaid)
  }
  if (window.__mermaidPromise) return window.__mermaidPromise

  const promise = new Promise<any>((resolve, reject) => {
    const existing = document.querySelector(`script[src="${MERMAID_SRC}"]`)
    if (existing) {
      existing.addEventListener("load", () => resolve(window.mermaid))
      existing.addEventListener("error", () => reject(new Error("Mermaid CDN load failed")))
      return
    }
    const script = document.createElement("script")
    script.src = MERMAID_SRC
    script.async = true
    script.onload = () => {
      if (!window.mermaid) {
        reject(new Error("Mermaid script loaded but window.mermaid is undefined"))
        return
      }
      window.mermaid.initialize({
        startOnLoad: false,
        theme,
        securityLevel: "loose",
        flowchart: { useMaxWidth: true, htmlLabels: true },
      })
      resolve(window.mermaid)
    }
    script.onerror = () => reject(new Error("Failed to load Mermaid CDN"))
    document.head.appendChild(script)
  })
  window.__mermaidPromise = promise
  return promise
}

interface LoadedFile {
  name: string
  path: string
  content: string
}

function fmtChars(n: number): string {
  return n.toLocaleString() + " chars"
}

export function MermaidViewerPage() {
  const themeMode = useThemeStore((s) => s.mode)
  const mermaidTheme: "default" | "dark" = themeMode === "dark" ? "dark" : "default"

  const [files, setFiles] = useState<LoadedFile[]>([])
  const [activeIdx, setActiveIdx] = useState<number>(-1)
  const [dragOver, setDragOver] = useState(false)
  const [renderError, setRenderError] = useState<string | null>(null)
  const [mermaidReady, setMermaidReady] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [lastSvg, setLastSvg] = useState<string>("")
  const [showSource, setShowSource] = useState(false)

  const outputRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const dirInputRef = useRef<HTMLInputElement>(null)

  // Load Mermaid on mount and whenever theme changes
  useEffect(() => {
    let cancelled = false
    loadMermaid(mermaidTheme)
      .then(() => {
        if (cancelled) return
        setMermaidReady(true)
        setLoadError(null)
      })
      .catch((err) => {
        if (cancelled) return
        setLoadError(err?.message || String(err))
      })
    return () => {
      cancelled = true
    }
  }, [mermaidTheme])

  // Render the active diagram whenever it changes (or once mermaid is ready)
  const renderActive = useCallback(async () => {
    if (!mermaidReady || activeIdx < 0 || !files[activeIdx]) {
      setLastSvg("")
      if (outputRef.current) outputRef.current.innerHTML = ""
      return
    }
    setRenderError(null)
    try {
      const id = "mmd-" + Date.now() + "-" + Math.floor(Math.random() * 1e6)
      const result = await window.mermaid.render(id, files[activeIdx].content)
      const svg: string = result?.svg ?? result
      if (!svg || typeof svg !== "string") throw new Error("Mermaid returned no SVG")
      setLastSvg(svg)
      if (outputRef.current) outputRef.current.innerHTML = svg
    } catch (err: any) {
      const msg = err?.message || String(err)
      setRenderError(msg)
      setLastSvg("")
      if (outputRef.current) outputRef.current.innerHTML = ""
    }
  }, [mermaidReady, activeIdx, files])

  useEffect(() => {
    renderActive()
  }, [renderActive])

  // ── File intake ─────────────────────────────────
  const handleFileList = useCallback(async (list: FileList | null) => {
    if (!list) return
    const arr = Array.from(list).filter((f) => {
      const n = f.name.toLowerCase()
      return n.endsWith(".mmd") || n.endsWith(".mermaid")
    })
    if (arr.length === 0) {
      setRenderError("No .mmd or .mermaid files in the selection.")
      return
    }
    const newFiles: LoadedFile[] = []
    for (const f of arr) {
      try {
        const text = await f.text()
        const path = (f as any).webkitRelativePath || f.name
        newFiles.push({ name: f.name, path, content: text })
      } catch (err) {
        console.error("Failed to read", f.name, err)
      }
    }
    setFiles((prev) => {
      // Dedupe by path
      const merged = [...prev]
      for (const f of newFiles) {
        if (!merged.some((x) => x.path === f.path)) merged.push(f)
      }
      merged.sort((a, b) => a.path.localeCompare(b.path))
      // If nothing was selected before, select the first new file
      if (prev.length === 0 && merged.length > 0) {
        setActiveIdx(0)
      }
      return merged
    })
  }, [])

  const clearAll = () => {
    setFiles([])
    setActiveIdx(-1)
    setLastSvg("")
    setRenderError(null)
    if (outputRef.current) outputRef.current.innerHTML = ""
  }

  const downloadSvg = () => {
    if (!lastSvg || activeIdx < 0) return
    const blob = new Blob([lastSvg], { type: "image/svg+xml;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    const base = files[activeIdx].name.replace(/\.(mmd|mermaid)$/i, "")
    a.download = base + ".svg"
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  // ── Drag and drop handlers ───────────────────────
  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(true)
  }
  const onDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
  }
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
    handleFileList(e.dataTransfer.files)
  }

  // ── Styles (CSS vars for theme integration) ─────
  const cardStyle: React.CSSProperties = {
    background: "var(--bg-card)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius)",
    padding: 20,
    marginBottom: 16,
  }
  const dropStyle: React.CSSProperties = {
    border: `2px dashed ${dragOver ? "var(--accent)" : "var(--border)"}`,
    background: dragOver ? "var(--accent-subtle)" : "var(--bg-input)",
    borderRadius: "var(--radius)",
    padding: "32px 20px",
    textAlign: "center",
    transition: "background 0.15s, border-color 0.15s",
  }
  const buttonStyle: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    padding: "7px 14px",
    fontSize: 13,
    fontWeight: 500,
    fontFamily: "var(--font)",
    background: "var(--bg-card)",
    color: "var(--text-secondary)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius)",
    cursor: "pointer",
    transition: "border-color 0.15s, color 0.15s",
  }
  const primaryButtonStyle: React.CSSProperties = {
    ...buttonStyle,
    background: "var(--accent)",
    color: "#fff",
    border: "1px solid var(--accent)",
  }

  return (
    <div style={{ maxWidth: 1280, margin: "0 auto", padding: 24, fontFamily: "var(--font)" }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
        <Workflow size={24} style={{ color: "var(--accent)" }} />
        <h1 style={{ fontSize: 22, fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>
          Mermaid Diagram Viewer
        </h1>
      </div>
      <p style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 0, marginBottom: 20 }}>
        Drop a <code style={{ fontFamily: "var(--font-mono)" }}>.mmd</code> file (or pick a folder
        to scan recursively) to render it. Loads Mermaid from a CDN — no install, runs entirely in
        your browser. Tip: pick the <code style={{ fontFamily: "var(--font-mono)" }}>docs/research/</code>
        directory to see every research diagram in the tree at once.
      </p>

      {loadError && (
        <div
          style={{
            ...cardStyle,
            background: "var(--danger-subtle)",
            color: "var(--danger)",
            borderColor: "var(--danger)",
          }}
        >
          Mermaid failed to load from CDN: {loadError}
          <br />
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            Check your internet connection (only required on first visit; the script is cached after).
          </span>
        </div>
      )}

      {/* Drop zone + buttons */}
      <div style={cardStyle}>
        <div
          style={dropStyle}
          onDragEnter={onDragOver}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
        >
          <div style={{ fontSize: 15, fontWeight: 500, color: "var(--text-primary)", marginBottom: 4 }}>
            Drop .mmd files here
          </div>
          <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 14 }}>
            or pick them with a button below
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "center", flexWrap: "wrap" }}>
            <button type="button" style={buttonStyle} onClick={() => fileInputRef.current?.click()}>
              <Upload size={13} /> Choose file(s)
            </button>
            <button type="button" style={buttonStyle} onClick={() => dirInputRef.current?.click()}>
              <FolderOpen size={13} /> Choose folder
            </button>
            {files.length > 0 && (
              <button type="button" style={buttonStyle} onClick={clearAll}>
                <X size={13} /> Clear
              </button>
            )}
          </div>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept=".mmd,.mermaid,text/plain"
          multiple
          style={{ display: "none" }}
          onChange={(e) => {
            handleFileList(e.target.files)
            e.target.value = ""
          }}
        />
        <input
          ref={dirInputRef}
          type="file"
          // @ts-expect-error — webkitdirectory is non-standard but supported in Chrome/Edge/Firefox
          webkitdirectory=""
          directory=""
          multiple
          style={{ display: "none" }}
          onChange={(e) => {
            handleFileList(e.target.files)
            e.target.value = ""
          }}
        />

        {/* File list */}
        {files.length > 0 && (
          <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 4 }}>
            {files.map((f, i) => {
              const active = i === activeIdx
              return (
                <div
                  key={f.path}
                  onClick={() => setActiveIdx(i)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 12,
                    padding: "8px 12px",
                    background: active ? "var(--accent-subtle)" : "var(--bg-input)",
                    border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
                    borderRadius: "var(--radius)",
                    fontSize: 13,
                    cursor: "pointer",
                    transition: "border-color 0.15s, background 0.15s",
                  }}
                >
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>
                      {f.name}
                    </div>
                    <div
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        color: "var(--text-muted)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {f.path}
                    </div>
                  </div>
                  <span
                    style={{
                      fontSize: 11,
                      color: "var(--text-muted)",
                      whiteSpace: "nowrap",
                      flexShrink: 0,
                    }}
                  >
                    {fmtChars(f.content.length)}
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Output */}
      <div style={cardStyle}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 12,
            marginBottom: 14,
          }}
        >
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 13,
              color: "var(--text-primary)",
              wordBreak: "break-all",
              minWidth: 0,
              flex: 1,
            }}
          >
            {activeIdx >= 0 && files[activeIdx] ? files[activeIdx].path : "No diagram loaded"}
          </div>
          <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
            <button
              type="button"
              style={primaryButtonStyle}
              onClick={downloadSvg}
              disabled={!lastSvg}
            >
              <Download size={13} /> SVG
            </button>
          </div>
        </div>

        {!mermaidReady && !loadError && (
          <div style={{ color: "var(--text-muted)", padding: 60, textAlign: "center", fontSize: 14 }}>
            Loading Mermaid from CDN…
          </div>
        )}

        {mermaidReady && activeIdx < 0 && !renderError && (
          <div style={{ color: "var(--text-muted)", padding: 60, textAlign: "center", fontSize: 14 }}>
            Drop a .mmd file above to get started.
          </div>
        )}

        {renderError && (
          <div
            style={{
              color: "var(--danger)",
              background: "var(--danger-subtle)",
              border: "1px solid var(--danger)",
              borderRadius: "var(--radius)",
              padding: "12px 14px",
              fontFamily: "var(--font-mono)",
              fontSize: 12,
              whiteSpace: "pre-wrap",
            }}
          >
            Render failed:{"\n\n"}
            {renderError}
          </div>
        )}

        <div ref={outputRef} style={{ overflowX: "auto", textAlign: "center" }} />

        {activeIdx >= 0 && files[activeIdx] && (
          <div
            style={{
              marginTop: 16,
              padding: "10px 12px",
              background: "var(--bg-input)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              fontSize: 13,
            }}
          >
            <div
              onClick={() => setShowSource((v) => !v)}
              style={{ cursor: "pointer", color: "var(--text-muted)", userSelect: "none" }}
            >
              {showSource ? "▼" : "▶"} Show source
            </div>
            {showSource && (
              <pre
                style={{
                  margin: "10px 0 0",
                  background: "var(--bg-card)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius)",
                  padding: 12,
                  overflowX: "auto",
                  fontSize: 12,
                  fontFamily: "var(--font-mono)",
                  color: "var(--text-primary)",
                }}
              >
                {files[activeIdx].content}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
