"""Structured exception hierarchy for nanobot."""


class NanobotError(Exception):
    """Base exception for all nanobot-specific errors."""


class ConfigError(NanobotError):
    """Configuration load/validation errors."""


class ProviderError(NanobotError):
    """LLM provider request/response errors."""


class ChannelError(NanobotError):
    """Channel message transport/parsing errors."""


class ToolError(NanobotError):
    """Tool validation or execution errors."""


class SessionError(NanobotError):
    """Session persistence/state errors."""


class AuthenticationError(NanobotError):
    """Authentication/authorization errors."""
