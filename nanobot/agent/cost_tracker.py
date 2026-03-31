"""Session cost tracking for LLM usage."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nanobot.providers.base import LLMResponse


@dataclass(frozen=True)
class ModelPricing:
    """Per-million-token pricing in USD."""

    input_tokens: float
    output_tokens: float
    cache_read_tokens: float
    cache_creation_tokens: float


@dataclass
class ModelUsage:
    """Aggregated usage for a single model."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class CostSnapshot:
    """Immutable-style session summary for reporting and tests."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
    total_cost_usd: float = 0.0
    model_usage: dict[str, ModelUsage] = field(default_factory=dict)


# Internal pricing table for the current nanobot-custom models.
# Rates are USD per 1M tokens.
MODEL_PRICING: dict[str, ModelPricing] = {
    "anthropic/claude-3-5-sonnet": ModelPricing(3.0, 15.0, 0.30, 3.75),
    "anthropic/claude-3-7-sonnet": ModelPricing(3.0, 15.0, 0.30, 3.75),
    "anthropic/claude-sonnet-4": ModelPricing(3.0, 15.0, 0.30, 3.75),
    "anthropic/claude-sonnet-4-5": ModelPricing(3.0, 15.0, 0.30, 3.75),
    "anthropic/claude-opus-4-5": ModelPricing(5.0, 25.0, 0.50, 6.25),
    "anthropic/claude-opus-4-6": ModelPricing(5.0, 25.0, 0.50, 6.25),
    "openai/gpt-4o-mini": ModelPricing(0.15, 0.60, 0.00, 0.00),
    "gpt-5.3-codex": ModelPricing(1.25, 5.00, 0.00, 0.00),
}


def _normalize_model_name(model: str | None) -> str:
    return (model or "").strip().lower()


def _get_pricing(model: str | None) -> ModelPricing | None:
    normalized = _normalize_model_name(model)
    if normalized in MODEL_PRICING:
        return MODEL_PRICING[normalized]

    if normalized.startswith("anthropic/claude-"):
        return MODEL_PRICING["anthropic/claude-3-5-sonnet"]
    if normalized.startswith("openai/gpt-"):
        return MODEL_PRICING["openai/gpt-4o-mini"]
    if normalized.startswith("gpt-"):
        return MODEL_PRICING["gpt-5.3-codex"]
    return None


def _usage_from_response(response: LLMResponse) -> tuple[int, int, int, int]:
    usage = response.usage or {}
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    cache_read_tokens = int(getattr(response, "cache_read_tokens", 0) or 0)
    cache_creation_tokens = int(getattr(response, "cache_creation_tokens", 0) or 0)
    return input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens


def calculate_cost_usd(
    model: str | None,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Estimate USD cost for a single model turn."""
    pricing = _get_pricing(model)
    if pricing is None:
        return 0.0
    return (
        (input_tokens / 1_000_000) * pricing.input_tokens
        + (output_tokens / 1_000_000) * pricing.output_tokens
        + (cache_read_tokens / 1_000_000) * pricing.cache_read_tokens
        + (cache_creation_tokens / 1_000_000) * pricing.cache_creation_tokens
    )


class SessionCostTracker:
    """Aggregate per-model token usage and estimated cost for a session."""

    def __init__(self) -> None:
        self._snapshot = CostSnapshot()

    def record(self, model: str | None, response: LLMResponse) -> float:
        """Record one LLM response and return the estimated turn cost."""
        input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens = _usage_from_response(response)
        cost_usd = calculate_cost_usd(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
        )

        usage = self._snapshot.model_usage.get(model or "")
        if usage is None:
            usage = ModelUsage()
            self._snapshot.model_usage[model or ""] = usage

        usage.input_tokens += input_tokens
        usage.output_tokens += output_tokens
        usage.cache_read_tokens += cache_read_tokens
        usage.cache_creation_tokens += cache_creation_tokens
        usage.cost_usd += cost_usd

        self._snapshot.total_input_tokens += input_tokens
        self._snapshot.total_output_tokens += output_tokens
        self._snapshot.total_cache_read_tokens += cache_read_tokens
        self._snapshot.total_cache_creation_tokens += cache_creation_tokens
        self._snapshot.total_cost_usd += cost_usd
        return cost_usd

    def snapshot(self) -> CostSnapshot:
        """Return the current aggregated session totals."""
        return CostSnapshot(
            total_input_tokens=self._snapshot.total_input_tokens,
            total_output_tokens=self._snapshot.total_output_tokens,
            total_cache_read_tokens=self._snapshot.total_cache_read_tokens,
            total_cache_creation_tokens=self._snapshot.total_cache_creation_tokens,
            total_cost_usd=self._snapshot.total_cost_usd,
            model_usage={
                model: ModelUsage(
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                    cache_creation_tokens=usage.cache_creation_tokens,
                    cost_usd=usage.cost_usd,
                )
                for model, usage in self._snapshot.model_usage.items()
            },
        )

    def reset(self) -> None:
        self._snapshot = CostSnapshot()


def record_response(
    tracker: SessionCostTracker,
    model: str | None,
    response: LLMResponse,
) -> float:
    """Convenience wrapper for future loop integration."""
    return tracker.record(model, response)

