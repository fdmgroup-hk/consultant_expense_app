"""Pluggable text embeddings.

Three providers, chosen by ``EMBEDDING_PROVIDER`` (default ``auto``):

* ``voyage``    - hosted (Voyage AI). Best quality, generous free tier, needs a key.
* ``fastembed`` - local ONNX model. No key, no network, no torch. ~90 MB download once.
* ``hashing``   - deterministic feature hashing in pure numpy. No install, no network.
                  Weaker on synonyms, which is why retrieval always fuses it with BM25.

``auto`` picks the best one actually available, so the app runs on a clean install.
"""
from __future__ import annotations

import hashlib
import logging
import re
from abc import ABC, abstractmethod
from functools import lru_cache

import numpy as np

from ..config import get_settings

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9]+")


class Embedder(ABC):
    provider: str
    model: str
    dim: int

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    @abstractmethod
    def embed_query(self, text: str) -> np.ndarray: ...

    @property
    def signature(self) -> str:
        return f"{self.provider}:{self.model}:{self.dim}"


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class HashingEmbedder(Embedder):
    """Signed feature hashing over word unigrams, bigrams and character 4-grams.

    Zero dependencies beyond numpy, fully deterministic, and good enough to be a
    useful second opinion alongside BM25 while someone decides on a real model.
    """

    provider = "hashing"

    def __init__(self, dim: int = 768) -> None:
        self.dim = dim
        self.model = f"signed-hashing-{dim}"

    @staticmethod
    def _features(text: str) -> dict[str, float]:
        words = _WORD_RE.findall(text.lower())
        counts: dict[str, float] = {}
        for word in words:
            counts[f"w:{word}"] = counts.get(f"w:{word}", 0.0) + 1.0
            if len(word) > 5:
                for i in range(len(word) - 3):
                    gram = f"c:{word[i:i + 4]}"
                    counts[gram] = counts.get(gram, 0.0) + 0.5
        for a, b in zip(words, words[1:]):
            key = f"b:{a}_{b}"
            counts[key] = counts.get(key, 0.0) + 1.0
        return counts

    def _vector(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        for feature, count in self._features(text).items():
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[index] += sign * (1.0 + np.log(count))  # sublinear term frequency
        return vec

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return _l2_normalize(np.vstack([self._vector(t) for t in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_documents([text])[0]


class FastEmbedEmbedder(Embedder):
    """Local ONNX sentence embeddings - nothing leaves the machine."""

    provider = "fastembed"

    def __init__(self, model_name: str) -> None:
        from fastembed import TextEmbedding

        self.model = model_name
        self._model = TextEmbedding(model_name=model_name)
        probe = next(iter(self._model.embed(["dimension probe"])))
        self.dim = int(np.asarray(probe).shape[0])

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vectors = np.vstack([np.asarray(v, dtype=np.float32) for v in self._model.embed(texts)])
        return _l2_normalize(vectors)

    def embed_query(self, text: str) -> np.ndarray:
        vector = np.asarray(next(iter(self._model.query_embed([text]))), dtype=np.float32)
        return _l2_normalize(vector.reshape(1, -1))[0]


class VoyageEmbedder(Embedder):
    """Hosted embeddings from Voyage AI (free tier available)."""

    provider = "voyage"
    _BATCH = 64

    def __init__(self, api_key: str, model_name: str) -> None:
        import voyageai

        self.model = model_name
        self._client = voyageai.Client(api_key=api_key)
        probe = self._client.embed(["dimension probe"], model=model_name, input_type="document")
        self.dim = len(probe.embeddings[0])

    def _embed(self, texts: list[str], input_type: str) -> np.ndarray:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._BATCH):
            batch = texts[start:start + self._BATCH]
            result = self._client.embed(batch, model=self.model, input_type=input_type)
            vectors.extend(result.embeddings)
        return _l2_normalize(np.asarray(vectors, dtype=np.float32))

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return self._embed(texts, "document")

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([text], "query")[0]


def _try_voyage() -> Embedder | None:
    settings = get_settings()
    if not settings.voyage_api_key:
        return None
    try:
        return VoyageEmbedder(settings.voyage_api_key, settings.voyage_model)
    except Exception as exc:  # missing package, bad key, network down
        logger.warning("Voyage embeddings unavailable (%s); falling back.", exc)
        return None


def _try_fastembed() -> Embedder | None:
    try:
        return FastEmbedEmbedder(get_settings().fastembed_model)
    except Exception as exc:  # package not installed, or model download failed
        logger.warning("fastembed unavailable (%s); falling back.", exc)
        return None


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    choice = get_settings().embedding_provider.lower().strip()

    if choice == "voyage":
        embedder = _try_voyage()
    elif choice == "fastembed":
        embedder = _try_fastembed()
    elif choice == "hashing":
        embedder = HashingEmbedder()
    else:  # auto
        embedder = _try_voyage() or _try_fastembed()

    if embedder is None:
        embedder = HashingEmbedder()
    logger.info("Embedding provider: %s (%s, dim=%d)", embedder.provider, embedder.model, embedder.dim)
    return embedder
