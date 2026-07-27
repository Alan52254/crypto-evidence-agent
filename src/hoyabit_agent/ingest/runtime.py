"""組裝 Gemini 市場證據 source 的單一 composition root。"""

from __future__ import annotations

import httpx

from hoyabit_agent.ingest.embeddings import GeminiEmbedder
from hoyabit_agent.ingest.historical import MarketDatasetEvidenceSource
from hoyabit_agent.ingest.postgres_store import PostgresMarketDocumentStore
from hoyabit_agent.seams import EvidenceSource, ModelProvider
from hoyabit_agent.sources.binance import BinanceDerivativesSource, BinanceSpotSource
from hoyabit_agent.sources.news import NewsRssSource
from hoyabit_agent.storage.postgres import reachable


async def build_market_evidence_source(
    client: httpx.AsyncClient,
) -> MarketDatasetEvidenceSource | None:
    embedder = GeminiEmbedder.from_environment(client, task_type="RETRIEVAL_QUERY")
    if embedder is None or not await reachable():
        return None
    return MarketDatasetEvidenceSource(
        PostgresMarketDocumentStore(dimensions=embedder.dimensions), embedder
    )


async def build_competition_sources(
    client: httpx.AsyncClient, model: ModelProvider
) -> list[EvidenceSource]:
    """組裝正式競賽的獨立多源證據 adapters。

    7 個獨立來源、4 面全覆蓋：
    - 技術面：Binance Spot + 歷史 OHLCV
    - 籌碼面：Binance Derivatives
    - 基本面：CoinDesk/CT + Blocktempo/Blockworks + 官方公告
    - 情緒面：新聞文本打分（中英文交叉）
    """
    from hoyabit_agent.sources.csv_historical import CsvHistoricalSource
    from hoyabit_agent.sources.rss_extended import (
        ExtendedNewsSource,
        OfficialAnnouncementSource,
    )
    from hoyabit_agent.sources.coingecko import CoinGeckoSource

    sources: list[EvidenceSource] = [
        BinanceSpotSource(client),
        BinanceDerivativesSource(client),
        NewsRssSource(client, labeller=model),
        ExtendedNewsSource(client, labeller=model),
        OfficialAnnouncementSource(client, labeller=model),
        CoinGeckoSource(client),
    ]
    # 優先用 pgvector 版本（完整語意檢索）；連不上就用 CSV 直讀版
    historical = await build_market_evidence_source(client)
    if historical is not None:
        sources.append(historical)
    else:
        sources.append(CsvHistoricalSource())
    return sources


__all__ = ["build_competition_sources", "build_market_evidence_source"]
