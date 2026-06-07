# Product Requirements Document (PRD)
# Hermes Agent ↔ Claude Agent Team Integration

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 1.1 |
| Created Date | 2026-06-07 |
| Last Updated | 2026-06-07 |
| Status | **Draft — pending approval (gap-review applied)** |
| Product Owner | Chandramouli |
| Task Prefix | `HAI` (Hermes Agent Integration) |
| Related Task List | [docs/tasks-hermes-agent-integration.md](tasks-hermes-agent-integration.md) |
| Related Docs | [architecture.md](architecture.md), [cross-cutting-concerns.md](cross-cutting-concerns.md), [setup-claude-platform-on-aws.md](setup-claude-platform-on-aws.md) |

---

## 1. Executive Summary

### 1.1 Product Vision

Let an operator run the entire Claude Agent Team by **talking to Hermes Agent** (Nous Research's autonomous, self-improving agent) instead of clicking the dashboard. Hermes becomes the always-on "shift manager": it **observes** the Agent Team freely (status, cost, failures, deploy health) and reports back through whatever channel the operator prefers (Slack, CLI, Desktop), and it **acts** on the operator's behalf — standing up projects, generating PRDs/specs/build plans, dispatching tasks, deploying — but **only after explicit, per-action human approval**.

The integration adds no orchestration logic of its own. The Agent Team remains the single source of truth for *how* work is sequenced (dependency graph, parallel/sequential scheduling), *what* each agent knows (per-project knowledge namespaces), and *how* artifacts are produced and stored. Hermes is a controlling/monitoring layer **on top**, connected through a thin Model Context Protocol (MCP) server that wraps the existing REST API.

### 1.2 Problem Statement

Today the Agent Team can only be driven by a human sitting at the React dashboard:

- **Monitoring is pull-only and manual.** A failed 2 a.m. deploy sits silently until someone opens the tab. There is no way for an external agent to watch the team and proactively surface incidents.
- **Every action requires a human in the browser.** Creating a project, generating a PRD/API spec, decomposing a build plan, dispatching tasks, and deploying are all dashboard clicks. There is no headless, conversational, or programmatic operator path.
- **No machine-friendly identity.** Auth is browser-oriented: JWT access tokens expire after 30 minutes. An always-on external agent has no first-class long-lived credential.
- **Existing autonomous behaviors are ungoverned by the operator.** Three loops already mutate state with no human in the loop — auto-dispatch of newly-unblocked tasks (`src/core/auto_dispatch.py`, BPD-24), automatic anomaly rollback/alert (`src/core/ops_heal_handler.py`, AET-31), and automatic lesson-writing on failure (`src/core/self_learning_trigger.py`, AET-11). Any "nothing moves without my approval" guarantee must reconcile these.

### 1.3 Target Users

**Primary Users:**
- **Operators / owners** (admin role) who want to run the Agent Team conversationally through Hermes and approve each consequential action.

**Secondary Users:**
- **On-call engineers** who want push alerts (failure, anomaly) routed to a Hermes channel with a one-tap approve/deny for remediation.
- **Developers** who want a scriptable, headless control surface over the Agent Team for automation that still honors the approval gate.

### 1.4 Background — What Hermes Agent Is (and why MCP)

Hermes Agent (Nous Research, MIT-licensed, ~v0.15.x) is a long-running autonomous agent with persistent memory, auto-generated skills, subagents, natural-language scheduling, and multi-platform front-ends (Slack, Discord, Telegram, WhatsApp, Signal, Email, CLI, Desktop). Critically, **Hermes is an MCP client**: it connects out to MCP tool servers over **stdio** (local subprocess), **streamable HTTP**, or **SSE** (remote endpoints), configured in `~/.hermes/config.yaml` under `mcp_servers:` with a `transport:` field. It supports per-server tool filtering (`include`/`exclude`), static bearer-token headers, OAuth 2.1, and mTLS (`ssl_verify`/`client_cert`).

The idiomatic, vendor-supported way to expose a system to Hermes is therefore **an MCP server**. We use **streamable HTTP** (not SSE-only), which Hermes's HTTP client mode supports. Hermes's own scheduler covers periodic pull; an outbound event bridge covers low-latency push.

> **Threat-model note (verified against Hermes docs):** Hermes has **no client-side human-in-the-loop for tool *execution*** — its scheduler and subagents invoke MCP tools **unattended**. Any write tool we expose **will** be called by Hermes autonomously. Therefore the approval gate (§3.4) cannot be a client-side courtesy; it **must** be enforced server-side, and the confirm/reject authority must be physically unreachable by the Hermes service identity (FR-038, NFR-003). This is the load-bearing security assumption of the whole design.

---

## 2. Goals

- **G1** — Hermes can **monitor** the Agent Team end-to-end (requests, projects, costs, deploy health, failures, team status) with read-only access that never requires per-call approval.
- **G2** — Hermes can **drive the full project lifecycle** (new project → brief → PRD → API spec → epics/features/tasks → dispatch → deploy) and ad-hoc task submission, all through the same API the dashboard uses.
- **G3** — **Approval-by-default**: every state-changing action is blocked until the operator explicitly approves it. Observation is exempt. The gate is enforced **server-side in the Agent Team**, not merely by Hermes's prompt behavior.
- **G4** — Hermes **infers the target project** for an untagged task, but the work is **parked** and never starts until the operator confirms the project (or corrects it).
- **G5** — The three existing autonomous loops are **reconciled** with G3: auto-dispatch and auto-rollback become operator-approved proposals; self-learning remains automatic (configurable).
- **G6** — A **long-lived service identity** lets headless Hermes authenticate without 30-minute token churn, scoped by role, revocable, and fully audited.
- **G7** — **Push + pull updates**: Hermes is notified in near-real-time of failures/anomalies/completions via an outbound bridge, and can reconcile full state on a schedule via read tools.
- **G8** — **Phased delivery**: a self-contained read-only Monitor slice ships and is validated first; the gated full-lifecycle slice ships immediately after on top of it.
- **G9** — **Zero duplication of orchestration logic**: the MCP server is a thin adapter over existing endpoints; all RBAC, events, dependency sequencing, and artifact handling stay the single source of truth in the Agent Team.
- **G10** — **Complete audit trail**: every proposal, approval, rejection, expiry, and executed action attributable to the Hermes service identity is persisted and queryable.

---

## 3. Functional Requirements

Priority legend: **Critical** (MVP), **High**, **Medium**, **Low**.

### 3.1 MCP Server (the "way in")

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-001 | A new standalone service `agent-team-mcp` exposes the Agent Team as an MCP server using the official MCP Python SDK. It runs as its own container in the compose stack with an explicit `name:` (`agent-team-mcp`) per the repo's stable-project-name convention. | Critical |
| FR-002 | The MCP server uses the MCP Python SDK's **`streamable-http`** transport, matching a Hermes `mcp_servers.<name>.transport: streamable_http` (HTTP) config entry — **not** SSE-only and **not** stdio (cross-container path fragility, mirrors the supervisor DinD lesson). The exact transport string is pinned in the setup doc so the implementer does not build an SSE server by mistake. | Critical |
| FR-003 | The MCP server is a **thin adapter**: each MCP tool maps to one or more existing `/api/v1` REST calls against the backend over the internal Docker network. It contains **no** business logic, persistence, RBAC decisions, or event emission of its own. | Critical |
| FR-004 | The MCP server authenticates to the backend using the service token (FR-010) and forwards the call. It MUST NOT hold or accept end-user JWTs. | Critical |
| FR-005 | Every MCP tool has a precise name, description, and typed input schema so Hermes's tool-discovery and `include`/`exclude` filtering work cleanly. Tool names are grouped by capability tier (`monitor_*`, `project_*`, `task_*`, `ops_*`) to make Hermes-side scoping ergonomic. | High |
| FR-006 | A `GET /healthz` (or MCP `ping`) on the MCP server reports backend reachability and the resolved service-identity role, so `hermes mcp test` yields a meaningful result. | High |
| FR-007 | MCP tool errors return structured, human-readable messages (recursive root-cause extraction) so Hermes can relay actionable feedback rather than opaque stack traces. | Medium |
| FR-008 | The MCP server is configuration-driven: the set of exposed tools and their required minimum role is declared in a single manifest (YAML), not hardcoded per-tool, so widening/narrowing scope is a config edit. | Medium |
| FR-009 | A documented `~/.hermes/config.yaml` snippet (HTTP server entry + bearer header + `tools.include` per tier) ships in `docs/` so an operator can connect Hermes reproducibly. | High |

### 3.2 Service Identity & Authentication

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-010 | A **service-token** auth mechanism: a long-lived, opaque API key presented as `Authorization: Bearer <token>`, validated by a FastAPI dependency that resolves it to a synthetic service principal with a fixed role. Tokens are stored hashed in a new `service_tokens` SQLite table (id, name, hashed_token, role, created_at, last_used_at, revoked_at). | Critical |
| FR-011 | Service tokens carry a role from the existing `viewer → developer → admin` hierarchy. **⚠️ Correction (review v1.1):** most existing project write endpoints (`create_project`, `prd/generate`, `api-spec/generate`, `tasks/generate`, `build/dispatch`, `deploy`, `stop`) are guarded by `get_current_user` **only — no role check** (verified in `src/api/routes/projects.py`). Therefore role tiering for service tokens **cannot be inherited from existing route guards** and the Monitor tier's read-only-ness is **not** provided by RBAC. Tiering is enforced by two new mechanisms instead: (a) the MCP tool manifest only exposes read tools to a monitor-scoped token (FR-008), and (b) the service-token write-block (FR-015a). Adding `require_role` to the under-guarded write endpoints is **optional hardening, explicitly out of this PRD's critical path** (scope flag — see §5). | Critical |
| FR-012 | Tokens are **revocable** (set `revoked_at`); a revoked token fails auth immediately. Token issuance/revocation is admin-only via `POST /api/v1/service-tokens` and `DELETE /api/v1/service-tokens/{id}`. The raw token is shown exactly once at creation. | High |
| FR-013 | Every authenticated service-token call stamps actions with the service principal identity (e.g. `created_by="hermes-service"`) so the audit trail (FR-040) distinguishes Hermes-driven actions from human ones. | High |
| FR-014 | Optional **per-tier identities**: support issuing separate tokens (e.g. a read-only `hermes-monitor` and an action-capable `hermes-operator`) so monitoring and acting can be separated in the audit log and revoked independently. | Medium |
| FR-015 | **`last_used_at` write debounce**: updating `last_used_at` on every service-token call would add one SQLite write per request to the same DB the host supervisor reads. Coalesce updates (e.g. at most once per 60 s per token) to avoid write contention. | Medium |
| FR-015a | **Service-token write-block (defense in depth)**: the service-token auth dependency denies any direct state-changing call (POST/PUT/PATCH/DELETE to gated resources) that is **not** a proposal create/list/read or an explicitly read-only route — regardless of the route's own guard. A service principal can only mutate state by creating a proposal (FR-031); it can never reach an underlying write endpoint directly. This is the server-side backstop that makes FR-011's missing route RBAC non-exploitable. | Critical |
| FR-015b | **Token rotation runbook**: static bearer tokens have no client-side refresh — a revoked/rotated token (FR-012) keeps being presented by Hermes until the operator edits `headers.Authorization` in `~/.hermes/config.yaml` and reconnects. The setup doc (FR-009) documents this rotation procedure. (OAuth, the real rotation answer, is out of scope §5.) | Medium |

### 3.3 Monitor Tier (read-only, no approval required)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-020 | MCP tool `monitor_list_requests` — list requests with status/project/time filters. Maps to existing requests read endpoints. | Critical |
| FR-021 | MCP tool `monitor_get_request` — full detail + current status + stories/subtasks for one request. | Critical |
| FR-022 | MCP tool `monitor_list_projects` and `monitor_get_project` — project catalog and detail (used by project inference, FR-031). | Critical |
| FR-023 | MCP tool `monitor_get_costs` — cost/spend rollups by request and project (TokenTracker data). | High |
| FR-024 | MCP tool `monitor_recent_failures` — recently failed requests with fingerprints, for proactive surfacing. | High |
| FR-025 | MCP tool `monitor_deploy_health` — per-environment deploy health / anomaly state. | High |
| FR-026 | MCP tool `monitor_team_status` — agents, their resolved models, and current activity. | Medium |
| FR-027 | All Monitor tools are **read-only and never create a proposal**; they execute immediately. Their read-only safety derives from **the MCP manifest exposing no write tools to a monitor-scoped token** (FR-008) and the **service-token write-block** (FR-015a) — **not** from route RBAC, which does not gate these endpoints (see FR-011 correction). `monitor_get_costs` (FR-023) uses only the viewer-readable `/api/v1/cost/dashboard`, never the admin-only orphan/reconcile cost endpoints. | Critical |

### 3.4 Approval Gate (the keystone)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-030 | A unified **Action Proposal** model: a new `proposals` SQLite table (`proposal_id`, `action_type`, `target_ref`, `payload_json`, `status` ∈ {pending, confirmed, rejected, expired, executed, failed}, `proposed_by`, `created_at`, `decided_by`, `decided_at`, `executed_at`, `ttl_seconds`, `result_ref`). Every gated action becomes a proposal before it executes. | Critical |
| FR-031 | API `POST /api/v1/proposals` creates a pending proposal describing an intended action (action_type + payload). It performs **no** side effects beyond persisting the proposal and emitting `proposal.created`. Returns the proposal id. | Critical |
| FR-032 | API `POST /api/v1/proposals/{id}/confirm` (human auth required — admin/developer, **not** the service token alone) transitions pending→confirmed and triggers execution of the underlying action via the existing internal handler for that `action_type`. Emits `proposal.confirmed` then `proposal.executed`/`proposal.failed`. | Critical |
| FR-033 | API `POST /api/v1/proposals/{id}/reject` transitions pending→rejected with an optional reason; the action never runs. Emits `proposal.rejected`. | Critical |
| FR-034 | API `GET /api/v1/proposals` lists proposals with status/type/proposer filters; `GET /api/v1/proposals/{id}` returns one. MCP read tools surface these so Hermes can show "what's awaiting your approval." | High |
| FR-035 | A **confirmation guard invariant**: no `action_type` registered as "gated" may execute through any code path without a corresponding `confirmed` proposal. Enforced centrally in the proposal-execution dispatcher, not per-call. **This includes in-process call sites, not just HTTP routes** — the gate must wrap the internal handlers the autonomous loops call directly: `orchestrator.submit()` (called from `auto_dispatch.py`) and the `auto_rollback` tool invocation (called from `ops_heal_handler.py`). A REST-only gate would leave these in-process paths ungated. | Critical |
| FR-035a | **Idempotency**: `POST /api/v1/proposals` accepts an optional `idempotency_key`; a repeat with the same key returns the existing proposal instead of creating a duplicate (guards against Hermes network retries / re-prompts). Mirrors the idempotency contract already documented on `RollbackRequest`. | High |
| FR-035b | **Atomic state transitions**: pending→{confirmed, rejected, expired} is a single atomic compare-and-set. Concurrent confirm-vs-reject, or confirm-vs-expiry-sweeper, resolves to exactly one winner; the loser is a no-op returning the current state. No double-execution. | Critical |
| FR-035c | **Crash recovery**: on backend startup, a reconciliation pass inspects proposals left in `confirmed` (but not `executed`) by a restart mid-execution and either safely re-drives or marks them `failed` with a clear reason — never silently strands them. (The auto-expire sweeper is in-process like the anomaly sweeper, so restart safety must be explicit.) | High |
| FR-035d | **Execution failure semantics**: when a confirmed action's underlying handler partially fails (e.g. project created but brief write fails), the proposal records `failed` with a structured result describing what did/didn't happen. Re-confirm of a `failed` proposal is **not** allowed (it could double-apply side effects); the operator re-proposes instead. | High |
| FR-035e | **Target referential integrity**: at confirm time (not just propose time) the executor re-validates `target_ref` still exists and is in a legal state (e.g. project not deleted/archived during the up-to-24h TTL window). A stale target fails the proposal with a clear reason rather than executing against a missing entity. | High |
| FR-036 | Pending proposals **auto-expire** after `ttl_seconds` (default 24h, configurable). A background sweeper transitions stale pending→expired and emits `proposal.expired`. Expiry never executes the action. | High |
| FR-037 | The set of **gated action types** is declared in config: `project.create`, `project.brief.set`, `prd.generate`, `apispec.generate`, `epics.generate`, `features.generate`, `tasks.generate`, `buildplan.generate`, `task.dispatch`, `request.submit`, `request.cancel`, `agent.model.set`, `deploy`, `rollback`. Read actions are never gated. | Critical |
| FR-038 | **Confirmation authority is human, enforced physically.** The confirm/reject endpoints **must be unreachable by the Hermes service principal** — a service token presented to confirm/reject returns **403**. They require a human-authenticated session OR a per-proposal one-time approval token the operator triggers from a Hermes channel. This is not a courtesy: because Hermes executes tools unattended (§1.4 threat-model note), a service-token-reachable confirm path would let Hermes self-approve its own proposals. A test MUST assert the 403. | Critical |

### 3.5 Project Inference & Parked Tasks

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-040 | When Hermes submits an untagged task, the MCP `task_submit` tool creates a **`request.submit` proposal** that carries Hermes's **inferred `project_id`** (chosen by Hermes from `monitor_list_projects`) plus a confidence/rationale note. No request is created and no work starts yet. | Critical |
| FR-041 | On confirm, the request is created tagged to the **approved** project and the workflow launches. If the operator corrects the project, Hermes updates the proposal payload (re-propose) before confirm. | Critical |
| FR-042 | If Hermes provides no project and infers none, the proposal records `project_id = Unassigned`; the operator must explicitly confirm the Unassigned tagging (so the loss of per-project context is a conscious choice, not silent). | High |
| FR-043 | The existing direct-submit path (dashboard) is unchanged for human users; only service-token-originated submits are forced through the proposal gate (governed by FR-037). | High |

### 3.6 Full Project Lifecycle Tools (gated)

Each tool below creates a proposal (FR-030) of the matching `action_type`; execution maps to the existing `projects` route handler. None executes without confirmation.

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-050 | `project_create` → `project.create` → `POST /api/v1/projects`. | Critical |
| FR-051 | `project_set_brief` → `project.brief.set` → `PUT /api/v1/projects/{id}/brief`. | Critical |
| FR-052 | `project_generate_prd` → `prd.generate` → `POST /api/v1/projects/{id}/prd/generate`. | Critical |
| FR-053 | `project_generate_apispec` → `apispec.generate` → `POST /api/v1/projects/{id}/api-spec/generate`. | High |
| FR-054 | `project_generate_buildplan` → `buildplan.generate` (or the discrete `epics.generate` / `features.generate` / `tasks.generate`) → corresponding generate endpoints. | High |
| FR-055 | `project_finalize_*` and `task_dispatch` (`task.dispatch` → build/dispatch endpoints) are gated and emit the dispatch only after confirmation. | High |
| FR-056 | `ops_deploy` (`deploy`) maps to `POST /api/v1/projects/{id}/deploy`. **⚠️ Correction (review v1.1):** that endpoint is `get_current_user`-guarded (not admin) today, so the **admin scope for deploy is new and enforced in the proposal confirm-authority check (FR-038), not inherited from the route.** `ops_rollback` (`rollback`) has **no functional rollback endpoint**. A route exists — `POST /api/v1/releases/{deploy_id}/rollback` (`releases.py:225`, **already `require_role("admin")`-guarded**) — but it is an explicit **stub** that returns `rollback_initiated` and performs no rollback; the real work is done by the host-side supervisor. Therefore the `rollback` action_type's executor must *enqueue a `RollbackRequest` row* (the path the supervisor consumes and executes via `git revert`), not call the stub. (The agent-tool `deploy_tools.py` rollback shells `docker compose` *inside* the backend container and is non-functional under DinD per CLAUDE.md — also NOT the rollback path.) **Verified 2026-06-07.** | High |
| FR-057 | Read companions for the above (`project_get_prd`, `project_get_apispec`, `project_get_buildplan`, `project_get_tasks`) are Monitor-tier (ungated) so Hermes can show generated artifacts for review before the next gated step. | Medium |

**Tool → action_type → endpoint map (authoritative; resolves `request.submit` vs `task.dispatch` ambiguity):**

| MCP tool | action_type | Underlying execution | Notes |
|----------|-------------|----------------------|-------|
| `task_submit` | `request.submit` | `orchestrator.submit()` | One-off / ad-hoc task **not** tied to a project task; carries inferred `project_id` (FR-040). |
| `task_dispatch` | `task.dispatch` | `POST /projects/{id}/build/dispatch` | Dispatches an **existing project build-plan task**; honors `depends_on`. Distinct from `request.submit`. |
| `project_create` | `project.create` | `POST /projects` | |
| `project_set_brief` | `project.brief.set` | `PUT /projects/{id}/brief` | |
| `project_generate_prd` | `prd.generate` | `POST /projects/{id}/prd/generate` | LLM cost — gated. |
| `project_generate_apispec` | `apispec.generate` | `POST /projects/{id}/api-spec/generate` | LLM cost — gated. |
| `project_generate_buildplan` | `buildplan.generate` (or `epics`/`features`/`tasks.generate`) | `POST /projects/{id}/.../generate` | LLM cost — gated. |
| `request_cancel` | `request.cancel` | `POST /requests/{id}/cancel` | |
| `agent_set_model` | `agent.model.set` | model override route | |
| `ops_deploy` | `deploy` | `POST /projects/{id}/deploy` | Admin scope is **new**, enforced at confirm (FR-056). |
| `ops_rollback` | `rollback` | **enqueue `RollbackRequest` row** | No REST endpoint; supervisor executes (FR-056). |

### 3.7 Autonomous-Loop Reconciliation

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-060 | **Auto-dispatch** (BPD-24): the handler fires on **`request.completed` / `request.status_changed(status=completed\|deployed)`** for requests with a `source_task_id` — **there is no `request.deployed` event** (verified: that name appears only in comments, never emitted). In Hermes-governed mode it creates a single `task.dispatch` **proposal** listing the newly-unblocked tasks + a notification, instead of calling `orchestrator.submit()` directly; tasks dispatch only on confirm. A global/project flag selects legacy-auto vs propose mode (default propose when a Hermes operator identity exists). | High |
| FR-061 | **Auto-rollback/alert** (AET-31): the anomaly→rollback decision is **deterministic in-backend (no LLM)** and currently calls the `auto_rollback` tool which **enqueues a `RollbackRequest` row** consumed by the host supervisor (not a REST call). In Hermes-governed mode the `ANOMALY` verdict instead creates a `rollback` **proposal** + push alert, and only enqueues the `RollbackRequest` on confirm. Pure alerts (no state change) may still fire automatically. A config flag selects auto vs propose (default propose). The gate intercepts the **in-process `auto_rollback` tool invocation** inside `make_ops_heal_handler`, per FR-035. | High |
| FR-062 | **Self-learning** (AET-11) remains **automatic** by default (it only appends a lesson; no work moves) but is gated behind a config flag so the operator can require approval if desired. | Medium |
| FR-063 | The mode flags (FR-060/61/62) are documented and default to the safest interpretation of "nothing moves without my permission" while preserving the existing behavior when no Hermes operator identity is configured (backward compatible). | High |

### 3.8 Push Notifications (outbound bridge)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-070 | An **outbound event bridge** subscribes to the internal `EventEmitter` and forwards a curated set of events (`request.failed`, `deploy_health.anomaly_detected`, `request.completed`, `proposal.created`, `proposal.expired`) to a configured Hermes inbound channel via webhook. | High |
| FR-071 | The bridge is **configuration-driven** (which events, which webhook URL, which channel) and **soft-fails**: a delivery error logs and is retried/dropped but never blocks event broadcasting or request processing. | High |
| FR-072 | Push payloads are concise and link back to the proposal/request id so the operator can approve from the channel (FR-038 one-time approval token). | Medium |
| FR-073 | Pull remains fully functional without push: Hermes's native scheduler can reconcile state via Monitor tools on an interval even if the bridge is disabled. | High |
| FR-074 | **Disconnect handling / bounded gap**: if Hermes is unreachable, the bridge does not silently drop critical alerts forever. Failed deliveries are buffered with bounded retry/TTL, and on the consumer side Hermes's scheduled pull (FR-073) is the guaranteed reconciliation backstop so the gap window for any missed alert is bounded (≤ the pull interval). Push is best-effort-low-latency; pull is the durability guarantee. The bridge never blocks the EventEmitter (NFR-002). | High |
| FR-075 | **Forwarded-event phase gating**: `proposal.*` events only exist after the proposals engine (§3.4) ships, so the bridge forwards them only from that phase onward. The three pre-existing events (`request.failed`, `request.completed`, `deploy_health.anomaly_detected`) are forwardable from the Monitor phase. | Medium |

### 3.9 Audit & Observability

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-080 | Every proposal lifecycle transition and every executed action is recorded with actor (service principal vs human approver), timestamp, action_type, target, and result — queryable via `GET /api/v1/proposals` and surfaced in events. | High |
| FR-081 | The dashboard gains a minimal **"Pending Approvals"** read view (proposals list) so a human at the UI sees and can confirm/reject the same proposals Hermes raised. (Reuses existing API; small frontend addition.) | Medium |
| FR-082 | Structured logs distinguish Hermes-originated traffic (service principal) for cost and activity attribution. | Medium |
| FR-083 | **Gate observability**: continuous, queryable signals for pending-proposal backlog depth, expired-without-action rate, and service-token call volume — not just the point-in-time audit list. Supports operating the gate (M1) rather than only auditing it after the fact. | Medium |
| FR-084 | **MCP ↔ backend contract test**: a CI/contract test pins the endpoint shapes the MCP adapter depends on against the backend's live OpenAPI schema (the project routes change frequently — 5800+ lines). Mirrors how the frontend regenerates types from OpenAPI; prevents silent MCP/backend version skew. | High |

---

## 4. Non-Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| NFR-001 | **Backward compatibility**: with no service token issued and no Hermes operator configured, the platform behaves exactly as today (auto-dispatch/auto-rollback in legacy mode, no proposal gate for human dashboard users). | Critical |
| NFR-002 | **Soft-fail isolation**: the MCP server or push bridge being down MUST NOT degrade the core Agent Team. The backend boots and runs normally without them. | Critical |
| NFR-003 | **Security**: service tokens are stored hashed; raw shown once; TLS required for remote Hermes; the MCP server never accepts end-user JWTs; the confirm/reject path cannot be exercised by a service-token-only principal. | Critical |
| NFR-004 | **Test coverage** ≥ 80% on new **backend** modules (proposals engine, service-token auth, autonomous-loop mode switches), measured by the repo's existing `--cov=src` gate. The standalone `agent-team-mcp` service is **outside** that gate (separate codebase); it carries its **own** pytest/coverage config, and its end-to-end behavior is additionally covered by the Hermes connect E2E (HAI-20/44). State which gate applies where so neither is silently unmeasured. | High |
| NFR-005 | **Latency**: gated-action proposal creation and confirm→execute add < 250 ms overhead beyond the underlying action. Monitor reads add negligible overhead over the wrapped REST call. | Medium |
| NFR-006 | **Docker-native**: all new services run in the compose stack with pinned project names; no "run locally without Docker" path is introduced. | High |
| NFR-007 | **Config-driven scope**: tool exposure, gated action types, and loop modes are YAML/config, not code, so policy changes need no redeploy of logic. | High |
| NFR-008 | **Documentation**: a setup guide (`docs/setup-hermes-integration.md`) covers issuing a service token, the `~/.hermes/config.yaml` entry, tier scoping, and the approval flow. | High |

---

## 5. Out of Scope (v1)

- **Standing per-action auto-approval grants** (e.g. "always auto-deploy to staging"). Designed for later; v1 is approval-by-default with no standing grants.
- **stdio MCP transport** (HTTP only in v1).
- **Hermes-side skill authoring** — we configure Hermes as an MCP client; we do not build custom native Hermes tools/skills.
- **Multi-tenant Hermes** (multiple independent Hermes instances against one Agent Team) — single operator identity model in v1.
- **OAuth 2.1 / mTLS** between Hermes and the MCP server — bearer service token in v1; OAuth/mTLS noted as a hardening follow-up (also the real token-rotation answer per FR-015b).
- **Adding `require_role` to the under-guarded project write endpoints** (FR-011 correction) — desirable hardening, but the integration's safety does not depend on it (the service-token write-block FR-015a is the backstop). Tracked as a separate hardening item, not on this PRD's critical path.
- **Reworking the dashboard** beyond the minimal Pending Approvals read view.

---

## 6. Architecture Overview

```
  Operator ──chat──▶ Hermes Agent ──MCP/HTTP (Bearer service token)──▶ agent-team-mcp
                        ▲                                                   │ internal REST (service token)
                        │ push alerts (webhook)                             ▼
                  outbound event bridge ◀── EventEmitter ◀──────── Agent Team API (FastAPI :8000)
                                                                            │
                                                          ┌─────────────────┼─────────────────┐
                                                          ▼                 ▼                 ▼
                                                   proposals engine   existing routes    autonomous loops
                                                   (approval gate)    (projects, etc.)   (now propose-mode)
```

Key seams:
- **MCP server** = thin adapter (FR-001..009).
- **Service token** = the only new identity (FR-010..014).
- **Proposals engine** = the only new control-flow concept; gates all state-changing actions uniformly (FR-030..038).
- **Autonomous loops** reroute through proposals in Hermes-governed mode (FR-060..063).
- **Push bridge** = soft-fail outbound notifier (FR-070..073).

Only two changes touch existing backend behavior: **service-token auth** and the **proposals gate** (which also routes the three autonomous loops). Everything else is additive.

---

## 7. Phased Rollout

| Phase | Theme | FRs | Exit criteria |
|-------|-------|-----|---------------|
| **P0 — Foundations** | Service token + MCP skeleton | FR-001..006, FR-010..013 | `hermes mcp test` succeeds against a `viewer` token; handshake verified |
| **P1 — Monitor** | Read-only observation + push | FR-020..027, FR-070..073, FR-009 | Hermes reports live status/cost/failures; failure alert reaches a Hermes channel; zero write capability |
| **P2 — Approval Gate** | Proposals engine (keystone) | FR-030..038, FR-080..082 | A gated action cannot execute without a confirmed proposal; auto-expire works; Pending Approvals view shows proposals |
| **P3 — Lifecycle Actions** | Gated full lifecycle + tagging | FR-040..043, FR-050..057, FR-014 | Operator runs new project → PRD → spec → build plan → dispatch entirely via Hermes, each step confirmed |
| **P4 — Loop Reconciliation** | Bring auto-loops under the gate | FR-060..063 | Auto-dispatch & auto-rollback emit proposals in Hermes-governed mode; legacy mode preserved when no Hermes identity |

P0+P1 deliver the validated Monitor slice (G8). P2→P4 deliver the gated full lifecycle.

---

## 8. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Hermes (or a bug) executes an action without approval | Server-side gate (FR-035/038): confirm/reject require human authority; service token alone cannot self-approve |
| Wrong project inferred → polluted project memory | Parked proposal carries the guess; nothing runs until the operator confirms the project (FR-040..042) |
| Existing auto-loops bypass the gate | Reroute through proposals in Hermes-governed mode; default to propose (FR-060..063) |
| MCP server or bridge outage breaks the platform | Soft-fail isolation (NFR-002); core backend independent of both |
| Long-lived token leakage | Hashed storage, one-time display, revocation, TLS, per-tier scoping (FR-010..014, NFR-003) |
| Proposal backlog accumulates silently | Auto-expire + push notification of pending/expired (FR-036, FR-070) |

---

## 9. Success Metrics

- **M1** — 100% of state-changing Hermes actions pass through a confirmed proposal (audit query shows zero ungated executions by the service principal).
- **M2** — Operator can complete a full project lifecycle (create → PRD → spec → build plan → dispatch) without touching the dashboard.
- **M3** — Mean time from `request.failed` to operator notification < 60 s via push.
- **M4** — Backward-compat check: platform with no Hermes identity behaves byte-for-byte as pre-integration in the existing test suite.
- **M5** — New backend modules ≥ 80% coverage.

---

## 10. Open Questions

| # | Question | Owner | Default if unresolved |
|---|----------|-------|----------------------|
| Q1 | Parked/pending proposal TTL value? | Operator | 24h auto-expire |
| Q2 | Single service identity or per-tier (monitor vs operator)? | Operator | Single `developer`-scoped token in P0; add per-tier in P3 (FR-014) |
| Q3 | Which Hermes channel is the primary console for alerts/approvals? | Operator | CLI/Desktop in P1; Slack optional |
| Q4 | Self-learning: keep automatic or gate it? | Operator | Keep automatic (FR-062) |
| Q5 | Where does Hermes run (same host vs remote)? Decides TLS vs internal-network HTTP | Operator | Same host, internal network, in P0; TLS for remote in hardening |

---

## 11. Maintenance Log

| Date | Change |
|------|--------|
| 2026-06-07 | v1.0 — initial PRD drafted from the Hermes-integration design conversation. Pending operator approval. |
| 2026-06-07 | v1.1a — **direct spot-verification.** Confirmed by reading source: project write endpoints (`create_project`/`deploy_project`/`stop_project`/`generate_prd`/`dispatch_tasks`) are `get_current_user`-only (FR-011 correction stands). Corrected the rollback claim: a `/releases/{deploy_id}/rollback` route **does** exist but is an admin-guarded **stub** (no real rollback); supervisor + `RollbackRequest` remain the real path (FR-056 reworded). Note: `require_role` exists and is used (releases rollback), just not on project writes — so the §5 endpoint-RBAC hardening is straightforward if pursued. |
| 2026-06-07 | v1.1 — **deep gap-review applied.** Corrected the RBAC premise (existing write endpoints are `get_current_user`-only, not role-gated) → added service-token write-block FR-015a as the real backstop. Corrected rollback (no REST endpoint; enqueues `RollbackRequest` via supervisor) and the phantom `request.deployed` event (real trigger is `request.completed`). Strengthened FR-035/FR-038 around Hermes's unattended tool execution + in-process gate interception + 403-on-confirm. Added FR-015/015a/015b, FR-035a–e (idempotency, atomic CAS, crash recovery, execution-failure, target integrity), FR-074/075 (push disconnect/phase gating), FR-083/084 (gate observability, contract test). Pinned `streamable-http` transport. Clarified MCP-service coverage scope (NFR-004) and the optional endpoint-RBAC hardening (§5). |
