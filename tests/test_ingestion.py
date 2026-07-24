"""背景 ingestion 與歷史檢索測試 —— 用記憶體向量庫，多數 hermetic。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

import pytest

from hoyabit_agent.domain import Asset, LabelAspect
from hoyabit_agent.ingest.embeddings import HashingEmbedder
from hoyabit_agent.ingest.historical import HistoricalEvidenceSource
from hoyabit_agent.ingest.memory_store import InMemoryVectorStore
from hoyabit_agent.ingest.service import IngestionService
from hoyabit_agent.sources.news import Article

NOW = datetime(2026, 7, 23, tzinfo=UTC)


def article(title: str, link: str, outlet: str = "cointelegraph", summary: str = "") -> Article:
    return Article(title=title, link=link, published=NOW, summary=summary, outlet=outlet)


def feed(
    mapping: dict[Asset, Sequence[Article]],
) -> Callable[[Asset], Awaitable[Sequence[Article]]]:
    async def fetch(asset: Asset) -> Sequence[Article]:
        return mapping.get(asset, ())

    return fetch


# --------------------------------------------------------------------------
# ingestion
# --------------------------------------------------------------------------


async def test_ingesting_writes_documents_into_the_store() -> None:
    store = InMemoryVectorStore()
    service = IngestionService(
        feed({Asset.BTC: [article("Bitcoin ETF inflow", "https://a.test/1")]}),
        HashingEmbedder(),
        store,
    )
    report = await service.run_once([Asset.BTC])

    assert report.ingested == 1
    assert len(store) == 1


async def test_ingestion_is_idempotent_across_runs() -> None:
    """重複輪詢同一則新聞不會產生重複文件。"""
    store = InMemoryVectorStore()
    articles: dict[Asset, Sequence[Article]] = {
        Asset.BTC: [article("Bitcoin ETF inflow", "https://a.test/1")]
    }
    service = IngestionService(feed(articles), HashingEmbedder(), store)

    first = await service.run_once([Asset.BTC])
    second = await service.run_once([Asset.BTC])

    assert first.ingested == 1
    assert second.ingested == 0  # 第二輪沒有新東西
    assert len(store) == 1


async def test_the_same_story_from_two_outlets_is_kept_as_two_documents() -> None:
    """轉載在庫裡各留一份（不同媒體），歸併留到檢索時做。"""
    store = InMemoryVectorStore()
    service = IngestionService(
        feed(
            {
                Asset.BTC: [
                    article("Bitcoin spot ETF records inflow", "https://a.test/1", "cointelegraph"),
                    article("Bitcoin spot ETF sees inflow", "https://b.test/2", "coindesk"),
                ]
            }
        ),
        HashingEmbedder(),
        store,
    )
    await service.run_once([Asset.BTC])
    assert len(store) == 2


async def test_sentiment_is_scored_at_ingest_time_when_a_labeller_is_given() -> None:
    """打分在背景就付掉，檢索路徑上不必再花壁鐘預算。"""

    class Bullish:
        async def label(
            self, texts: Sequence[str], aspect: LabelAspect = LabelAspect.SENTIMENT
        ) -> tuple[float, ...]:
            return tuple(0.9 for _ in texts)

    store = InMemoryVectorStore()
    service = IngestionService(
        feed({Asset.BTC: [article("Bitcoin rallies", "https://a.test/1")]}),
        HashingEmbedder(),
        store,
        labeller=Bullish(),
    )
    await service.run_once([Asset.BTC])

    found = await HistoricalEvidenceSource(store, HashingEmbedder()).fetch(Asset.BTC, {})
    assert found[0].stance_hint == pytest.approx(0.9)


async def test_no_articles_yields_an_empty_but_valid_report() -> None:
    service = IngestionService(feed({}), HashingEmbedder(), InMemoryVectorStore())
    report = await service.run_once([Asset.BTC, Asset.ETH])
    assert report.ingested == 0
    assert report.per_asset == {"BTC": 0, "ETH": 0}


# --------------------------------------------------------------------------
# 歷史檢索
# --------------------------------------------------------------------------


async def _ingest(store: InMemoryVectorStore, *articles: tuple[Asset, Article]) -> None:
    embedder = HashingEmbedder()
    for asset, art in articles:
        service = IngestionService(feed({asset: [art]}), embedder, store)
        await service.run_once([asset])


async def test_retrieval_returns_historical_evidence_with_provenance() -> None:
    store = InMemoryVectorStore()
    await _ingest(store, (Asset.BTC, article("Bitcoin ETF inflow hits record", "https://a.test/1")))

    found = await HistoricalEvidenceSource(store, HashingEmbedder()).fetch(
        Asset.BTC, {"query": "ETF inflow"}
    )

    assert found
    assert found[0].id.startswith("HIST-")
    assert "歷史" in found[0].summary
    assert found[0].excerpts[0].url == "https://a.test/1"
    assert "原發布於" in found[0].excerpts[0].locator


async def test_retrieval_only_returns_the_requested_asset() -> None:
    store = InMemoryVectorStore()
    await _ingest(
        store,
        (Asset.BTC, article("Bitcoin news", "https://a.test/1")),
        (Asset.ETH, article("Ethereum news", "https://b.test/2")),
    )

    found = await HistoricalEvidenceSource(store, HashingEmbedder()).fetch(Asset.ETH, {})
    assert all(item.excerpts[0].url == "https://b.test/2" for item in found)


async def test_retrieval_ranks_the_more_relevant_document_first() -> None:
    store = InMemoryVectorStore()
    await _ingest(
        store,
        (Asset.BTC, article("Bitcoin spot ETF records largest daily inflow", "https://a.test/1")),
        (Asset.BTC, article("Bitcoin miner capitulation deepens sharply", "https://a.test/2")),
    )

    found = await HistoricalEvidenceSource(store, HashingEmbedder()).fetch(
        Asset.BTC, {"query": "spot ETF daily inflow record", "limit": 2}
    )
    assert found[0].excerpts[0].url == "https://a.test/1"


async def test_an_empty_store_yields_no_historical_evidence() -> None:
    found = await HistoricalEvidenceSource(InMemoryVectorStore(), HashingEmbedder()).fetch(
        Asset.BTC, {}
    )
    assert found == ()


async def test_the_limit_caps_how_many_documents_come_back() -> None:
    store = InMemoryVectorStore()
    await _ingest(
        store,
        *[(Asset.BTC, article(f"Bitcoin story {i}", f"https://a.test/{i}")) for i in range(8)],
    )

    found = await HistoricalEvidenceSource(store, HashingEmbedder()).fetch(
        Asset.BTC, {"limit": 3}
    )
    assert len(found) <= 3


async def test_the_source_exposes_a_tool_spec_named_for_mcp() -> None:
    source = HistoricalEvidenceSource(InMemoryVectorStore(), HashingEmbedder())
    assert source.spec.name == "historical_context"
    assert "query" in source.spec.parameters["properties"]


# --------------------------------------------------------------------------
# pgvector 實作（需 Postgres）
# --------------------------------------------------------------------------


@pytest.mark.postgres
async def test_the_postgres_store_round_trips_and_ranks() -> None:
    from hoyabit_agent.ingest.postgres_store import PostgresVectorStore
    from hoyabit_agent.storage.postgres import database_url

    embedder = HashingEmbedder(dimensions=64)
    store = PostgresVectorStore(dimensions=64, url=database_url())
    await store.reset()

    service = IngestionService(
        feed(
            {
                Asset.BTC: [
                    article("Bitcoin spot ETF records largest inflow", "https://a.test/1"),
                    article("Bitcoin miner capitulation deepens", "https://a.test/2"),
                ]
            }
        ),
        embedder,
        store,
    )
    report = await service.run_once([Asset.BTC])
    assert report.ingested == 2

    (vector,) = await embedder.embed(["spot ETF inflow record"])
    ranked = await store.search(Asset.BTC, vector, limit=2)
    assert ranked[0].url == "https://a.test/1"

    # 冪等：再跑一次不新增
    assert (await service.run_once([Asset.BTC])).ingested == 0
