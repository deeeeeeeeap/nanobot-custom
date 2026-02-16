import pytest

pytestmark = pytest.mark.skip(reason="Requires live bridge service and auth fixtures")


async def test_bridge_forwards_tools() -> None:
    """Bridge should preserve tool payloads when forwarding requests."""


async def test_bridge_handles_auth_expiry() -> None:
    """Bridge should recover from 401 by reloading auth/session state."""
