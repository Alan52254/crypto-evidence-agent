"""組裝 Gemini 市場證據 source 的單一 composition root。"""

from __future__ import annotations

import httpx

from hoyabit_agent.ingest.embeddings import GeminiEmbedder
from hoyabit_agent.ingest.historical import MarketDatasetEvidenceSource
from hoyabit_agent.ingest.postgres_store import PostgresMarketDocumentStore
from hoyabit_agent.models.vision import VisionModelAdapter
from hoyabit_agent.seams import EvidenceSource, ModelProvider
from hoyabit_agent.sources.binance import BinanceDerivativesSource, BinanceSpotSource
from hoyabit_agent.sources.chart_reader import ChartReaderSource
from hoyabit_agent.sources.news import NewsRssSource
from hoyabit_agent.sources.web_chart_capture import WebChartCaptureSource
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
    from hoyabit_agent.sources.rss_extended import (
        ExtendedNewsSource,
        OfficialAnnouncementSource,
    )

    sources: list[EvidenceSource] = [
        BinanceSpotSource(client),
        BinanceDerivativesSource(client),
        NewsRssSource(client, labeller=model),
        ExtendedNewsSource(client, labeller=model),
        OfficialAnnouncementSource(client, labeller=model),
    ]
    historical = await build_market_evidence_source(client)
    if historical is not None:
        sources.append(historical)

    # Vision 工具（需要 VisionModelAdapter；無 API key 時不註冊）
    vision_adapter = VisionModelAdapter.from_environment(client)
    if vision_adapter is not None:
        sources.append(ChartReaderSource(client, vision_adapter))
        sources.append(WebChartCaptureSource(client, vision_adapter))

    return sources


__all__ = ["build_competition_sources", "build_market_evidence_source"]
