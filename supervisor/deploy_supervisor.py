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
import pathlib
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Force UTF-8 stdout/stderr on Windows (default cp1252 can't render the emoji
# glyphs we use in log lines — ✅ ⚖ 🧹 📥 etc. — and crashes the supervisor
# at first log call). No-op on macOS/Linux.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Load environment variables from .env file if present. The supervisor needs
# ANTHROPIC_AWS_API_KEY / ANTHROPIC_AWS_WORKSPACE_ID (for the deployment judge
# LLM) and GITHUB_TOKEN / GITHUB_REPO (for rollback git push). When the
# supervisor runs on host, these come from the same .env that docker compose
# reads. Minimal parser — no quoting / escaping support, but the project's
# .env is simple KEY=VALUE.
def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Configuration — supervisor runs on the HOST (not in Docker).
#
# Why on host: when the supervisor ran inside a container, it couldn't manage
# dev's bind-mounted compose stack because Docker daemon resolved relative
# volume paths against the HOST filesystem, not the supervisor's container
# filesystem (the "/app/project/frontend/vite.config.ts not a directory"
# error). Moving the supervisor to host eliminates that path-translation
# class of bugs entirely — `docker compose` from host resolves bind mounts
# normally, exactly as a human developer would.
#
# Defaults assume project root = parent of this file's directory (i.e.,
# the supervisor script lives at <project>/supervisor/deploy_supervisor.py).
# Env vars `PROJECT_ROOT` and `DB_PATH` override for non-standard layouts.
_DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", str(_DEFAULT_PROJECT_ROOT)))
DB_PATH = Path(os.getenv("DB_PATH", str(PROJECT_ROOT / "data" / "agent_team.db")))
POLL_INTERVAL = 5  # seconds
HEALTH_CHECK_RETRIES = 10
HEALTH_CHECK_INTERVAL = 3  # seconds

# Dev rebuild needs MUCH more patience than staging/prod. Cold start runs the
# FastAPI lifespan: instantiates 9 agents from YAML, loads 17 tool schemas,
# opens the AnthropicAWS client, runs SQLite migrations. Takes 10-30s before
# /health returns 200. The default 30s window (10x3s) raced and lost on
# REQ-3EED7F, marking the deploy "uncertain" while dev was still mid-init.
DEV_HEALTH_CHECK_RETRIES = 30
DEV_HEALTH_CHECK_INTERVAL = 4  # 30 * 4 = 120s window

STAGING_COMPOSE = "docker-compose.staging.yml"
PROD_COMPOSE = "docker-compose.prod.yml"
DEV_COMPOSE = "docker-compose.yml"


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] SUPERVISOR: {msg}", flush=True)


def run_cmd(cmd: str | list[str], timeout: int = 120) -> tuple[int, str]:
    """Run a command and return (exit_code, output).

    Pass a list[str] (argv) for any call that interpolates dynamic data
    like file paths — argv form is shell-free and immune to platform
    quoting differences (cmd.exe vs /bin/sh handle single quotes
    differently, which silently breaks `git checkout -- 'path'` on
    Windows). String form remains supported for static commands like
    `docker compose build`.
    """
    use_shell = isinstance(cmd, str)
    display = cmd if use_shell else " ".join(cmd)
    log(f"  → {display}")
    try:
        # encoding='utf-8' overrides the platform default (cp1252 on Windows),
        # which can't decode UTF-8 progress spinners / emoji in docker output
        # and silently crashes the subprocess reader thread → result.stdout=None
        # → `None + str` blows up. errors='replace' keeps us alive on any
        # remaining oddball bytes.
        result = subprocess.run(
            cmd, shell=use_shell, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=str(PROJECT_ROOT), timeout=timeout,
        )
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            # On failure, log MUCH more output. Was truncating to 200 chars
            # which hid the actual cause of every staging build failure in
            # this session (REQ-7C6527, REQ-D0742A, REQ-8C3B4F all showed
            # "Sending build context to Docker daemon  524.4kB" with no
            # actual error). 4000 chars covers a full Docker build error
            # including the failing layer's stderr. The supervisor's stdout
            # goes to docker logs anyway, so the cost is one big log entry
            # per failure — worth it.
            log(f"  ✗ Exit {result.returncode}:")
            for line in output[:4000].splitlines():
                log(f"      {line}")
            if len(output) > 4000:
                log(f"      ... [truncated, {len(output) - 4000} more chars]")
        else:
            log(f"  ✓ OK")
        return result.returncode, output
    except subprocess.TimeoutExpired:
        log(f"  ✗ Timeout after {timeout}s")
        return 1, f"Timeout after {timeout}s"
    except Exception as e:
        log(f"  ✗ Error: {e}")
        return 1, str(e)


def health_check(
    port: int,
    retries: int | None = None,
    interval: int | None = None,
) -> bool:
    """Check if a service is healthy on the given port.

    Accepts per-call overrides for retries/interval so callers can give dev
    cold-start more patience than staging (where the image is already
    built and starts fast). When None, falls back to module defaults.
    """
    n_retries = retries if retries is not None else HEALTH_CHECK_RETRIES
    n_interval = interval if interval is not None else HEALTH_CHECK_INTERVAL
    total_seconds = n_retries * n_interval
    for attempt in range(n_retries):
        # Supervisor runs on the host, so `localhost` resolves to the
        # host's published ports (:8000 / :8010 / :8020) as expected.
        # The HEALTHCHECK_HOST env var stays available as an override for
        # weird network setups, but defaults to localhost on host.
        host = os.getenv("HEALTHCHECK_HOST", "localhost")
        url = f"http://{host}:{port}/api/v1/health"
        # Use urllib (stdlib) instead of shelling out to curl. The previous
        # `curl -sf -o /dev/null` returned exit 23 on Windows because cmd.exe
        # treats `/dev/null` as a literal path (no such file on Windows) —
        # the body write failed even though the HTTP response was 200, so the
        # supervisor mistakenly concluded staging was unhealthy and triggered
        # rollback. urllib has no shell involvement and works identically on
        # every platform.
        status_code = 0
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                status_code = resp.status
        except urllib.error.HTTPError as e:
            status_code = e.code
        except Exception:
            status_code = 0
        if status_code == 200:
            log(f"  ✓ Health check passed on :{port} (attempt {attempt + 1})")
            return True
        log(f"  ⏳ Health check attempt {attempt + 1}/{n_retries} on :{port} (got {status_code})...")
        time.sleep(n_interval)
    log(f"  ✗ Health check FAILED on :{port} after {n_retries} retries ({total_seconds}s)")
    return False


def get_db() -> sqlite3.Connection:
    """Open the SQLite database."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_pending_deployments(db: sqlite3.Connection) -> list[dict]:
    """Find deployments with current_step = 'code_committed'.

    Filters out per-project commits — those have their own deploy
    lifecycle via the Deploy/Stop endpoints and shouldn't trigger the
    platform redeploy. A commit is per-project when its underlying
    request has a non-null ``project_id``.

    Without this filter, every project task would cause the platform
    stack to rebuild and restart, which was the original bug: the user
    saw their project's "build a login page" task trigger an Agent
    Team app redeploy.
    """
    cursor = db.execute(
        # LEFT JOIN — code_committed rows always have a request, but
        # defensive in case a manual row got inserted without one.
        """
        SELECT ds.*, r.project_id AS request_project_id
        FROM deployment_states ds
        LEFT JOIN requests r ON r.request_id = ds.request_id
        WHERE ds.current_step = 'code_committed'
        ORDER BY ds.started_at
        """
    )
    rows = []
    skipped_project_rows = 0
    for row in cursor.fetchall():
        d = dict(row)
        if d.get("request_project_id"):
            # Mark the row "skipped" so it doesn't sit in code_committed
            # forever (which would make a future supervisor pickup
            # process it). The per-project Deploy endpoint owns this
            # request's deploy lifecycle.
            mark_project_commit_skipped(db, d["deployment_id"])
            skipped_project_rows += 1
            continue
        rows.append(d)
    if skipped_project_rows:
        log(
            f"  ⏭  skipped {skipped_project_rows} per-project commit(s) — "
            f"those deploy via the per-project Deploy endpoint, not the supervisor"
        )
    return rows


def mark_project_commit_skipped(db: sqlite3.Connection, deployment_id: str) -> None:
    """Transition a per-project commit row to a terminal 'skipped'
    state so it doesn't keep appearing in the pending poll. Records
    a step in step_history so the UI / audit log shows why."""
    cursor = db.execute(
        "SELECT step_history FROM deployment_states WHERE deployment_id = ?",
        (deployment_id,),
    )
    row = cursor.fetchone()
    history = json.loads(row["step_history"]) if row and row["step_history"] else []
    history.append({
        "step": "supervisor_skipped",
        "status": "skipped",
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "detail": (
            "Per-project commit — supervisor does not deploy these. "
            "Use the project's Deploy button to start the project's stack."
        ),
    })
    db.execute(
        "UPDATE deployment_states SET current_step = ?, step_history = ?, "
        "completed_at = ? WHERE deployment_id = ?",
        ("skipped", json.dumps(history), datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), deployment_id),
    )
    db.commit()


# Non-terminal "in flight" states. If a row is sitting in one of these when
# the supervisor restarts, it means the supervisor was actively processing
# that row and got interrupted (container crash, OS reboot, manual restart).
# The poller only picks up 'code_committed' rows, so without cleanup these
# orphaned rows would sit forever — and the devops_specialist polling them
# via wait_for_deployment would time out at the 10-minute mark with no useful
# information. Mark them failed at startup with a clear reason instead.
IN_FLIGHT_STATES = (
    "judging", "syncing", "building",
    "staging_deploying", "staging_healthy",
    "prod_deploying",
    "rolling_back",
)


def cleanup_stale_inflight_rows(db: sqlite3.Connection) -> int:
    """Mark any rows the supervisor was mid-processing as failed.

    Returns the number of rows touched. Called once at supervisor startup —
    after this, the poller's `code_committed` filter is the only source of
    pickups, so there's no race with active processing.
    """
    placeholders = ",".join("?" * len(IN_FLIGHT_STATES))
    cursor = db.execute(
        f"SELECT deployment_id, request_id, current_step "
        f"FROM deployment_states WHERE current_step IN ({placeholders})",
        IN_FLIGHT_STATES,
    )
    rows = cursor.fetchall()
    if not rows:
        return 0
    for row in rows:
        update_step(
            db, row["deployment_id"], "failed",
            f"Supervisor was interrupted while in step '{row['current_step']}'. "
            f"Mark failed on restart so the devops_specialist sees a definitive "
            f"outcome instead of polling forever.",
            error="Supervisor interrupted mid-deploy (restart cleanup)",
        )
        log(
            f"  🧹 cleanup: marked {row['request_id']} (was {row['current_step']}) as failed"
        )
    return len(rows)


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
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "detail": detail,
    })

    # Update
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
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


def rollback(
    db: sqlite3.Connection, deployment_id: str, rollback_sha: str, commit_sha: str = "",
) -> None:
    """Rollback: git revert + stop containers + rebuild + restart.

    Hardened against several real-world breakage modes seen in prior cycles:
      1. Dirty working tree blocks `git revert`. We stash --include-untracked
         before reverting and restore after.
      2. Local main might be behind origin/main, so HEAD doesn't point at the
         commit we just deployed. We `git fetch + checkout main` and verify
         HEAD matches the commit_sha we were supposed to revert before
         touching anything.
      3. Any rollback substep failing was previously ignored — the supervisor
         would barrel into `docker compose up` afterwards and pretend prod was
         deploying. Now any failure marks the row error and returns early.
    """
    log("🔄 ROLLING BACK...")
    update_step(db, deployment_id, "rolling_back", "Initiating rollback")

    # 1. Stash any uncommitted working-tree changes so `git revert` can run.
    #    Always restore at the end (success OR failure) so we don't strand
    #    user WIP that happens to live in the supervisor's bound project dir.
    stash_label = f"supervisor-rollback-{deployment_id[:8]}"
    code, stash_out = run_cmd(
        f'git stash push --include-untracked -m "{stash_label}"', timeout=30,
    )
    stashed = code == 0 and "No local changes" not in stash_out

    try:
        if rollback_sha and commit_sha:
            # 2. Make sure local HEAD actually points at the commit we shipped.
            #    Without this, `git revert HEAD` reverts whatever happens to be
            #    at the top of local main — which may be a totally unrelated
            #    commit if local has drifted from origin.
            run_cmd("git fetch origin main", timeout=30)
            run_cmd("git checkout main", timeout=15)
            code, head_sha = run_cmd("git rev-parse HEAD", timeout=10)
            head_sha = head_sha.strip()
            if not head_sha.startswith(commit_sha):
                # Hard-reset local to origin/main so HEAD = the shipped commit,
                # then revert it. We don't blindly revert HEAD without this.
                code, _ = run_cmd(
                    f"git reset --hard {commit_sha}", timeout=30,
                )
                if code != 0:
                    update_step(
                        db, deployment_id, "rolled_back",
                        f"Could not sync local main to commit {commit_sha[:8]} for revert",
                        error="Rollback aborted — local main drift",
                    )
                    return

            code, _ = run_cmd("git revert HEAD --no-edit", timeout=30)
            if code != 0:
                update_step(
                    db, deployment_id, "rolled_back",
                    "git revert failed — see supervisor logs for details",
                    error="Rollback aborted — git revert returned non-zero",
                )
                return
            code, _ = run_cmd("git push origin main", timeout=30)
            if code != 0:
                update_step(
                    db, deployment_id, "rolled_back",
                    "git push of revert failed",
                    error="Rollback aborted — could not push revert to origin",
                )
                return

        # 3. Stop staging (best-effort). Prod was removed from the supervisor
        #    flow, so no prod to tear down here.
        run_cmd(f"docker compose -f {STAGING_COMPOSE} down", timeout=30)

        # 4. Rebuild dev with reverted code so the running dev stack reflects
        #    the rollback state.
        code, _ = run_cmd("docker compose build", timeout=300)
        if code != 0:
            update_step(
                db, deployment_id, "rolled_back",
                "Rebuild after revert failed",
                error="Rollback aborted — could not rebuild reverted code",
            )
            return

        # 5. Bring dev back up with the reverted image. 180s window because
        # cold container start on Windows can easily take 60–90s, and a
        # rollback that hits its own timeout strands the user with both
        # the failed deploy AND a non-running dev environment.
        code, _ = run_cmd(f"docker compose -f {DEV_COMPOSE} up -d", timeout=180)
        if code != 0:
            update_step(
                db, deployment_id, "rolled_back",
                "docker compose dev up after revert failed",
                error="Rollback aborted — could not bring up reverted dev",
            )
            return

        if health_check(8000):
            update_step(
                db, deployment_id, "rolled_back",
                "Rollback successful — dev restored to pre-deploy state on :8000",
            )
        else:
            update_step(
                db, deployment_id, "rolled_back",
                "Rollback completed but dev health check uncertain on :8000",
                error="Rollback uncertain — dev not responding on :8000 after revert",
            )
    finally:
        if stashed:
            run_cmd("git stash pop", timeout=30)


def _trigger_ops_monitor_async(request_id: str, deployment_id: str) -> None:
    """POST to /api/v1/ops/monitor so the backend spawns the ops_heal_agent.

    Called after a successful deployment. Fire-and-forget — any HTTP error is
    logged but does not affect the deployment outcome. The ops_heal_agent runs
    inside the backend container and emits ops.* WebSocket events independently.

    Uses OPS_SECRET env var if set; omits the field otherwise (dev default).
    """
    import json as _json

    secret = os.getenv("OPS_SECRET", "").strip()
    host = os.getenv("HEALTHCHECK_HOST", "localhost")
    url = f"http://{host}:8000/api/v1/ops/monitor"
    payload = _json.dumps({
        "request_id": request_id,
        "deployment_id": deployment_id,
        "secret": secret,
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read(256).decode("utf-8", errors="replace")
            log(f"  🔬 ops_heal_agent triggered: {body[:80]}")
    except Exception as exc:
        # Non-fatal — ops monitoring is best-effort
        log(f"  ⚠  ops_monitor trigger failed (non-fatal): {exc}")


def _get_quality_risk(db: sqlite3.Connection, request_id: str) -> str:
    """Read the Quality Guardian's risk rating from the documents table.

    The quality_guardian emits `Risk: low`, `Risk: medium`, or `Risk: high`
    in its report, stored with doc_type='quality_report'. This rating is
    passed to the deploy-judge LLM so it can calibrate deploy strategy:
    high → prefer deploy_staging_only; low → can lower overall risk estimate.

    Returns "low", "medium", or "high". Defaults to "unknown" if the
    quality_report doesn't exist (e.g. docs-only request where quality_guardian
    was not part of the workflow).
    """
    try:
        cursor = db.execute(
            "SELECT content FROM documents "
            "WHERE request_id = ? AND doc_type = 'quality_report' "
            "ORDER BY created_at DESC LIMIT 1",
            (request_id,),
        )
        row = cursor.fetchone()
        if not row or not row["content"]:
            return "unknown"
        # Parse the "Risk: low/medium/high" line the quality_guardian always emits
        for line in row["content"].splitlines():
            stripped = line.strip().lower()
            if stripped.startswith("risk:"):
                val = stripped[5:].strip()
                if val in ("low", "medium", "high"):
                    return val
        return "unknown"
    except Exception as e:
        log(f"  ⚠  _get_quality_risk failed: {e} — defaulting to unknown")
        return "unknown"


def _persist_judge_result(
    db: sqlite3.Connection, deployment_id: str, result,
) -> None:
    """Write the judge's decision back to the deployment_states row so the UI
    and audit trail can see WHY the supervisor took the action it did."""
    db.execute(
        """UPDATE deployment_states
           SET strategy=?, strategy_reasoning=?, risk=?
           WHERE deployment_id=?""",
        (result.strategy, result.reasoning, result.risk, deployment_id),
    )
    db.commit()


def deploy(db: sqlite3.Connection, deployment: dict) -> None:
    """Execute the full deployment pipeline for a single deployment."""
    dep_id = deployment["deployment_id"]
    req_id = deployment["request_id"]
    rollback_sha = deployment["rollback_sha"]
    commit_sha = deployment.get("commit_sha", "")
    files_committed_raw = deployment.get("files_committed") or "[]"
    try:
        files_committed = json.loads(files_committed_raw)
    except json.JSONDecodeError:
        files_committed = []

    log(f"🚀 Starting deployment {dep_id} for {req_id} (sha={commit_sha[:8] or '?'})")

    # ── Step 0: Sync local working tree with origin/main (UNCONDITIONAL) ─────
    # This used to run AFTER the judge step, which meant it was skipped when
    # the judge said "skip" or "hold" — leaving the host filesystem out of
    # sync with GitHub even when the user only wanted docs they could read
    # locally. Now sync runs FIRST so the local folder reflects origin/main
    # regardless of whether docker work follows. The judge's decision then
    # only governs docker (build/staging/prod), not file-system mirroring.
    #
    # We do a SURGICAL `git fetch` + `git checkout origin/main -- <files>`
    # rather than `git pull` / `git reset --hard` so we don't clobber any
    # uncommitted work on other paths.
    if files_committed:
        update_step(db, dep_id, "syncing", f"Fetching {len(files_committed)} file(s) from origin/main")
        run_cmd("git fetch origin main", timeout=30)
        # Pass paths via argv (list) — no shell, no quoting needed. The
        # previous shell-string version wrapped each path in single quotes,
        # which broke on Windows: cmd.exe does not strip single quotes the
        # way /bin/sh does, so git received literal `'path'` as the pathspec
        # and 404'd every file. argv form sidesteps the shell entirely.
        code, output = run_cmd(
            ["git", "checkout", "origin/main", "--", *files_committed],
            timeout=30,
        )
        if code != 0:
            update_step(
                db, dep_id, "syncing", output[:200],
                error="git checkout from origin/main failed — files may not exist on remote yet",
            )
            return
        update_step(
            db, dep_id, "syncing",
            f"Synced {len(files_committed)} file(s) from origin/main",
        )

    # ── Judge step (hybrid agent — Milestone 1) ─────────────────────────────
    # Ask the deployment-judge LLM whether to do docker work. By the time the
    # judge runs, the local working tree already has the new files (synced
    # above), so even SKIP/HOLD outcomes leave the host filesystem current.
    try:
        from judge import JudgeResult, evaluate_deployment  # local import; runs in supervisor only
    except Exception as e:
        log(f"  ✗ judge module import failed: {e} — using safe default")
        JudgeResult = None  # type: ignore
        result = None
    else:
        update_step(db, dep_id, "judging", "Asking deployment judge for strategy")
        quality_risk = _get_quality_risk(db, req_id)
        log(f"  🛡️  quality_guardian risk: {quality_risk}")
        result = evaluate_deployment(
            commit_sha=commit_sha,
            request_id=req_id,
            files_committed=files_committed,
            rollback_sha=rollback_sha,
            quality_risk=quality_risk,
        )
        _persist_judge_result(db, dep_id, result)
        log(
            f"  ⚖  judge: strategy={result.strategy} risk={result.risk} "
            f"from_llm={result.from_llm}"
        )
        log(f"      reasoning: {result.reasoning[:200]}")

        # Branch on the judge's strategy. SKIP and HOLD bypass docker entirely
        # — but the file sync above ALREADY ran, so the local folder is current
        # regardless of which branch we take here.
        if result.strategy == "skip":
            update_step(
                db, dep_id, "completed",
                f"Skipped per judge: {result.reasoning[:200]}",
            )
            log("  ⏭  judge said skip — no docker work needed (files already synced)")
            return
        if result.strategy == "hold":
            update_step(
                db, dep_id, "on_hold",
                f"On hold per judge: {result.reasoning[:200]}",
            )
            log("  ⏸  judge said hold — deployment paused for manual review (files already synced)")
            return
        # For deploy_full and deploy_staging_only, fall through and execute.

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
        rollback(db, dep_id, rollback_sha, commit_sha)
        return

    # Step 3: Verify staging health
    log("Checking staging health...")
    if not health_check(8010):
        update_step(db, dep_id, "staging_deploying", "Staging unhealthy", error="Staging health check failed")
        run_cmd(f"docker compose -f {STAGING_COMPOSE} down")
        rollback(db, dep_id, rollback_sha, commit_sha)
        return

    update_step(db, dep_id, "staging_healthy", "Staging deployed and healthy on :8010/:3010")

    # Step 4: Tear staging down IMMEDIATELY now that its healthcheck passed.
    # Staging is a transient validation environment — its only job was to prove
    # the new image starts cleanly and responds to /health. Once that's
    # confirmed, keeping staging up while we rebuild dev wastes 2-4 minutes of
    # RAM/CPU on a container that does no further work. Previously the teardown
    # ran AFTER dev rebuild, leaving staging idle for ~80% of its lifetime.
    log("Staging validated. Tearing down staging before dev rebuild (no more idle time)...")
    run_cmd(f"docker compose -f {STAGING_COMPOSE} down", timeout=30)

    # Step 5: Rebuild dev environment with the new code.
    #
    # Prod deployment was removed earlier (per user request). The judge's
    # "deploy_staging_only" and "deploy_full" both reach this point.
    #
    # Healthcheck strategy for dev:
    #   1. Generous initial window (120s) for the dev backend's heavy cold start
    #      (9 agents + 17 tools + StateStore + Anthropic client init).
    #   2. If the first window fails, do ONE restart attempt — catches the
    #      "container started but uvicorn race-condition'd on first init" case
    #      we hit on REQ-3EED7F. Then poll another 80s.
    #   3. If dev STILL isn't healthy: mark the deployment FAILED. UI shows a
    #      definitive ❌ rather than silently leaving the user with a broken dev.
    log("Rebuilding dev environment...")
    run_cmd(f"docker compose -f {DEV_COMPOSE} down", timeout=30)
    run_cmd(f"docker compose -f {DEV_COMPOSE} up -d --build", timeout=180)
    dev_healthy = health_check(
        8000,
        retries=DEV_HEALTH_CHECK_RETRIES,
        interval=DEV_HEALTH_CHECK_INTERVAL,
    )

    if not dev_healthy:
        log("Dev didn't become healthy in 120s — attempting one restart...")
        run_cmd(f"docker compose -f {DEV_COMPOSE} restart", timeout=60)
        # Shorter second window — if the restart didn't fix it within 80s,
        # we're not just racing init; something is genuinely wrong.
        dev_healthy = health_check(8000, retries=20, interval=4)

    if dev_healthy:
        update_step(
            db, dep_id, "completed",
            "Staging validated + torn down; dev rebuilt and healthy on :8000.",
        )
        log(f"✅ Deployment {dep_id} COMPLETED (dev live)")
        # Trigger the ops_heal_agent to verify the running stack is healthy.
        # Fire-and-forget — POST to the backend which spawns the agent as a
        # background task. Uses OPS_SECRET if set (see .env / src/api/routes/ops.py).
        _trigger_ops_monitor_async(req_id, dep_id)
    else:
        update_step(
            db, dep_id, "failed",
            "Dev did not become healthy on :8000 after rebuild + restart (~200s total).",
            error="Dev healthcheck failed after rebuild + one retry — manual intervention needed",
        )
        log(f"✗ Deployment {dep_id} FAILED (staging deployed OK; dev never came up)")


# How often the supervisor checks origin/main for new commits to mirror down.
# The poll loop runs every POLL_INTERVAL (5s) — mirroring on every tick is
# essentially free (a single `git fetch` over HTTPS) and gives users a hard
# upper-bound on staleness: any commit on origin/main appears in the local
# working tree within ~5s as long as the supervisor is running.
def mirror_origin_main() -> None:
    """Keep the supervisor's project root (= main repo on host) in sync with
    origin/main, independent of the deploy state machine.

    Why this exists: without it, the local folder only updates when a deploy
    runs the per-file sync step (and only for files in that specific commit).
    Commits made manually, commits judged 'skip' before this change, or any
    commit produced while the supervisor was down all leave local main behind.
    This loop closes that gap by continuously mirroring origin/main.

    Safety properties:
      - Uses `git fetch` then `git merge --ff-only` — NEVER creates merge
        commits or overwrites local work. If origin and local have diverged
        (someone committed locally), ff-merge fails cleanly and we leave the
        working tree alone.
      - Skips silently when fetch fails (network blip — retry next cycle).
      - Skips silently when ff-merge fails (local has uncommitted changes that
        conflict with incoming commits — user must resolve manually).
      - Only logs when mirror actually moves the HEAD forward, so the log
        stream isn't flooded with "0 commits" messages every 5s.
    """
    code, _ = run_cmd("git fetch origin main", timeout=15)
    if code != 0:
        return  # transient — try again next poll
    code, behind_out = run_cmd("git rev-list --count HEAD..origin/main", timeout=10)
    if code != 0:
        return
    try:
        behind = int(behind_out.strip())
    except ValueError:
        return
    if behind == 0:
        return  # already in sync
    code, output = run_cmd("git merge --ff-only origin/main", timeout=15)
    if code != 0:
        # User has local commits or working-tree changes blocking ff-merge.
        # Don't try harder — they may have intentional WIP. Log a one-time
        # diagnostic each cycle while behind so they can see why.
        log(f"  ⚠  origin/main is {behind} commit(s) ahead but ff-merge blocked: {output[:120]}")
        return
    log(f"  📥 mirrored {behind} commit(s) from origin/main into local main")


def _setup_github_auth() -> None:
    """Configure git to authenticate as the GitHub bot using GITHUB_TOKEN.

    Without this, `git push origin main` (the rollback step) fails with
    "could not read Username for 'https://github.com'" — even though the
    GitHub Trees API used by the agent pipeline succeeds with the same
    token. Reason: `git` uses the credential helper, which has no token
    knowledge by default.

    We use the URL-rewrite approach (`git config url.<token>@github.com`)
    rather than .netrc because it's simpler and the token is already in
    process env. The token never lands on disk in any config file.
    """
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        log("⚠️  GITHUB_TOKEN not set — git push for rollback will fail")
        return
    # Rewrite all https://github.com URLs to include the token. Affects only
    # git operations in this container's HOME (~/.gitconfig).
    run_cmd(
        f'git config --global url."https://x-access-token:{token}@github.com/".insteadOf "https://github.com/"',
        timeout=10,
    )
    log("🔑 GitHub credential helper configured for rollback push")


# ── Per-project Deploy / Stop ─────────────────────────────────────────
# When the user clicks Deploy on the Project Detail page, the backend
# flips ``projects.deploy_status`` to ``pending_deploy``. This function
# (called from the main loop) picks those rows up and runs the actual
# ``docker compose up -d --build`` from the host (where the daemon is)
# against the project's scaffolded compose file at
# ``C:/ai-projects/<Name>/docker-compose.yml``.
#
# We run on the host for the same reason the platform supervisor does:
# inside-container ``docker compose`` would resolve relative compose
# paths against the container filesystem, not the host, and bind-mounts
# would break (see CLAUDE.md "Supervisor scope").

# Where projects live on the host. Read from env so the user can
# override the layout if their workspace lives elsewhere.
PROJECT_HOST_ROOT = pathlib.Path(os.getenv("HOST_PROJECT_ROOT", "C:/ai-projects"))


def get_pending_project_deploys(db: sqlite3.Connection) -> list[dict]:
    """Return projects whose deploy_status is pending_deploy or pending_stop.

    Also pulls ``deploy_pending_action`` (set by the AI Deploy Judge's
    Apply/Override endpoints) and ``last_deploy_commit_sha`` so
    ``_run_project_deploy`` can dispatch the right docker invocation
    instead of always doing a full rebuild-all."""
    cursor = db.execute(
        "SELECT project_id, name, kind, deploy_status, "
        "deploy_backend_port, deploy_frontend_port, "
        "deploy_pending_action, last_deploy_commit_sha "
        "FROM projects "
        "WHERE deploy_status IN ('pending_deploy', 'pending_stop')"
    )
    return [dict(row) for row in cursor.fetchall()]


def update_project_deploy_status(
    db: sqlite3.Connection,
    project_id: str,
    *,
    status: str,
    url: str | None = None,
    error: str | None = None,
    clear_pending_action: bool = False,
) -> None:
    """Write status/url/error back to the projects row. Mirrors the
    backend's ``update_project_deploy`` so the UI sees the same state
    transitions whether the change came from the backend or here.

    When ``clear_pending_action=True``, also nulls out
    ``deploy_pending_action``. Callers pass True when transitioning
    to a terminal state (running, failed, stopped) so the next
    pending_deploy doesn't accidentally inherit the previous
    action."""
    sets = ["deploy_status = ?"]
    params: list = [status]
    if url is not None:
        sets.append("deploy_url = ?")
        params.append(url)
    if error is not None:
        sets.append("deploy_error = ?")
        # Empty string → NULL for tidiness, matches the backend convention.
        params.append(error or None)
    if clear_pending_action:
        sets.append("deploy_pending_action = NULL")
    sets.append("updated_at = ?")
    params.append(datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
    params.append(project_id)
    db.execute(
        f"UPDATE projects SET {', '.join(sets)} WHERE project_id = ?",
        tuple(params),
    )
    db.commit()
    # Force the WAL contents into the main DB file. Without this,
    # the backend's long-lived aiosqlite connection (in a separate
    # process) keeps its pre-update read snapshot and never sees the
    # ``deploy_status = running`` write — the UI gets stuck on
    # ``deploying`` until the backend restarts. PASSIVE checkpoint
    # doesn't block on readers, so it's safe to call frequently.
    try:
        db.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except sqlite3.OperationalError:
        # Checkpoint can fail if the WAL is currently in use; safe
        # to swallow — next caller will retry.
        pass


def process_project_deploy_requests(db: sqlite3.Connection) -> None:
    """Poll cycle for per-project Deploy / Stop. Runs in the same tick
    as the platform autodeploy poll. Each pending row gets handled
    inline (blocks the main loop for the duration) — ``docker compose
    up`` with caching is typically <30s after the first build."""
    for row in get_pending_project_deploys(db):
        status = row["deploy_status"]
        if status == "pending_deploy":
            _run_project_deploy(db, row)
        elif status == "pending_stop":
            _run_project_stop(db, row)


# ── AI Deploy Judge → docker compose action mapping ───────────────
#
# Each action the judge can recommend maps to a specific docker compose
# invocation. Per-tier restarts skip the build step entirely (~3-5s vs.
# 30-60s rebuild) — the judge picks them when only source files
# changed and no dependency was bumped. The rebuild-* actions force a
# rebuild of the specific tier without disturbing the other one.
# Unknown / NULL actions fall back to the legacy rebuild-all path so
# manual Deploy button clicks (pre-judge) keep working.

_ACTION_REBUILD_ALL = ("up", "-d", "--build")  # legacy default

_ACTION_TO_COMPOSE_ARGS: dict[str, tuple[str, ...]] = {
    "restart-backend":  ("restart", "backend"),
    "restart-frontend": ("restart", "frontend"),
    "rebuild-backend":  ("up", "-d", "--build", "backend"),
    "rebuild-frontend": ("up", "-d", "--build", "frontend"),
    "rebuild-all":      _ACTION_REBUILD_ALL,
}


def _compose_args_for_action(action: str | None) -> tuple[str, ...]:
    """Resolve a DeployAction value to the args we append to
    ``docker compose -p <slug> -f <path>``. Defaults to rebuild-all
    for NULL / unknown actions so legacy manual deploys (which don't
    set deploy_pending_action) keep their previous behaviour."""
    if not action:
        return _ACTION_REBUILD_ALL
    args = _ACTION_TO_COMPOSE_ARGS.get(action)
    if args is None:
        # Unknown action (e.g. skip / hold that should have been
        # handled in the backend) — be safe and rebuild-all rather
        # than skip the action entirely.
        log(f"  ⚠  unknown deploy action {action!r}; falling back to rebuild-all")
        return _ACTION_REBUILD_ALL
    return args


def _project_compose_path(name: str) -> pathlib.Path:
    """``C:/ai-projects/<Name>/docker-compose.yml`` — what we hand to
    ``docker compose -f``. The backend already validated the name when
    the project was created, so this is safe to concat."""
    return PROJECT_HOST_ROOT / name / "docker-compose.yml"


def _project_compose_project_name(name: str) -> str:
    """Compose project name = ``project-<slug>``. Mirrors the value
    baked into the scaffolded compose file by the backend's
    ``project_repo_slug`` substitution, so deploy/stop target the same
    container set."""
    slug = "".join(c if c.isalnum() else "-" for c in name.lower())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"project-{slug.strip('-')}"


def _run_project_deploy(db: sqlite3.Connection, row: dict) -> None:
    """Run the docker compose action selected by the AI Deploy Judge
    (``row['deploy_pending_action']``), or the legacy
    ``up -d --build`` if no action is set.

    Action → docker compose args mapping lives in
    ``_compose_args_for_action`` above. Restarts skip the build step
    (~3-5s); rebuild-<tier> rebuilds just one container; rebuild-all
    matches the pre-judge behaviour. Skip/hold are handled in the
    backend route and never reach here.

    Terminal states (running, failed) clear ``deploy_pending_action``
    via ``update_project_deploy_status(clear_pending_action=True)`` so
    the next pending_deploy starts from a clean slate."""
    pid = row["project_id"]
    name = row["name"]
    kind = row["kind"]
    backend_port = row["deploy_backend_port"]
    frontend_port = row["deploy_frontend_port"]
    action = row.get("deploy_pending_action") or "rebuild-all"

    log(
        f"📦 deploy: {name} ({pid}) kind={kind} action={action} "
        f"backend={backend_port} frontend={frontend_port}"
    )

    compose_path = _project_compose_path(name)
    if not compose_path.exists():
        msg = f"docker-compose.yml not found at {compose_path}"
        log(f"  ❌ {msg}")
        update_project_deploy_status(
            db, pid, status="failed", error=msg, clear_pending_action=True,
        )
        return

    # Transition pending_deploy → deploying so the UI shows progress.
    # `deploy_pending_action` stays set until we hit a terminal state.
    update_project_deploy_status(db, pid, status="deploying", error="")

    project_name = _project_compose_project_name(name)
    compose_args = _compose_args_for_action(action)
    cmd = [
        "docker", "compose",
        "-p", project_name,
        "-f", str(compose_path),
        *compose_args,
    ]
    log(f"  ▶  {' '.join(cmd)}")
    # Restart actions are quick (<10s typically) — give them a tight
    # timeout so a stuck container doesn't tie up the supervisor for
    # 10 minutes. Rebuilds and rebuild-all keep the original 10-min cap.
    is_restart = compose_args[0] == "restart"
    timeout_s = 60 if is_restart else 600
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        msg = f"docker compose {action} timed out after {timeout_s}s"
        log(f"  ❌ {msg}")
        update_project_deploy_status(
            db, pid, status="failed", error=msg, clear_pending_action=True,
        )
        return
    except Exception as e:
        msg = f"docker compose invocation failed: {e}"
        log(f"  ❌ {msg}")
        update_project_deploy_status(
            db, pid, status="failed", error=msg, clear_pending_action=True,
        )
        return

    if result.returncode != 0:
        # Take the LAST few lines of stderr; full output is too long for the
        # deploy_error column / UI.
        tail = (result.stderr or result.stdout or "").splitlines()[-15:]
        msg = "\n".join(tail) or f"exit code {result.returncode}"
        log(f"  ❌ {action} failed (exit {result.returncode}):\n{msg}")
        update_project_deploy_status(
            db, pid, status="failed", error=msg, clear_pending_action=True,
        )
        return

    # Pick the launch URL: frontend port for kinds that have one,
    # backend port for api-service.
    if kind == "api-service":
        url = f"http://localhost:{backend_port}" if backend_port else ""
    else:
        url = f"http://localhost:{frontend_port}" if frontend_port else ""
    log(f"  ✅ {action} ok — {url}")
    update_project_deploy_status(
        db, pid, status="running", url=url, error="", clear_pending_action=True,
    )


def _run_project_stop(db: sqlite3.Connection, row: dict) -> None:
    """Run ``docker compose -p <project> down`` and flip status to
    ``stopped``. Failure here is rare but still soft-fails to ``failed``
    so the UI can show why."""
    pid = row["project_id"]
    name = row["name"]
    log(f"🛑 stop: {name} ({pid})")

    compose_path = _project_compose_path(name)
    if not compose_path.exists():
        # Stop with no compose file is fine — mark stopped, clear URL.
        update_project_deploy_status(db, pid, status="stopped", url="", error="")
        return

    update_project_deploy_status(db, pid, status="stopping")
    project_name = _project_compose_project_name(name)
    cmd = [
        "docker", "compose",
        "-p", project_name,
        "-f", str(compose_path),
        "down",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as e:
        msg = f"docker compose down failed: {e}"
        log(f"  ❌ {msg}")
        update_project_deploy_status(db, pid, status="failed", error=msg)
        return

    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").splitlines()[-10:]
        msg = "\n".join(tail) or f"exit code {result.returncode}"
        log(f"  ❌ stop failed (exit {result.returncode}):\n{msg}")
        update_project_deploy_status(db, pid, status="failed", error=msg)
        return

    log("  ✅ stopped")
    update_project_deploy_status(db, pid, status="stopped", url="", error="")


# ── AET-24 — Deploy health probe loop ────────────────────────────────────


# Env → host port mapping. Matches docker-compose.{env}.yml port
# bindings; kept in lockstep with src/main.py::_cors_default_map (L23
# cross-layer drift defence — both files name the same env→port pairs).
_ENV_PORT_MAP: dict[str, int] = {
    "development": 8000,
    "staging":     8010,
    "production":  8020,
    "demo":        8030,
}

# Probe cadence — every 60 s as AET-24 spec says. POLL_INTERVAL is 5s
# so we fire on every 12th tick. Tracked via a process-local counter
# (no DB state) so a supervisor restart re-aligns to a fresh window
# without missing a probe.
HEALTH_PROBE_INTERVAL_S = 60
_PROBE_TICK_COUNTER: dict[str, int] = {"n": 0}


def _probe_one_env(env: str, port: int, deploy_id: str) -> dict:
    """Synchronously probe one environment's /api/v1/health endpoint.

    Returns a dict the caller stuffs into ``deploy_health`` columns +
    ``raw_metrics_json``. Total budget: 5 s (the urlopen timeout). On
    any failure the row still lands — we record http_status=0 so the
    anomaly detector (AET-26) can see "env stopped responding" as a
    real signal, not as a missing sample."""
    host = os.getenv("HEALTHCHECK_HOST", "localhost")
    url = f"http://{host}:{port}/api/v1/health"
    started = time.monotonic()
    status_code = 0
    response_body = ""
    error: str | None = None
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            status_code = resp.status
            # Cap body read — /health is small but a misbehaving env could
            # stream gigabytes; we only need enough to confirm it's JSON.
            response_body = resp.read(2048).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        status_code = e.code
        error = f"HTTP {e.code}"
    except urllib.error.URLError as e:
        error = f"URLError: {e.reason}"
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    elapsed_ms = int((time.monotonic() - started) * 1000)

    return {
        "deploy_id": deploy_id,
        "env": env,
        "response_time_ms": elapsed_ms,
        "http_status": status_code,
        # AET-26 will compute error_rate_5m from rolling history; this
        # single probe contributes its own bit (1.0 = error, 0.0 = OK).
        # Writing the per-probe contribution NOT the rolling rate keeps
        # the column meaningful without a SQL JOIN at write time.
        "error_rate_5m": 0.0 if 200 <= status_code < 300 else 1.0,
        "restart_count": 0,  # populated by docker-stats probe in a later AET
        "raw_metrics_json": json.dumps({
            "url": url,
            "error": error,
            "response_body_preview": response_body[:200],
            "probe_source": "supervisor.health_probe_tick",
        }),
    }


def _latest_deploy_id_for(db: sqlite3.Connection, env: str) -> str:
    """Find the deployment_id of the most recent completed deploy.
    Falls back to a synthetic ``platform-{env}`` ID when no deploy
    has reached completion yet — the probe still lands so we capture
    cold-start data, but the deploy_id makes it obvious this row is
    not tied to a specific rollout."""
    try:
        cur = db.execute(
            """SELECT deployment_id
               FROM deployment_states
               WHERE current_step = 'completed'
               ORDER BY started_at DESC LIMIT 1"""
        )
        row = cur.fetchone()
        if row:
            return row[0]
    except Exception:
        pass
    return f"platform-{env}"


def _insert_probe_row(db: sqlite3.Connection, fields: dict) -> None:
    """Write one probe row. Generates a probe_id with millisecond
    precision so the supervisor restart loop can't collide. INSERT
    OR IGNORE protects against a transient retry."""
    probe_id = (
        f"PROBE-{fields['env']}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}"
    )
    db.execute(
        """INSERT OR IGNORE INTO deploy_health
           (probe_id, deploy_id, env, recorded_at,
            response_time_ms, error_rate_5m, http_status,
            restart_count, raw_metrics_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            probe_id,
            fields["deploy_id"],
            fields["env"],
            datetime.now(timezone.utc).isoformat(),
            fields["response_time_ms"],
            fields["error_rate_5m"],
            fields["http_status"],
            fields["restart_count"],
            fields["raw_metrics_json"],
        ),
    )
    db.commit()


def health_probe_tick(db: sqlite3.Connection) -> None:
    """Called once per supervisor poll iteration; runs the actual
    probe only every Nth tick where N = HEALTH_PROBE_INTERVAL_S /
    POLL_INTERVAL. Crash-resilient: any per-env probe failure logs
    and continues; one bad env can't stop the others from being
    recorded. Idempotent under restart — counter resets to 0, so
    the next tick fires immediately and the cadence reconverges
    within one window.
    """
    _PROBE_TICK_COUNTER["n"] += 1
    ticks_per_probe = max(1, HEALTH_PROBE_INTERVAL_S // POLL_INTERVAL)
    if _PROBE_TICK_COUNTER["n"] % ticks_per_probe != 0:
        return

    # Determine which envs to probe. We probe ANY env that has a
    # configured port AND whose dev-stack docker-compose file exists
    # at the project root — the file's presence is the cheapest
    # "is this env even installed?" check available without a docker
    # round-trip. Avoids logging cold-port failures on a user's box
    # that doesn't run prod/demo locally.
    envs_to_probe: list[tuple[str, int]] = []
    for env, port in _ENV_PORT_MAP.items():
        compose_path = PROJECT_ROOT / f"docker-compose.{env if env != 'development' else ''}.yml".replace("..yml", ".yml").lstrip(".")
        # development uses the root docker-compose.yml; everything else
        # has a docker-compose.{env}.yml sibling.
        if env == "development":
            compose_path = PROJECT_ROOT / "docker-compose.yml"
        else:
            compose_path = PROJECT_ROOT / f"docker-compose.{env}.yml"
        if compose_path.exists():
            envs_to_probe.append((env, port))

    for env, port in envs_to_probe:
        try:
            deploy_id = _latest_deploy_id_for(db, env)
            fields = _probe_one_env(env, port, deploy_id)
            _insert_probe_row(db, fields)
            # One-line log per env per probe — verbose but the supervisor
            # produces ~12 lines/minute total across 4 envs, well under
            # noise threshold. Useful for "is the probe even running?"
            # questions during AE-1 debugging.
            log(
                f"  📊 probe {env}:{port} → "
                f"{fields['http_status']} ({fields['response_time_ms']}ms)"
            )
        except Exception as e:  # noqa: BLE001
            # Crash-resilience contract: log and continue. Never let
            # one env's probe wedge the loop.
            log(f"  ⚠️  probe {env}:{port} failed: {e}")


def main() -> None:
    """Main supervisor loop — poll for pending deployments."""
    log("=" * 60)
    log("Deployment Supervisor started")
    log(f"Project root: {PROJECT_ROOT}")
    log(f"Database: {DB_PATH}")
    log(f"Poll interval: {POLL_INTERVAL}s")
    log("=" * 60)

    _setup_github_auth()

    if not DB_PATH.exists():
        log(f"⚠️  Database not found at {DB_PATH} — waiting for app to create it...")
    else:
        # Sweep any orphaned in-flight rows from a previous supervisor instance.
        # See cleanup_stale_inflight_rows() for why this matters.
        try:
            db = get_db()
            n = cleanup_stale_inflight_rows(db)
            db.close()
            if n:
                log(f"🧹 Startup cleanup: marked {n} stale in-flight row(s) as failed")
        except Exception as e:
            log(f"⚠️  Startup cleanup failed: {e}")

    # Catch up local main → origin/main on first boot, before entering the
    # poll loop. Picks up any commits made while the supervisor was down.
    try:
        mirror_origin_main()
    except Exception as e:
        log(f"⚠️  Startup mirror failed: {e}")

    while True:
        try:
            # Keep local main in sync with origin/main every tick. Cheap
            # (single `git fetch` over HTTPS) and gives users a hard 5s upper
            # bound on local-vs-GitHub staleness, regardless of whether
            # there's an active deployment to process.
            mirror_origin_main()
            if DB_PATH.exists():
                db = get_db()
                pending = get_pending_deployments(db)
                if pending:
                    for deployment in pending:
                        deploy(db, deployment)
                # Per-project Deploy/Stop pickup. Same DB connection,
                # same poll cycle — keeps the user's host setup to one
                # process for both platform autodeploy and per-project
                # deploys.
                process_project_deploy_requests(db)
                # AET-24 — emit deploy_health probe rows every 60 s.
                # Cheap per-tick (just increments a counter) until the
                # 12th poll, then probes each installed env in series.
                # Wrapped in its own try because a probe failure must
                # NOT prevent the next deployment pickup.
                try:
                    health_probe_tick(db)
                except Exception as e:  # noqa: BLE001
                    log(f"⚠️  health_probe_tick crashed: {e}")
                db.close()
        except KeyboardInterrupt:
            log("Supervisor stopped by user")
            sys.exit(0)
        except Exception as e:
            log(f"Error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
