"""Gemini embedding adapter，供市場文件索引與時間截斷檢索。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from typing import Any, Literal, Protocol, runtime_checkable

import httpx

Vector = tuple[float, ...]
TaskType = Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
DEFAULT_EMBEDDING_DIMENSIONS = 768
EMBEDDING_MODEL_ENV = "GEMINI_EMBEDDING_MODEL"
EMBEDDING_DIMENSIONS_ENV = "GEMINI_EMBEDDING_DIMENSIONS"
API_KEY_ENV = "GEMINI_API_KEY"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


@runtime_checkable
class Embedder(Protocol):
    @property
    def dimensions(self) -> int: ...

    @property
    def model(self) -> str: ...

    async def embed(self, texts: Sequence[str]) -> tuple[Vector, ...]: ...


class GeminiEmbedder:
    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        *,
        model: str = DEFAULT_EMBEDDING_MODEL,
        dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS,
        task_type: TaskType = "RETRIEVAL_QUERY",
        timeout_seconds: float = 90.0,
    ) -> None:
        if dimensions <= 0:
            raise ValueError("embedding dimensions must be positive")
        self._client = client
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions
        self._task_type = task_type
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(
        cls, client: httpx.AsyncClient, *, task_type: TaskType
    ) -> GeminiEmbedder | None:
        api_key = os.environ.get(API_KEY_ENV, "").strip()
        if not api_key:
            return None
        try:
            dimensions = int(
                os.environ.get(EMBEDDING_DIMENSIONS_ENV, str(DEFAULT_EMBEDDING_DIMENSIONS))
            )
        except ValueError as error:
            raise ValueError(f"invalid {EMBEDDING_DIMENSIONS_ENV}") from error
        return cls(
            client,
            api_key,
            model=os.environ.get(EMBEDDING_MODEL_ENV, DEFAULT_EMBEDDING_MODEL).strip()
            or DEFAULT_EMBEDDING_MODEL,
            dimensions=dimensions,
            task_type=task_type,
        )

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model(self) -> str:
        return self._model

    async def embed(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        items = list(texts)
        if not items:
            return ()
        model_name = f"models/{self._model}"
        requests = [
            {
                "model": model_name,
                "content": {"parts": [{"text": text}]},
                "taskType": self._task_type,
                "outputDimensionality": self._dimensions,
            }
            for text in items
        ]
        try:
            response = await asyncio.wait_for(
                self._client.post(
                    f"{BASE_URL}/models/{self._model}:batchEmbedContents",
                    headers={"x-goog-api-key": self._api_key},
                    json={"model": model_name, "requests": requests},
                ),
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            body: Any = response.json()
        except (TimeoutError, httpx.HTTPError, ValueError) as error:
            raise RuntimeError("Gemini embedding request failed") from error
        vectors = _vectors(body)
        if len(vectors) != len(items) or any(len(vector) != self._dimensions for vector in vectors):
            raise RuntimeError(
                f"Gemini embedding response must contain {len(items)} vectors of "
                f"{self._dimensions} dimensions"
            )
        return vectors


def _vectors(body: Any) -> tuple[Vector, ...]:
    if not isinstance(body, dict) or not isinstance(body.get("embeddings"), list):
        return ()
    result: list[Vector] = []
    for embedding in body["embeddings"]:
        values = embedding.get("values") if isinstance(embedding, dict) else None
        if not isinstance(values, list):
            return ()
        try:
            result.append(tuple(float(value) for value in values))
        except (TypeError, ValueError):
            return ()
    return tuple(result)


def cosine(a: Vector, b: Vector) -> float:
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True))


__all__ = [
    "DEFAULT_EMBEDDING_DIMENSIONS",
    "DEFAULT_EMBEDDING_MODEL",
    "EMBEDDING_DIMENSIONS_ENV",
    "EMBEDDING_MODEL_ENV",
    "Embedder",
    "GeminiEmbedder",
    "TaskType",
    "Vector",
    "cosine",
]
