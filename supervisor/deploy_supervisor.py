"""Sidecar Deployment Supervisor — runs on host, outside Docker.

Watches the deployment_state table for 'code_committed' entries,
then builds Docker images, deploys staging/production, verifies health,
and handles rollback on failure.

Usage:
    python supervisor/deploy_supervisor.py

Requires: Docker, docker compose, git, sqlite3, curl on the host.
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Configuration
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "agent_team.db"
POLL_INTERVAL = 5  # seconds
HEALTH_CHECK_RETRIES = 10
HEALTH_CHECK_INTERVAL = 3  # seconds

STAGING_COMPOSE = "docker-compose.staging.yml"
PROD_COMPOSE = "docker-compose.prod.yml"
DEV_COMPOSE = "docker-compose.yml"


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] SUPERVISOR: {msg}", flush=True)


def run_cmd(cmd: str, timeout: int = 120) -> tuple[int, str]:
    """Run a shell command and return (exit_code, output)."""
    log(f"  → {cmd}")
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            cwd=str(PROJECT_ROOT), timeout=timeout,
        )
        output = result.stdout + result.stderr
        if result.returncode != 0:
            log(f"  ✗ Exit {result.returncode}: {output[:200]}")
        else:
            log(f"  ✓ OK")
        return result.returncode, output
    except subprocess.TimeoutExpired:
        log(f"  ✗ Timeout after {timeout}s")
        return 1, f"Timeout after {timeout}s"
    except Exception as e:
        log(f"  ✗ Error: {e}")
        return 1, str(e)


def health_check(port: int) -> bool:
    """Check if a service is healthy on the given port."""
    for attempt in range(HEALTH_CHECK_RETRIES):
        code, output = run_cmd(f'curl -sf -o /dev/null -w "%{{http_code}}" http://localhost:{port}/api/v1/health', timeout=10)
        if code == 0 and "200" in output:
            log(f"  ✓ Health check passed on :{port}")
            return True
        log(f"  ⏳ Health check attempt {attempt + 1}/{HEALTH_CHECK_RETRIES} on :{port}...")
        time.sleep(HEALTH_CHECK_INTERVAL)
    log(f"  ✗ Health check FAILED on :{port} after {HEALTH_CHECK_RETRIES} retries")
    return False


def get_db() -> sqlite3.Connection:
    """Open the SQLite database."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_pending_deployments(db: sqlite3.Connection) -> list[dict]:
    """Find deployments with current_step = 'code_committed'."""
    cursor = db.execute(
        "SELECT * FROM deployment_states WHERE current_step = 'code_committed' ORDER BY started_at"
    )
    return [dict(row) for row in cursor.fetchall()]


def update_step(db: sqlite3.Connection, deployment_id: str, step: str, detail: str, error: str = "") -> None:
    """Update the deployment state with a new step."""
    # Read current history
    cursor = db.execute("SELECT step_history FROM deployment_states WHERE deployment_id = ?", (deployment_id,))
    row = cursor.fetchone()
    history = json.loads(row["step_history"]) if row and row["step_history"] else []

    # Append new step
    history.append({
        "step": step,
        "status": "error" if error else "done",
        "timestamp": datetime.utcnow().isoformat(),
        "detail": detail,
    })

    # Update
    now = datetime.utcnow().isoformat()
    if error:
        db.execute(
            """UPDATE deployment_states SET current_step=?, step_history=?,
               updated_at=?, error_message=? WHERE deployment_id=?""",
            ("failed", json.dumps(history), now, error, deployment_id),
        )
    elif step in ("completed", "rolled_back"):
        db.execute(
            """UPDATE deployment_states SET current_step=?, step_history=?,
               updated_at=?, completed_at=? WHERE deployment_id=?""",
            (step, json.dumps(history), now, now, deployment_id),
        )
    else:
        db.execute(
            """UPDATE deployment_states SET current_step=?, step_history=?,
               updated_at=? WHERE deployment_id=?""",
            (step, json.dumps(history), now, deployment_id),
        )
    db.commit()


def rollback(db: sqlite3.Connection, deployment_id: str, rollback_sha: str) -> None:
    """Rollback: git revert + stop containers + rebuild + restart."""
    log("🔄 ROLLING BACK...")
    update_step(db, deployment_id, "rolling_back", "Initiating rollback")

    # Git revert
    if rollback_sha:
        code, _ = run_cmd("git revert HEAD --no-edit")
        if code == 0:
            run_cmd("git push origin main")

    # Stop staging and prod
    run_cmd(f"docker compose -f {STAGING_COMPOSE} down", timeout=30)
    run_cmd(f"docker compose -f {PROD_COMPOSE} down", timeout=30)

    # Rebuild with reverted code
    code, _ = run_cmd("docker compose build", timeout=300)
    if code == 0:
        # Restart prod with old code
        run_cmd(f"docker compose -f {PROD_COMPOSE} up -d", timeout=60)
        if health_check(8020):
            update_step(db, deployment_id, "rolled_back", "Rollback successful — production restored")
            return

    update_step(db, deployment_id, "rolled_back", "Rollback attempted — manual verification needed", error="Rollback health check uncertain")


def deploy(db: sqlite3.Connection, deployment: dict) -> None:
    """Execute the full deployment pipeline for a single deployment."""
    dep_id = deployment["deployment_id"]
    req_id = deployment["request_id"]
    rollback_sha = deployment["rollback_sha"]

    log(f"🚀 Starting deployment {dep_id} for {req_id}")

    # Step 1: Build Docker images
    update_step(db, dep_id, "building", "Building Docker images...")
    code, output = run_cmd("docker compose build", timeout=300)
    if code != 0:
        update_step(db, dep_id, "building", output[:200], error="Docker build failed")
        return

    update_step(db, dep_id, "building", "Docker images built successfully")

    # Step 2: Deploy to staging
    update_step(db, dep_id, "staging_deploying", "Deploying to staging...")
    code, _ = run_cmd(f"docker compose -f {STAGING_COMPOSE} up -d --build", timeout=120)
    if code != 0:
        update_step(db, dep_id, "staging_deploying", "Staging deploy failed", error="docker compose staging failed")
        rollback(db, dep_id, rollback_sha)
        return

    # Step 3: Verify staging health
    log("Checking staging health...")
    if not health_check(8010):
        update_step(db, dep_id, "staging_deploying", "Staging unhealthy", error="Staging health check failed")
        run_cmd(f"docker compose -f {STAGING_COMPOSE} down")
        rollback(db, dep_id, rollback_sha)
        return

    update_step(db, dep_id, "staging_healthy", "Staging deployed and healthy on :8010/:3010")

    # Step 4: Deploy to production
    update_step(db, dep_id, "prod_deploying", "Deploying to production...")
    code, _ = run_cmd(f"docker compose -f {PROD_COMPOSE} up -d --build", timeout=120)
    if code != 0:
        update_step(db, dep_id, "prod_deploying", "Production deploy failed", error="docker compose prod failed")
        run_cmd(f"docker compose -f {STAGING_COMPOSE} down")
        rollback(db, dep_id, rollback_sha)
        return

    # Step 5: Verify production health
    log("Checking production health...")
    if not health_check(8020):
        update_step(db, dep_id, "prod_deploying", "Production unhealthy", error="Production health check failed")
        run_cmd(f"docker compose -f {PROD_COMPOSE} down")
        run_cmd(f"docker compose -f {STAGING_COMPOSE} down")
        rollback(db, dep_id, rollback_sha)
        return

    update_step(db, dep_id, "prod_healthy", "Production deployed and healthy on :8020/:3020")

    # Step 6: Rebuild dev environment with new code
    log("Rebuilding dev environment...")
    run_cmd(f"docker compose -f {DEV_COMPOSE} down", timeout=30)
    run_cmd(f"docker compose -f {DEV_COMPOSE} up -d --build", timeout=120)

    # Wait for dev to be healthy
    if health_check(8000):
        update_step(db, dep_id, "completed", "All environments deployed and healthy. Dev:8000 Staging:8010 Prod:8020")
    else:
        update_step(db, dep_id, "completed", "Staging+Prod healthy. Dev rebuild pending — may need manual restart.")

    log(f"✅ Deployment {dep_id} COMPLETED")


def main() -> None:
    """Main supervisor loop — poll for pending deployments."""
    log("=" * 60)
    log("Deployment Supervisor started")
    log(f"Project root: {PROJECT_ROOT}")
    log(f"Database: {DB_PATH}")
    log(f"Poll interval: {POLL_INTERVAL}s")
    log("=" * 60)

    if not DB_PATH.exists():
        log(f"⚠️  Database not found at {DB_PATH} — waiting for app to create it...")

    while True:
        try:
            if DB_PATH.exists():
                db = get_db()
                pending = get_pending_deployments(db)
                if pending:
                    for deployment in pending:
                        deploy(db, deployment)
                db.close()
        except KeyboardInterrupt:
            log("Supervisor stopped by user")
            sys.exit(0)
        except Exception as e:
            log(f"Error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
