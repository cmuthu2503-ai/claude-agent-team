interface MarkdownRendererProps {
  content: string
}

export function MarkdownRenderer({ content }: MarkdownRendererProps) {
  if (!content || !content.trim()) return null
  const html = markdownToHtml(content)
  return (
    <div
      className="md-content"
      dangerouslySetInnerHTML={{ __html: html }}
      style={{ fontFamily: "var(--font)", fontSize: 13, lineHeight: 1.7, color: "var(--text-secondary)" }}
    />
  )
}

function markdownToHtml(md: string): string {
  let html = md

  // Escape HTML entities
  html = html.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")

  // Code blocks (``` ... ```) — must be before other transformations
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_match, lang, code) => {
    return `<pre style="background:var(--bg-primary);border:1px solid var(--border);border-radius:var(--radius);padding:12px 16px;margin:8px 0;overflow-x:auto;font-size:12px;line-height:1.5"><code style="font-family:var(--font-mono);color:var(--text-primary)">${code.trim()}</code></pre>`
  })

  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code style="background:var(--bg-hover);padding:1px 5px;border-radius:3px;font-size:12px;font-family:var(--font-mono);color:var(--accent)">$1</code>')

  // Headers
  html = html.replace(/^#### (.+)$/gm, '<h4 style="font-size:14px;font-weight:600;color:var(--text-primary);margin:12px 0 4px">$1</h4>')
  html = html.replace(/^### (.+)$/gm, '<h3 style="font-size:15px;font-weight:600;color:var(--text-primary);margin:14px 0 6px">$1</h3>')
  html = html.replace(/^## (.+)$/gm, '<h2 style="font-size:17px;font-weight:600;color:var(--text-primary);margin:18px 0 8px">$1</h2>')
  html = html.replace(/^# (.+)$/gm, '<h1 style="font-size:20px;font-weight:700;color:var(--text-primary);margin:20px 0 10px;border-bottom:1px solid var(--border);padding-bottom:8px">$1</h1>')

  // Bold and italic
  html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em style="color:var(--text-primary)">$1</em></strong>')
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong style="color:var(--text-primary);font-weight:600">$1</strong>')
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>')

  // Horizontal rules
  html = html.replace(/^---+$/gm, '<hr style="border:none;border-top:1px solid var(--border);margin:16px 0">')

  // Tables
  html = html.replace(/^\|(.+)\|$/gm, (line) => {
    const cells = line.split("|").filter((c) => c.trim() !== "")
    const isHeader = cells.every((c) => /^[\s-:]+$/.test(c))
    if (isHeader) return "<!--table-sep-->"
    return cells.map((c) => `<td style="padding:6px 12px;border-bottom:1px solid var(--border);color:var(--text-secondary)">${c.trim()}</td>`).join("")
  })

  // Wrap table rows
  const lines = html.split("\n")
  let inTable = false
  let tableHtml = ""
  const processed: string[] = []

  for (const line of lines) {
    if (line.includes("<td")) {
      if (!inTable) {
        inTable = true
        tableHtml = '<div style="overflow-x:auto;margin:10px 0"><table style="width:100%;border-collapse:collapse;font-size:12px;border:1px solid var(--border)">'
        // First row is header
        tableHtml += `<thead style="background:var(--bg-hover)"><tr>${line.replace(/<td/g, '<th').replace(/<\/td>/g, '</th>').replace(/style="[^"]*"/g, 'style="padding:8px 12px;text-align:left;font-weight:600;color:var(--text-primary);border-bottom:1px solid var(--border);font-size:11px;text-transform:uppercase;letter-spacing:0.03em"')}</tr></thead><tbody>`
        continue
      }
      tableHtml += `<tr>${line}</tr>`
    } else if (line === "<!--table-sep-->") {
      continue // Skip separator row
    } else {
      if (inTable) {
        tableHtml += "</tbody></table></div>"
        processed.push(tableHtml)
        inTable = false
        tableHtml = ""
      }
      processed.push(line)
    }
  }
  if (inTable) {
    tableHtml += "</tbody></table></div>"
    processed.push(tableHtml)
  }
  html = processed.join("\n")

  // Checkboxes
  html = html.replace(/- \[x\]/gi, '<span style="margin-right:6px">☑</span>')
  html = html.replace(/- \[ \]/g, '<span style="margin-right:6px">☐</span>')

  // Unordered lists
  html = html.replace(/^(\s*)[-*] (.+)$/gm, (_match, indent, text) => {
    const level = Math.floor((indent?.length || 0) / 2)
    const ml = 16 + level * 16
    return `<div style="margin:3px 0;padding-left:${ml}px;position:relative"><span style="position:absolute;left:${ml - 12}px;color:var(--text-muted)">•</span>${text}</div>`
  })

  // Ordered lists
  html = html.replace(/^(\s*)(\d+)\. (.+)$/gm, (_match, indent, num, text) => {
    const ml = 16
    return `<div style="margin:3px 0;padding-left:${ml}px"><span style="color:var(--text-muted);margin-right:6px;font-weight:500">${num}.</span>${text}</div>`
  })

  // Blockquotes
  html = html.replace(/^&gt; (.+)$/gm, '<blockquote style="border-left:3px solid var(--accent);padding-left:12px;margin:10px 0;color:var(--text-muted)">$1</blockquote>')

  // Paragraphs — wrap loose lines
  html = html.replace(/^(?!<[hpdbut\-\/!]|<!--)(.+)$/gm, (match) => {
    if (match.trim() === "") return ""
    return `<p style="margin:6px 0">${match}</p>`
  })

  // Clean up empty paragraphs
  html = html.replace(/<p style="margin:6px 0"><\/p>/g, "")

  return html
}
