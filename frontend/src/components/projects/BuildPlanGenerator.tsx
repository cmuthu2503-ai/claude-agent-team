/**
 * BuildPlanGenerator — UI wrapper for the BPD §6.8 three-pass
 * generation flow.
 *
 * Three buttons, one per pass:
 *   1. Generate Epics            → POST /epics/generate
 *   2. Generate Features (all)   → POST /epics/:eid/features/generate-all
 *   3. Generate Tasks (all)      → POST /features/:fid/tasks/generate-all
 *
 * Plus an opt-in mega-button:
 *   ⚡ Approve all & Auto-dispatch → POST /build-plan/generate
 *   then PATCH auto_dispatch_on_deploy=true
 *
 * Default state: step-by-step gates per BPD-35 (each button enabled
 * only when its predecessor has at least one row in the DB). Mega-
 * button shown but with a "this fires N LLM calls" caveat.
 *
 * Renders a tight status strip at the top showing the current state
 * (epics: 0 / features: 0 / tasks: 0) so the user can see progress
 * without clicking through to other panels.
 */

import { useCallback, useEffect, useState } from "react"
import {
  Sparkles, ChevronRight, Loader2, CheckCircle2,
  AlertTriangle, Rocket,
} from "lucide-react"
import { api } from "../../lib/api"

interface Props {
  projectId: string
  /** Called after a successful generation so the parent can re-fetch. */
  onChanged?: () => void
}

interface Counts {
  epics: number
  features: number
  tasks: number
}

type BusyKind = "epics" | "features" | "tasks" | "mega" | null

export function BuildPlanGenerator({ projectId, onChanged }: Props) {
  const [counts, setCounts] = useState<Counts>({ epics: 0, features: 0, tasks: 0 })
  const [busy, setBusy] = useState<BusyKind>(null)
  const [error, setError] = useState("")
  const [warnings, setWarnings] = useState<string[]>([])

  const loadCounts = useCallback(async () => {
    try {
      const [e, f, t] = await Promise.all([
        api.get(`/projects/${projectId}/epics`).catch(() => ({ data: [] })),
        api.get(`/projects/${projectId}/features`).catch(() => ({ data: [] })),
        api.get(`/projects/${projectId}/tasks`).catch(() => ({ data: [] })),
      ])
      setCounts({
        epics: (e?.data || []).length,
        features: (f?.data || []).length,
        tasks: (t?.data || []).length,
      })
    } catch {/* soft */}
  }, [projectId])

  useEffect(() => { void loadCounts() }, [loadCounts])

  const runEpics = async () => {
    setBusy("epics"); setError(""); setWarnings([])
    try {
      const res = await api.post(`/projects/${projectId}/epics/generate`, {})
      if (res?.meta?.truncation_hint) setWarnings([res.meta.truncation_hint])
      await loadCounts()
      onChanged?.()
    } catch (e: any) {
      setError(parseDetail(e?.message) || "Epic generation failed")
    } finally { setBusy(null) }
  }

  const runFeaturesAll = async () => {
    setBusy("features"); setError("")
    try {
      // Resolve an epic id to satisfy the path; the endpoint ignores it.
      const epics = await api.get(`/projects/${projectId}/epics`)
      const first = epics?.data?.[0]?.epic_id
      if (!first) {
        setError("No epics yet — run 'Generate Epics' first.")
        return
      }
      await api.post(
        `/projects/${projectId}/epics/${first}/features/generate-all`, {},
      )
      await loadCounts()
      onChanged?.()
    } catch (e: any) {
      setError(parseDetail(e?.message) || "Feature generation failed")
    } finally { setBusy(null) }
  }

  const runTasksAll = async () => {
    setBusy("tasks"); setError("")
    try {
      const features = await api.get(`/projects/${projectId}/features`)
      const first = features?.data?.[0]?.feature_id
      if (!first) {
        setError("No features yet — run 'Generate Features' first.")
        return
      }
      await api.post(
        `/projects/${projectId}/features/${first}/tasks/generate-all`, {},
      )
      await loadCounts()
      onChanged?.()
    } catch (e: any) {
      setError(parseDetail(e?.message) || "Task generation failed")
    } finally { setBusy(null) }
  }

  const runMega = async () => {
    if (!window.confirm(
      "Run all 3 passes back-to-back?\n\n" +
      "This makes 1 (epics) + N (features per epic) + M (tasks per feature) " +
      "LLM calls. For a typical project that's 15-50 calls and a few minutes " +
      "of wall time. Each level auto-finalizes between passes — no review " +
      "gates.\n\nProceed?"
    )) return
    setBusy("mega"); setError(""); setWarnings([])
    try {
      await api.post(`/projects/${projectId}/build-plan/generate`, {})
      await loadCounts()
      onChanged?.()
    } catch (e: any) {
      setError(parseDetail(e?.message) || "Build-plan orchestrator failed")
    } finally { setBusy(null) }
  }

  const canRunFeatures = counts.epics > 0
  const canRunTasks = counts.features > 0

  return (
    <div style={{
      background: "var(--bg-card)", border: "1px solid var(--border)",
      borderRadius: "var(--radius)", padding: 16,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <Sparkles size={14} color="var(--accent)" />
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>
          Build Plan Decomposition
        </h3>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 6, fontSize: 11, fontFamily: "var(--font-mono)" }}>
          <Pill label="epics" value={counts.epics} />
          <Pill label="features" value={counts.features} />
          <Pill label="tasks" value={counts.tasks} />
        </span>
      </div>

      <div style={{
        fontSize: 11, color: "var(--text-secondary)",
        marginBottom: 12, lineHeight: 1.5,
      }}>
        Decompose the PRD into <strong>Epics → Features → Atomic Tasks</strong> with
        a dependency DAG. Run each pass in order, or use the orchestrator to
        cascade them. See <code>docs/prd-build-plan-decomposition.md</code>.
      </div>

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "stretch" }}>
        <StepButton
          n="1"
          label="Generate Epics"
          done={counts.epics > 0}
          busy={busy === "epics"}
          onClick={runEpics}
        />
        <Arrow done={counts.features > 0} />
        <StepButton
          n="2"
          label="Generate Features"
          done={counts.features > 0}
          busy={busy === "features"}
          disabled={!canRunFeatures}
          onClick={runFeaturesAll}
        />
        <Arrow done={counts.tasks > 0} />
        <StepButton
          n="3"
          label="Generate Tasks"
          done={counts.tasks > 0}
          busy={busy === "tasks"}
          disabled={!canRunTasks}
          onClick={runTasksAll}
        />
      </div>

      <div style={{ marginTop: 10 }}>
        <button
          type="button"
          onClick={runMega}
          disabled={busy !== null}
          style={{
            width: "100%", padding: "8px 12px",
            background: busy === "mega"
              ? "var(--bg-hover)"
              : "linear-gradient(135deg, var(--accent), var(--info, #b026ff))",
            color: busy === "mega" ? "var(--text-muted)" : "#0a0014",
            border: "none", borderRadius: "var(--radius)",
            fontWeight: 700, fontSize: 12, letterSpacing: 0.3,
            cursor: busy ? "wait" : "pointer",
            opacity: busy && busy !== "mega" ? 0.5 : 1,
            display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6,
            fontFamily: "var(--font)",
          }}
        >
          {busy === "mega" ? <Loader2 size={12} className="spin" /> : <Rocket size={12} />}
          {busy === "mega"
            ? "Running all three passes…"
            : "⚡ Approve All & Run All Three Passes"}
        </button>
      </div>

      {warnings.length > 0 && (
        <div style={{
          marginTop: 8, padding: "6px 10px",
          background: "var(--warning-subtle, color-mix(in srgb, var(--warning, #d4a017) 12%, transparent))",
          border: "1px solid var(--warning, #d4a017)",
          borderRadius: 3, fontSize: 11, color: "var(--warning, #d4a017)",
          display: "flex", gap: 6, alignItems: "flex-start",
        }}>
          <AlertTriangle size={11} style={{ flexShrink: 0, marginTop: 2 }} />
          <span style={{ flex: 1 }}>{warnings[0]}</span>
        </div>
      )}
      {error && (
        <div style={{
          marginTop: 8, padding: "6px 10px",
          background: "var(--danger-subtle)",
          border: "1px solid var(--danger)", borderRadius: 3,
          fontSize: 11, color: "var(--danger)",
        }}>
          {error}
        </div>
      )}

      <style>{`
        .spin { animation: bpd-spin 0.9s linear infinite; }
        @keyframes bpd-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
    </div>
  )
}

function StepButton({
  n, label, done, busy, disabled, onClick,
}: {
  n: string; label: string; done: boolean; busy: boolean;
  disabled?: boolean; onClick: () => void
}) {
  const color = done
    ? "var(--success)"
    : busy ? "var(--accent)" : disabled ? "var(--text-muted)" : "var(--text-secondary)"
  const bg = done
    ? "color-mix(in srgb, var(--success) 14%, transparent)"
    : busy
      ? "color-mix(in srgb, var(--accent) 14%, transparent)"
      : "transparent"
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
      style={{
        flex: 1, minWidth: 140, padding: "10px 12px",
        background: bg, color, border: `1px solid ${color}`,
        borderRadius: "var(--radius)", cursor: disabled ? "not-allowed" : busy ? "wait" : "pointer",
        fontSize: 11, fontWeight: 600, fontFamily: "var(--font)",
        display: "flex", flexDirection: "column", alignItems: "center", gap: 4,
        opacity: disabled ? 0.5 : 1,
      }}
    >
      <span style={{ fontSize: 9, letterSpacing: 1, opacity: 0.8 }}>
        PASS {n} {done ? "✓" : busy ? "…" : ""}
      </span>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
        {done && <CheckCircle2 size={11} />}
        {busy && <Loader2 size={11} className="spin" />}
        {label}
      </span>
    </button>
  )
}

function Arrow({ done }: { done: boolean }) {
  return (
    <span style={{
      display: "flex", alignItems: "center",
      color: done ? "var(--success)" : "var(--text-muted)",
    }}>
      <ChevronRight size={14} />
    </span>
  )
}

function Pill({ label, value }: { label: string; value: number }) {
  const color = value > 0 ? "var(--accent)" : "var(--text-muted)"
  return (
    <span style={{
      padding: "2px 7px", borderRadius: 2,
      background: "var(--bg-hover)", border: "1px solid var(--border)",
      color,
    }}>
      <span style={{ color: "var(--text-muted)" }}>{label}:</span>{" "}
      <strong>{value}</strong>
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
    if (typeof d === "object" && d) return d.hint || d.message || d.error || JSON.stringify(d)
    return msg
  } catch { return msg }
}
