"""Local knowledge search package."""

from nanobot.search.indexer import Indexer
from nanobot.search.store import DocumentResult, SearchResult, SearchStore

__all__ = [
    "SearchStore",
    "SearchResult",
    "DocumentResult",
    "Indexer",
]


def __getattr__(name: str):
    """Lazy-export optional embedder to avoid eager optional-dependency exposure."""
    if name == "SentenceTransformerEmbedder":
        from nanobot.search.embedder import SentenceTransformerEmbedder

        return SentenceTransformerEmbedder
    raise AttributeError(f"module 'nanobot.search' has no attribute {name!r}")
