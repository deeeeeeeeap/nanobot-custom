"""LiteLLM provider implementation for multi-provider support."""

import json
import os
from typing import Any

import litellm
from litellm import acompletion
from loguru import logger

from nanobot.exceptions import ProviderError
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.providers.registry import find_by_model, find_gateway

_ALLOWED_MSG_KEYS = frozenset(
    {"role", "content", "tool_calls", "tool_call_id", "name", "reasoning_content"}
)


class LiteLLMProvider(LLMProvider):
    """
    LLM provider using LiteLLM for multi-provider support.

    Supports OpenRouter, Anthropic, OpenAI, Gemini, and other providers
    through a single API.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "anthropic/claude-opus-4-5",
        extra_headers: dict[str, str] | None = None,
        provider_name: str | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self._gateway = find_gateway(provider_name, api_key, api_base)

        if api_key:
            self._setup_env(api_key, api_base, default_model)
        if api_base:
            litellm.api_base = api_base

        litellm.suppress_debug_info = True
        litellm.drop_params = True

    def _setup_env(self, api_key: str, api_base: str | None, model: str) -> None:
        """Set environment variables based on detected provider."""
        spec = self._gateway or find_by_model(model)
        if not spec:
            return

        if self._gateway:
            os.environ[spec.env_key] = api_key
        else:
            os.environ.setdefault(spec.env_key, api_key)

        effective_base = api_base or spec.default_api_base
        for env_name, env_val in spec.env_extras:
            resolved = env_val.replace("{api_key}", api_key).replace("{api_base}", effective_base)
            os.environ.setdefault(env_name, resolved)

    def _resolve_model(self, model: str) -> str:
        """Resolve model name by applying provider/gateway prefixes."""
        if self._gateway:
            prefix = self._gateway.litellm_prefix
            if self._gateway.strip_model_prefix:
                model = model.split("/")[-1]
            if prefix and not model.startswith(f"{prefix}/"):
                model = f"{prefix}/{model}"
            return model

        spec = find_by_model(model)
        if spec and spec.litellm_prefix and not any(model.startswith(s) for s in spec.skip_prefixes):
            model = f"{spec.litellm_prefix}/{model}"
        return model

    def _supports_cache_control(self, model: str) -> bool:
        """Return True when provider/model supports cache_control on content blocks."""
        if self._gateway is not None:
            flag = getattr(self._gateway, "supports_prompt_caching", None)
            if flag is not None:
                return bool(flag)

        spec = find_by_model(model)
        flag = getattr(spec, "supports_prompt_caching", None) if spec else None
        if flag is not None:
            return bool(flag)

        model_lower = model.lower()
        return "anthropic" in model_lower or "claude" in model_lower

    def _apply_cache_control(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
        """Return copies of messages/tools with cache_control injected where applicable."""
        new_messages: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") == "system":
                content = msg.get("content")
                if isinstance(content, str):
                    new_content: Any = [
                        {
                            "type": "text",
                            "text": content,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]
                elif isinstance(content, list) and content:
                    new_content = list(content)
                    last = new_content[-1]
                    if isinstance(last, dict):
                        new_content[-1] = {**last, "cache_control": {"type": "ephemeral"}}
                else:
                    new_content = content
                new_messages.append({**msg, "content": new_content})
            else:
                new_messages.append(msg)

        new_tools = tools
        if tools:
            new_tools = list(tools)
            new_tools[-1] = {**new_tools[-1], "cache_control": {"type": "ephemeral"}}
        return new_messages, new_tools

    def _apply_model_overrides(self, model: str, kwargs: dict[str, Any]) -> None:
        """Apply model-specific parameter overrides from provider registry."""
        spec = find_by_model(model)
        if not spec:
            return
        model_lower = model.lower()
        for pattern, overrides in spec.model_overrides:
            if pattern in model_lower:
                kwargs.update(overrides)
                return

    @staticmethod
    def _sanitize_empty_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter empty text blocks that can trigger strict provider 400 errors."""
        sanitized: list[dict[str, Any]] = []
        for msg in messages:
            clean = dict(msg)
            content = clean.get("content")
            if isinstance(content, list):
                blocks: list[Any] = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = str(block.get("text", ""))
                        if not text.strip():
                            continue
                    blocks.append(block)
                clean["content"] = blocks or None
            sanitized.append(clean)
        return sanitized

    @staticmethod
    def _sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Strip non-standard top-level message keys before sending to providers."""
        sanitized: list[dict[str, Any]] = []
        for msg in messages:
            clean = {k: v for k, v in msg.items() if k in _ALLOWED_MSG_KEYS}
            if clean.get("role") == "assistant" and "content" not in clean:
                clean["content"] = None
            sanitized.append(clean)
        return sanitized

    @staticmethod
    def _classify_error(status_code: int | None, message: str, code: str | None = None) -> str:
        """Classify provider/network failures into a stable error category."""
        text = f"{message} {code or ''}".lower()
        if status_code == 402 or "billing" in text or "quota" in text:
            return "billing"
        if status_code == 429 or "rate limit" in text or "too many requests" in text:
            return "rate_limit"
        if status_code in {401, 403} or "unauthorized" in text or "forbidden" in text:
            return "auth"
        if status_code in {502, 503, 504}:
            return "timeout"
        if status_code == 404 and "model" in text:
            return "model_not_found"
        if any(k in text for k in ("etimedout", "timeout", "timed out", "econnreset", "econnaborted")):
            return "timeout"
        if any(k in text for k in ("model not found", "unknown model", "does not exist")):
            return "model_not_found"
        if any(
            k in text
            for k in (
                "invalid tool",
                "tool_choice",
                "json schema",
                "invalid json",
                "malformed json",
                "tool format",
            )
        ):
            return "format"
        return "unknown"

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """
        Send a chat completion request via LiteLLM.

        Returns an LLMResponse with either assistant content, tool calls,
        or an error payload that upper layers can handle gracefully.
        """
        original_model = model or self.default_model
        model = self._resolve_model(original_model)
        if self._supports_cache_control(original_model):
            messages, tools = self._apply_cache_control(messages, tools)

        max_tokens = max(1, max_tokens)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": self._sanitize_messages(self._sanitize_empty_content(messages)),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        self._apply_model_overrides(model, kwargs)

        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        try:
            logger.debug(
                "LLM request: model={}, tools={}, tool_choice={}, api_base={}",
                model,
                len(tools) if tools else 0,
                kwargs.get("tool_choice", "n/a"),
                kwargs.get("api_base", "default"),
            )
            response = await acompletion(**kwargs)
            choice = response.choices[0] if response.choices else None
            if choice:
                has_tc = bool(getattr(choice.message, "tool_calls", None))
                logger.debug(
                    "LLM response: finish_reason={}, has_tool_calls={}, content_len={}",
                    choice.finish_reason,
                    has_tc,
                    len(choice.message.content or ""),
                )
            return self._parse_response(response)
        except (TypeError, ValueError, OSError, TimeoutError) as e:
            err = ProviderError(f"LLM request failed: {e}")
            error_type = self._classify_error(
                getattr(e, "status_code", None),
                str(e),
                getattr(e, "code", None),
            )
            logger.error(str(err))
            return LLMResponse(
                content=f"Error calling LLM: {err}",
                finish_reason="error",
                error_type=error_type,
            )
        except RuntimeError as e:
            err = ProviderError(f"LLM runtime failure: {e}")
            error_type = self._classify_error(
                getattr(e, "status_code", None),
                str(e),
                getattr(e, "code", None),
            )
            logger.error(str(err))
            return LLMResponse(
                content=f"Error calling LLM: {err}",
                finish_reason="error",
                error_type=error_type,
            )
        except Exception as e:
            err = ProviderError(f"Unexpected LLM failure: {e}")
            error_type = self._classify_error(
                getattr(e, "status_code", None),
                str(e),
                getattr(e, "code", None),
            )
            logger.exception(str(err))
            return LLMResponse(
                content=f"Error calling LLM: {err}",
                finish_reason="error",
                error_type=error_type,
            )

    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse LiteLLM response into project-standard format."""
        choice = response.choices[0]
        message = choice.message

        tool_calls = []
        for tc in getattr(message, "tool_calls", []) or []:
            args = tc.function.arguments
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"raw": args}
            elif not isinstance(args, dict):
                args = {"raw": str(args)}
            tool_calls.append(
                ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                )
            )

        usage = {}
        if getattr(response, "usage", None):
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
            reasoning_content=getattr(message, "reasoning_content", None) or None,
        )

    def get_default_model(self) -> str:
        """Get the default model."""
        return self.default_model
