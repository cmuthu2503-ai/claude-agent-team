interface StatusBadgeProps {
  status: string
  size?: "sm" | "md"
}

interface StatusStyle {
  bg: string
  text: string
  dot: string
  pulse?: boolean
}

const statusStyles: Record<string, StatusStyle> = {
  received:    { bg: "var(--accent-subtle)",  text: "var(--accent)",  dot: "var(--accent)" },
  analyzing:   { bg: "var(--accent-subtle)",  text: "var(--accent)",  dot: "var(--accent)",  pulse: true },
  in_progress: { bg: "var(--accent-subtle)",  text: "var(--accent)",  dot: "var(--accent)",  pulse: true },
  delegated:   { bg: "var(--accent-subtle)",  text: "var(--accent)",  dot: "var(--accent)" },
  completed:   { bg: "var(--success-subtle)", text: "var(--success)", dot: "var(--success)" },
  done:        { bg: "var(--success-subtle)", text: "var(--success)", dot: "var(--success)" },
  failed:      { bg: "var(--danger-subtle)",  text: "var(--danger)",  dot: "var(--danger)" },
  cancelled:   { bg: "var(--bg-hover)",       text: "var(--text-muted)", dot: "var(--text-muted)" },
  rolled_back: { bg: "var(--warning-subtle)", text: "var(--warning)", dot: "var(--warning)" },
  pending:     { bg: "var(--bg-hover)",       text: "var(--text-muted)", dot: "var(--text-muted)" },
  idle:        { bg: "var(--bg-hover)",       text: "var(--text-muted)", dot: "var(--text-muted)" },
  todo:        { bg: "var(--bg-hover)",       text: "var(--text-muted)", dot: "var(--text-muted)" },
  review:      { bg: "var(--info-subtle)",    text: "var(--info)",    dot: "var(--info)" },
  testing:     { bg: "var(--warning-subtle)", text: "var(--warning)", dot: "var(--warning)" },
}

const fallback = statusStyles.pending

export function StatusBadge({ status, size = "sm" }: StatusBadgeProps) {
  const config = statusStyles[status] || fallback
  const padding = size === "sm" ? "2px 8px" : "4px 10px"
  const fontSize = size === "sm" ? 12 : 14

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        borderRadius: 9999,
        fontWeight: 500,
        background: config.bg,
        color: config.text,
        padding,
        fontSize,
        lineHeight: 1.4,
        whiteSpace: "nowrap",
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: 9999,
          background: config.dot,
          flexShrink: 0,
          animation: config.pulse ? "pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite" : "none",
        }}
      />
      {status.replace(/_/g, " ")}
    </span>
  )
}
