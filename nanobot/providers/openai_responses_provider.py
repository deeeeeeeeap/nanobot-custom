"""OpenAI Responses API provider for compatible endpoints."""

from __future__ import annotations

import json
from typing import Any

import httpx
from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.providers.codex_adapter import convert_messages_to_payload, parse_response_output


class OpenAIResponsesProvider(LLMProvider):
    """Call `/responses` on OpenAI or a compatible third-party relay."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "gpt-5",
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        timeout: float = 120.0,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self.extra_body = extra_body or {}
        self.timeout = timeout

    @staticmethod
    def _responses_url(api_base: str | None) -> str:
        base = (api_base or "https://api.openai.com/v1").strip().rstrip("/")
        if base.endswith("/responses"):
            return base
        return f"{base}/responses"

    @staticmethod
    def _usage(response: dict[str, Any]) -> dict[str, int]:
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return {}

        def get_int(*keys: str) -> int:
            for key in keys:
                value = usage.get(key)
                if isinstance(value, int):
                    return value
            return 0

        prompt = get_int("input_tokens", "prompt_tokens")
        completion = get_int("output_tokens", "completion_tokens")
        total = get_int("total_tokens")
        if not total:
            total = prompt + completion
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        }

    @staticmethod
    def _error_type(status_code: int | None, text: str) -> str:
        lowered = text.lower()
        if status_code == 402 or any(
            marker in lowered for marker in ("billing", "quota", "arrearage", "欠费", "余额不足")
        ):
            return "billing"
        if status_code == 429 or any(
            marker in lowered for marker in ("rate limit", "too many requests", "访问量过大", "请求过多")
        ):
            return "rate_limit"
        if status_code in {401, 403}:
            return "auth"
        if status_code in {408, 502, 503, 504} or "timeout" in lowered:
            return "timeout"
        if status_code == 404 and "model" in lowered:
            return "model_not_found"
        return "unknown"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        active_model = model or self.default_model
        payload, dropped = convert_messages_to_payload(
            messages=messages,
            model=active_model,
            tools=tools,
            tool_choice=tool_choice,
            stream=False,
        )
        if dropped:
            logger.warning("Dropped {} orphan Responses input item(s)", dropped)

        payload["max_output_tokens"] = max(1, int(max_tokens))
        payload["temperature"] = temperature
        if reasoning_effort and reasoning_effort.lower() != "none":
            payload["reasoning"] = {"effort": reasoning_effort.lower()}
            payload["include"] = ["reasoning.encrypted_content"]
        if self.extra_body:
            payload.update(self.extra_body)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self._responses_url(self.api_base),
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            return LLMResponse(
                content=f"Error calling Responses API: request timed out: {exc}",
                finish_reason="error",
                error_type="timeout",
            )
        except httpx.HTTPStatusError as exc:
            message = exc.response.text[:500]
            return LLMResponse(
                content=f"Error calling Responses API: HTTP {exc.response.status_code}: {message}",
                finish_reason="error",
                error_type=self._error_type(exc.response.status_code, message),
            )
        except httpx.HTTPError as exc:
            return LLMResponse(
                content=f"Error calling Responses API: {exc}",
                finish_reason="error",
                error_type=self._error_type(None, str(exc)),
            )

        try:
            data = response.json()
            output = data.get("output", [])
            if not isinstance(output, list):
                raise ValueError("Responses API returned non-list output")
            text, raw_tool_calls, reasoning = parse_response_output(output)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return LLMResponse(
                content=f"Error parsing Responses API response: {exc}",
                finish_reason="error",
                error_type="format",
            )

        tool_calls = [
            ToolCallRequest(
                id=str(item.get("id", "")),
                name=str(item.get("name", "")),
                arguments=self._parse_arguments(item.get("arguments", "{}")),
            )
            for item in raw_tool_calls
        ]
        return LLMResponse(
            content=text or None,
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else "stop",
            usage=self._usage(data),
            reasoning_content=reasoning or None,
        )

    @staticmethod
    def _parse_arguments(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str) or not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    def get_default_model(self) -> str:
        return self.default_model
