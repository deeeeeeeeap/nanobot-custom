from pathlib import Path

from nanobot.search.indexer import Indexer
from nanobot.search.store import SearchStore


class FakeEmbedder:
    model_name = "fake-embedder"

    @staticmethod
    def embed_texts(texts):
        vectors = []
        for text in texts:
            low = text.lower()
            vectors.append(
                [
                    1.0 if "alpha" in low else 0.0,
                    1.0 if "beta" in low else 0.0,
                    1.0 if "telemetry" in low else 0.0,
                ]
            )
        return vectors

    def embed_query(self, text):
        return self.embed_texts([text])[0]


def test_indexer_full_index_and_search(tmp_path: Path) -> None:
    workspace = tmp_path
    memory_dir = workspace / "memory"
    memory_dir.mkdir()
    (memory_dir / "MEMORY.md").write_text("Project uses sqlite fts for search.", encoding="utf-8")

    store = SearchStore(workspace / "search" / "index.sqlite")
    try:
        indexer = Indexer(store=store, workspace=workspace)
        stats = indexer.full_index([memory_dir], collection="memory")
        assert stats["indexed"] >= 1

        hits = store.search("sqlite", limit=5, min_score=0.0, collection="memory")
        assert hits
    finally:
        store.close()


def test_indexer_embed_documents(tmp_path: Path) -> None:
    workspace = tmp_path
    memory_dir = workspace / "memory"
    memory_dir.mkdir()
    (memory_dir / "a.md").write_text("alpha telemetry", encoding="utf-8")
    (memory_dir / "b.md").write_text("beta details", encoding="utf-8")

    store = SearchStore(workspace / "search" / "index.sqlite")
    try:
        indexer = Indexer(store=store, workspace=workspace)
        stats = indexer.full_index([memory_dir], collection="memory")
        assert stats["indexed"] == 2

        embed_stats = indexer.embed_documents(
            embedder=FakeEmbedder(),
            collection="memory",
            force=False,
            chunk_size=50,
            chunk_overlap=0.0,
            batch_size=2,
        )
        assert embed_stats["docs_embedded"] == 2
        assert embed_stats["chunks_embedded"] >= 2

        results = store.search_vector(
            FakeEmbedder().embed_query("alpha"),
            model=FakeEmbedder.model_name,
            limit=5,
            min_score=0.0,
            collection="memory",
        )
        assert results
        assert "a.md" in results[0].display_path
    finally:
        store.close()


def test_indexer_incremental_add_and_remove(tmp_path: Path) -> None:
    workspace = tmp_path
    memory_dir = workspace / "memory"
    memory_dir.mkdir()

    store = SearchStore(workspace / "search" / "index.sqlite")
    try:
        indexer = Indexer(store=store, workspace=workspace)
        target = memory_dir / "USER.md"
        target.write_text("User prefers weekly reports.", encoding="utf-8")

        add_result = indexer.index_single(target, collection="memory")
        assert add_result["status"] in {"indexed", "updated", "unchanged"}
        assert store.search("weekly", limit=5, min_score=0.0)

        target.unlink()
        remove_result = indexer.index_single(target, collection="memory")
        assert remove_result["status"] in {"removed", "missing"}
    finally:
        store.close()
