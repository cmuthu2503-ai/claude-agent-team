# agent-team-mcp

MCP server that exposes the **Claude Agent Team** to **Hermes Agent** as tools.
Thin adapter only — it wraps the existing `/api/v1` REST API and holds no
business logic of its own (see `docs/prd-hermes-agent-integration.md`).

## Status

| Task | What | Status |
|------|------|--------|
| HAI-05 | Scaffold: FastMCP server, streamable-HTTP transport, `ping` tool, Dockerfile | ✅ |
| HAI-06 | Compose wiring (`agent-team-mcp` service) | ✅ |
| HAI-07 | Thin REST-adapter client (`backend_client.py`, service-token auth) | ✅ |
| HAI-08 | Tool manifest loader (`manifest.py` + `tools_manifest.yaml`, role-scoped) | ✅ |
| HAI-09 | Health/ping (`healthz`) + Hermes connect docs | ✅ |
| HAI-53 | MCP↔backend contract test + coverage config | ✅ |
| HAI-10+ | Monitor / action tools (append to `tools_manifest.yaml` + `tool_impls.py`) | ☐ |

## Tests & coverage

The MCP service has its **own** harness — it's outside the backend's `--cov=src`
gate (the backend container doesn't even mount `mcp_server/`). Run inside the
running service container (it carries the deps):

```bash
docker compose exec agent-team-mcp sh -c \
  "pip install -q pytest pytest-asyncio pytest-cov && python -m pytest --cov"
```

- `pytest.ini` — `asyncio_mode=auto`, `testpaths=tests`.
- `.coveragerc` — measures `mcp_server/` (this service's own code).
- `tests/test_contract.py` (HAI-53) pins the backend endpoints the adapter
  depends on against the live OpenAPI schema; it **skips** when the backend
  isn't reachable.

## Transport

**Streamable HTTP** (FR-002), the mode Hermes connects to. Configure Hermes in
`~/.hermes/config.yaml`:

```yaml
mcp_servers:
  agent-team:
    url: "http://localhost:9000/mcp"
    transport: streamable_http
    headers:
      Authorization: "Bearer <service-token>"   # minted via POST /api/v1/service-tokens
```

## Run

```bash
# Local (needs `pip install -r requirements.txt`)
python -m mcp_server.server      # or: cd mcp_server && python server.py

# Docker
docker build -t agent-team-mcp ./mcp_server
docker run --rm -p 9000:9000 \
  -e AGENT_TEAM_BACKEND_URL=http://host.docker.internal:8000 \
  -e AGENT_TEAM_SERVICE_TOKEN=<token> \
  agent-team-mcp
```

## Configuration (env)

| Var | Default | Purpose |
|-----|---------|---------|
| `MCP_HOST` | `0.0.0.0` | Bind host for the MCP HTTP endpoint |
| `MCP_PORT` | `9000` | Bind port |
| `AGENT_TEAM_BACKEND_URL` | `http://backend:8000` | Agent Team backend the adapter calls (HAI-07) |
| `AGENT_TEAM_SERVICE_TOKEN` | _(empty)_ | Long-lived service token used to authenticate to the backend |
