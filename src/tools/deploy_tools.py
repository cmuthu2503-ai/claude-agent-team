"""Deployment tools — wraps Docker Compose commands for agent use."""

import asyncio
from typing import Any

import structlog

logger = structlog.get_logger()


class DeployTool:
    """Wraps docker compose commands for deployment operations."""

    def __init__(self, project_root: str = ".") -> None:
        self.project_root = project_root

    def schema(self) -> dict[str, Any]:
        return {
            "name": "deployment",
            "description": "Deploy, rollback, and manage Docker Compose environments",
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["deploy", "rollback", "status", "logs", "down"],
                    },
                    "environment": {
                        "type": "string",
                        "enum": ["staging", "production", "demo"],
                    },
                },
                "required": ["action", "environment"],
            },
        }

    def _compose_file(self, environment: str) -> str:
        files = {
            "staging": "docker-compose.staging.yml",
            "production": "docker-compose.prod.yml",
            "demo": "docker-compose.demo.yml",
        }
        return files.get(environment, "docker-compose.yml")

    async def _run(self, cmd: str) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.project_root,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        return proc.returncode, stdout.decode(), stderr.decode()

    async def execute(self, params: dict) -> str:
        action = params["action"]
        env = params["environment"]
        compose_file = self._compose_file(env)

        if action == "deploy":
            return await self._deploy(compose_file, env)
        elif action == "rollback":
            return await self._rollback(compose_file, env)
        elif action == "status":
            return await self._status(compose_file)
        elif action == "logs":
            return await self._logs(compose_file)
        elif action == "down":
            return await self._down(compose_file)
        return f"Unknown action: {action}"

    async def _deploy(self, compose_file: str, env: str) -> str:
        logger.info("deploying", environment=env, compose_file=compose_file)
        code, out, err = await self._run(
            f"docker compose -f {compose_file} up --build -d"
        )
        if code != 0:
            return f"Deploy failed:\n{err}"
        return f"Deployed {env} successfully.\n{out}"

    async def _rollback(self, compose_file: str, env: str) -> str:
        logger.info("rolling_back", environment=env)
        # Stop current containers
        await self._run(f"docker compose -f {compose_file} down")
        # Restart with previous images (docker compose will use cached layers)
        code, out, err = await self._run(
            f"docker compose -f {compose_file} up -d"
        )
        if code != 0:
            return f"Rollback failed:\n{err}"
        return f"Rolled back {env}.\n{out}"

    async def _status(self, compose_file: str) -> str:
        code, out, err = await self._run(
            f"docker compose -f {compose_file} ps"
        )
        return out or err or "(no output)"

    async def _logs(self, compose_file: str) -> str:
        code, out, err = await self._run(
            f"docker compose -f {compose_file} logs --tail 50"
        )
        return (out or err)[:5000]

    async def _down(self, compose_file: str) -> str:
        code, out, err = await self._run(
            f"docker compose -f {compose_file} down"
        )
        return out or err or "Stopped."


class WaitForDeploymentTool:
    """Block the agent until the supervisor reaches a terminal state for this request.

    The agent (devops_specialist) calls this as its primary tool — it observes
    the supervisor's actual outcome instead of producing a fictional deployment
    report. Returns a structured dict (serialized to a string for the LLM) so
    the agent can read strategy / reasoning / step_history / error_message and
    write a truthful report.
    """

    # Terminal states from the supervisor's perspective — once current_step is
    # one of these, no further state transitions will happen (until a manual
    # unblock for "on_hold"). The agent should report what it sees and stop.
    TERMINAL_STATES = frozenset({"completed", "failed", "rolled_back", "on_hold"})

    DEFAULT_TIMEOUT_SECONDS = 600   # 10 minutes — enough for build + staging + prod
    POLL_INTERVAL_SECONDS = 5
    # If no deployment_states row appears within this many seconds, return
    # INDETERMINATE immediately — don't burn the full timeout. code_commit
    # runs synchronously before devops_specialist, so a missing row after
    # 30s means the workflow has no code_commit stage (e.g. demo_preparation)
    # and the row will NEVER appear. Without this, demo flows wait 10 minutes.
    NO_ROW_GRACE_SECONDS = 30

    def __init__(self, state: Any = None) -> None:
        # `state` is the StateStore singleton from app.state. Required for
        # this tool to function; if None, execute() returns an explanatory error.
        self.state = state

    def schema(self) -> dict[str, Any]:
        return {
            "name": "wait_for_deployment",
            "description": (
                "Wait until the standalone deployment supervisor finishes processing "
                "this request's commit. Polls every 5s up to a configurable timeout. "
                "Returns the deployment_states row including the judge's strategy + "
                "reasoning, the supervisor's step_history, and the final current_step. "
                "Use this as your ONLY tool — do not write a deployment report until "
                "you've called this and read the real outcome."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "description": "The REQ-XXX request ID whose deployment you're waiting on.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": (
                            "Maximum seconds to wait before giving up. Default 600 "
                            "(10 minutes). The tool returns INDETERMINATE state if "
                            "the timeout fires."
                        ),
                        "default": 600,
                    },
                },
                "required": ["request_id"],
            },
        }

    async def execute(self, params: dict) -> str:
        import json as _json  # local — avoid polluting module top

        if self.state is None:
            return (
                "ERROR: state store unavailable to this tool. The supervisor's deploy "
                "decision can't be observed. Report status=INDETERMINATE in your output."
            )

        request_id = (params.get("request_id") or "").strip()
        if not request_id:
            return "ERROR: request_id is required."
        timeout = int(params.get("timeout") or self.DEFAULT_TIMEOUT_SECONDS)

        loop = asyncio.get_event_loop()
        start = loop.time()
        deadline = start + max(timeout, 30)
        # Separate, shorter deadline that ONLY applies while no row exists.
        # Once a row appears, we switch to waiting up to the full `timeout`
        # for it to reach a terminal state.
        no_row_deadline = start + self.NO_ROW_GRACE_SECONDS
        last_step: str | None = None
        ever_saw_row = False

        while True:
            try:
                ds = await self.state.get_deployment_state_for_request(request_id)
            except Exception as e:
                # Don't let a transient DB hiccup kill the wait — log and keep polling.
                logger.warning(
                    "wait_for_deployment_query_failed",
                    request_id=request_id,
                    error=str(e),
                )
                ds = None

            if ds is None:
                # No deployment_states row yet. If we've waited the grace period
                # and STILL nothing, this workflow doesn't have a code_commit
                # stage — no row will ever appear. Bail out fast so the agent
                # can report NO_DEPLOY_TRIGGERED instead of timing out at 10min.
                if not ever_saw_row and loop.time() >= no_row_deadline:
                    return _json.dumps({
                        "status": "INDETERMINATE",
                        "reason": (
                            f"No deployment_states row appeared within "
                            f"{self.NO_ROW_GRACE_SECONDS}s. The workflow likely has "
                            f"no code_commit stage (e.g. demo_preparation, or a "
                            f"workflow that intentionally doesn't ship code)."
                        ),
                        "request_id": request_id,
                    })
                if loop.time() >= deadline:
                    return _json.dumps({
                        "status": "INDETERMINATE",
                        "reason": "No deployment_states row appeared within timeout. "
                                  "Was code_commit stage skipped or did it fail to publish?",
                        "request_id": request_id,
                    })
            else:
                ever_saw_row = True
                if ds.current_step != last_step:
                    logger.info(
                        "wait_for_deployment_progress",
                        request_id=request_id,
                        current_step=ds.current_step,
                    )
                    last_step = ds.current_step

                if ds.current_step in self.TERMINAL_STATES:
                    # Serialize the dataclass-like state to a JSON dict the LLM
                    # can parse easily.
                    return _json.dumps({
                        "status": "TERMINAL",
                        "deployment_id": ds.deployment_id,
                        "request_id": ds.request_id,
                        "current_step": ds.current_step,
                        "strategy": ds.strategy,
                        "strategy_reasoning": ds.strategy_reasoning,
                        "risk": ds.risk,
                        "commit_sha": ds.commit_sha,
                        "step_history": ds.step_history,
                        "error_message": ds.error_message,
                        "files_committed": ds.files_committed,
                        "rollback_sha": ds.rollback_sha,
                    }, default=str)

                if loop.time() >= deadline:
                    return _json.dumps({
                        "status": "TIMEOUT",
                        "deployment_id": ds.deployment_id,
                        "current_step": ds.current_step,
                        "step_history": ds.step_history,
                        "reason": f"Supervisor still working after {timeout}s; "
                                  f"current step is {ds.current_step}. Treat as INDETERMINATE.",
                    }, default=str)

            await asyncio.sleep(self.POLL_INTERVAL_SECONDS)
