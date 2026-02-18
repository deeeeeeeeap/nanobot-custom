"""LLM-driven memory extraction and persistence."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger

from nanobot.memory.models import CATEGORY_DIRS, CandidateMemory, MemoryCategory
from nanobot.prompts import render_prompt
from nanobot.providers.base import LLMProvider
from nanobot.utils.helpers import ensure_dir


class MemoryExtractor:
    """Extract candidate memories from session messages and persist them."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        workspace: Path,
        model: str,
        output_language: str = "zh-CN",
    ):
        self._provider = provider
        self._workspace = Path(workspace).expanduser()
        self._model = model
        self._output_language = output_language
        self._memory_root = ensure_dir(self._workspace / "memory")

    async def extract(self, messages: list[dict[str, Any]], session_key: str) -> list[CandidateMemory]:
        """Extract candidate memories from conversation messages."""
        formatted = self._format_messages(messages)
        if not formatted.strip():
            return []

        prompt = render_prompt(
            "memory_extraction",
            {
                "messages": formatted,
                "session_key": session_key,
                "output_language": self._output_language,
            },
        )
        response = await self._provider.chat(
            messages=[
                {"role": "system", "content": "你是严格的 JSON 提取器。只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            model=self._model,
        )
        payload = self._parse_json_payload(response.content or "")
        candidates: list[CandidateMemory] = []
        for item in payload.get("memories", []):
            category = self._parse_category(item.get("category", "patterns"))
            abstract = str(item.get("abstract", "")).strip()
            overview = str(item.get("overview", "")).strip()
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            if not abstract:
                abstract = content.splitlines()[0][:100]
            candidates.append(
                CandidateMemory(
                    category=category,
                    abstract=abstract,
                    overview=overview or abstract,
                    content=content,
                    source_session=session_key,
                    language=self._output_language,
                )
            )
        return candidates

    async def create_memory(self, candidate: CandidateMemory, session_key: str) -> Path | None:
        """Persist a candidate memory into structured memory files."""
        if candidate.category == MemoryCategory.PROFILE:
            profile_path = self._memory_root / CATEGORY_DIRS[MemoryCategory.PROFILE]
            ensure_dir(profile_path.parent)
            existing = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
            merged = await self._merge_memory(existing, candidate.content, candidate.category.value)
            final_text = (merged or candidate.content).strip()
            profile_path.write_text(final_text + "\n", encoding="utf-8")
            return profile_path

        category_dir = self._memory_root / CATEGORY_DIRS[candidate.category]
        ensure_dir(category_dir)
        mem_path = category_dir / f"mem_{uuid4().hex}.md"
        mem_path.write_text(self._format_memory_file(candidate, session_key), encoding="utf-8")
        return mem_path

    async def merge_into_file(self, target: Path, candidate: CandidateMemory) -> bool:
        """Merge candidate content into an existing memory file."""
        if not target.exists():
            return False
        existing = target.read_text(encoding="utf-8")
        merged = await self._merge_memory(existing, candidate.content, candidate.category.value)
        final_text = (merged or f"{existing.rstrip()}\n\n{candidate.content.strip()}").strip()
        target.write_text(final_text + "\n", encoding="utf-8")
        return True

    async def _merge_memory(self, existing: str, new: str, category: str) -> str | None:
        if not existing.strip():
            return new.strip()
        prompt = render_prompt(
            "memory_merge",
            {
                "existing_content": existing,
                "new_content": new,
                "category": category,
                "output_language": self._output_language,
            },
        )
        try:
            response = await self._provider.chat(
                messages=[
                    {"role": "system", "content": "你是记忆合并器。输出纯文本。"},
                    {"role": "user", "content": prompt},
                ],
                model=self._model,
            )
            merged = (response.content or "").strip()
            return merged or None
        except Exception as e:  # pragma: no cover - provider/runtime defensive path
            logger.warning(f"Memory merge failed: {e}")
            return None

    @staticmethod
    def _format_messages(messages: list[dict[str, Any]]) -> str:
        lines = []
        for msg in messages:
            role = str(msg.get("role", "unknown")).upper()
            content = str(msg.get("content", "")).strip()
            if not content:
                continue
            lines.append(f"[{role}] {content}")
        return "\n".join(lines)

    @staticmethod
    def _parse_category(value: str) -> MemoryCategory:
        try:
            return MemoryCategory(str(value).strip().lower())
        except ValueError:
            return MemoryCategory.PATTERNS

    @staticmethod
    def _extract_json_text(text: str) -> str:
        raw = text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        if raw.startswith("{") and raw.endswith("}"):
            return raw
        match = re.search(r"\{.*\}", raw, flags=re.S)
        return match.group(0) if match else raw

    def _parse_json_payload(self, text: str) -> dict[str, Any]:
        json_text = self._extract_json_text(text)
        try:
            parsed = json.loads(json_text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            logger.warning("Memory extraction JSON parse failed; falling back to empty result")
        return {"memories": []}

    @staticmethod
    def _format_memory_file(candidate: CandidateMemory, session_key: str) -> str:
        return (
            f"{candidate.abstract.strip()}\n\n"
            f"## 概要\n{candidate.overview.strip()}\n\n"
            f"## 详情\n{candidate.content.strip()}\n\n"
            f"## 元数据\n"
            f"- 分类: {candidate.category.value}\n"
            f"- 来源会话: {session_key}\n"
            f"- 语言: {candidate.language}\n"
        )

