/**
 * BPD-36 — pure depends_on validator extracted from TaskListEditor.
 *
 * Lives outside the component file so a vitest can import it without
 * dragging in React + the entire editor. The exact same function the
 * editor's useMemo calls; renaming or changing the shape here changes
 * both call-sites in lockstep (L23 single source of truth).
 *
 * Issue kinds:
 *
 *   self_reference  — task lists its own task_id in depends_on
 *                     SEVERITY: error
 *
 *   dangling        — depends_on entry references a task_id that
 *                     doesn't exist in the project
 *                     SEVERITY: error
 *
 *   forward_ref     — depends_on entry references a task with an
 *                     ordinal ≥ the current task's. Forward refs are
 *                     legal at the data layer but the dispatcher walks
 *                     ordinal order, so a forward dep will never block
 *                     dispatch as the operator expects.
 *                     SEVERITY: warn
 *
 *   cycle           — task participates in a circular depends_on
 *                     chain (caught by DFS color marking)
 *                     SEVERITY: error
 *
 * Returns Map<task_id, DepIssue[]>. Tasks with no issues are absent
 * from the map; callers check via `.get(id) || []`.
 */

export type DepIssueKind = "self_reference" | "dangling" | "forward_ref" | "cycle"

export interface DepIssue {
  kind: DepIssueKind
  severity: "error" | "warn"
  bad_ref: string
  bad_ref_title?: string
  fix_hint: string
}

export interface DepValidatorTaskInput {
  task_id: string
  ordinal: number
  title: string
  depends_on?: string[] | null
}

export function validateDepends(
  tasks: DepValidatorTaskInput[],
): Map<string, DepIssue[]> {
  const out = new Map<string, DepIssue[]>()
  if (!tasks || tasks.length === 0) return out

  const taskById = new Map<string, DepValidatorTaskInput>()
  for (const t of tasks) taskById.set(t.task_id, t)

  // DFS color marking for cycle detection. WHITE=unvisited,
  // GRAY=on current DFS stack, BLACK=done. A back-edge to a GRAY
  // node means we found a cycle; mark every node on the gray stack
  // from the back-edge target through the current node as in-cycle.
  // Self-edges are excluded here because self_reference handles
  // them with clearer messaging.
  const color = new Map<string, "W" | "G" | "B">()
  const stack: string[] = []
  const inCycle = new Set<string>()
  for (const t of tasks) color.set(t.task_id, "W")

  const dfs = (id: string): void => {
    if (color.get(id) !== "W") return
    color.set(id, "G"); stack.push(id)
    const t = taskById.get(id)
    for (const dep of t?.depends_on || []) {
      if (dep === id) continue
      const c = color.get(dep)
      if (c === "G") {
        const start = stack.indexOf(dep)
        for (let i = start; i < stack.length; i++) inCycle.add(stack[i])
      } else if (c === "W") {
        dfs(dep)
      }
    }
    stack.pop(); color.set(id, "B")
  }
  for (const t of tasks) dfs(t.task_id)

  for (const t of tasks) {
    const issues: DepIssue[] = []
    const deps = t.depends_on || []
    for (const dep of deps) {
      if (dep === t.task_id) {
        issues.push({
          kind: "self_reference",
          severity: "error",
          bad_ref: dep,
          fix_hint: "Remove the self-reference from this task's depends_on.",
        })
        continue
      }
      const target = taskById.get(dep)
      if (!target) {
        issues.push({
          kind: "dangling",
          severity: "error",
          bad_ref: dep,
          fix_hint: `Remove '${dep}' from depends_on, or restore the missing task.`,
        })
        continue
      }
      if (target.ordinal >= t.ordinal) {
        issues.push({
          kind: "forward_ref",
          severity: "warn",
          bad_ref: dep,
          bad_ref_title: target.title,
          fix_hint:
            `Reorder so this task runs AFTER ${dep} (ordinal ${target.ordinal}), ` +
            `or drop the dependency. Forward references can't be enforced — ` +
            `the dispatcher walks ordinal order.`,
        })
      }
    }
    if (inCycle.has(t.task_id)) {
      const peer = deps.find((d) => inCycle.has(d)) || "(see cycle members)"
      issues.push({
        kind: "cycle",
        severity: "error",
        bad_ref: peer,
        bad_ref_title: taskById.get(peer)?.title,
        fix_hint:
          `This task is part of a circular dependency chain (via ${peer}). ` +
          `Break the cycle by removing one depends_on entry along the loop.`,
      })
    }
    if (issues.length > 0) out.set(t.task_id, issues)
  }
  return out
}
