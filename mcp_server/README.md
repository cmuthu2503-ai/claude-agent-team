# agent-team-mcp

MCP server that exposes the **Claude Agent Team** to **Hermes Agent** as tools.
Thin adapter only — it wraps the existing `/api/v1` REST API and holds no
business logic of its own (see `docs/prd-hermes-agent-integration.md`).

## Status

| Task | What | Status |
|------|------|--------|
| HAI-05 | Scaffold: FastMCP server, streamable-HTTP transport, `ping` tool, Dockerfile | ✅ this |
| HAI-06 | Compose wiring (`agent-team-mcp` service) | ☐ |
| HAI-07 | Thin REST-adapter client (service-token auth) | ☐ |
| HAI-08 | Tool manifest loader (role-scoped tool exposure) | ☐ |
| HAI-09 | Health/ping + Hermes connect docs | ☐ |
| HAI-10+ | Monitor / action tools | ☐ |

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
