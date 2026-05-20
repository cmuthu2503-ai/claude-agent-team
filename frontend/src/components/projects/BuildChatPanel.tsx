/**
 * BuildChatPanel — Project-driven Build, Phase D (PDB-39 / PDB-40 / PDB-42).
 *
 * Chat with the `project_orchestrator` agent. Renders chat history with
 * inline tool-call chips between turns. Sends to POST /build/chat;
 * subscribes to the existing /ws/activity stream for live updates so
 * messages from another tab on the same project show up here too.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import { Send, MessageSquare } from "lucide-react"
import { api } from "../../lib/api"

interface ToolCallSummary {
  tool: string
  input: Record<string, any>
  result_summary: string
}

interface Message {
  message_id: string
  role: "user" | "assistant" | "tool"
  content: string
  tool_calls: ToolCallSummary[] | null
  created_at: string
  created_by: string | null
}

interface Props {
  projectId: string
}

// Build a dynamic starter-prompts list. The first prompt's wording
// depends on whether the project has ever dispatched any task — first
// time vs. continuing. These are info-seeking questions; the user
// decides whether to dispatch after seeing the orchestrator's reply.
function starterPrompts(hasDispatched: boolean): string[] {
  return [
    hasDispatched
      ? "What's the next set of tasks?"
      : "What's the first set of independent tasks?",
    "What's the High Priority tasks?",
    "What's the Status of Build?",
  ]
}

export function BuildChatPanel({ projectId }: Props) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState("")
  const [sending, setSending] = useState(false)
  const [error, setError] = useState("")
  // True when at least one task on the current finalized list has been
  // dispatched (task_status != 'backlog'). Drives the first starter
  // prompt's wording — see starterPrompts().
  const [hasDispatched, setHasDispatched] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)
  const wsRef = useRef<WebSocket | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await api.get(`/projects/${projectId}/build/messages`)
      setMessages((res.data || []) as Message[])
    } catch (e: any) {
      setError(parseDetail(e?.message) || "Failed to load chat history")
    }
  }, [projectId])

  // Fetch task list state so the starter-prompt wording reflects
  // whether anything's been dispatched yet. Re-runs whenever the
  // message count changes — sending a chat message can trigger a
  // dispatch behind the scenes, so we want the prompt to flip from
  // "first set" → "next set" after that.
  const loadDispatchState = useCallback(async () => {
    try {
      const res = await api.get(`/projects/${projectId}/tasks`)
      const tasks: { task_status?: string }[] = res.data || []
      const dispatched = tasks.some(
        (t) => t.task_status && t.task_status !== "backlog",
      )
      setHasDispatched(dispatched)
    } catch {
      // Soft-fail — prompts still render with the default "first set"
      // wording if the tasks endpoint isn't ready yet.
    }
  }, [projectId])

  useEffect(() => {
    void load()
    void loadDispatchState()
  }, [load, loadDispatchState])

  // Re-check dispatch state shortly after each new message — the
  // orchestrator may have dispatched something in response.
  useEffect(() => {
    if (messages.length === 0) return
    const t = window.setTimeout(() => { void loadDispatchState() }, 800)
    return () => window.clearTimeout(t)
  }, [messages.length, loadDispatchState])

  // Live updates. The chat endpoint emits `project.build.message` on every
  // user + assistant turn; we just re-fetch when one fires for this project.
  // Simpler than splicing single messages into local state, and the message
  // list is bounded anyway.
  useEffect(() => {
    if (!projectId) return
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/activity`)
    wsRef.current = ws
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === "project.build.message" && msg.data?.project_id === projectId) {
          void load()
        }
      } catch {}
    }
    return () => {
      ws.close()
      wsRef.current = null
    }
  }, [projectId, load])

  // Auto-scroll on new messages.
  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight
    }
  }, [messages])

  const send = async (override?: string) => {
    const message = (override ?? input).trim()
    if (!message || sending) return
    setSending(true)
    setError("")
    setInput("")
    try {
      await api.post(`/projects/${projectId}/build/chat`, { message })
      // The chat endpoint already emits a WS event that triggers load(),
      // but call it directly too in case the WS dropped — UX shouldn't
      // depend on the WS being connected.
      await load()
    } catch (e: any) {
      setError(parseDetail(e?.message) || "Send failed")
    } finally {
      setSending(false)
    }
  }

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      void send()
    }
  }

  return (
    <div style={{
      display: "flex", flexDirection: "column",
      height: 480, minHeight: 280, maxHeight: 720,
      background: "var(--bg-card)",
      border: "1px solid var(--border)",
      borderRadius: "var(--radius)",
      overflow: "hidden",
    }}>
      {/* Header */}
      <div style={{
        padding: "10px 14px",
        borderBottom: "1px solid var(--border)",
        display: "flex", alignItems: "center", gap: 8,
        fontSize: 13, fontWeight: 600, color: "var(--text-primary)",
        flexShrink: 0,
      }}>
        <MessageSquare size={14} />
        Build Chat
        <span style={{
          marginLeft: "auto", fontSize: 11, color: "var(--text-muted)",
          fontWeight: 400,
        }}>
          chat with the project_orchestrator agent
        </span>
      </div>

      {/* Messages */}
      <div
        ref={listRef}
        style={{
          flex: 1, overflowY: "auto",
          padding: 12,
          display: "flex", flexDirection: "column", gap: 10,
          fontSize: 13,
        }}
      >
        {messages.length === 0 && (
          <EmptyState onPick={(p) => void send(p)} hasDispatched={hasDispatched} />
        )}
        {messages.map((m) => (
          <MessageRow key={m.message_id} message={m} />
        ))}
        {sending && (
          <div style={{ color: "var(--text-muted)", fontSize: 11, fontStyle: "italic", paddingLeft: 4 }}>
            project_orchestrator is thinking…
          </div>
        )}
      </div>

      {/* Composer */}
      <div style={{
        borderTop: "1px solid var(--border)",
        padding: 10, flexShrink: 0,
        background: "var(--bg-hover)",
      }}>
        {error && (
          <div style={{
            marginBottom: 6, padding: "4px 8px",
            fontSize: 11, color: "var(--danger)",
            background: "var(--danger-subtle)",
            border: "1px solid var(--danger)",
            borderRadius: "var(--radius)",
          }}>{error}</div>
        )}
        <div style={{ display: "flex", gap: 6 }}>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value.slice(0, 4000))}
            onKeyDown={onKey}
            placeholder="Ask about status, dispatch tasks, modify the list…"
            rows={2}
            disabled={sending}
            style={{
              flex: 1, padding: "6px 10px", fontSize: 12,
              fontFamily: "var(--font)", lineHeight: 1.4,
              color: "var(--text-primary)",
              background: "var(--bg-card)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              resize: "none", outline: "none",
              minHeight: 36, maxHeight: 120,
            }}
          />
          <button
            type="button"
            onClick={() => void send()}
            disabled={sending || !input.trim()}
            style={{
              display: "inline-flex", alignItems: "center", gap: 4,
              padding: "0 14px", fontSize: 12, fontWeight: 700,
              background: sending || !input.trim() ? "var(--bg-card)" : "var(--accent)",
              color: sending || !input.trim() ? "var(--text-muted)" : "#0a0014",
              border: "1px solid " + (sending || !input.trim() ? "var(--border)" : "var(--accent)"),
              borderRadius: "var(--radius)",
              cursor: sending || !input.trim() ? "not-allowed" : "pointer",
              fontFamily: "var(--font)", whiteSpace: "nowrap",
            }}
          >
            <Send size={11} />
            <span>Send</span>
          </button>
        </div>
        <div style={{
          marginTop: 4, fontSize: 10, color: "var(--text-muted)",
          textAlign: "right",
        }}>
          {input.length}/4000 · ⏎ to send · ⇧⏎ for newline
        </div>
      </div>
    </div>
  )
}

// ── Empty state ─────────────────────────────────────────────────────────

function EmptyState({
  onPick,
  hasDispatched,
}: {
  onPick: (prompt: string) => void
  hasDispatched: boolean
}) {
  const prompts = starterPrompts(hasDispatched)
  return (
    <div style={{
      flex: 1, display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", gap: 10,
      padding: 20, color: "var(--text-muted)", fontSize: 12,
    }}>
      <div style={{ fontWeight: 600 }}>Talk to the build coordinator.</div>
      <div style={{ fontSize: 11, textAlign: "center", lineHeight: 1.5, maxWidth: 360 }}>
        Ask for status, dispatch tasks one by one or in bulk, modify priorities,
        or add new tasks. The agent has tools to operate on this project's task list.
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6, justifyContent: "center" }}>
        {prompts.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => onPick(p)}
            style={{
              padding: "3px 10px", fontSize: 11,
              background: "var(--bg-hover)", color: "var(--text-secondary)",
              border: "1px solid var(--border)", borderRadius: 12,
              cursor: "pointer", fontFamily: "var(--font)",
            }}
          >
            {p}
          </button>
        ))}
      </div>
    </div>
  )
}

// ── Message row ─────────────────────────────────────────────────────────

function MessageRow({ message }: { message: Message }) {
  const isUser = message.role === "user"
  return (
    <div style={{
      display: "flex", flexDirection: "column", gap: 4,
      alignItems: isUser ? "flex-end" : "flex-start",
    }}>
      <div style={{
        maxWidth: "85%",
        padding: "6px 12px",
        background: isUser ? "var(--accent-subtle, color-mix(in srgb, var(--accent) 15%, transparent))" : "var(--bg-hover)",
        color: "var(--text-primary)",
        border: "1px solid " + (isUser ? "var(--accent)" : "var(--border)"),
        borderRadius: "var(--radius)",
        fontSize: 12, lineHeight: 1.5,
        whiteSpace: "pre-wrap", wordBreak: "break-word",
      }}>
        {message.content || (isUser ? "" : "(no reply)")}
      </div>
      {message.tool_calls && message.tool_calls.length > 0 && (
        <div style={{
          display: "flex", flexWrap: "wrap", gap: 4,
          maxWidth: "85%", paddingLeft: isUser ? 0 : 4,
          alignSelf: isUser ? "flex-end" : "flex-start",
        }}>
          {message.tool_calls.map((tc, i) => (
            <ToolChip key={i} summary={tc.result_summary} />
          ))}
        </div>
      )}
    </div>
  )
}

function ToolChip({ summary }: { summary: string }) {
  // Pick chip color from the emoji prefix the BuildTools class emits.
  const accent =
    summary.startsWith("🚀") ? "var(--accent)" :
    summary.startsWith("⏸️") ? "var(--warning, #f59e0b)" :
    summary.startsWith("❌") ? "var(--danger)" :
    summary.startsWith("➕") ? "var(--success)" :
    summary.startsWith("✏️") ? "var(--info, var(--accent))" :
    summary.startsWith("📋") ? "var(--text-muted)" :
    summary.startsWith("📊") ? "var(--text-muted)" :
    summary.startsWith("ℹ️") ? "var(--text-muted)" :
    "var(--text-muted)"
  return (
    <span style={{
      display: "inline-block",
      padding: "2px 8px",
      fontSize: 10, fontFamily: "var(--font-mono)",
      background: "var(--bg-card)",
      color: accent,
      border: `1px solid ${accent}`,
      borderRadius: 10,
      whiteSpace: "nowrap",
    }}>
      {summary}
    </span>
  )
}

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
