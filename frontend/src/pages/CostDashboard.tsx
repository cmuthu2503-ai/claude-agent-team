import { useState, useEffect } from "react"
import { api } from "../lib/api"

export function CostDashboardPage() {
  const [data, setData] = useState<any>(null)

  useEffect(() => {
    api.get("/cost/dashboard").then((res) => setData(res.data)).catch(() => {})
  }, [])

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <h1 className="text-xl font-bold text-gray-900">Cost Dashboard</h1>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <p className="text-sm text-gray-500">Today's Spend</p>
          <p className="mt-1 text-3xl font-bold text-gray-900">
            ${data?.today?.total_cost_usd?.toFixed(2) ?? "0.00"}
          </p>
        </div>
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <p className="text-sm text-gray-500">This Month</p>
          <p className="mt-1 text-3xl font-bold text-gray-900">
            ${data?.this_month?.total_cost_usd?.toFixed(2) ?? "0.00"}
          </p>
        </div>
      </div>
    </div>
  )
}
