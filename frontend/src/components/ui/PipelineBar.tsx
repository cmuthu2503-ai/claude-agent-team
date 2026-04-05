interface Stage {
  name: string
  status: "pending" | "active" | "completed" | "failed"
}

interface PipelineBarProps {
  stages: Stage[]
}

const stageColors = {
  pending: "bg-gray-200",
  active: "bg-blue-500 animate-pulse",
  completed: "bg-green-500",
  failed: "bg-red-500",
}

export function PipelineBar({ stages }: PipelineBarProps) {
  return (
    <div className="flex items-center gap-1">
      {stages.map((stage, i) => (
        <div key={stage.name} className="flex items-center gap-1">
          <div className="flex flex-col items-center gap-0.5">
            <div className={`h-2 w-8 rounded-full ${stageColors[stage.status]}`} title={stage.name} />
            <span className="text-[9px] text-gray-400">{stage.name}</span>
          </div>
          {i < stages.length - 1 && <div className="mb-3 h-px w-2 bg-gray-300" />}
        </div>
      ))}
    </div>
  )
}
