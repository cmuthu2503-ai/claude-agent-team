/**
 * Shared types for the enriched task / request card + drill-in
 * components used by ProjectStoryBoard and Command Center.
 *
 * Both surfaces present the same conceptual unit — a Task that may
 * have a linked Request walking through the workflow — but their
 * data sources differ:
 *
 *   - ProjectStoryBoard reads `GET /projects/:id/tasks` (each row
 *     has a request_id when dispatched)
 *   - Command Center reads `GET /requests?per_page=N` (each row IS
 *     a request — task_id may or may not be present)
 *
 * The card component accepts the unified shape below; each page
 * builds it from its own raw data.
 */

export type TaskStatus =
  | "backlog"
  | "dispatched"
  | "in_progress"
  | "review"
  | "testing"
  | "deployed"
  | "failed"
  | "cancelled"
  | "completed"      // command-center one-off shape uses this
  | "pending"        // ditto

export type WorkflowStage =
  | "prd"
  | "stories"
  | "development"
  | "review"
  | "testing"
  | "code_commit"
  | "deploy"

export interface CardData {
  /** Stable opaque identifier — task_id or request_id depending on source. */
  id: string
  /** Optional task_id when we're rendering a project task. */
  task_id?: string | null
  /** Optional request_id when this card represents a dispatched task. */
  request_id?: string | null
  /** Phase prefix like "Phase 1: Foundation" — null when there's no phase. */
  phase?: string | null
  /** Card title (task title or request description). */
  title: string
  /** Free-text body shown in the drill-in. */
  description?: string | null
  /** Domain tag — "backend", "frontend", "devops", etc. Optional badge. */
  type?: string | null
  /** Suggested agent. */
  agent?: string | null
  /** "high" / "medium" / "low". */
  priority: "high" | "medium" | "low"
  /** Lifecycle state. */
  status: TaskStatus
  /** Current workflow stage when in flight; null otherwise. */
  current_stage?: WorkflowStage | null
  /** Rework cycle counter. */
  cycle?: number | null
  max_cycles?: number | null
  /** Elapsed seconds since first dispatch. */
  elapsed_seconds?: number | null
  /** Cumulative cost USD. */
  cost_usd?: number | null
  /** Commit SHA (deployed). */
  commit_sha?: string | null
  /** Files committed count (deployed). */
  files_count?: number | null
  /** One-line error summary (failed). */
  error_summary?: string | null
  /** Created timestamp ISO. */
  created_at?: string | null
  /** BPD §6.8 fields. Populated by surfaces that have epic/feature
   * context available (BuildPlanView). When undefined, the drill-in's
   * BPD section is hidden — preserves legacy callers without forcing
   * an extra fetch. */
  bpd?: {
    epic_id?: string | null
    epic_title?: string | null
    feature_id?: string | null
    feature_title?: string | null
    primary_file?: string | null
    expected_loc?: number | null
    acceptance_test?: string | null
    /** Pairs of {task_id, title, status} for each depends_on entry,
     * resolved by the caller. Status is the blocker's current
     * task_status; UI color-codes accordingly. */
    depends_on?: Array<{ task_id: string; title: string; status: string }>
  }
}
