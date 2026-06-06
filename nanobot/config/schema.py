"""Configuration schema using Pydantic."""

from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _validate_url(value: str, field_name: str, schemes: set[str]) -> str:
    raw = value.strip()
    parsed = urlparse(raw)
    if parsed.scheme not in schemes or not parsed.netloc:
        expected = "/".join(sorted(schemes))
        raise ValueError(f"{field_name} must be a valid {expected} URL")
    return raw


class WhatsAppConfig(BaseModel):
    """WhatsApp channel configuration."""
    enabled: bool = False
    bridge_url: str = "ws://localhost:3001"
    allow_from: list[str] = Field(default_factory=list)  # Allowed phone numbers

    @field_validator("bridge_url")
    @classmethod
    def validate_bridge_url(cls, value: str) -> str:
        return _validate_url(value, "bridge_url", {"ws", "wss", "http", "https"})


class TelegramConfig(BaseModel):
    """Telegram channel configuration."""
    enabled: bool = False
    token: str = ""  # Bot token from @BotFather
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs or usernames
    proxy: str | None = None  # HTTP/SOCKS5 proxy URL, e.g. "http://127.0.0.1:7890" or "socks5://127.0.0.1:1080"
    reply_to_message: bool = False  # If true, outbound messages quote original incoming message

    @field_validator("token")
    @classmethod
    def normalize_token(cls, value: str) -> str:
        return value.strip()

    @field_validator("proxy")
    @classmethod
    def validate_proxy(cls, value: str | None) -> str | None:
        if value is None:
            return None
        raw = value.strip()
        if not raw:
            return None
        return _validate_url(raw, "proxy", {"http", "https", "socks5", "socks5h"})

    @model_validator(mode="after")
    def validate_enabled_requirements(self):
        if self.enabled and not self.token:
            raise ValueError("channels.telegram.token is required when telegram is enabled")
        return self


class FeishuConfig(BaseModel):
    """Feishu/Lark channel configuration using WebSocket long connection."""
    enabled: bool = False
    app_id: str = ""  # App ID from Feishu Open Platform
    app_secret: str = ""  # App Secret from Feishu Open Platform
    encrypt_key: str = ""  # Encrypt Key for event subscription (optional)
    verification_token: str = ""  # Verification Token for event subscription (optional)
    allow_from: list[str] = Field(default_factory=list)  # Allowed user open_ids


class DingTalkConfig(BaseModel):
    """DingTalk channel configuration using Stream mode."""
    enabled: bool = False
    client_id: str = ""  # AppKey
    client_secret: str = ""  # AppSecret
    allow_from: list[str] = Field(default_factory=list)  # Allowed staff_ids


class DiscordConfig(BaseModel):
    """Discord channel configuration."""
    enabled: bool = False
    token: str = ""  # Bot token from Discord Developer Portal
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs
    gateway_url: str = "wss://gateway.discord.gg/?v=10&encoding=json"
    intents: int = 37377  # GUILDS + GUILD_MESSAGES + DIRECT_MESSAGES + MESSAGE_CONTENT

    @field_validator("token")
    @classmethod
    def normalize_token(cls, value: str) -> str:
        return value.strip()

    @field_validator("gateway_url")
    @classmethod
    def validate_gateway_url(cls, value: str) -> str:
        return _validate_url(value, "gateway_url", {"ws", "wss"})

    @model_validator(mode="after")
    def validate_enabled_requirements(self):
        if self.enabled and not self.token:
            raise ValueError("channels.discord.token is required when discord is enabled")
        return self

class EmailConfig(BaseModel):
    """Email channel configuration (IMAP inbound + SMTP outbound)."""
    enabled: bool = False
    consent_granted: bool = False  # Explicit owner permission to access mailbox data

    # IMAP (receive)
    imap_host: str = ""
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_username: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    imap_use_ssl: bool = True

    # SMTP (send)
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    from_address: str = ""

    # Behavior
    auto_reply_enabled: bool = True  # If false, inbound email is read but no automatic reply is sent
    poll_interval_seconds: int = Field(default=30, ge=1, le=86400)
    mark_seen: bool = True
    max_body_chars: int = Field(default=12000, ge=256, le=200000)
    subject_prefix: str = "Re: "
    allow_from: list[str] = Field(default_factory=list)  # Allowed sender email addresses


class MochatMentionConfig(BaseModel):
    """Mochat mention behavior configuration."""
    require_in_groups: bool = False


class MochatGroupRule(BaseModel):
    """Mochat per-group mention requirement."""
    require_mention: bool = False


class MochatConfig(BaseModel):
    """Mochat channel configuration."""
    enabled: bool = False
    base_url: str = "https://mochat.io"
    socket_url: str = ""
    socket_path: str = "/socket.io"
    socket_disable_msgpack: bool = False
    socket_reconnect_delay_ms: int = 1000
    socket_max_reconnect_delay_ms: int = 10000
    socket_connect_timeout_ms: int = 10000
    refresh_interval_ms: int = 30000
    watch_timeout_ms: int = 25000
    watch_limit: int = 100
    retry_delay_ms: int = 500
    max_retry_attempts: int = 0  # 0 means unlimited retries
    claw_token: str = ""
    agent_user_id: str = ""
    sessions: list[str] = Field(default_factory=list)
    panels: list[str] = Field(default_factory=list)
    allow_from: list[str] = Field(default_factory=list)
    mention: MochatMentionConfig = Field(default_factory=MochatMentionConfig)
    groups: dict[str, MochatGroupRule] = Field(default_factory=dict)
    reply_delay_mode: str = "non-mention"  # off | non-mention
    reply_delay_ms: int = 120000

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _validate_url(value, "base_url", {"http", "https"})

    @field_validator("socket_url")
    @classmethod
    def validate_socket_url(cls, value: str) -> str:
        raw = value.strip()
        if not raw:
            return ""
        return _validate_url(raw, "socket_url", {"ws", "wss"})


class SlackDMConfig(BaseModel):
    """Slack DM policy configuration."""
    enabled: bool = True
    policy: str = "open"  # "open" or "allowlist"
    allow_from: list[str] = Field(default_factory=list)  # Allowed Slack user IDs


class SlackConfig(BaseModel):
    """Slack channel configuration."""
    enabled: bool = False
    mode: str = "socket"  # "socket" supported
    webhook_path: str = "/slack/events"
    bot_token: str = ""  # xoxb-...
    app_token: str = ""  # xapp-...
    user_token_read_only: bool = True
    group_policy: str = "mention"  # "mention", "open", "allowlist"
    group_allow_from: list[str] = Field(default_factory=list)  # Allowed channel IDs if allowlist
    dm: SlackDMConfig = Field(default_factory=SlackDMConfig)


class QQConfig(BaseModel):
    """QQ channel configuration using botpy SDK."""
    enabled: bool = False
    app_id: str = ""  # 鏈哄櫒浜?ID (AppID) from q.qq.com
    secret: str = ""  # 鏈哄櫒浜哄瘑閽?(AppSecret) from q.qq.com
    allow_from: list[str] = Field(default_factory=list)  # Allowed user openids (empty = public access)


class ChannelsConfig(BaseModel):
    """Configuration for chat channels."""
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    mochat: MochatConfig = Field(default_factory=MochatConfig)
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    qq: QQConfig = Field(default_factory=QQConfig)


class AgentDefaults(BaseModel):
    """Default agent configuration."""
    workspace: str = "~/.nanobot/workspace"
    model: str = "anthropic/claude-opus-4-5"
    reasoning_effort: str = "medium"
    max_tokens: int = Field(default=16384, ge=1, le=262144)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tool_iterations: int = Field(default=50, ge=1, le=200)
    idle_intervention: bool = True
    loop_detection_enabled: bool = True
    loop_window: int = Field(default=30, ge=6, le=200)
    loop_warn_threshold: int = Field(default=12, ge=2, le=200)
    loop_critical_threshold: int = Field(default=18, ge=2, le=200)
    loop_break_threshold: int = Field(default=25, ge=3, le=200)
    max_exempt_rounds: int = Field(default=4, ge=1, le=20)
    max_message_calls_per_turn: int = Field(default=5, ge=1, le=20)
    model_fallbacks: list[str] = Field(default_factory=list)
    failover_retry_once: bool = True
    context_guard_min_tokens: int = Field(default=16000, ge=1024, le=1_000_000)
    context_guard_warn_tokens: int = Field(default=32000, ge=1024, le=1_000_000)
    tool_result_max_chars: int = Field(default=12000, ge=1000, le=200_000)
    compaction_enabled: bool = True
    compaction_target_ratio: float = Field(default=0.45, ge=0.1, le=0.9)

    @field_validator("workspace")
    @classmethod
    def validate_workspace(cls, value: str) -> str:
        raw = value.strip()
        if not raw:
            raise ValueError("workspace cannot be empty")
        return raw

    @field_validator("model_fallbacks")
    @classmethod
    def normalize_fallback_models(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for model in value:
            candidate = model.strip()
            if not candidate:
                continue
            key = candidate.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(candidate)
        return cleaned

    @field_validator("reasoning_effort")
    @classmethod
    def validate_reasoning_effort(cls, value: str) -> str:
        effort = value.strip().lower()
        allowed = {"none", "low", "medium", "high"}
        if effort not in allowed:
            raise ValueError(f"reasoning_effort must be one of: {', '.join(sorted(allowed))}")
        return effort

    @model_validator(mode="after")
    def validate_loop_thresholds(self):
        if self.loop_warn_threshold > self.loop_critical_threshold:
            raise ValueError("loop_warn_threshold must be <= loop_critical_threshold")
        if self.loop_critical_threshold > self.loop_break_threshold:
            raise ValueError("loop_critical_threshold must be <= loop_break_threshold")
        if self.loop_break_threshold > self.loop_window:
            raise ValueError("loop_break_threshold must be <= loop_window")
        if self.max_exempt_rounds > self.max_tool_iterations:
            raise ValueError("max_exempt_rounds must be <= max_tool_iterations")
        if self.max_message_calls_per_turn > self.max_tool_iterations:
            raise ValueError("max_message_calls_per_turn must be <= max_tool_iterations")
        return self


class AgentsConfig(BaseModel):
    """Agent configuration."""
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ProviderConfig(BaseModel):
    """LLM provider configuration."""
    api_key: str = ""
    api_base: str | None = None
    api_type: Literal["auto", "chat_completions", "responses"] = "auto"
    extra_headers: dict[str, str] | None = None  # Custom headers (e.g. APP-Code for AiHubMix)
    extra_body: dict[str, Any] | None = None  # Extra request body fields for compatible gateways.

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: str) -> str:
        return value.strip()

    @field_validator("api_base")
    @classmethod
    def validate_api_base(cls, value: str | None) -> str | None:
        if value is None:
            return None
        raw = value.strip()
        if not raw:
            return None
        return _validate_url(raw, "api_base", {"http", "https"})


class CodexProviderConfig(BaseModel):
    """Native Codex provider configuration."""

    enabled: bool = False
    codex_home: str = "~/.codex"
    model: str = "gpt-5.3-codex"
    timeout: int = Field(default=300, ge=30, le=600)
    server_compaction_enabled: bool = False
    compact_threshold: int = Field(default=80000, ge=1000, le=500000)

    @field_validator("codex_home")
    @classmethod
    def normalize_codex_home(cls, value: str) -> str:
        raw = value.strip()
        if not raw:
            raise ValueError("codex_home cannot be empty")
        return raw

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        raw = value.strip()
        if not raw:
            raise ValueError("model cannot be empty")
        return raw


class ProvidersConfig(BaseModel):
    """Configuration for LLM providers."""
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)  # 闃块噷浜戦€氫箟鍗冮棶
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)  # MiniMax
    aihubmix: ProviderConfig = Field(default_factory=ProviderConfig)  # AiHubMix API gateway
    antigravity: ProviderConfig = Field(default_factory=ProviderConfig)  # Antigravity gateway (multi-account routing)
    codex: CodexProviderConfig = Field(default_factory=CodexProviderConfig)

class GatewayConfig(BaseModel):
    """Gateway/server configuration."""
    host: str = "0.0.0.0"
    port: int = Field(default=18790, ge=1, le=65535)


class WebSearchConfig(BaseModel):
    """Web search tool configuration."""
    api_key: str = ""  # Brave Search API key
    max_results: int = Field(default=5, ge=1, le=20)


class WebToolsConfig(BaseModel):
    """Web tools configuration."""
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


class ExecToolConfig(BaseModel):
    """Shell exec tool configuration."""
    timeout: int = Field(default=120, ge=1, le=600)


class ResultStorageConfig(BaseModel):
    """Configuration for spilling large tool results to workspace files."""

    enabled: bool = True
    threshold_chars: int = Field(default=8000, ge=1000, le=1_000_000)
    turn_budget_chars: int = Field(default=60000, ge=5000, le=2_000_000)
    path: str = "tool-results"
    preview_chars: int = Field(default=3000, ge=500, le=100_000)
    max_files: int = Field(default=500, ge=1, le=100_000)
    max_bytes: int = Field(default=256 * 1024 * 1024, ge=1024 * 1024, le=10 * 1024**3)
    max_age_days: int = Field(default=30, ge=1, le=3650)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        raw = value.strip().replace("\\", "/")
        if not raw:
            return "tool-results"
        candidate = Path(raw)
        if raw.startswith("/") or candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("tools.result_storage.path must be workspace-relative")
        return raw.strip("/")


class ToolsConfig(BaseModel):
    """Tools configuration."""
    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    result_storage: ResultStorageConfig = Field(default_factory=ResultStorageConfig)
    restrict_to_workspace: bool = False  # If true, restrict all tool access to workspace directory


class SearchConfig(BaseModel):
    """Built-in local search configuration."""

    enabled: bool = True
    db_path: str = ""  # Empty means workspace/search/index.sqlite
    default_limit: int = Field(default=5, ge=1, le=50)
    min_score: float = Field(default=0.1, ge=0.0, le=1.0)
    auto_index: bool = True
    index_dirs: list[str] = Field(default_factory=lambda: ["memory"])
    vector_enabled: bool = False
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_batch_size: int = Field(default=32, ge=1, le=512)
    embedding_chunk_size: int = Field(default=900, ge=100, le=4000)
    embedding_chunk_overlap: float = Field(default=0.15, ge=0.0, le=0.5)


class MemoryConfig(BaseModel):
    """Structured memory extraction/compression configuration."""

    enabled: bool = True
    auto_compress: bool = True
    compress_threshold: int = Field(default=10, ge=1, le=500)
    max_memories_per_category: int = Field(default=50, ge=1, le=5000)
    max_message_chars: int = Field(default=4000, ge=512, le=50000)
    output_language: str = "zh-CN"
    dedup_min_score: float = Field(default=0.15, ge=0.0, le=1.0)


class LoggingConfig(BaseModel):
    """Runtime logging behavior."""

    level: str = "INFO"
    max_file_bytes: int = Field(default=500 * 1024 * 1024, ge=1 * 1024 * 1024, le=2 * 1024**3)
    max_files: int = Field(default=5, ge=1, le=50)

    @field_validator("level")
    @classmethod
    def normalize_level(cls, value: str) -> str:
        level = value.strip().upper()
        allowed = {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}
        if level not in allowed:
            raise ValueError(f"logging.level must be one of: {', '.join(sorted(allowed))}")
        return level


class Config(BaseSettings):
    """Root configuration for nanobot."""
    model_config = SettingsConfigDict(
        env_prefix="NANOBOT_",
        env_nested_delimiter="__",
    )

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    
    @property
    def workspace_path(self) -> Path:
        """Get expanded workspace path."""
        return Path(self.agents.defaults.workspace).expanduser()
    
    def _match_provider(self, model: str | None = None) -> tuple["ProviderConfig | None", str | None]:
        """Match provider config and its registry name. Returns (config, spec_name)."""
        from nanobot.providers.registry import PROVIDERS
        model_lower = (model or self.agents.defaults.model).lower()

        # Match by keyword (order follows PROVIDERS registry)
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and any(kw in model_lower for kw in spec.keywords) and p.api_key:
                return p, spec.name

        # Fallback: gateways first, then others (follows registry order)
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and p.api_key:
                return p, spec.name
        return None, None

    def get_provider(self, model: str | None = None) -> ProviderConfig | None:
        """Get matched provider config (api_key, api_base, extra_headers). Falls back to first available."""
        p, _ = self._match_provider(model)
        return p

    def get_provider_name(self, model: str | None = None) -> str | None:
        """Get the registry name of the matched provider (e.g. "deepseek", "openrouter")."""
        _, name = self._match_provider(model)
        return name

    def get_api_key(self, model: str | None = None) -> str | None:
        """Get API key for the given model. Falls back to first available key."""
        p = self.get_provider(model)
        return p.api_key if p else None
    
    def get_api_base(self, model: str | None = None) -> str | None:
        """Get API base URL for the given model. Applies default URLs for known gateways."""
        from nanobot.providers.registry import find_by_name
        p, name = self._match_provider(model)
        if p and p.api_base:
            return p.api_base
        # 所有有 default_api_base 的 provider 都应返回，
        # 否则 litellm 可能无法正确路由（如 MiniMax）。
        if name:
            spec = find_by_name(name)
            if spec and spec.default_api_base:
                return spec.default_api_base
        return None
