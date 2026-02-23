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
        tool_choice: str = "auto",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        return LLMResponse(content=f'{{"decision":"{self.decision}","reason":"ok"}}')

    def get_default_model(self) -> str:
        return "test/model"


async def test_dedup_create_when_no_similar(tmp_path) -> None:
    store = SearchStore(tmp_path / "index.sqlite")
    try:
        dedup = MemoryDeduplicator(
            store=store,
            provider=_Provider("create"),
            model="test/model",
            search_config=SearchConfig(min_score=0.0),
            min_score=0.0,
        )
        candidate = CandidateMemory(
            category=MemoryCategory.PREFERENCES,
            abstract="喜欢中文",
            overview="",
            content="以后用中文回复",
            source_session="s1",
        )
        result = await dedup.deduplicate(candidate)
        assert result.decision == DedupDecision.CREATE
        assert result.similar_memories == []
    finally:
        store.close()


async def test_dedup_merge_when_llm_returns_merge(tmp_path) -> None:
    store = SearchStore(tmp_path / "index.sqlite")
    try:
        store.index_file(
            collection="memory",
            path="memory/memories/preferences/mem_1.md",
            content="user prefers chinese replies",
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
            abstract="prefers chinese replies",
            overview="",
            content="always reply in chinese",
            source_session="s2",
        )
        result = await dedup.deduplicate(candidate)
        assert result.decision == DedupDecision.MERGE
        assert result.similar_memories
    finally:
        store.close()
