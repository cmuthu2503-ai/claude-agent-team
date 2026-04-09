# Docker Deployment Assessment
# Agent Team — Pre-Push Audit, Self-Containment Verification, and Remote-Deploy Guide

---

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 1.0 |
| Created Date | 2026-04-08 |
| Last Updated | 2026-04-08 |
| Status | Current |
| Scope | Verify that the latest code is packaged into Docker images, explain what "self-contained" means for this repo, and document the path from workstation → Docker Hub → remote server |
| Images audited | `cmuthu2503/agent-team-backend:latest`, `cmuthu2503/agent-team-frontend:latest` |

---

## 1. Executive Summary

The two prod images (`cmuthu2503/agent-team-backend:latest` and `cmuthu2503/agent-team-frontend:latest`) are **self-contained for code and dependencies** and are ready to push to Docker Hub. A remote server running them needs **Docker + 10 files** (1 compose file + 7 secret files + 1 optional `.env` + 1 placeholder), no source clone, no Python toolchain, no Node.

The repo also has a **third** Docker service — the `agent-team-supervisor` stack defined in `docker-compose.supervisor.yml`. It has materially **different** self-containment characteristics from the main app and lives on a **different** deployment axis: it is a local / CI-host orchestration tool that watches the `deployment_states` DB table and drives `build → staging → prod → rebuild dev` pipelines, rolling back on failure via `git revert`. It is **not** part of the remote-server bill of materials — a remote server that only pulls prebuilt images has no reason to run it. Full coverage is in §11.

Before this assessment was run, the deployment pipeline had **7 latent problems** that would have silently broken a remote deploy. All 7 were fixed during the audit:

| # | Problem | Why it mattered | Fixed |
|---|---|---|---|
| 1 | No `.dockerignore` | `.env` (with the live OpenAI key), `secrets/`, `data/` were all in the build context — any future Dockerfile edit using `COPY .` would bake them into image layers | ✅ Created |
| 2 | Frontend prod build broken (4 TypeScript errors) | `npm run build` exited with code 2 — no fresh frontend image could be produced at all; Vite dev mode masked the errors | ✅ Fixed |
| 3 | Backend image 4 hours stale, frontend image 1 day stale | Running dev containers appeared healthy due to bind mounts of host source, but the image layers themselves were missing today's work. A `docker push` would have sent old code. | ✅ Rebuilt both as prod images with today's code baked in |
| 4 | Code read API keys from env vars only; prod compose mounted them as secret files | Prod was a dead-letter config — compose mounted files but code ignored them. The OpenAI key would never reach the prod backend. | ✅ Added `src/utils/secrets.py` helper, threaded through all 7 secret read sites |
| 5 | CORS origins hardcoded to `http://localhost:3020` for production environment | Any remote deploy would immediately break — the deployed frontend would be unable to call the backend from any hostname | ✅ `CORS_ORIGINS` env var with localhost fallback in `src/main.py` |
| 6 | `docker-compose.prod.yml` had no `image:` directive and used the same auto-generated tag as dev | (a) Couldn't `docker pull` the prod image by name on a remote. (b) Every prod build clobbered the dev tag, making `docker compose up -d` on dev quietly run prod containers. | ✅ Added `image: cmuthu2503/...:latest` on both services, kept `build:` so both `pull` and `build` work |
| 7 | `docker-compose.prod.yml` secrets section listed only 3 of the 7 secrets the code needs | `openai_api_key`, `aws_access_key_id`, `aws_secret_access_key`, `firecrawl_api_key` were never wired into prod — all four LLM providers plus Firecrawl web tools would have been disabled on the deployed stack | ✅ All 7 secrets wired in, with 10 non-secret env vars (CORS_ORIGINS, model overrides, etc.) exposed for per-deploy override |

**One pre-existing bug** was surfaced by the standalone verification run and is NOT yet fixed: the admin bootstrap race under `uvicorn --workers 2`. See §9.

---

## 2. The Mental Model — Image Layers and Volume Mounts

Understanding why the audit's findings matter requires understanding how Docker stores and runs images. This section is the ground truth everything else in this document refers back to.

### 2.1 An image is an immutable stack of filesystem layers

A Docker image is a stack of read-only filesystem snapshots. Each instruction in a Dockerfile that modifies the filesystem creates one layer. When you run a container, Docker unions the layers into a single view, plus a thin writable layer on top (the container's own scratch space).

Example from `Dockerfile.backend` (prod target):

```
Dockerfile instruction                   Layer created
────────────────────────────────         ───────────────────────────────
FROM python:3.12-slim                 →  Layer 1: Python 3.12 on Debian slim
RUN apt-get install ...               →  Layer 2: Pango, Cairo, Pillow system libs
COPY pyproject.toml README.md ./      →  Layer 3: pyproject.toml + README
RUN pip install --no-cache-dir .      →  Layer 4: anthropic, openai, fastapi, ~80 more
COPY src/ src/                        →  Layer 5: YOUR BACKEND CODE
COPY config/ config/                  →  Layer 6: YAML configs
RUN mkdir -p /app/data /app/reports   →  Layer 7: empty data/reports/backups dirs
+ CMD [...]                              + container metadata (not a layer)
```

**Every layer is immutable.** Once the image is built, none of these layers change. When you push `cmuthu2503/agent-team-backend:latest` to Docker Hub and pull it on a remote server in 6 months, you get byte-for-byte the same layers — same Python interpreter, same pip packages, same source code at `/app/src/`, same configs at `/app/config/`.

### 2.2 A volume mount is a runtime overlay that hides layer content

At container start time, you can tell Docker "mount this host directory at this path inside the container". This is called a **bind mount**. The files at that path inside the image are still there, but the mount **hides** them with whatever is on the host right now.

```
Without any mount:                    With `-v ./src:/app/src`:

┌──────────────────────────┐          ┌──────────────────────────┐
│ Image Layer 5: /app/src   │          │ Image Layer 5: /app/src   │
│   (backend code baked in  │          │   (hidden by the mount)   │
│    when image was built)  │          └──────────────────────────┘
└──────────────────────────┘                    ↑
          ↓                                 overlaid by
                                                ↓
  What the container sees:           ┌──────────────────────────┐
  → image's baked-in code            │ Host: ./src               │
                                     │   (live source files)     │
                                     └──────────────────────────┘

                                     What the container sees:
                                     → host's live source files,
                                       NOT what was baked in
```

**This is exactly why the dev containers appeared to run the latest code even when the images were hours or a day stale.** The bind mounts of `./src` and `./frontend/src` overlaid the host's live files on top of the image layers. Remove the mounts and the stale image content reappears.

### 2.3 Named volumes are different from bind mounts

The prod compose file uses **named volumes** (not bind mounts) for data persistence:

```yaml
volumes:
  - agent-team-prod-data:/app/data          # named volume
  - agent-team-prod-reports:/app/reports    # named volume
  - agent-team-prod-backups:/app/backups    # named volume
```

Docker manages named volumes as opaque storage (on Linux: `/var/lib/docker/volumes/...`). Unlike bind mounts, they don't reference a host path. They are the correct way to persist data across container rebuilds:

- `docker compose down` stops and removes the container → named volume **survives**
- `docker compose up` creates a new container → it attaches to the same named volume → **data is still there**
- `docker compose down -v` → volumes deleted (destructive)

In the prod stack, the SQLite database at `/app/data/agent_team.db` lives in the `agent-team-prod-data` volume. Users, requests, cost records, and research artifacts all survive restarts and image upgrades.

---

## 3. Dev vs Prod Configuration — What's Baked In vs What's Mounted

| Thing | `docker-compose.yml` (dev) | `docker-compose.prod.yml` (prod) |
|---|---|---|
| **Build target** | `target: dev` (stage 1 of Dockerfile) | `target: prod` (stage 2) |
| **Backend source (`src/`)** | `- ./src:/app/src` **bind mount** → host's live code wins | **NO mount** — uses `/app/src` baked into Layer 5 |
| **Config files (`config/`)** | `- ./config:/app/config` **bind mount** | **NO mount** — uses baked-in Layer 6 |
| **Frontend source** | `- ./frontend/src:/app/src` **bind mount** → Vite hot-reloads host files | **N/A** — frontend prod image is nginx serving pre-compiled JS from `/usr/share/nginx/html/` |
| **Data (SQLite, reports)** | Named volumes `agent-team-{data,reports,backups}` | Named volumes `agent-team-prod-{data,reports,backups}` |
| **Secrets** | `env_file: .env` — read from host file as env vars | `secrets:` — mounted as files at `/run/secrets/<name>` |
| **Image tag** | `claude-agent-team-{backend,frontend}:latest` (compose auto-naming) | `cmuthu2503/agent-team-{backend,frontend}:latest` (explicit `image:` directive, distinct from dev) |
| **Uvicorn CMD** | `uvicorn src.main:app --reload --host 0.0.0.0 --port 8000` | `uvicorn src.main:app --workers 2 --host 0.0.0.0 --port 8000` |
| **Runs as** | `root` (dev convenience) | `agent` non-root user (prod security) |

**Summary:** Dev relies on 8 different bind mounts — it's a "thin image + fat local workspace" setup, designed for fast iteration where you edit a file and see the change immediately. Prod has **zero source-code bind mounts** — the image *is* the code. The only things mounted at runtime are (a) persistent data named volumes and (b) the secrets directory, neither of which contain application code.

---

## 4. What Was Fixed During the Audit

### 4.1 `.dockerignore` (NEW)

Created at the repo root with exclusions for:
- **Secrets**: `.env`, `.env.*`, `secrets/`, `*.pem`, `*.key`
- **Runtime state**: `data/`, `backups/`, `reports/`, `error/`, `*.db`, `*.sqlite*`
- **VCS / IDE noise**: `.git/`, `.vscode/`, `.idea/`, `.DS_Store`
- **Python build cache**: `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`
- **Node artifacts**: `frontend/node_modules/`, `frontend/dist/`, `node_modules/`
- **Docs and tests** (not needed at runtime): `docs/`, `tests/`, `src/tests/`, `**/test_*.py`
- **Compose/Dockerfiles** (no need to ship into images)

Critical note: `README.md` is **not** excluded because `Dockerfile.backend` runs `COPY pyproject.toml README.md ./` and hatchling needs it at wheel-build time.

### 4.2 Frontend TypeScript errors blocking `npm run build`

Vite dev mode does not run the strict TypeScript compiler; `npm run build` runs `tsc -b && vite build` with `noUnusedLocals: true` and `noUnusedParameters: true` from `tsconfig.app.json`. Four errors were masked by dev mode and only surfaced when the prod build was attempted:

| File | Line | Fix |
|---|---|---|
| `frontend/src/components/ui/AgentCard.tsx` | 13 | Removed unused `agentId` prop from the destructure (kept in the interface) |
| `frontend/src/components/ui/MarkdownRenderer.tsx` | 24 | Regex callback param `lang` → `_lang` (TS convention for intentionally unused) |
| `frontend/src/components/ui/MarkdownRenderer.tsx` | 99 | Regex callback param `indent` → `_indent` |
| `frontend/src/pages/CommandCenter.tsx` | 685 | Removed dead `const name = data.display_name || ...` line that was never referenced in `_eventMessage` |

**Lesson:** never claim "the prod build works" without actually running `docker compose -f docker-compose.prod.yml build` end-to-end. Vite dev will happily serve code the prod build rejects.

### 4.3 Stale image layers

Before the audit:

| Image | Last built | Today's code baked in? |
|---|---|---|
| `claude-agent-team-backend:latest` | 2026-04-08 14:06 | ✅ openai package + provider work, ❌ cancel/delete/CANCELLED (added later) |
| `claude-agent-team-frontend:latest` | 2026-04-07 20:35 | ❌ nothing from today — all 5-button selector, Diagrams page, Cancel/Delete, MermaidViewer missing |

Running dev containers appeared to work only because the bind mounts of `./src` and `./frontend/src` overlaid the host's live code. A `docker push` at that point would have sent yesterday's frontend to anyone who pulled.

After the audit: fresh prod images built with every layer reflecting the current HEAD.

### 4.4 Secrets handling: file-mode vs env-mode

The prod compose file listed a `secrets:` block with 3 entries, but every code site read credentials from `os.getenv()`, not from `/run/secrets/<name>`. This meant the prod compose was half-wired — even if you populated `./secrets/anthropic_api_key.txt`, the backend would ignore it.

Fix: new helper at `src/utils/secrets.py`:

```python
def read_secret(secret_name: str, env_var: str, default: str = "") -> str:
    """Prefer /run/secrets/<name>; fall back to env var; then default."""
    secret_path = SECRETS_DIR / secret_name  # /run/secrets/ by default
    if secret_path.exists() and secret_path.is_file():
        value = secret_path.read_text(encoding="utf-8").strip()
        if value:
            return value
    return os.getenv(env_var, default)
```

Threaded through every secret read site:

| File | Secret | Env var fallback |
|---|---|---|
| `src/main.py` | `jwt_secret` | `JWT_SECRET` |
| `src/agents/executor.py` | `anthropic_api_key` | `ANTHROPIC_API_KEY` |
| `src/agents/executor.py` | `openai_api_key` | `OPENAI_API_KEY` |
| `src/agents/executor.py` | `aws_access_key_id` | `AWS_ACCESS_KEY_ID` (also re-exported to env for boto3's credential chain) |
| `src/agents/executor.py` | `aws_secret_access_key` | `AWS_SECRET_ACCESS_KEY` (same) |
| `src/core/github_publisher.py` | `github_token` | `GITHUB_TOKEN` |
| `src/tools/firecrawl_tools.py` | `firecrawl_api_key` | `FIRECRAWL_API_KEY` |

The helper makes the same code work in dev (env vars from `.env` file) and prod (Docker secrets mounted as files), with no if/else branches at call sites.

### 4.5 CORS configurability

`src/main.py` previously hardcoded a per-environment origins map:

```python
origins_map = {
    "production": ["http://localhost:3020"],   # wrong for any remote deploy
    ...
}
```

Replaced with env-var-driven logic that falls back to the old defaults for local multi-env development:

```python
_cors_env = os.getenv("CORS_ORIGINS", "").strip()
if _cors_env:
    cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
else:
    cors_origins = _cors_default_map.get(ENVIRONMENT, ["http://localhost:3000"])
```

On the remote, set `CORS_ORIGINS=https://your-domain.com` in the prod `.env` (next to the compose file) and the backend will accept the real frontend origin. Wildcards are accepted by Starlette but break credentialed requests; prefer explicit hostnames.

### 4.6 Image naming & tag collision

Before: both compose files auto-generated `claude-agent-team-backend:latest` and `claude-agent-team-frontend:latest`. The last build to run — dev or prod — won the tag, so a prod rebuild would silently clobber the dev tag and vice versa.

After: `docker-compose.prod.yml` has explicit `image: cmuthu2503/agent-team-{backend,frontend}:latest` directives. Dev still auto-names as `claude-agent-team-*`. The two stacks no longer interfere.

Both `image:` and `build:` are present on the prod services so:

- `docker compose -f docker-compose.prod.yml build` — builds locally, tags as `cmuthu2503/...`
- `docker compose -f docker-compose.prod.yml pull`  — pulls from Docker Hub (remote workflow)
- `docker compose -f docker-compose.prod.yml up` — uses the named image; builds if not cached

### 4.7 Missing secrets and env vars in prod compose

Before: `docker-compose.prod.yml` secrets section:
```yaml
secrets:
  - anthropic_api_key
  - github_token
  - jwt_secret
```

OpenAI, AWS (Bedrock), and Firecrawl were nowhere to be found — so even after fixing the secret-read helper, the prod stack would have OpenAI GPT-5.4, o4-mini, Bedrock, and web tools all disabled.

After: all 7 secrets wired in, plus 10 non-secret env vars exposed with sensible defaults that can be overridden per deploy:

```yaml
environment:
  - ENVIRONMENT=production
  - LOG_LEVEL=WARNING
  - CORS_ORIGINS=${CORS_ORIGINS:-http://localhost:3020}
  - AWS_REGION=${AWS_REGION:-us-east-1}
  - BEDROCK_MODEL_ID=${BEDROCK_MODEL_ID:-anthropic.claude-sonnet-4-20250514-v1:0}
  - GITHUB_REPO=${GITHUB_REPO:-cmuthu2503-ai/claude-agent-team}
  - GITHUB_BRANCH=${GITHUB_BRANCH:-main}
  - OPENAI_GPT5_MODEL_ID=${OPENAI_GPT5_MODEL_ID:-gpt-5.4}
  - OPENAI_O3_MODEL_ID=${OPENAI_O3_MODEL_ID:-o4-mini}
  - ANTHROPIC_OPUS_MODEL_ID=${ANTHROPIC_OPUS_MODEL_ID:-claude-opus-4-6}
  - ANTHROPIC_SONNET_MODEL_ID=${ANTHROPIC_SONNET_MODEL_ID:-claude-sonnet-4-6}
secrets:
  - anthropic_api_key
  - openai_api_key
  - aws_access_key_id
  - aws_secret_access_key
  - github_token
  - firecrawl_api_key
  - jwt_secret
```

A matching `secrets/README.md` was created describing what files to drop into `./secrets/`.

---

## 5. Live Proof — Running the Prod Backend Standalone

To prove the image is self-contained, the prod backend was started with **zero compose, zero env file, zero volume mounts, zero secrets**, and the standalone container was poked for real evidence:

```bash
docker run --rm -d \
  --name audit-standalone \
  -p 8099:8000 \
  cmuthu2503/agent-team-backend:latest
```

### 5.1 Result 1 — Health endpoint responded

```
$ curl http://localhost:8099/api/v1/health
{"status":"healthy","version":"0.1.0","environment":"development"}
```

The container started uvicorn, loaded all Python modules, initialized FastAPI, and served a real HTTP response — all from files baked into the image layers.

### 5.2 Result 2 — Zero bind mounts on the running container

```
$ docker inspect audit-standalone \
    --format '{{range .Mounts}}{{.Type}}  {{.Source}} → {{.Destination}}{{println}}{{end}}'
(empty output)
```

No bind mounts of any kind. No `./src` overlay. No `./config` overlay. No `./secrets/` directory. The container is running purely from image layers.

### 5.3 Result 3 — Today's new routes are in the OpenAPI spec

```
$ curl http://localhost:8099/api/v1/openapi.json | jq '.paths | keys[]' | grep request
GET    /api/v1/cost/requests/{request_id}
GET    /api/v1/requests/{request_id}
DELETE /api/v1/requests/{request_id}           ← added this session
POST   /api/v1/requests/{request_id}/cancel    ← added this session
POST   /api/v1/requests/{request_id}/retry
GET    /api/v1/requests/{request_id}/stories
```

33 API paths total. The OpenAPI spec served by the standalone container confirms every route from this session's cancel/delete work is present — and the OpenAPI spec is generated at startup from the decorators in `src/api/routes/requests.py`, which means those files are physically in the image.

### 5.4 Conclusion

The prod image is **complete for code and dependencies**. Running it requires only a container runtime and the runtime inputs described in §7.

---

## 6. What's Inside the Prod Images

*This section covers the two images that ship to a remote server: the backend (§6.1) and frontend (§6.2). The third service in this repo — the supervisor stack — is a local / CI-host tool with different self-containment characteristics and is documented separately in §11.*

### 6.1 Backend: `cmuthu2503/agent-team-backend:latest` (836 MB)

```
┌────────────────────────────────────────────────────────────────┐
│ Layer 1: Debian slim + Python 3.12                             │
│ Layer 2: System libs (Pango, Cairo, Pillow, fontconfig)        │  ← for weasyprint PDF
│ Layer 3: pyproject.toml + README.md                            │
│ Layer 4: Installed Python packages (~80 total):                │  ← from pip install
│   ├── anthropic 0.92.0 (Claude direct + Bedrock)               │
│   ├── openai 2.31.0 (GPT-5.4 + o4-mini)                        │
│   ├── fastapi 0.135.3, uvicorn 0.44.0                          │
│   ├── pydantic 2.12.5                                          │
│   ├── aiosqlite 0.22.1, alembic 1.18.4, SQLAlchemy 2.0.49      │
│   ├── python-jose, bcrypt (auth)                               │
│   ├── weasyprint 68.1, python-pptx 1.0.2 (reports)             │
│   ├── firecrawl-py 4.22.1 (web tools)                          │
│   ├── structlog, slowapi, httpx, boto3, and ~70 others         │
│ Layer 5: /app/src/ — all backend code                          │
│   ├── main.py, agents/, api/, auth/, config/, core/,           │
│   │   state/, tools/, utils/, workflows/, models/,             │
│   │   github/, notifications/, security/                       │
│ Layer 6: /app/config/ — YAML configs                           │
│   ├── project.yaml, teams.yaml, workflows.yaml,                │
│   │   tools.yaml, thresholds.yaml, prompt_templates.yaml,      │
│   │   agents/{backend,frontend,code_reviewer,...}.yaml         │
│ Layer 7: empty /app/data, /app/reports, /app/backups dirs      │
│ + CMD: uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 2 │
│ + USER agent (non-root)                                        │
└────────────────────────────────────────────────────────────────┘
```

**Verified contents of `/app` in the standalone container:**

```
README.md        backups/      config/     data/
pyproject.toml   reports/      src/
```

**Not present (intentionally):**
- No `.env` file
- No `secrets/` directory
- No `docs/` directory (excluded by `.dockerignore`)
- No `tests/` directory (excluded by `.dockerignore`)
- No `frontend/` (backend image doesn't need it)
- No `.git/` metadata

### 6.2 Frontend: `cmuthu2503/agent-team-frontend:latest` (93 MB)

```
┌────────────────────────────────────────────────────────────────┐
│ Layer 1: Alpine Linux + nginx                                  │
│ Layer 2: /usr/share/nginx/html/index.html                      │
│ Layer 3: /usr/share/nginx/html/assets/index-BdZ8LAzy.js        │  ← bundled
│   ├── React 19 + all components                                    React app
│   ├── All pages: CommandCenter, RequestDetail, StoryBoard,
│   │   PromptStudio, Diagrams (MermaidViewer), History,
│   │   Releases, TeamStatus, CostDashboard, UserManagement,
│   │   Login
│   ├── 5-button provider selector (Opus/Sonnet/Bedrock/GPT-5.4/o4-mini)
│   ├── Cancel/Delete button handlers
│   ├── Zustand auth & theme stores
│   ├── TanStack Query, Tailwind CSS (compiled)
│   └── lucide-react icons
│ Layer 4: /etc/nginx/conf.d/default.conf                        │
│   proxy /api/ → http://backend:8000                            │
│   proxy /ws/  → ws://backend:8000                              │
│   SPA fallback: all unknown routes → index.html                │
│ + CMD: nginx -g 'daemon off;'                                  │
└────────────────────────────────────────────────────────────────┘
```

Total client payload: **392 KB** (compressed index-BdZ8LAzy.js + index.html + assets). Mermaid (~2 MB) is deliberately not in the bundle — the Diagrams page lazy-loads it from jsDelivr CDN on first visit, cached by the browser thereafter.

**Verified strings in the bundled JS** (proof of self-contained code):
- `"Mermaid Diagram Viewer"` — Diagrams page
- `"openai_gpt5"`, `"openai_o3"`, `"anthropic_opus"`, `"anthropic_sonnet"` — 5-button selector
- `"/diagrams"` — new route

---

## 7. What the Remote Machine Still Needs (And Why That's Fine)

The images are complete for code, but four categories of things are deliberately **NOT** baked in. Each has a good reason to be a runtime input:

| Category | Examples | Why NOT in image | Where it comes from on the remote |
|---|---|---|---|
| **Secrets** | `anthropic_api_key`, `openai_api_key`, `aws_*`, `github_token`, `firecrawl_api_key`, `jwt_secret` | Security — baking them in means anyone who pulls the image from Docker Hub (especially if public) gets the keys. Also makes key rotation a rebuild operation instead of a restart. | `./secrets/*.txt` on the remote, mounted via the compose `secrets:` block at `/run/secrets/<name>` |
| **Per-deploy config** | `CORS_ORIGINS`, `GITHUB_REPO`, model overrides | These change per environment and per deploy. Baking them in would force a rebuild for every target (staging, prod, regional). | Env vars in `.env` file next to the compose on the remote, or exported before `docker compose up` |
| **Persistent data** | SQLite DB, research artifacts, backups | Data must survive container rebuilds. If baked in, every image upgrade would wipe user accounts, request history, cost records, research outputs. | Docker **named volumes** — `agent-team-prod-{data,reports,backups}`. Managed by Docker; persist across `down`/`up` cycles. |
| **Runtime TLS / reverse proxy** | Domain certs, HTTPS termination | Per-environment concern; infrastructure, not application | nginx/Caddy/Traefik in front of the compose stack on the remote |

### 7.1 Minimum bill of materials for the remote machine

```
~/agent-team/                          ← any directory on the remote
├── docker-compose.prod.yml            ← 1 file, ~4 KB, scp from workstation
├── .env                               ← OPTIONAL, ~100 bytes
│                                        Example contents:
│                                          CORS_ORIGINS=https://agent-team.example.com
│                                          GITHUB_REPO=cmuthu2503-ai/claude-agent-team
│                                          BEDROCK_MODEL_ID=anthropic.claude-sonnet-4-20250514-v1:0
│
└── secrets/                           ← 7 files, ~600 bytes total
    ├── anthropic_api_key.txt
    ├── openai_api_key.txt
    ├── aws_access_key_id.txt
    ├── aws_secret_access_key.txt
    ├── github_token.txt
    ├── firecrawl_api_key.txt
    └── jwt_secret.txt
```

**That's it.** 1 compose file + 7 secret files + 1 optional env file = ~10 files, ~5 KB total. **No source clone. No pip install. No npm install. No Python toolchain. No Node. No build tools. Nothing but Docker + these 10 files.**

### 7.2 What does NOT belong on the remote

One thing to be explicit about, because it's counter-intuitive: **the supervisor stack (`docker-compose.supervisor.yml`) is not part of the remote bill of materials**. If you copy this compose file to the remote server and try to run it there, one of two things will happen:

1. It will fail because `.:/app/project` tries to bind-mount a directory that isn't a git checkout of the repo.
2. If you *do* clone the repo on the remote just to satisfy the bind mount, the supervisor will start polling — but it has no valid work to do, because the backend / frontend / code-writer agents that populate the `deployment_states` table aren't the ones running there. You'd be running an orchestrator with nothing to orchestrate.

The supervisor is a **build-machine / CI-host concern**. It belongs next to the developer workstation or CI runner that produces and pushes images, not next to the consumer that only pulls and runs them. Full rationale and the two deployment models are in §11.5.

The remote only needs the 10 files listed in §7.1. Do not ship the supervisor.

---

## 8. Workflow — Workstation to Docker Hub to Remote

### 8.1 On the workstation (one-time setup)

```bash
# Authenticate with Docker Hub (use a Personal Access Token, not a password)
docker login

# Build the prod images (if not already done)
docker compose -f docker-compose.prod.yml build

# Push to Docker Hub
docker push cmuthu2503/agent-team-backend:latest
docker push cmuthu2503/agent-team-frontend:latest

# OPTIONAL: also push a dated tag for rollback safety
TAG=$(date +%Y%m%d-%H%M)
docker tag cmuthu2503/agent-team-backend:latest  cmuthu2503/agent-team-backend:$TAG
docker tag cmuthu2503/agent-team-frontend:latest cmuthu2503/agent-team-frontend:$TAG
docker push cmuthu2503/agent-team-backend:$TAG
docker push cmuthu2503/agent-team-frontend:$TAG
```

After this, both images are available at:
- https://hub.docker.com/r/cmuthu2503/agent-team-backend
- https://hub.docker.com/r/cmuthu2503/agent-team-frontend

**Visibility:** default is public. If any source file or YAML config is sensitive (system prompts, internal URLs, etc.), set the repos to **private** on Docker Hub before pushing.

### 8.2 On the remote server (first-time deploy)

```bash
# 1. Install Docker if not present (Ubuntu / Debian)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER     # log out and back in

# 2. Prep the deploy directory
mkdir -p ~/agent-team && cd ~/agent-team

# 3. Copy the prod compose file from your workstation
scp your-workstation:/path/to/claude-agent-team/docker-compose.prod.yml .

# 4. Create and populate the secrets directory
mkdir -p secrets
scp your-workstation:/path/to/claude-agent-team/secrets/*.txt secrets/
chmod 600 secrets/*.txt

# 5. Create a .env file with your per-deploy config
cat > .env <<'EOF'
CORS_ORIGINS=https://agent-team.example.com
GITHUB_REPO=cmuthu2503-ai/claude-agent-team
GITHUB_BRANCH=main
EOF

# 6. Pull images from Docker Hub and start
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d

# 7. Verify
docker compose -f docker-compose.prod.yml ps
curl http://localhost:8020/api/v1/health
curl -I http://localhost:3020/

# 8. Tail logs while smoke-testing
docker compose -f docker-compose.prod.yml logs -f
```

### 8.3 On the remote server (subsequent deploys)

```bash
cd ~/agent-team
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Data in the named volumes survives. Downtime is seconds.

### 8.4 Rotating a secret

```bash
# On the remote
cd ~/agent-team
echo -n "sk-proj-<new-value>" > secrets/openai_api_key.txt
docker compose -f docker-compose.prod.yml restart backend
```

No rebuild, no image pull, no image push. The secret is a file mounted at runtime.

---

## 9. Known Issue — Multi-Worker Admin Bootstrap Race

**Discovered during §5 standalone verification.** This is a pre-existing bug, NOT a regression from the audit work.

### 9.1 Symptom

Running the prod backend image shows this in startup logs:

```
INFO:     Application startup complete.
ERROR:    Application startup failed. Exiting.
sqlite3.IntegrityError: UNIQUE constraint failed: users.email
```

### 9.2 Root cause

`Dockerfile.backend` prod CMD runs uvicorn with `--workers 2`. Both worker processes run `lifespan()` on startup, both call `AuthService.bootstrap_admin()`, both try to `INSERT` the same default admin user into the fresh SQLite DB. One wins the race; the other gets a UNIQUE constraint violation on `users.email` and exits.

The surviving worker handles all requests, so the container appears healthy to Docker's healthcheck (`/api/v1/health` returns 200). But the stack is running at half the designed capacity, and the failure is silent.

### 9.3 Recommended fix — Option A (idempotent insert)

In `src/auth/service.py::bootstrap_admin()`, wrap the INSERT with existence-checking:

```python
async def bootstrap_admin(self) -> str | None:
    existing = await self.state.get_user_by_username("admin")
    if existing is not None:
        return None    # admin already exists — no-op, safe for multi-worker
    # ... existing create_user logic ...
```

Alternative: `try/except IntegrityError` around the INSERT and swallow it. Also valid, but check-first is cleaner.

### 9.4 Alternative — Option B (single worker in prod)

Change the Dockerfile.backend prod CMD from `--workers 2` to `--workers 1`. No code change. Trade-off: slightly lower concurrency ceiling, but this is a dev tool and a single worker is almost certainly enough.

### 9.5 Status

**Not yet applied.** Requires decision on Option A vs B. See the "Next Steps" section.

---

## 10. Next Steps Checklist

### 10.1 Must do before pushing to Docker Hub

- [ ] **Rotate the OpenAI key.** The key currently in `secrets/openai_api_key.txt` was pasted in chat earlier in this session and may be in conversation logs. Rotate at https://platform.openai.com/api-keys before any push.
- [ ] **Decide on Docker Hub repo visibility** — public or private? If anything in `src/` or `config/` is sensitive, create the repo as private first.
- [ ] **Fix the multi-worker bootstrap bug (§9)** — pick Option A or B. The current behavior is half-broken in prod.

### 10.2 Must do before the first remote deploy

- [ ] **Set `CORS_ORIGINS`** on the remote in `.env` to the real deployed frontend hostname(s). Without this, the deployed frontend cannot talk to the deployed backend.
- [ ] **Ship all 7 secret files** to the remote via scp/rsync. Set `chmod 600` on each.
- [ ] **Set up a reverse proxy** (nginx/Caddy/Traefik) in front of ports 8020/3020 for TLS termination. The compose file does not include one.
- [ ] **Schedule offsite backups** for the `agent-team-prod-data` named volume. The SQLite database holds user accounts, request history, cost records, and research artifacts — losing the volume loses everything.
- [ ] **Set `JWT_SECRET`** to a fresh random value on the remote (don't reuse `secrets/jwt_secret.txt` from the workstation if they need to be different). The audit generated a 64-char url-safe random value automatically.

### 10.3 Commit hygiene

The audit touched 12 tracked files plus created 4 new ones (`.dockerignore`, `src/utils/secrets.py`, `secrets/README.md`, this document). They are currently **uncommitted**. Before pushing images, commit the repository state so the workstation and any remote clones match what's in the image layers.

| File | Change type |
|---|---|
| `.dockerignore` | NEW |
| `src/utils/secrets.py` | NEW |
| `secrets/README.md` | NEW |
| `docs/docker-deployment-assessment.md` | NEW (this file) |
| `src/main.py` | modified — CORS env var, JWT via `read_secret` |
| `src/agents/executor.py` | modified — 4 secret reads through helper |
| `src/core/github_publisher.py` | modified — GITHUB_TOKEN via helper |
| `src/tools/firecrawl_tools.py` | modified — FIRECRAWL_API_KEY via helper |
| `docker-compose.prod.yml` | modified — `image:` directives, all 7 secrets, env vars |
| `frontend/src/components/ui/AgentCard.tsx` | modified — TS6133 fix |
| `frontend/src/components/ui/MarkdownRenderer.tsx` | modified — TS6133 fix (×2) |
| `frontend/src/pages/CommandCenter.tsx` | modified — TS6133 fix |
| `secrets/*.txt` | populated but `.gitignore`'d — must NOT be committed |

Pre-existing uncommitted files from prior sessions (`docs/prd-template.md`, `docs/task-list.md`, `frontend/src/components/layout/Navbar.tsx`, `frontend/src/components/ui/ThemeSelector.tsx`) are intentionally left alone — they should be committed separately on their own review cycle.

---

## 11. The Supervisor Stack — Level 3 Deployment Orchestrator

This section covers the third Docker service in the repo — the `agent-team-supervisor` stack — which was deferred from the main audit because its self-containment story and deployment model are fundamentally different from the backend / frontend images.

### 11.1 What it is and why it's a separate stack

The supervisor is a **Level 3 fully-autonomous deployment orchestrator**. It is the sidecar that turns a successful `code_committed` state in the database into a live deployment across staging, production, and dev. It is defined entirely in three files:

- `supervisor/deploy_supervisor.py` — ~260-line Python script using only stdlib (`json`, `sqlite3`, `subprocess`, `pathlib`)
- `Dockerfile.supervisor` — single-stage image based on `docker:27-cli` (Alpine + Docker CLI) with `python3`, `py3-pip`, `curl`, and `git` added via `apk`
- `docker-compose.supervisor.yml` — standalone compose stack with `name: agent-team-supervisor` (distinct project name from the main `claude-agent-team` stack)

It runs as its own compose project for one critical reason, quoted from `supervisor/README.md`:

> *"The supervisor must survive container rebuilds. If it ran inside `docker-compose.yml`, running `docker compose down` to rebuild the app would kill the supervisor too — breaking the deployment mid-process."*

Put differently: if you co-located the supervisor with the app it orchestrates, restarting the app would mid-deploy-cancel every in-flight deployment. Running it in its own compose stack gives it an independent lifecycle — `make supervisor`, `make supervisor-logs`, `make supervisor-stop` (from the Makefile, lines 57–65) manage it without touching the main stack, and vice versa.

### 11.2 Runtime behavior

The supervisor runs a tight polling loop with `POLL_INTERVAL = 5` seconds (`deploy_supervisor.py` line 32). Each iteration:

1. **Poll**: `get_pending_deployments(db)` runs `SELECT * FROM deployment_states WHERE current_step='code_committed'` against the shared SQLite database.
2. **If no pending**: sleep 5s, repeat.
3. **If pending**: call `deploy(db, deployment)` which runs this pipeline:

   | Step | Command | Timeout | Health check |
   |---|---|---|---|
   | 1. Build | `docker compose build` | 300s | — |
   | 2. Staging up | `docker compose -f docker-compose.staging.yml up -d --build` | 120s | — |
   | 3. Staging health | `curl http://localhost:8010/api/v1/health` | 30s (10 retries × 3s) | required to pass |
   | 4. Prod up | `docker compose -f docker-compose.prod.yml up -d --build` | 120s | — |
   | 5. Prod health | `curl http://localhost:8020/api/v1/health` | 30s (10 retries × 3s) | required to pass |
   | 6. Dev rebuild | `docker compose -f docker-compose.yml down && up -d --build` | 120s | — |
   | 7. Dev health | `curl http://localhost:8000/api/v1/health` | 30s | required to pass |

   After each step, the supervisor updates `step_history` (stored as JSON on the deployment_states row) so the `/api/v1/releases` endpoint can report progress in real time.

4. **On any failure**: `rollback()` runs (`deploy_supervisor.py` lines 134–158):
   - `git revert HEAD --no-edit` in the bind-mounted project directory
   - `git push origin main` to push the revert to the remote
   - Stop staging and prod containers
   - Rebuild prod with the reverted code
   - Health-check prod on :8020
   - Mark the deployment state as `rolled_back` (or flag for manual verification if the rollback itself fails)

The main loop has a broad `except Exception` at line 254 so a single bad deployment doesn't crash the supervisor — it logs and moves on. The process is designed to run for weeks without restart.

### 11.3 Runtime dependencies

From `docker-compose.supervisor.yml` — three bind mounts, each essential:

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock    # Docker socket (host daemon control)
  - agent-team-data:/app/data                    # Shared named volume with the main backend
  - .:/app/project                               # Project directory (for git commands)
working_dir: /app/project
```

All three are **read-write**:

- **Docker socket** — without this, the supervisor cannot run `docker compose build`, `up`, `down`. It needs to talk to the host's Docker daemon as if it were running on the host directly.
- **`agent-team-data` named volume** — declared as `external: true` with `name: claude-agent-team_agent-team-data`, meaning it's the *same physical volume* the main backend uses for `/app/data/agent_team.db`. This is how the supervisor sees the `deployment_states` table the backend writes to. Shared SQLite is the only inter-process channel.
- **Project directory bind mount (`.:/app/project`)** — required for the `git revert` and `git push` calls during rollback. Git needs the full `.git/` directory and a real working tree, which cannot exist inside an immutable image layer. This is also what distinguishes the supervisor from a fully self-contained image.

Other details:

- **Base image**: `docker:27-cli` (Alpine) — chosen because it ships with a current `docker` / `docker compose` CLI. Python3 and git are added on top via `apk add --no-cache`.
- **User**: root. There is no `USER` directive in `Dockerfile.supervisor`. Running as root is currently necessary for the git and docker commands; dropping privileges would require socket group ownership tuning that isn't worth the churn for a dev tool.
- **Ports exposed**: none. The supervisor listens to nothing and exposes nothing. Everything is outbound (CLI calls) or through the shared DB.
- **Env vars**: two optional overrides, both with sensible defaults for the container layout:
  - `PROJECT_ROOT` (default `/app/project`)
  - `DB_PATH` (default `/app/data/agent_team.db`)
- **Secrets**: none. The supervisor does not read API keys. It doesn't talk to Anthropic, OpenAI, AWS, GitHub (except via `git push`, which uses the mounted project's git config / SSH key), or Firecrawl. All 7 app-level secrets documented in §4.4 are consumed by the backend, not the supervisor.
- **HEALTHCHECK**: `pgrep -f deploy_supervisor || exit 1` every 30 seconds — a liveness check only. Docker marks the container unhealthy after 3 consecutive failures but does not auto-restart (the `restart: unless-stopped` policy only restarts on crash or explicit stop).
- **CMD**: `python3 /app/deploy_supervisor.py`. No explicit ENTRYPOINT.

### 11.4 Self-containment assessment (the critical contrast with §6)

The supervisor image is **partially** self-contained — in a way that's different from backend (§6.1) and frontend (§6.2):

| Aspect | Backend (§6.1) | Frontend (§6.2) | Supervisor |
|---|---|---|---|
| Application code baked in | ✅ `/app/src/` copied from `COPY src/ src/` | ✅ Vite dist copied into nginx html dir | ✅ `deploy_supervisor.py` copied from `COPY supervisor/deploy_supervisor.py` |
| All dependencies baked in | ✅ ~80 Python packages via `pip install .` | ✅ React + bundled assets (Mermaid lazy-loaded from CDN) | ✅ `python3`, `git`, `curl`, `docker` CLI via `apk add` |
| Runs standalone with `docker run <image>` | ✅ Yes (verified in §5) | ✅ Yes | ❌ **No** — requires three bind mounts at runtime |
| Image is pushable to Docker Hub | ✅ Yes | ✅ Yes | ✅ Yes (after the `image:` directive fix below) |
| Image is *runnable* from a plain `docker pull` on an unrelated machine | ✅ Yes | ✅ Yes | ❌ **No** — no Docker socket → no work, no project dir → `git revert` fails, no shared data volume → can't see the deployment queue |

**The supervisor cannot run standalone.** It needs:

1. A Docker daemon to talk to (the socket mount)
2. A git checkout of this repo on disk (the project bind mount)
3. Access to the SQLite database the backend is writing to (the shared named volume)

This is **not a bug to fix**. It's a fundamental design choice: the supervisor's whole job is to manipulate the host's Docker daemon and the host's git repo. A perfectly self-contained image literally *cannot* do that job, because those resources live outside the image by definition.

### 11.5 Where the supervisor runs (and where it doesn't)

This is the most important takeaway of the whole section: the supervisor is a **local / build-machine / CI-host orchestration tool**. It has **no role** on a remote server that only pulls prebuilt images and runs them.

Two deployment models make this concrete:

**Model A — Local full-stack with supervisor (what this repo is designed around today):**

```
┌─────────────────────────────── Workstation / CI host ───────────────────────────────┐
│                                                                                      │
│   ┌────────────────────┐   ┌────────────────────┐   ┌────────────────────┐            │
│   │ Dev stack          │   │ Staging stack      │   │ Prod stack         │            │
│   │ backend  :8000     │   │ backend  :8010     │   │ backend  :8020     │            │
│   │ frontend :3000     │   │ frontend :3010     │   │ frontend :3020     │            │
│   └─────────┬──────────┘   └─────────┬──────────┘   └─────────┬──────────┘            │
│             │                         │                         │                   │
│             └─── agent-team-data ─────┴─────────────────────────┘                    │
│                  (shared named volume, holds SQLite DB)                              │
│                                   │                                                  │
│                                   ▼                                                  │
│                         ┌─────────────────────────┐                                   │
│                         │ Supervisor              │                                   │
│                         │  polls deployment_states│                                   │
│                         │  runs docker compose    │                                   │
│                         │  git revert on failure  │                                   │
│                         └─────────────────────────┘                                   │
│                                                                                      │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

All four stacks on one machine. The supervisor watches the DB and drives promotions automatically when the DevOps agent commits code.

**Model B — Remote deploy with no supervisor (what you're adding):**

```
┌────────── Workstation / CI host ──────────┐        ┌──────── Remote server ─────────┐
│                                            │        │                                 │
│   docker compose -f ...prod.yml build      │        │   docker compose -f ...prod.yml │
│   docker push cmuthu2503/...:latest   ─────┼────────┼──▶        pull                  │
│                                            │        │   docker compose -f ...prod.yml │
│   (supervisor still runs here locally if   │        │        up -d                    │
│    you want the Level 3 auto-promotion     │        │                                 │
│    pipeline for LOCAL staging/prod)        │        │   backend  :8020                │
│                                            │        │   frontend :3020                │
│                                            │        │   named volumes for data        │
│                                            │        │   NO supervisor                 │
└────────────────────────────────────────────┘        └─────────────────────────────────┘
```

The remote has no Level 3 workflow, no `deployment_states` to watch, no dev/staging stacks to rebuild. It just serves prebuilt images. The supervisor stays on the workstation / CI host where the build and orchestration happen.

The `.gitignore`, `.dockerignore`, and `docker-compose.supervisor.yml` all stay in the repo for Model A users, but none of those files need to ship to the remote.

### 11.6 Current image state and what changed in this pass

**Before this pass:**

```
claude-agent-team-supervisor:latest       01e128176357   367 MB
agent-team-supervisor-supervisor:latest   01e128176357   367 MB   ← duplicate of same image ID
```

`docker-compose.supervisor.yml` had only `build:`, no `image:`. Docker's compose plugin auto-named the image based on the compose project name — which works locally but means (a) the image has no intentional, globally-unique name, (b) it can't be cleanly pushed to Docker Hub under the `cmuthu2503/` namespace, and (c) any rename of the compose project would orphan the old tag.

**After this pass:**

```yaml
services:
  supervisor:
    image: cmuthu2503/agent-team-supervisor:latest   # NEW — explicit name
    build:
      context: .
      dockerfile: Dockerfile.supervisor
```

Parallel to the pattern applied to `docker-compose.prod.yml` in §4.6. The behavior of `make supervisor` is unchanged; the only visible difference is the image tag the build produces and the ability to `docker push` it cleanly:

```bash
make supervisor                                         # builds as cmuthu2503/agent-team-supervisor:latest
docker push cmuthu2503/agent-team-supervisor:latest     # now pushable to Docker Hub (when you want to)
```

The supervisor image has **not** been rebuilt in this pass. The existing image (`01e128176357`, ~367 MB) is recent — `deploy_supervisor.py` was last modified 2026-04-06, and no supervisor source has changed since. If you want a fresh tag under the new name, run `make supervisor` (or `docker compose -f docker-compose.supervisor.yml build`) at your convenience.

**Still open (not applied in this pass):**

- Actually pushing `cmuthu2503/agent-team-supervisor:latest` to Docker Hub — optional. The supervisor is a thin wrapper around `docker:27-cli + python3 + one script`, so pulling it from Docker Hub doesn't save much build time. The push is only valuable if you want reproducible versioning across multiple CI hosts. Your call — not required for any deployment path.
- Adding a `.dockerignore`-scoped test to the supervisor CI — the existing `.dockerignore` already protects it, but there's no explicit verification step.

---

## 12. References

- **`Dockerfile.backend`** — multi-stage (dev / prod) Python 3.12 image; prod uses explicit `COPY src/` + `COPY config/`, runs as non-root `agent` user
- **`Dockerfile.frontend`** — multi-stage (dev / build / prod); prod uses nginx:alpine serving the Vite dist
- **`Dockerfile.supervisor`** — single-stage Alpine + `docker:27-cli` + `python3` + `git` + `curl`; COPYs `deploy_supervisor.py`, runs as root
- **`docker-compose.yml`** — local dev stack with bind mounts for hot reload
- **`docker-compose.prod.yml`** — prod stack with named volumes, Docker secrets, explicit image names
- **`docker-compose.staging.yml`** — staging stack (also uses `target: prod` but with a simpler `env_file` setup)
- **`docker-compose.supervisor.yml`** — standalone Level 3 deployment supervisor stack; bind-mounts Docker socket, shared DB volume, and project directory
- **`supervisor/deploy_supervisor.py`** — ~260-line polling loop + deploy pipeline + rollback logic; stdlib-only
- **`supervisor/README.md`** — rationale for running as a separate compose stack
- **`src/utils/secrets.py`** — secrets helper (file → env var → default lookup)
- **`src/main.py`** — CORS origins configuration + lifespan
- **`src/core/code_writer.py`** — upstream producer of `deployment_states` rows the supervisor polls
- **`config/agents/devops_specialist.yaml`** — describes "Level 3 — Fully Autonomous" deployment model the supervisor implements
- **`secrets/README.md`** — onboarding doc for which files go in `./secrets/`
- **`Makefile`** — `supervisor`, `supervisor-logs`, `supervisor-stop` targets (lines 57–65)
- **`CLAUDE.md`** — repo orientation doc for AI-assisted development sessions
