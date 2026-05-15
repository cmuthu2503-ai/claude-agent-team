import { useState, useEffect } from "react"
import { api } from "../lib/api"
import { StatusBadge } from "../components/ui/StatusBadge"

// Canonical Release shape — mirrors src/api/routes/releases.py::_CANONICAL_DOC.
// Keep these field names in sync; the page renders nothing useful if they drift.
interface Release {
  deploy_id: string
  request_id: string
  commit_sha: string
  environment: string
  status: string
  started_at: string | null
  completed_at: string | null
  error_message: string | null
}

function formatTime(iso: string | null): string {
  if (!iso) return "—"
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

export function ReleasesPage() {
  const [releases, setReleases] = useState<Release[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    api
      .get("/releases")
      .then((res) => {
        setReleases(res.data || [])
        setError(null)
      })
      .catch((err: any) => {
        setError(err?.message || "Failed to load releases")
      })
      .finally(() => setLoading(false))
  }, [])

  return (
    <div
      style={{
        maxWidth: 1100,
        margin: "0 auto",
        padding: 24,
        display: "flex",
        flexDirection: "column",
        gap: 16,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
        <h1
          style={{
            fontSize: 22,
            fontWeight: 700,
            color: "var(--text-primary)",
            margin: 0,
          }}
        >
          Releases
        </h1>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          Recent deployments across all environments
        </span>
      </div>

      {error && (
        <div
          style={{
            padding: "10px 14px",
            background: "var(--danger-subtle)",
            color: "var(--danger)",
            border: "1px solid var(--danger)",
            borderRadius: "var(--radius)",
            fontSize: 13,
          }}
        >
          {error}
        </div>
      )}

      <div
        style={{
          background: "var(--bg-card)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          overflow: "hidden",
        }}
      >
        <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
          <thead>
            <tr
              style={{
                background: "var(--bg-input)",
                borderBottom: "1px solid var(--border)",
                textAlign: "left",
              }}
            >
              <Th>Deploy ID</Th>
              <Th>Request</Th>
              <Th>Commit</Th>
              <Th>Environment</Th>
              <Th>Status</Th>
              <Th>Started</Th>
              <Th>Completed</Th>
            </tr>
          </thead>
          <tbody>
            {releases.map((r) => (
              <tr
                key={r.deploy_id}
                style={{ borderBottom: "1px solid var(--border)" }}
              >
                <Td mono muted>
                  {r.deploy_id}
                </Td>
                <Td mono>
                  <span style={{ color: "var(--accent)" }}>{r.request_id}</span>
                </Td>
                <Td mono muted>
                  {r.commit_sha || "—"}
                </Td>
                <Td capitalize>{r.environment || "—"}</Td>
                <Td>
                  <StatusBadge status={r.status} />
                </Td>
                <Td muted>{formatTime(r.started_at)}</Td>
                <Td muted>{formatTime(r.completed_at)}</Td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && releases.length === 0 && !error && (
          <div
            style={{
              padding: 48,
              textAlign: "center",
              color: "var(--text-muted)",
              fontSize: 13,
            }}
          >
            No deployments yet
          </div>
        )}
        {loading && (
          <div
            style={{
              padding: 48,
              textAlign: "center",
              color: "var(--text-muted)",
              fontSize: 13,
            }}
          >
            Loading…
          </div>
        )}
      </div>
    </div>
  )
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th
      style={{
        padding: "10px 14px",
        fontSize: 11,
        fontWeight: 600,
        color: "var(--text-muted)",
        textTransform: "uppercase",
        letterSpacing: 0.5,
      }}
    >
      {children}
    </th>
  )
}

function Td({
  children,
  mono = false,
  muted = false,
  capitalize = false,
}: {
  children: React.ReactNode
  mono?: boolean
  muted?: boolean
  capitalize?: boolean
}) {
  return (
    <td
      style={{
        padding: "10px 14px",
        fontFamily: mono ? "var(--font-mono)" : "var(--font)",
        color: muted ? "var(--text-muted)" : "var(--text-primary)",
        textTransform: capitalize ? "capitalize" : "none",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </td>
  )
}
