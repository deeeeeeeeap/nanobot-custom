from pathlib import Path
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.memory.compressor import SessionCompressor
from nanobot.memory.models import CandidateMemory, DedupDecision, DedupResult, MemoryCategory
from nanobot.providers.base import LLMProvider, LLMResponse


class _Provider(LLMProvider):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        return LLMResponse(content="会话摘要")

    def get_default_model(self) -> str:
        return "test/model"


class _Extractor:
    def __init__(self, workspace: Path):
        self.workspace = workspace

    async def extract(self, messages, session_key):
        return [
            CandidateMemory(
                category=MemoryCategory.PREFERENCES,
                abstract="偏好中文",
                overview="偏好",
                content="用户偏好中文",
                source_session=session_key,
            )
        ]

    async def create_memory(self, candidate, session_key):
        path = self.workspace / "memory" / "memories" / "preferences" / "mem_x.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(candidate.abstract, encoding="utf-8")
        return path

    async def merge_into_file(self, target, candidate):
        return False


class _Dedup:
    async def deduplicate(self, candidate):
        return DedupResult(
            decision=DedupDecision.CREATE,
            candidate=candidate,
            similar_memories=[],
            reason="",
        )


async def test_session_compressor_create_and_summary(tmp_path: Path) -> None:
    memory_store = MemoryStore(tmp_path)
    compressor = SessionCompressor(
        extractor=_Extractor(tmp_path),
        deduplicator=_Dedup(),
        memory_store=memory_store,
        provider=_Provider(),
        model="test/model",
        indexer=None,
    )
    result = await compressor.compress(
        messages=[{"role": "user", "content": "以后都用中文"}],
        session_key="telegram:1",
    )
    assert result.created == 1
    assert result.summary == "会话摘要"
    assert "会话摘要" in memory_store.history_file.read_text(encoding="utf-8")

