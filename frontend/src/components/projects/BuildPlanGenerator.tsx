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
  AlertTriangle, Trash2,
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

// BPD-50 — readiness gate state. Both PRD and API Spec must be
// FINALIZED before any of the three passes (or the mega button) can
// fire. Tracked here so the buttons can be disabled with a clear
// tooltip instead of letting the user click and get a 409 back.
interface Readiness {
  prd_finalized: boolean
  api_spec_finalized: boolean
}

type BusyKind = "epics" | "features" | "tasks" | "reset" | null

export function BuildPlanGenerator({ projectId, onChanged }: Props) {
  const [counts, setCounts] = useState<Counts>({ epics: 0, features: 0, tasks: 0 })
  const [readiness, setReadiness] = useState<Readiness>({
    prd_finalized: false,
    api_spec_finalized: false,
  })
  const [busy, setBusy] = useState<BusyKind>(null)
  const [error, setError] = useState("")
  const [warnings, setWarnings] = useState<string[]>([])

  const loadCounts = useCallback(async () => {
    try {
      const [e, f, t, prd, spec] = await Promise.all([
        api.get(`/projects/${projectId}/epics`).catch((err) => {
          console.warn("[BPD-gate] /epics fetch failed:", err); return { data: [] }
        }),
        api.get(`/projects/${projectId}/features`).catch((err) => {
          console.warn("[BPD-gate] /features fetch failed:", err); return { data: [] }
        }),
        api.get(`/projects/${projectId}/tasks`).catch((err) => {
          console.warn("[BPD-gate] /tasks fetch failed:", err); return { data: [] }
        }),
        // 404s cleanly when the artifact doesn't exist (no PRD generated
        // yet); the catch turns that into { data: null } so we just
        // treat it as not-finalized. Non-404 errors also fall through
        // here — we log them so a silent 5xx or auth blip doesn't get
        // hidden behind a benign-looking "not finalized" gate.
        api.get(`/projects/${projectId}/prd`).catch((err) => {
          const msg = String(err?.message || err)
          if (!msg.startsWith("404")) console.warn("[BPD-gate] /prd fetch error:", msg)
          return { data: null }
        }),
        api.get(`/projects/${projectId}/api-spec`).catch((err) => {
          const msg = String(err?.message || err)
          if (!msg.startsWith("404")) console.warn("[BPD-gate] /api-spec fetch error:", msg)
          return { data: null }
        }),
      ])
      setCounts({
        epics: (e?.data || []).length,
        features: (f?.data || []).length,
        tasks: (t?.data || []).length,
      })
      // Defensive status check. Backend serializes ArtifactStatus enum
      // values to lowercase strings ("finalized" / "draft"), but make
      // the comparison case-insensitive + tolerate the enum object
      // shape just in case. Log the resolved values to the browser
      // console so a stuck gate is debuggable in 5 seconds.
      const prdStatus = String(prd?.data?.status ?? "").toLowerCase()
      const specStatus = String(spec?.data?.status ?? "").toLowerCase()
      const prdOK = prdStatus === "finalized"
      const specOK = specStatus === "finalized"
      console.debug("[BPD-gate] readiness", {
        prd: { status: prdStatus, finalized: prdOK, version: prd?.data?.version },
        api_spec: { status: specStatus, finalized: specOK, version: spec?.data?.version },
      })
      setReadiness({ prd_finalized: prdOK, api_spec_finalized: specOK })
    } catch (err) {
      console.warn("[BPD-gate] loadCounts crashed:", err)
    }
  }, [projectId])

  useEffect(() => { void loadCounts() }, [loadCounts])

  // Compute a single "gate" message for the disabled tooltip + banner.
  // Empty string means all prerequisites are met.
  const gateMessage = (() => {
    const missing: string[] = []
    if (!readiness.prd_finalized) missing.push("PRD")
    if (!readiness.api_spec_finalized) missing.push("API Specification")
    if (missing.length === 0) return ""
    return `Finalize the ${missing.join(" and ")} before generating epics, features, or tasks.`
  })()
  const isGated = gateMessage !== ""

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

  // The "Approve All & Run All Three Passes" mega-button was removed per
  // user request — it bypassed the per-level review checkpoints that are
  // the whole point of the 3-pass design. Each pass now requires an
  // explicit click, which keeps the user in the loop between epic /
  // feature / task generation. The orchestrator endpoint
  // (`POST /build-plan/generate`) is still on the backend if external
  // tooling needs it; it just isn't surfaced in the UI any more.

  // Reset wipes every epic, feature, and unstarted backlog task in one
  // call (POST /build-plan/reset). Dispatched tasks survive as "Legacy"
  // pseudo-epic rows so the user's already-paid-for work isn't lost.
  // PRD and API Spec are NOT touched — this is a build-plan reset, not
  // a project reset.
  const runReset = async () => {
    const summary = `${counts.epics} epic${counts.epics === 1 ? "" : "s"}, ` +
      `${counts.features} feature${counts.features === 1 ? "" : "s"}, ` +
      `${counts.tasks} task${counts.tasks === 1 ? "" : "s"}`
    if (!window.confirm(
      `Wipe the entire build plan for this project?\n\n` +
      `This deletes: ${summary}\n\n` +
      `Dispatched / completed tasks survive as "Legacy" rows so you ` +
      `don't lose work history. PRD + API Spec are NOT touched.\n\n` +
      `This cannot be undone.`,
    )) return
    setBusy("reset"); setError(""); setWarnings([])
    try {
      const res = await api.post(`/projects/${projectId}/build-plan/reset`, {})
      const d = res?.data || {}
      const lines = [
        `Build plan reset complete.`,
        `Deleted: ${d.epics_deleted || 0} epics, ${d.features_deleted || 0} features, ${d.tasks_deleted || 0} backlog tasks.`,
      ]
      if (d.tasks_preserved_as_legacy > 0) {
        lines.push(`Preserved: ${d.tasks_preserved_as_legacy} dispatched task(s) as Legacy (history kept).`)
      }
      window.alert(lines.join("\n"))
      await loadCounts()
      onChanged?.()
    } catch (e: any) {
      setError(parseDetail(e?.message) || "Build-plan reset failed")
    } finally { setBusy(null) }
  }

  const canRunFeatures = counts.epics > 0
  const canRunTasks = counts.features > 0
  // Reset is meaningful only when there's something to wipe.
  const canReset = counts.epics > 0 || counts.features > 0 || counts.tasks > 0

  return (
    <div style={{
      background: "var(--bg-card)", border: "1px solid var(--border)",
      borderRadius: "var(--radius)", padding: 16,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
        <Sparkles size={14} color="var(--accent)" />
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>
          Build Plan Decomposition
        </h3>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 6, fontSize: 11, fontFamily: "var(--font-mono)" }}>
          <Pill label="epics" value={counts.epics} />
          <Pill label="features" value={counts.features} />
          <Pill label="tasks" value={counts.tasks} />
        </span>
        {/* One-shot wipe — clears every epic, feature, and unstarted task
            for this project. Disabled when there's nothing to wipe.
            Dispatched tasks survive as Legacy rows; PRD + API Spec are
            preserved. Kept visually distinct (danger border, dashed when
            inert) so it can't be confused with a generation button. */}
        <button
          type="button"
          onClick={runReset}
          disabled={!canReset || busy !== null}
          title={
            !canReset
              ? "Nothing to reset — generate epics/features/tasks first."
              : "Wipe every epic, feature, and unstarted backlog task. Dispatched tasks survive as Legacy. PRD + API Spec untouched."
          }
          style={{
            display: "inline-flex", alignItems: "center", gap: 5,
            height: 22, padding: "0 10px", fontSize: 10,
            background: "transparent",
            color: (!canReset || busy !== null) ? "var(--text-muted)" : "var(--danger)",
            border: `1px ${(!canReset || busy !== null) ? "dashed" : "solid"} ${(!canReset || busy !== null) ? "var(--border)" : "var(--danger)"}`,
            borderRadius: 2,
            cursor: (!canReset || busy !== null) ? "not-allowed" : "pointer",
            fontFamily: "var(--font-mono)", whiteSpace: "nowrap", lineHeight: 1,
            textTransform: "uppercase", letterSpacing: 0.5,
          }}
        >
          {busy === "reset"
            ? <Loader2 size={11} className="spin" />
            : <Trash2 size={11} />}
          {busy === "reset" ? "Resetting…" : "Reset Build Plan"}
        </button>
      </div>

      <div style={{
        fontSize: 11, color: "var(--text-secondary)",
        marginBottom: 12, lineHeight: 1.5,
      }}>
        Decompose the PRD into <strong>Epics → Features → Atomic Tasks</strong> with
        a dependency DAG. Run each pass in order, or use the orchestrator to
        cascade them. See <code>docs/prd-build-plan-decomposition.md</code>.
      </div>

      {/* BPD-50 — readiness gate banner. Shown when PRD or API Spec is
          missing; calls out exactly what's blocking + links to the
          sections above. Buttons stay disabled while this banner is up. */}
      {isGated && (
        <div style={{
          marginBottom: 12, padding: "8px 12px",
          background: "color-mix(in srgb, var(--warning, #d4a017) 10%, transparent)",
          border: "1px solid var(--warning, #d4a017)",
          borderRadius: 3, fontSize: 11, color: "var(--text-primary)",
          display: "flex", gap: 8, alignItems: "flex-start",
        }}>
          <AlertTriangle size={12} color="var(--warning, #d4a017)" style={{ flexShrink: 0, marginTop: 1 }} />
          <span style={{ flex: 1, lineHeight: 1.5 }}>
            <strong style={{ color: "var(--warning, #d4a017)" }}>Blocked: </strong>
            {gateMessage} Generation produces dramatically better epics / features /
            atomic tasks when it has the concrete API surface to decompose against.
          </span>
        </div>
      )}

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "stretch" }}>
        <StepButton
          n="1"
          label="Generate Epics"
          done={counts.epics > 0}
          busy={busy === "epics"}
          disabled={isGated}
          disabledReason={gateMessage}
          onClick={runEpics}
        />
        <Arrow done={counts.features > 0} />
        <StepButton
          n="2"
          label="Generate Features"
          done={counts.features > 0}
          busy={busy === "features"}
          disabled={isGated || !canRunFeatures}
          disabledReason={isGated ? gateMessage : "Generate Epics first"}
          onClick={runFeaturesAll}
        />
        <Arrow done={counts.tasks > 0} />
        <StepButton
          n="3"
          label="Generate Tasks"
          done={counts.tasks > 0}
          busy={busy === "tasks"}
          disabled={isGated || !canRunTasks}
          disabledReason={isGated ? gateMessage : "Generate Features first"}
          onClick={runTasksAll}
        />
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
  n, label, done, busy, disabled, disabledReason, onClick,
}: {
  n: string; label: string; done: boolean; busy: boolean;
  disabled?: boolean;
  /** Shown as native `title=` tooltip when the button is disabled.
   *  Used by BPD-50 to explain "Finalize the PRD and API Specification
   *  before generating epics, features, or tasks." on hover instead
   *  of leaving the user to guess why nothing happens. */
  disabledReason?: string;
  onClick: () => void
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
      title={disabled && disabledReason ? disabledReason : undefined}
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
