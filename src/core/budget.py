"""Budget enforcer — checks spend against limits before each LLM call."""

import structlog
import yaml

from src.state.base import StateStore

logger = structlog.get_logger()


class BudgetExceededError(Exception):
    """Raised when budget limit is exceeded."""


class BudgetEnforcer:
    """Checks daily/monthly spend against configured limits."""

    def __init__(self, state: StateStore, config_path: str = "config/thresholds.yaml") -> None:
        self.state = state
        self._config = self._load_config(config_path)

    def _load_config(self, config_path: str) -> dict:
        try:
            with open(config_path) as f:
                config = yaml.safe_load(f)
            return config.get("cost", {}).get("budget", {})
        except FileNotFoundError:
            return {}

    @property
    def daily_limit(self) -> float | None:
        return self._config.get("daily_limit_usd")

    @property
    def monthly_limit(self) -> float | None:
        return self._config.get("monthly_limit_usd")

    @property
    def per_request_limit(self) -> float | None:
        return self._config.get("per_request_limit_usd")

    @property
    def alert_threshold(self) -> float:
        return self._config.get("alert_threshold_pct", 0.8)

    async def check_budget(self) -> dict:
        daily_cost = await self.state.get_daily_cost()
        monthly_cost = await self.state.get_monthly_cost()

        result = {
            "daily_cost": daily_cost,
            "monthly_cost": monthly_cost,
            "daily_limit": self.daily_limit,
            "monthly_limit": self.monthly_limit,
            "allowed": True,
            "warning": False,
        }

        # Check daily limit
        if self.daily_limit:
            if daily_cost >= self.daily_limit:
                result["allowed"] = False
                logger.critical("daily_budget_exceeded", cost=daily_cost, limit=self.daily_limit)
                raise BudgetExceededError(
                    f"Daily budget exceeded: ${daily_cost:.2f} >= ${self.daily_limit:.2f}"
                )
            if daily_cost >= self.daily_limit * self.alert_threshold:
                result["warning"] = True
                logger.warning(
                    "daily_budget_warning",
                    cost=daily_cost, limit=self.daily_limit,
                    pct=daily_cost / self.daily_limit,
                )

        # Check monthly limit
        if self.monthly_limit:
            if monthly_cost >= self.monthly_limit:
                result["allowed"] = False
                logger.critical("monthly_budget_exceeded", cost=monthly_cost, limit=self.monthly_limit)
                raise BudgetExceededError(
                    f"Monthly budget exceeded: ${monthly_cost:.2f} >= ${self.monthly_limit:.2f}"
                )
            if monthly_cost >= self.monthly_limit * self.alert_threshold:
                result["warning"] = True
                logger.warning(
                    "monthly_budget_warning",
                    cost=monthly_cost, limit=self.monthly_limit,
                    pct=monthly_cost / self.monthly_limit,
                )

        return result
