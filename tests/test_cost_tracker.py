from nanobot.agent.cost_tracker import (
    MODEL_PRICING,
    SessionCostTracker,
    calculate_cost_usd,
    record_response,
)
from nanobot.providers.base import LLMResponse


def test_calculate_cost_usd_uses_token_and_cache_rates() -> None:
    cost = calculate_cost_usd(
        "anthropic/claude-3-5-sonnet",
        input_tokens=1_000_000,
        output_tokens=2_000_000,
        cache_read_tokens=500_000,
        cache_creation_tokens=250_000,
    )

    assert cost == 3.0 + 30.0 + 0.15 + 0.9375


def test_calculate_cost_usd_returns_zero_for_unknown_model() -> None:
    assert calculate_cost_usd("unknown/model", input_tokens=1_000) == 0.0


def test_session_cost_tracker_aggregates_per_model_usage() -> None:
    tracker = SessionCostTracker()

    first = LLMResponse(
        content="ok",
        usage={"input_tokens": 10, "output_tokens": 4},
        cache_read_tokens=3,
        cache_creation_tokens=2,
    )
    second = LLMResponse(
        content="ok",
        usage={"prompt_tokens": 20, "completion_tokens": 8},
        cache_read_tokens=1,
        cache_creation_tokens=0,
    )

    first_cost = tracker.record("anthropic/claude-3-5-sonnet", first)
    second_cost = record_response(tracker, "anthropic/claude-3-5-sonnet", second)
    snapshot = tracker.snapshot()

    assert first_cost > 0
    assert second_cost > 0
    assert snapshot.total_input_tokens == 30
    assert snapshot.total_output_tokens == 12
    assert snapshot.total_cache_read_tokens == 4
    assert snapshot.total_cache_creation_tokens == 2
    assert snapshot.total_cost_usd == first_cost + second_cost

    usage = snapshot.model_usage["anthropic/claude-3-5-sonnet"]
    assert usage.input_tokens == 30
    assert usage.output_tokens == 12
    assert usage.cache_read_tokens == 4
    assert usage.cache_creation_tokens == 2
    assert usage.cost_usd == snapshot.total_cost_usd


def test_session_cost_tracker_separates_models() -> None:
    tracker = SessionCostTracker()
    tracker.record(
        "anthropic/claude-3-5-sonnet",
        LLMResponse(content="a", usage={"input_tokens": 100, "output_tokens": 50}),
    )
    tracker.record(
        "openai/gpt-4o-mini",
        LLMResponse(content="b", usage={"input_tokens": 200, "output_tokens": 25}),
    )

    snapshot = tracker.snapshot()
    assert set(snapshot.model_usage) == {
        "anthropic/claude-3-5-sonnet",
        "openai/gpt-4o-mini",
    }
    assert snapshot.total_input_tokens == 300
    assert snapshot.total_output_tokens == 75


def test_model_pricing_table_contains_current_targets() -> None:
    assert "anthropic/claude-opus-4-5" in MODEL_PRICING
    assert "openai/gpt-4o-mini" in MODEL_PRICING

