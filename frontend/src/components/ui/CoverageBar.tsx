interface CoverageBarProps {
  value: number | null
}

export function CoverageBar({ value }: CoverageBarProps) {
  if (value === null) {
    return <span style={{ fontSize: 12, color: "var(--text-muted)" }}>&mdash;</span>
  }

  const fillColor = value >= 80 ? "#22c55e" : value >= 60 ? "#eab308" : "#ef4444"

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div
        style={{
          height: 6,
          width: 80,
          overflow: "hidden",
          borderRadius: 9999,
          background: "var(--bg-hover)",
        }}
      >
        <div
          style={{
            height: "100%",
            borderRadius: 9999,
            background: fillColor,
            width: `${Math.min(value, 100)}%`,
          }}
        />
      </div>
      <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{value}%</span>
    </div>
  )
}
