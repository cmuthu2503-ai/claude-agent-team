import { useRef, useCallback, forwardRef, useImperativeHandle } from "react"
import { Paperclip } from "lucide-react"

export interface RichTextInputHandle {
  getContent: () => { text: string; files: File[] }
  clear: () => void
}

interface RichTextInputProps {
  placeholder?: string
  onFilesChange?: (count: number) => void
  onTextChange?: (text: string) => void
}

export const RichTextInput = forwardRef<RichTextInputHandle, RichTextInputProps>(
  ({ placeholder = "Describe what you want to build...", onFilesChange, onTextChange }, ref) => {
    const editorRef = useRef<HTMLDivElement>(null)
    const fileInputRef = useRef<HTMLInputElement>(null)
    const filesRef = useRef<Map<string, File>>(new Map())

    const insertImageAtCursor = useCallback((file: File) => {
      const id = Math.random().toString(36).slice(2, 10)
      filesRef.current.set(id, file)
      onFilesChange?.(filesRef.current.size)

      const reader = new FileReader()
      reader.onload = (e) => {
        const editor = editorRef.current
        if (!editor) return

        // Focus the editor
        editor.focus()

        // Create image element
        const img = document.createElement("img")
        img.src = e.target?.result as string
        img.dataset.fileId = id
        img.style.cssText =
          "max-width:100%;max-height:200px;border-radius:6px;margin:8px 0;display:block;border:1px solid var(--border);cursor:pointer;"
        img.title = `${file.name} — click to remove`
        img.onclick = () => {
          filesRef.current.delete(id)
          img.remove()
          onFilesChange?.(filesRef.current.size)
        }

        // Create a wrapper with filename label
        const wrapper = document.createElement("div")
        wrapper.dataset.fileId = id
        wrapper.style.cssText = "position:relative;display:inline-block;margin:4px 0;"
        wrapper.contentEditable = "false"

        const label = document.createElement("div")
        label.style.cssText =
          "font-size:10px;color:var(--text-muted);margin-top:2px;font-family:var(--font-mono);"
        label.textContent = `📎 ${file.name} (${(file.size / 1024).toFixed(0)} KB)`

        wrapper.appendChild(img)
        wrapper.appendChild(label)

        // Insert at cursor position
        const sel = window.getSelection()
        if (sel && sel.rangeCount > 0) {
          const range = sel.getRangeAt(0)
          // Only insert within our editor
          if (editor.contains(range.startContainer)) {
            range.deleteContents()
            range.insertNode(document.createElement("br"))
            range.insertNode(wrapper)
            range.insertNode(document.createElement("br"))
            range.collapse(false)
          } else {
            editor.appendChild(document.createElement("br"))
            editor.appendChild(wrapper)
            editor.appendChild(document.createElement("br"))
          }
        } else {
          editor.appendChild(document.createElement("br"))
          editor.appendChild(wrapper)
          editor.appendChild(document.createElement("br"))
        }
      }
      reader.readAsDataURL(file)
    }, [onFilesChange])

    const handlePaste = useCallback(
      (e: React.ClipboardEvent) => {
        const items = e.clipboardData.items
        let hasImage = false
        for (const item of Array.from(items)) {
          if (item.type.startsWith("image/")) {
            e.preventDefault()
            const file = item.getAsFile()
            if (file) {
              insertImageAtCursor(file)
              hasImage = true
            }
          }
        }
        // For plain text paste, let the default behavior handle it
        if (!hasImage) {
          // Strip HTML formatting from pasted text
          const text = e.clipboardData.getData("text/plain")
          if (text) {
            e.preventDefault()
            document.execCommand("insertText", false, text)
          }
        }
      },
      [insertImageAtCursor],
    )

    const handleDrop = useCallback(
      (e: React.DragEvent) => {
        e.preventDefault()
        const files = e.dataTransfer.files
        for (const file of Array.from(files)) {
          if (file.type.startsWith("image/") || file.type === "application/pdf") {
            insertImageAtCursor(file)
          }
        }
      },
      [insertImageAtCursor],
    )

    const handleFileSelect = useCallback(() => {
      fileInputRef.current?.click()
    }, [])

    const handleFileInputChange = useCallback(
      (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files) {
          for (const file of Array.from(e.target.files)) {
            insertImageAtCursor(file)
          }
          e.target.value = ""
        }
      },
      [insertImageAtCursor],
    )

    useImperativeHandle(ref, () => ({
      getContent: () => {
        const editor = editorRef.current
        if (!editor) return { text: "", files: [] }

        // Extract text content (excluding image elements)
        const clone = editor.cloneNode(true) as HTMLElement
        // Remove all image wrappers and replace with placeholder text
        clone.querySelectorAll("[data-file-id]").forEach((el) => {
          const fileId = el.getAttribute("data-file-id") || el.querySelector("img")?.dataset.fileId
          if (fileId) {
            const file = filesRef.current.get(fileId)
            if (file) {
              el.replaceWith(`[screenshot: ${file.name}]`)
            } else {
              el.remove()
            }
          }
        })

        const text = clone.innerText.trim()
        const files = Array.from(filesRef.current.values())
        return { text, files }
      },
      clear: () => {
        if (editorRef.current) {
          editorRef.current.innerHTML = ""
        }
        filesRef.current.clear()
        onFilesChange?.(0)
      },
    }))

    return (
      <div style={{ position: "relative" }}>
        <div
          ref={editorRef}
          contentEditable
          onPaste={handlePaste}
          onDrop={handleDrop}
          onDragOver={(e) => e.preventDefault()}
          onInput={() => {
            if (onTextChange && editorRef.current) {
              onTextChange(editorRef.current.innerText.trim())
            }
          }}
          data-placeholder={placeholder}
          style={{
            width: "100%",
            minHeight: 100,
            maxHeight: 400,
            overflowY: "auto",
            background: "var(--bg-input)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            color: "var(--text-primary)",
            fontFamily: "var(--font)",
            padding: "12px 16px",
            paddingBottom: 36,
            fontSize: 14,
            lineHeight: 1.6,
            outline: "none",
            boxSizing: "border-box",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        />
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*,.pdf"
          multiple
          onChange={handleFileInputChange}
          style={{ display: "none" }}
        />
        <div
          style={{
            position: "absolute",
            bottom: 8,
            left: 12,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <button
            type="button"
            onClick={handleFileSelect}
            title="Attach screenshot (or paste / drag into text)"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 4,
              padding: "3px 8px",
              borderRadius: "var(--radius)",
              background: "var(--bg-hover)",
              color: "var(--text-muted)",
              border: "1px solid var(--border)",
              cursor: "pointer",
              fontSize: 11,
            }}
          >
            <Paperclip size={12} />
            Attach image
          </button>
          <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
            Paste, drag, or click to attach screenshots
          </span>
        </div>

        <style>{`
          [contenteditable][data-placeholder]:empty::before {
            content: attr(data-placeholder);
            color: var(--text-muted);
            pointer-events: none;
          }
        `}</style>
      </div>
    )
  },
)
