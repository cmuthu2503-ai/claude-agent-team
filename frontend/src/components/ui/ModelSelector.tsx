/**
 * PAM-18 — ModelSelector.
 *
 * Portal-based per-agent model picker. Modeled after ThemeSelector
 * (createPortal, rect-based positioning, click-outside, re-applies
 * data-theme + data-mode for CSS-var cascade) so it behaves like
 * every other dropdown on the page — never clips inside a scrolling
 * row, never gets covered by a sticky column.
 *
 * Modes
 * -----
 * - Admin (isAdmin=true): rendered as a button with a caret; clicking
 *   opens the grouped picker. Footer shows "Reset to default" when
 *   override_active.
 * - Read-only (isAdmin=false): rendered as a static chip — no caret,
 *   no click handler — so viewers see the assigned model but can't
 *   mutate it. The backend's admin gate would refuse anyway; this
 *   just removes the affordance so the UI doesn't lie about what's
 *   possible.
 *
 * Display name
 * ------------
 * The dropdown uses each model's `display_name` (catalog field).
 * Falls back to the catalog id when display_name is empty — happens
 * for legacy alias entries that the operator hasn't named yet.
 *
 * Grouping
 * --------
 * Models are grouped first by tier (frontier > workhorse > fast >
 * local > other) then by provider_type. The order matches how an
 * operator thinks ("show me the best models first"), and the local
 * group is segregated so a misclick on Ollama doesn't replace a
 * production frontier model with a self-hosted one.
 */

import { Check, ChevronDown, RotateCcw } from "lucide-react"
import { useState, useRef, useEffect, useCallback, useMemo } from "react"
import { createPortal } from "react-dom"
import { useThemeStore } from "../../stores/theme"
import type { Model } from "../../stores/models"
import { groupByTier, modelLabel } from "./_modelSelectorUtils"

export interface ModelSelectorProps {
  /** Stable key so parent can correlate the change. */
  agentId: string
  /** Catalog id of the model currently effective for this agent. */
  currentModelId: string
  /** YAML default — shown in the "Reset to default" footer label. */
  defaultModelId: string
  /** True iff a DB override is set (drives the dot + footer visibility). */
  overrideActive: boolean
  /** Available models — typically `useModelsStore().models`. */
  models: Model[]
  /** Admin gate. Non-admin renders as a static chip. */
  isAdmin: boolean
  /** Called when admin picks a new model_id. */
  onChange: (agentId: string, modelId: string) => void
  /** Called when admin clicks "Reset to default" footer. Only shown
   *  when overrideActive AND isAdmin. */
  onReset: (agentId: string) => void
}

// ── Component ─────────────────────────────────────────────────────────

export function ModelSelector({
  agentId,
  currentModelId,
  defaultModelId,
  overrideActive,
  models,
  isAdmin,
  onChange,
  onReset,
}: ModelSelectorProps) {
  const { theme, mode } = useThemeStore()
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{
    top?: number
    bottom?: number
    left?: number
    right?: number
  } | null>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)

  // Look up current model for the trigger label. Falls back to the
  // raw id (so an in-flight override with a legacy alias still
  // renders something readable until the agents list refreshes).
  const currentModel = models.find((m) => m.id === currentModelId)
  const currentLabel = currentModel ? modelLabel(currentModel) : currentModelId

  const groups = useMemo(() => groupByTier(models), [models])

  const updatePosition = useCallback(() => {
    if (!buttonRef.current) return
    const rect = buttonRef.current.getBoundingClientRect()
    const DROPDOWN_HEIGHT_ESTIMATE = 320
    const spaceBelow = window.innerHeight - rect.bottom
    const openUp = spaceBelow < DROPDOWN_HEIGHT_ESTIMATE
    const DROPDOWN_WIDTH = 280
    const spaceRight = window.innerWidth - rect.left
    const rightOverflow = spaceRight < DROPDOWN_WIDTH + 12
    setPos({
      ...(openUp
        ? { bottom: window.innerHeight - rect.top + 4 }
        : { top: rect.bottom + 4 }),
      ...(rightOverflow
        ? { right: window.innerWidth - rect.right }
        : { left: rect.left }),
    })
  }, [])

  useEffect(() => {
    if (!open) return
    updatePosition()
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Node
      if (
        buttonRef.current && !buttonRef.current.contains(target) &&
        dropdownRef.current && !dropdownRef.current.contains(target)
      ) {
        setOpen(false)
      }
    }
    const handleScroll = () => updatePosition()
    const handleResize = () => updatePosition()
    document.addEventListener("mousedown", handleClickOutside)
    window.addEventListener("scroll", handleScroll, true)
    window.addEventListener("resize", handleResize)
    return () => {
      document.removeEventListener("mousedown", handleClickOutside)
      window.removeEventListener("scroll", handleScroll, true)
      window.removeEventListener("resize", handleResize)
    }
  }, [open, updatePosition])

  // ── Read-only chip for viewers ──────────────────────────────────────
  if (!isAdmin) {
    return (
      <span
        title={`Model: ${currentLabel}${overrideActive ? " (override)" : " (default)"}`}
        style={chipStyle(overrideActive)}
      >
        {overrideActive && <span style={dotStyle} />}
        {currentLabel}
      </span>
    )
  }

  // ── Admin button + portal dropdown ──────────────────────────────────
  const dropdown = open && pos ? createPortal(
    <div data-theme={theme} data-mode={mode}>
      <div
        ref={dropdownRef}
        style={{
          position: "fixed",
          ...(pos.top != null ? { top: pos.top } : {}),
          ...(pos.bottom != null ? { bottom: pos.bottom } : {}),
          ...(pos.left != null ? { left: pos.left } : {}),
          ...(pos.right != null ? { right: pos.right } : {}),
          width: 280,
          maxHeight: 380,
          background: "var(--bg-card)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          boxShadow: "0 8px 24px rgba(0,0,0,0.3)",
          zIndex: 10000,
          overflowY: "auto",
          fontFamily: "var(--font)",
          color: "var(--text-primary)",
          isolation: "isolate",
        }}
      >
        <div style={headerStyle}>Assign model · {agentId}</div>
        {groups.map((g) => (
          <div key={g.tier}>
            <div style={tierHeaderStyle}>{g.label}</div>
            {g.models.map((m) => (
              <ModelRow
                key={m.id}
                model={m}
                isCurrent={m.id === currentModelId}
                isDefault={m.id === defaultModelId}
                onSelect={() => {
                  onChange(agentId, m.id)
                  setOpen(false)
                }}
              />
            ))}
          </div>
        ))}
        {overrideActive && (
          <button
            onClick={() => {
              onReset(agentId)
              setOpen(false)
            }}
            style={footerButtonStyle}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "var(--bg-hover)"
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "var(--bg-card)"
            }}
          >
            <RotateCcw size={14} />
            Reset to default ({defaultModelId})
          </button>
        )}
      </div>
    </div>,
    document.body,
  ) : null

  return (
    <>
      <button
        ref={buttonRef}
        onClick={() => setOpen(!open)}
        title={
          overrideActive
            ? `Override active: ${currentLabel} (default: ${defaultModelId})`
            : `Model: ${currentLabel}`
        }
        style={buttonStyle(overrideActive, open)}
      >
        {overrideActive && <span style={dotStyle} />}
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {currentLabel}
        </span>
        <ChevronDown size={12} style={{ flexShrink: 0, opacity: 0.6 }} />
      </button>
      {dropdown}
    </>
  )
}

// ── Row ──────────────────────────────────────────────────────────────

function ModelRow({
  model: m,
  isCurrent,
  isDefault,
  onSelect,
}: {
  model: Model
  isCurrent: boolean
  isDefault: boolean
  onSelect: () => void
}) {
  return (
    <button
      onClick={onSelect}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        width: "100%",
        textAlign: "left",
        padding: "8px 12px",
        background: isCurrent ? "var(--accent-subtle)" : "var(--bg-card)",
        color: isCurrent ? "var(--accent)" : "var(--text-primary)",
        border: "none",
        borderBottom: "1px solid var(--border)",
        cursor: "pointer",
        fontFamily: "var(--font)",
        fontSize: 12,
      }}
      onMouseEnter={(e) => {
        if (!isCurrent) e.currentTarget.style.background = "var(--bg-hover)"
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = isCurrent
          ? "var(--accent-subtle)"
          : "var(--bg-card)"
      }}
    >
      <div style={{ minWidth: 0, flex: 1 }}>
        <div
          style={{
            fontWeight: 600,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {modelLabel(m)}
        </div>
        <div
          style={{
            fontSize: 10,
            color: "var(--text-muted)",
            marginTop: 2,
            display: "flex",
            gap: 6,
            alignItems: "center",
          }}
        >
          <span>{m.provider_type}</span>
          {isDefault && <span style={defaultBadgeStyle}>default</span>}
          {m.tool_calling_mode === "prompted" && (
            <span title="Tool calls happen via prompt-based ReAct loop, not native function calling">
              prompted
            </span>
          )}
        </div>
      </div>
      {isCurrent && <Check size={14} style={{ flexShrink: 0 }} />}
    </button>
  )
}

// ── Styles (inline so the component is drop-in without theme CSS) ─────

const dotStyle: React.CSSProperties = {
  width: 6,
  height: 6,
  borderRadius: 99,
  background: "var(--warning, #f59e0b)",
  flexShrink: 0,
}

function chipStyle(overrideActive: boolean): React.CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    padding: "3px 8px",
    background: "var(--bg-hover)",
    color: "var(--text-secondary)",
    border: `1px solid ${overrideActive ? "var(--warning, #f59e0b)" : "var(--border)"}`,
    borderRadius: "var(--radius)",
    fontSize: 11,
    fontFamily: "var(--font)",
    whiteSpace: "nowrap",
    cursor: "default",
  }
}

function buttonStyle(overrideActive: boolean, open: boolean): React.CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    padding: "4px 8px",
    maxWidth: 200,
    background: open ? "var(--accent-subtle)" : "var(--bg-hover)",
    color: open ? "var(--accent)" : "var(--text-secondary)",
    border: `1px solid ${overrideActive ? "var(--warning, #f59e0b)" : "var(--border)"}`,
    borderRadius: "var(--radius)",
    fontSize: 11,
    fontFamily: "var(--font)",
    cursor: "pointer",
    whiteSpace: "nowrap",
  }
}

const headerStyle: React.CSSProperties = {
  padding: "8px 12px",
  fontSize: 10,
  textTransform: "uppercase",
  letterSpacing: "0.08em",
  color: "var(--text-muted)",
  borderBottom: "1px solid var(--border)",
  background: "var(--bg-card)",
  position: "sticky",
  top: 0,
  zIndex: 1,
}

const tierHeaderStyle: React.CSSProperties = {
  padding: "6px 12px",
  fontSize: 9,
  textTransform: "uppercase",
  letterSpacing: "0.1em",
  color: "var(--text-muted)",
  background: "var(--bg-subtle, var(--bg-card))",
  borderBottom: "1px solid var(--border)",
  fontWeight: 600,
}

const footerButtonStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  width: "100%",
  padding: "10px 12px",
  background: "var(--bg-card)",
  color: "var(--text-secondary)",
  border: "none",
  borderTop: "1px solid var(--border)",
  cursor: "pointer",
  fontFamily: "var(--font)",
  fontSize: 12,
  position: "sticky",
  bottom: 0,
}

const defaultBadgeStyle: React.CSSProperties = {
  fontSize: 9,
  padding: "1px 5px",
  background: "var(--accent-subtle)",
  color: "var(--accent)",
  borderRadius: 3,
  textTransform: "uppercase",
  letterSpacing: "0.05em",
}
