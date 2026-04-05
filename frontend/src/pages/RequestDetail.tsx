import { useState, useEffect } from "react"
import { useParams, Link } from "react-router-dom"
import { api } from "../lib/api"
import { StatusBadge } from "../components/ui/StatusBadge"
import { ArrowLeft } from "lucide-react"

export function RequestDetailPage() {
  const { requestId } = useParams()
  const [data, setData] = useState<any>(null)

  useEffect(() => {
    if (requestId) {
      api.get(`/requests/${requestId}`).then((res) => setData(res.data)).catch(() => {})
    }
  }, [requestId])

  if (!data) return <div className="p-6 text-gray-400">Loading...</div>

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <Link to="/" className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700">
        <ArrowLeft size={14} /> Back
      </Link>
      <div className="rounded-lg border border-gray-200 bg-white p-6">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-xl font-bold text-gray-900">{data.request_id}</h1>
            <p className="mt-1 text-gray-600">{data.description}</p>
          </div>
          <StatusBadge status={data.status} size="md" />
        </div>
        <div className="mt-4 flex gap-4 text-sm text-gray-500">
          <span>Type: <span className="capitalize">{data.task_type?.replace("_", " ")}</span></span>
          <span>Priority: <span className="capitalize">{data.priority}</span></span>
          <span>Created: {new Date(data.created_at).toLocaleString()}</span>
          {data.total_cost && <span>Cost: ${data.total_cost.cost_usd}</span>}
        </div>
      </div>

      {/* Subtasks */}
      {data.subtasks?.length > 0 && (
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <h2 className="mb-4 text-lg font-semibold text-gray-900">Agent Timeline</h2>
          <div className="space-y-3">
            {data.subtasks.map((s: any) => (
              <div key={s.subtask_id} className="flex items-center justify-between rounded-md border border-gray-100 px-4 py-3">
                <div className="flex items-center gap-3">
                  <StatusBadge status={s.status} />
                  <span className="text-sm font-medium text-gray-700">{s.agent_id.replace(/_/g, " ")}</span>
                </div>
                <span className="text-xs text-gray-400">
                  {s.started_at ? new Date(s.started_at).toLocaleTimeString() : "—"}
                  {s.completed_at && ` → ${new Date(s.completed_at).toLocaleTimeString()}`}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Stories */}
      {data.stories?.length > 0 && (
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <h2 className="mb-4 text-lg font-semibold text-gray-900">Stories</h2>
          <div className="space-y-2">
            {data.stories.map((st: any) => (
              <div key={st.story_id} className="flex items-center justify-between rounded-md border border-gray-100 px-4 py-3">
                <div>
                  <span className="text-xs font-mono text-gray-400">{st.story_id}</span>
                  <span className="ml-2 text-sm text-gray-700">{st.title}</span>
                </div>
                <StatusBadge status={st.status} />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
