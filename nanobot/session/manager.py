"""Session management for conversation history."""

import base64
import json
import os
import threading
from contextlib import suppress
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
    "thinking_blocks",
)

_METADATA_KEYS = (
    "last_assistant_timestamp",
    "compaction_failure_streak",
    "microcompact_stats",
    "cost_tracker_state",
    "mode",
    "worker_summary",
)

FILE_MAX_MESSAGES = 2000


def _estimate_message_tokens(message: dict[str, Any]) -> int:
    """Cheap token estimate used only for history slicing."""
    return max(1, len(json.dumps(message, ensure_ascii=False, default=str)) // 4 + 4)


def _find_legal_message_start(messages: list[dict[str, Any]]) -> int:
    """Return an index that avoids leading orphan tool results."""
    for idx, message in enumerate(messages):
        if message.get("role") == "tool":
            continue
        return idx
    return len(messages)


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
    last_consolidated: int = 0
    
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
    
    def get_history(self, max_messages: int = 50, *, max_tokens: int = 0) -> list[dict[str, Any]]:
        """
        Get message history for LLM context.
        
        Args:
            max_messages: Maximum messages to return.
        
        Returns:
            List of messages in LLM format.
        """
        max_messages = max_messages if max_messages > 0 else 50
        visible = self.messages[self.last_consolidated :]
        recent = visible[-max_messages:] if len(visible) > max_messages else visible

        start = _find_legal_message_start(recent)
        if start:
            recent = recent[start:]
        
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

        if max_tokens > 0 and history:
            kept: list[dict[str, Any]] = []
            used = 0
            for item in reversed(history):
                tokens = _estimate_message_tokens(item)
                if kept and used + tokens > max_tokens:
                    break
                kept.append(item)
                used += tokens
            kept.reverse()

            first_user = next((i for i, item in enumerate(kept) if item.get("role") == "user"), None)
            if first_user is not None:
                kept = kept[first_user:]
            else:
                last_user = next(
                    (i for i in range(len(history) - 1, -1, -1) if history[i].get("role") == "user"),
                    None,
                )
                if last_user is not None:
                    kept = history[last_user:]

            start = _find_legal_message_start(kept)
            history = kept[start:] if start else kept
        return history
    
    def clear(self) -> None:
        """Clear all messages in the session."""
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()

    def set_metadata(self, **kwargs: Any) -> None:
        """Update persisted session metadata fields."""
        for key, value in kwargs.items():
            if key in _METADATA_KEYS:
                self.metadata[key] = value

    def retain_recent_legal_suffix(self, max_messages: int) -> tuple[list[dict[str, Any]], int]:
        """Keep a recent suffix without starting from an orphan tool result."""
        if max_messages <= 0:
            dropped = list(self.messages)
            already = min(self.last_consolidated, len(dropped))
            self.clear()
            return dropped, already
        if len(self.messages) <= max_messages:
            return [], 0

        original = list(self.messages)
        before_lc = self.last_consolidated
        retained = list(self.messages[-max_messages:])

        first_user = next((i for i, item in enumerate(retained) if item.get("role") == "user"), None)
        if first_user is not None:
            retained = retained[first_user:]

        start = _find_legal_message_start(retained)
        if start:
            retained = retained[start:]

        if len(retained) > max_messages:
            retained = retained[-max_messages:]
            start = _find_legal_message_start(retained)
            if start:
                retained = retained[start:]

        retained_ids = {id(item) for item in retained}
        dropped = [item for item in original if id(item) not in retained_ids]
        already_consolidated = sum(
            1 for idx, item in enumerate(original)
            if idx < before_lc and id(item) not in retained_ids
        )
        self.last_consolidated = sum(
            1 for idx, item in enumerate(original)
            if idx < before_lc and id(item) in retained_ids
        )
        self.messages = retained
        self.updated_at = datetime.now()
        return dropped, already_consolidated

    def enforce_file_cap(self, on_archive: Any = None, limit: int = FILE_MAX_MESSAGES) -> None:
        """Bound the in-memory session before saving."""
        if limit <= 0 or len(self.messages) <= limit:
            return
        dropped, already_consolidated = self.retain_recent_legal_suffix(limit)
        archive_chunk = dropped[already_consolidated:]
        if archive_chunk and on_archive is not None:
            on_archive(archive_chunk)
        logger.info(
            "Session file cap hit for {}: dropped {}, raw-archived {}, kept {}",
            self.key,
            len(dropped),
            len(archive_chunk),
            len(self.messages),
        )


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
                messages: list[dict[str, Any]] = []
                metadata: dict[str, Any] = {}
                created_at = None
                updated_at = None
                stored_key: str | None = None
                last_consolidated = 0

                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue

                        data = json.loads(line)

                        if data.get("_type") == "metadata":
                            raw_metadata = data.get("metadata", {})
                            metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
                            created_at = (
                                datetime.fromisoformat(data["created_at"])
                                if data.get("created_at")
                                else None
                            )
                            updated_at = (
                                datetime.fromisoformat(data["updated_at"])
                                if data.get("updated_at")
                                else None
                            )
                            key_value = data.get("key")
                            if isinstance(key_value, str) and key_value:
                                stored_key = key_value
                            raw_last = data.get("last_consolidated", 0)
                            if isinstance(raw_last, int) and raw_last >= 0:
                                last_consolidated = raw_last
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
                    updated_at=updated_at or created_at or datetime.now(),
                    metadata=metadata,
                    last_consolidated=min(last_consolidated, len(messages)),
                )
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
                logger.warning(f"Failed to load session {key} from {path}: {e}")
                repaired = self._repair_path(path, key)
                if repaired is not None:
                    logger.info(
                        "Recovered session {} from corrupt file ({} messages)",
                        key,
                        len(repaired.messages),
                    )
                    return repaired
            except OSError as e:
                logger.warning(f"Failed to load session {key} from {path}: {e}")

        return None

    def _repair_path(self, path: Path, key: str) -> Session | None:
        """Recover valid JSONL records from a damaged session file."""
        if not path.exists():
            return None

        messages: list[dict[str, Any]] = []
        metadata: dict[str, Any] = {}
        created_at: datetime | None = None
        updated_at: datetime | None = None
        stored_key: str | None = None
        last_consolidated = 0
        skipped = 0

        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        skipped += 1
                        continue

                    if data.get("_type") == "metadata":
                        raw_metadata = data.get("metadata", {})
                        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
                        with suppress(ValueError, TypeError):
                            created_at = (
                                datetime.fromisoformat(data["created_at"])
                                if data.get("created_at")
                                else None
                            )
                        with suppress(ValueError, TypeError):
                            updated_at = (
                                datetime.fromisoformat(data["updated_at"])
                                if data.get("updated_at")
                                else None
                            )
                        key_value = data.get("key")
                        if isinstance(key_value, str) and key_value:
                            stored_key = key_value
                        raw_last = data.get("last_consolidated", 0)
                        if isinstance(raw_last, int) and raw_last >= 0:
                            last_consolidated = raw_last
                    else:
                        messages.append(data)
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Session repair failed for {}: {}", path, e)
            return None

        if skipped:
            logger.warning("Skipped {} corrupt line(s) while repairing {}", skipped, path)
        if not messages and not metadata:
            return None
        effective_key = stored_key or key
        if stored_key and stored_key != key:
            logger.warning(
                "Session key mismatch while repairing {}: requested={}, stored={}",
                path,
                key,
                stored_key,
            )
            return None
        return Session(
            key=effective_key,
            messages=messages,
            created_at=created_at or datetime.now(),
            updated_at=updated_at or created_at or datetime.now(),
            metadata=metadata,
            last_consolidated=min(last_consolidated, len(messages)),
        )

    def save(self, session: Session, *, fsync: bool = False) -> None:
        """Save a session to disk."""
        path = self._get_session_path(session.key)
        tmp_path = path.with_suffix(path.suffix + ".tmp")

        with self._lock:
            try:
                session.enforce_file_cap()
                metadata_line = {
                    "_type": "metadata",
                    "key": session.key,
                    "created_at": session.created_at.isoformat(),
                    "updated_at": session.updated_at.isoformat(),
                    "metadata": session.metadata,
                    "last_consolidated": session.last_consolidated,
                }
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(json.dumps(metadata_line, ensure_ascii=False))
                    f.write("\n")
                    for msg in session.messages:
                        f.write(json.dumps(msg, ensure_ascii=False))
                        f.write("\n")
                    if fsync:
                        f.flush()
                        os.fsync(f.fileno())
                os.replace(tmp_path, path)
                if fsync and os.name != "nt":
                    fd = os.open(str(path.parent), os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                self._cache[session.key] = session
            except OSError:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
                raise

    def flush_all(self) -> int:
        """Durably save all cached sessions during shutdown."""
        flushed = 0
        for key, session in list(self._cache.items()):
            try:
                self.save(session, fsync=True)
                flushed += 1
            except OSError as e:
                logger.warning("Failed to flush session {}: {}", key, e)
        return flushed
    
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

    def read_session_file(self, key: str) -> dict[str, Any] | None:
        """Read a session from disk without populating the in-memory cache."""
        path = self._get_session_path(key)
        if not path.exists():
            legacy_path = self._get_legacy_session_path(key)
            path = legacy_path if legacy_path.exists() else path
        if not path.exists():
            return None

        session = self._repair_path(path, key)
        if session is None:
            return None
        return {
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata,
            "last_consolidated": session.last_consolidated,
            "messages": session.messages,
        }

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
                        else:
                            fallback_key = self._decode_session_stem(path.stem) or path.stem.replace("_", ":")
                            repaired = self._repair_path(path, fallback_key)
                            if repaired is not None:
                                sessions_by_key[repaired.key] = {
                                    "key": repaired.key,
                                    "created_at": repaired.created_at.isoformat(),
                                    "updated_at": repaired.updated_at.isoformat(),
                                    "path": str(path),
                                }
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                fallback_key = self._decode_session_stem(path.stem) or path.stem.replace("_", ":")
                repaired = self._repair_path(path, fallback_key)
                if repaired is None:
                    continue
                sessions_by_key[repaired.key] = {
                    "key": repaired.key,
                    "created_at": repaired.created_at.isoformat(),
                    "updated_at": repaired.updated_at.isoformat(),
                    "path": str(path),
                }

        return sorted(
            sessions_by_key.values(),
            key=lambda x: x.get("updated_at", ""),
            reverse=True,
        )
