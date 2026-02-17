import json

from nanobot.agent.tools.knowledge_search import KnowledgeSearchTool
from nanobot.config.schema import SearchConfig
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
                    1.0 if "telemetry" in low else 0.0,
                    1.0 if "semantic" in low else 0.0,
                    1.0 if "report" in low else 0.0,
                ]
            )
        return vectors

    def embed_query(self, text):
        return self.embed_texts([text])[0]


async def test_knowledge_search_search_and_get(tmp_path) -> None:
    store = SearchStore(tmp_path / "index.sqlite")
    try:
        store.index_file("memory", "notes.md", "This document mentions telemetry and indexing.")
        tool = KnowledgeSearchTool(store=store, config=SearchConfig(min_score=0.0, default_limit=5))

        search_raw = await tool.execute(action="search", query="telemetry")
        search_data = json.loads(search_raw)
        assert search_data["count"] >= 1
        first = search_data["results"][0]
        assert first["docid"]

        get_raw = await tool.execute(action="get", file=first["docid"])
        get_data = json.loads(get_raw)
        assert "telemetry" in get_data["body"].lower()
    finally:
        store.close()


async def test_knowledge_search_status_and_errors(tmp_path) -> None:
    store = SearchStore(tmp_path / "index.sqlite")
    try:
        tool = KnowledgeSearchTool(store=store, config=SearchConfig())

        status_raw = await tool.execute(action="status")
        status_data = json.loads(status_raw)
        assert "total_documents" in status_data

        err_search = await tool.execute(action="search")
        assert "query" in err_search

        err_get = await tool.execute(action="get")
        assert "file" in err_get
    finally:
        store.close()


async def test_knowledge_search_hybrid_and_vector_modes(tmp_path) -> None:
    store = SearchStore(tmp_path / "index.sqlite")
    try:
        store.index_file("memory", "telemetry.md", "weekly telemetry report")
        store.index_file("memory", "semantic.md", "conceptual mapping only")
        docs = store.list_documents_for_embedding(model=FakeEmbedder.model_name, force=True)
        for doc in docs:
            vectors = FakeEmbedder().embed_texts([doc["body"]])
            store.replace_document_embeddings(
                hash_value=doc["hash"],
                collection=doc["collection"],
                path=doc["path"],
                title=doc["title"],
                modified_at=doc["modified_at"],
                model=FakeEmbedder.model_name,
                chunks=[doc["body"]],
                vectors=vectors,
            )

        tool = KnowledgeSearchTool(
            store=store,
            config=SearchConfig(
                min_score=0.0,
                default_limit=5,
                vector_enabled=True,
                embedding_model=FakeEmbedder.model_name,
            ),
            embedder=FakeEmbedder(),
        )

        hybrid_raw = await tool.execute(action="search", query="telemetry")
        hybrid_data = json.loads(hybrid_raw)
        assert hybrid_data["mode"] == "hybrid"
        assert hybrid_data["count"] >= 1

        vector_raw = await tool.execute(action="search", query="semantic retrieval")
        vector_data = json.loads(vector_raw)
        assert vector_data["mode"] in {"hybrid", "vector"}
        assert vector_data["count"] >= 1
    finally:
        store.close()
