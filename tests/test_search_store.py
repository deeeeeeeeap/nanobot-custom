from pathlib import Path

from nanobot.search.store import SearchStore


def test_search_store_index_and_query(tmp_path: Path) -> None:
    store = SearchStore(tmp_path / "index.sqlite")
    try:
        assert store.index_file("memory", "USER.md", "# User\nLikes Golang and SQLite") == "indexed"
        assert store.index_file("memory", "MEMORY.md", "We discussed cron job setup.") == "indexed"

        results = store.search("golang", limit=5, min_score=0.0)
        assert len(results) >= 1
        assert results[0].title
        assert results[0].docid

        by_docid = store.get_document(results[0].docid)
        assert by_docid is not None
        assert "golang" in by_docid.body.lower()
    finally:
        store.close()


def test_search_store_updates_existing_path(tmp_path: Path) -> None:
    store = SearchStore(tmp_path / "index.sqlite")
    try:
        first = store.index_file("memory", "notes.md", "old content about alpha")
        second = store.index_file("memory", "notes.md", "new content about beta")
        assert first == "indexed"
        assert second == "updated"

        old_hits = store.search("alpha", limit=5, min_score=0.0)
        new_hits = store.search("beta", limit=5, min_score=0.0)
        assert not old_hits
        assert new_hits
    finally:
        store.close()


def test_search_store_directory_index_and_deactivate(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\nhello world", encoding="utf-8")
    (docs / "b.md").write_text("# B\nhello sqlite", encoding="utf-8")

    store = SearchStore(tmp_path / "index.sqlite")
    try:
        first = store.index_directory(docs, collection="docs")
        assert first["indexed"] == 2
        assert first["removed"] == 0

        (docs / "b.md").unlink()
        second = store.index_directory(docs, collection="docs")
        assert second["removed"] >= 1

        status = store.get_status()
        assert status["active_documents"] == 1
    finally:
        store.close()


def test_search_store_vector_search(tmp_path: Path) -> None:
    store = SearchStore(tmp_path / "index.sqlite")
    try:
        store.index_file("memory", "a.md", "alpha topic details")
        store.index_file("memory", "b.md", "beta topic details")

        docs = store.list_documents_for_embedding(model="fake-model", force=True)
        by_path = {doc["path"]: doc for doc in docs}

        doc_a = by_path["a.md"]
        doc_b = by_path["b.md"]
        store.replace_document_embeddings(
            hash_value=doc_a["hash"],
            collection=doc_a["collection"],
            path=doc_a["path"],
            title=doc_a["title"],
            modified_at=doc_a["modified_at"],
            model="fake-model",
            chunks=["alpha chunk"],
            vectors=[[1.0, 0.0, 0.0]],
        )
        store.replace_document_embeddings(
            hash_value=doc_b["hash"],
            collection=doc_b["collection"],
            path=doc_b["path"],
            title=doc_b["title"],
            modified_at=doc_b["modified_at"],
            model="fake-model",
            chunks=["beta chunk"],
            vectors=[[0.0, 1.0, 0.0]],
        )

        results = store.search_vector(
            [1.0, 0.0, 0.0],
            model="fake-model",
            limit=5,
            min_score=0.0,
        )
        assert results
        assert results[0].display_path.endswith("a.md")
        assert results[0].source == "vector"
    finally:
        store.close()
