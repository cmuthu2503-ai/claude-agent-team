/**
 * Project detail page (PM-33/34/35) at /projects/:projectId.
 *
 *   - Header: color/icon/name, description, lead, tags, repo link, target date, status
 *   - 4 stat cards (total / active / completed / cost USD)
 *   - "Next Steps" panel (PRJ-017 / PM-34) — only when project.template_id is set;
 *     renders the template's starter_checklist with click-to-pre-fill behavior
 *   - "Submit Request →" button (PM-35) that lands on Command Center
 *     with the project pre-selected via query param
 *   - Request list (every request in the project)
 *   - Recent Documents panel (latest 10 docs across requests)
 */

import { useEffect, useMemo, useState } from "react"
import { Link, useParams } from "react-router-dom"
import {
  Folder, Rocket, Layers, Code, FlaskConical, Palette, Bug, BookOpen,
  ArrowLeft, ExternalLink, FileText, ListChecks, CheckCircle2, Circle, Github,
  Pencil,
} from "lucide-react"
import { api } from "../lib/api"
import { StatusBadge } from "../components/ui/StatusBadge"
import { EditProjectModal } from "../components/projects/EditProjectModal"
import { BuildWorkspace } from "../components/projects/BuildWorkspace"

const ICON_MAP: Record<string, React.ComponentType<{ size?: number; color?: string }>> = {
  folder: Folder, rocket: Rocket, layers: Layers, code: Code,
  "flask-conical": FlaskConical, palette: Palette, bug: Bug, "book-open": BookOpen,
}

interface ProjectDetail {
  project_id: string
  name: string
  description: string
  status: string
  color: string
  icon: string
  tags: string[]
  lead_user_id: string | null
  repo_url: string
  default_team: string | null
  target_date: string | null
  template_id: string | null
  created_by: string | null
  created_at: string
  updated_at: string | null
  stats: { total: number; active: number; completed: number; failed: number }
  requests: Array<{
    request_id: string
    description: string
    task_type: string
    priority: string
    status: string
    created_at: string
    completed_at: string | null
  }>
  recent_documents: Array<{
    document_id: string
    request_id: string
    doc_type: string
    title: string
    agent_id: string
    version: number
    created_at: string
  }>
  template: {
    id: string
    name: string
    description: string
    starter_checklist: Array<{ description: string; task_type: string; priority: string }>
  } | null
}

export function ProjectDetailPage() {
  const { projectId } = useParams()
  const [data, setData] = useState<ProjectDetail | null>(null)
  const [error, setError] = useState("")
  const [editOpen, setEditOpen] = useState(false)

  const load = async () => {
    if (!projectId) return
    try {
      const res = await api.get(`/projects/${projectId}`)
      setData(res.data)
    } catch (e: any) {
      setError(e?.message || "Failed to load project")
    }
  }

  useEffect(() => {
    load()
    const id = window.setInterval(load, 5000)
    return () => window.clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  // Map checklist items → "filed" status by description-prefix match against
  // this project's requests. Approximate (no canonical template-instance
  // tracking yet) but good enough to dim items the user has actioned.
  const checklistMatches = useMemo(() => {
    if (!data?.template?.starter_checklist) return new Map<number, string>()
    const out = new Map<number, string>()
    data.template.starter_checklist.forEach((item, idx) => {
      // Strip the [placeholder] markers from the template description so we
      // can prefix-match the user's filled-in version.
      const prefix = item.description.split("[")[0].trim().toLowerCase()
      if (prefix.length < 5) return
      const match = data.requests.find((r) => r.description.toLowerCase().startsWith(prefix))
      if (match) out.set(idx, match.request_id)
    })
    return out
  }, [data])

  if (error) return <div style={{ padding: 24, color: "var(--danger)" }}>{error}</div>
  if (!data) return <div style={{ padding: 24, color: "var(--text-muted)" }}>Loading…</div>

  const Icon = ICON_MAP[data.icon] || Folder
  const isGithub = data.repo_url && /github\.com\//.test(data.repo_url)
  const targetDt = data.target_date ? new Date(data.target_date) : null
  const overdue = targetDt && targetDt < new Date()

  return (
    <div style={{ maxWidth: 1800, margin: "0 auto", padding: "24px 36px", display: "flex", flexDirection: "column", gap: 20 }}>
      <Link to="/projects" style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 13, color: "var(--text-muted)", textDecoration: "none" }}>
        <ArrowLeft size={14} /> Back to Projects
      </Link>

      {/* ── Header ── */}
      <div style={{
        position: "relative", overflow: "hidden",
        background: "var(--bg-card)", border: "1px solid var(--border)",
        borderRadius: "var(--radius)", padding: "20px 24px 20px 30px",
      }}>
        <div style={{ position: "absolute", top: 0, bottom: 0, left: 0, width: 4, background: data.color }} />
        <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
          <div style={{
            flexShrink: 0, width: 48, height: 48, borderRadius: 8,
            background: "var(--bg-hover)",
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            color: data.color, border: `1px solid ${data.color}`,
          }}>
            <Icon size={24} />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
              <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: "var(--text-primary)" }}>{data.name}</h2>
              <span style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>{data.project_id}</span>
              {data.status === "archived" && (
                <span style={{
                  padding: "2px 8px", borderRadius: 4, fontSize: 10, fontWeight: 700,
                  textTransform: "uppercase", letterSpacing: 1,
                  background: "var(--bg-hover)", color: "var(--text-muted)",
                  border: "1px solid var(--border)",
                }}>archived</span>
              )}
            </div>
            {data.description && (
              <p style={{ marginTop: 8, marginBottom: 0, fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.5 }}>
                {data.description}
              </p>
            )}
            <div style={{
              marginTop: 12, display: "flex", gap: 16, fontSize: 12,
              color: "var(--text-muted)", flexWrap: "wrap",
            }}>
              {data.created_by && <span>Lead: {data.created_by}</span>}
              {data.default_team && <span>Default team: {data.default_team}</span>}
              {targetDt && (
                <span style={{ color: overdue ? "var(--danger)" : undefined }}>
                  Target: {targetDt.toLocaleDateString()} {overdue && "(Overdue)"}
                </span>
              )}
              <span>Created: {new Date(data.created_at).toLocaleDateString()}</span>
              {data.repo_url && (
                <a href={data.repo_url} target="_blank" rel="noopener noreferrer"
                   style={{ display: "inline-flex", alignItems: "center", gap: 4, color: "var(--accent)", textDecoration: "none" }}>
                  {isGithub ? <Github size={12} /> : <ExternalLink size={12} />}
                  {isGithub ? "View on GitHub" : "Repo"}
                  <ExternalLink size={10} />
                </a>
              )}
            </div>
            {data.tags.length > 0 && (
              <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 4 }}>
                {data.tags.map((t) => (
                  <span key={t} style={{
                    padding: "2px 8px", borderRadius: 4, fontSize: 10,
                    background: "var(--bg-hover)", color: "var(--text-secondary)",
                    fontFamily: "var(--font-mono)",
                  }}>{t}</span>
                ))}
              </div>
            )}
          </div>
          <div style={{ display: "flex", gap: 8, flexShrink: 0, alignItems: "center" }}>
            {data.project_id !== "proj-unassigned" && (
              <button
                type="button"
                onClick={() => setEditOpen(true)}
                title="Edit project details"
                style={{
                  display: "inline-flex", alignItems: "center", gap: 6,
                  padding: "8px 12px", fontSize: 12, fontWeight: 600,
                  background: "transparent", color: "var(--text-secondary)",
                  border: "1px solid var(--border)", borderRadius: "var(--radius)",
                  cursor: "pointer", whiteSpace: "nowrap", lineHeight: 1,
                  fontFamily: "var(--font)",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = "var(--accent)"
                  e.currentTarget.style.color = "var(--accent)"
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = "var(--border)"
                  e.currentTarget.style.color = "var(--text-secondary)"
                }}
              >
                <Pencil size={12} />
                <span>Edit</span>
              </button>
            )}
            <Link
              to={`/?project_id=${data.project_id}`}
              style={{
                display: "inline-flex", alignItems: "center",
                padding: "8px 14px", fontSize: 12, fontWeight: 700,
                background: "var(--accent)", color: "#0a0014",
                border: "1px solid var(--accent)", borderRadius: "var(--radius)",
                textDecoration: "none",
                whiteSpace: "nowrap", lineHeight: 1,
              }}
            >
              Submit Request →
            </Link>
          </div>
        </div>
      </div>

      {/* ── Stat cards ── */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 12 }}>
        <StatCard label="Total requests" value={String(data.stats.total)} />
        <StatCard label="Active" value={String(data.stats.active)} color="var(--accent)" />
        <StatCard label="Completed" value={String(data.stats.completed)} color="var(--success)" />
        <StatCard label="Failed" value={String(data.stats.failed)} color={data.stats.failed > 0 ? "var(--danger)" : "var(--text-muted)"} />
      </div>

      {/* ── Build Workspace (PDB-09 → PDB-12) — Brief → PRD ── */}
      {data.project_id !== "proj-unassigned" && (
        <BuildWorkspace projectId={data.project_id} />
      )}

      {/* ── Next Steps (PM-34) — only when template was selected ── */}
      {data.template && data.template.starter_checklist.length > 0 && (
        <div style={{
          background: "var(--bg-card)", border: "1px solid var(--border)",
          borderRadius: "var(--radius)", padding: 18,
        }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 8,
            fontSize: 13, fontWeight: 600, color: "var(--text-primary)",
            marginBottom: 12,
          }}>
            <ListChecks size={16} color={data.color} />
            Next Steps · template <code style={{ fontSize: 11, color: "var(--text-muted)" }}>{data.template.name}</code>
          </div>
          <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
            {data.template.starter_checklist.map((item, idx) => {
              const matchedReqId = checklistMatches.get(idx)
              const done = !!matchedReqId
              return (
                <li key={idx} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  {done
                    ? <CheckCircle2 size={14} color="var(--success)" />
                    : <Circle size={14} color="var(--text-muted)" />}
                  <Link
                    to={done
                      ? `/request/${matchedReqId}`
                      : `/?project_id=${data.project_id}&prefill=${encodeURIComponent(item.description)}&task_type=${item.task_type}&priority=${item.priority}`}
                    style={{
                      fontSize: 13,
                      color: done ? "var(--text-muted)" : "var(--text-primary)",
                      textDecoration: done ? "line-through" : "none",
                      flex: 1, minWidth: 0,
                    }}
                  >
                    {item.description}
                  </Link>
                  <span style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: 0.5 }}>
                    {item.task_type.replace("_", " ")} · {item.priority}
                  </span>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {/* ── Requests in this project ── */}
      <div style={{
        background: "var(--bg-card)", border: "1px solid var(--border)",
        borderRadius: "var(--radius)", padding: 18,
      }}>
        <h3 style={{ margin: "0 0 12px 0", fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
          Requests in this project ({data.requests.length})
        </h3>
        {data.requests.length === 0 ? (
          <div style={{ color: "var(--text-muted)", fontSize: 13 }}>None yet.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {data.requests.map((r) => (
              <Link
                key={r.request_id}
                to={`/request/${r.request_id}`}
                style={{
                  display: "flex", alignItems: "center", gap: 12,
                  padding: "8px 12px", borderRadius: "var(--radius)",
                  background: "var(--bg-hover)", border: "1px solid var(--border)",
                  textDecoration: "none", color: "inherit", fontSize: 13,
                }}
              >
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-muted)", flexShrink: 0 }}>
                  {r.request_id}
                </span>
                <span style={{
                  flex: 1, color: "var(--text-primary)",
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>
                  {r.description}
                </span>
                <span style={{ fontSize: 11, color: "var(--text-muted)", flexShrink: 0 }}>
                  {new Date(r.created_at).toLocaleDateString()}
                </span>
                <StatusBadge status={r.status} />
              </Link>
            ))}
          </div>
        )}
      </div>

      <EditProjectModal
        open={editOpen}
        initial={{
          project_id: data.project_id,
          name: data.name,
          description: data.description,
          color: data.color,
          icon: data.icon,
          tags: data.tags,
          lead_user_id: data.lead_user_id,
          repo_url: data.repo_url,
          default_team: data.default_team,
          target_date: data.target_date,
        }}
        onClose={() => setEditOpen(false)}
        onSaved={load}
      />

      {/* ── Recent documents ── */}
      {data.recent_documents.length > 0 && (
        <div style={{
          background: "var(--bg-card)", border: "1px solid var(--border)",
          borderRadius: "var(--radius)", padding: 18,
        }}>
          <h3 style={{ margin: "0 0 12px 0", fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
            Recent documents ({data.recent_documents.length})
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {data.recent_documents.map((d) => (
              <Link key={d.document_id} to={`/request/${d.request_id}`}
                style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "6px 10px", borderRadius: "var(--radius)",
                  textDecoration: "none", color: "inherit", fontSize: 12,
                }}
              >
                <FileText size={13} color="var(--accent)" />
                <span style={{ color: "var(--text-primary)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {d.title}
                </span>
                <span style={{ fontSize: 10, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                  v{d.version} · {d.doc_type}
                </span>
                <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                  {new Date(d.created_at).toLocaleDateString()}
                </span>
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function StatCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{
      background: "var(--bg-card)", border: "1px solid var(--border)",
      borderRadius: "var(--radius)", padding: "14px 16px",
      display: "flex", flexDirection: "column", gap: 4,
    }}>
      <div style={{ fontSize: 10, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: 1 }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 700, color: color || "var(--text-primary)" }}>
        {value}
      </div>
    </div>
  )
}
