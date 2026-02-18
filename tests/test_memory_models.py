from nanobot.memory.models import CATEGORY_DIRS, CandidateMemory, MemoryCategory


def test_category_dirs_covers_all_categories() -> None:
    assert set(CATEGORY_DIRS.keys()) == set(MemoryCategory)


def test_candidate_memory_defaults() -> None:
    mem = CandidateMemory(
        category=MemoryCategory.PREFERENCES,
        abstract="偏好摘要",
        overview="偏好概览",
        content="偏好详情",
        source_session="telegram:1",
    )
    assert mem.language == "zh-CN"
    assert mem.category == MemoryCategory.PREFERENCES

