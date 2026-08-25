"""Ingestion pipeline: file on disk -> extracted -> chunked -> embedded -> stored."""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import db
from ..config import get_settings
from ..retrieval import search as search_index
from ..retrieval.embeddings import get_embedder
from ..storage import get_storage, object_key
from .chunk import chunk_segments
from .extract import UnsupportedFileType, extract

logger = logging.getLogger(__name__)

ROLES = ("developer", "production_support", "business_analyst", "general")


@dataclass
class DocumentMeta:
    title: str
    consultant: str | None = None
    client: str | None = None
    role: str = "general"
    placement_period: str | None = None
    tags: list[str] | None = None
    notes: str | None = None


class DuplicateDocument(ValueError):
    def __init__(self, document_id: str, title: str) -> None:
        super().__init__(f"Already in the knowledge base as '{title}'.")
        self.document_id = document_id
        self.title = title


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _embed_and_store(conn, chunk_ids: list[int], texts: list[str]) -> None:
    embedder = get_embedder()
    vectors = embedder.embed_documents(texts)
    conn.executemany(
        "INSERT INTO embeddings (chunk_id, provider, model, dim, vector) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (chunk_id) DO UPDATE SET "
        "provider = excluded.provider, model = excluded.model, "
        "dim = excluded.dim, vector = excluded.vector",
        [
            (chunk_id, embedder.provider, embedder.model, embedder.dim, vector.tobytes())
            for chunk_id, vector in zip(chunk_ids, vectors)
        ],
    )


def ingest_file(path: Path, meta: DocumentMeta, *, replace_existing: bool = False) -> dict[str, Any]:
    """Index one file. Raises ``UnsupportedFileType`` or ``DuplicateDocument``."""
    settings = get_settings()
    digest = file_sha256(path)

    with db.connection() as conn:
        existing = conn.execute(
            "SELECT id, title, object_key FROM documents WHERE sha256 = ?", (digest,)
        ).fetchone()
        if existing and not replace_existing:
            raise DuplicateDocument(existing["id"], existing["title"])
        if existing:
            stale_key = existing.get("object_key")
            conn.execute("DELETE FROM documents WHERE id = ?", (existing["id"],))
            # The replacement is stored under a fresh id, so the old object would
            # otherwise be orphaned in the bucket forever.
            if stale_key:
                get_storage().delete(stale_key)

    segments = extract(path)
    if not segments:
        raise UnsupportedFileType(
            "No readable text found. Scanned PDFs and image-only slides need OCR first."
        )

    chunks = chunk_segments(
        segments,
        target_chars=settings.chunk_target_chars,
        overlap_chars=settings.chunk_overlap_chars,
    )
    if not chunks:
        raise UnsupportedFileType("Nothing left to index after cleaning the text.")

    document_id = str(uuid.uuid4())
    suffix = path.suffix.lower()
    # Store the original before the row is written, so a storage outage fails the
    # ingest rather than leaving a document row pointing at a file that is absent.
    stored_key = get_storage().put(object_key(document_id, suffix), path)

    with db.connection() as conn:
        import json

        conn.execute(
            """
            INSERT INTO documents
                (id, title, filename, source_type, consultant, client, role,
                 placement_period, tags, notes, sha256, object_key, n_chunks, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'indexed')
            """,
            (
                document_id,
                meta.title,
                path.name,
                suffix.lstrip("."),
                meta.consultant,
                meta.client,
                meta.role if meta.role in ROLES else "general",
                meta.placement_period,
                json.dumps(meta.tags or []),
                meta.notes,
                digest,
                stored_key,
                len(chunks),
            ),
        )

        chunk_ids: list[int] = []
        for chunk in chunks:
            chunk_ids.append(conn.insert(
                "INSERT INTO chunks (document_id, ordinal, locator, heading, text) "
                "VALUES (?, ?, ?, ?, ?)",
                (document_id, chunk.ordinal, chunk.locator, chunk.heading, chunk.text),
            ))

        _embed_and_store(conn, chunk_ids, [c.text for c in chunks])
        db.bump_kb_version(conn)

    search_index.invalidate()
    logger.info("Indexed '%s' as %s (%d chunks)", meta.title, document_id, len(chunks))
    return {
        "document_id": document_id,
        "title": meta.title,
        "chunks": len(chunks),
        "source_type": path.suffix.lower().lstrip("."),
    }


def delete_document(document_id: str) -> bool:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT object_key FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        cursor = conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        deleted = cursor.rowcount > 0
        if deleted:
            db.bump_kb_version(conn)
    if deleted:
        if row and row.get("object_key"):
            get_storage().delete(row["object_key"])
        search_index.invalidate()
    return deleted


def reembed_all() -> dict[str, Any]:
    """Re-embed every chunk with the currently configured model.

    Run this after switching ``EMBEDDING_PROVIDER`` - old vectors from a different
    model are ignored by search until they are regenerated.
    """
    embedder = get_embedder()
    with db.connection() as conn:
        rows = conn.execute("SELECT id, text FROM chunks ORDER BY id").fetchall()
        if not rows:
            return {"reembedded": 0, "provider": embedder.provider, "model": embedder.model}

        batch_size = 128
        total = 0
        for start in range(0, len(rows), batch_size):
            batch = rows[start:start + batch_size]
            _embed_and_store(conn, [r["id"] for r in batch], [r["text"] for r in batch])
            total += len(batch)
        db.bump_kb_version(conn)

    search_index.invalidate()
    return {"reembedded": total, "provider": embedder.provider, "model": embedder.model}
