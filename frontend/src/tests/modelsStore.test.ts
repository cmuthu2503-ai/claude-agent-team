/**
 * PAM-17 — useModelsStore optimistic update contract.
 *
 * Pinned behaviour:
 *   - fetchModels / fetchAgents populate state, clear `error`
 *   - assignModel applies optimistically, syncs from response on success,
 *     reverts to snapshot AND surfaces a parsed error on failure
 *   - assignModel pre-flight rejects models not in the local catalog
 *   - clearOverride reverts to default_model optimistically,
 *     tolerates 404 (no-op), reverts on 5xx
 *   - resetAll snapshots every overridden row and reverts them all on failure
 *   - error helper parses the api lib's "<status>: <body>" Error
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { api } from "../lib/api"
import { useModelsStore, type Agent, type Model } from "../stores/models"

// ── Fixtures ──────────────────────────────────────────────────────────

const MODEL_OPUS: Model = {
  id: "claude-opus-4-7",
  provider_type: "anthropic_aws",
  model_id: "claude-opus-4-7",
  display_name: "Claude Opus 4.7",
  tier: "frontier",
  tool_calling_mode: "native",
  base_url: null,
  pricing_per_million: { input: 15, output: 75 },
}
const MODEL_HAIKU: Model = {
  ...MODEL_OPUS,
  id: "claude-haiku-4-7",
  model_id: "claude-haiku-4-7",
  display_name: "Claude Haiku 4.7",
  tier: "fast",
  pricing_per_million: { input: 1, output: 5 },
}

function agent(over: Partial<Agent> = {}): Agent {
  return {
    agent_id: "backend_specialist",
    display_name: "Backend",
    role: "backend",
    team: "engineering",
    model: "claude-opus-4-7",
    default_model: "claude-opus-4-7",
    assigned_model: "claude-opus-4-7",
    override_active: false,
    tool_count: 5,
    status: "idle",
    current_task: null,
    current_label: null,
    elapsed_seconds: null,
    total_subtasks: 0,
    ...over,
  }
}

// Reset store between tests — Zustand stores persist by default.
beforeEach(() => {
  useModelsStore.setState({
    models: [MODEL_OPUS, MODEL_HAIKU],
    defaultModel: "claude-opus-4-7",
    agents: [agent()],
    loading: false,
    error: null,
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ── fetchModels / fetchAgents ─────────────────────────────────────────

describe("fetchModels", () => {
  it("populates models + defaultModel and clears error", async () => {
    useModelsStore.setState({ error: "prior" })
    vi.spyOn(api, "get").mockResolvedValueOnce({
      data: { default_model: "claude-opus-4-7", models: [MODEL_OPUS] },
    })
    await useModelsStore.getState().fetchModels()
    const s = useModelsStore.getState()
    expect(s.models).toEqual([MODEL_OPUS])
    expect(s.defaultModel).toBe("claude-opus-4-7")
    expect(s.error).toBeNull()
    expect(s.loading).toBe(false)
  })

  it("surfaces parsed error from api lib on failure", async () => {
    vi.spyOn(api, "get").mockRejectedValueOnce(
      new Error('503: {"detail":"catalog not loaded"}'),
    )
    await useModelsStore.getState().fetchModels()
    expect(useModelsStore.getState().error).toBe("catalog not loaded")
  })
})

describe("fetchAgents", () => {
  it("populates agents list", async () => {
    vi.spyOn(api, "get").mockResolvedValueOnce({ data: [agent()] })
    await useModelsStore.getState().fetchAgents()
    expect(useModelsStore.getState().agents).toHaveLength(1)
  })
})

// ── assignModel ───────────────────────────────────────────────────────

describe("assignModel", () => {
  it("applies optimistically + syncs from response on success", async () => {
    const patchSpy = vi.spyOn(api, "patch").mockResolvedValueOnce({
      data: {
        agent_id: "backend_specialist",
        model_id: "claude-haiku-4-7",
        override_active: true,
      },
    })
    await useModelsStore.getState().assignModel("backend_specialist", "claude-haiku-4-7")
    const row = useModelsStore.getState().agents[0]
    expect(row.assigned_model).toBe("claude-haiku-4-7")
    expect(row.model).toBe("claude-haiku-4-7")  // legacy field stays in sync
    expect(row.override_active).toBe(true)
    expect(patchSpy).toHaveBeenCalledWith(
      "/agents/backend_specialist/model",
      { model_id: "claude-haiku-4-7" },
    )
  })

  it("reverts to snapshot AND surfaces detail on PATCH failure", async () => {
    vi.spyOn(api, "patch").mockRejectedValueOnce(
      new Error('422: {"detail":"model_id is not in the catalog"}'),
    )
    await useModelsStore.getState().assignModel("backend_specialist", "claude-haiku-4-7")
    const s = useModelsStore.getState()
    expect(s.agents[0].assigned_model).toBe("claude-opus-4-7")
    expect(s.agents[0].override_active).toBe(false)
    expect(s.error).toBe("model_id is not in the catalog")
  })

  it("rejects unknown model_id without firing PATCH", async () => {
    const patchSpy = vi.spyOn(api, "patch")
    await useModelsStore.getState().assignModel("backend_specialist", "ghost-model")
    expect(patchSpy).not.toHaveBeenCalled()
    expect(useModelsStore.getState().error).toMatch(/not in the loaded catalog/)
  })

  it("rejects unknown agent without firing PATCH", async () => {
    const patchSpy = vi.spyOn(api, "patch")
    await useModelsStore.getState().assignModel("ghost_agent", "claude-haiku-4-7")
    expect(patchSpy).not.toHaveBeenCalled()
    expect(useModelsStore.getState().error).toMatch(/Unknown agent/)
  })

  it("canonicalises model_id from response (legacy alias path)", async () => {
    // Server might canonicalise a legacy alias → catalog id. The
    // store must reflect the canonical value, not the input.
    useModelsStore.setState({ models: [MODEL_OPUS, MODEL_HAIKU] })
    vi.spyOn(api, "patch").mockResolvedValueOnce({
      data: {
        agent_id: "backend_specialist",
        model_id: "claude-haiku-4-7",  // canonicalised
        override_active: true,
      },
    })
    // (Pretend the user picked haiku via the dropdown — the local
    // catalog gate passes; we're verifying response-sync, not input.)
    await useModelsStore.getState().assignModel("backend_specialist", "claude-haiku-4-7")
    expect(useModelsStore.getState().agents[0].assigned_model).toBe("claude-haiku-4-7")
  })
})

// ── clearOverride ─────────────────────────────────────────────────────

describe("clearOverride", () => {
  it("snaps assigned_model back to default_model optimistically", async () => {
    useModelsStore.setState({
      agents: [agent({ assigned_model: "claude-haiku-4-7", override_active: true })],
    })
    vi.spyOn(api, "delete").mockResolvedValueOnce({} as unknown)
    await useModelsStore.getState().clearOverride("backend_specialist")
    const row = useModelsStore.getState().agents[0]
    expect(row.assigned_model).toBe("claude-opus-4-7")
    expect(row.override_active).toBe(false)
  })

  it("treats 404 as success (no override existed = goal met)", async () => {
    useModelsStore.setState({
      agents: [agent({ assigned_model: "claude-haiku-4-7", override_active: true })],
    })
    vi.spyOn(api, "delete").mockRejectedValueOnce(
      new Error('404: {"detail":"no override set"}'),
    )
    await useModelsStore.getState().clearOverride("backend_specialist")
    const s = useModelsStore.getState()
    // Optimistic state kept; no error surfaced.
    expect(s.agents[0].assigned_model).toBe("claude-opus-4-7")
    expect(s.agents[0].override_active).toBe(false)
    expect(s.error).toBeNull()
  })

  it("reverts on 5xx + surfaces error", async () => {
    useModelsStore.setState({
      agents: [agent({ assigned_model: "claude-haiku-4-7", override_active: true })],
    })
    vi.spyOn(api, "delete").mockRejectedValueOnce(
      new Error('500: {"detail":"db locked"}'),
    )
    await useModelsStore.getState().clearOverride("backend_specialist")
    const s = useModelsStore.getState()
    expect(s.agents[0].assigned_model).toBe("claude-haiku-4-7")
    expect(s.agents[0].override_active).toBe(true)
    expect(s.error).toBe("db locked")
  })
})

// ── resetAll ──────────────────────────────────────────────────────────

describe("resetAll", () => {
  it("no-ops (no HTTP) when nothing is overridden", async () => {
    const delSpy = vi.spyOn(api, "delete")
    await useModelsStore.getState().resetAll()
    expect(delSpy).not.toHaveBeenCalled()
  })

  it("snapshots ALL overridden agents and reverts every one on failure", async () => {
    useModelsStore.setState({
      agents: [
        agent({ agent_id: "a", assigned_model: "claude-haiku-4-7", override_active: true }),
        agent({ agent_id: "b", assigned_model: "claude-haiku-4-7", override_active: true }),
        agent({ agent_id: "c", assigned_model: "claude-opus-4-7", override_active: false }),
      ],
    })
    vi.spyOn(api, "delete").mockRejectedValueOnce(
      new Error('500: {"detail":"db locked"}'),
    )
    await useModelsStore.getState().resetAll()
    const s = useModelsStore.getState()
    // a and b restored, c untouched.
    const byId = Object.fromEntries(s.agents.map((a) => [a.agent_id, a]))
    expect(byId.a.assigned_model).toBe("claude-haiku-4-7")
    expect(byId.a.override_active).toBe(true)
    expect(byId.b.assigned_model).toBe("claude-haiku-4-7")
    expect(byId.b.override_active).toBe(true)
    expect(byId.c.override_active).toBe(false)
    expect(s.error).toBe("db locked")
  })

  it("commits the optimistic clear on success", async () => {
    useModelsStore.setState({
      agents: [
        agent({ agent_id: "a", assigned_model: "claude-haiku-4-7", override_active: true }),
      ],
    })
    vi.spyOn(api, "delete").mockResolvedValueOnce({} as unknown)
    await useModelsStore.getState().resetAll()
    const row = useModelsStore.getState().agents[0]
    expect(row.assigned_model).toBe("claude-opus-4-7")
    expect(row.override_active).toBe(false)
  })
})

// ── clearError ────────────────────────────────────────────────────────

describe("clearError", () => {
  it("nulls the error field", () => {
    useModelsStore.setState({ error: "x" })
    useModelsStore.getState().clearError()
    expect(useModelsStore.getState().error).toBeNull()
  })
})
