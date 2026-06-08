# Setup: Connecting Hermes Agent to the Claude Agent Team

How to point **Hermes Agent** (Nous Research) at the **Claude Agent Team** so it
can monitor and (later, behind human approval) drive the platform. Design:
[docs/prd-hermes-agent-integration.md](prd-hermes-agent-integration.md).

## Document Information

| Field | Value |
|-------|-------|
| Created | 2026-06-08 |
| Status | Living — updated as P1+ tools land |
| Covers | HAI-09 (FR-006, FR-009, FR-015b, NFR-008) |

---

## 1. Architecture in one line

Hermes connects, over MCP/streamable-HTTP, to **`agent-team-mcp`** — a thin
adapter that wraps the backend's `/api/v1` REST API and authenticates with a
**long-lived service token**. A global write-block ensures a service token can
only ever *create a proposal*; nothing destructive happens without a human.

```
Hermes ──MCP/streamable-http (Bearer service token)──▶ agent-team-mcp ──REST──▶ backend
```

## 2. Bring up `agent-team-mcp`

It runs in the dev stack (port 9000 published for Hermes):

```bash
docker compose up -d agent-team-mcp
docker compose ps agent-team-mcp        # → Up (healthy)
```

## 3. Issue a service token (admin)

The raw token is shown **once** — store it immediately.

```bash
# As an admin user (JWT). The first-run admin password is logged once at
# backend startup: docker compose logs backend | grep first_run_admin_created
curl -sX POST http://localhost:8000/api/v1/service-tokens \
  -H "Authorization: Bearer <ADMIN_JWT>" \
  -H "Content-Type: application/json" \
  -d '{"name":"hermes-monitor","role":"viewer"}'
# → { "data": { "token_id": "stok-…", "token": "hermes_…", "role": "viewer" }, … }
```

Start with **`role: viewer`** (read-only / monitor). Widen to `developer`/`admin`
only when you reach the gated action tiers (and even then, every state change is
human-approved via the proposal flow).

Wire it into the MCP service (so the adapter authenticates and the server
resolves its role):

```bash
# In .env (compose reads ${AGENT_TEAM_SERVICE_TOKEN})
AGENT_TEAM_SERVICE_TOKEN=hermes_…
# then
docker compose up -d agent-team-mcp
```

Confirm it resolved the role and registered the tools it's allowed to:

```bash
docker compose logs agent-team-mcp | grep "agent-team-mcp ready"
# → … role=viewer registered=['monitor_backend_health'] …
```

## 4. Point Hermes at it

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  agent-team:
    url: "http://localhost:9000/mcp"
    transport: streamable_http          # NOT sse, NOT stdio (FR-002)
    headers:
      Authorization: "Bearer hermes_…"  # the service token from step 3
    # Scope what Hermes loads. Start with the monitor tier:
    tools:
      include: [ping, healthz, monitor_backend_health]
```

Then validate the connection:

```bash
hermes mcp test agent-team
# Should list ping / healthz / the monitor_* tools.
```

`healthz` is the first thing to call — it reports backend reachability, the
resolved role, and which tools registered.

## 5. Token rotation runbook (FR-015b)

Static bearer tokens have **no client-side refresh** — Hermes keeps presenting a
token until you change the config. To rotate (or after a leak):

1. **Issue** a new token (step 3).
2. **Update** `headers.Authorization` in `~/.hermes/config.yaml` to the new value.
3. **Reconnect** Hermes (`hermes mcp remove agent-team && hermes mcp add …`, or
   restart Hermes) so it picks up the new header.
4. **Revoke** the old token — it stops authenticating immediately:
   ```bash
   curl -sX DELETE http://localhost:8000/api/v1/service-tokens/<OLD_TOKEN_ID> \
     -H "Authorization: Bearer <ADMIN_JWT>"   # 204
   ```

Do step 4 **after** step 3, or in-flight Hermes calls will start 401-ing before
the new token is in place. (OAuth — which would handle rotation automatically —
is a deferred hardening item; see PRD §5.)

## 6. Real-time alerts (optional push)

By default Hermes learns about state changes on its **scheduled pull**. To also
get *pinged* the moment something notable happens, point the backend's push
bridge at a Hermes inbound webhook. Set in `.env`:

```bash
PUSH_WEBHOOK_URL=https://<your-hermes-inbound-webhook>
PUSH_WEBHOOK_SECRET=<optional shared secret, sent as X-Push-Secret>
# optional override; default forwards the three below
PUSH_EVENTS=request.failed,request.completed,deploy_health.anomaly_detected
```

Then `docker compose up -d backend`. On boot you'll see `push_bridge_registered`
(or `push_bridge_disabled` when no URL is set).

The bridge forwards a concise payload (`event`, `request_id`, `summary`, …) with
**bounded retry**; if the webhook is down, delivery is dropped after retries and
**Hermes's pull reconciles the gap** — push is best-effort-low-latency, pull is
the durability guarantee (so a missed alert's gap is ≤ the pull interval). A dead
webhook never blocks the platform.

## 7. What Hermes can and cannot do

- **Read freely** (monitor tools) — no approval needed.
- **Mutate** — only by creating a proposal (`POST /api/v1/proposals`, P2). Every
  other state-changing call is rejected 403 by the write-block, *regardless of
  the token's role*. Proposal confirm/reject are human-only.

So even an `admin`-scoped service token cannot deploy, cancel, or change a model
directly — it can only *ask*, and a human approves.
