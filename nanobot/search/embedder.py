"""Embedding backends for semantic search."""

from __future__ import annotations

from typing import Sequence


class SentenceTransformerEmbedder:
    """Sentence-transformers embedder with normalized vectors."""

    def __init__(self, model_name: str):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:  # pragma: no cover - dependency optional by design
            raise RuntimeError(
                "Semantic search requires `sentence-transformers`. "
                "Install with: pip install sentence-transformers"
            ) from e
        self.model_name = model_name
        self._model = SentenceTransformer(model_name)

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [list(map(float, vec)) for vec in vectors]

    def embed_query(self, text: str) -> list[float]:
        vectors = self.embed_texts([text])
        return vectors[0] if vectors else []

