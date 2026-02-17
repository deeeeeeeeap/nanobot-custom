"""Incremental file indexer for SearchStore."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

from nanobot.search.store import SearchStore


class TextEmbedder(Protocol):
    model_name: str

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed_query(self, text: str) -> list[float]:
        ...


class Indexer:
    """Manage full/incremental indexing and optional embedding generation."""

    def __init__(self, store: SearchStore, workspace: Path):
        self.store = store
        self.workspace = Path(workspace).expanduser().resolve()

    def _iter_files(self, base: Path, pattern: str) -> Iterable[Path]:
        for file_path in base.glob(pattern):
            if not file_path.is_file():
                continue
            rel_parts = file_path.relative_to(base).parts
            if any(part.startswith(".") or part in self.store.EXCLUDED_DIRS for part in rel_parts):
                continue
            yield file_path

    def _to_store_path(self, filepath: Path) -> str:
        fp = filepath.resolve()
        try:
            return fp.relative_to(self.workspace).as_posix()
        except ValueError:
            return fp.name

    @staticmethod
    def _file_mtime_iso(filepath: Path) -> str:
        return datetime.fromtimestamp(filepath.stat().st_mtime, tz=timezone.utc).isoformat()

    @staticmethod
    def _chunk_text(text: str, chunk_size: int, chunk_overlap: float) -> list[str]:
        words = text.split()
        if not words:
            return []
        overlap = int(chunk_size * chunk_overlap)
        step = max(1, chunk_size - overlap)
        chunks: list[str] = []
        i = 0
        while i < len(words):
            part = words[i : i + chunk_size]
            if not part:
                break
            chunks.append(" ".join(part))
            if i + chunk_size >= len(words):
                break
            i += step
        return chunks

    def full_index(
        self,
        directories: list[Path],
        collection: str = "memory",
        pattern: str = "**/*.md",
    ) -> dict[str, int]:
        indexed = updated = unchanged = removed = skipped = 0
        seen_paths: set[str] = set()

        for directory in directories:
            base = Path(directory).expanduser()
            if not base.is_absolute():
                base = (self.workspace / base).resolve()
            if not base.exists():
                continue

            for file_path in self._iter_files(base, pattern):
                try:
                    content = file_path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    skipped += 1
                    continue
                if not content.strip():
                    skipped += 1
                    continue

                store_path = self._to_store_path(file_path)
                seen_paths.add(store_path)
                status = self.store.index_file(
                    collection=collection,
                    path=store_path,
                    content=content,
                    modified_at=self._file_mtime_iso(file_path),
                )
                if status == "indexed":
                    indexed += 1
                elif status == "updated":
                    updated += 1
                elif status == "unchanged":
                    unchanged += 1
                else:
                    skipped += 1

        removed = self.store.deactivate_missing(collection, seen_paths)
        return {
            "indexed": indexed,
            "updated": updated,
            "unchanged": unchanged,
            "removed": removed,
            "skipped": skipped,
        }

    def index_single(self, filepath: Path, collection: str = "memory") -> dict[str, str | bool]:
        fp = Path(filepath).expanduser()
        if not fp.is_absolute():
            fp = (self.workspace / fp).resolve()

        store_path = self._to_store_path(fp)
        if not fp.exists():
            removed = self.store.deactivate_path(collection, store_path)
            if removed:
                self.store.cleanup_orphaned_content()
            return {"status": "removed" if removed else "missing", "path": store_path}

        try:
            content = fp.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {"status": "skipped", "path": store_path}

        status = self.store.index_file(
            collection=collection,
            path=store_path,
            content=content,
            modified_at=self._file_mtime_iso(fp),
        )
        return {"status": status, "path": store_path}

    def embed_documents(
        self,
        *,
        embedder: TextEmbedder,
        collection: str | None = None,
        force: bool = False,
        chunk_size: int = 900,
        chunk_overlap: float = 0.15,
        batch_size: int = 32,
    ) -> dict[str, Any]:
        docs = self.store.list_documents_for_embedding(
            model=embedder.model_name,
            collection=collection,
            force=force,
        )
        docs_embedded = 0
        docs_skipped = 0
        chunks_embedded = 0

        for doc in docs:
            chunks = self._chunk_text(doc["body"], chunk_size=chunk_size, chunk_overlap=chunk_overlap)
            if not chunks:
                docs_skipped += 1
                continue

            vectors: list[list[float]] = []
            for i in range(0, len(chunks), batch_size):
                vectors.extend(embedder.embed_texts(chunks[i : i + batch_size]))

            count = self.store.replace_document_embeddings(
                hash_value=doc["hash"],
                collection=doc["collection"],
                path=doc["path"],
                title=doc["title"],
                modified_at=doc["modified_at"],
                model=embedder.model_name,
                chunks=chunks,
                vectors=vectors,
            )
            docs_embedded += 1
            chunks_embedded += count

        return {
            "model": embedder.model_name,
            "docs_considered": len(docs),
            "docs_embedded": docs_embedded,
            "docs_skipped": docs_skipped,
            "chunks_embedded": chunks_embedded,
        }

    def auto_index_on_startup(
        self,
        index_dirs: list[str] | None = None,
        collection: str = "memory",
        pattern: str = "**/*.md",
    ) -> dict[str, int]:
        targets = index_dirs or ["memory"]
        dirs = [self.workspace / d for d in targets]
        return self.full_index(directories=dirs, collection=collection, pattern=pattern)

