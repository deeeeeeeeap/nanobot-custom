"""Message tool for sending messages to users."""

import re
from typing import Any, Callable, Awaitable

from nanobot.agent.tools.base import Tool
from nanobot.bus.events import OutboundMessage


class MessageTool(Tool):
    """Tool to send messages to users on chat channels."""

    MAX_CONTENT_LEN = 8000
    _CHANNEL_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
    _CHAT_ID_RE = re.compile(r"^[A-Za-z0-9:_-]{1,128}$")
    
    def __init__(
        self, 
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = ""
    ):
        self._send_callback = send_callback
        self._default_channel = default_channel
        self._default_chat_id = default_chat_id
    
    def set_context(self, channel: str, chat_id: str) -> None:
        """Set the current message context."""
        self._default_channel = channel
        self._default_chat_id = chat_id
    
    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback
    
    @property
    def name(self) -> str:
        return "message"
    
    @property
    def description(self) -> str:
        return "Send a message to the user. Use this when you want to communicate something."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The message content to send"
                },
                "channel": {
                    "type": "string",
                    "description": "Optional: target channel (telegram, discord, etc.)"
                },
                "chat_id": {
                    "type": "string",
                    "description": "Optional: target chat/user ID"
                }
            },
            "required": ["content"]
        }

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors = super().validate_params(params)
        channel = params.get("channel")
        chat_id = params.get("chat_id")
        content = params.get("content", "")

        if isinstance(content, str) and len(content) > self.MAX_CONTENT_LEN:
            errors.append(f"content must be at most {self.MAX_CONTENT_LEN} chars")
        if channel is not None and isinstance(channel, str):
            if not self._CHANNEL_RE.match(channel):
                errors.append("channel should match ^[a-z][a-z0-9_]{1,31}$")
        if chat_id is not None and isinstance(chat_id, str):
            if not self._CHAT_ID_RE.match(chat_id):
                errors.append("chat_id contains invalid characters")
        return errors
    
    async def execute(
        self, 
        content: str, 
        channel: str | None = None, 
        chat_id: str | None = None,
        **kwargs: Any
    ) -> str:
        channel = channel or self._default_channel
        chat_id = chat_id or self._default_chat_id
        
        if not channel or not chat_id:
            return "Error: No target channel/chat specified"
        
        if not self._send_callback:
            return "Error: Message sending not configured"
        
        msg = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content
        )
        
        try:
            await self._send_callback(msg)
            return f"Message sent to {channel}:{chat_id}"
        except (TypeError, ValueError, RuntimeError) as e:
            return f"Error sending message: {str(e)}"
