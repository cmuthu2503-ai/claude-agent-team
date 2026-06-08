# Implementation Task List
# Hermes Agent ↔ Claude Agent Team Integration (HAI)

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 1.1 |
| Created Date | 2026-06-07 |
| Last Updated | 2026-06-07 |
| Status | Active — not started (gap-review applied) |
| Product Owner | Chandramouli |
| Task Prefix | `HAI` |
| Source PRD | [docs/prd-hermes-agent-integration.md](prd-hermes-agent-integration.md) |

> This breakdown follows the per-feature task-tracker convention used by PAM,
> BPD, KB, etc. in [task-list.md](task-list.md). Every task traces to a PRD
> functional requirement (`FR-xxx`). Once a phase ships, roll its summary row
> into `task-list.md`'s post-release feature tracker.

---

## How to Use This Document

- Each task has a unique ID: `HAI-<NN>`.
- Tasks are grouped into the 5 phases defined in PRD §7 (P0→P4).
- A task cannot start until all listed dependencies are done.
- Effort: **S** = hours, **M** = 1–2 days, **L** = 3–5 days, **XL** = 1+ week.
- **Traces FR**: the PRD requirement(s) the task implements. Build strictly to the FR — no scope creep beyond it.

### Status Legend

| Status | Meaning |
|--------|---------|
| `[ ]` | Not started |
| `[~]` | In progress |
| `[x]` | Completed |
| `[!]` | Blocked |
| `[-]` | Skipped / Deferred |

---

## Progress Summary

| Phase | Theme | Total | Done | In Progress | Blocked | Not Started |
|-------|-------|-------|------|-------------|---------|-------------|
| P0 | Foundations (service token + MCP skeleton) | 12 | 12 | 0 | 0 | 0 |
| P1 | Monitor (read-only + push) | 12 | 1 | 0 | 0 | 11 |
| P2 | Approval Gate (proposals engine) | 21 | 0 | 0 | 0 | 21 |
| P3 | Lifecycle Actions (gated) | 12 | 0 | 0 | 0 | 12 |
| P4 | Autonomous-Loop Reconciliation | 6 | 0 | 0 | 0 | 6 |
| **Total** | | **63** | **13** | **0** | **0** | **50** |

> Task IDs run HAI-01..HAI-63. IDs are unique but **not contiguous per phase** — HAI-51..63 were added in the v1.1 gap-review and slot into their dependency phase (P0: 51–53, P1: 54, P2: 55–63), not at the end. Sort by the Depends-On graph, not by ID number.

---

## Phase P0 — Foundations

Service identity + MCP server skeleton. Goal: `hermes mcp test` succeeds against a read-only token. (PRD §7 P0)

| ID | Task | Description | Effort | Depends On | Traces FR | Status |
|----|------|-------------|--------|-----------|-----------|--------|
| HAI-01 | `service_tokens` schema | Add `service_tokens` table (id, name, hashed_token, role, created_at, last_used_at, revoked_at) to `SQLiteStateStore`; migration via `ALTER`/`CREATE IF NOT EXISTS`. **Done:** table in `SCHEMA_SQL`, `ServiceToken` model, store CRUD (create/get_by_hash/list/revoke/touch) on the abstract + SQLite store, 6 unit tests. | M | — | FR-010 | `[x]` |
| HAI-02 | Service-token auth dependency | FastAPI dependency that validates `Authorization: Bearer <token>`, resolves to a synthetic service principal with the token's role; updates `last_used_at`; rejects revoked tokens. **Done:** `get_service_principal` + `hash_service_token` in `src/auth/service.py`; principal shaped like the JWT payload + `is_service_token=True`; best-effort `last_used` touch; 6 unit tests. | M | HAI-01 | FR-010, FR-011, FR-012 | `[x]` |
| HAI-03 | Service-token admin routes | `POST /api/v1/service-tokens` (create, returns raw token once), `GET` (list, no secret), `DELETE /{id}` (revoke). Admin-only. **Done:** `src/api/routes/service_tokens.py` (admin-gated via `require_role`), raw token shown once + hashed for storage, idempotent revoke (204/404), role/name validation (400); router registered in `main.py`; 6 route tests. | M | HAI-02 | FR-012 | `[x]` |
| HAI-04 | Service-principal attribution | Thread the service identity into `created_by`/actor fields **and structured logs** so DB rows AND logs are attributable to Hermes. **Done:** `principal_actor(principal)` canonical actor string (`service:<name>` vs human username) for `created_by`/`proposed_by`/audit; bounded `service_principal_active` structured log (gated by the HAI-52 debounce). 1 test. (Proposals engine P2 consumes `principal_actor` for `proposed_by`.) | S | HAI-02 | FR-013, FR-082 | `[x]` |
| HAI-51 | Service-token write-block | The service-token dependency denies any direct state-changing call that isn't a proposal create/list/read or a read-only route — regardless of route guard. Service principal can only mutate via proposals. **Done:** global `ServiceTokenWriteBlockMiddleware` — a live service token may only `POST /api/v1/proposals`; every other POST/PUT/PATCH/DELETE → 403 (incl. proposal confirm/reject, which are human-only). Human JWT + read traffic untouched; registered in `main.py`; 8 tests. | M | HAI-02 | FR-015a | `[x]` |
| HAI-52 | `last_used_at` debounce | Coalesce `last_used_at` updates (≤ once/60s per token) to avoid SQLite write contention with the supervisor. **Done:** process-local `_should_touch_last_used` (60s window, time-injectable) gating the touch in `get_service_principal`; 3 tests (incl. two rapid auths → one write). | S | HAI-02 | FR-015 | `[x]` |
| HAI-53 | MCP↔backend contract test + cov config | Contract test pinning adapter-used endpoint shapes against live backend OpenAPI; give `agent-team-mcp` its own pytest/coverage config (outside backend `--cov=src`). **Done:** `tests/test_contract.py` pins `/health` + `/service-tokens/me` against the live `/openapi.json` (skips if backend down); `.coveragerc` (`source=.`) — the MCP service's own gate. Verified: 14 MCP tests pass with coverage. | M | HAI-07 | FR-084, NFR-004 | `[x]` |
| HAI-05 | MCP server scaffold | New `agent-team-mcp` service (official MCP Python SDK, streamable HTTP transport). Project skeleton + Dockerfile. **Done:** `mcp_server/` package (`server.py` FastMCP + `ping` tool, `config.py` env, `requirements.txt`, `Dockerfile`, `README.md`). Verified: image builds, container starts, `StreamableHTTP session manager started`, `/mcp` serves (406 to a bare GET = endpoint live). | M | — | FR-001, FR-002 | `[x]` |
| HAI-06 | Compose wiring | Add `agent-team-mcp` to compose with pinned `name:`, internal network access to backend, env for backend URL + service token. **Done:** `agent-team-mcp` service in `docker-compose.yml` (build `./mcp_server`, on `dev-net`, port 9000 published, `AGENT_TEAM_BACKEND_URL=http://backend:8000` + `AGENT_TEAM_SERVICE_TOKEN`, TCP healthcheck, depends_on backend healthy). Verified: comes up healthy + reaches `backend:8000` over the network. | S | HAI-05 | FR-001, NFR-006 | `[x]` |
| HAI-07 | Thin REST-adapter client | HTTP client in the MCP server that calls backend `/api/v1` with the service token; structured error → recursive root-cause extraction. **Done:** `mcp_server/backend_client.py` (`BackendClient` get/post, Bearer service-token auth, unwraps the `{data,meta,error}` envelope, `BackendError` with recursive `root_cause` + envelope/`detail` message extraction); 8 tests (httpx.MockTransport) + a live end-to-end call against the backend. | M | HAI-05, HAI-02 | FR-003, FR-004, FR-007 | `[x]` |
| HAI-08 | Tool manifest loader | YAML manifest declaring exposed tools + minimum role per tool; MCP server registers tools from it (no hardcoding). **Done:** `tools_manifest.yaml` + `manifest.py` (`load_manifest`, `role_allows` viewer⊂developer⊂admin, `tools_for_role`, `register_tools`) + `tool_impls.py` registry; server registers only role-allowed tools that have an impl. 4 tests. | M | HAI-05 | FR-005, FR-008, NFR-007 | `[x]` |
| HAI-09 | Health/ping + connect docs | MCP `ping`/`GET /healthz` reporting backend reachability + resolved role; ship `~/.hermes/config.yaml` snippet (pinned `transport: streamable_http` + bearer header) **and the token-rotation runbook** in `docs/setup-hermes-integration.md`. **Done:** `ping` + `healthz` MCP tools (backend reachability + resolved role + registered tools); backend `GET /api/v1/service-tokens/me` whoami for role resolution; `docs/setup-hermes-integration.md` (config snippet + rotation runbook). Verified E2E: seeded viewer token → server resolved `role=viewer` and registered the tool. | S | HAI-06, HAI-07 | FR-006, FR-009, FR-015b, NFR-008 | `[x]` |

---

## Phase P1 — Monitor

Read-only observation tools + outbound push. Goal: Hermes reports live state and gets failure alerts; zero write capability. (PRD §7 P1)

| ID | Task | Description | Effort | Depends On | Traces FR | Status |
|----|------|-------------|--------|-----------|-----------|--------|
| HAI-10 | `monitor_list_requests` | List requests with status/project/time filters → requests read endpoints. **Done:** manifest entry + `monitor_list_requests(status, project_id, per_page)` impl → `GET /api/v1/requests`. Enabler: combined **`get_principal`** dependency (JWT *or* service token) applied to the list route — humans unaffected, write-block still guards mutations. Verified E2E: viewer token listed requests via the adapter. 5 backend + 3 MCP tests + contract endpoint. | S | HAI-07, HAI-08 | FR-020, FR-027 | `[x]` |
| HAI-11 | `monitor_get_request` | Request detail + status + stories/subtasks. | S | HAI-10 | FR-021, FR-027 | `[ ]` |
| HAI-12 | `monitor_list_projects` / `monitor_get_project` | Project catalog + detail (feeds project inference). | S | HAI-07 | FR-022, FR-027 | `[ ]` |
| HAI-13 | `monitor_get_costs` | Cost/spend rollups by request + project. Uses **only** the viewer-readable `/cost/dashboard` — never the admin-only orphan/reconcile endpoints (would 403). | S | HAI-07 | FR-023, FR-027 | `[ ]` |
| HAI-14 | `monitor_recent_failures` | Recent failed requests + fingerprints. | S | HAI-07 | FR-024, FR-027 | `[ ]` |
| HAI-15 | `monitor_deploy_health` | Per-env deploy health / anomaly state. | S | HAI-07 | FR-025, FR-027 | `[ ]` |
| HAI-16 | `monitor_team_status` | Agents, resolved models, current activity. | S | HAI-07 | FR-026, FR-027 | `[ ]` |
| HAI-17 | Outbound bridge core | EventEmitter subscriber that forwards curated events to a configured webhook; soft-fail, retry/drop, never blocks broadcasting. | M | HAI-02 | FR-070, FR-071, NFR-002 | `[ ]` |
| HAI-18 | Push event selection + payloads | Config-driven forwarding of the **three real existing events only** in P1 (`request.failed`, `request.completed`, `deploy_health.anomaly_detected`); concise payloads linking back to ids. **No `request.deployed`** (never emitted). `proposal.*` forwarding is deferred to P2 (HAI-61). | S | HAI-17 | FR-070, FR-072, FR-075 | `[ ]` |
| HAI-19 | Pull-only fallback verify | Confirm Monitor tools + Hermes scheduler reconcile state with the bridge disabled. | S | HAI-10..16 | FR-073 | `[ ]` |
| HAI-54 | Bridge disconnect / bounded gap | Buffer failed deliveries with bounded retry/TTL; document pull (FR-073) as the durability backstop so any missed alert's gap ≤ pull interval. Never block EventEmitter. | M | HAI-17 | FR-074 | `[ ]` |
| HAI-20 | P1 tests + Hermes connect E2E | Unit tests for read tools/bridge; manual E2E: connect Hermes, run a brief, trigger a failure, see alert. | M | HAI-10..19, HAI-54 | NFR-004 | `[ ]` |

---

## Phase P2 — Approval Gate (keystone)

Unified proposals engine. Goal: no gated action executes without a confirmed proposal. (PRD §7 P2)

| ID | Task | Description | Effort | Depends On | Traces FR | Status |
|----|------|-------------|--------|-----------|-----------|--------|
| HAI-21 | `proposals` schema | New `proposals` table per FR-030 (full lifecycle fields). | M | HAI-01 | FR-030 | `[ ]` |
| HAI-22 | Proposal model + store methods | Pydantic model + CRUD/transition methods on the state store. | M | HAI-21 | FR-030 | `[ ]` |
| HAI-23 | `POST /proposals` (create) | Create pending proposal; no side effects; emit `proposal.created`. | M | HAI-22 | FR-031 | `[ ]` |
| HAI-24 | Gated-action registry | Config of gated `action_type`s (FR-037) + dispatcher mapping each to its internal handler. | M | HAI-22 | FR-037 | `[ ]` |
| HAI-25 | Central confirmation guard | Invariant enforcement: a gated action_type cannot execute without a `confirmed` proposal, enforced in the dispatcher (not per-call). | L | HAI-24 | FR-035 | `[ ]` |
| HAI-26 | `POST /proposals/{id}/confirm` | Human-authority guard (reject service-token-only); pending→confirmed→execute via dispatcher; emit `confirmed`+`executed`/`failed`. | L | HAI-25 | FR-032, FR-035, FR-038 | `[ ]` |
| HAI-27 | `POST /proposals/{id}/reject` | pending→rejected (+reason); never executes; emit `proposal.rejected`. | S | HAI-22 | FR-033 | `[ ]` |
| HAI-28 | `GET /proposals` + `GET /{id}` | List/detail with filters; backing for MCP + UI. | S | HAI-22 | FR-034, FR-080 | `[ ]` |
| HAI-29 | Auto-expire sweeper | Background task: stale pending→expired after `ttl_seconds` (default 24h); emit `proposal.expired`; never executes. | M | HAI-22 | FR-036 | `[ ]` |
| HAI-30 | One-time approval token (channel approve) | Per-proposal token enabling operator to confirm from a Hermes channel without a full dashboard session, still human-authority. | M | HAI-26 | FR-038, FR-072 | `[ ]` |
| HAI-31 | Pending Approvals UI view | Minimal dashboard read view listing proposals with confirm/reject (reuses API). | M | HAI-28 | FR-081 | `[ ]` |
| HAI-55 | Proposal idempotency | Optional `idempotency_key` on `POST /proposals`; repeat key returns existing proposal (guards Hermes retries/re-prompts). | S | HAI-23 | FR-035a | `[ ]` |
| HAI-56 | Atomic state transitions (CAS) | pending→{confirmed,rejected,expired} as a single atomic compare-and-set; concurrent confirm/reject/expiry resolves to one winner, no double-execute. | M | HAI-22 | FR-035b | `[ ]` |
| HAI-57 | Crash-recovery reconciliation | Startup pass over `confirmed`-but-not-`executed` proposals: safely re-drive or mark `failed`; never strand. | M | HAI-26 | FR-035c | `[ ]` |
| HAI-58 | Execution-failure semantics | On partial handler failure, record `failed` + structured result; disallow re-confirm of `failed` (operator re-proposes). | M | HAI-26 | FR-035d | `[ ]` |
| HAI-59 | Target integrity at confirm | Re-validate `target_ref` exists + legal state at confirm time (project not deleted/archived in TTL window); else fail with reason. | S | HAI-26 | FR-035e | `[ ]` |
| HAI-60 | 403-on-confirm test | Assert a service-token principal gets **403** on confirm/reject; only human auth / one-time token may approve. | S | HAI-26, HAI-30 | FR-038, NFR-003 | `[ ]` |
| HAI-61 | `proposal.*` forwarding | Forward `proposal.created`/`proposal.expired` via the bridge (deferred from P1 — producers now exist). | S | HAI-23, HAI-29, HAI-17 | FR-070, FR-075 | `[ ]` |
| HAI-62 | Gate observability | Queryable signals: pending-backlog depth, expired-without-action rate, service-token call volume. | M | HAI-28 | FR-083 | `[ ]` |
| HAI-63 | In-process gate interception | Wrap internal handlers (`orchestrator.submit` from auto_dispatch, `auto_rollback` from ops_heal) so in-process callers also pass the gate — not just HTTP routes. | L | HAI-25 | FR-035 | `[ ]` |
| HAI-32 | P2 tests | Coverage ≥80% on proposals engine + guard; explicit test that no gated path (HTTP **and in-process**) executes ungated; idempotency/CAS/recovery covered. | M | HAI-21..30, HAI-55..63 | NFR-004, M1 | `[ ]` |

---

## Phase P3 — Lifecycle Actions (gated)

Gated MCP tools for full project lifecycle + project-inference parked tasks. Goal: run the whole lifecycle via Hermes, each step confirmed. (PRD §7 P3)

| ID | Task | Description | Effort | Depends On | Traces FR | Status |
|----|------|-------------|--------|-----------|-----------|--------|
| HAI-33 | `task_submit` (inferred project, parked) | Creates a `request.submit` proposal carrying Hermes's inferred `project_id` + rationale; nothing runs until confirm. | M | HAI-26, HAI-12 | FR-040, FR-041 | `[ ]` |
| HAI-34 | Unassigned explicit-confirm path | No inference → proposal records `Unassigned`; operator must consciously confirm. | S | HAI-33 | FR-042 | `[ ]` |
| HAI-35 | Service-submit forced through gate | Service-token submits route through proposals; human dashboard submit unchanged. | M | HAI-25 | FR-043, NFR-001 | `[ ]` |
| HAI-36 | `project_create` | `project.create` proposal → `POST /projects`. | S | HAI-26 | FR-050 | `[ ]` |
| HAI-37 | `project_set_brief` | `project.brief.set` proposal → `PUT /projects/{id}/brief`. | S | HAI-26 | FR-051 | `[ ]` |
| HAI-38 | `project_generate_prd` | `prd.generate` proposal → PRD generate endpoint. | S | HAI-26 | FR-052 | `[ ]` |
| HAI-39 | `project_generate_apispec` | `apispec.generate` proposal → API-spec generate endpoint. | S | HAI-26 | FR-053 | `[ ]` |
| HAI-40 | `project_generate_buildplan` | `buildplan.generate` (or discrete epics/features/tasks) proposals → generate endpoints. | M | HAI-26 | FR-054 | `[ ]` |
| HAI-41 | `task_dispatch` + finalize | `task.dispatch` proposal → build/dispatch endpoints; finalize gated. | M | HAI-26 | FR-055 | `[ ]` |
| HAI-42 | `ops_deploy` / `ops_rollback` | `deploy`/`rollback` proposals at admin scope → deploy/stop endpoints. | M | HAI-26 | FR-056 | `[ ]` |
| HAI-43 | Lifecycle read companions | `project_get_prd/apispec/buildplan/tasks` as Monitor-tier for review between gated steps. | S | HAI-07 | FR-057 | `[ ]` |
| HAI-44 | Per-tier identities + P3 E2E | Optional `hermes-monitor` vs `hermes-operator` tokens; full lifecycle E2E via Hermes, each step confirmed. | M | HAI-03, HAI-36..42 | FR-014, M2 | `[ ]` |

---

## Phase P4 — Autonomous-Loop Reconciliation

Bring the three existing auto-loops under the gate in Hermes-governed mode; preserve legacy mode. (PRD §7 P4)

| ID | Task | Description | Effort | Depends On | Traces FR | Status |
|----|------|-------------|--------|-----------|-----------|--------|
| HAI-45 | Governed-mode flag plumbing | Config flags selecting legacy-auto vs propose mode; default propose when a Hermes operator identity exists, legacy otherwise. | M | HAI-24 | FR-063, NFR-001 | `[ ]` |
| HAI-46 | Auto-dispatch → proposal | In governed mode, BPD-24 (fires on **`request.completed`/`status_changed`** for `source_task_id` requests — **not** a `request.deployed` event, which doesn't exist) emits one `task.dispatch` proposal for unblocked tasks + notification instead of calling `orchestrator.submit()`. Reuses the in-process interception (HAI-63). | M | HAI-45, HAI-41, HAI-63 | FR-060 | `[ ]` |
| HAI-47 | Auto-rollback → proposal | In governed mode, AET-31 `ANOMALY` (deterministic, no LLM) emits a `rollback` proposal + push alert; on confirm the executor **enqueues a `RollbackRequest` row** for the host supervisor (there is **no `/rollback` REST endpoint**). Pure alerts may still auto-fire. Reuses in-process interception (HAI-63). | M | HAI-45, HAI-42, HAI-63 | FR-061 | `[ ]` |
| HAI-48 | Self-learning gate flag | Keep AET-11 automatic by default; flag to require approval. | S | HAI-45 | FR-062 | `[ ]` |
| HAI-49 | Backward-compat verification | Prove no-Hermes-identity config behaves as pre-integration across the existing suite. | M | HAI-45..48 | NFR-001, M4 | `[ ]` |
| HAI-50 | P4 tests + audit query | Loop-mode tests; audit query proving zero ungated executions by the service principal (M1). | M | HAI-45..49 | NFR-004, M1, M5 | `[ ]` |

---

## Dependency Notes

- **P0 → P1**: Monitor tools need the MCP adapter (HAI-07) and service-token auth (HAI-02).
- **P2 is the keystone**: every P3/P4 gated tool depends on the proposals engine (HAI-25/26).
- **In-process interception (HAI-63)** is what lets P4's auto-loop reconciliation work — auto-dispatch and auto-rollback call internal handlers directly, so a REST-only gate would miss them. HAI-46/47 depend on it.
- **P4 depends on P3**: auto-dispatch/auto-rollback proposals reuse the `task.dispatch` (HAI-41) and `rollback`-enqueue (HAI-42/47) execution paths.
- **Service-token write-block (HAI-51)** is the security backstop, not route RBAC — existing project write endpoints are `get_current_user`-only. The Monitor tier's read-only-ness depends on HAI-08 (manifest) + HAI-51, never on role guards.
- **Backward compatibility (NFR-001)** is re-verified at HAI-35 and HAI-49.

---

## Maintenance Log

| Date | Change |
|------|--------|
| 2026-06-07 | v1.0 — initial 50-task breakdown (HAI-01..50) created from PRD v1.0. All tasks not started. |
| 2026-06-07 | v1.1 — gap-review applied. Added 13 tasks (HAI-51..63): service-token write-block, `last_used_at` debounce, contract test/cov config, bridge disconnect handling, proposal idempotency/atomic-CAS/crash-recovery/execution-failure/target-integrity, 403-on-confirm test, `proposal.*` forwarding, gate observability, in-process gate interception. Corrected HAI-18 (no `request.deployed`; `proposal.*` → P2), HAI-46 (real trigger `request.completed`), HAI-47 (rollback = enqueue `RollbackRequest`, no REST endpoint), HAI-13 (cost dashboard endpoint only), HAI-04/09 (FR-082 log attribution, rotation runbook). Total 50→63. |
