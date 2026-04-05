import { Check, X, Loader2, Circle } from "lucide-react"

interface TestCase {
  name: string
  status: "pass" | "fail" | "running" | "pending"
}

interface TestCaseListProps {
  tests: TestCase[]
}

const iconMap = {
  pass: <Check size={14} className="text-green-500" />,
  fail: <X size={14} className="text-red-500" />,
  running: <Loader2 size={14} className="animate-spin text-blue-500" />,
  pending: <Circle size={14} className="text-gray-300" />,
}

export function TestCaseList({ tests }: TestCaseListProps) {
  if (tests.length === 0) return <span className="text-xs text-gray-400">No tests</span>
  return (
    <ul className="space-y-1">
      {tests.map((t) => (
        <li key={t.name} className="flex items-center gap-1.5 text-xs text-gray-700">
          {iconMap[t.status]}
          {t.name}
        </li>
      ))}
    </ul>
  )
}
