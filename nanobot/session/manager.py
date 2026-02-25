"""Session management for conversation history."""

import base64
import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.utils.helpers import ensure_dir, safe_filename

_HISTORY_KEYS = (
    "role",
    "content",
    "tool_calls",
    "tool_call_id",
    "name",
    "reasoning_content",
)


@dataclass
class Session:
    """
    A conversation session.
    
    Stores messages in JSONL format for easy reading and persistence.
    """
    
    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()
    
    def get_history(self, max_messages: int = 50) -> list[dict[str, Any]]:
        """
        Get message history for LLM context.
        
        Args:
            max_messages: Maximum messages to return.
        
        Returns:
            List of messages in LLM format.
        """
        # Get recent messages
        recent = self.messages[-max_messages:] if len(self.messages) > max_messages else self.messages
        
        history: list[dict[str, Any]] = []
        for msg in recent:
            item: dict[str, Any] = {}
            for key in _HISTORY_KEYS:
                value = msg.get(key)
                if value is not None:
                    item[key] = value

            # Keep compatibility with older entries.
            if "role" not in item:
                continue
            item.setdefault("content", "")
            history.append(item)
        return history
    
    def clear(self) -> None:
        """Clear all messages in the session."""
        self.messages = []
        self.updated_at = datetime.now()


class SessionManager:
    """
    Manages conversation sessions.
    
    Sessions are stored as JSONL files in the sessions directory.
    """
    
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(Path.home() / ".nanobot" / "sessions")
        self._cache: dict[str, Session] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _encode_session_key(key: str) -> str:
        """Encode session key into a collision-resistant filename stem."""
        encoded = base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii").rstrip("=")
        return f"k_{encoded}"

    @staticmethod
    def _decode_session_stem(stem: str) -> str | None:
        """Decode encoded filename stem back to session key."""
        if not stem.startswith("k_"):
            return None
        payload = stem[2:]
        if not payload:
            return None
        payload += "=" * (-len(payload) % 4)
        try:
            return base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        safe_key = safe_filename(self._encode_session_key(key))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def _get_legacy_session_path(self, key: str) -> Path:
        """Get the legacy file path for backward compatibility."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.
        
        Args:
            key: Session key (usually channel:chat_id).
        
        Returns:
            The session.
        """
        # Check cache
        if key in self._cache:
            return self._cache[key]
        
        # Try to load from disk
        session = self._load(key)
        if session is None:
            session = Session(key=key)
        
        self._cache[key] = session
        return session
    
    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
        candidates = [self._get_session_path(key), self._get_legacy_session_path(key)]
        seen: set[Path] = set()
        for path in candidates:
            if path in seen or not path.exists():
                continue
            seen.add(path)

            try:
                messages = []
                metadata = {}
                created_at = None
                stored_key: str | None = None

                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue

                        data = json.loads(line)

                        if data.get("_type") == "metadata":
                            metadata = data.get("metadata", {})
                            created_at = (
                                datetime.fromisoformat(data["created_at"])
                                if data.get("created_at")
                                else None
                            )
                            key_value = data.get("key")
                            if isinstance(key_value, str) and key_value:
                                stored_key = key_value
                        else:
                            messages.append(data)

                effective_key = stored_key or key
                if stored_key and stored_key != key:
                    logger.warning(
                        "Session key mismatch while loading {}: requested={}, stored={}",
                        path,
                        key,
                        stored_key,
                    )
                    continue

                return Session(
                    key=effective_key,
                    messages=messages,
                    created_at=created_at or datetime.now(),
                    metadata=metadata,
                )
            except Exception as e:
                logger.warning(f"Failed to load session {key} from {path}: {e}")

        return None

    def save(self, session: Session) -> None:
        """Save a session to disk."""
        path = self._get_session_path(session.key)
        tmp_path = path.with_suffix(path.suffix + ".tmp")

        metadata_line = {
            "_type": "metadata",
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata,
        }
        lines = [json.dumps(metadata_line)]
        lines.extend(json.dumps(msg) for msg in session.messages)
        payload = "\n".join(lines) + "\n"

        with self._lock:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, path)
                self._cache[session.key] = session
            except Exception:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
                raise
    
    def delete(self, key: str) -> bool:
        """
        Delete a session.
        
        Args:
            key: Session key.
        
        Returns:
            True if deleted, False if not found.
        """
        # Remove from cache
        self._cache.pop(key, None)
        
        # Remove file
        removed = False
        for path in (self._get_session_path(key), self._get_legacy_session_path(key)):
            if path.exists():
                path.unlink()
                removed = True
        return removed

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.
        
        Returns:
            List of session info dicts.
        """
        sessions_by_key: dict[str, dict[str, Any]] = {}

        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                # Read just the metadata line
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            key = data.get("key")
                            if not isinstance(key, str) or not key:
                                key = self._decode_session_stem(path.stem) or path.stem.replace("_", ":")

                            entry = {
                                "key": key,
                                "created_at": data.get("created_at"),
                                "updated_at": data.get("updated_at"),
                                "path": str(path),
                            }
                            existing = sessions_by_key.get(key)
                            if existing is None or str(existing.get("updated_at", "")) < str(
                                entry.get("updated_at", "")
                            ):
                                sessions_by_key[key] = entry
            except Exception:
                continue

        return sorted(
            sessions_by_key.values(),
            key=lambda x: x.get("updated_at", ""),
            reverse=True,
        )
