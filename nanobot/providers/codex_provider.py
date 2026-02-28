"""Native Codex Responses API provider (no external bridge)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.providers.codex_adapter import convert_messages_to_payload, parse_response_output
from nanobot.providers.codex_auth import CodexAuth

_DEFAULT_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"


@dataclass
class _ParsedSSE:
    content: str | None
    tool_calls: list[ToolCallRequest]
    reasoning_content: str | None


class CodexProvider(LLMProvider):
    """LLM provider that talks directly to Codex Responses API."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "gpt-5.3-codex",
        codex_home: str | None = None,
        timeout: int = 300,
        server_compaction_enabled: bool = False,
        compact_threshold: int = 80000,
        auth: CodexAuth | None = None,
        responses_url: str | None = None,
    ) -> None:
        super().__init__(api_key=api_key, api_base=api_base)
        self.default_model = default_model
        self.timeout = float(max(10, timeout))
        self.server_compaction_enabled = bool(server_compaction_enabled)
        self.compact_threshold = max(1000, int(compact_threshold))
        self.responses_url = self._resolve_responses_url(api_base=api_base, responses_url=responses_url)
        self.auth = auth or CodexAuth(codex_home=codex_home)

    @staticmethod
    def _resolve_responses_url(api_base: str | None, responses_url: str | None) -> str:
        if responses_url:
            return responses_url
        if api_base:
            base = api_base.rstrip("/")
            if base.endswith("/responses"):
                return base
            return f"{base}/responses"
        return _DEFAULT_RESPONSES_URL

    @staticmethod
    def _normalize_model(model: str) -> str:
        candidate = model.strip()
        if "/" in candidate:
            return candidate.split("/")[-1]
        return candidate

    @staticmethod
    def _classify_error(status_code: int | None, message: str) -> str:
        text = message.lower()
        if status_code in {401, 403} or "unauthorized" in text or "forbidden" in text:
            return "auth_expired"
        if status_code in {429} or "rate limit" in text:
            return "rate_limit"
        if status_code in {502, 503, 504} or "timeout" in text or "timed out" in text:
            return "timeout"
        if status_code == 404 and "model" in text:
            return "model_not_found"
        if "tool call" in text and "call_id" in text:
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
        del max_tokens  # Codex backend currently rejects max_output_tokens.
        del temperature  # Codex backend currently rejects temperature.

        target_model = self._normalize_model(model or self.default_model)
        payload, dropped = convert_messages_to_payload(
            messages=messages,
            model=target_model,
            tools=tools,
            tool_choice=tool_choice,
            enable_server_compaction=self.server_compaction_enabled,
            compact_threshold=self.compact_threshold,
        )
        if dropped:
            logger.warning("Removed {} orphan function_call_output items before Codex request", dropped)

        try:
            await self.auth.ensure_valid()
            headers = self.auth.get_headers()
            status_code, result = await self._send_request(payload, headers)
            if status_code == 401:
                await self.auth.ensure_valid(force=True)
                headers = self.auth.get_headers()
                status_code, result = await self._send_request(payload, headers)

            if status_code >= 400:
                body = str(result)
                error_type = self._classify_error(status_code, body)
                return LLMResponse(
                    content=f"Error calling LLM: ChatGPT API error ({status_code}): {body}",
                    finish_reason="error",
                    error_type=error_type,
                )

            parsed = result if isinstance(result, _ParsedSSE) else _ParsedSSE(content=None, tool_calls=[], reasoning_content=None)
            finish_reason = "tool_calls" if parsed.tool_calls else "stop"
            return LLMResponse(
                content=parsed.content,
                tool_calls=parsed.tool_calls,
                finish_reason=finish_reason,
                reasoning_content=parsed.reasoning_content,
            )
        except RuntimeError as e:
            msg = f"Error calling LLM: {e}"
            return LLMResponse(
                content=msg,
                finish_reason="error",
                error_type=self._classify_error(None, str(e)),
            )
        except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError) as e:
            msg = f"Error calling LLM: {e}"
            return LLMResponse(
                content=msg,
                finish_reason="error",
                error_type=self._classify_error(None, str(e)),
            )
        except Exception as e:
            logger.exception("Unexpected Codex provider failure")
            msg = f"Error calling LLM: Unexpected LLM failure: {e}"
            return LLMResponse(
                content=msg,
                finish_reason="error",
                error_type="unknown",
            )

    async def _send_request(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> tuple[int, _ParsedSSE | str]:
        """发送请求并流式解析 SSE，避免全量读入内存。"""
        timeout = httpx.Timeout(self.timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                self.responses_url,
                json=payload,
                headers=headers,
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    return response.status_code, body[:4000]
                # 流式逐行解析 SSE，避免全量缓存
                parsed = self._create_sse_state()
                async for raw_line in response.aiter_lines():
                    if raw_line:
                        self._process_sse_line(raw_line, parsed)
                self._finalize_pending_calls(parsed)
                result = _ParsedSSE(
                    content="".join(parsed["text_chunks"]).strip() or None,
                    tool_calls=parsed["tool_calls"],
                    reasoning_content="".join(parsed["reasoning_chunks"]).strip() or None,
                )
                return response.status_code, result

    @staticmethod
    def _create_sse_state() -> dict:
        """创建 SSE 解析的初始状态。"""
        return {
            "text_chunks": [],
            "reasoning_chunks": [],
            "pending_calls": {},
            "tool_calls": [],
            "seen_call_ids": set(),
        }

    def _process_sse_line(self, line: str, state: dict) -> None:
        """处理单行 SSE 数据，更新 state。"""
        if not line.startswith("data: "):
            return
        payload = line[6:]
        if payload == "[DONE]":
            return
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return

        event_type = str(event.get("type", ""))
        if event_type == "response.output_text.delta":
            delta = str(event.get("delta", ""))
            if delta:
                state["text_chunks"].append(delta)
            return
        if event_type == "response.reasoning_summary_text.delta":
            delta = str(event.get("delta", ""))
            if delta:
                state["reasoning_chunks"].append(delta)
            return
        if event_type == "response.output_item.added":
            item = event.get("item", {}) if isinstance(event.get("item"), dict) else {}
            if item.get("type") == "function_call":
                item_id = str(item.get("id", "")).strip() or f"item_{uuid.uuid4().hex[:8]}"
                state["pending_calls"][item_id] = {
                    "call_id": str(item.get("call_id") or f"call_{uuid.uuid4().hex[:8]}"),
                    "name": str(item.get("name", "")),
                    "arguments": str(item.get("arguments", "")),
                }
            return
        if event_type == "response.function_call_arguments.delta":
            item_id = str(event.get("item_id", "")).strip()
            delta = str(event.get("delta", ""))
            if item_id in state["pending_calls"] and delta:
                state["pending_calls"][item_id]["arguments"] += delta
            return
        if event_type == "response.output_item.done":
            item = event.get("item", {}) if isinstance(event.get("item"), dict) else {}
            self._merge_output_item(
                item=item,
                pending_calls=state["pending_calls"],
                tool_calls=state["tool_calls"],
                seen_call_ids=state["seen_call_ids"],
                text_chunks=state["text_chunks"],
                reasoning_chunks=state["reasoning_chunks"],
            )
            return
        if event_type in {"response.completed", "response.done"}:
            response_blob = (
                event.get("response", {}) if isinstance(event.get("response"), dict) else {}
            )
            output_items = (
                response_blob.get("output", [])
                if isinstance(response_blob.get("output"), list)
                else []
            )
            text, completed_calls, reasoning = parse_response_output(output_items)
            if text:
                state["text_chunks"][:] = [text]
            if reasoning:
                state["reasoning_chunks"].append(reasoning)
            for call in completed_calls:
                call_id = str(call.get("id", "")).strip()
                if not call_id or call_id in state["seen_call_ids"]:
                    continue
                state["seen_call_ids"].add(call_id)
                state["tool_calls"].append(
                    ToolCallRequest(
                        id=call_id,
                        name=str(call.get("name", "")),
                        arguments=self._parse_arguments(str(call.get("arguments", "{}"))),
                    )
                )
            return
        if event_type == "response.failed":
            err = event.get("error") if isinstance(event.get("error"), dict) else {}
            message = err.get("message") or event.get("message") or "response.failed"
            raise RuntimeError(str(message))

    def _finalize_pending_calls(self, state: dict) -> None:
        """将未完成的 pending_calls 转为 tool_calls。"""
        for pending in state["pending_calls"].values():
            call_id = pending["call_id"]
            if call_id in state["seen_call_ids"]:
                continue
            state["seen_call_ids"].add(call_id)
            state["tool_calls"].append(
                ToolCallRequest(
                    id=call_id,
                    name=pending["name"],
                    arguments=self._parse_arguments(pending["arguments"] or "{}"),
                )
            )

    def _parse_sse(self, lines: list[str]) -> _ParsedSSE:
        """兼容旧接口：批量解析 SSE 行（用于测试）。"""
        state = self._create_sse_state()
        for line in lines:
            self._process_sse_line(line, state)
        self._finalize_pending_calls(state)
        return _ParsedSSE(
            content="".join(state["text_chunks"]).strip() or None,
            tool_calls=state["tool_calls"],
            reasoning_content="".join(state["reasoning_chunks"]).strip() or None,
        )

    def _merge_output_item(
        self,
        *,
        item: dict[str, Any],
        pending_calls: dict[str, dict[str, str]],
        tool_calls: list[ToolCallRequest],
        seen_call_ids: set[str],
        text_chunks: list[str],
        reasoning_chunks: list[str],
    ) -> None:
        item_type = item.get("type")
        if item_type == "function_call":
            item_id = str(item.get("id", "")).strip()
            pending = pending_calls.pop(item_id, None)
            call_id = str(item.get("call_id", "")).strip()
            if not call_id and pending:
                call_id = pending.get("call_id", "")
            if not call_id:
                call_id = f"call_{uuid.uuid4().hex[:8]}"
            if call_id in seen_call_ids:
                return
            seen_call_ids.add(call_id)
            name = str(item.get("name", "")).strip()
            if not name and pending:
                name = pending.get("name", "")
            arguments = item.get("arguments")
            if arguments is None and pending:
                arguments = pending.get("arguments", "{}")
            if isinstance(arguments, dict):
                parsed_args = arguments
            else:
                parsed_args = self._parse_arguments(str(arguments or "{}"))
            tool_calls.append(
                ToolCallRequest(id=call_id, name=name, arguments=parsed_args)
            )
            return

        if item_type != "message":
            return
        content_items = item.get("content", []) if isinstance(item.get("content"), list) else []
        for content_item in content_items:
            if not isinstance(content_item, dict):
                continue
            if content_item.get("type") == "output_text" and content_item.get("text"):
                text_chunks[:] = [str(content_item.get("text", ""))]
            if (
                content_item.get("type") == "reasoning_summary_text"
                and content_item.get("text")
            ):
                reasoning_chunks.append(str(content_item.get("text", "")))

    @staticmethod
    def _parse_arguments(raw: str) -> dict[str, Any]:
        text = raw.strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                return payload
            return {"raw": payload}
        except json.JSONDecodeError:
            return {"raw": text}

    def get_default_model(self) -> str:
        return self.default_model
