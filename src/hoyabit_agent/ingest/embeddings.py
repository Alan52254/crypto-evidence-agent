"""文字向量化 —— 供背景 ingestion 與檢索使用。

這台機器不保證有 embedding 模型，所以預設是**確定性的雜湊 embedder**：
把字元 n-gram 雜湊進固定維度並做 L2 正規化。它不懂語意，但相同文字永遠
得到相同向量、相似文字（共享 n-gram）餘弦相似度較高 —— 對「找回相關的
歷史報導」這個用途已經夠用，而且**完全確定、可無 mock 測試、零依賴**。

真的要語意檢索時換 `OpenAIEmbedder`（Ollama 的 /v1/embeddings 也相容）——
兩者滿足同一個 `Embedder` 介面，可直接對調。
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

Vector = tuple[float, ...]

_TOKEN = re.compile(r"[a-z0-9]+|[一-鿿]")


@runtime_checkable
class Embedder(Protocol):
    """把一批文字轉成等長的向量。

    不變式：回傳向量數量與輸入相同、每個向量維度一致（`dimensions`）。
    """

    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: Sequence[str]) -> tuple[Vector, ...]: ...


class HashingEmbedder:
    """確定性的雜湊 embedder。無 I/O、無外部依賴。

    英文以詞、中日韓以字為單位切 token，再取相鄰 bigram 一起雜湊，
    讓「詞序相近」也能反映在向量上。
    """

    def __init__(self, dimensions: int = 256) -> None:
        if dimensions <= 0:
            raise ValueError("維度必須為正")
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        return tuple(self._one(text) for text in texts)

    def _one(self, text: str) -> Vector:
        tokens = _TOKEN.findall(text.lower())
        features = list(tokens)
        features += [f"{a}\x1f{b}" for a, b in zip(tokens, tokens[1:], strict=False)]

        vector = [0.0] * self._dimensions
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dimensions
            sign = 1.0 if digest[4] & 1 else -1.0  # 有號雜湊，減少碰撞偏差
            vector[bucket] += sign

        return _normalise(vector)


def _normalise(vector: list[float]) -> Vector:
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        return tuple(vector)
    return tuple(component / norm for component in vector)


def cosine(a: Vector, b: Vector) -> float:
    """兩個向量的餘弦相似度。維度不同時回傳 0。"""
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True))


__all__ = ["Embedder", "HashingEmbedder", "Vector", "cosine"]
