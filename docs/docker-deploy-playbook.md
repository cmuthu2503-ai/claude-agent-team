# Docker Deployment Playbook
# Agent Team — Workstation → Docker Hub → Remote Linux VM

---

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 1.0 |
| Created Date | 2026-04-08 |
| Last Updated | 2026-04-08 |
| Status | Current |
| Purpose | One opinionated end-to-end path to run the prod images on a remote Linux server with minimum moving parts and minimum surprises |
| Companion doc | `docs/docker-deployment-assessment.md` — explains the *why* (image layers, volume mounts, self-containment, what was audited and fixed). Read that first if you want the conceptual grounding; read this one if you just want the commands. |

---

## 0. When to Use This Document

Use this playbook when you want to deploy the Agent Team stack (`cmuthu2503/agent-team-backend` + `cmuthu2503/agent-team-frontend`) to a remote Linux server and have it stay up.

This is **one opinionated path**, not a menu of options. It assumes:

- Your workstation is the one where you build images (Windows or Linux, x86_64)
- You want images stored on Docker Hub (private) as the single source of truth
- The remote is a fresh Linux VM (Ubuntu 22.04+ recommended)
- You want HTTPS via Caddy auto-certs (recommended) OR plain HTTP on ports 8020/3020 (acceptable for dev/internal use)
- You are okay with a 30-second manual update cycle per deploy (no CI/CD yet)

If any of those assumptions don't fit your situation, **read `docs/docker-deployment-assessment.md` for background, then come back here** to adapt specific steps.

---

## 1. End-State Architecture

```
┌─────────── Your workstation ───────────┐
│                                          │
│   Built images locally:                  │
│     cmuthu2503/agent-team-backend:latest │
│     cmuthu2503/agent-team-frontend:latest│
│                                          │
│   You push them ONCE to Docker Hub ─┐    │
│                                      │    │
│   Update cycle:                      │    │
│     1. rebuild prod image            │    │
│     2. docker push  ─────────────────┤    │
│     3. ssh remote + pull + up        │    │
└──────────────────────────────────────┼────┘
                                       │
                                       ▼
                            ┌──────────────────────┐
                            │     Docker Hub       │
                            │  (private registry)  │
                            │   cmuthu2503/...     │
                            └──────────┬───────────┘
                                       │ docker pull
                                       ▼
┌──────── Remote Linux VM (Ubuntu 22.04+) ────────────────┐
│                                                          │
│   /home/deploy/agent-team/                               │
│   ├── docker-compose.prod.yml    ← scp'd once            │
│   ├── .env                        ← CORS_ORIGINS etc.    │
│   └── secrets/                    ← scp'd once, 600      │
│       ├── anthropic_api_key.txt                          │
│       ├── openai_api_key.txt                             │
│       ├── aws_access_key_id.txt                          │
│       ├── aws_secret_access_key.txt                      │
│       ├── github_token.txt                               │
│       ├── firecrawl_api_key.txt                          │
│       └── jwt_secret.txt                                 │
│                                                          │
│   Running containers:                                    │
│   ┌────────────┐    ┌────────────┐                       │
│   │  backend   │◄───│  frontend  │                       │
│   │   :8000    │    │   :3000    │                       │
│   │   (prod)   │    │   (nginx)  │                       │
│   └────────────┘    └────────────┘                       │
│       ▲                  ▲                               │
│       │                  │                               │
│   Mounted at runtime:    │                               │
│   - /run/secrets/* (7 files)                             │
│   - named volumes agent-team-prod-{data,reports,backups} │
│                          │                               │
│   ┌──────────────────────┴──────────┐                   │
│   │  Caddy (reverse proxy + HTTPS)  │ ← ports 80, 443   │
│   └─────────────────────────────────┘                   │
│                 ▲                                        │
└─────────────────┼────────────────────────────────────────┘
                  │
         https://agent-team.yourdomain.com
```

**Setup time: ~25 minutes first time. Subsequent deploys: under 1 minute.**

---

## 2. Step 1 — Docker Hub account setup (5 min, one-time)

### 2.1 Create the two repositories

1. Sign in at https://hub.docker.com/ as `cmuthu2503`.
2. Click **Create Repository** twice. Create these **as Private**:
   - Name: `agent-team-backend`, Visibility: **Private**
   - Name: `agent-team-frontend`, Visibility: **Private**

### 2.2 Create an access token

1. https://hub.docker.com/settings/security → **New Access Token**
2. Description: `deploy-token`
3. Permissions: **Read, Write, Delete**
4. Save the `dckr_pat_...` token — you'll use it on both the workstation and the remote VM. Store it in your password manager.

**Why Private:** The prod images contain all of `src/`, `config/`, agent system prompts, and YAML configs. That's not sensitive in the "leaked keys" sense (secrets stay in files mounted at runtime, not in image layers — see assessment §6.1), but it's your business logic. Private costs nothing extra on Docker Hub and takes 30 seconds to set up.

---

## 3. Step 2 — Push images from workstation (3 min)

Run from `C:\ai-projects\claude-agent-team` (or wherever you cloned the repo).

### 3.1 Log in using the access token

```bash
docker login -u cmuthu2503
# When prompted for password, paste the dckr_pat_... token — NOT your Docker Hub password
```

### 3.2 Push the images

For **x86_64 Windows or Linux workstations** (most common — this is your case):

```bash
docker push cmuthu2503/agent-team-backend:latest
docker push cmuthu2503/agent-team-frontend:latest
```

For **Apple Silicon / ARM workstations** (important gotcha — see §10.1):

```bash
# Force amd64 build for the push — default ARM build will fail on x86 remotes
docker buildx build --platform linux/amd64 \
  -f Dockerfile.backend --target prod \
  -t cmuthu2503/agent-team-backend:latest --push .
docker buildx build --platform linux/amd64 \
  -f Dockerfile.frontend --target prod \
  -t cmuthu2503/agent-team-frontend:latest --push .
```

### 3.3 Verify

Log in to hub.docker.com in a browser and check both repositories — you should see a `latest` tag with a recent timestamp.

---

## 4. Step 3 — Provision the remote VM (5 min, one-time)

Pick any of these providers and order a small VM:

| Provider | Recommended plan | Monthly cost (approx) |
|---|---|---|
| **Hetzner Cloud** *(cheapest)* | CPX21 — 2 vCPU, 4 GB RAM, 80 GB | ~€6 |
| **DigitalOcean** | Basic Regular, 2 GB RAM, 50 GB | $12 |
| **AWS Lightsail** | $10 plan, 2 GB RAM, 60 GB | $10 |
| **Linode / Akamai** | Shared CPU 2 GB | $12 |

**Requirements for the VM image:**

- Ubuntu 22.04 LTS or 24.04 LTS
- **amd64 / x86_64** architecture (ARM works only if you rebuilt with `--platform linux/arm64` in Step 2)
- Minimum 2 GB RAM, 20 GB disk
- SSH key-based access (do not use password auth)
- A domain name pointing at the VM's public IP — optional but strongly recommended for auto-HTTPS via Caddy

---

## 5. Step 4 — Bootstrap the remote VM (5 min, one-time)

SSH in as the provider's default user (usually `root` or `ubuntu`) and run each block.

### 5.1 Install Docker via the official script

```bash
curl -fsSL https://get.docker.com | sudo sh
```

### 5.2 Create a dedicated deploy user

Running Docker as root is a bad idea. Create a `deploy` user that's in the `docker` group:

```bash
sudo useradd -m -s /bin/bash deploy
sudo usermod -aG docker deploy

# Copy your SSH key so you can log in directly as deploy
sudo mkdir -p /home/deploy/.ssh
sudo cp ~/.ssh/authorized_keys /home/deploy/.ssh/
sudo chown -R deploy:deploy /home/deploy/.ssh
sudo chmod 700 /home/deploy/.ssh
sudo chmod 600 /home/deploy/.ssh/authorized_keys
```

### 5.3 Switch to the deploy user

```bash
sudo su - deploy
```

### 5.4 Log in to Docker Hub from the remote

```bash
docker login -u cmuthu2503
# Paste the same dckr_pat_... token from Step 1
```

The credentials are saved in `~/.docker/config.json` so subsequent `docker pull` commands from your private repos work without prompting.

### 5.5 Create the deploy directory layout

```bash
mkdir -p ~/agent-team/secrets
cd ~/agent-team
```

Exit the SSH session; you'll come back via the `deploy` user in Step 6.

---

## 6. Step 5 — Ship 8 files to the remote (1 min)

From your **workstation**, in the project root:

```bash
REMOTE=deploy@your.vm.ip.address

# 1. Compose file
scp docker-compose.prod.yml $REMOTE:~/agent-team/

# 2. All 7 secret files
scp secrets/*.txt $REMOTE:~/agent-team/secrets/

# 3. Tighten permissions on the remote
ssh $REMOTE 'chmod 600 ~/agent-team/secrets/*.txt'
```

Then **SSH back in as deploy** and create the `.env` file:

```bash
ssh deploy@your.vm.ip.address
cd ~/agent-team
cat > .env <<'EOF'
# Required: comma-separated list of allowed frontend origins.
# Replace with your actual domain. Must EXACTLY match what users type
# in the browser, including scheme and port (if any).
CORS_ORIGINS=https://agent-team.yourdomain.com

# Optional — override any of these if you want a different model
# OPENAI_GPT5_MODEL_ID=gpt-5.4
# OPENAI_O3_MODEL_ID=o4-mini
# BEDROCK_MODEL_ID=anthropic.claude-sonnet-4-20250514-v1:0
EOF
```

**Important:** Put your real domain in `CORS_ORIGINS`. If you don't have a domain yet and are just testing with IP addresses, use `CORS_ORIGINS=http://your.vm.ip:3020` temporarily — you can update it and recreate the container later.

---

## 7. Step 6 — First deploy (1 min)

Still SSH'd into the remote as `deploy`, in `~/agent-team`:

```bash
# Pull both images from your private Docker Hub repos
docker compose -f docker-compose.prod.yml pull

# Start the stack in detached mode
docker compose -f docker-compose.prod.yml up -d

# Wait ~10 seconds for the stack to come up, then check status
sleep 10
docker compose -f docker-compose.prod.yml ps
```

Expected output:

```
NAME                       STATUS
agent-team-prod-backend    Up 10 seconds (healthy)
agent-team-prod-frontend   Up 5 seconds (healthy)
```

If either container is not `(healthy)`, check logs:

```bash
docker compose -f docker-compose.prod.yml logs backend --tail 50
docker compose -f docker-compose.prod.yml logs frontend --tail 50
```

Common causes of first-deploy failures:
- Wrong architecture (§10.1)
- Missing or empty secret file (`ls -la secrets/` — every file should be non-zero bytes)
- Wrong `CORS_ORIGINS` (§10.2)
- Port 8020 or 3020 already in use on the host

---

## 8. Step 7 — Verify the stack (1 min)

### 8.1 Health checks

```bash
curl http://localhost:8020/api/v1/health
# Expected:
# {"status":"healthy","version":"0.1.0","environment":"production"}

curl -I http://localhost:3020/
# Expected first line:
# HTTP/1.1 200 OK
```

### 8.2 Provider initialization — the most important check

```bash
docker compose -f docker-compose.prod.yml logs backend --tail 30 | \
  grep -iE 'openai|anthropic|bedrock|cors|ready'
```

You want to see **all four** of these lines:

```
anthropic_client_initialized
bedrock_client_initialized     model=anthropic.claude-sonnet-4-20250514-v1:0 region=us-east-1
openai_client_initialized      gpt5_model=gpt-5.4 o3_model=o4-mini
cors_origins_configured        origins=['https://agent-team.yourdomain.com']
agent_system_ready             agents=9 anthropic_available=True bedrock_available=True openai_available=True tools=15
```

If any provider shows `available=False`, the secret file for that provider is missing, empty, or wrong — check `~/agent-team/secrets/`.

### 8.3 Capture the admin password — DO NOT SKIP

On first run, the backend creates an `admin` user with a random password. **Capture it now**:

```bash
docker compose -f docker-compose.prod.yml logs backend 2>&1 | grep first_run_admin_created
```

You'll see something like:

```
first_run_admin_created username=admin password=<random 16 chars>
```

**Save that password in your password manager immediately.** See §10.3 for what happens if you miss it.

### 8.4 Browser smoke test

From your laptop, open `http://your.vm.ip:3020/` (or your domain once Caddy is set up). You should see the Command Center login page. Log in as `admin` with the password from §8.3.

---

## 9. Step 8 — Add HTTPS via Caddy (strongly recommended, ~5 min)

The stack is currently exposed on ports 8020 and 3020 over plain HTTP. Put Caddy in front for auto-HTTPS.

### 9.1 Requirements

- A domain name (e.g., `agent-team.yourdomain.com`)
- An `A` DNS record pointing the domain at your VM's public IP
- Ports 80 and 443 open on the VM firewall (most cloud providers require you to allow these in a security group/firewall rule)

### 9.2 Run Caddy

On the remote, in `~/agent-team`:

```bash
cat > Caddyfile <<'EOF'
agent-team.yourdomain.com {
    reverse_proxy agent-team-prod-frontend:3000
}
EOF

docker run -d \
  --name caddy \
  --network agent-team-prod \
  -p 80:80 \
  -p 443:443 \
  -v $PWD/Caddyfile:/etc/caddy/Caddyfile \
  -v caddy_data:/data \
  -v caddy_config:/config \
  --restart unless-stopped \
  caddy:2
```

Caddy will contact Let's Encrypt and obtain a certificate within ~30 seconds. Check progress:

```bash
docker logs caddy --follow
```

Look for `certificate obtained successfully`.

### 9.3 Update CORS_ORIGINS to match the real domain

```bash
# Edit ~/agent-team/.env and set the new domain
sed -i 's|^CORS_ORIGINS=.*|CORS_ORIGINS=https://agent-team.yourdomain.com|' .env

# Recreate the backend so the new CORS_ORIGINS value takes effect
docker compose -f docker-compose.prod.yml up -d
```

Visit `https://agent-team.yourdomain.com` in a browser — the valid cert should appear automatically.

---

## 10. The 4 Gotchas That Will Bite You

These are the non-obvious failure modes that break "bulletproof" Docker deploys in practice. Read them.

### 10.1 CPU architecture mismatch (the silent killer)

**What breaks:** If your workstation is Apple Silicon (M1/M2/M3) and your remote VM is Intel/AMD, a plain `docker push` from the workstation uploads an ARM64 image. The remote tries to run it and fails with `exec format error`, OR hangs on startup, OR the `pull` itself fails to find a matching architecture.

**Symptom:**
```
docker compose -f docker-compose.prod.yml up -d
# → exec /usr/local/bin/uvicorn: exec format error
```

**Fix:** Rebuild with `docker buildx build --platform linux/amd64 ... --push` from the workstation. See §3.2 for the exact commands.

**Not your problem today** (your workstation is Windows x86_64), but if you ever switch to a Mac or deploy to a Raspberry Pi, remember this.

### 10.2 CORS_ORIGINS wrong = frontend loads, every API call fails silently

**What breaks:** The login page loads, but clicking Login gives `Network error` or `Failed to fetch`. The browser console shows a CORS error mentioning `Access-Control-Allow-Origin`. This is the #1 cause of "deploy looks fine but nothing works."

**Root cause:** `CORS_ORIGINS` in `~/agent-team/.env` on the remote must **exactly** match the URL users type in the browser, including the scheme (`http://` vs `https://`) and port (include `:3020` if not using Caddy, omit it if Caddy is in front on :443).

| Scenario | Correct value |
|---|---|
| With Caddy + HTTPS + domain | `CORS_ORIGINS=https://agent-team.yourdomain.com` |
| Without Caddy, using IP and port | `CORS_ORIGINS=http://192.168.1.50:3020` |
| ❌ Wrong — missing scheme | `CORS_ORIGINS=agent-team.yourdomain.com` |
| ❌ Wrong — port doesn't match frontend origin | `CORS_ORIGINS=https://agent-team.yourdomain.com:3020` |

**Fix:** Edit `.env`, then run `docker compose -f docker-compose.prod.yml up -d` to recreate the backend container with the new env value. `restart` is not enough — compose only re-reads `.env` on container creation, not restart.

### 10.3 First-run admin password is logged ONCE and gone forever

**What breaks:** On first startup the backend creates an `admin` user with a random password and emits it via structlog exactly once. If you don't capture it from the logs, you're locked out of the UI.

**Prevention:** Run §8.3 immediately after the first `docker compose up`.

**Recovery if you missed it:**

Option A — nuke and redeploy (destroys all data):
```bash
docker compose -f docker-compose.prod.yml down -v
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs backend | grep first_run_admin_created
```

Option B — reset the admin password via SQLite inside the backend container (preserves data):
```bash
docker compose -f docker-compose.prod.yml exec backend python -c "
import asyncio, bcrypt
from src.state.sqlite_store import SQLiteStateStore
async def reset():
    s = SQLiteStateStore(db_path='data/agent_team.db')
    await s.initialize()
    new_hash = bcrypt.hashpw(b'NewPassword123', bcrypt.gensalt()).decode()
    await s.update_password('admin', new_hash)
    await s.close()
    print('Admin password reset to NewPassword123 — change it via the UI.')
asyncio.run(reset())
"
```

### 10.4 `docker compose down -v` wipes ALL data

**What breaks:** The `-v` flag removes named volumes. Your SQLite DB lives in `agent-team-prod-data`. If you run `down -v`, all user accounts, request history, cost records, prompt sessions, research artifacts, and backups are gone.

**Safe operations (keep data):**
```bash
docker compose -f docker-compose.prod.yml down                    # stops, keeps volumes
docker compose -f docker-compose.prod.yml restart                 # restarts containers
docker compose -f docker-compose.prod.yml up -d                   # starts fresh containers, keeps volumes
docker compose -f docker-compose.prod.yml pull && up -d           # update cycle — keeps volumes
```

**Destructive (wipes data):**
```bash
docker compose -f docker-compose.prod.yml down -v                 # ⚠️ loses everything
docker volume rm agent-team-prod-data                             # ⚠️ same
```

**Backup recipe** — schedule a weekly cron that snapshots the data volume to compressed tarballs:

```bash
# Create /etc/cron.weekly/backup-agent-team (as root)
sudo tee /etc/cron.weekly/backup-agent-team > /dev/null <<'EOF'
#!/bin/bash
BACKUP_DIR=/home/deploy/backups
mkdir -p $BACKUP_DIR
docker run --rm \
  -v agent-team-prod-data:/source:ro \
  -v $BACKUP_DIR:/backup \
  alpine tar -czf /backup/agent-team-$(date +%F).tar.gz -C /source .
# Keep only the last 12 weeks
ls -t $BACKUP_DIR/agent-team-*.tar.gz | tail -n +13 | xargs -r rm
EOF
sudo chmod +x /etc/cron.weekly/backup-agent-team
```

For offsite backup, extend the script to `aws s3 cp` or `rclone copy` the tarball to object storage.

---

## 11. The Update Cycle (30 seconds per deploy)

Once the initial setup is done, pushing a new version is three commands total.

### 11.1 On the workstation

```bash
# After making code changes, rebuild and push
docker compose -f docker-compose.prod.yml build
docker push cmuthu2503/agent-team-backend:latest
docker push cmuthu2503/agent-team-frontend:latest
```

### 11.2 On the remote

```bash
cd ~/agent-team
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Data in named volumes survives. Users stay logged in (tokens are JWT-based, not server-session). Downtime is measured in seconds (the brief window while the new containers replace the old ones).

### 11.3 Optional — dated tags for rollback safety

If you want the ability to roll back to a previous version, also push dated tags:

```bash
# On the workstation, alongside the :latest push
TAG=$(date +%Y%m%d-%H%M)
docker tag cmuthu2503/agent-team-backend:latest  cmuthu2503/agent-team-backend:$TAG
docker tag cmuthu2503/agent-team-frontend:latest cmuthu2503/agent-team-frontend:$TAG
docker push cmuthu2503/agent-team-backend:$TAG
docker push cmuthu2503/agent-team-frontend:$TAG
```

To roll back on the remote, edit `docker-compose.prod.yml` and change the `image:` lines from `:latest` to the specific dated tag, then `up -d`. Or use an env-var indirection like `image: cmuthu2503/agent-team-backend:${BACKEND_TAG:-latest}` and set `BACKEND_TAG=20260408-1830` in `.env`.

---

## 12. Summary Checklist

Print this and tick off as you go.

### First-time setup
- [ ] Step 1: Docker Hub repos created (private) and access token saved
- [ ] Step 2: `docker login` on the workstation + `docker push` both images
- [ ] Step 3: VM provisioned (Ubuntu 22.04+, 2+ GB RAM, x86_64)
- [ ] Step 4: Docker installed, `deploy` user created, `docker login` on the remote
- [ ] Step 5: `docker-compose.prod.yml` + `secrets/*.txt` scp'd, `.env` created with real `CORS_ORIGINS`
- [ ] Step 6: `docker compose pull && up -d` succeeds, both containers show `(healthy)`
- [ ] Step 7: `/api/v1/health` returns 200, all 3 LLM providers initialized in logs, admin password captured
- [ ] Step 8: (optional) Caddy running, HTTPS certificate obtained, `CORS_ORIGINS` updated
- [ ] Backup cron configured (§10.4)

### Every subsequent deploy
- [ ] Rebuild prod images on workstation
- [ ] `docker push` both
- [ ] `docker compose pull && up -d` on remote
- [ ] (Optional) tag with date for rollback

---

## 13. Where to Go Next

- **For conceptual background on how Docker images, layers, and mounts work:** `docs/docker-deployment-assessment.md`
- **For the Level 3 supervisor stack (only relevant if you want autonomous agent-driven deployments):** `docs/docker-deployment-assessment.md` §11
- **For local dev workflow (make dev, make logs, etc.):** `CLAUDE.md` at the repo root
- **For the architecture of the agent system itself:** `docs/architecture.md`
- **For what each API key / env var does:** `secrets/README.md`

---

## 14. File Locations Referenced in This Playbook

| Path | Role |
|---|---|
| `docker-compose.prod.yml` | Prod compose file — scp to remote |
| `secrets/*.txt` (7 files) | API keys + JWT secret — scp to remote |
| `.env` | Per-deploy config (CORS_ORIGINS, model overrides) — create on remote |
| `Dockerfile.backend` (prod target) | Produces the backend image pushed to Docker Hub |
| `Dockerfile.frontend` (prod target) | Produces the frontend image pushed to Docker Hub |
| `src/utils/secrets.py` | Helper the backend uses to read secret files at runtime |
| `src/main.py` | Where `CORS_ORIGINS` env var is read and applied |
| `src/auth/service.py::bootstrap_admin()` | First-run admin user creation — see §10.3 |
