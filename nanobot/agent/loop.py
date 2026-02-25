"""Agent loop: the core processing engine."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.agent.context import ContextBuilder
from nanobot.agent.memory import MemoryStore
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebSearchTool, WebFetchTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.memory_tool import MemoryTool
from nanobot.agent.subagent import SubagentManager
from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import ExecToolConfig, MemoryConfig, SearchConfig
from nanobot.cron.service import CronService
from nanobot.exceptions import ConfigError, NanobotError
from nanobot.memory.compressor import SessionCompressor
from nanobot.memory.deduplicator import MemoryDeduplicator
from nanobot.memory.extractor import MemoryExtractor
from nanobot.memory.models import CompressionResult
from nanobot.session.manager import SessionManager
from nanobot.config.model_capabilities import supports_function_calling
from nanobot.agent.hallucination_detector import (
    detect_hallucination,
    create_honest_response,
)
from nanobot.agent.status import StatusMessage, StatusReporter, NullReporter


def _is_lazy_response(content: str, user_message: str = "") -> bool:
    """Heuristic check for stalled/non-actionable assistant replies."""
    import re

    if not content or len(content.strip()) < 50:
        return False

    # If user is explicitly asking for planning/discussion, do not treat as lazy.
    planning_keywords = (
        "plan",
        "方案",
        "讨论",
        "建议",
        "怎么看",
        "can you",
    )
    if user_message and any(k in user_message.lower() for k in planning_keywords):
        return False

    score = 0
    lower = content.lower()

    if any(k in lower for k in ["马上", "立即", "正在", "稍等", "please wait", "stand by"]):
        score += 2
    if any(k in lower for k in ["我会", "我将", "i will", "i'll"]):
        score += 2
    if any(
        k in lower
        for k in ["接下来", "下一步", "如果你同意", "已开始执行", "next step", "if you agree", "i will proceed", "started execution"]
    ):
        score += 2
    # 模型承认自己还没做正事（打卡后继续空转的典型模式）
    if any(k in lower for k in ["还没做", "还未完成", "未完成核心", "未完成你要求", "还没真正", "not yet done", "haven't completed", "not yet completed"]):
        score += 3
    if re.search(r"\n\s*[1-9]\.\s+", content):
        score += 1
    if len(content) > 200:
        score += 1
    if re.search(r"[?？]\s*$", content.strip()):
        score -= 1

    lazy = score >= 4
    if lazy:
        logger.info(f"Lazy response detected, score={score}")
    return lazy


def _is_execution_intent(user_message: str) -> bool:
    """Default to execution intent, excluding obvious Q&A prompts."""
    text = (user_message or "").strip()
    if not text:
        return False

    lower = text.lower()
    smalltalk_literals = {
        "hi",
        "hello",
        "hey",
        "thanks",
        "thank you",
        "ok",
        "okay",
        "你好",
        "嗨",
        "哈喽",
        "在吗",
        "谢谢",
        "好的",
    }
    if lower in smalltalk_literals or text in smalltalk_literals:
        return False

    if text.endswith(("?", "？", "吗", "呢")):
        return False

    cn_question_markers = (
        "为什么",
        "什么区别",
        "怎么理解",
        "如何看待",
        "如何解释",
    )
    en_question_markers = (
        "why",
        "what's the difference",
        "what is the difference",
        "how to explain",
    )
    if any(marker in text for marker in cn_question_markers):
        return False
    if any(marker in lower for marker in en_question_markers):
        return False
    return True


# 这些工具不算「真正完成了任务」，即使调了也不阻止空转干预
_IDLE_EXEMPT_TOOLS = frozenset({"message", "send_message"})
_IDLE_EXEMPT_EXEC_COMMANDS = frozenset({"date", "pwd", "whoami", "hostname"})


def _is_meaningful_tool_call(tool_name: str, arguments: dict | None = None) -> bool:
    """Return whether this tool call should count as meaningful progress."""
    if tool_name in _IDLE_EXEMPT_TOOLS:
        return False
    if tool_name != "exec":
        return True

    command = str((arguments or {}).get("command", "")).strip().lower()
    if not command:
        return False
    # If command chains multiple operations, treat it as meaningful.
    if any(op in command for op in ("&&", "||", ";", "|")):
        return True
    # Single lightweight identity/time checks do not count as meaningful progress.
    if re.fullmatch(r"(date|pwd|whoami|hostname)(\s+[-/\w:.]+)?", command):
        return False
    return True


_STOP_SIGNALS = frozenset(
    {
        "/stop",
        "stop",
        "cancel",
        "abort",
        "quit",
        "停止",
        "取消",
        "终止",
        "停下",
        "算了",
        "parar",
        "detener",
        "arreter",
        "arrêter",
        "anhalten",
        # Mixed-script homograph (Latin "o") kept intentionally for keyboard-layout mistakes.
        "останoвить",
        "остановить",
        "停止して",
        "やめて",
        "إيقاف",
    }
)


def _normalize_signal(text: str) -> str:
    normalized = re.sub(r"[\s\W_]+", "", text.lower(), flags=re.UNICODE)
    return normalized


_NORMALIZED_STOP_SIGNALS = frozenset(_normalize_signal(v) for v in _STOP_SIGNALS)


def _is_stop_signal(text: str) -> bool:
    candidate = (text or "").strip().lower()
    if not candidate:
        return False
    if candidate in _STOP_SIGNALS:
        return True
    return _normalize_signal(candidate) in _NORMALIZED_STOP_SIGNALS


@dataclass
class _ToolLoopSignal:
    kind: str
    count: int
    severity: str
    should_break: bool


class _ToolLoopDetector:
    """Detect repeated tool-call patterns that indicate a likely dead loop."""

    def __init__(
        self,
        *,
        window: int,
        warn_threshold: int,
        critical_threshold: int,
        break_threshold: int,
    ) -> None:
        self.window = max(6, window)
        self.warn_threshold = max(2, warn_threshold)
        self.critical_threshold = max(self.warn_threshold, critical_threshold)
        self.break_threshold = max(self.critical_threshold, break_threshold)
        self._history: deque[tuple[str, str, str]] = deque(maxlen=self.window)

    @staticmethod
    def _hash_tool_call(tool_name: str, arguments: dict | None) -> str:
        payload = json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha1(f"{tool_name}:{payload}".encode("utf-8")).hexdigest()
        return digest[:16]

    @staticmethod
    def _hash_result(result: str) -> str:
        digest = hashlib.sha1(result.encode("utf-8", errors="replace")).hexdigest()
        return digest[:16]

    def _severity(self, count: int) -> str | None:
        if count >= self.break_threshold:
            return "circuit_breaker"
        if count >= self.critical_threshold:
            return "critical"
        if count >= self.warn_threshold:
            return "warning"
        return None

    def _check_generic_repeat(self) -> _ToolLoopSignal | None:
        if not self._history:
            return None
        counts = Counter(item[1] for item in self._history)
        max_count = max(counts.values())
        severity = self._severity(max_count)
        if not severity:
            return None
        return _ToolLoopSignal(
            kind="generic_repeat",
            count=max_count,
            severity=severity,
            should_break=max_count >= self.break_threshold,
        )

    def _check_poll_no_progress(self) -> _ToolLoopSignal | None:
        if not self._history:
            return None
        tail = list(self._history)
        last = tail[-1]
        same_tail = 0
        for item in reversed(tail):
            if item[1] == last[1] and item[2] == last[2]:
                same_tail += 1
                continue
            break
        # Exclude the latest entry itself; we only care about repeated no-progress calls.
        repeat_count = max(0, same_tail - 1)
        severity = self._severity(repeat_count)
        if not severity:
            return None
        return _ToolLoopSignal(
            kind="known_poll_no_progress",
            count=repeat_count,
            severity=severity,
            should_break=repeat_count >= self.break_threshold,
        )

    def _check_ping_pong(self) -> _ToolLoopSignal | None:
        if len(self._history) < 4:
            return None
        calls = [item[1] for item in self._history]
        a = calls[-2]
        b = calls[-1]
        if a == b:
            return None
        streak = 0
        expected = b
        for value in reversed(calls):
            if value != expected:
                break
            streak += 1
            expected = a if expected == b else b
        if streak < 4:
            return None

        warn = max(4, self.warn_threshold // 2 * 2)
        critical = max(warn, self.critical_threshold // 2 * 2)
        breaker = max(critical, self.break_threshold // 2 * 2)
        severity = None
        should_break = False
        if streak >= breaker:
            severity = "circuit_breaker"
            should_break = True
        elif streak >= critical:
            severity = "critical"
        elif streak >= warn:
            severity = "warning"
        if not severity:
            return None
        return _ToolLoopSignal(
            kind="ping_pong",
            count=streak,
            severity=severity,
            should_break=should_break,
        )

    def observe(self, tool_name: str, arguments: dict | None, result: str) -> _ToolLoopSignal | None:
        tool_hash = self._hash_tool_call(tool_name, arguments)
        result_hash = self._hash_result(result)
        self._history.append((tool_name, tool_hash, result_hash))

        signals = [
            self._check_generic_repeat(),
            self._check_poll_no_progress(),
            self._check_ping_pong(),
        ]
        signals = [signal for signal in signals if signal is not None]
        if not signals:
            return None
        return max(signals, key=lambda x: x.count)


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 30,
        max_tokens: int = 8192,
        temperature: float = 0.7,
        memory_window: int = 50,
        brave_api_key: str | None = None,
        exec_config: ExecToolConfig | None = None,
        search_config: SearchConfig | None = None,
        memory_config: MemoryConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        reporter_factory: Callable[[str, str], StatusReporter] | None = None,
        idle_intervention: bool = True,
        loop_detection_enabled: bool = True,
        loop_window: int = 30,
        loop_warn_threshold: int = 8,
        loop_critical_threshold: int = 12,
        loop_break_threshold: int = 18,
        model_fallbacks: list[str] | None = None,
        failover_retry_once: bool = True,
        context_guard_min_tokens: int = 16000,
        context_guard_warn_tokens: int = 32000,
        tool_result_max_chars: int = 12000,
        compaction_enabled: bool = True,
        compaction_target_ratio: float = 0.45,
    ):
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.max_tokens = max(1, max_tokens)
        self.temperature = temperature
        self.memory_window = memory_window
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.search_config = search_config
        self.memory_config = memory_config or MemoryConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self.reporter_factory = reporter_factory  # Optional status reporter factory.
        self.idle_intervention = idle_intervention
        self.loop_detection_enabled = loop_detection_enabled
        self.loop_window = max(6, loop_window)
        self.loop_warn_threshold = max(2, loop_warn_threshold)
        self.loop_critical_threshold = max(self.loop_warn_threshold, loop_critical_threshold)
        self.loop_break_threshold = max(self.loop_critical_threshold, loop_break_threshold)
        self.model_fallbacks = model_fallbacks or []
        self.failover_retry_once = failover_retry_once
        self.context_guard_min_tokens = max(1024, context_guard_min_tokens)
        self.context_guard_warn_tokens = max(
            self.context_guard_min_tokens,
            context_guard_warn_tokens,
        )
        self.tool_result_max_chars = max(1000, tool_result_max_chars)
        self.compaction_enabled = compaction_enabled
        self.compaction_target_ratio = min(0.9, max(0.1, compaction_target_ratio))
        self.search_store = None
        self.search_indexer = None
        self.search_embedder = None
        self.memory_compressor: SessionCompressor | None = None
        self._compression_tasks: set[asyncio.Task] = set()
        self._session_compression_locks: dict[str, asyncio.Lock] = {}
        self._sessions_compressing: set[str] = set()

        self.context = ContextBuilder(workspace)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        self._running = False
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        self.tools.register(ReadFileTool(allowed_dir=allowed_dir))
        self.tools.register(WriteFileTool(allowed_dir=allowed_dir))
        self.tools.register(EditFileTool(allowed_dir=allowed_dir))
        self.tools.register(ListDirTool(allowed_dir=allowed_dir))

        # Shell tool.
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
        ))

        # Web tools.
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())

        # Message tool.
        message_tool = MessageTool(send_callback=self.bus.publish_outbound)
        self.tools.register(message_tool)

        spawn_tool = SpawnTool(manager=self.subagents)
        self.tools.register(spawn_tool)

        # Memory management tool.
        memory_tool = MemoryTool(
            memory_store=self.context.memory,
            workspace=self.workspace,
        )
        self.tools.register(memory_tool)

        if self.search_config and self.search_config.enabled:
            from nanobot.agent.tools.knowledge_search import KnowledgeSearchTool
            from nanobot.search.embedder import SentenceTransformerEmbedder
            from nanobot.search.indexer import Indexer
            from nanobot.search.store import SearchStore

            db_path = (
                Path(self.search_config.db_path).expanduser()
                if self.search_config.db_path
                else self.workspace / "search" / "index.sqlite"
            )
            self.search_store = SearchStore(db_path)
            self.search_indexer = Indexer(self.search_store, self.workspace)
            if self.search_config.auto_index:
                self.search_indexer.auto_index_on_startup(self.search_config.index_dirs)
            if self.search_config.vector_enabled:
                try:
                    self.search_embedder = SentenceTransformerEmbedder(
                        self.search_config.embedding_model
                    )
                    embed_stats = self.search_indexer.embed_documents(
                        embedder=self.search_embedder,
                        force=False,
                        chunk_size=self.search_config.embedding_chunk_size,
                        chunk_overlap=self.search_config.embedding_chunk_overlap,
                        batch_size=self.search_config.embedding_batch_size,
                    )
                    if embed_stats["docs_embedded"] > 0:
                        logger.info(
                            "Semantic embeddings updated: "
                            f"{embed_stats['docs_embedded']} docs / {embed_stats['chunks_embedded']} chunks"
                        )
                except RuntimeError as e:
                    logger.warning(f"Semantic search disabled: {e}")
                except Exception as e:
                    logger.warning(f"Semantic embedding bootstrap failed: {e}")
            memory_tool.set_indexer(self.search_indexer)
            self.tools.register(
                KnowledgeSearchTool(
                    store=self.search_store,
                    config=self.search_config,
                    embedder=self.search_embedder,
                )
            )
        self._setup_memory_compressor()

        # Scheduled-task tool.
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

    def _setup_memory_compressor(self) -> None:
        """Initialize structured session compressor based on memory settings."""
        if not self.memory_config.enabled:
            self.memory_compressor = None
            return
        dedup = MemoryDeduplicator(
            store=self.search_store,
            provider=self.provider,
            model=self.model,
            search_config=self.search_config,
            embedder=self.search_embedder,
            min_score=self.memory_config.dedup_min_score,
            output_language=self.memory_config.output_language,
        )
        extractor = MemoryExtractor(
            provider=self.provider,
            workspace=self.workspace,
            model=self.model,
            output_language=self.memory_config.output_language,
        )
        self.memory_compressor = SessionCompressor(
            extractor=extractor,
            deduplicator=dedup,
            memory_store=self.context.memory,
            provider=self.provider,
            model=self.model,
            indexer=self.search_indexer,
            max_memories_per_category=self.memory_config.max_memories_per_category,
            output_language=self.memory_config.output_language,
        )

    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=1.0
                )

                reporter = None
                if self.reporter_factory and msg.channel != "system":
                    try:
                        reporter = self.reporter_factory(msg.channel, msg.chat_id)
                    except (TypeError, ValueError, RuntimeError) as e:
                        logger.warning(f"鍒涘缓鐘舵€佹姤鍛婂櫒澶辫触: {e}")

                # Process one inbound message.
                try:
                    response = await self._process_message(msg, reporter=reporter)

                    if reporter:
                        await reporter.finalize(delete_status=True)

                    if response:
                        await self.bus.publish_outbound(response)
                except (NanobotError, RuntimeError, ValueError, OSError) as e:
                    logger.error(
                        "Message processing failed "
                        f"(channel={msg.channel}, chat_id={msg.chat_id}, sender_id={msg.sender_id}): {e}"
                    )
                    if reporter:
                        await reporter.finalize(delete_status=True)
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"[E_AGENT_PROCESSING] 请求处理失败: {str(e)}"
                    ))
                except Exception as e:
                    logger.exception(
                        "Unexpected message-processing failure "
                        f"(channel={msg.channel}, chat_id={msg.chat_id}, sender_id={msg.sender_id}): {e}"
                    )
                    if reporter:
                        await reporter.finalize(delete_status=True)
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="[E_AGENT_UNEXPECTED] 处理请求时发生未预期错误。",
                    ))
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        for task in list(self._compression_tasks):
            task.cancel()
        if self.search_store is not None:
            self.search_store.close()
        logger.info("Agent loop stopping")

    def _update_provider_env(self, model: str, api_key: str | None, api_base: str | None) -> None:
        """根据 registry 设置 provider 环境变量，避免硬编码。"""
        import os
        from nanobot.providers.registry import find_by_model

        if api_key:
            self.provider.api_key = api_key

            # 通过 registry 查找正确的 env_key 和 env_extras
            spec = find_by_model(model)
            if spec:
                os.environ[spec.env_key] = api_key
                logger.debug(f"Set {spec.env_key} for model {model}")
                resolved_base = api_base or spec.default_api_base or ""
                for env_name, template in spec.env_extras:
                    value = template.replace("{api_key}", api_key).replace("{api_base}", resolved_base)
                    os.environ[env_name] = value
                    logger.debug(f"Set {env_name} for model {model}")

        # 始终更新 api_base，确保切换模型后 provider endpoint 正确重置
        self.provider.api_base = api_base

    def _refresh_runtime_options(self, config) -> None:
        """Refresh loop controls from latest config without recreating the loop."""
        defaults = config.agents.defaults
        self.max_tokens = max(1, defaults.max_tokens)
        self.temperature = defaults.temperature
        self.idle_intervention = defaults.idle_intervention
        self.loop_detection_enabled = defaults.loop_detection_enabled
        self.loop_window = max(6, defaults.loop_window)
        self.loop_warn_threshold = max(2, defaults.loop_warn_threshold)
        self.loop_critical_threshold = max(
            self.loop_warn_threshold,
            defaults.loop_critical_threshold,
        )
        self.loop_break_threshold = max(
            self.loop_critical_threshold,
            defaults.loop_break_threshold,
        )
        self.model_fallbacks = list(defaults.model_fallbacks)
        self.failover_retry_once = defaults.failover_retry_once
        self.context_guard_min_tokens = max(1024, defaults.context_guard_min_tokens)
        self.context_guard_warn_tokens = max(
            self.context_guard_min_tokens,
            defaults.context_guard_warn_tokens,
        )
        self.tool_result_max_chars = max(1000, defaults.tool_result_max_chars)
        self.compaction_enabled = defaults.compaction_enabled
        self.compaction_target_ratio = min(0.9, max(0.1, defaults.compaction_target_ratio))

    @staticmethod
    def _estimate_context_window_tokens(model: str) -> int:
        lower = model.lower()
        if "gemini" in lower:
            return 1_000_000
        if "claude" in lower:
            return 200_000
        if "gpt-5" in lower:
            return 256_000
        if "gpt-4" in lower:
            return 128_000
        if "deepseek" in lower:
            return 64_000
        if "qwen" in lower or "llama" in lower:
            return 32_000
        return 32_000

    @staticmethod
    def _estimate_text_tokens(text: str) -> int:
        if not text:
            return 0
        # Use UTF-8 byte length to stay conservative for CJK-heavy content.
        return max(1, len(text.encode("utf-8", errors="ignore")) // 4)

    def _estimate_message_tokens(self, message: dict) -> int:
        content = message.get("content")
        if isinstance(content, str):
            return self._estimate_text_tokens(content)
        if isinstance(content, list):
            total = 0
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    total += self._estimate_text_tokens(str(item.get("text", "")))
                else:
                    total += 256
            return total
        return self._estimate_text_tokens(str(content))

    def _estimate_messages_tokens(self, messages: list[dict]) -> int:
        return sum(self._estimate_message_tokens(msg) for msg in messages)

    def _truncate_text(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        if max_chars <= 64:
            return text[:max_chars]
        head = max_chars // 2
        tail = max_chars - head - 24
        if tail <= 0:
            return text[:max_chars]
        omitted = len(text) - head - tail
        return f"{text[:head]}...[省略 {omitted} 字符]...{text[-tail:]}"

    def _trim_messages_to_budget(self, messages: list[dict], budget: int) -> list[dict]:
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        kept: list[dict] = []
        used = self._estimate_messages_tokens(system_msgs)

        for msg in reversed(non_system):
            msg_tokens = self._estimate_message_tokens(msg)
            if used + msg_tokens > budget and kept:
                continue
            if used + msg_tokens > budget and not kept:
                shrunk = dict(msg)
                content = shrunk.get("content")
                if isinstance(content, str):
                    max_chars = max(500, (budget - used) * 4)
                    shrunk["content"] = self._truncate_text(content, max_chars=max_chars)
                kept.append(shrunk)
                used += self._estimate_message_tokens(shrunk)
                continue
            kept.append(msg)
            used += msg_tokens

        return [*system_msgs, *reversed(kept)]

    def _message_to_compaction_text(self, msg: dict) -> str:
        role = str(msg.get("role", "unknown")).upper()
        content = msg.get("content")
        if isinstance(content, str):
            body = content
        elif isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            body = "\n".join(parts) if parts else "[non-text content]"
        else:
            body = str(content or "")
        body = self._truncate_text(body.strip(), max_chars=2000)
        return f"[{role}] {body}"

    def _split_messages_for_compaction(
        self,
        messages: list[dict],
        *,
        target_chunk_tokens: int,
        max_chunks: int = 3,
    ) -> list[list[dict]]:
        if not messages:
            return []
        chunks: list[list[dict]] = []
        current: list[dict] = []
        current_tokens = 0

        for msg in messages:
            msg_tokens = self._estimate_message_tokens(msg)
            if current and current_tokens + msg_tokens > target_chunk_tokens and len(chunks) < max_chunks - 1:
                chunks.append(current)
                current = [msg]
                current_tokens = msg_tokens
                continue
            current.append(msg)
            current_tokens += msg_tokens

        if current:
            chunks.append(current)
        return chunks

    def _fallback_chunk_summary(self, chunk: list[dict], index: int) -> str:
        tail = chunk[-4:] if len(chunk) > 4 else chunk
        lines = [self._message_to_compaction_text(msg) for msg in tail]
        body = "\n".join(lines)
        return f"片段 {index}（降级摘要）:\n{self._truncate_text(body, max_chars=1200)}"

    async def _summarize_compaction_text(
        self,
        *,
        source_text: str,
        active_model: str,
        runtime_config=None,
    ) -> tuple[str | None, str]:
        prompt_messages = [
            {
                "role": "system",
                "content": (
                    "你是会话上下文压缩器。请输出简洁、可检索的摘要，保留：关键目标、"
                    "已执行操作、失败原因、待办项、重要路径/命令。输出纯文本，不要 Markdown。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请压缩以下历史对话内容，限制在 8-12 行内：\n\n"
                    f"{source_text}"
                ),
            },
        ]

        try:
            response, used_model = await asyncio.wait_for(
                self._chat_with_failover(
                    messages=prompt_messages,
                    tools=None,
                    tool_choice="auto",
                    primary_model=active_model,
                    runtime_config=runtime_config,
                ),
                timeout=30,
            )
        except asyncio.TimeoutError:
            logger.warning("Compaction summary timed out (model={})", active_model)
            return None, active_model
        if not response or response.finish_reason == "error":
            return None, used_model
        text = (response.content or "").strip()
        if not text:
            return None, used_model
        return self._truncate_text(text, max_chars=2000), used_model

    async def _compact_messages_for_context(
        self,
        messages: list[dict],
        model: str,
        runtime_config=None,
    ) -> tuple[list[dict], str]:
        if not self.compaction_enabled:
            return self._guard_context_window(messages, model), model

        window = self._estimate_context_window_tokens(model)
        budget = int(window * 0.9)
        estimated = self._estimate_messages_tokens(messages)
        if estimated <= budget:
            return messages, model

        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        if len(non_system) < 4:
            return self._guard_context_window(messages, model), model

        recent_budget = int(budget * (1.0 - self.compaction_target_ratio))
        recent: list[dict] = []
        recent_tokens = 0
        for msg in reversed(non_system):
            msg_tokens = self._estimate_message_tokens(msg)
            if recent and recent_tokens + msg_tokens > recent_budget:
                break
            recent.append(msg)
            recent_tokens += msg_tokens
        recent = list(reversed(recent))
        old_count = len(non_system) - len(recent)
        if old_count <= 0:
            return self._guard_context_window(messages, model), model

        old_messages = non_system[:old_count]
        chunk_target = max(800, int((budget * self.compaction_target_ratio) / 2))
        chunks = self._split_messages_for_compaction(
            old_messages,
            target_chunk_tokens=chunk_target,
            max_chunks=3,
        )
        if not chunks:
            return self._guard_context_window(messages, model), model

        active_model = model
        summaries: list[str] = []
        calls_used = 0
        max_calls = 3
        for idx, chunk in enumerate(chunks, start=1):
            if calls_used >= max_calls:
                break
            chunk_text = "\n".join(self._message_to_compaction_text(msg) for msg in chunk)
            summary, used_model = await self._summarize_compaction_text(
                source_text=chunk_text,
                active_model=active_model,
                runtime_config=runtime_config,
            )
            active_model = used_model
            calls_used += 1
            if summary:
                summaries.append(f"片段 {idx}: {summary}")
            else:
                summaries.append(self._fallback_chunk_summary(chunk, idx))

        if len(summaries) > 1 and calls_used < max_calls:
            merged_source = "\n\n".join(summaries)
            merged, used_model = await self._summarize_compaction_text(
                source_text=merged_source,
                active_model=active_model,
                runtime_config=runtime_config,
            )
            active_model = used_model
            calls_used += 1
            if merged:
                summaries = [merged]

        summary_text = "\n".join(summaries).strip()
        if not summary_text:
            return self._guard_context_window(messages, model), model

        summary_message = {
            "role": "system",
            "content": (
                "# 会话压缩摘要\n"
                "以下为较早对话的压缩摘要，请与近期消息一起使用：\n"
                f"{summary_text}"
            ),
        }
        candidate = [*system_msgs, summary_message, *recent]
        compacted = self._trim_messages_to_budget(candidate, budget)
        logger.info(
            "Context compaction applied: model={}, before_tokens={}, after_tokens={}, calls_used={}, old_messages={}",
            model,
            estimated,
            self._estimate_messages_tokens(compacted),
            calls_used,
            old_count,
        )
        return compacted, active_model

    def _guard_context_window(self, messages: list[dict], model: str) -> list[dict]:
        window = self._estimate_context_window_tokens(model)
        if window < self.context_guard_min_tokens:
            raise NanobotError(
                f"[E_CONTEXT_WINDOW] 模型上下文窗口过小: {window} < {self.context_guard_min_tokens}"
            )
        if window < self.context_guard_warn_tokens:
            logger.warning(
                "Model {} has a small context window ({}) below warn threshold {}",
                model,
                window,
                self.context_guard_warn_tokens,
            )

        budget = int(window * 0.9)
        estimated = self._estimate_messages_tokens(messages)
        if estimated <= budget:
            return messages

        trimmed = self._trim_messages_to_budget(messages, budget)
        logger.warning(
            "Context guard pruned messages: model={}, before_tokens={}, after_tokens={}, removed={}",
            model,
            estimated,
            self._estimate_messages_tokens(trimmed),
            max(0, len(messages) - len(trimmed)),
        )
        return trimmed

    def _truncate_tool_result(self, result: str) -> str:
        if len(result) <= self.tool_result_max_chars:
            return result

        head = 4000
        tail = 2000
        if self.tool_result_max_chars < head + tail:
            head = int(self.tool_result_max_chars * 0.67)
            tail = max(0, self.tool_result_max_chars - head)
        omitted = len(result) - head - tail
        tail_text = result[-tail:] if tail > 0 else ""
        return f"{result[:head]}\n[...省略 {omitted} 字符...]\n{tail_text}"

    def _get_session_compression_lock(self, session_key: str) -> asyncio.Lock:
        lock = self._session_compression_locks.get(session_key)
        if lock is None:
            lock = asyncio.Lock()
            self._session_compression_locks[session_key] = lock
        return lock

    @staticmethod
    def _add_tool_error_hint(result_text: str) -> str:
        """Append actionable hint for tool failures."""
        if not isinstance(result_text, str):
            return str(result_text)
        if not result_text.startswith("Error"):
            return result_text
        hint = (
            "\nHint: 请根据错误调整参数后重试；"
            "路径类错误先调用 list_dir/read_file 确认目标是否存在且有权限。"
        )
        return result_text if hint.strip() in result_text else result_text + hint

    @staticmethod
    def _classify_response_error(response) -> str:
        if response.error_type:
            return response.error_type
        text = str(response.content or "").lower()
        if any(term in text for term in ("rate limit", "too many requests", "429")):
            return "rate_limit"
        if any(term in text for term in ("unauthorized", "forbidden", "invalid api key", "401", "403")):
            return "auth"
        if any(term in text for term in ("billing", "quota", "402")):
            return "billing"
        if any(term in text for term in ("timeout", "timed out", "etimedout", "econnreset", "econnaborted", "502", "503", "504")):
            return "timeout"
        if any(term in text for term in ("model not found", "unknown model", "does not exist")):
            return "model_not_found"
        if any(
            term in text
            for term in (
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

    def _should_retry_same_model(self, error_type: str) -> bool:
        return error_type in {"timeout", "rate_limit"}

    async def _chat_with_failover(
        self,
        *,
        messages: list[dict],
        tools: list[dict] | None,
        tool_choice: str,
        primary_model: str,
        runtime_config=None,
    ):
        candidates = [primary_model]
        for fallback in self.model_fallbacks:
            if fallback and fallback not in candidates:
                candidates.append(fallback)

        last_response = None
        active_model = primary_model
        for model_name in candidates:
            if runtime_config is not None:
                self._update_provider_env(
                    model_name,
                    runtime_config.get_api_key(model_name),
                    runtime_config.get_api_base(model_name),
                )

            retries_left = 1 if self.failover_retry_once else 0
            while True:
                response = await self.provider.chat(
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    model=model_name,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                )
                last_response = response
                active_model = model_name
                if response.finish_reason != "error":
                    return response, active_model

                error_type = self._classify_response_error(response)
                if retries_left > 0 and self._should_retry_same_model(error_type):
                    retries_left -= 1
                    logger.warning(
                        "LLM call failed on {} with {}. Retrying once.",
                        model_name,
                        error_type,
                    )
                    continue
                logger.warning("LLM call failed on {} with {}.", model_name, error_type)
                break

            if model_name != candidates[-1]:
                logger.warning("Switching to fallback model after failure: {}", model_name)

        return last_response, active_model

    def _build_tool_loop_break_text(self, signal: _ToolLoopSignal, tool_name: str) -> str:
        return (
            "⚠️ 检测到工具调用可能进入死循环，已自动中断以节省成本。\n\n"
            f"- 检测类型: {signal.kind}\n"
            f"- 工具: {tool_name}\n"
            f"- 重复次数: {signal.count}\n"
            f"- 级别: {signal.severity}\n\n"
            "建议：调整指令约束、减少轮询类调用，或提供更明确的停止条件。"
        )

    async def _process_message(
        self,
        msg: InboundMessage,
        reporter: StatusReporter | None = None
    ) -> OutboundMessage | None:
        """
        Process a single inbound message.

        Args:
            msg: The inbound message to process.
            reporter: Optional status reporter for realtime progress updates.

        Returns:
            The response message, or None if no response needed.
        """
        if reporter is None:
            reporter = NullReporter()

        if msg.channel == "system":
            return await self._process_system_message(msg)

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info(f"Processing message from {msg.channel}:{msg.sender_id}: {preview}")

        # Reload config dynamically so `/model` changes take effect immediately.
        current_model = self.model
        runtime_config = None
        try:
            runtime_config = load_config()
            current_model = runtime_config.agents.defaults.model
            self._refresh_runtime_options(runtime_config)
            api_key = runtime_config.get_api_key(current_model)
            api_base = runtime_config.get_api_base(current_model)
            self._update_provider_env(current_model, api_key, api_base)
            if current_model != self.model:
                logger.info(f"Model changed from {self.model} to {current_model}")
                self.model = current_model
                self._setup_memory_compressor()
        except (ConfigError, ValueError, OSError) as e:
            logger.warning(f"Failed to reload config: {e}, using cached model")

        override_session_key = msg.metadata.get("session_key") if msg.metadata else None
        effective_session_key = override_session_key or msg.session_key
        session = self.sessions.get_or_create(effective_session_key)

        # Handle slash commands.
        raw_cmd = msg.content.strip()
        cmd = raw_cmd.lower()
        if _is_stop_signal(raw_cmd):
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="已收到停止指令，本次请求已中断。",
            )
        if cmd == "/new":
            msg_count = len(session.messages)
            lock = self._get_session_compression_lock(session.key)
            async with lock:
                if session.key in self._sessions_compressing:
                    return OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="会话正在进行记忆整合，请稍后重试 /new。",
                    )
                self._sessions_compressing.add(session.key)
                try:
                    result = await self._compress_session_for_new(session)
                finally:
                    self._sessions_compressing.discard(session.key)

            if result is None and msg_count > 0:
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="记忆整合失败，已保留当前会话。请稍后重试 /new。",
                )

            session.clear()
            self.sessions.save(session)
            if result is not None:
                parts = ["已开始新会话。"]
                parts.append(f"- 已归档消息数: {msg_count}")
                parts.append(
                    f"- 记忆更新: created={result.created}, merged={result.merged}, skipped={result.skipped}"
                )
                if result.summary:
                    parts.append("- 历史记录: 已写入 HISTORY.md")
                else:
                    parts.append("- 历史记录: 无新增摘要")
                feedback = "\n".join(parts)
            elif msg_count == 0:
                feedback = "已开始新会话（原会话本来就是空的）。"
            else:
                feedback = "已开始新会话（记忆整合失败）。"
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=feedback)
        if cmd == "/clear":
            removed = len(session.messages)
            session.clear()
            self.sessions.save(session)
            feedback = (
                f"会话已清空（删除 {removed} 条消息）。"
                if removed
                else "会话本来就是空的。"
            )
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=feedback)
        if cmd == "/status":
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=self._build_status_text(session),
            )
        if cmd == "/model" or cmd.startswith("/model "):
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=self._handle_model_command(raw_cmd),
            )
        if cmd == "/help":
            help_text = (
                "nanobot 命令：\n\n"
                "/new - 开始新会话并整合记忆\n"
                "/clear - 清空当前会话历史\n"
                "/stop - 停止当前请求\n"
                "/model <name> - 切换模型\n"
                "/status - 查看运行状态\n"
                "/help - 显示帮助"
            )
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=help_text)

        # Refresh per-channel tool context for this message.
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(msg.channel, msg.chat_id)

        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(msg.channel, msg.chat_id)

        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(msg.channel, msg.chat_id)

        # Build LLM context with history + current message.
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )
        messages, current_model = await self._compact_messages_for_context(
            messages,
            current_model,
            runtime_config=runtime_config,
        )

        # Main agent loop.
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        tools_were_called = False  # Track whether any tool was actually called.
        meaningful_tools_called = False
        required_retry_used = False
        required_no_tool_observed = False
        required_no_tool_streak = int(session.metadata.get("required_no_tool_streak", 0))
        execution_intent = _is_execution_intent(msg.content)
        active_model = current_model
        loop_detector = (
            _ToolLoopDetector(
                window=self.loop_window,
                warn_threshold=self.loop_warn_threshold,
                critical_threshold=self.loop_critical_threshold,
                break_threshold=self.loop_break_threshold,
            )
            if self.loop_detection_enabled
            else None
        )

        # Check whether the current model supports function-calling.
        model_supports_tools = supports_function_calling(active_model)
        if not model_supports_tools:
            logger.warning(f"Model {active_model} does not support function calling, tools disabled")

        # Persist the inbound user message before tool/assistant chain to preserve full chronology.
        session.add_message("user", msg.content)

        while iteration < self.max_iterations:
            iteration += 1

            model_supports_tools = supports_function_calling(active_model)
            is_codex = "codex" in active_model.lower()
            await reporter.report(StatusMessage.thinking(is_codex=is_codex))

            tools_definitions = self.tools.get_definitions() if model_supports_tools else None
            requested_tool_choice = (
                "required"
                if (
                    self.idle_intervention
                    and execution_intent
                    and model_supports_tools
                    and not meaningful_tools_called
                )
                else "auto"
            )
            logger.debug(
                "Tool choice decision: execution_intent={}, meaningful_tools_called={}, tool_choice={}",
                execution_intent,
                meaningful_tools_called,
                requested_tool_choice,
            )

            response, used_model = await self._chat_with_failover(
                messages=messages,
                tools=tools_definitions,
                tool_choice=requested_tool_choice,
                primary_model=active_model,
                runtime_config=runtime_config,
            )
            if used_model != active_model:
                logger.warning("Using fallback model for this request: {} -> {}", active_model, used_model)
                active_model = used_model
            actual_tool_choice = requested_tool_choice
            if requested_tool_choice == "required" and response.finish_reason == "error":
                logger.warning(
                    "[E_TOOL_CHOICE_FALLBACK] tool_choice=required failed, retrying with auto once"
                )
                response, used_model = await self._chat_with_failover(
                    messages=messages,
                    tools=tools_definitions,
                    tool_choice="auto",
                    primary_model=active_model,
                    runtime_config=runtime_config,
                )
                active_model = used_model
                actual_tool_choice = "auto"

            # Handle tool calls.
            if response.has_tool_calls:
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )
                session.add_message(
                    "assistant",
                    response.content or "",
                    tool_calls=tool_call_dicts if tool_call_dicts else None,
                    reasoning_content=response.reasoning_content or None,
                )

                # Execute tool call.
                tool_loop_break = False
                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")

                    await reporter.report(StatusMessage.tool_start(
                        tool_call.name,
                        tool_call.arguments
                    ))

                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    result_text = self._add_tool_error_hint(
                        self._truncate_tool_result(str(result))
                    )

                    success = not (isinstance(result_text, str) and result_text.startswith("Error"))
                    await reporter.report(StatusMessage.tool_done(tool_call.name, success))

                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result_text
                    )
                    session.add_message(
                        "tool",
                        result_text,
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                    )
                    tools_used.append(tool_call.name)
                    if _is_meaningful_tool_call(tool_call.name, tool_call.arguments):
                        meaningful_tools_called = True
                    tools_were_called = True
                    if loop_detector is not None:
                        signal = loop_detector.observe(
                            tool_call.name,
                            tool_call.arguments,
                            result_text,
                        )
                        if signal:
                            logger.warning(
                                "Tool loop signal: kind={}, severity={}, count={}",
                                signal.kind,
                                signal.severity,
                                signal.count,
                            )
                            if signal.should_break:
                                final_content = self._build_tool_loop_break_text(
                                    signal,
                                    tool_call.name,
                                )
                                tool_loop_break = True
                                break

                if tool_loop_break:
                    break

                # 如果本轮所有工具调用都是 exempt（如 message），且之前已做过正事 → 结束循环
                all_exempt = all(
                    not _is_meaningful_tool_call(tc.name, tc.arguments)
                    for tc in response.tool_calls
                )
                if all_exempt and meaningful_tools_called:
                    logger.info(
                        "All tool calls in this round are exempt (e.g. message) and meaningful work already done, stopping loop"
                    )
                    # 用 message 内容或 response.content 作为最终输出
                    last_msg_content = None
                    for tc in reversed(response.tool_calls):
                        if tc.name in ("message", "send_message"):
                            last_msg_content = (tc.arguments or {}).get("content")
                            if last_msg_content:
                                break
                    final_content = response.content or last_msg_content or ""
                    break

                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "根据工具返回结果决定下一步：可继续调用工具执行、"
                            "先分析后调整方案，或在任务完成时给出总结。"
                        ),
                    }
                )
            else:
                if response.finish_reason == "error":
                    error_type = self._classify_response_error(response)
                    logger.warning(
                        "LLM response ended with error after failover attempts: type={}",
                        error_type,
                    )
                lazy = _is_lazy_response(response.content or "", msg.content) if response.content else False
                if lazy:
                    logger.warning("Lazy response observed (monitor-only): iteration={}", iteration)
                hallucination = detect_hallucination(
                    response.content or "",
                    tools_were_called=False,
                    model_supports_tools=model_supports_tools,
                ) if response.content else None
                if hallucination and hallucination.is_hallucination:
                    logger.warning(
                        "Hallucination signal observed (monitor-only): pattern={}, confidence={:.2f}",
                        hallucination.pattern_name,
                        hallucination.confidence,
                    )
                required_guard = (
                    self.idle_intervention
                    and model_supports_tools
                    and execution_intent
                    and not meaningful_tools_called
                    and requested_tool_choice == "required"
                )
                if required_guard:
                    if not required_retry_used and iteration < self.max_iterations:
                        logger.warning(
                            "[E_TOOL_CHOICE_IGNORED] tool_choice=required returned no tool_calls, retrying with explicit nudge once"
                        )
                        messages = self.context.add_assistant_message(
                            messages, response.content, reasoning_content=response.reasoning_content
                        )
                        nudge = (
                            "检测到执行型请求。下一轮请优先直接调用工具（read_file/list_dir/exec），"
                            "不要先征求确认。"
                        )
                        messages = self.context.add_user_nudge(messages, nudge)
                        required_retry_used = True
                        continue

                    required_no_tool_observed = True
                    required_no_tool_streak += 1
                    logger.warning(
                        "[E_IDLE_EXEC_NO_MEANINGFUL_TOOL] tool_choice={}, streak={}, allowing normal output",
                        actual_tool_choice,
                        required_no_tool_streak,
                    )

                # No tool call; use model content as final output.
                final_content = response.content
                break

        if not final_content:
            final_content = "（任务已执行完毕，但模型未生成回复文本。）"

        # Run hallucination checks when tools are unavailable or not used.
        should_check_hallucination = (
            not model_supports_tools
            or (model_supports_tools and not tools_were_called)  # model supports tools, but none were called
        )
        if should_check_hallucination:
            hallucination = detect_hallucination(
                final_content,
                tools_were_called=tools_were_called,
                model_supports_tools=model_supports_tools
            )
            if hallucination.is_hallucination:
                logger.warning(
                    f"Hallucination detected and blocked: {hallucination.pattern_name} "
                    f"(confidence: {hallucination.confidence:.2f})"
                )
                final_content = create_honest_response(self.model)

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info(f"Response to {msg.channel}:{msg.sender_id}: {preview}")

        # Persist conversation; include used tools for later memory consolidation.
        if required_no_tool_observed:
            session.metadata["required_no_tool_streak"] = required_no_tool_streak
        else:
            session.metadata["required_no_tool_streak"] = 0
        session.add_message(
            "assistant",
            final_content,
            tools_used=tools_used if tools_used else None,
        )
        self._maybe_schedule_session_compression(session)
        self.sessions.save(session)

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=msg.metadata or {},
        )

    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a system message (e.g., subagent announce).

        The chat_id field contains "original_channel:original_chat_id" to route
        the response back to the correct destination.
        """
        logger.info(f"Processing system message from {msg.sender_id}")

        if ":" in msg.chat_id:
            parts = msg.chat_id.split(":", 1)
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            origin_channel = "cli"
            origin_chat_id = msg.chat_id

        # Reuse the source conversation session.
        session_key = f"{origin_channel}:{origin_chat_id}"
        session = self.sessions.get_or_create(session_key)

        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(origin_channel, origin_chat_id)

        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(origin_channel, origin_chat_id)

        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(origin_channel, origin_chat_id)

        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            channel=origin_channel,
            chat_id=origin_chat_id,
        )
        messages, active_system_model = await self._compact_messages_for_context(
            messages,
            self.model,
        )

        iteration = 0
        final_content = None
        active_model = active_system_model
        loop_detector = (
            _ToolLoopDetector(
                window=self.loop_window,
                warn_threshold=self.loop_warn_threshold,
                critical_threshold=self.loop_critical_threshold,
                break_threshold=self.loop_break_threshold,
            )
            if self.loop_detection_enabled
            else None
        )

        while iteration < self.max_iterations:
            iteration += 1

            response, used_model = await self._chat_with_failover(
                messages=messages,
                tools=self.tools.get_definitions(),
                tool_choice="auto",
                primary_model=active_model,
            )
            active_model = used_model

            if response.has_tool_calls:
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )

                tool_loop_break = False
                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    result_text = self._add_tool_error_hint(
                        self._truncate_tool_result(str(result))
                    )
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result_text
                    )
                    if loop_detector is not None:
                        signal = loop_detector.observe(
                            tool_call.name,
                            tool_call.arguments,
                            result_text,
                        )
                        if signal:
                            logger.warning(
                                "System message tool loop signal: kind={}, severity={}, count={}",
                                signal.kind,
                                signal.severity,
                                signal.count,
                            )
                            if signal.should_break:
                                final_content = self._build_tool_loop_break_text(
                                    signal,
                                    tool_call.name,
                                )
                                tool_loop_break = True
                                break
                if tool_loop_break:
                    break
                messages, active_model = await self._compact_messages_for_context(
                    messages,
                    active_model,
                )
            else:
                final_content = response.content
                break

        if final_content is None:
            final_content = "Background task completed."

        session.add_message("user", f"[System: {msg.sender_id}] {msg.content}")
        session.add_message("assistant", final_content)
        self.sessions.save(session)

        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content
        )

    def _build_status_text(self, session) -> str:
        """Build a concise runtime status message."""
        model = self.model
        fc = "已启用" if supports_function_calling(model) else "未启用"
        tools = ", ".join(self.tools.tool_names) if self.tools.tool_names else "（无）"
        return (
            "运行状态：\n\n"
            f"- 模型: {model}\n"
            f"- Function calling: {fc}\n"
            f"- 已注册工具: {tools}\n"
            f"- 会话消息数: {len(session.messages)}\n"
            f"- 记忆自动压缩: {'开启' if self.memory_config.auto_compress else '关闭'}\n"
            f"- Loop detection: {'开启' if self.loop_detection_enabled else '关闭'} "
            f"(break={self.loop_break_threshold})\n"
            f"- Model failover: {'开启' if bool(self.model_fallbacks) else '关闭'}\n"
            f"- Context compaction: {'开启' if self.compaction_enabled else '关闭'} "
            f"(ratio={self.compaction_target_ratio:.2f})\n"
            f"- Tool 输出预算: {self.tool_result_max_chars} chars"
        )

    def _handle_model_command(self, raw_cmd: str) -> str:
        """Handle /model command for querying or switching model."""
        parts = raw_cmd.split(maxsplit=1)

        try:
            config = load_config()
        except (ConfigError, ValueError, OSError) as e:
            return f"读取配置失败: {e}"

        if len(parts) == 1:
            model = config.agents.defaults.model
            provider = config.get_provider_name(model) or "未匹配"
            fc = "已启用" if supports_function_calling(model) else "受限"
            return (
                "当前模型配置：\n\n"
                f"- 模型: {model}\n"
                f"- 匹配 provider: {provider}\n"
                f"- Function calling: {fc}\n\n"
                "切换模型命令: /model <provider/model>"
            )

        new_model = parts[1].strip()
        if not new_model:
            return "错误：模型名不能为空。用法: /model <provider/model>"

        old_model = config.agents.defaults.model
        config.agents.defaults.model = new_model

        try:
            save_config(config)
        except (ConfigError, ValueError, OSError) as e:
            return f"保存配置失败: {e}"

        self.model = new_model
        self._update_provider_env(
            new_model,
            config.get_api_key(new_model),
            config.get_api_base(new_model),
        )
        self._setup_memory_compressor()
        fc = "已启用" if supports_function_calling(new_model) else "受限"
        return (
            "模型切换成功。\n\n"
            f"- 旧模型: {old_model}\n"
            f"- 新模型: {new_model}\n"
            f"- Function calling: {fc}"
        )

    def _maybe_schedule_session_compression(self, session) -> None:
        if (
            self.memory_compressor is None
            or not self.memory_config.auto_compress
            or self.memory_config.compress_threshold <= 0
        ):
            return
        if session.key in self._sessions_compressing:
            return

        current_count = len(session.messages)
        last_count = int(session.metadata.get("last_compressed_count", 0))
        if current_count < self.memory_config.compress_threshold:
            return
        if current_count - last_count < self.memory_config.compress_threshold:
            return

        task = asyncio.create_task(self._compress_session_background(session.key, current_count))
        self._compression_tasks.add(task)
        task.add_done_callback(self._compression_tasks.discard)

    async def _compress_session_background(self, session_key: str, message_count: int) -> None:
        lock = self._get_session_compression_lock(session_key)
        async with lock:
            if session_key in self._sessions_compressing:
                return
            self._sessions_compressing.add(session_key)
            try:
                session = self.sessions.get_or_create(session_key)
                result = await self._compress_session_for_new(session)
            finally:
                self._sessions_compressing.discard(session_key)
        if result is None:
            return
        session.metadata["last_compressed_count"] = message_count
        self.sessions.save(session)
        logger.info(
            "记忆压缩完成: "
            f"session={session_key}, created={result.created}, merged={result.merged}, skipped={result.skipped}"
        )

    async def _compress_session_for_new(self, session):
        if not session.messages:
            return None
        if self.memory_compressor is None:
            legacy = await self._consolidate_memory(session, archive_all=True)
            if not legacy or not legacy.get("success"):
                return None
            return CompressionResult(
                created=1 if legacy.get("memory_updated") else 0,
                merged=0,
                skipped=0,
                summary="legacy",
            )
        try:
            return await self.memory_compressor.compress(session.messages, session.key)
        except Exception as e:
            logger.error(f"记忆压缩失败: {e}")
            return None

    async def _consolidate_memory(self, session, archive_all: bool = False) -> dict | None:
        """
        Consolidate older messages into MEMORY.md and HISTORY.md, then trim session history.

        Returns:
            Result dict: {success, archived, memory_updated, history_added}
            Returns None when there are no messages to process.
        """
        if not session.messages:
            return None
        memory = MemoryStore(self.workspace)
        if archive_all:
            old_messages = session.messages
            keep_count = 0
        else:
            keep_count = min(10, max(2, self.memory_window // 2))
            old_messages = session.messages[:-keep_count]
        if not old_messages:
            return None
        archived_count = len(old_messages)
        logger.info(
            f"Memory consolidation start: total={len(session.messages)}, "
            f"archive={archived_count}, keep={keep_count}"
        )

        # Format conversation for LLM processing.
        lines = []
        for m in old_messages:
            if not m.get("content"):
                continue
            tools = f" [tools: {', '.join(m['tools_used'])}]" if m.get("tools_used") else ""
            lines.append(f"[{m.get('timestamp', '?')[:16]}] {m['role'].upper()}{tools}: {m['content']}")
        conversation = "\n".join(lines)
        current_memory = memory.read_long_term()

        prompt = f"""You are a memory consolidation agent. Process this conversation and return a JSON object with exactly two keys:

1. "history_entry": A paragraph (2-5 sentences) summarizing the key events/decisions/topics. Start with a timestamp like [YYYY-MM-DD HH:MM]. Include enough detail to be useful when found by grep search later.

2. "memory_update": The updated long-term memory content. Add any new facts: user location, preferences, personal info, habits, project context, technical decisions, tools/services used. If nothing new, return the existing content unchanged.

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{conversation}

Respond with ONLY valid JSON, no markdown fences."""

        try:
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": "You are a memory consolidation agent. Respond only with valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                tool_choice="auto",
                model=self.model,
            )
            result: dict
            if response.has_tool_calls and response.tool_calls:
                args = response.tool_calls[0].arguments
                if isinstance(args, dict):
                    result = args
                elif isinstance(args, str):
                    result = json.loads(args)
                else:
                    logger.warning(
                        "Unexpected memory consolidation tool arguments type: {}",
                        type(args).__name__,
                    )
                    result = {}
            else:
                text = (response.content or "").strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                result = json.loads(text)

            history_added = False
            memory_updated = False
            if entry := result.get("history_entry"):
                memory.append_history(entry)
                history_added = True
            if update := result.get("memory_update"):
                if update != current_memory:
                    memory.write_long_term(update)
                    memory_updated = True

            session.messages = session.messages[-keep_count:] if keep_count else []
            self.sessions.save(session)
            logger.info(
                f"Memory consolidation completed, session trimmed to {len(session.messages)} messages"
            )
            return {
                "success": True,
                "archived": archived_count,
                "memory_updated": memory_updated,
                "history_added": history_added,
            }
        except (json.JSONDecodeError, ValueError, OSError, RuntimeError) as e:
            logger.error(f"Memory consolidation failed: {e}")
            return {"success": False, "archived": archived_count, "error": str(e)}

    async def process_direct(
        self,
        content: str,
        session_key: str | None = None,
        channel: str = "cli",
        chat_id: str = "direct",
    ) -> str:
        """
        Process a message directly (for CLI or cron usage).

        Args:
            content: The message content.
            session_key: Session identifier.
            channel: Source channel (for context).
            chat_id: Source chat ID (for context).

        Returns:
            The agent's response.
        """
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content,
            metadata={"session_key": session_key} if session_key else {},
        )

        response = await self._process_message(msg)
        return response.content if response else ""



