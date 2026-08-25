"""Hybrid retrieval: BM25 keyword search fused with vector similarity.

Neither signal alone is enough here. BM25 nails the jargon consultants actually
use ("T+2", "FIX 35=D", "PnL break"); vectors catch the paraphrases a nervous
candidate types ("what happens after a trade is agreed"). Reciprocal Rank Fusion
combines the two rankings without needing the scores on a common scale.
"""
from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .. import db
from ..config import get_settings
from .embeddings import get_embedder

_WORD_RE = re.compile(r"[a-z0-9]+")
_RRF_K = 60

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "do", "does",
    "for", "from", "had", "has", "have", "how", "i", "if", "in", "is", "it", "its",
    "of", "on", "or", "that", "the", "their", "them", "then", "there", "these",
    "they", "this", "to", "was", "were", "what", "when", "which", "who", "why",
    "will", "with", "you", "your",
}


def tokenize(text: str) -> list[str]:
    tokens = []
    for raw in _WORD_RE.findall(text.lower()):
        if raw in _STOPWORDS or len(raw) < 2:
            continue
        # Very light suffix stripping - enough to match plurals without a stemmer dep.
        if len(raw) > 4 and raw.endswith("ies"):
            raw = raw[:-3] + "y"
        elif len(raw) > 3 and raw.endswith("es") and not raw.endswith("ses"):
            raw = raw[:-2]
        elif len(raw) > 3 and raw.endswith("s") and not raw.endswith("ss"):
            raw = raw[:-1]
        tokens.append(raw)
    return tokens


@dataclass
class Hit:
    chunk_id: int
    document_id: str
    text: str
    locator: str
    heading: str
    document_title: str
    client: str | None
    role: str | None
    consultant: str | None
    placement_period: str | None
    score: float
    matched_by: list[str] = field(default_factory=list)

    def as_citation(self, index: int) -> dict[str, Any]:
        return {
            "index": index,
            "document_id": self.document_id,
            "title": self.document_title,
            "locator": self.locator,
            "heading": self.heading,
            "client": self.client,
            "role": self.role,
            "consultant": self.consultant,
        }


class _Index:
    """In-memory BM25 + vector index over every chunk in the knowledge base."""

    def __init__(self) -> None:
        self.version = -1
        self.embedder_signature = ""
        self.chunk_ids: list[int] = []
        self.rows: list[dict[str, Any]] = []
        self.doc_tokens: list[dict[str, int]] = []
        self.doc_len: np.ndarray = np.zeros(0, dtype=np.float32)
        self.avg_len = 1.0
        self.df: dict[str, int] = {}
        self.n_chunks = 0
        self.vectors: np.ndarray | None = None
        self.vector_positions: list[int] = []
        self.missing_embeddings = 0

    def build(self) -> None:
        embedder = get_embedder()
        with db.connection() as conn:
            meta = conn.execute("SELECT value FROM kb_meta WHERE key='version'").fetchone()
            version = int(meta["value"]) if meta else 0
            rows = conn.execute(
                """
                SELECT c.id AS chunk_id, c.document_id, c.locator, c.heading, c.text,
                       d.title AS document_title, d.client, d.role, d.consultant,
                       d.placement_period
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                ORDER BY c.id
                """
            ).fetchall()
            emb_rows = conn.execute(
                "SELECT chunk_id, dim, provider, model, vector FROM embeddings"
            ).fetchall()

        self.version = version
        self.embedder_signature = embedder.signature
        self.rows = [dict(r) for r in rows]
        self.chunk_ids = [r["chunk_id"] for r in self.rows]

        # --- BM25 statistics ---
        self.doc_tokens = []
        self.df = {}
        lengths: list[int] = []
        for row in self.rows:
            searchable = f"{row['document_title']} {row['heading']} {row['text']}"
            counts: dict[str, int] = {}
            tokens = tokenize(searchable)
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1
            self.doc_tokens.append(counts)
            lengths.append(len(tokens))
            for token in counts:
                self.df[token] = self.df.get(token, 0) + 1
        self.doc_len = np.asarray(lengths, dtype=np.float32)
        self.n_chunks = len(self.rows)
        self.avg_len = float(self.doc_len.mean()) if self.n_chunks else 1.0

        # --- vector matrix (only embeddings produced by the active model) ---
        wanted = (embedder.provider, embedder.model, embedder.dim)
        by_chunk = {
            r["chunk_id"]: r["vector"]
            for r in emb_rows
            if (r["provider"], r["model"], r["dim"]) == wanted
        }
        positions: list[int] = []
        vectors: list[np.ndarray] = []
        for position, chunk_id in enumerate(self.chunk_ids):
            blob = by_chunk.get(chunk_id)
            if blob is None:
                continue
            positions.append(position)
            vectors.append(np.frombuffer(blob, dtype=np.float32))
        self.vector_positions = positions
        self.vectors = np.vstack(vectors) if vectors else None
        self.missing_embeddings = self.n_chunks - len(positions)

    def bm25(self, query_tokens: list[str], allowed: np.ndarray) -> list[tuple[int, float]]:
        if not query_tokens or self.n_chunks == 0:
            return []
        k1, b = 1.5, 0.75
        scores = np.zeros(self.n_chunks, dtype=np.float32)
        for token in set(query_tokens):
            df = self.df.get(token)
            if not df:
                continue
            idf = math.log(1 + (self.n_chunks - df + 0.5) / (df + 0.5))
            for position, counts in enumerate(self.doc_tokens):
                tf = counts.get(token)
                if not tf:
                    continue
                norm = 1 - b + b * (self.doc_len[position] / self.avg_len)
                scores[position] += idf * (tf * (k1 + 1)) / (tf + k1 * norm)
        scores = np.where(allowed, scores, 0.0)
        ranked = np.argsort(-scores)
        return [(int(i), float(scores[i])) for i in ranked if scores[i] > 0]

    def vector_search(
        self, query_vector: np.ndarray, allowed: np.ndarray
    ) -> list[tuple[int, float]]:
        if self.vectors is None or self.vectors.size == 0:
            return []
        sims = self.vectors @ query_vector  # both sides are L2-normalised
        results: list[tuple[int, float]] = []
        for offset, position in enumerate(self.vector_positions):
            if allowed[position]:
                results.append((position, float(sims[offset])))
        results.sort(key=lambda item: -item[1])
        return results


_index = _Index()
_index_lock = threading.Lock()


def get_index() -> _Index:
    """Return the index, rebuilding it if the knowledge base or model changed."""
    current_version = db.kb_version()
    signature = get_embedder().signature
    if _index.version != current_version or _index.embedder_signature != signature:
        with _index_lock:
            if _index.version != current_version or _index.embedder_signature != signature:
                _index.build()
    return _index


def invalidate() -> None:
    with _index_lock:
        _index.version = -1


def search(
    query: str,
    top_k: int | None = None,
    role: str | None = None,
    client: str | None = None,
    document_ids: list[str] | None = None,
) -> list[Hit]:
    settings = get_settings()
    top_k = top_k or settings.retrieval_top_k
    index = get_index()
    if index.n_chunks == 0 or not query.strip():
        return []

    allowed = np.ones(index.n_chunks, dtype=bool)
    if role or client or document_ids:
        for position, row in enumerate(index.rows):
            if role and (row["role"] or "").lower() != role.lower():
                allowed[position] = False
            elif client and (row["client"] or "").lower() != client.lower():
                allowed[position] = False
            elif document_ids and row["document_id"] not in document_ids:
                allowed[position] = False
    if not allowed.any():
        return []

    pool = max(top_k * 4, 24)
    keyword_ranking = index.bm25(tokenize(query), allowed)[:pool]
    vector_ranking = index.vector_search(get_embedder().embed_query(query), allowed)[:pool]

    fused: dict[int, float] = {}
    matched: dict[int, list[str]] = {}
    for rank, (position, _score) in enumerate(keyword_ranking):
        fused[position] = fused.get(position, 0.0) + 1.0 / (_RRF_K + rank + 1)
        matched.setdefault(position, []).append("keyword")
    for rank, (position, _score) in enumerate(vector_ranking):
        fused[position] = fused.get(position, 0.0) + 1.0 / (_RRF_K + rank + 1)
        matched.setdefault(position, []).append("semantic")

    ordered = sorted(fused.items(), key=lambda item: -item[1])[:top_k]
    hits: list[Hit] = []
    for position, score in ordered:
        row = index.rows[position]
        hits.append(
            Hit(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                text=row["text"],
                locator=row["locator"] or "",
                heading=row["heading"] or "",
                document_title=row["document_title"],
                client=row["client"],
                role=row["role"],
                consultant=row["consultant"],
                placement_period=row["placement_period"],
                score=round(score, 6),
                matched_by=matched.get(position, []),
            )
        )
    return hits


def index_stats() -> dict[str, Any]:
    index = get_index()
    embedder = get_embedder()
    return {
        "chunks": index.n_chunks,
        "embedded_chunks": len(index.vector_positions),
        "missing_embeddings": index.missing_embeddings,
        "embedding_provider": embedder.provider,
        "embedding_model": embedder.model,
        "embedding_dim": embedder.dim,
        "kb_version": index.version,
    }
