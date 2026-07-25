from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from hoyabit_agent.domain import Asset, Facet
from hoyabit_agent.ingest.documents import MarketDocument, MarketIndicators, OhlcvBar
from hoyabit_agent.ingest.embeddings import Vector
from hoyabit_agent.ingest.historical import MarketDatasetEvidenceSource
from hoyabit_agent.ingest.memory_store import InMemoryMarketDocumentStore


class FakeQueryEmbedder:
    dimensions = 2
    model = "gemini-embedding-001"

    async def embed(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        return tuple((1.0, 0.0) for _ in texts)


def document(day: date) -> MarketDocument:
    bar = OhlcvBar(day, *(Decimal("1") for _ in range(5)))
    indicators = MarketIndicators(*(None for _ in range(9)))
    return MarketDocument(
        asset=Asset.BTC,
        as_of_date=day,
        ohlcv=(bar,),
        indicators=indicators,
        window_complete=False,
        source_file=Path("BTC_daily_ohlcv.csv"),
        source_row_start=2,
        source_row_end=2,
    )


@pytest.mark.asyncio
async def test_retrieval_never_returns_future_documents() -> None:
    store = InMemoryMarketDocumentStore()
    await store.upsert(
        [(document(date(2026, 5, 1)), (1.0, 0.0)), (document(date(2026, 5, 2)), (1.0, 0.0))],
        embedding_model="gemini-embedding-001",
    )
    found = await store.search(
        Asset.BTC,
        (1.0, 0.0),
        as_of_date=date(2026, 5, 1),
        embedding_model="gemini-embedding-001",
    )
    assert [item.as_of_date for item in found] == [date(2026, 5, 1)]


@pytest.mark.asyncio
async def test_dates_after_dataset_end_return_insufficient_evidence() -> None:
    source = MarketDatasetEvidenceSource(InMemoryMarketDocumentStore(), FakeQueryEmbedder())
    assert await source.fetch(Asset.BTC, {"as_of_date": "2026-06-01"}) == ()


@pytest.mark.asyncio
async def test_dataset_documents_only_become_technical_evidence() -> None:
    store = InMemoryMarketDocumentStore()
    item = document(date(2026, 5, 1))
    await store.upsert([(item, (1.0, 0.0))], embedding_model="gemini-embedding-001")
    source = MarketDatasetEvidenceSource(store, FakeQueryEmbedder())
    (evidence,) = await source.fetch(Asset.BTC, {"as_of_date": "2026-05-01"})
    assert evidence.facet is Facet.TECHNICAL
    assert "2026-05-31 UTC" in evidence.summary


@pytest.mark.asyncio
async def test_non_technical_query_returns_insufficient_evidence() -> None:
    source = MarketDatasetEvidenceSource(InMemoryMarketDocumentStore(), FakeQueryEmbedder())
    assert await source.fetch(Asset.BTC, {"query": "BTC ETF 新聞"}) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["SEC 對 BTC 的看法", "X 上大家看多嗎"])
async def test_ambiguous_non_technical_queries_are_conservatively_rejected(query: str) -> None:
    source = MarketDatasetEvidenceSource(InMemoryMarketDocumentStore(), FakeQueryEmbedder())
    assert await source.fetch(Asset.BTC, {"query": query}) == ()
