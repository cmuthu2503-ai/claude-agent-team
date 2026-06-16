# Setup: Hermes LLM Proxy (cloud Claude brain, no local model)

Run **Hermes's brain on Claude Platform on AWS** instead of a local model — so the
Mac Studio stays fast (no LM Studio) and tool-calling is reliable (cloud Claude),
reusing the **same AWS credentials** the agent team already uses. No OpenAI account
is involved: "OpenAI-compatible" refers only to the request/response JSON shape.

## Document Information

| Field | Value |
|-------|-------|
| Created | 2026-06-11 |
| Status | Living |
| Covers | HLP (Hermes LLM proxy) |

---

## 1. Why this exists

Hermes speaks the **OpenAI Chat Completions** wire format (`provider: custom`,
`api_mode: chat_completions`). The Claude Platform on AWS speaks the **Anthropic
Messages** API over a different endpoint with AWS auth — and the AWS key is **not**
a direct-Anthropic `sk-ant-` key, so you can't point Hermes at it directly.

`hermes-llm-proxy` is a thin translator:

```
Hermes ──POST /v1/chat/completions (OpenAI)──▶ proxy ──AsyncAnthropicAWS.messages.create()──▶ Claude Platform on AWS
       ◀────────── OpenAI response ───────────       ◀────────── Anthropic response ──────────
```

It translates messages, **tools** (`parameters`⇄`input_schema`, `tool_use`⇄`tool_calls`,
`tool_result` round-trip), `tool_choice`, system-prompt hoisting, and usage — so
Hermes gets cloud-Claude tool-calling with zero local compute.

## 2. Bring it up

It runs in the dev stack (port 8088), reusing `.env`'s
`ANTHROPIC_AWS_API_KEY` / `ANTHROPIC_AWS_WORKSPACE_ID` / `AWS_REGION`:

```bash
docker compose up -d hermes-llm-proxy
docker compose logs hermes-llm-proxy | tail -5
curl -s http://localhost:8088/healthz
# → {"status":"ok","model":"claude-opus-4-8","inference_geo":"us","creds_present":true}
```

Source is bind-mounted (`./proxy:/app`), so edits need only
`docker compose restart hermes-llm-proxy` — no rebuild.

### Config (env, all optional)

| Var | Default | Notes |
|---|---|---|
| `PROXY_DEFAULT_MODEL` | `claude-opus-4-8` | Model for Hermes's brain. Must be **provisioned on your AWS workspace** — `claude-opus-4-8` is confirmed; `claude-sonnet-4-7` 404s on this workspace. Override if a cheaper model is available. |
| `ANTHROPIC_AWS_INFERENCE_GEO` | `us` | Passed per-call (matches the agent team). |
| `PROXY_DEFAULT_MAX_TOKENS` | `4096` | Anthropic requires `max_tokens`; used when the request omits it. |
| `PROXY_API_KEY` | _(empty)_ | Optional bearer to guard the proxy. Empty = no auth (localhost only). |

## 3. Point Hermes at it

In `~/.hermes/config.yaml`:

```yaml
model:
  default: agent-team-router        # any name; the proxy maps non-claude-* names to PROXY_DEFAULT_MODEL
  provider: custom
  base_url: http://localhost:8088/v1
  api_key: lm-studio                # placeholder unless you set PROXY_API_KEY (then use that value)
  api_mode: chat_completions
```

Then **stop LM Studio** (the resource win), restart Hermes, and start a **fresh
session** so the operator persona reloads:

```bash
hermes gateway restart
```

> Keep the operator persona in `agent.personalities.helpful` (see
> [setup-hermes-integration.md](setup-hermes-integration.md) §8) — that's what makes
> the model actually call tools. The proxy only changes *which* model runs.

## 4. Verify end-to-end

Quick proof the proxy tool-calls (no Hermes needed):

```bash
curl -s http://localhost:8088/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model":"agent-team-router",
    "messages":[
      {"role":"system","content":"You are an operator. When asked to create a project, call create_project."},
      {"role":"user","content":"create a project named Foo"}
    ],
    "tools":[{"type":"function","function":{"name":"create_project","description":"Create a project","parameters":{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}}}],
    "tool_choice":"auto","max_tokens":512
  }' | python3 -m json.tool
# → choices[0].finish_reason == "tool_calls", tool_calls[0].function.name == "create_project"
```

Then from Telegram: *"set the project brief of testProject to …"* → should call the
tool reliably.

## 5. Cost note

`claude-opus-4-8` is pricier than Sonnet, but Hermes's routing prompts are tiny
(deciding which tool to call), so the per-message cost is small. If your workspace
provisions a Sonnet/Haiku id, set `PROXY_DEFAULT_MODEL` to it for cheaper routing.

## 6. Notes / limitations

- **Streaming:** `stream:true` is supported via a single-chunk SSE shim (the full
  answer arrives as one chunk + `[DONE]`), not token-by-token. Fine for Hermes;
  true streaming is a possible later enhancement.
- **`inference_geo`** is passed per-call and is rejected by models older than 4.6 —
  keep `PROXY_DEFAULT_MODEL` at a 4.6+ model (Opus 4.8 is fine).
- **Auth:** localhost-only by default. Set `PROXY_API_KEY` if you expose the port.
