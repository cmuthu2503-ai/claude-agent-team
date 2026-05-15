# Production Secrets

This directory holds runtime secrets for `docker-compose.prod.yml`. Each file
is mounted into the prod backend container at `/run/secrets/<name>` and read
by `src/utils/secrets.py::read_secret()` which prefers the file path over
matching environment variables.

## Files required for `docker compose -f docker-compose.prod.yml build|up`

| File | Read by | Env var fallback (dev/staging) |
|---|---|---|
| `anthropic_aws_api_key.txt` | `src/agents/executor.py` (Claude Platform on AWS auth) | `ANTHROPIC_AWS_API_KEY` |
| `anthropic_aws_workspace_id.txt` | `src/agents/executor.py` (Claude Platform on AWS workspace) | `ANTHROPIC_AWS_WORKSPACE_ID` |
| `github_token.txt`      | `src/core/github_publisher.py` (research auto-push) | `GITHUB_TOKEN` |
| `firecrawl_api_key.txt` | `src/tools/firecrawl_tools.py` (web search/scrape) | `FIRECRAWL_API_KEY` |
| `jwt_secret.txt`        | `src/main.py` (JWT signing) | `JWT_SECRET` |

Each file should contain **only the secret value** — no quotes, no key name,
no trailing newlines (the reader strips whitespace, but cleaner is better).

## How to obtain the Claude Platform on AWS values

Long-term API key — visible only at generation time:

1. AWS Console → **Claude Platform on AWS** → **API keys** → **Generate long-term key**
2. Copy the key value, paste into `anthropic_aws_api_key.txt`
3. (One-time per account, run before first use:)
   ```bash
   aws iam enable-outbound-web-identity-federation
   ```

Workspace ID — visible any time:

1. AWS Console → **Claude Platform on AWS** → **Workspaces**
2. Copy the ID (format `wrkspc_<alphanumeric>`) into `anthropic_aws_workspace_id.txt`
3. Workspaces are bound to one AWS region. Create yours in `us-east-1` (the
   default `AWS_REGION` baked into the compose files) unless you have a reason to use another.

Full setup walkthrough: [docs/setup-claude-platform-on-aws.md](../docs/setup-claude-platform-on-aws.md).

## Rules

- Files in this directory matching `*.txt` are gitignored — never commit them.
- For dev / staging, use `.env` / `.env.staging` env vars instead — the
  `read_secret()` helper falls back to env vars when the file is absent.
- Rotating a secret? Edit the file and `docker compose -f docker-compose.prod.yml restart backend`.
- For remote deployment, ship these files to the server out-of-band
  (scp, rsync, secrets manager, configuration management). They are NOT in
  the Docker image — they're mounted at runtime.

## Non-secret config (set in compose `environment:`, NOT here)

These are configuration values, not secrets, and live in `docker-compose.prod.yml`
under `environment:`. Override per-deployment by exporting before `up`:

| Variable | Default | Purpose |
|---|---|---|
| `CORS_ORIGINS` | `http://localhost:3020` | Comma-separated list of allowed frontend origins |
| `AWS_REGION` | `us-east-1` | AWS region for Claude Platform on AWS billing/IAM |
| `ANTHROPIC_AWS_INFERENCE_GEO` | `us` | Where inference runs: `us` (1.1× cost, US data centers) or `global` (standard cost, any region) |
| `GITHUB_REPO` | `cmuthu2503-ai/claude-agent-team` | Where the research publisher pushes |
| `GITHUB_BRANCH` | `main` | Same |
