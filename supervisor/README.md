# Deployment Supervisor

Standalone Docker container that orchestrates deployments. Runs **independently** from the main app — survives when the app containers are rebuilt or restarted.

## How to Run

```bash
# Start the supervisor (first time — builds the image)
make supervisor

# Or directly:
docker compose -f docker-compose.supervisor.yml up -d --build

# View logs
make supervisor-logs

# Stop
make supervisor-stop
```

## Architecture

```
┌─────────────────────────────────┐
│  Main App (docker-compose.yml)  │
│  - backend (:8000)              │  ← Can be rebuilt/restarted
│  - frontend (:3000)             │     without affecting supervisor
└────────────┬────────────────────┘
             │ shared volume: agent-team-data
             │ (SQLite database)
┌────────────▼────────────────────┐
│  Supervisor (separate stack)    │
│  docker-compose.supervisor.yml  │  ← Runs independently
│  - Watches deployment_state DB  │
│  - Has Docker socket access     │
│  - Rebuilds main app containers │
└─────────────────────────────────┘
```

## What It Does

1. Polls `deployment_states` table every 5 seconds
2. When it finds `current_step = 'code_committed'`:
   - Builds Docker images (`docker compose build`)
   - Deploys to staging → verifies health on :8010
   - Deploys to production → verifies health on :8020
   - Rebuilds dev environment → verifies health on :8000
3. On any failure: rolls back (git revert + Docker restart)
4. Updates `deployment_state` at every step transition

## Why Standalone?

The supervisor must survive container rebuilds. If it ran inside `docker-compose.yml`, running `docker compose down` to rebuild the app would kill the supervisor too — breaking the deployment mid-process.

By running as a separate Docker Compose stack, the supervisor:
- Has its own lifecycle (start/stop independently)
- Survives main app rebuilds
- Shares the SQLite database via a named Docker volume
- Has Docker socket access to control other containers
