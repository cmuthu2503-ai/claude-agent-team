/**
 * TaskDrillIn — the body content shown inside a PopupWindow when the
 * user clicks an EnrichedTaskCard.
 *
 * Loads detail via a single GET /requests/:id call (which already
 * includes subtasks, stories+test_cases, deployment state, cost, and
 * artifacts on the server). No separate /subtasks fetch needed.
 *
 * Renders, in priority order for a glance:
 *   - Summary chips (status, project, cost, duration)
 *   - Workflow stage strip
 *   - Description
 *   - Code Review (code_reviewer.output_text + linked PR if present)
 *   - Tests Executed (tester_specialist.output_text + structured
 *     test_case rows from stories[])
 *   - Code Committed (commit SHA, repo link, file list)
 *   - Deployment (strategy, supervisor step_history, URL, errors)
 *   - Agent Timeline (chronological)
 *   - Error block when present
 *
 * Gracefully degrades when the card has no request_id (pure-backlog
 * task — only description shown).
 */

import { useEffect, useState } from "react"
import {
  ExternalLink, GitCommit, AlertTriangle, ChevronRight, ChevronDown,
  CheckCircle2, XCircle, FileText, Eye, FlaskConical, Rocket, Clock,
} from "lucide-react"
import { api } from "../../lib/api"
import type { CardData, WorkflowStage } from "./types"

const STAGES: WorkflowStage[] = [
  "prd", "stories", "development", "review", "testing", "code_commit", "deploy",
]
const STAGE_SHORT: Record<WorkflowStage, string> = {
  prd: "PRD", stories: "Stories", development: "Dev", review: "Review",
  testing: "Test", code_commit: "Commit", deploy: "Deploy",
}

interface SubtaskRow {
  subtask_id: string
  agent_id: string
  display_name?: string
  status: string
  started_at: string | null
  completed_at: string | null
  output_text?: string
  output_artifacts?: any
  error_message?: string | null
}

interface TestCase {
  test_id: string
  name: string
  status: string
  last_run_at: string | null
}

interface Story {
  story_id: string
  title: string
  status: string
  test_cases?: TestCase[]
}

interface DeploymentBlock {
  deployment_id?: string
  current_step?: string | null
  strategy?: string | null
  strategy_reasoning?: string | null
  risk?: string | null
  commit_sha?: string | null
  step_history?: string | any[] | null
  files_committed?: any
  error_message?: string | null
  started_at?: string | null
  completed_at?: string | null
}

interface ArtifactsBlock {
  published_files?: string[] | null
  commit_sha?: string | null
  commit_url?: string | null
}

interface RequestDetail {
  request_id: string
  status: string
  description?: string
  project_id?: string | null
  created_at?: string | null
  completed_at?: string | null
  subtasks?: SubtaskRow[]
  stories?: Story[]
  deployment?: DeploymentBlock | null
  artifacts?: ArtifactsBlock
  total_cost?: { cost_usd?: number }
  commit_sha?: string | null
  commit_url?: string | null
  published_files?: string[] | null
  code_commit_error?: string | null
}

export function TaskDrillIn({ card }: { card: CardData }) {
  const [loading, setLoading] = useState<boolean>(!!card.request_id)
  const [error, setError] = useState<string>("")
  const [detail, setDetail] = useState<RequestDetail | null>(null)

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      if (!card.request_id) {
        setLoading(false)
        return
      }
      setLoading(true)
      setError("")
      try {
        const res = await api.get<{ data: RequestDetail }>(`/requests/${card.request_id}`)
        if (cancelled) return
        if (res?.data) setDetail(res.data)
      } catch (e: any) {
        if (!cancelled) setError(parseDetail(e?.message) || "Failed to load detail")
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void run()
    return () => { cancelled = true }
  }, [card.request_id])

  const subtasks = detail?.subtasks ?? []
  const reviewer = subtasks.find((s) => s.agent_id === "code_reviewer")
  const tester = subtasks.find((s) => s.agent_id === "tester_specialist")
  const commitSha = detail?.artifacts?.commit_sha ?? detail?.commit_sha
  const commitUrl = detail?.artifacts?.commit_url ?? detail?.commit_url
  const publishedFiles = detail?.artifacts?.published_files ?? detail?.published_files ?? []
  const allTestCases = (detail?.stories ?? []).flatMap((s) => s.test_cases ?? [])

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* Summary chip row */}
      <SummaryChips card={card} detail={detail} />

      {/* Workflow stage strip */}
      {card.current_stage && (
        <Section label="Workflow">
          <FullStageStrip
            currentStage={card.current_stage}
            terminalGood={card.status === "deployed"}
            terminalBad={card.status === "failed" || card.status === "cancelled"}
          />
        </Section>
      )}

      {/* Description */}
      {card.description && (
        <Section label="Description">
          <p style={{
            margin: 0, fontSize: 12, color: "var(--text-secondary)",
            lineHeight: 1.5, whiteSpace: "pre-wrap",
          }}>
            {card.description}
          </p>
        </Section>
      )}

      {/* Loading / error states for fetched detail */}
      {card.request_id && loading && (
        <p style={{ fontSize: 12, color: "var(--text-muted)", margin: 0 }}>
          Loading request detail…
        </p>
      )}
      {error && (
        <p style={{ fontSize: 12, color: "var(--danger)", margin: 0 }}>
          {error}
        </p>
      )}

      {/* Code Review */}
      {reviewer && (reviewer.output_text || reviewer.error_message) && (
        <Section
          label="Code Review"
          icon={<Eye size={11} />}
          subtitle={`${reviewer.display_name || reviewer.agent_id} · ${reviewer.status}`}
        >
          <CollapsibleText
            text={reviewer.output_text || reviewer.error_message || ""}
            previewLines={4}
            label="reviewer feedback"
          />
        </Section>
      )}

      {/* Tests Executed */}
      {(tester || allTestCases.length > 0) && (
        <Section
          label="Tests Executed"
          icon={<FlaskConical size={11} />}
          subtitle={
            tester
              ? `${tester.display_name || tester.agent_id} · ${tester.status}`
              : undefined
          }
        >
          {allTestCases.length > 0 && (
            <TestCasesList cases={allTestCases} />
          )}
          {tester && (tester.output_text || tester.error_message) && (
            <div style={{ marginTop: allTestCases.length > 0 ? 8 : 0 }}>
              <CollapsibleText
                text={tester.output_text || tester.error_message || ""}
                previewLines={4}
                label="tester output"
              />
            </div>
          )}
        </Section>
      )}

      {/* Code Committed */}
      {(commitSha || publishedFiles.length > 0) && (
        <Section label="Code Committed" icon={<GitCommit size={11} />}>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {commitSha && (
              <div style={{ display: "flex", gap: 8, fontSize: 11, alignItems: "center", flexWrap: "wrap" }}>
                <span style={{
                  fontFamily: "var(--font-mono)", color: "var(--accent)",
                  padding: "1px 6px", borderRadius: 2,
                  background: "color-mix(in srgb, var(--accent) 10%, transparent)",
                  border: "1px solid var(--accent)",
                }}>
                  {commitSha.slice(0, 10)}
                </span>
                {commitUrl && (
                  <a
                    href={commitUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      color: "var(--accent)", textDecoration: "none",
                      display: "inline-flex", alignItems: "center", gap: 3,
                      fontSize: 11,
                    }}
                  >
                    view on GitHub <ExternalLink size={10} />
                  </a>
                )}
              </div>
            )}
            {publishedFiles.length > 0 && (
              <CollapsibleFiles files={publishedFiles} />
            )}
          </div>
        </Section>
      )}

      {/* Deployment */}
      {detail?.deployment && (
        <Section label="Deployment" icon={<Rocket size={11} />}>
          <DeploymentBlockView dep={detail.deployment} />
        </Section>
      )}

      {/* Agent timeline (compact — full outputs are above where relevant) */}
      {subtasks.length > 0 && (
        <Section label="Agent Timeline" count={subtasks.length}>
          <div style={{
            display: "flex", flexDirection: "column", gap: 4,
            color: "var(--text-secondary)",
          }}>
            {subtasks.map((s) => (
              <div key={s.subtask_id} style={{
                display: "flex", gap: 8, fontSize: 11,
                alignItems: "center",
              }}>
                <span style={{
                  fontFamily: "var(--font-mono)", color: "var(--text-muted)",
                  minWidth: 56, fontSize: 10,
                }}>
                  {formatTs(s.started_at) || "—"}
                </span>
                <span style={{ fontWeight: 600, minWidth: 130 }}>
                  {s.display_name || s.agent_id}
                </span>
                <StatusChip status={s.status} />
                <DurationChip start={s.started_at} end={s.completed_at} />
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Error block */}
      {(card.error_summary || detail?.code_commit_error) && (
        <Section label="Error" icon={<AlertTriangle size={11} color="var(--danger)" />}>
          <div style={{
            padding: 8, borderRadius: 3,
            background: "color-mix(in srgb, var(--danger) 8%, transparent)",
            border: "1px solid var(--danger)",
            color: "var(--danger)", fontSize: 11, fontFamily: "var(--font-mono)",
            whiteSpace: "pre-wrap", maxHeight: 240, overflow: "auto",
          }}>
            {card.error_summary || detail?.code_commit_error}
          </div>
        </Section>
      )}

      {!card.request_id && !card.description && (
        <p style={{
          margin: 0, fontSize: 12, color: "var(--text-muted)",
          fontStyle: "italic",
        }}>
          This task hasn't been dispatched yet — no agent activity or outputs to show.
        </p>
      )}
    </div>
  )
}

// ── Section primitives ──────────────────────────────────────────────

function Section({
  label, count, icon, subtitle, children,
}: {
  label: string
  count?: number
  icon?: React.ReactNode
  subtitle?: string
  children: React.ReactNode
}) {
  return (
    <div>
      <div style={{
        fontSize: 10, fontWeight: 700, letterSpacing: 1,
        textTransform: "uppercase", color: "var(--text-muted)",
        marginBottom: 4, display: "inline-flex", alignItems: "center", gap: 6,
      }}>
        {icon}
        <span>{label}{count !== undefined ? ` · ${count}` : ""}</span>
        {subtitle && (
          <span style={{
            color: "var(--text-secondary)", letterSpacing: 0,
            textTransform: "none", fontWeight: 500,
          }}>
            · {subtitle}
          </span>
        )}
      </div>
      {children}
    </div>
  )
}

function SummaryChips({
  card, detail,
}: { card: CardData; detail: RequestDetail | null }) {
  // Build a row of compact stat chips that answers "what should I know
  // at a glance?". Each chip is conditional — never show "—" placeholders
  // for missing data; just drop the chip.
  const chips: Array<{ label: string; value: string; tone?: "good" | "bad" | "neutral" }> = []
  chips.push({
    label: "status", value: card.status,
    tone: card.status === "deployed" ? "good"
        : card.status === "failed" || card.status === "cancelled" ? "bad"
        : "neutral",
  })
  if (card.request_id) chips.push({ label: "request", value: card.request_id })
  const cost = detail?.total_cost?.cost_usd
  if (typeof cost === "number" && cost > 0) {
    chips.push({ label: "cost", value: `$${cost.toFixed(2)}` })
  }
  const duration = humanDuration(detail?.created_at, detail?.completed_at)
  if (duration) chips.push({ label: "duration", value: duration })
  if (card.priority) chips.push({ label: "priority", value: card.priority })
  if (chips.length === 0) return null
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 2 }}>
      {chips.map((c) => {
        const color =
          c.tone === "good" ? "var(--success)" :
          c.tone === "bad" ? "var(--danger)" :
          "var(--text-secondary)"
        return (
          <span key={c.label} style={{
            display: "inline-flex", alignItems: "center", gap: 4,
            fontSize: 10, fontFamily: "var(--font-mono)",
            padding: "2px 8px", borderRadius: 3,
            background: "var(--bg-hover)",
            border: "1px solid var(--border)",
            color,
          }}>
            <span style={{
              color: "var(--text-muted)", textTransform: "uppercase",
              letterSpacing: 0.5, fontSize: 9,
            }}>
              {c.label}
            </span>
            <span>{c.value}</span>
          </span>
        )
      })}
    </div>
  )
}

// ── Collapsible long text (agent outputs) ───────────────────────────

function CollapsibleText({
  text, previewLines = 4, label = "output",
}: { text: string; previewLines?: number; label?: string }) {
  const [expanded, setExpanded] = useState(false)
  const lines = text.split("\n")
  const truncated = lines.length > previewLines
  const shown = expanded || !truncated ? text : lines.slice(0, previewLines).join("\n")
  return (
    <div>
      <pre style={{
        margin: 0, fontSize: 11, lineHeight: 1.5,
        whiteSpace: "pre-wrap", wordBreak: "break-word",
        color: "var(--text-secondary)", fontFamily: "var(--font)",
        padding: "8px 10px", borderRadius: 3,
        background: "var(--bg-hover)", border: "1px solid var(--border)",
        maxHeight: expanded ? 400 : undefined,
        overflow: expanded ? "auto" : "hidden",
      }}>
        {shown}
        {truncated && !expanded && "\n…"}
      </pre>
      {truncated && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          style={{
            marginTop: 4, padding: "2px 6px", fontSize: 10,
            background: "transparent", color: "var(--accent)",
            border: "none", cursor: "pointer", fontFamily: "var(--font)",
            display: "inline-flex", alignItems: "center", gap: 3,
          }}
        >
          {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
          {expanded ? `collapse ${label}` : `show full ${label} (${lines.length} lines)`}
        </button>
      )}
    </div>
  )
}

// ── Test case list ─────────────────────────────────────────────────

function TestCasesList({ cases }: { cases: TestCase[] }) {
  const passed = cases.filter((c) => c.status === "passed").length
  const failed = cases.filter((c) => c.status === "failed").length
  const other = cases.length - passed - failed
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", gap: 8, fontSize: 11, color: "var(--text-secondary)" }}>
        <span style={{ color: "var(--success)" }}>
          <CheckCircle2 size={10} style={{ verticalAlign: -1 }} /> {passed} passed
        </span>
        {failed > 0 && (
          <span style={{ color: "var(--danger)" }}>
            <XCircle size={10} style={{ verticalAlign: -1 }} /> {failed} failed
          </span>
        )}
        {other > 0 && (
          <span style={{ color: "var(--text-muted)" }}>
            {other} other
          </span>
        )}
      </div>
      <div style={{
        display: "flex", flexDirection: "column",
        maxHeight: 180, overflow: "auto",
        border: "1px solid var(--border)", borderRadius: 3,
      }}>
        {cases.slice(0, 30).map((tc, i) => (
          <div key={tc.test_id || i} style={{
            display: "flex", alignItems: "center", gap: 8,
            padding: "4px 8px", fontSize: 11,
            borderTop: i > 0 ? "1px solid var(--border)" : "none",
          }}>
            <StatusChip status={tc.status} />
            <span style={{
              flex: 1, fontFamily: "var(--font-mono)", fontSize: 10,
              color: "var(--text-secondary)", overflow: "hidden",
              textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}>
              {tc.name}
            </span>
          </div>
        ))}
        {cases.length > 30 && (
          <div style={{ padding: "4px 8px", fontSize: 10, color: "var(--text-muted)" }}>
            … and {cases.length - 30} more
          </div>
        )}
      </div>
    </div>
  )
}

// ── Committed files list ───────────────────────────────────────────

function CollapsibleFiles({ files }: { files: string[] }) {
  const [expanded, setExpanded] = useState(false)
  const preview = 6
  const shown = expanded ? files : files.slice(0, preview)
  return (
    <div>
      <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 4 }}>
        <FileText size={10} style={{ verticalAlign: -1, marginRight: 4 }} />
        {files.length} file{files.length === 1 ? "" : "s"} committed
      </div>
      <div style={{
        display: "flex", flexDirection: "column", gap: 2,
        fontFamily: "var(--font-mono)", fontSize: 10,
        color: "var(--text-secondary)",
      }}>
        {shown.map((f) => (
          <div key={f} style={{
            padding: "2px 6px", borderRadius: 2,
            background: "var(--bg-hover)",
          }}>
            {f}
          </div>
        ))}
      </div>
      {files.length > preview && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          style={{
            marginTop: 4, padding: "2px 6px", fontSize: 10,
            background: "transparent", color: "var(--accent)",
            border: "none", cursor: "pointer", fontFamily: "var(--font)",
            display: "inline-flex", alignItems: "center", gap: 3,
          }}
        >
          {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
          {expanded ? "collapse" : `show all ${files.length} files`}
        </button>
      )}
    </div>
  )
}

// ── Deployment block ───────────────────────────────────────────────

function DeploymentBlockView({ dep }: { dep: DeploymentBlock }) {
  // step_history is stored as a JSON string in the DB and might already
  // be decoded by the API serializer. Normalize defensively.
  let steps: any[] = []
  if (Array.isArray(dep.step_history)) {
    steps = dep.step_history
  } else if (typeof dep.step_history === "string" && dep.step_history) {
    try { steps = JSON.parse(dep.step_history) } catch { /* keep [] */ }
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 11 }}>
      {(dep.strategy || dep.risk) && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {dep.strategy && (
            <span style={{
              padding: "2px 8px", borderRadius: 2,
              background: "var(--bg-hover)", border: "1px solid var(--border)",
              fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--accent)",
            }}>
              strategy: {dep.strategy}
            </span>
          )}
          {dep.risk && (
            <span style={{
              padding: "2px 8px", borderRadius: 2,
              background: "var(--bg-hover)", border: "1px solid var(--border)",
              fontSize: 10, fontFamily: "var(--font-mono)",
              color:
                dep.risk === "low" ? "var(--success)" :
                dep.risk === "high" ? "var(--danger)" :
                "var(--warning, #d4a017)",
            }}>
              risk: {dep.risk}
            </span>
          )}
          {dep.current_step && (
            <span style={{
              padding: "2px 8px", borderRadius: 2,
              background: "var(--bg-hover)", border: "1px solid var(--border)",
              fontSize: 10, fontFamily: "var(--font-mono)",
              color: "var(--text-secondary)",
            }}>
              step: {dep.current_step}
            </span>
          )}
        </div>
      )}
      {dep.strategy_reasoning && (
        <div style={{
          padding: "6px 8px", borderRadius: 3,
          background: "var(--bg-hover)", border: "1px solid var(--border)",
          color: "var(--text-secondary)", fontSize: 11, lineHeight: 1.4,
        }}>
          <span style={{ color: "var(--text-muted)", fontSize: 10, fontWeight: 700 }}>
            Judge reasoning:
          </span>
          {dep.strategy_reasoning}
        </div>
      )}
      {steps.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontSize: 10, color: "var(--text-muted)", fontWeight: 700 }}>
            Supervisor steps:
          </span>
          {steps.slice(0, 12).map((s, i) => {
            const name = s?.step ?? s?.name ?? String(s)
            const ok = s?.ok ?? (s?.status === "ok" || s?.status === "completed")
            return (
              <div key={i} style={{
                display: "flex", gap: 6, alignItems: "center",
                fontSize: 10, color: "var(--text-secondary)",
              }}>
                {ok
                  ? <CheckCircle2 size={9} color="var(--success)" />
                  : <Clock size={9} color="var(--text-muted)" />}
                <span style={{ fontFamily: "var(--font-mono)" }}>{name}</span>
              </div>
            )
          })}
        </div>
      )}
      {dep.error_message && (
        <div style={{
          padding: 8, borderRadius: 3,
          background: "color-mix(in srgb, var(--danger) 8%, transparent)",
          border: "1px solid var(--danger)",
          color: "var(--danger)", fontSize: 11, fontFamily: "var(--font-mono)",
          whiteSpace: "pre-wrap", maxHeight: 140, overflow: "auto",
        }}>
          {dep.error_message}
        </div>
      )}
    </div>
  )
}

// ── Workflow strip ─────────────────────────────────────────────────

function FullStageStrip({
  currentStage, terminalGood, terminalBad,
}: { currentStage: WorkflowStage; terminalGood: boolean; terminalBad: boolean }) {
  const currentIdx = STAGES.indexOf(currentStage)
  return (
    <div style={{ display: "flex", gap: 4 }}>
      {STAGES.map((s, i) => {
        const isPast = terminalGood || i < currentIdx
        const isCurrent = i === currentIdx && !terminalGood
        const isFailed = i === currentIdx && terminalBad
        const color =
          isFailed ? "var(--danger)" :
          isPast   ? "var(--success)" :
          isCurrent ? "var(--accent)" :
          "var(--text-muted)"
        return (
          <span
            key={s}
            style={{
              flex: 1, padding: "6px 4px", textAlign: "center",
              fontSize: 10, fontWeight: 600,
              background: isCurrent
                ? "color-mix(in srgb, var(--accent) 14%, transparent)"
                : isFailed
                  ? "color-mix(in srgb, var(--danger) 14%, transparent)"
                  : "var(--bg-hover)",
              border: "1px solid " + color,
              borderRadius: 3, color,
            }}
          >
            {isPast ? "✓ " : ""}{STAGE_SHORT[s]}
          </span>
        )
      })}
    </div>
  )
}

// ── Status / duration chips ────────────────────────────────────────

function StatusChip({ status }: { status: string }) {
  const color =
    status === "completed" || status === "deployed" || status === "passed" ? "var(--success)" :
    status === "failed" || status === "cancelled"   ? "var(--danger)" :
    status === "in_progress" || status === "running" ? "var(--accent)" :
    "var(--text-muted)"
  return (
    <span style={{
      fontSize: 10, padding: "1px 6px", borderRadius: 2,
      color,
      background: "color-mix(in srgb, " + color + " 12%, transparent)",
      border: "1px solid " + color,
      fontFamily: "var(--font-mono)",
    }}>
      {status}
    </span>
  )
}

function DurationChip({
  start, end,
}: { start: string | null; end: string | null }) {
  const d = humanDuration(start, end)
  if (!d) return null
  return (
    <span style={{
      fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)",
    }}>
      {d}
    </span>
  )
}

// ── Helpers ────────────────────────────────────────────────────────

function formatTs(iso: string | null): string {
  if (!iso) return ""
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" })
  } catch {
    return iso.slice(11, 19)
  }
}

function humanDuration(start?: string | null, end?: string | null): string | null {
  if (!start || !end) return null
  try {
    const a = new Date(start).getTime()
    const b = new Date(end).getTime()
    const ms = b - a
    if (!isFinite(ms) || ms <= 0) return null
    const sec = Math.floor(ms / 1000)
    if (sec < 60) return `${sec}s`
    const min = Math.floor(sec / 60)
    if (min < 60) return `${min}m ${sec % 60}s`
    const hr = Math.floor(min / 60)
    return `${hr}h ${min % 60}m`
  } catch { return null }
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
