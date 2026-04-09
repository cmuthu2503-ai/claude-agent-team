# Production Secrets

This directory holds runtime secrets for `docker-compose.prod.yml`. Each file
is mounted into the prod backend container at `/run/secrets/<name>` and read
by `src/utils/secrets.py::read_secret()` which prefers the file path over
matching environment variables.

## Files required for `docker compose -f docker-compose.prod.yml build|up`

| File | Read by | Env var fallback (dev/staging) |
|---|---|---|
| `anthropic_api_key.txt` | `src/agents/executor.py` (Claude direct) | `ANTHROPIC_API_KEY` |
| `openai_api_key.txt`    | `src/agents/executor.py` (GPT-5.4 / o4-mini) | `OPENAI_API_KEY` |
| `aws_access_key_id.txt` | `src/agents/executor.py` (Bedrock client) | `AWS_ACCESS_KEY_ID` |
| `aws_secret_access_key.txt` | `src/agents/executor.py` (Bedrock client) | `AWS_SECRET_ACCESS_KEY` |
| `github_token.txt`      | `src/core/github_publisher.py` (research auto-push) | `GITHUB_TOKEN` |
| `firecrawl_api_key.txt` | `src/tools/firecrawl_tools.py` (web search/scrape) | `FIRECRAWL_API_KEY` |
| `jwt_secret.txt`        | `src/main.py` (JWT signing) | `JWT_SECRET` |

Each file should contain **only the secret value** — no quotes, no key name,
no trailing newlines (the reader strips whitespace, but cleaner is better).

## Rules

- Files in this directory matching `*.txt` are gitignored — never commit them.
- For dev / staging, use `.env` / `.env.staging` env vars instead — the
  `read_secret()` helper falls back to env vars when the file is absent.
- Rotating a secret? Just edit the file and `docker compose -f docker-compose.prod.yml restart backend`.
- For remote deployment, ship these files to the server out-of-band
  (scp, rsync, secrets manager, configuration management). They are NOT in
  the Docker image — they're mounted at runtime.

## Non-secret config (set in compose `environment:`, NOT here)

These are configuration values, not secrets, and live in `docker-compose.prod.yml`
under `environment:`. Override per-deployment by exporting before `up`:

| Variable | Default | Purpose |
|---|---|---|
| `CORS_ORIGINS` | `http://localhost:3020` | Comma-separated list of allowed frontend origins |
| `AWS_REGION` | `us-east-1` | Bedrock region |
| `BEDROCK_MODEL_ID` | Claude Sonnet 4 | Bedrock model used by all agents in bedrock mode |
| `OPENAI_GPT5_MODEL_ID` | `gpt-5.4` | OpenAI model the GPT-5.4 button uses |
| `OPENAI_O3_MODEL_ID` | `o4-mini` | OpenAI model the o4-mini button uses |
| `ANTHROPIC_OPUS_MODEL_ID` | `claude-opus-4-6` | Direct Anthropic model for the Opus button |
| `ANTHROPIC_SONNET_MODEL_ID` | `claude-sonnet-4-6` | Direct Anthropic model for the Sonnet button |
| `GITHUB_REPO` | `cmuthu2503-ai/claude-agent-team` | Where the research publisher pushes |
| `GITHUB_BRANCH` | `main` | Same |
