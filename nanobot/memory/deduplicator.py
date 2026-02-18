"""Memory deduplication with search pre-filter and LLM decision."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.config.schema import SearchConfig
from nanobot.memory.models import CandidateMemory, DedupDecision, DedupResult
from nanobot.prompts import render_prompt
from nanobot.providers.base import LLMProvider
from nanobot.search.indexer import TextEmbedder
from nanobot.search.store import SearchResult, SearchStore


class MemoryDeduplicator:
    """Detect duplicate memories before persistence."""

    def __init__(
        self,
        *,
        store: SearchStore | None,
        provider: LLMProvider,
        model: str,
        search_config: SearchConfig | None,
        embedder: TextEmbedder | None = None,
        min_score: float = 0.2,
        output_language: str = "zh-CN",
    ):
        self._store = store
        self._provider = provider
        self._model = model
        self._search_config = search_config
        self._embedder = embedder
        self._min_score = min_score
        self._output_language = output_language

    async def deduplicate(self, candidate: CandidateMemory) -> DedupResult:
        """Return create/merge/skip decision for a memory candidate."""
        similar = self._find_similar_memories(candidate)
        if not similar:
            return DedupResult(
                decision=DedupDecision.CREATE,
                candidate=candidate,
                similar_memories=[],
                reason="No similar memories found",
            )

        decision, reason = await self._llm_decision(candidate, similar)
        return DedupResult(
            decision=decision,
            candidate=candidate,
            similar_memories=similar,
            reason=reason,
        )

    def _find_similar_memories(self, candidate: CandidateMemory) -> list[str]:
        if self._store is None:
            return []

        query = (candidate.abstract or "").strip() or (candidate.content or "").strip()
        if not query:
            return []

        results = self._store.search(query, limit=8, min_score=self._min_score, collection="memory")
        if (
            self._search_config
            and self._search_config.vector_enabled
            and self._embedder is not None
        ):
            try:
                vector_results = self._store.search_vector(
                    self._embedder.embed_query(query),
                    model=self._search_config.embedding_model,
                    limit=8,
                    min_score=self._min_score,
                    collection="memory",
                )
                results = self._merge_results(results, vector_results, limit=8)
            except Exception as e:  # pragma: no cover - optional runtime fallback
                logger.warning(f"Vector dedup pre-filter failed: {e}")

        category_fragment = f"/memories/{candidate.category.value}/"
        similar = [
            self._to_workspace_path(item)
            for item in results
            if category_fragment in f"/{item.display_path.strip('/')}/"
        ]
        return [path for path in similar if path]

    async def _llm_decision(self, candidate: CandidateMemory, similar: list[str]) -> tuple[DedupDecision, str]:
        payload = {
            "category": candidate.category.value,
            "abstract": candidate.abstract,
            "overview": candidate.overview,
            "content": candidate.content,
            "source_session": candidate.source_session,
        }
        prompt = render_prompt(
            "dedup_decision",
            {
                "candidate": json.dumps(payload, ensure_ascii=False, indent=2),
                "existing_memories": "\n".join(f"- {p}" for p in similar[:5]),
                "output_language": self._output_language,
            },
        )
        try:
            response = await self._provider.chat(
                messages=[
                    {"role": "system", "content": "你是去重决策器。只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                model=self._model,
            )
            data = self._parse_json(response.content or "")
            decision = DedupDecision(str(data.get("decision", "create")).lower())
            reason = str(data.get("reason", "")).strip()
            return decision, reason
        except Exception as e:
            logger.warning(f"LLM dedup decision failed, fallback CREATE: {e}")
            return DedupDecision.CREATE, "LLM unavailable"

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        raw = text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        if not (raw.startswith("{") and raw.endswith("}")):
            match = re.search(r"\{.*\}", raw, flags=re.S)
            raw = match.group(0) if match else raw
        return json.loads(raw)

    @staticmethod
    def _merge_results(
        bm25_results: list[SearchResult],
        vector_results: list[SearchResult],
        limit: int,
    ) -> list[SearchResult]:
        merged: dict[str, SearchResult] = {}
        for result in bm25_results + vector_results:
            current = merged.get(result.display_path)
            if current is None or result.score > current.score:
                merged[result.display_path] = result
        return sorted(merged.values(), key=lambda item: item.score, reverse=True)[:limit]

    @staticmethod
    def _to_workspace_path(result: SearchResult) -> str:
        path = result.display_path.strip().replace("\\", "/")
        if not path:
            return ""
        return str(Path(path))
