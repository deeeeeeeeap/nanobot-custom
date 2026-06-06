"""Persist large tool results to workspace files and keep previews in context."""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

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


def _result_files(storage_dir: Path) -> list[Path]:
    return [path for path in storage_dir.glob("*.txt") if path.is_file()]


def _cleanup_storage_dir(storage_dir: Path, config: ResultStorageConfig, keep_path: Path) -> None:
    """Bound result-storage growth by age, file count, and total bytes."""

    now = time.time()
    max_age_seconds = config.max_age_days * 24 * 60 * 60
    files: list[Path] = []

    for path in _result_files(storage_dir):
        try:
            stat = path.stat()
        except OSError as exc:
            logger.warning("Could not stat tool result file {}: {}", path, exc)
            continue

        if path != keep_path and now - stat.st_mtime > max_age_seconds:
            try:
                path.unlink()
            except OSError as exc:
                logger.warning("Could not remove expired tool result file {}: {}", path, exc)
            continue
        files.append(path)

    def _stat_key(path: Path) -> tuple[float, int]:
        try:
            stat = path.stat()
            return stat.st_mtime, stat.st_size
        except OSError:
            return 0.0, 0

    files = [path for path in files if path.exists()]
    files.sort(key=_stat_key, reverse=True)

    kept: list[Path] = []
    total_bytes = 0
    for path in files:
        try:
            size = path.stat().st_size
        except OSError as exc:
            logger.warning("Could not stat tool result file {}: {}", path, exc)
            continue

        over_count = len(kept) >= config.max_files
        over_bytes = total_bytes + size > config.max_bytes
        if path != keep_path and (over_count or over_bytes):
            try:
                path.unlink()
            except OSError as exc:
                logger.warning("Could not remove old tool result file {}: {}", path, exc)
            continue
        kept.append(path)
        total_bytes += size


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
    _cleanup_storage_dir(storage_dir, config, keep_path=path)

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
