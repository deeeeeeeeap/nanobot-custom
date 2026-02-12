"""Agent loop: the core processing engine."""

import asyncio
import json
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.agent.context import ContextBuilder
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebSearchTool, WebFetchTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.memory_tool import MemoryTool
from nanobot.agent.subagent import SubagentManager
from nanobot.session.manager import SessionManager
# 定制：模型能力检测
from nanobot.config.model_capabilities import supports_function_calling
# 定制：幻觉检测
from nanobot.agent.hallucination_detector import (
    detect_hallucination, 
    create_honest_response,
    create_no_tools_available_response
)
# 定制：状态报告
from nanobot.agent.status import StatusMessage, StatusReporter, NullReporter


def _is_lazy_response(content: str, user_message: str = "") -> bool:
    """
    检测"懒惰回复"：模型声称将要执行操作但没有真正调用工具。
    
    判断标准：内容中同时包含"意图词"和"工具名称"。
    排除条件：如果用户的消息是规划/讨论请求，则不触发。
    """
    import re
    
    if not content or len(content) < 10:
        return False
    
    # 排除：用户在征求意见/讨论方案，模型不该被强制行动
    planning_patterns = [
        r"先.{0,4}(看看|试试|想想|聊聊|说说|分析|规划|讨论)",
        r"(能不能|可不可以|可以吗|行不行|怎么样|什么方案|怎么做)",
        r"(你觉得|你认为|你看|你说|有什么.*建议|有什么.*办法)",
        r"(能做吗|做得到吗|可行吗|靠谱吗)",
    ]
    if user_message and any(re.search(p, user_message) for p in planning_patterns):
        return False
    
    # 意图词：表示"将要做"但没做的措辞
    intent_patterns = [
        r"我(将|会|来|要|正在|准备|尝试)(使用|调用|执行|运行)",
        r"(让我|我来|我先|我去)(使用|调用|执行|运行|查|看|帮)",
        r"(立即|马上|现在)(使用|调用|执行|尝试)",
        r"我(将|会).*?(工具|命令|指令)",
    ]
    
    # 工具名称
    tool_names = ["exec", "cron", "weather", "web_search", "web_fetch", 
                  "message", "read_file", "write_file", "curl", "命令"]
    
    has_intent = any(re.search(p, content) for p in intent_patterns)
    has_tool_ref = any(t in content.lower() for t in tool_names)
    
    return has_intent and has_tool_ref


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
        brave_api_key: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        cron_service: "CronService | None" = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        reporter_factory: "Callable[[str, str], StatusReporter] | None" = None,
    ):
        from nanobot.config.schema import ExecToolConfig
        from nanobot.cron.service import CronService
        from typing import Callable
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self.reporter_factory = reporter_factory  # 定制：状态报告器工厂
        
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
        # 文件工具（如配置则限制为 workspace 目录）
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        self.tools.register(ReadFileTool(allowed_dir=allowed_dir))
        self.tools.register(WriteFileTool(allowed_dir=allowed_dir))
        self.tools.register(EditFileTool(allowed_dir=allowed_dir))
        self.tools.register(ListDirTool(allowed_dir=allowed_dir))
        
        # Shell 工具
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
        ))
        
        # Web 工具
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())
        
        # 消息工具
        message_tool = MessageTool(send_callback=self.bus.publish_outbound)
        self.tools.register(message_tool)
        
        # 子代理工具
        spawn_tool = SpawnTool(manager=self.subagents)
        self.tools.register(spawn_tool)
        
        # 记忆管理工具
        self.tools.register(MemoryTool(
            memory_store=self.context.memory,
            workspace=self.workspace,
        ))
        
        # 定时任务工具
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))
    
    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        logger.info("Agent loop started")
        
        while self._running:
            try:
                # 等待下一条消息
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=1.0
                )
                
                # 定制：创建状态报告器（如果有工厂）
                reporter = None
                if self.reporter_factory and msg.channel != "system":
                    try:
                        reporter = self.reporter_factory(msg.channel, msg.chat_id)
                    except Exception as e:
                        logger.warning(f"创建状态报告器失败: {e}")
                
                # 处理消息
                try:
                    response = await self._process_message(msg, reporter=reporter)
                    
                    # 定制：完成后清理状态消息
                    if reporter:
                        await reporter.finalize(delete_status=True)
                    
                    if response:
                        await self.bus.publish_outbound(response)
                except Exception as e:
                    logger.error(f"处理消息时出错: {e}")
                    # 出错时也要清理状态消息
                    if reporter:
                        await reporter.finalize(delete_status=True)
                    # 发送错误响应
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"抱歉，处理时遇到错误: {str(e)}"
                    ))
            except asyncio.TimeoutError:
                continue
    
    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")
    
    def _update_provider_env(self, model: str, api_key: str | None, api_base: str | None) -> None:
        """
        定制：更新 provider 的 API key 和环境变量。
        用于动态模型切换时更新 provider 配置。
        """
        import os
        
        if api_key:
            self.provider.api_key = api_key
            model_lower = model.lower()
            
            # 根据模型名称设置对应的环境变量
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
            
            # MiniMax 需要特殊处理（使用 Anthropic 兼容 API）
            if "minimax" in model_lower:
                os.environ["ANTHROPIC_API_KEY"] = api_key
                os.environ["ANTHROPIC_BASE_URL"] = api_base or "https://api.minimaxi.com/anthropic"
        
        # 更新 api_base（无论是否有值，都需要更新以确保切换模型时重置）
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
            reporter: 定制：可选的状态报告器，用于实时反馈进度
        
        Returns:
            The response message, or None if no response needed.
        """
        # 定制：使用空报告器如果没有提供
        if reporter is None:
            reporter = NullReporter()
        
        # 处理系统消息（子代理通知）
        if msg.channel == "system":
            return await self._process_system_message(msg)
        
        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info(f"Processing message from {msg.channel}:{msg.sender_id}: {preview}")
        
        # 定制：动态读取最新的模型配置，让 /model 命令切换立即生效
        current_model = self.model
        from nanobot.config.loader import load_config
        try:
            current_config = load_config()
            current_model = current_config.agents.defaults.model
            # 更新 provider 的 API key 和 base（如果模型变更需要不同的 provider）
            if current_model != self.model:
                logger.info(f"Model changed from {self.model} to {current_model}")
                self.model = current_model
                api_key = current_config.get_api_key(current_model)
                api_base = current_config.get_api_base(current_model)
                self._update_provider_env(current_model, api_key, api_base)
        except Exception as e:
            logger.warning(f"Failed to reload config: {e}, using cached model")
        
        # 获取或创建会话
        session = self.sessions.get_or_create(msg.session_key)
        
        # 更新工具上下文
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(msg.channel, msg.chat_id)
        
        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(msg.channel, msg.chat_id)
        
        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(msg.channel, msg.chat_id)
        
        # 构建消息上下文
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )
        
        # Agent 循环
        iteration = 0
        final_content = None
        tools_were_called = False  # 定制：追踪是否真正调用了工具
        
        # 定制：检查模型是否支持 Function Calling
        model_supports_tools = supports_function_calling(self.model)
        if not model_supports_tools:
            logger.warning(f"Model {self.model} does not support function calling, tools disabled")
        
        while iteration < self.max_iterations:
            iteration += 1
            
            # 定制：报告思考状态（Codex 模型使用特殊消息）
            is_codex = "codex" in current_model.lower()
            await reporter.report(StatusMessage.thinking(is_codex=is_codex))
            
            # 调用 LLM - 定制：根据模型能力决定是否传递 tools
            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions() if model_supports_tools else None,
                model=self.model
            )
            
            # 处理工具调用
            if response.has_tool_calls:
                # 添加 assistant 消息（含工具调用）
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
                
                # 执行工具
                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")
                    
                    # 定制：报告工具开始执行
                    await reporter.report(StatusMessage.tool_start(
                        tool_call.name, 
                        tool_call.arguments
                    ))
                    
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    
                    # 定制：报告工具执行完成
                    success = not (isinstance(result, str) and result.startswith("Error"))
                    await reporter.report(StatusMessage.tool_done(tool_call.name, success))
                    
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                    tools_were_called = True  # 标记工具被调用
            else:
                # 定制：懒惰检测 — 模型说了要做但没调用工具，自动催促重试
                if (
                    iteration == 1
                    and model_supports_tools
                    and response.content
                    and _is_lazy_response(response.content, msg.content)
                ):
                    logger.warning("检测到懒惰回复（说了要做但没调用工具），注入催促消息重试")
                    messages = self.context.add_assistant_message(messages, response.content)
                    messages = self.context.add_user_nudge(
                        messages,
                        "你刚才只是描述了你要做什么，但没有真正调用工具。"
                        "请立即调用对应工具执行，不要再描述了。"
                    )
                    continue  # 不 break，再给一轮机会
                
                # 无工具调用，完成
                final_content = response.content
                break
        
        if not final_content:
            final_content = "（任务已执行完毕，但模型未生成回复文本。）"
        
        # 定制：幻觉检测
        # 触发条件：
        # 1. 模型不支持工具调用（纯对话模式）
        # 2. 模型支持工具但本次没有调用任何工具（可能编造了执行结果）
        # 排除条件：Codex 模型（自带执行能力）、工具确实被调用过的情况
        is_codex_model = "codex" in current_model.lower()
        should_check_hallucination = (
            not is_codex_model
            and (
                not model_supports_tools  # 模型本身不支持工具
                or (model_supports_tools and not tools_were_called)  # 支持但没调用
            )
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
        
        # 日志：响应预览
        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info(f"Response to {msg.channel}:{msg.sender_id}: {preview}")
        
        # 保存会话
        session.add_message("user", msg.content)
        session.add_message("assistant", final_content)
        self.sessions.save(session)
        
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=msg.metadata or {},  # 传递频道特定元数据（如 Slack thread_ts）
        )
    
    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a system message (e.g., subagent announce).
        
        The chat_id field contains "original_channel:original_chat_id" to route
        the response back to the correct destination.
        """
        logger.info(f"Processing system message from {msg.sender_id}")
        
        # 解析来源（格式: "channel:chat_id"）
        if ":" in msg.chat_id:
            parts = msg.chat_id.split(":", 1)
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            origin_channel = "cli"
            origin_chat_id = msg.chat_id
        
        # 使用来源会话
        session_key = f"{origin_channel}:{origin_chat_id}"
        session = self.sessions.get_or_create(session_key)
        
        # 更新工具上下文
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(origin_channel, origin_chat_id)
        
        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(origin_channel, origin_chat_id)
        
        cron_tool = self.tools.get("cron")
        if isinstance(cron_tool, CronTool):
            cron_tool.set_context(origin_channel, origin_chat_id)
        
        # 构建消息上下文
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            channel=origin_channel,
            chat_id=origin_chat_id,
        )
        
        # Agent 循环（子代理消息处理）
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
        
        # 保存会话（标记为系统消息）
        session.add_message("user", f"[System: {msg.sender_id}] {msg.content}")
        session.add_message("assistant", final_content)
        self.sessions.save(session)
        
        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content
        )
    
    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
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
            content=content
        )
        
        response = await self._process_message(msg)
        return response.content if response else ""
