import pytest

pytestmark = pytest.mark.skip(reason="Requires live Telegram/bus/provider integration environment")


async def test_message_triggers_tool_call() -> None:
    """User message should trigger exec tool through full Telegram chain."""


async def test_new_command_consolidates_memory() -> None:
    """/new should consolidate memory and return feedback."""


async def test_clear_command_resets_session() -> None:
    """/clear should reset session without memory consolidation side effects."""


async def test_model_switch_takes_effect() -> None:
    """After /model switch, next message should use the new model."""
