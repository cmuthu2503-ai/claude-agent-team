import { useState, useEffect } from "react"
import { api } from "../lib/api"
import { StatusBadge } from "../components/ui/StatusBadge"

export function ReleasesPage() {
  const [releases, setReleases] = useState<any[]>([])

  useEffect(() => {
    api.get("/releases").then((res) => setReleases(res.data)).catch(() => {})
  }, [])

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-6">
      <h1 className="text-xl font-bold text-gray-900">Releases</h1>
      <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
        <table className="w-full text-sm">
          <thead className="border-b bg-gray-50 text-left text-xs font-medium text-gray-500">
            <tr>
              <th className="px-4 py-3">Deploy ID</th>
              <th className="px-4 py-3">Request</th>
              <th className="px-4 py-3">Environment</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Deployed</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {releases.map((d) => (
              <tr key={d.deploy_id} className="hover:bg-gray-50">
                <td className="px-4 py-3 font-mono text-xs text-gray-400">{d.deploy_id}</td>
                <td className="px-4 py-3 font-mono text-xs text-blue-600">{d.request_id}</td>
                <td className="px-4 py-3 capitalize">{d.environment}</td>
                <td className="px-4 py-3"><StatusBadge status={d.status} /></td>
                <td className="px-4 py-3 text-gray-400">{d.deployed_at ? new Date(d.deployed_at).toLocaleString() : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {releases.length === 0 && (
          <div className="py-12 text-center text-gray-400">No deployments yet</div>
        )}
      </div>
    </div>
  )
}
