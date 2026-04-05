"""Smoke tests — validates environment health after deployment."""

import asyncio
from typing import Any

import structlog

logger = structlog.get_logger()


class SmokeTestRunner:
    """Runs smoke tests against a deployed environment."""

    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []

    async def run_all(self, base_url: str) -> dict[str, Any]:
        """Run all smoke tests against the given base URL."""
        self.results = []

        await self._test_health(base_url)
        await self._test_api_docs(base_url)
        await self._test_auth_endpoint(base_url)
        await self._test_frontend(base_url.replace(":8000", ":3000").replace(":8010", ":3010").replace(":8020", ":3020").replace(":8030", ":3030"))

        passed = sum(1 for r in self.results if r["passed"])
        failed = sum(1 for r in self.results if not r["passed"])

        return {
            "total": len(self.results),
            "passed": passed,
            "failed": failed,
            "all_passed": failed == 0,
            "results": self.results,
        }

    async def _test(self, name: str, url: str, expected_status: int = 200) -> None:
        try:
            proc = await asyncio.create_subprocess_shell(
                f'curl -sf -o /dev/null -w "%{{http_code}}" {url}',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            status_code = int(stdout.decode().strip())
            passed = status_code == expected_status
            self.results.append({
                "name": name,
                "url": url,
                "expected": expected_status,
                "actual": status_code,
                "passed": passed,
            })
            if passed:
                logger.info("smoke_test_passed", name=name)
            else:
                logger.warning("smoke_test_failed", name=name, expected=expected_status, actual=status_code)
        except Exception as e:
            self.results.append({
                "name": name,
                "url": url,
                "expected": expected_status,
                "actual": 0,
                "passed": False,
                "error": str(e),
            })

    async def _test_health(self, base_url: str) -> None:
        await self._test("Health Check", f"{base_url}/api/v1/health")

    async def _test_api_docs(self, base_url: str) -> None:
        await self._test("API Docs", f"{base_url}/api/v1/docs")

    async def _test_auth_endpoint(self, base_url: str) -> None:
        await self._test("Auth (unauthenticated)", f"{base_url}/api/v1/agents", expected_status=401)

    async def _test_frontend(self, frontend_url: str) -> None:
        await self._test("Frontend", frontend_url)
