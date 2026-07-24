"""雜湊 embedder 的單元測試 —— 純函數，無 mock、無 I/O。"""

from __future__ import annotations

import math

import pytest

from hoyabit_agent.ingest.embeddings import HashingEmbedder, cosine


async def test_the_same_text_always_embeds_to_the_same_vector() -> None:
    embedder = HashingEmbedder()
    (a,) = await embedder.embed(["Bitcoin ETF sees record inflow"])
    (b,) = await embedder.embed(["Bitcoin ETF sees record inflow"])
    assert a == b


async def test_every_vector_has_the_configured_dimension() -> None:
    embedder = HashingEmbedder(dimensions=64)
    vectors = await embedder.embed(["a", "b", "c"])
    assert len(vectors) == 3
    assert all(len(v) == 64 for v in vectors)


async def test_vectors_are_l2_normalised() -> None:
    embedder = HashingEmbedder()
    (vector,) = await embedder.embed(["Bitcoin rallies to a record high"])
    assert math.isclose(math.sqrt(sum(c * c for c in vector)), 1.0, rel_tol=1e-9)


async def test_similar_texts_are_closer_than_unrelated_ones() -> None:
    """雜湊 embedder 不懂語意，但共享 n-gram 的文字餘弦相似度較高 —— 足夠檢索。"""
    embedder = HashingEmbedder()
    a, b, c = await embedder.embed(
        [
            "Bitcoin spot ETF records the largest daily inflow",
            "Bitcoin spot ETF sees the largest daily inflow",
            "Solana validator client suffers an outage",
        ]
    )
    assert cosine(a, b) > cosine(a, c)


async def test_an_empty_batch_yields_no_vectors() -> None:
    assert await HashingEmbedder().embed([]) == ()


async def test_empty_text_embeds_to_a_zero_vector_without_crashing() -> None:
    (vector,) = await HashingEmbedder(dimensions=16).embed([""])
    assert vector == tuple([0.0] * 16)


def test_a_non_positive_dimension_is_rejected() -> None:
    with pytest.raises(ValueError):
        HashingEmbedder(dimensions=0)


def test_cosine_of_identical_vectors_is_one() -> None:
    v = (0.6, 0.8)
    assert math.isclose(cosine(v, v), 1.0)


def test_cosine_of_mismatched_dimensions_is_zero() -> None:
    assert cosine((1.0, 0.0), (1.0, 0.0, 0.0)) == 0.0
