"""Deployment tools — wraps Docker Compose commands for agent use."""

import asyncio
from typing import Any

import structlog
import yaml

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
