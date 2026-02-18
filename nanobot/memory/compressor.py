"""Session memory compression orchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.memory import MemoryStore
from nanobot.memory.deduplicator import MemoryDeduplicator
from nanobot.memory.extractor import MemoryExtractor
from nanobot.memory.models import CATEGORY_DIRS, CompressionResult, DedupDecision, MemoryCategory
from nanobot.prompts import render_prompt
from nanobot.providers.base import LLMProvider
from nanobot.search.indexer import Indexer


class SessionCompressor:
    """Execute memory extraction, deduplication, persistence and summary."""

    def __init__(
        self,
        *,
        extractor: MemoryExtractor,
        deduplicator: MemoryDeduplicator,
        memory_store: MemoryStore,
        provider: LLMProvider,
        model: str,
        indexer: Indexer | None = None,
        max_memories_per_category: int = 50,
        output_language: str = "zh-CN",
    ):
        self._extractor = extractor
        self._deduplicator = deduplicator
        self._memory = memory_store
        self._provider = provider
        self._model = model
        self._indexer = indexer
        self._max_memories_per_category = max_memories_per_category
        self._output_language = output_language

    async def compress(self, messages: list[dict[str, Any]], session_key: str) -> CompressionResult:
        """Compress a session into structured memories."""
        result = CompressionResult()
        if not messages:
            return result

        candidates = await self._extractor.extract(messages, session_key)
        for candidate in candidates:
            if candidate.category == MemoryCategory.PROFILE:
                existed = (self._memory.memory_dir / CATEGORY_DIRS[MemoryCategory.PROFILE]).exists()
                target = await self._extractor.create_memory(candidate, session_key)
                if target:
                    self._index_file(target)
                    if existed:
                        result.merged += 1
                    else:
                        result.created += 1
                continue

            dedup = await self._deduplicator.deduplicate(candidate)
            if dedup.decision == DedupDecision.SKIP:
                result.skipped += 1
                continue

            if dedup.decision == DedupDecision.MERGE and dedup.similar_memories:
                target = self._memory.workspace / dedup.similar_memories[0]
                ok = await self._extractor.merge_into_file(target, candidate)
                if ok:
                    self._index_file(target)
                    result.merged += 1
                else:
                    result.skipped += 1
                continue

            target = await self._extractor.create_memory(candidate, session_key)
            if target:
                self._index_file(target)
                result.created += 1
            else:
                result.skipped += 1

        self._enforce_limits()
        result.summary = await self._build_structured_summary(messages, session_key)
        if result.summary:
            self._memory.append_history(result.summary)
        return result

    def _index_file(self, path: Path) -> None:
        if self._indexer is None:
            return
        try:
            self._indexer.index_single(path, collection="memory")
        except Exception as e:  # pragma: no cover - non-critical index failure
            logger.warning(f"Memory indexing failed for {path}: {e}")

    async def _build_structured_summary(self, messages: list[dict[str, Any]], session_key: str) -> str:
        lines = []
        for msg in messages:
            role = str(msg.get("role", "unknown")).upper()
            content = str(msg.get("content", "")).strip()
            if content:
                lines.append(f"[{role}] {content}")
        prompt = render_prompt(
            "structured_summary",
            {
                "messages": "\n".join(lines),
                "session_key": session_key,
                "output_language": self._output_language,
            },
        )
        try:
            response = await self._provider.chat(
                messages=[
                    {"role": "system", "content": "你是会话摘要器。输出纯文本摘要。"},
                    {"role": "user", "content": prompt},
                ],
                model=self._model,
            )
            summary = (response.content or "").strip()
            return summary
        except Exception as e:  # pragma: no cover - provider/runtime fallback
            logger.warning(f"Structured summary generation failed: {e}")
            return ""

    def _enforce_limits(self) -> None:
        memory_root = self._memory.memory_dir / "memories"
        if not memory_root.exists():
            return
        for category in MemoryCategory:
            if category == MemoryCategory.PROFILE:
                continue
            cat_dir = memory_root / category.value
            if not cat_dir.exists():
                continue
            files = sorted((p for p in cat_dir.glob("*.md") if p.is_file()), key=lambda p: p.stat().st_mtime)
            if len(files) <= self._max_memories_per_category:
                continue
            overflow = len(files) - self._max_memories_per_category
            for old in files[:overflow]:
                old.unlink(missing_ok=True)
                self._index_file(old)

