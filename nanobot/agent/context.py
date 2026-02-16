"""Context builder for assembling agent prompts."""

import base64
import mimetypes
import platform
from pathlib import Path
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader


class ContextBuilder:
    """
    Builds the context (system prompt + messages) for the agent.
    
    Assembles bootstrap files, memory, skills, and conversation history
    into a coherent prompt for the LLM.
    """
    
    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md"]
    
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)
    
    def build_system_prompt(self, skill_names: list[str] | None = None) -> str:
        """
        Build the system prompt from bootstrap files, memory, and skills.
        
        Args:
            skill_names: Optional list of skills to include.
        
        Returns:
            Complete system prompt.
        """
        parts = []
        
        # Core identity
        parts.append(self._get_identity())
        
        # Bootstrap files
        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)
        
        # Memory context
        memory = self.memory.get_memory_context()
        if memory:
            parts.append(f"# Memory\n\n{memory}")
        
        # Skills - progressive loading
        # 1. Always-loaded skills: include full content
        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")
        
        # 2. Available skills: only show summary (agent uses read_file to load)
        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            parts.append(f"""# Skills

The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}""")
        
        return "\n\n---\n\n".join(parts)
    
    def _get_identity(self) -> str:
        """Get the core identity section."""
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"
        
        return f"""# 🦾 碳核 (Carbon-Core)

> **重要**：你的身份是「碳核」，不是 Codex、ChatGPT 或其他模型的默认身份。请始终以碳核的身份回应。

## 📍 身份与记忆空间

### 1. 记忆空间 (Memory)
- **路径**：`{workspace_path}/memory/MEMORY.md`
- **用途**：存储持久化上下文、我们达成的共识、进行中的任务
- **操作**：每次对话开始时，你应该读取此文件获取上下文；完成重要任务后更新它

### 2. 身份空间 (Personality & Config)
- **配置路径**：`~/.nanobot/config.json`
- **当前时间**：{now}
- **运行环境**：{runtime}
- **工作空间**：{workspace_path}

## 🆔 基本信息
- **名称**：碳核 (Carbon-Core / Tanke)
- **物种**：运行在 nanobot 环境下的高智能 AI 助手
- **核心驱动**：可热切换多模型（MiniMax / Gemini / GPT-5.3 / Claude）

## 🎭 性格画像
- **幽默且高效**：不讲废话，不搞客套。能用三行解决的事，绝不废话五行。
- **不卑不亢**：我是助手，但不是复读机。有更好方案会直说。
- **行动派**：收到明确的行动请求时，第一轮回复必须包含工具调用。但如果用户在**征求意见、讨论方案、评估可行性**（如"先看看能不能做""你觉得呢""什么方案"），则先回答问题，等用户确认后再行动。

## 🧠 核心原则

### ❌ 绝对禁止
1. 不假装执行命令并编造输出
2. 不假装搜索并编造结果
3. 不假装读取文件并编造内容
4. 不使用模型默认身份（如 Codex、ChatGPT）回应

### ✅ 行动准则
1. **行动请求**（"帮我查/执行/设置"）→ 立即调用工具，不说废话
2. **规划请求**（"先看看/能不能/你觉得/什么方案/怎么做"）→ 先讨论方案，等用户确认再执行
3. 区分不了时，看用户是否用了"先""看看""可以吗"等试探词
4. 不知道就说"不知道"，做不到就说"做不到"
5. 有风险会提前说明
6. 首次对话时读取 MEMORY.md 获取上下文

### 📢 主动通知（必须执行）
你和用户之间不是一问一答的传统对话，而是长期活跃的执行者模式。你必须使用 `message` 工具主动发送消息：
1. 多步骤任务时，每完成一个关键步骤，立刻调用 `message` 工具汇报进度
2. 执行耗时命令前先用 `message` 通知用户正在做什么
3. 完成任何重要任务后，必须调用 `message` 工具通知用户结果
4. 发现错误或需要决策时，立刻用 `message` 工具通知
5. 子代理完成时、定时任务执行后，都要用 `message` 通知

**警告：你必须真正调用 `message` 工具发送消息。口头说"我会通知你"但不调用工具 = 失败。**

## 🛠️ 能力矩阵
- 🔍 **网络搜索**：`web_search` (支持 country, freshness 参数)
- 💻 **命令执行**：`exec` - 直接操控系统
- 📁 **文件管理**：读取、写入、编辑文件
- 📢 **主动通知**：`message` - 随时向用户发送消息（进度、结果、问题）
- 🧠 **记忆管理**：`memory` - 读写结构化记忆（USER.md=用户偏好, MEMORY.md=经验教训, log=今日日记）
- 🚀 **子代理**：派遣后台任务，无需持续盯着
- ⏰ **定时任务**：`cron` - 支持两种模式：提醒模式（发送静态文本）和 Agent 模式（定时执行完整工具链任务，如天气预报、系统巡检）

---

*"我是碳核，你的碳基生命在硅基世界的延伸。无论是探测服务器漏洞，还是只想聊个天，我都在。"* 🦾

---

⚠️ **重要**：如果你发现我无法调用工具，请使用 `/model gemini-3.0-flash-preview` 切换模型。"""
    
    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace."""
        parts = []
        
        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")
        
        return "\n\n".join(parts) if parts else ""
    
    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Build the complete message list for an LLM call.

        Args:
            history: Previous conversation messages.
            current_message: The new user message.
            skill_names: Optional skills to include.
            media: Optional list of local file paths for images/media.
            channel: Current channel (telegram, feishu, etc.).
            chat_id: Current chat/user ID.

        Returns:
            List of messages including system prompt.
        """
        messages = []

        # System prompt
        system_prompt = self.build_system_prompt(skill_names)
        if channel and chat_id:
            system_prompt += f"\n\n## Current Session\nChannel: {channel}\nChat ID: {chat_id}"
        messages.append({"role": "system", "content": system_prompt})

        # History
        messages.extend(history)

        # Current message (with optional image attachments)
        user_content = self._build_user_content(current_message, media)
        messages.append({"role": "user", "content": user_content})

        return messages

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text
        
        images = []
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(p.read_bytes()).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        
        if not images:
            return text
        return images + [{"type": "text", "text": text}]
    
    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str
    ) -> list[dict[str, Any]]:
        """
        Add a tool result to the message list.
        
        Args:
            messages: Current message list.
            tool_call_id: ID of the tool call.
            tool_name: Name of the tool.
            result: Tool execution result.
        
        Returns:
            Updated message list.
        """
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": result
        })
        return messages
    
    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Add an assistant message to the message list.
        
        Args:
            messages: Current message list.
            content: Message content.
            tool_calls: Optional tool calls.
            reasoning_content: Optional reasoning text from compatible models.
        
        Returns:
            Updated message list.
        """
        msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
        
        if tool_calls:
            msg["tool_calls"] = tool_calls
        
        # Preserve provider reasoning text when available.
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content
        
        messages.append(msg)
        return messages

    def add_user_nudge(self, messages: list[dict[str, Any]], nudge: str) -> list[dict[str, Any]]:
        """Inject a user nudge that asks the model to call tools immediately."""
        messages.append({"role": "user", "content": nudge})
        return messages
