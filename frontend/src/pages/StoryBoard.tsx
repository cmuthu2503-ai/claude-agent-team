import { useState, useEffect } from "react"
import { useParams, Link } from "react-router-dom"
import { api } from "../lib/api"
import { StatusBadge } from "../components/ui/StatusBadge"
import { CoverageBar } from "../components/ui/CoverageBar"
import { ArrowLeft } from "lucide-react"

const columns = ["todo", "in_progress", "review", "testing", "done"]
const columnLabels: Record<string, string> = {
  todo: "To Do",
  in_progress: "In Progress",
  review: "Review",
  testing: "Testing",
  done: "Done",
}

export function StoryBoardPage() {
  const { requestId } = useParams()
  const [stories, setStories] = useState<any[]>([])

  useEffect(() => {
    if (requestId) {
      api.get(`/requests/${requestId}/stories`).then((res) => setStories(res.data)).catch(() => {})
    }
  }, [requestId])

  return (
    <div className="mx-auto max-w-7xl space-y-4 p-6">
      <Link to={`/request/${requestId}`} className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700">
        <ArrowLeft size={14} /> Back to {requestId}
      </Link>
      <h1 className="text-xl font-bold text-gray-900">Story Board — {requestId}</h1>

      {/* Pipeline header */}
      <div className="flex gap-2 rounded-lg border border-gray-200 bg-white px-4 py-3">
        {columns.map((col) => {
          const count = stories.filter((s) => s.status === col).length
          return (
            <div key={col} className="flex items-center gap-1.5 text-sm">
              <StatusBadge status={col} />
              <span className="font-mono text-xs text-gray-400">{count}</span>
            </div>
          )
        })}
        <div className="ml-auto text-xs text-gray-400">
          {stories.length} stories
        </div>
      </div>

      {/* Kanban columns */}
      <div className="grid grid-cols-5 gap-4">
        {columns.map((col) => (
          <div key={col} className="space-y-2">
            <h3 className="text-sm font-semibold text-gray-700">{columnLabels[col]}</h3>
            <div className="min-h-[200px] space-y-2 rounded-lg bg-gray-50 p-2">
              {stories
                .filter((s) => s.status === col)
                .map((s) => (
                  <div key={s.story_id} className="rounded-md border border-gray-200 bg-white p-3">
                    <div className="flex items-start justify-between">
                      <span className="text-[10px] font-mono text-gray-400">{s.story_id}</span>
                      {s.assigned_agent && (
                        <span className="rounded bg-purple-50 px-1.5 py-0.5 text-[10px] text-purple-600">
                          {s.assigned_agent.replace(/_/g, " ")}
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-xs font-medium text-gray-800">{s.title}</p>
                    {s.coverage_pct !== null && (
                      <div className="mt-2">
                        <CoverageBar value={s.coverage_pct} />
                      </div>
                    )}
                    {s.github_issue_number && (
                      <span className="mt-1 inline-block rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500">
                        #{s.github_issue_number}
                      </span>
                    )}
                  </div>
                ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
