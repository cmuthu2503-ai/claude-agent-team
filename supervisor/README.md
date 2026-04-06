# Deployment Supervisor

Sidecar process that runs **on the host machine** (outside Docker containers).

## What it does

1. Polls the `deployment_state` table for entries with `current_step = 'code_committed'`
2. Builds Docker images
3. Deploys to staging → verifies health → deploys to production → verifies health
4. On failure: rolls back (git revert + Docker restart)
5. Rebuilds dev environment after successful deployment

## How to run

```bash
# From the project root
python supervisor/deploy_supervisor.py
```

Keep it running in a separate terminal. It polls every 5 seconds.

## Requirements

- Python 3.12+
- Docker + Docker Compose
- Git
- curl
- Access to the SQLite database at `data/agent_team.db`

## How it works with the pipeline

```
Agent Pipeline (inside Docker):
  PRD → Stories → Dev → Review → Test → Code Commit
                                            ↓
                                    Writes deployment_state
                                    with step = 'code_committed'
                                            ↓
Supervisor (on host):
  Detects 'code_committed' → build → staging → health → prod → health → done
```

The supervisor updates `deployment_state` at every step. If the dev container restarts (during rebuild), the DevOps agent reads the state and knows where the deployment is.
