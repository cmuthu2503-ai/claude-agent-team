"""Token tracker — captures LLM usage and calculates cost."""

import uuid
from datetime import datetime
from typing import Any

import structlog
import yaml

from src.models.base import TokenUsage
from src.state.base import StateStore

logger = structlog.get_logger()


class TokenTracker:
    """Records token usage from Anthropic SDK responses and calculates cost."""

    def __init__(self, state: StateStore, config_path: str = "config/thresholds.yaml") -> None:
        self.state = state
        self._pricing = self._load_pricing(config_path)

    def _load_pricing(self, config_path: str) -> dict[str, dict[str, float]]:
        try:
            with open(config_path) as f:
                config = yaml.safe_load(f)
            return config.get("cost", {}).get("pricing", {})
        except FileNotFoundError:
            logger.warning("pricing_config_not_found", path=config_path)
            return {}

    def calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        model_pricing = self._pricing.get(model, {})
        input_price = model_pricing.get("input", 0.0)
        output_price = model_pricing.get("output", 0.0)
        return (input_tokens * input_price + output_tokens * output_price) / 1_000_000

    async def record(
        self, request_id: str, subtask_id: str, agent_id: str,
        model: str, input_tokens: int, output_tokens: int,
    ) -> TokenUsage:
        cost = self.calculate_cost(model, input_tokens, output_tokens)
        usage = TokenUsage(
            usage_id=str(uuid.uuid4()),
            request_id=request_id,
            subtask_id=subtask_id,
            agent_id=agent_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
        await self.state.record_token_usage(usage)
        logger.debug(
            "token_usage_recorded", agent=agent_id, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost,
        )
        return usage
