"""Embedders.

``hash`` is a deterministic, dependency-free embedder so the demo and the test
suite run offline. It is *not* a semantic model — production switches to
``openai`` (any OpenAI-compatible embeddings endpoint, e.g. BGE-M3) by setting
``NEXUS_EMBEDDING_PROVIDER=openai``.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

from app.core.config import get_settings
from app.rag.text import tokenize


class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]: ...


class HashEmbedder:
    """Hashed bag-of-tokens with sublinear term weighting, L2 normalised."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def _bucket(self, token: str) -> int:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self.dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        counts: dict[str, int] = {}
        for token in tokenize(text):
            counts[token] = counts.get(token, 0) + 1
        for token, count in counts.items():
            vec[self._bucket(token)] += 1.0 + math.log(count)
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


class OpenAIEmbedder:
    """OpenAI-compatible ``/embeddings`` client (sync, small batches)."""

    def __init__(self, base_url: str, api_key: str, model: str, dim: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        import httpx

        resp = httpx.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": text},
            timeout=30.0,
        )
        resp.raise_for_status()
        vector = resp.json()["data"][0]["embedding"]
        self.dim = len(vector)
        return vector


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        settings = get_settings()
        if settings.embedding_provider == "openai" and settings.llm_api_key:
            _embedder = OpenAIEmbedder(
                settings.llm_base_url, settings.llm_api_key, settings.embedding_model, settings.embedding_dim
            )
        else:
            _embedder = HashEmbedder(settings.embedding_dim)
    return _embedder
