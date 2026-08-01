"""組裝 Gemini 市場證據 source 的單一 composition root。"""

from __future__ import annotations

import logging
import httpx

from hoyabit_agent.ingest.embeddings import GeminiEmbedder
from hoyabit_agent.ingest.historical import MarketDatasetEvidenceSource
from hoyabit_agent.ingest.postgres_store import PostgresMarketDocumentStore
from hoyabit_agent.models.vision import VisionModelAdapter
from hoyabit_agent.seams import EvidenceSource, ModelProvider
from hoyabit_agent.sources.binance import BinanceDerivativesSource, BinanceSpotSource
from hoyabit_agent.sources.cached_source import wrap_with_cache
from hoyabit_agent.sources.chart_reader import ChartReaderSource
from hoyabit_agent.sources.news import NewsRssSource
from hoyabit_agent.sources.web_chart_capture import WebChartCaptureSource
from hoyabit_agent.storage.cache_dynamodb import get_cache
from hoyabit_agent.storage.postgres import reachable

logger = logging.getLogger(__name__)


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
    - 技術面：Binance Spot + 歷史 OHLCV（直讀 CSV，不需要 PostgreSQL）
    - 籌碼面：Binance Derivatives
    - 基本面：CoinDesk/CT + Blocktempo/Blockworks + 官方公告
    - 情緒面：新聞文本打分（中英文交叉）
    """
    from hoyabit_agent.sources.csv_historical import CsvHistoricalSource
    from hoyabit_agent.sources.rss_extended import (
        ExtendedNewsSource,
        OfficialAnnouncementSource,
    )
    from hoyabit_agent.sources.candlestick_builder import CandlestickBuilderSource
    from hoyabit_agent.sources.chart_ocr import ChartOCRSource
    from hoyabit_agent.sources.coingecko import CoinGeckoSource
    from hoyabit_agent.sources.defillama import DefiLlamaSource
    from hoyabit_agent.sources.fear_greed import FearGreedSource
    from hoyabit_agent.sources.fred import FredMacroSource

    sources: list[EvidenceSource] = [
        BinanceSpotSource(client),
        BinanceDerivativesSource(client),
        NewsRssSource(client, labeller=model),
        ExtendedNewsSource(client, labeller=model),
        OfficialAnnouncementSource(client, labeller=model),
        CoinGeckoSource(client),
        DefiLlamaSource(client),
        FearGreedSource(client),
        FredMacroSource(client),
        ChartOCRSource(client, model_provider=model),
        CandlestickBuilderSource(client),
    ]
    # 優先用 pgvector 版本（完整語意檢索）；連不上就用 CSV 直讀版
    historical = await build_market_evidence_source(client)
    if historical is not None:
        sources.append(historical)
    else:
        sources.append(CsvHistoricalSource())

    # Vision 工具（需要 VisionModelAdapter；無 API key 時不註冊）
    vision_adapter = VisionModelAdapter.from_environment(client)
    if vision_adapter is not None:
        sources.append(ChartReaderSource(client, vision_adapter))
        sources.append(WebChartCaptureSource(client, vision_adapter))

    # ─── DynamoDB 快取初始化（靜默降級；boto3 未安裝或連不到都不阻斷）───
    cache = get_cache()
    try:
        cache.ensure_tables_exist()
    except Exception as exc:
        logger.warning("[runtime] DynamoDB table init failed (non-blocking): %s", exc)

    # Athena 歷史資料倉儲（S3 + Glue + Athena）—— 有 AWS 認證時自動啟用
    # 包裝 DynamoDB 快取：昂貴的 SQL 查詢結果快取 5 分鐘
    try:
        from hoyabit_agent.sources.athena import AthenaEvidenceSource

        athena_source = AthenaEvidenceSource()
        sources.append(wrap_with_cache(athena_source, cache=cache, ttl_seconds=300))
    except Exception:
        pass  # 沒有 boto3 或 AWS 認證時靜默跳過

    # Kinesis 即時串流（Binance WS → Kinesis → Evidence）—— 有 AWS 認證時自動啟用
    # 包裝 DynamoDB 快取：即時價格快取 60 秒（短 TTL 保持即時性）
    try:
        from hoyabit_agent.sources.kinesis_stream import KinesisEvidenceSource

        kinesis_source = KinesisEvidenceSource()
        sources.append(wrap_with_cache(kinesis_source, cache=cache, ttl_seconds=60))
    except Exception:
        pass  # 沒有 boto3 或 AWS 認證時靜默跳過

    return sources


__all__ = ["build_competition_sources", "build_market_evidence_source"]
