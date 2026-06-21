# Secret Rotation Checklist

## Document Information

| Field | Value |
|---|---|
| **Title** | Rotate exposed secrets |
| **Status** | Action required (operator-executed) |
| **Date** | 2026-06-21 |
| **Why** | Several live secrets were exposed in chat/screenshots. Deleting them from files is NOT enough — they must be **rotated at the provider** (assume compromised). |

> ⚠️ Claude cannot rotate these for you (entering credentials / provider actions are operator-only). Do each step yourself. **Never paste the new secret values back into chat.**

---

## Golden rules

1. **Rotate at the provider**, then update the local env, then restart, then **revoke the old** secret.
2. Secrets live in **env vars / `.env`**, never in committed YAML. Keep it that way.
3. After all rotations: restart `backend`, `agent-team-mcp`, the host `supervisor`, and `hermes`, then verify.

---

## The secrets

### 1. Agent Team service token (`AGENT_TEAM_SERVICE_TOKEN`)
- **Used by:** the MCP server + Hermes to call the backend.
- **Rotate:**
  1. As admin, mint a new developer token: `POST /api/v1/service-tokens` (via the dashboard or an authenticated curl).
  2. Update `AGENT_TEAM_SERVICE_TOKEN` in `.env` (backend/MCP) **and** in the Hermes config that holds it.
  3. Revoke the old token (delete it via the service-tokens admin route/UI).
- **Verify:** `docker compose restart agent-team-mcp` → after backend healthy, MCP logs show `role=developer` (the new #1 retry fix makes this self-heal).

### 2. Claude Platform on AWS key (`ANTHROPIC_AWS_API_KEY` + `ANTHROPIC_AWS_WORKSPACE_ID`)
- **Used by:** every agent LLM call + the hermes-llm-proxy.
- **Rotate:** issue a new key in the Anthropic/AWS workspace console; disable the old. Update `.env`.
- **Verify:** `docker compose restart backend hermes-llm-proxy` → `curl localhost:8000/api/v1/health`; run one PRD generation.

### 3. GitHub token (`GITHUB_TOKEN`)
- **Used by:** the supervisor + Trees-API publisher (commits/pushes, PRs).
- **Rotate:** github.com → Settings → Developer settings → Personal access tokens → generate new (same scopes: repo), then **delete the old**. Update `.env`.
- **Verify:** restart the host supervisor; confirm a publish/commit cycle works (or `gh auth status` if that token is shared).

### 4. Telegram bot token
- **Used by:** Hermes (Telegram gateway).
- **Rotate:** message **@BotFather** → `/revoke` (or `/token`) for the bot → get a new token. Update the Hermes config.
- **Verify:** `hermes gateway restart` → send `/new` in Telegram; bot responds.

### 5. Direct Anthropic API key (`sk-ant-…`, if configured as fallback)
- **Used by:** any direct-API fallback path.
- **Rotate:** console.anthropic.com → API keys → create new, **revoke old**. Update `.env`.
- **Verify:** only if a direct-API path is in use.

---

## Final verification (after all rotations)

```bash
git pull origin main                 # ensure latest (incl. the MCP cold-boot fix)
docker compose restart backend
docker compose ps                    # backend (healthy)
docker compose restart agent-team-mcp hermes-llm-proxy
# restart host supervisor (make supervisor-bg) and: hermes gateway restart
curl -s localhost:8000/api/v1/health
```

- [ ] #1 service token rotated + old revoked
- [ ] #2 AWS key rotated + old disabled
- [ ] #3 GitHub token rotated + old deleted
- [ ] #4 Telegram token rotated (BotFather)
- [ ] #5 sk-ant key rotated (if used)
- [ ] All services restarted + healthcheck green
- [ ] MCP `healthz` shows `resolved_role: developer`
- [ ] Hermes responds in Telegram

## Going forward
- Keep secrets in `.env` / provider config only — never in committed files or chat.
- Consider provider-side expiry/rotation reminders so this isn't manual next time.
