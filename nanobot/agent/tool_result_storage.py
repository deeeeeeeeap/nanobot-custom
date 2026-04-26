"""Persist large tool results to workspace files and keep previews in context."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from nanobot.config.schema import ResultStorageConfig

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_TAG_OPEN = "<persisted-tool-result>"
_TAG_CLOSE = "</persisted-tool-result>"


@dataclass(frozen=True)
class StoredToolResult:
    """Result of tool-result storage."""

    content: str
    persisted: bool
    path: Path | None = None


def _safe_tool_id(raw: str) -> str:
    value = _SAFE_NAME_RE.sub("_", (raw or "").strip())[:80].strip("._-")
    return value or f"tool_{uuid.uuid4().hex[:10]}"


def _preview(content: str, max_chars: int) -> tuple[str, bool]:
    if len(content) <= max_chars:
        return content, False
    chunk = content[:max_chars]
    newline = chunk.rfind("\n")
    if newline > max_chars // 2:
        chunk = chunk[: newline + 1]
    return chunk, True


def _resolve_storage_dir(workspace: Path, config: ResultStorageConfig) -> Path:
    root = workspace.expanduser().resolve()
    target = (root / config.path).resolve()
    if target != root and root not in target.parents:
        raise ValueError("result storage path escaped workspace")
    return target


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def persist_tool_result_if_needed(
    *,
    content: str,
    tool_name: str,
    tool_call_id: str,
    workspace: Path,
    config: ResultStorageConfig,
    force: bool = False,
) -> StoredToolResult:
    """Persist oversized tool output and return a preview block for the LLM context."""

    if not config.enabled or (not force and len(content) <= config.threshold_chars):
        return StoredToolResult(content=content, persisted=False)

    storage_dir = _resolve_storage_dir(workspace, config)
    safe_id = _safe_tool_id(tool_call_id)
    safe_tool = _safe_tool_id(tool_name)
    path = storage_dir / f"{safe_id}_{safe_tool}_{uuid.uuid4().hex[:12]}.txt"
    _write_atomic(path, content)

    preview, has_more = _preview(content, config.preview_chars)
    rel = path.relative_to(workspace.expanduser().resolve()).as_posix()
    size_kb = len(content.encode("utf-8", errors="ignore")) / 1024
    more_line = "\n..." if has_more else ""
    block = (
        f"{_TAG_OPEN}\n"
        f"Tool result was large ({len(content):,} chars, {size_kb:.1f} KB).\n"
        f"Full output saved to workspace path: {rel}\n"
        "Use read_file with this path if more detail is needed.\n\n"
        f"Preview:\n{preview}{more_line}\n"
        f"{_TAG_CLOSE}"
    )
    return StoredToolResult(content=block, persisted=True, path=path)
