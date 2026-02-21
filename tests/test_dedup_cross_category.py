from typing import Any

from nanobot.config.schema import SearchConfig
from nanobot.memory.deduplicator import MemoryDeduplicator
from nanobot.memory.models import CandidateMemory, DedupDecision, MemoryCategory
from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.search.store import SearchStore


class _Provider(LLMProvider):
    def __init__(self, decision: str = "create"):
        super().__init__(None, None)
        self.decision = decision

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        return LLMResponse(content=f'{{"decision":"{self.decision}","reason":"ok"}}')

    def get_default_model(self) -> str:
        return "test/model"


async def test_dedup_merges_high_score_cross_category(tmp_path) -> None:
    store = SearchStore(tmp_path / "index.sqlite")
    try:
        store.index_file(
            collection="memory",
            path="memory/memories/entities/mem_1.md",
            content="server region shanghai",
        )
        dedup = MemoryDeduplicator(
            store=store,
            provider=_Provider("merge"),
            model="test/model",
            search_config=SearchConfig(min_score=0.0),
            min_score=0.0,
        )
        candidate = CandidateMemory(
            category=MemoryCategory.PREFERENCES,
            abstract="server region shanghai",
            overview="",
            content="server region shanghai",
            source_session="s1",
        )

        result = await dedup.deduplicate(candidate)
        assert result.decision == DedupDecision.MERGE
        assert result.similar_memories
    finally:
        store.close()


async def test_dedup_keeps_create_when_cross_category_not_similar(tmp_path) -> None:
    store = SearchStore(tmp_path / "index.sqlite")
    try:
        store.index_file(
            collection="memory",
            path="memory/memories/entities/mem_2.md",
            content="weather in beijing",
        )
        dedup = MemoryDeduplicator(
            store=store,
            provider=_Provider("merge"),
            model="test/model",
            search_config=SearchConfig(min_score=0.0),
            min_score=0.0,
        )
        candidate = CandidateMemory(
            category=MemoryCategory.PREFERENCES,
            abstract="prefer concise answers",
            overview="",
            content="prefer concise answers",
            source_session="s2",
        )

        result = await dedup.deduplicate(candidate)
        assert result.decision == DedupDecision.CREATE
        assert result.similar_memories == []
    finally:
        store.close()
