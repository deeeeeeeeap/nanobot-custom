"""SQLite FTS5-backed local knowledge search store."""

from __future__ import annotations

from array import array
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from math import sqrt
from pathlib import Path
import re
import sqlite3
from typing import Any, Sequence


@dataclass
class SnippetResult:
    line: int
    snippet: str
    lines_before: int
    lines_after: int
    snippet_lines: int


@dataclass
class SearchResult:
    filepath: str
    display_path: str
    title: str
    hash: str
    docid: str
    collection: str
    modified_at: str
    body_length: int
    snippet: str
    score: float
    source: str = "fts"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DocumentResult:
    filepath: str
    display_path: str
    title: str
    hash: str
    docid: str
    collection: str
    modified_at: str
    body_length: int
    body: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SearchStore:
    """SQLite search store with BM25 and optional vector semantic retrieval."""

    EXCLUDED_DIRS = {"node_modules", ".git", ".cache", "vendor", "dist", "build"}

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA foreign_keys = ON")

        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS content (
              hash TEXT PRIMARY KEY,
              doc TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS documents (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              collection TEXT NOT NULL,
              path TEXT NOT NULL,
              title TEXT NOT NULL,
              hash TEXT NOT NULL,
              created_at TEXT NOT NULL,
              modified_at TEXT NOT NULL,
              active INTEGER NOT NULL DEFAULT 1,
              FOREIGN KEY (hash) REFERENCES content(hash) ON DELETE CASCADE,
              UNIQUE(collection, path)
            );

            CREATE INDEX IF NOT EXISTS idx_documents_collection
              ON documents(collection, active);
            CREATE INDEX IF NOT EXISTS idx_documents_hash
              ON documents(hash);
            CREATE INDEX IF NOT EXISTS idx_documents_path
              ON documents(path, active);
            """
        )

        self.conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
              filepath, title, body,
              tokenize='porter unicode61'
            )
            """
        )

        self.conn.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS documents_ai
            AFTER INSERT ON documents
            WHEN new.active = 1
            BEGIN
              INSERT INTO documents_fts(rowid, filepath, title, body)
              SELECT
                new.id,
                new.collection || '/' || new.path,
                new.title,
                (SELECT doc FROM content WHERE hash = new.hash)
              WHERE new.active = 1;
            END;

            CREATE TRIGGER IF NOT EXISTS documents_ad
            AFTER DELETE ON documents
            BEGIN
              DELETE FROM documents_fts WHERE rowid = old.id;
            END;

            CREATE TRIGGER IF NOT EXISTS documents_au
            AFTER UPDATE ON documents
            BEGIN
              DELETE FROM documents_fts WHERE rowid = old.id AND new.active = 0;
              INSERT OR REPLACE INTO documents_fts(rowid, filepath, title, body)
              SELECT
                new.id,
                new.collection || '/' || new.path,
                new.title,
                (SELECT doc FROM content WHERE hash = new.hash)
              WHERE new.active = 1;
            END;
            """
        )

        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS document_vectors (
              hash TEXT NOT NULL,
              chunk_index INTEGER NOT NULL,
              collection TEXT NOT NULL,
              path TEXT NOT NULL,
              title TEXT NOT NULL,
              modified_at TEXT NOT NULL,
              chunk_text TEXT NOT NULL,
              embedding BLOB NOT NULL,
              dim INTEGER NOT NULL,
              model TEXT NOT NULL,
              embedded_at TEXT NOT NULL,
              PRIMARY KEY(hash, chunk_index, model),
              FOREIGN KEY (hash) REFERENCES content(hash) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_document_vectors_model_collection
              ON document_vectors(model, collection);
            CREATE INDEX IF NOT EXISTS idx_document_vectors_hash_model
              ON document_vectors(hash, model);
            """
        )

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_path(path: str) -> str:
        return path.replace("\\", "/").strip("/")

    @staticmethod
    def _hash_content(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _get_docid(hash_value: str) -> str:
        return hash_value[:6]

    @staticmethod
    def _vector_to_blob(vector: Sequence[float]) -> bytes:
        return array("f", [float(x) for x in vector]).tobytes()

    @staticmethod
    def _blob_to_vector(blob: bytes) -> list[float]:
        values = array("f")
        values.frombytes(blob)
        return list(values)

    @staticmethod
    def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
        if len(left) != len(right) or not left:
            return 0.0
        dot = sum(a * b for a, b in zip(left, right, strict=False))
        ln = sqrt(sum(a * a for a in left))
        rn = sqrt(sum(b * b for b in right))
        if ln == 0.0 or rn == 0.0:
            return 0.0
        return dot / (ln * rn)

    @staticmethod
    def _normalize_docid(value: str) -> str:
        normalized = value.strip()
        if (normalized.startswith('"') and normalized.endswith('"')) or (
            normalized.startswith("'") and normalized.endswith("'")
        ):
            normalized = normalized[1:-1]
        if normalized.startswith("#"):
            normalized = normalized[1:]
        return normalized

    @classmethod
    def _is_docid(cls, value: str) -> bool:
        normalized = cls._normalize_docid(value)
        return len(normalized) >= 6 and bool(re.fullmatch(r"[a-fA-F0-9]+", normalized))

    @staticmethod
    def _extract_title(content: str, filename: str) -> str:
        ext = Path(filename).suffix.lower()
        if ext == ".md":
            m = re.search(r"^##?\s+(.+)$", content, flags=re.MULTILINE)
            if m:
                title = m.group(1).strip()
                if title in {"📑 Notes", "Notes"}:
                    m2 = re.search(r"^##\s+(.+)$", content, flags=re.MULTILINE)
                    if m2:
                        return m2.group(1).strip()
                return title
        if ext == ".org":
            m = re.search(r"^#\+TITLE:\s*(.+)$", content, flags=re.IGNORECASE | re.MULTILINE)
            if m:
                return m.group(1).strip()
            m = re.search(r"^\*+\s+(.+)$", content, flags=re.MULTILINE)
            if m:
                return m.group(1).strip()
        return Path(filename).name.rsplit(".", 1)[0]

    @staticmethod
    def _sanitize_term(term: str) -> str:
        chars = [ch for ch in term if ch.isalnum() or ch == "'"]
        return "".join(chars).lower()

    @classmethod
    def _build_fts_query(cls, query: str) -> str | None:
        terms = [cls._sanitize_term(t) for t in query.split()]
        terms = [t for t in terms if t]
        if not terms:
            return None
        if len(terms) == 1:
            return f"\"{terms[0]}\"*"
        return " AND ".join(f"\"{t}\"*" for t in terms)

    @staticmethod
    def _bm25_to_score(value: float) -> float:
        abs_value = abs(value)
        return abs_value / (1.0 + abs_value)

    @staticmethod
    def extract_snippet(body: str, query: str, max_len: int = 500) -> SnippetResult:
        lines = body.split("\n")
        total_lines = len(lines)
        terms = [t for t in query.lower().split() if t]
        best_line = 0
        best_score = -1
        for i, line in enumerate(lines):
            score = sum(1 for term in terms if term in line.lower())
            if score > best_score:
                best_score = score
                best_line = i

        start = max(0, best_line - 1)
        end = min(total_lines, best_line + 3)
        snippet_lines = lines[start:end]
        snippet_text = "\n".join(snippet_lines)
        if len(snippet_text) > max_len:
            snippet_text = snippet_text[: max_len - 3] + "..."

        absolute_start = start + 1
        count = len(snippet_lines)
        lines_before = absolute_start - 1
        lines_after = total_lines - (absolute_start + count - 1)
        header = f"@@ -{absolute_start},{count} @@ ({lines_before} before, {lines_after} after)"
        return SnippetResult(
            line=best_line + 1,
            snippet=f"{header}\n{snippet_text}",
            lines_before=lines_before,
            lines_after=lines_after,
            snippet_lines=count,
        )

    def search(
        self,
        query: str,
        limit: int = 10,
        min_score: float = 0.0,
        collection: str | None = None,
    ) -> list[SearchResult]:
        fts_query = self._build_fts_query(query)
        if not fts_query:
            return []

        sql = """
            SELECT
              'qmd://' || d.collection || '/' || d.path AS filepath,
              d.collection || '/' || d.path AS display_path,
              d.title,
              content.doc AS body,
              d.hash,
              d.collection,
              d.modified_at,
              bm25(documents_fts, 10.0, 1.0) AS bm25_score
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.rowid
            JOIN content ON content.hash = d.hash
            WHERE documents_fts MATCH ? AND d.active = 1
        """
        params: list[Any] = [fts_query]
        if collection:
            sql += " AND d.collection = ?"
            params.append(collection)
        sql += " ORDER BY bm25_score ASC LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(sql, params).fetchall()
        results: list[SearchResult] = []
        for row in rows:
            score = self._bm25_to_score(float(row["bm25_score"]))
            if score < min_score:
                continue
            snippet = self.extract_snippet(row["body"], query).snippet
            results.append(
                SearchResult(
                    filepath=row["filepath"],
                    display_path=row["display_path"],
                    title=row["title"],
                    hash=row["hash"],
                    docid=self._get_docid(row["hash"]),
                    collection=row["collection"],
                    modified_at=row["modified_at"],
                    body_length=len(row["body"] or ""),
                    snippet=snippet,
                    score=score,
                    source="fts",
                )
            )
        return results

    def search_vector(
        self,
        query_vector: Sequence[float],
        *,
        model: str,
        limit: int = 10,
        min_score: float = 0.0,
        collection: str | None = None,
    ) -> list[SearchResult]:
        if not query_vector:
            return []
        dim = len(query_vector)
        sql = """
            SELECT
              hash, chunk_index, collection, path, title, modified_at, chunk_text, embedding
            FROM document_vectors
            WHERE model = ? AND dim = ?
        """
        params: list[Any] = [model, dim]
        if collection:
            sql += " AND collection = ?"
            params.append(collection)
        rows = self.conn.execute(sql, params).fetchall()

        best_by_doc: dict[str, SearchResult] = {}
        for row in rows:
            vector = self._blob_to_vector(row["embedding"])
            cosine = self._cosine_similarity(query_vector, vector)
            score = (cosine + 1.0) / 2.0
            if score < min_score:
                continue

            display_path = f"{row['collection']}/{row['path']}"
            key = display_path
            snippet_text = row["chunk_text"]
            if len(snippet_text) > 500:
                snippet_text = snippet_text[:497] + "..."
            candidate = SearchResult(
                filepath=f"qmd://{display_path}",
                display_path=display_path,
                title=row["title"],
                hash=row["hash"],
                docid=self._get_docid(row["hash"]),
                collection=row["collection"],
                modified_at=row["modified_at"],
                body_length=len(row["chunk_text"] or ""),
                snippet=snippet_text,
                score=score,
                source="vector",
            )
            if key not in best_by_doc or candidate.score > best_by_doc[key].score:
                best_by_doc[key] = candidate

        return sorted(best_by_doc.values(), key=lambda x: x.score, reverse=True)[:limit]

    def get_document(self, file_or_docid: str) -> DocumentResult | None:
        value = file_or_docid.strip()
        if not value:
            return None

        row: sqlite3.Row | None = None
        if self._is_docid(value):
            short_hash = self._normalize_docid(value)
            row = self.conn.execute(
                """
                SELECT
                  'qmd://' || d.collection || '/' || d.path AS filepath,
                  d.collection || '/' || d.path AS display_path,
                  d.title,
                  d.hash,
                  d.collection,
                  d.modified_at,
                  content.doc AS body
                FROM documents d
                JOIN content ON content.hash = d.hash
                WHERE d.hash LIKE ? AND d.active = 1
                LIMIT 1
                """,
                (f"{short_hash}%",),
            ).fetchone()
        else:
            if value.startswith("qmd://"):
                virtual = value
            else:
                virtual = f"qmd://{value}"
            row = self.conn.execute(
                """
                SELECT
                  'qmd://' || d.collection || '/' || d.path AS filepath,
                  d.collection || '/' || d.path AS display_path,
                  d.title,
                  d.hash,
                  d.collection,
                  d.modified_at,
                  content.doc AS body
                FROM documents d
                JOIN content ON content.hash = d.hash
                WHERE ('qmd://' || d.collection || '/' || d.path = ? OR d.collection || '/' || d.path = ?)
                  AND d.active = 1
                LIMIT 1
                """,
                (virtual, value),
            ).fetchone()
            if row is None:
                row = self.conn.execute(
                    """
                    SELECT
                      'qmd://' || d.collection || '/' || d.path AS filepath,
                      d.collection || '/' || d.path AS display_path,
                      d.title,
                      d.hash,
                      d.collection,
                      d.modified_at,
                      content.doc AS body
                    FROM documents d
                    JOIN content ON content.hash = d.hash
                    WHERE ('qmd://' || d.collection || '/' || d.path LIKE ? OR d.path LIKE ?)
                      AND d.active = 1
                    LIMIT 1
                    """,
                    (f"%{value}", f"%{value}"),
                ).fetchone()

        if row is None:
            return None
        body = row["body"] or ""
        return DocumentResult(
            filepath=row["filepath"],
            display_path=row["display_path"],
            title=row["title"],
            hash=row["hash"],
            docid=self._get_docid(row["hash"]),
            collection=row["collection"],
            modified_at=row["modified_at"],
            body_length=len(body),
            body=body,
        )

    def index_file(self, collection: str, path: str, content: str, modified_at: str | None = None) -> str:
        if not content.strip():
            return "skipped"

        normalized_path = self._normalize_path(path)
        now = self._now_iso()
        modified = modified_at or now
        hash_value = self._hash_content(content)
        title = self._extract_title(content, normalized_path)

        existing = self.conn.execute(
            """
            SELECT id, hash, title, active
            FROM documents
            WHERE collection = ? AND path = ?
            """,
            (collection, normalized_path),
        ).fetchone()

        if (
            existing
            and existing["hash"] == hash_value
            and existing["title"] == title
            and int(existing["active"]) == 1
        ):
            self.conn.execute(
                "UPDATE documents SET modified_at = ? WHERE id = ?",
                (modified, existing["id"]),
            )
            self.conn.commit()
            return "unchanged"

        old_hash = existing["hash"] if existing else None
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO content (hash, doc, created_at) VALUES (?, ?, ?)",
                (hash_value, content, now),
            )
            if existing:
                self.conn.execute(
                    """
                    UPDATE documents
                    SET title = ?, hash = ?, modified_at = ?, active = 1
                    WHERE id = ?
                    """,
                    (title, hash_value, modified, existing["id"]),
                )
            else:
                self.conn.execute(
                    """
                    INSERT INTO documents (collection, path, title, hash, created_at, modified_at, active)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                    """,
                    (collection, normalized_path, title, hash_value, now, modified),
                )

        if old_hash and old_hash != hash_value:
            self.cleanup_orphaned_content()
        return "updated" if existing else "indexed"

    def list_documents_for_embedding(
        self,
        *,
        model: str,
        collection: str | None = None,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT d.collection, d.path, d.title, d.hash, d.modified_at, content.doc AS body
            FROM documents d
            JOIN content ON content.hash = d.hash
            WHERE d.active = 1
        """
        params: list[Any] = []
        if collection:
            sql += " AND d.collection = ?"
            params.append(collection)
        if not force:
            sql += """
                AND NOT EXISTS (
                  SELECT 1
                  FROM document_vectors v
                  WHERE v.hash = d.hash AND v.model = ?
                )
            """
            params.append(model)
        sql += " ORDER BY d.collection, d.path"
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def replace_document_embeddings(
        self,
        *,
        hash_value: str,
        collection: str,
        path: str,
        title: str,
        modified_at: str,
        model: str,
        chunks: Sequence[str],
        vectors: Sequence[Sequence[float]],
    ) -> int:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors length mismatch")
        if not chunks:
            return 0
        dim = len(vectors[0])
        if dim == 0:
            return 0

        now = self._now_iso()
        with self.conn:
            self.conn.execute(
                "DELETE FROM document_vectors WHERE hash = ? AND model = ?",
                (hash_value, model),
            )
            for idx, (chunk_text, vec) in enumerate(zip(chunks, vectors, strict=False)):
                if len(vec) != dim:
                    raise ValueError("inconsistent embedding dimensions")
                self.conn.execute(
                    """
                    INSERT INTO document_vectors(
                      hash, chunk_index, collection, path, title, modified_at,
                      chunk_text, embedding, dim, model, embedded_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        hash_value,
                        idx,
                        collection,
                        self._normalize_path(path),
                        title,
                        modified_at,
                        chunk_text,
                        self._vector_to_blob(vec),
                        dim,
                        model,
                        now,
                    ),
                )
        return len(chunks)

    def deactivate_path(self, collection: str, path: str) -> bool:
        normalized_path = self._normalize_path(path)
        with self.conn:
            cur = self.conn.execute(
                "UPDATE documents SET active = 0 WHERE collection = ? AND path = ? AND active = 1",
                (collection, normalized_path),
            )
        return cur.rowcount > 0

    def deactivate_missing(
        self,
        collection: str,
        seen_paths: set[str],
        path_prefix: str | None = None,
    ) -> int:
        sql = "SELECT path FROM documents WHERE collection = ? AND active = 1"
        params: list[Any] = [collection]
        prefix = self._normalize_path(path_prefix) if path_prefix else None
        if prefix:
            sql += " AND (path = ? OR path LIKE ?)"
            params.extend([prefix, f"{prefix}/%"])
        rows = self.conn.execute(sql, params).fetchall()

        removed = 0
        for row in rows:
            if row["path"] not in seen_paths:
                removed += int(self.deactivate_path(collection, row["path"]))
        if removed:
            self.cleanup_orphaned_content()
        return removed

    def cleanup_orphaned_content(self) -> int:
        with self.conn:
            cur = self.conn.execute(
                """
                DELETE FROM content
                WHERE hash NOT IN (SELECT DISTINCT hash FROM documents)
                """
            )
        return cur.rowcount

    def index_directory(
        self,
        directory: Path,
        collection: str,
        pattern: str = "**/*.md",
        path_prefix: str | None = None,
        deactivate_missing: bool = True,
    ) -> dict[str, int]:
        base = Path(directory)
        if not base.exists():
            return {"indexed": 0, "updated": 0, "unchanged": 0, "removed": 0, "skipped": 0}

        indexed = updated = unchanged = removed = skipped = 0
        seen_paths: set[str] = set()
        for file_path in base.glob(pattern):
            if not file_path.is_file():
                continue
            rel_parts = file_path.relative_to(base).parts
            if any(part.startswith(".") or part in self.EXCLUDED_DIRS for part in rel_parts):
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                skipped += 1
                continue

            rel = file_path.relative_to(base).as_posix()
            if path_prefix:
                rel = f"{self._normalize_path(path_prefix)}/{rel}"
            seen_paths.add(rel)
            status = self.index_file(collection=collection, path=rel, content=content)
            if status == "indexed":
                indexed += 1
            elif status == "updated":
                updated += 1
            elif status == "unchanged":
                unchanged += 1
            else:
                skipped += 1

        if deactivate_missing:
            removed = self.deactivate_missing(collection, seen_paths, path_prefix=path_prefix)

        return {
            "indexed": indexed,
            "updated": updated,
            "unchanged": unchanged,
            "removed": removed,
            "skipped": skipped,
        }

    def get_status(self) -> dict[str, Any]:
        total_docs = self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        active_docs = self.conn.execute("SELECT COUNT(*) FROM documents WHERE active = 1").fetchone()[0]
        total_content = self.conn.execute("SELECT COUNT(*) FROM content").fetchone()[0]
        fts_docs = self.conn.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0]
        vector_chunks = self.conn.execute("SELECT COUNT(*) FROM document_vectors").fetchone()[0]
        vector_models = self.conn.execute(
            """
            SELECT model, COUNT(*) AS chunks, COUNT(DISTINCT hash) AS documents
            FROM document_vectors
            GROUP BY model
            ORDER BY model
            """
        ).fetchall()
        collections = self.conn.execute(
            """
            SELECT collection, COUNT(*) AS total, SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END) AS active
            FROM documents
            GROUP BY collection
            ORDER BY collection
            """
        ).fetchall()
        return {
            "db_path": str(self.db_path),
            "total_documents": int(total_docs),
            "active_documents": int(active_docs),
            "content_blobs": int(total_content),
            "fts_rows": int(fts_docs),
            "vector_chunks": int(vector_chunks),
            "vector_models": [
                {"model": r["model"], "chunks": int(r["chunks"]), "documents": int(r["documents"])}
                for r in vector_models
            ],
            "collections": [
                {"name": r["collection"], "total": int(r["total"]), "active": int(r["active"] or 0)}
                for r in collections
            ],
        }

    def close(self) -> None:
        self.conn.close()

