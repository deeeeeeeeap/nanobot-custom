"""Knowledge search tool backed by local SQLite FTS index."""

from __future__ import annotations

import json
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.config.schema import SearchConfig
from nanobot.search.indexer import TextEmbedder
from nanobot.search.store import SearchResult, SearchStore


class KnowledgeSearchTool(Tool):
    """Search and fetch indexed local knowledge documents."""

    def __init__(
        self,
        store: SearchStore,
        config: SearchConfig,
        embedder: TextEmbedder | None = None,
    ):
        self._store = store
        self._config = config
        self._embedder = embedder

    @property
    def name(self) -> str:
        return "knowledge_search"

    @property
    def description(self) -> str:
        return (
            "Search indexed local knowledge with BM25 and optional semantic vector retrieval, "
            "fetch document by path/docid, or inspect index status."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "get", "status"],
                    "description": "search=query BM25/semantic, get=fetch a document, status=index overview",
                },
                "query": {
                    "type": "string",
                    "description": "Search query for action=search",
                },
                "file": {
                    "type": "string",
                    "description": "Virtual path, collection/path, or docid for action=get",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum number of search results",
                },
                "collection": {
                    "type": "string",
                    "description": "Optional collection filter",
                },
            },
            "required": ["action"],
        }

    @staticmethod
    def _merge_hybrid_results(
        bm25_results: list[SearchResult],
        vector_results: list[SearchResult],
        limit: int,
    ) -> list[SearchResult]:
        merged: dict[str, dict[str, SearchResult | None]] = {}
        for item in bm25_results:
            merged.setdefault(item.filepath, {"bm25": None, "vector": None})["bm25"] = item
        for item in vector_results:
            merged.setdefault(item.filepath, {"bm25": None, "vector": None})["vector"] = item

        out: list[SearchResult] = []
        for entry in merged.values():
            bm = entry["bm25"]
            vec = entry["vector"]
            if bm and vec:
                # Keep a slight preference for semantic score when both are present.
                score = (bm.score * 0.45) + (vec.score * 0.55)
                base = vec if vec.score >= bm.score else bm
                out.append(
                    SearchResult(
                        filepath=base.filepath,
                        display_path=base.display_path,
                        title=base.title,
                        hash=base.hash,
                        docid=base.docid,
                        collection=base.collection,
                        modified_at=base.modified_at,
                        body_length=base.body_length,
                        snippet=base.snippet,
                        score=score,
                        source="hybrid",
                    )
                )
            elif bm:
                out.append(bm)
            elif vec:
                out.append(vec)

        out.sort(key=lambda x: x.score, reverse=True)
        return out[:limit]

    async def execute(
        self,
        action: str,
        query: str | None = None,
        file: str | None = None,
        limit: int | None = None,
        collection: str | None = None,
        **kwargs: Any,
    ) -> str:
        if action == "status":
            payload = self._store.get_status()
            payload["vector_enabled"] = self._config.vector_enabled
            payload["embedding_model"] = self._config.embedding_model
            payload["embedder_loaded"] = self._embedder is not None
            return json.dumps(payload, ensure_ascii=False, indent=2)

        if action == "search":
            if not query:
                return "Error: `query` is required when action=search"
            use_limit = limit or self._config.default_limit
            bm25_results = self._store.search(
                query=query,
                limit=use_limit,
                min_score=self._config.min_score,
                collection=collection,
            )
            vector_results: list[SearchResult] = []
            mode = "bm25"

            if self._config.vector_enabled and self._embedder is not None:
                query_vec = self._embedder.embed_query(query)
                vector_results = self._store.search_vector(
                    query_vec,
                    model=self._config.embedding_model,
                    limit=use_limit,
                    min_score=self._config.min_score,
                    collection=collection,
                )
                if bm25_results and vector_results:
                    results = self._merge_hybrid_results(bm25_results, vector_results, use_limit)
                    mode = "hybrid"
                elif vector_results:
                    results = vector_results[:use_limit]
                    mode = "vector"
                else:
                    results = bm25_results
            else:
                results = bm25_results

            payload = {
                "query": query,
                "mode": mode,
                "count": len(results),
                "results": [item.to_dict() for item in results],
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)

        if action == "get":
            if not file:
                return "Error: `file` is required when action=get"
            doc = self._store.get_document(file)
            if doc is None:
                return f"Not found: {file}"
            return json.dumps(doc.to_dict(), ensure_ascii=False, indent=2)

        return f"Error: unknown action '{action}'"

