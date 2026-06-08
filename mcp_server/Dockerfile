# agent-team-mcp — MCP server (HAI-05). Streamable HTTP transport (FR-002).
# Build context is this directory (mcp_server/). Compose wiring is HAI-06.
FROM python:3.12-slim

WORKDIR /app

# Install deps first for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the flat package (server.py + config.py). Run flat so `import config`
# resolves; the server also supports `python -m mcp_server.server` in dev.
COPY . .

# Hermes connects to this port over HTTP.
EXPOSE 9000

CMD ["python", "server.py"]
