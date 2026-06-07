/**
 * PAM-17 — Models / agents store.
 *
 * Single Zustand store backing the Team Status overrides panel
 * (PAM-18..21). Holds both the runtime model catalog (from
 * `GET /api/v1/models`) and the per-agent override surface (from
 * `GET /api/v1/agents`) so the panel renders without fanning out
 * additional requests when the user opens a dropdown.
 *
 * Optimistic update pattern
 * -------------------------
 * `assignModel`, `clearOverride`, and `resetAll` apply the change
 * to the local store IMMEDIATELY so the UI feels instant, then fire
 * the HTTP call in the background. On failure the prior state is
 * RESTORED from a snapshot and an `error` string is surfaced for
 * the toast/banner to render.
 *
 * Why optimistic over a TanStack mutation: the override surface is
 * small (15 agents), users expect "click → see green badge", and the
 * round-trip in dev is ~50ms but in mock-LLM mode the PATCH happens
 * to be one of the few mutating routes — the snapshot/revert pattern
 * keeps the UI from blinking through a stale state.
 *
 * Error surface
 * -------------
 * `error` carries the last user-visible failure message; consumers
 * (`<ErrorBanner>` / `useToaster`) read it once and call
 * `clearError()` after acknowledgement. The store doesn't auto-clear
 * because the same user might re-click the same dropdown entry — we
 * want them to see the same error again, not silently re-retry.
 */

import { create } from "zustand"
import { api } from "../lib/api"

// ── Wire types (mirror backend response shape) ────────────────────────

export interface Model {
  id: string
  provider_type: string
  model_id: string
  display_name: string
  tier: "frontier" | "workhorse" | "fast" | "local" | string
  tool_calling_mode: "native" | "prompted" | string
  base_url: string | null
  pricing_per_million: {
    input: number
    output: number
  }
}

export interface Agent {
  agent_id: string
  display_name: string
  role: string
  team: string
  // Legacy `model` field — mirrors `assigned_model`. Kept for
  // backward compat with older components; new code reads
  // assigned_model / default_model / override_active directly.
  model: string
  default_model: string
  assigned_model: string
  override_active: boolean
  tool_count: number
  status: "idle" | "in_progress" | string
  current_task: string | null
  current_label: string | null
  elapsed_seconds: number | null
  total_subtasks: number
}

interface ModelsState {
  models: Model[]
  defaultModel: string | null
  agents: Agent[]
  loading: boolean
  error: string | null

  fetchModels: () => Promise<void>
  fetchAgents: () => Promise<void>
  assignModel: (agentId: string, modelId: string) => Promise<void>
  clearOverride: (agentId: string) => Promise<void>
  resetAll: () => Promise<void>
  clearError: () => void
}

// ── Helpers ───────────────────────────────────────────────────────────

function findModel(models: Model[], modelId: string): Model | undefined {
  return models.find((m) => m.id === modelId)
}

// Build the patched agent row used by optimistic updates. Pulled out
// so assignModel / clearOverride / resetAll share the same update
// logic — keeps the legacy `model` field in sync with
// `assigned_model`, which several existing components still read.
function patchAgentRow(
  row: Agent,
  nextAssigned: string,
  overrideActive: boolean,
): Agent {
  return {
    ...row,
    assigned_model: nextAssigned,
    model: nextAssigned,
    override_active: overrideActive,
  }
}

/**
 * The api wrapper throws plain `Error("<status>: <body>")` strings.
 * Try to parse the JSON body so we surface the backend's `detail`
 * field (e.g. "model_id 'xxx' is not in the catalog") rather than the
 * raw `{detail: ...}` JSON to the user.
 */
function errMessage(e: unknown, fallback: string): string {
  if (e instanceof Error) {
    const m = e.message.match(/^\d{3}:\s*(.*)$/s)
    if (m) {
      try {
        const parsed = JSON.parse(m[1])
        if (parsed && typeof parsed.detail === "string") return parsed.detail
      } catch {
        // body wasn't JSON — fall through to raw text
      }
      return m[1].trim() || fallback
    }
    return e.message || fallback
  }
  return fallback
}

/** Extract the HTTP status code from an api lib error, or null. */
function errStatus(e: unknown): number | null {
  if (e instanceof Error) {
    const m = e.message.match(/^(\d{3}):/)
    if (m) return Number(m[1])
  }
  return null
}

// ── Store ─────────────────────────────────────────────────────────────

export const useModelsStore = create<ModelsState>((set, get) => ({
  models: [],
  defaultModel: null,
  agents: [],
  loading: false,
  error: null,

  fetchModels: async () => {
    set({ loading: true, error: null })
    try {
      const res = await api.get<{
        data: { default_model: string; models: Model[] }
      }>("/models")
      set({
        models: res.data.models,
        defaultModel: res.data.default_model,
        loading: false,
      })
    } catch (e) {
      set({
        loading: false,
        error: errMessage(e, "Failed to load model catalog"),
      })
    }
  },

  fetchAgents: async () => {
    set({ loading: true, error: null })
    try {
      const res = await api.get<{ data: Agent[] }>("/agents")
      set({ agents: res.data, loading: false })
    } catch (e) {
      set({
        loading: false,
        error: errMessage(e, "Failed to load agents"),
      })
    }
  },

  /**
   * Assign `modelId` to `agentId` — optimistic.
   *
   * 1. Snapshot the affected row (so a failure can restore it).
   * 2. Apply the update locally — UI shows the new model immediately.
   * 3. PATCH the API. On 4xx/5xx, restore the snapshot AND surface
   *    the error message; on success, sync the row from the response
   *    so any backend-side canonicalisation (legacy alias → catalog
   *    id) is reflected in the UI.
   *
   * Pre-flight check: refuse to PATCH if `modelId` isn't in the local
   * catalog. Saves a round trip on operator typos, and the resolver's
   * 422 message would be less specific than what we show here.
   */
  assignModel: async (agentId, modelId) => {
    const { agents, models } = get()
    const snapshot = agents.find((a) => a.agent_id === agentId)
    if (!snapshot) {
      set({ error: `Unknown agent: ${agentId}` })
      return
    }
    if (!findModel(models, modelId)) {
      // Legacy provider strings (anthropic_aws_sonnet etc.) might
      // still be valid backend-side via the catalog's alias map, but
      // they're not in our local `models[]` so the dropdown can't
      // surface them. Refuse here so the user picks a catalog id.
      set({
        error: (
          `Model "${modelId}" is not in the loaded catalog. ` +
          `Try reloading the page or POST /models/reload.`
        ),
      })
      return
    }

    // Optimistic update.
    set({
      agents: agents.map((a) =>
        a.agent_id === agentId ? patchAgentRow(a, modelId, true) : a,
      ),
      error: null,
    })

    try {
      const res = await api.patch<{
        data: { agent_id: string; model_id: string; override_active: boolean }
      }>(`/agents/${encodeURIComponent(agentId)}/model`, { model_id: modelId })
      // Sync from response — covers legacy-alias canonicalisation.
      const canonical = res.data.model_id
      set({
        agents: get().agents.map((a) =>
          a.agent_id === agentId
            ? patchAgentRow(a, canonical, res.data.override_active)
            : a,
        ),
      })
    } catch (e) {
      // Revert to snapshot on failure.
      set({
        agents: get().agents.map((a) =>
          a.agent_id === agentId ? snapshot : a,
        ),
        error: errMessage(e, `Failed to assign ${modelId} to ${agentId}`),
      })
    }
  },

  /**
   * Clear the override on `agentId` — optimistic.
   *
   * Reverts the row to its `default_model` immediately, then
   * `DELETE`s. On 404 (no override existed) we DON'T treat it as a
   * failure — the desired end state (no override) was already true.
   * On 5xx we restore the snapshot and surface the error.
   */
  clearOverride: async (agentId) => {
    const { agents } = get()
    const snapshot = agents.find((a) => a.agent_id === agentId)
    if (!snapshot) {
      set({ error: `Unknown agent: ${agentId}` })
      return
    }
    // Optimistic: snap back to YAML default.
    set({
      agents: agents.map((a) =>
        a.agent_id === agentId
          ? patchAgentRow(a, a.default_model, false)
          : a,
      ),
      error: null,
    })

    try {
      await api.delete(`/agents/${encodeURIComponent(agentId)}/model`)
    } catch (e) {
      if (errStatus(e) === 404) {
        // Already cleared — the optimistic state IS the truth. Don't
        // revert, don't error.
        return
      }
      set({
        agents: get().agents.map((a) =>
          a.agent_id === agentId ? snapshot : a,
        ),
        error: errMessage(e, `Failed to clear override on ${agentId}`),
      })
    }
  },

  /**
   * Bulk reset: clear every override in one call. Snapshots ALL
   * currently-overridden agents so the UI can be restored verbatim
   * on failure.
   */
  resetAll: async () => {
    const { agents } = get()
    const snapshots = agents
      .filter((a) => a.override_active)
      .map((a) => ({ ...a }))
    if (snapshots.length === 0) {
      // Nothing to do — DON'T fire a no-op request.
      return
    }
    // Optimistic: snap every overridden agent back to default.
    set({
      agents: agents.map((a) =>
        a.override_active ? patchAgentRow(a, a.default_model, false) : a,
      ),
      error: null,
    })

    try {
      await api.delete("/agents/model-overrides")
    } catch (e) {
      // Restore every snapshot we took.
      const byId = new Map(snapshots.map((s) => [s.agent_id, s]))
      set({
        agents: get().agents.map((a) =>
          byId.has(a.agent_id) ? byId.get(a.agent_id)! : a,
        ),
        error: errMessage(e, "Failed to reset overrides"),
      })
    }
  },

  clearError: () => set({ error: null }),
}))
