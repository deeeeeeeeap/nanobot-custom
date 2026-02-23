from pathlib import Path
from typing import Any

from nanobot.memory.extractor import MemoryExtractor
from nanobot.providers.base import LLMProvider, LLMResponse


class _Provider(LLMProvider):
    def __init__(self):
        super().__init__(None, None)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        if "去重决策器" in str(messages[0]["content"]):
            return LLMResponse(content='{"decision":"create","reason":"ok"}')
        if "记忆合并器" in str(messages[0]["content"]):
            return LLMResponse(content="合并后的资料")
        if "记忆提取器" in str(messages[0]["content"]):
            return LLMResponse(
                content=(
                    '{"memories":[{"category":"preferences","abstract":"喜欢中文","overview":"偏好中文回复",'
                    '"content":"用户要求始终用中文回复"}]}'
                )
            )
        return LLMResponse(
            content=(
                '{"memories":[{"category":"preferences","abstract":"喜欢中文","overview":"偏好中文回复",'
                '"content":"用户要求始终用中文回复"}]}'
            )
        )

    def get_default_model(self) -> str:
        return "test/model"


async def test_extract_and_create_memory(tmp_path: Path) -> None:
    extractor = MemoryExtractor(
        provider=_Provider(),
        workspace=tmp_path,
        model="test/model",
        output_language="zh-CN",
    )
    candidates = await extractor.extract(
        [{"role": "user", "content": "以后都用中文"}],
        session_key="telegram:1",
    )
    assert len(candidates) == 1
    path = await extractor.create_memory(candidates[0], session_key="telegram:1")
    assert path is not None
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "喜欢中文" in text


async def test_profile_memory_merge(tmp_path: Path) -> None:
    extractor = MemoryExtractor(
        provider=_Provider(),
        workspace=tmp_path,
        model="test/model",
    )
    from nanobot.memory.models import CandidateMemory, MemoryCategory

    candidate = CandidateMemory(
        category=MemoryCategory.PROFILE,
        abstract="用户是开发者",
        overview="用户身份",
        content="用户是一名后端开发者",
        source_session="telegram:2",
    )
    path = await extractor.create_memory(candidate, session_key="telegram:2")
    assert path is not None
    assert path.exists()
    assert path.read_text(encoding="utf-8").strip() == "用户是一名后端开发者"

    candidate2 = CandidateMemory(
        category=MemoryCategory.PROFILE,
        abstract="用户偏好中文",
        overview="偏好",
        content="用户要求中文回复",
        source_session="telegram:3",
    )
    path2 = await extractor.create_memory(candidate2, session_key="telegram:3")
    assert path2 == path
    assert "合并后的资料" in path.read_text(encoding="utf-8")
