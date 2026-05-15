# Setup: Claude Platform on AWS

This project uses **Claude Platform on AWS** as its single LLM provider for every agent. Anthropic operates the inference stack; AWS provides authentication and billing through AWS Marketplace.

> This is **not** Amazon Bedrock and **not** the direct Anthropic API. The auth, base URL, SDK, and model IDs are all distinct from those products.

## Document Information

| Field | Value |
|---|---|
| Audience | New deployments + onboarding new contributors |
| Status | Required before first agent run |
| Last updated | 2026-05-14 |
| Related | [config/project.yaml](../config/project.yaml), [docker-compose.prod.yml](../docker-compose.prod.yml), [secrets/README.md](../secrets/README.md) |

## 1. One-time AWS account setup

You only do these steps once per AWS account, before the first request goes through.

### 1.1 Sign up for Claude Platform on AWS

1. Open the AWS Console → search for **Claude Platform on AWS**.
2. On the service page, choose **Sign up**.
3. Review the EULA + AWS terms, accept, and **Continue**.
4. Wait a few minutes while AWS provisions your Anthropic organization.
5. Complete the org-setup form when prompted (email, org name, country, intended use).

### 1.2 Enable outbound web identity federation

This is the single most-missed step. Run it once per AWS account:

```bash
aws iam enable-outbound-web-identity-federation
```

If the response says `already enabled`, you're fine.

Without this, every request to Claude Platform on AWS fails with `Outbound web identity federation is disabled for your account`.

Verify:

```bash
aws iam get-outbound-web-identity-federation-info
```

### 1.3 Create a workspace

1. AWS Console → **Claude Platform on AWS** → **Workspaces** → **Create workspace**.
2. Pick a region. **Use `us-east-1`** unless you have a specific reason otherwise — that's the default `AWS_REGION` baked into every docker-compose file in this repo. Workspaces are bound to one region.
3. Note the workspace ID (format `wrkspc_<alphanumeric>`).

### 1.4 Generate a long-term API key

1. AWS Console → **Claude Platform on AWS** → **API keys** → **Generate long-term key**.
2. Copy the value immediately — it's only shown once.
3. Long-term keys expire after 365 days. Set a calendar reminder to rotate before then.

### 1.5 Grant IAM permission to call with the bearer token

The principal that uses the API key needs:

```
aws-external-anthropic:CallWithBearerToken
```

If you're the AWS account owner, your existing admin permissions cover this. If you're handing the key to a non-admin role, attach a policy like:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "aws-external-anthropic:CallWithBearerToken",
      "Resource": "*"
    }
  ]
}
```

## 2. Wire the credentials into this project

You have two options depending on which compose stack you're running.

### 2.1 Dev / staging / demo (env-var style)

Edit `.env` (dev), `.env.staging`, or `.env.demo`:

```bash
ANTHROPIC_AWS_API_KEY=<paste the long-term key>
ANTHROPIC_AWS_WORKSPACE_ID=wrkspc_<your-id>
AWS_REGION=us-east-1
```

The codebase reads `ANTHROPIC_AWS_API_KEY` and `ANTHROPIC_AWS_WORKSPACE_ID` via [src/utils/secrets.py](../src/utils/secrets.py) (Docker secret file first, then env var). `AWS_REGION` is plain env.

### 2.2 Production (Docker secrets style)

`docker-compose.prod.yml` mounts each secret as a file under `/run/secrets/`. Create:

```
./secrets/anthropic_aws_api_key.txt           # paste the key, no trailing newline
./secrets/anthropic_aws_workspace_id.txt      # paste wrkspc_..., no trailing newline
```

These files are gitignored. See [secrets/README.md](../secrets/README.md) for the full list.

## 3. Configuration knobs

All in [config/project.yaml](../config/project.yaml) under `project.llm`:

| Field | Default | Effect |
|---|---|---|
| `provider` | `claude_platform_aws` | Locked — only one provider supported. |
| `region` | `us-east-1` | AWS region for billing/IAM. Override with `$AWS_REGION` env. |
| `inference_geo` | `us` | Pin inference to US data centers. 1.1× pricing multiplier. Set to `global` to allow routing to any Anthropic region at standard price. Override with `$ANTHROPIC_AWS_INFERENCE_GEO` env. |

All 9 agent YAMLs in `config/agents/*.yaml` have `model: claude-opus-4-7`. Change them together if you want a different model — the design here is "one model for all agents."

## 4. Smoke test

After setting the env vars / secret files:

```bash
make dev
# wait ~15s for healthchecks
make health
docker compose logs backend | grep anthropic_aws
```

You should see `anthropic_aws_client_initialized` in the backend logs. If you see `no_anthropic_aws_api_key` or `no_anthropic_aws_workspace_id`, one of the values isn't reaching the container.

Then submit a tiny request through the UI at http://localhost:3000 and confirm an agent run completes (e.g. a quick research request).

## 5. Failure modes and how to read them

| Log line / error | Meaning | Fix |
|---|---|---|
| `no_anthropic_aws_api_key` | Env var / secret file missing | Re-check `.env` or `./secrets/anthropic_aws_api_key.txt` |
| `no_anthropic_aws_workspace_id` | Workspace ID missing | Re-check `.env` or `./secrets/anthropic_aws_workspace_id.txt` |
| `Outbound web identity federation is disabled` | Section 1.2 wasn't run | Run `aws iam enable-outbound-web-identity-federation` |
| `400 ... inference_geo` | Workspace bound to wrong region for `inference_geo` | Re-create workspace in `us-east-1` or set `inference_geo=global` |
| `AccessDenied ... CallWithBearerToken` | IAM principal missing the action | Section 1.5 |
| `401 invalid api key` | Key was revoked or copied with whitespace | Generate a new key; trim the file |

## 6. Cost notes

- Billing flows through AWS Marketplace as **Claude Consumption Units (CCUs)** on your monthly AWS bill, not as a separate Anthropic invoice.
- US inference geo adds a 10% premium. The pricing table in [config/thresholds.yaml](../config/thresholds.yaml) under `cost.pricing.claude-opus-4-7` has that multiplier baked in for in-app cost estimation.
- Budget caps are `$250/day` and `$2500/month` (5× the pre-migration defaults, since Opus 4.7 is roughly 5× the per-token cost of the prior Sonnet setup). Adjust in `thresholds.yaml` if your usage profile differs.

## 7. Rotating the API key

Long-term keys live 365 days. To rotate:

1. Generate a new key in AWS Console → API keys.
2. Replace the value in `.env` (dev) and/or `./secrets/anthropic_aws_api_key.txt` (prod).
3. `docker compose restart backend` (and prod equivalent).
4. After confirming the new key works, revoke the old one from the AWS Console.
