"""Agent loop: the core processing engine."""
from __future__ import annotations

import asyncio
import json
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
        max_iterations: int = 20,
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
    ):
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.memory_window = memory_window
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.search_config = search_config
        self.memory_config = memory_config or MemoryConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self.reporter_factory = reporter_factory  # Optional status reporter factory.
        self.idle_intervention = idle_intervention
        self.search_store = None
        self.search_indexer = None
        self.search_embedder = None
        self.memory_compressor: SessionCompressor | None = None
        self._compression_tasks: set[asyncio.Task] = set()

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
        """Update provider API key/base and sync provider-specific env vars after model changes."""
        import os

        if api_key:
            self.provider.api_key = api_key
            model_lower = model.lower()

            env_mapping = {
                "gemini": "GEMINI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
                "claude": "ANTHROPIC_API_KEY",
                "openai": "OPENAI_API_KEY",
                "gpt": "OPENAI_API_KEY",
                "deepseek": "DEEPSEEK_API_KEY",
                "groq": "GROQ_API_KEY",
            }

            for keyword, env_var in env_mapping.items():
                if keyword in model_lower:
                    os.environ[env_var] = api_key
                    logger.debug(f"Set {env_var} for model {model}")
                    break

            if "minimax" in model_lower:
                os.environ["ANTHROPIC_API_KEY"] = api_key
                os.environ["ANTHROPIC_BASE_URL"] = api_base or "https://api.minimaxi.com/anthropic"

        # Always update api_base so model switching resets provider endpoint correctly.
        self.provider.api_base = api_base

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
        try:
            current_config = load_config()
            current_model = current_config.agents.defaults.model
            if current_model != self.model:
                logger.info(f"Model changed from {self.model} to {current_model}")
                self.model = current_model
                api_key = current_config.get_api_key(current_model)
                api_base = current_config.get_api_base(current_model)
                self._update_provider_env(current_model, api_key, api_base)
                self._setup_memory_compressor()
        except (ConfigError, ValueError, OSError) as e:
            logger.warning(f"Failed to reload config: {e}, using cached model")

        override_session_key = msg.metadata.get("session_key") if msg.metadata else None
        effective_session_key = override_session_key or msg.session_key
        session = self.sessions.get_or_create(effective_session_key)

        # Handle slash commands.
        raw_cmd = msg.content.strip()
        cmd = raw_cmd.lower()
        if cmd == "/new":
            msg_count = len(session.messages)
            result = await self._compress_session_for_new(session)
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

        # Main agent loop.
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        tools_were_called = False  # Track whether any tool was actually called.
        idle_retry_used = False
        forced_idle_stop = False
        execution_intent = _is_execution_intent(msg.content)

        # Check whether the current model supports function-calling.
        model_supports_tools = supports_function_calling(self.model)
        if not model_supports_tools:
            logger.warning(f"Model {self.model} does not support function calling, tools disabled")

        while iteration < self.max_iterations:
            iteration += 1

            is_codex = "codex" in current_model.lower()
            await reporter.report(StatusMessage.thinking(is_codex=is_codex))

            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions() if model_supports_tools else None,
                model=self.model
            )

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

                # Execute tool call.
                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")

                    await reporter.report(StatusMessage.tool_start(
                        tool_call.name,
                        tool_call.arguments
                    ))

                    result = await self.tools.execute(tool_call.name, tool_call.arguments)

                    success = not (isinstance(result, str) and result.startswith("Error"))
                    await reporter.report(StatusMessage.tool_done(tool_call.name, success))

                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                    tools_used.append(tool_call.name)
                    tools_were_called = True
                messages.append({"role": "user", "content": "Reflect on the results and decide next steps."})
            else:
                intervene = (
                    self.idle_intervention
                    and model_supports_tools
                    and execution_intent
                    and response.content
                    and not tools_were_called
                )
                if intervene:
                    if idle_retry_used:
                        logger.warning(
                            "Idle intervention level=2 (stop): still no tool calls after one forced retry"
                        )
                        forced_idle_stop = True
                        final_content = (
                            "⚠️ 检测到你当前请求是执行型任务，但模型连续未调用任何工具。\n"
                            "本次已停止空转以节省成本。\n\n"
                            "请直接发送可执行指令（例如：先 read_file/list_dir，再执行下一步），"
                            "我会立即执行并只回传实测结果。"
                        )
                        break

                    lazy = _is_lazy_response(response.content, msg.content)
                    hallucination = detect_hallucination(
                        response.content,
                        tools_were_called=False,
                        model_supports_tools=True,
                    )
                    should_intervene = lazy or hallucination.is_hallucination
                    if should_intervene and not idle_retry_used and iteration < self.max_iterations:
                        logger.warning(
                            "Idle intervention level=1 (retry once): lazy={}, hallucination={}, pattern={}",
                            lazy,
                            hallucination.is_hallucination,
                            hallucination.pattern_name or "n/a",
                        )
                        messages = self.context.add_assistant_message(messages, response.content)
                        nudge = (
                            "[System directive] Execution request detected. You must call tools in your next reply "
                            "(for example: read_file/list_dir/exec) before any summary. "
                            "Do not ask for confirmation, do not restate plans."
                        )
                        messages = self.context.add_user_nudge(messages, nudge)
                        idle_retry_used = True
                        continue

                # No tool call; use model content as final output.
                final_content = response.content
                break

        if not final_content:
            final_content = "锛堜换鍔″凡鎵ц瀹屾瘯锛屼絾妯″瀷鏈敓鎴愬洖澶嶆枃鏈€傦級"

        # Run hallucination checks when tools are unavailable or not used.
        should_check_hallucination = (
            not model_supports_tools
            or (model_supports_tools and not tools_were_called)  # model supports tools, but none were called
        )
        if should_check_hallucination and not forced_idle_stop:
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
        session.add_message("user", msg.content)
        session.add_message("assistant", final_content,
                            tools_used=tools_used if tools_used else None)
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

        iteration = 0
        final_content = None

        while iteration < self.max_iterations:
            iteration += 1

            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model
            )

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

                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
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
            f"- 记忆自动压缩: {'开启' if self.memory_config.auto_compress else '关闭'}"
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
        session = self.sessions.get_or_create(session_key)
        result = await self._compress_session_for_new(session)
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
                model=self.model,
            )
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



