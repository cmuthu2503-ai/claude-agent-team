# Product Requirements Document (PRD)
# Per-Agent Dynamic Model Assignment

---

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 1.0 |
| Created Date | 2026-04-08 |
| Last Updated | 2026-04-08 |
| Status | Draft |
| Product Owner | Chandramouli |
| Related Plan | `C:\Users\chand\.claude\plans\cryptic-knitting-platypus.md` |

---

## 1. Executive Summary

### 1.1 Product Vision

Give administrators the ability to assign a different LLM model to each agent in the team — from the Team page in the UI — including support for a new local Ollama runtime alongside the existing cloud providers (Anthropic direct, Amazon Bedrock, OpenAI). Assignments are persistent, take effect without a backend restart, and the system adapts dynamically so any model can be assigned to any agent with zero manual intervention — even models that do not natively support tool calling.

### 1.2 Problem Statement

The current platform has two model-selection layers, neither of which meets the real need:

- **Per-agent YAML defaults** — Each agent's `config/agents/*.yaml` file specifies a `model:` field, but this is only reachable via the legacy `anthropic` provider alias. The UI has no path to change it.
- **Per-request provider selector** — The CommandCenter exposes a 5-button row (Opus / Sonnet / Bedrock / GPT-5.4 / o4-mini) that forces **every agent** in the request to use the same provider's model. This is coarse and prevents cost optimization.

The result is that teams cannot mix models — e.g., running tool-heavy agents (backend_specialist, frontend_specialist) on capable cloud models while running lightweight agents (research_specialist, content_creator) on a free local model. Additionally, the singleton-mutation pattern currently used for per-call model overrides in `src/agents/executor.py` has a latent concurrency race that will become observable once per-agent overrides exist.

Lastly, there is no mechanism to support local LLMs at all: the codebase is hardcoded to Anthropic, Bedrock, and OpenAI cloud endpoints.

### 1.3 Target Users

**Primary Users:**
- **Admin users** managing an Agent Team instance who want to optimize cost, test different models per role, or route sensitive workloads to local models
- **Cost-conscious teams** wanting to mix free local models (Gemma via Ollama) with premium cloud models (Claude Opus) within a single workflow

**Secondary Users:**
- **Developers and viewers** who see the current model assignment in a read-only view on the Team page
- **Operators** who pull a new Ollama model on the host and want it reachable from the containerised backend

---

## 2. Goals

- **G1**: Allow admins to assign a model to each of the 9 agents individually via the Team page, with changes persisted to SQLite and no backend restart required
- **G2**: Support local LLMs via Ollama (OpenAI-compatible API at `http://host.docker.internal:11434/v1`) alongside the existing Anthropic, Bedrock, and OpenAI cloud providers — as a configuration entry only, with no new SDK
- **G3**: Make model selection fully dynamic and require zero manual intervention — any model can be assigned to any agent and will function correctly, including for tool-heavy agents, via a universal ReAct-style tool adapter
- **G4**: Eliminate the latent singleton-mutation concurrency race in `src/agents/executor.py` by threading the resolved model/client through `BaseAgent.process_task()` as keyword arguments instead of mutating instance state
- **G5**: Preserve backward compatibility with existing persisted `Request.provider` values via a `legacy_provider_aliases` map — no database migration required
- **G6**: Adding a new model (new Ollama tag, new Groq endpoint, new Claude version) is a `config/models.yaml` edit plus optional backend restart — zero code changes

---

## 3. Functional Requirements

### 3.1 Model Catalog

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-001 | A new YAML file `config/models.yaml` defines a first-class catalog of every LLM the system can call. Each entry has: `id`, `display_name`, `provider_type` (`anthropic_direct` \| `bedrock` \| `openai_compat`), `model_id` (vendor wire string), `api_key_env`, `base_url`, `tool_calling_mode` (`native` \| `prompted`), `tier` (`cloud` \| `local`), `pricing_per_million.{input,output}`. The catalog includes a `default_model` key and a `legacy_provider_aliases` map. | Critical |
| FR-002 | The initial catalog ships with entries for: `claude-opus-4-6`, `claude-sonnet-4-6`, `bedrock-sonnet-4`, `openai-gpt-5-4`, `openai-o4-mini`, and `ollama-gemma`. All cloud entries use `tool_calling_mode: native`. `ollama-gemma` uses `tool_calling_mode: prompted`. | Critical |
| FR-003 | A `ModelCatalog` Pydantic class in `src/models/catalog.py` loads the YAML and exposes: `load(path)`, `get(id)`, `list_all()`, `resolve_legacy_provider(str)`, `find_by_vendor_id(str)`, `default_model_id` property. | Critical |
| FR-012 | A `POST /api/v1/models/reload` admin-only endpoint re-reads `config/models.yaml` without a restart. Orphaned overrides (referencing deleted models) gracefully fall through to the YAML default. | Low |

### 3.2 Per-Agent Override Persistence

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-004 | A new SQLite table `agent_model_overrides` stores per-agent assignments: `agent_id TEXT PRIMARY KEY, model_id TEXT NOT NULL, updated_at TIMESTAMP, updated_by TEXT`. Added to `SCHEMA_SQL` in `src/state/sqlite_store.py` with `IF NOT EXISTS` (no migration). | Critical |
| FR-005 | `StateStore` exposes CRUD: `get_agent_model_override`, `set_agent_model_override`, `delete_agent_model_override`, `list_agent_model_overrides`, `clear_all_agent_model_overrides`. | Critical |
| FR-006 | Write operations (`PATCH`, `DELETE`) are gated by `require_role("admin")`. Read operations (`GET /api/v1/agents`) are available to all authenticated users. | Critical |

### 3.3 Runtime Resolution

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-007 | A new `ModelResolver` class in `src/agents/model_resolver.py` implements a three-layer resolution chain: **(1)** per-agent override from `agent_model_overrides`, **(2)** agent YAML default from `config/agents/{agent_id}.yaml` `model:` field, **(3)** catalog `default_model`. Legacy `Request.provider` strings are mapped via `legacy_provider_aliases` for historical rows only. | Critical |
| FR-008 | An `LLMClientPool` in `src/agents/client_pool.py` lazily creates and caches LLM clients keyed by `(provider_type, base_url)`. The pool creates one shared `AsyncAnthropic` (direct), one `AsyncAnthropicBedrock`, and one `AsyncOpenAI` per unique base_url — so OpenAI cloud and Ollama each get their own cached client. | Critical |

### 3.4 Universal Tool Calling (ReAct Adapter)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-009 | A new `PromptedToolAdapter` in `src/agents/tool_adapter.py` makes any text-generation model tool-capable through prompt engineering. When a model has `tool_calling_mode: prompted`, the adapter: (a) injects a tool-use instruction block into the system prompt describing available tools and a `<tool_call name="...">...<arg name="...">...</arg></tool_call>` response format; (b) parses the text response for `<tool_call>` blocks and extracts name + args robustly (tolerant of whitespace, case, and partial matches); (c) converts the parsed calls to the same internal shape as native tool calls so the rest of the agent loop is unchanged; (d) formats tool results as user-turn content for the next iteration. | Critical |
| FR-010 | `BaseAgent._call_llm()` in `src/agents/base.py` dispatches between native mode (existing `_call_anthropic` / `_call_openai` paths with native tool params) and prompted mode (same methods but with no native tools and the adapter wrapping the call) based on the resolved model's `tool_calling_mode`. | Critical |
| FR-011 | The adapter gracefully recovers from malformed output: unparseable `<tool_call>` blocks are treated as text; missing tool calls trigger the agent's existing retry loop; common alternative formats (markdown code blocks labeled as tool calls) are detected and extracted. | High |

### 3.5 Singleton Mutation Race Fix

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-013 | `BaseAgent.process_task()` and its `_call_anthropic()` / `_call_openai()` helpers accept `llm_client`, `model`, and `tool_calling_mode` as keyword arguments. The executor passes these values from the `ModelResolver`'s result and **never mutates** `agent.model` or `agent._llm_client`. This eliminates the concurrency race where two simultaneous `execute()` calls on the same singleton agent would corrupt each other's model/client state. | Critical |

### 3.6 API Endpoints

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-014 | `GET /api/v1/models` returns the full catalog: id, display_name, provider_type, tier, tool_calling_mode, pricing. Available to all authenticated users. | Critical |
| FR-015 | `GET /api/v1/agents` is enriched to include: `default_model` (from YAML), `assigned_model` (effective model after resolution), `override_active` (bool), `tool_count`. | Critical |
| FR-016 | `PATCH /api/v1/agents/{agent_id}/model` accepts `{model_id: string}`, validates the agent exists and the model_id exists in the catalog, and upserts the override. Admin-only. Emits an `agent.model_changed` event. | Critical |
| FR-017 | `DELETE /api/v1/agents/{agent_id}/model` clears the override and reverts the agent to its YAML default. Admin-only. | Critical |
| FR-018 | `DELETE /api/v1/agents/model-overrides` (bulk) clears all per-agent overrides in one call. Admin-only. Requires confirmation on the frontend. | Medium |

### 3.7 Team Page UI

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-019 | `frontend/src/pages/TeamStatus.tsx` replaces the static model badge on each agent card with a new `<ModelSelector>` component — a portal-based dropdown modeled after `frontend/src/components/ui/ThemeSelector.tsx` (same `createPortal` + click-outside + theme-cascade pattern). | Critical |
| FR-020 | The dropdown groups models by `tier` + `provider_type` (Cloud — Anthropic / Bedrock / OpenAI, Local). The current selection is marked with a check. When an override is active, a "Reset to default" footer link appears. | Critical |
| FR-021 | A new Zustand store `frontend/src/stores/models.ts` owns the model catalog + per-agent overrides and exposes `fetchModels`, `fetchAgents`, `assignModel`, `clearOverride`, `resetAll`. Updates are optimistic; errors revert the pill and show a toast. | Critical |
| FR-022 | For non-admin users, the model pill is read-only (no dropdown arrow, no click handler). Hover shows a tooltip "Admin only — contact an admin to change model assignments." | Medium |
| FR-023 | An admin-only "Reset all model assignments" button at the top of the Team page, with a confirmation dialog, calls the bulk DELETE endpoint. | Medium |

### 3.8 CommandCenter Cleanup

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-024 | The 5-button provider row (`PROVIDER_OPTIONS`, `selectedProvider` state, localStorage persistence, provider param in submit POST) is **removed** from `frontend/src/pages/CommandCenter.tsx`. Request submission no longer includes a provider field in the outgoing form data. | High |
| FR-025 | The identical provider row in `frontend/src/pages/PromptStudio.tsx` is also removed. | High |
| FR-026 | `POST /api/v1/requests` makes the `provider` form field **optional** (defaults to empty string on new submissions). The orchestrator passes it through unchanged. The resolver ignores empty values and falls through to the YAML default. | High |
| FR-027 | Old persisted `Request.provider` values (e.g., `"anthropic_sonnet"`, `"openai_gpt5"`) continue to render and resolve correctly via `legacy_provider_aliases` in `config/models.yaml`. No DB migration. | High |

### 3.9 Cost Tracking

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-028 | `src/core/token_tracker.py` reads pricing from `config/models.yaml` first, falls back to `config/thresholds.yaml` for back-compat. Unknown model lookups log a warning **once** per model (deduplicated via an in-memory set) instead of silently returning $0. | High |
| FR-029 | The Cost Dashboard frontend distinguishes `tier: local` rows as "Free (local)" to avoid confusion with unknown/missing pricing. | Medium |

### 3.10 Docker Networking

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-030 | All four compose files (`docker-compose.yml`, `docker-compose.staging.yml`, `docker-compose.prod.yml`, `docker-compose.demo.yml`) add `extra_hosts: ["host.docker.internal:host-gateway"]` to the backend service. This makes Ollama running on the host reachable from the container on Linux as well as Windows/macOS. | High |
| FR-031 | On connection failure to an Ollama endpoint, the OpenAI client call path surfaces a friendly `httpx.ConnectError` message: "Ollama not reachable at `{base_url}` — is `ollama serve` running on the host, and is `OLLAMA_HOST=0.0.0.0:11434` set?" | Medium |

---

## 4. Non-Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| NFR-001 | **Concurrency safety**: Two concurrent `AgentSystemExecutor.execute()` calls targeting the same agent with different resolved models must both complete with the correct model and no cross-pollination of state. Verified by a new `tests/test_concurrency.py` stress test. | Critical |
| NFR-002 | **Backward compatibility**: Historical rows in the `requests` table with populated `provider` columns must continue to resolve correctly without any database migration. | Critical |
| NFR-003 | **Test coverage**: The existing 80% coverage gate must be maintained. New code should target ~90% coverage. | Critical |
| NFR-004 | **Cross-platform Docker networking**: Ollama support must work on Windows, macOS, and Linux host systems via the `extra_hosts: host.docker.internal:host-gateway` directive. | High |
| NFR-005 | **Performance**: The per-agent resolution chain adds one indexed SQLite lookup per agent execution (~microseconds). Catalog lookups are in-memory. Total overhead must be negligible compared to LLM call latency. | High |
| NFR-006 | **Graceful degradation**: Removing a model from `config/models.yaml` while overrides reference it must not crash the resolver. Orphaned overrides fall through to the YAML default and show "(unknown)" in the UI until reassigned. | Medium |

---

## 5. Architecture & Design

### 5.1 Resolution chain

```
┌──────────────────────────────────────────────────────┐
│ 1. PER-AGENT OVERRIDE   (SQLite agent_model_overrides)│  ← highest precedence
├──────────────────────────────────────────────────────┤
│ 2. AGENT YAML DEFAULT   (config/agents/{id}.yaml)     │  ← baseline
├──────────────────────────────────────────────────────┤
│ 3. CATALOG default_model                              │  ← last resort
└──────────────────────────────────────────────────────┘

(Legacy Request.provider is only used for historical rows via alias map.)
```

### 5.2 Client pool

```
LLMClientPool
├── anthropic_direct  → AsyncAnthropic (eager, one shared)
├── bedrock           → AsyncAnthropicBedrock (lazy, one shared)
└── openai_compat[base_url]
    ├── "__openai_default__" → AsyncOpenAI(api_key=OPENAI_API_KEY)
    ├── "http://host.docker.internal:11434/v1" → AsyncOpenAI(base_url=..., api_key="ollama")
    └── … (cached per unique base_url)
```

### 5.3 Universal tool calling via ReAct adapter

For models with `tool_calling_mode: prompted`, the framework injects a tool-use instruction block into the system prompt and parses `<tool_call>` XML tags out of the text response. This makes any model — including Gemma, Llama, Phi, and other local models — tool-capable without requiring native function calling support. The dispatch is invisible to the rest of the agent loop.

### 5.4 References

- **Architecture**: [architecture.md](architecture.md) — overall system design and workflow engine
- **Cross-cutting concerns**: [cross-cutting-concerns.md](cross-cutting-concerns.md) — auth, RBAC, cost, testing patterns
- **Local LLM setup**: `docs/local-llms.md` (to be created in PR-4) — Ollama install, `OLLAMA_HOST=0.0.0.0:11434`, pulling models, ReAct adapter details
- **Implementation plan**: `C:\Users\chand\.claude\plans\cryptic-knitting-platypus.md`

---

## 6. Success Metrics

| ID | Metric | Target |
|----|--------|--------|
| SM-001 | All 9 agents can be assigned any of the 6+ catalog models via the Team page UI | 100% coverage |
| SM-002 | A tool-heavy agent (`backend_specialist`) assigned to a local model (Gemma via Ollama) produces working output (files written, tests run, git commits) through the ReAct adapter | End-to-end test passes |
| SM-003 | Concurrent `execute()` calls on the same agent with different models complete with correct model attribution in logs and cost tracking | Stress test passes |
| SM-004 | Adding a new Ollama model is zero YAML edits beyond the one-time `config/models.yaml` entry (plus running `ollama pull <tag>` on the host) | Documented in `docs/local-llms.md` |
| SM-005 | All existing tests continue to pass; new coverage ≥90% on new modules | `docker compose exec backend pytest --cov-fail-under=80` passes |
| SM-006 | Cost dashboard correctly attributes tokens to the model actually used per agent per request, distinguishing `tier: local` as "Free (local)" | Manual verification |

---

## 7. Out of Scope

The following items are explicitly deferred to future work:

- **Per-user loadouts** — Every user having their own model assignment set. Current scope is a single global assignment per agent (admin-only).
- **Runtime Ollama auto-discovery** — Polling `GET /api/tags` at backend startup to auto-populate the catalog from installed models. Current scope requires one-time `config/models.yaml` entries.
- **Per-call capability routing** — Automatically routing tool calls to a different model than reasoning calls within the same agent turn. Current scope uses one model per agent per call.
- **PromptStudio model selector redesign** — The current scope only removes the legacy provider button row. A new first-class model picker in PromptStudio is deferred.
- **Silent fallback retry on tool call failure** — If the prompted adapter fails to extract a tool call, the current scope uses the existing retry loop. Automatic substitution to a different model is deferred.
- **Catalog health checks** — `GET /api/v1/models/{id}/health` endpoint that probes each model's reachability. Nice-to-have, not required for v1.

---

## 8. Open Questions

All major design decisions were resolved during the planning phase. Locked decisions:

1. **Scope**: Global, admin-only. One assignment per agent, set by admins.
2. **Concurrency fix**: Included in PR-1 via the `BaseAgent.process_task()` kwarg refactor. Non-optional.
3. **Tool compatibility**: Universal ReAct tool adapter. No warnings, no hard blocks — the adapter makes any model tool-capable.
4. **CommandCenter buttons**: Removed entirely. All model selection happens on the Team page.

---

## 9. Implementation Phasing (PR Sequence)

| PR | Scope | Key Deliverables |
|----|-------|------------------|
| **PR-1** | Foundations | `config/models.yaml`, `catalog.py`, `model_resolver.py`, `client_pool.py`, `tool_adapter.py`, `executor.py` refactor, `BaseAgent` kwarg refactor (concurrency fix) |
| **PR-2** | Persistence + API | `agent_model_overrides` SQLite table, `PATCH/DELETE /api/v1/agents/{id}/model`, enriched `GET /api/v1/agents`, `GET /api/v1/models`, `POST /api/v1/models/reload` |
| **PR-3** | Frontend Team page | `stores/models.ts`, `ModelSelector.tsx`, `TeamStatus.tsx` integration, "Reset all" admin button |
| **PR-4** | CommandCenter cleanup + Ollama | Remove provider button rows, add `ollama-gemma` catalog entry, `extra_hosts` compose change, friendly connection error, `docs/local-llms.md` |
| **PR-5** | Cost migration + docs | `TokenTracker` reads from `models.yaml`, dedup warnings, Cost Dashboard "Free (local)" tier, `CLAUDE.md` update, this PRD, task-list Phase 9 updates |

Each PR is independently shippable. PR-1 alone ships the concurrency race fix.

---

## 10. Verification Plan

Unit tests, integration tests, and end-to-end smoke flow are documented in full in the implementation plan:
`C:\Users\chand\.claude\plans\cryptic-knitting-platypus.md` (section "Verification Plan")

Key test commands:

```bash
docker compose exec backend pytest tests/test_model_catalog.py tests/test_model_resolver.py tests/test_client_pool.py tests/test_tool_adapter.py tests/test_concurrency.py -xvs
docker compose exec backend pytest tests/test_agents_route_v2.py tests/test_models_route.py tests/test_state_overrides.py -xvs
docker compose exec backend pytest --cov=src --cov-fail-under=80
docker compose exec frontend npm run build
docker compose exec frontend npm run lint
docker compose exec frontend npm run generate-types
```

---

## 11. Appendix: Catalog YAML example

```yaml
models:
  claude-opus-4-6:
    display_name: "Claude Opus 4.6"
    provider_type: anthropic_direct
    model_id: claude-opus-4-6
    api_key_env: ANTHROPIC_API_KEY
    base_url: null
    tool_calling_mode: native
    tier: cloud
    pricing_per_million: {input: 15.00, output: 75.00}

  ollama-gemma:
    display_name: "Gemma (local Ollama)"
    provider_type: openai_compat
    model_id: gemma3:12b
    api_key_env: null
    base_url: http://host.docker.internal:11434/v1
    tool_calling_mode: prompted
    tier: local
    pricing_per_million: {input: 0.00, output: 0.00}

default_model: claude-sonnet-4-6

legacy_provider_aliases:
  anthropic_sonnet: claude-sonnet-4-6
  anthropic_opus:   claude-opus-4-6
  bedrock:          bedrock-sonnet-4
  openai_gpt5:      openai-gpt-5-4
  openai_o3:        openai-o4-mini
  anthropic:        claude-sonnet-4-6
```
