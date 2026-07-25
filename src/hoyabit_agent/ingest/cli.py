"""把競賽 OHLCV 資料集以 Gemini embeddings 匯入 Postgres。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

from hoyabit_agent.config import load_dotenv, run_async
from hoyabit_agent.ingest.dataset import DATASET_ENV
from hoyabit_agent.ingest.embeddings import GeminiEmbedder
from hoyabit_agent.ingest.postgres_store import PostgresMarketDocumentStore
from hoyabit_agent.ingest.service import MarketDatasetIngestionService
from hoyabit_agent.storage.postgres import database_url, reachable

DEFAULT_DATASET_DIR = (
    Path("(HOYA BIT) 命題數據集 - 2026 雲湧智生：臺灣生成式 AI 應用黑客松競賽")
    / "HOYA_BIT_crypto_market_dataset"
)


async def _ingest_once(dataset_dir: Path, batch_size: int) -> int:
    if not await reachable():
        print(f"連不到 {database_url()} —— ingestion 需要 Postgres + pgvector。")
        return 1
    async with httpx.AsyncClient(timeout=90.0) as client:
        embedder = GeminiEmbedder.from_environment(client, task_type="RETRIEVAL_DOCUMENT")
        if embedder is None:
            print("缺少 GEMINI_API_KEY；請放在被 .gitignore 排除的 .env。")
            return 2
        store = PostgresMarketDocumentStore(dimensions=embedder.dimensions)
        report = await MarketDatasetIngestionService(
            dataset_dir, embedder, store, batch_size=batch_size
        ).run_once()
    per_asset = "、".join(f"{asset}:{count}" for asset, count in report.per_asset.items())
    print(f"讀取 {report.loaded} 個窗口，新增 {report.ingested} 筆（{per_asset}）")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="以 Gemini 匯入競賽 OHLCV 資料集")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(os.environ.get(DATASET_ENV, str(DEFAULT_DATASET_DIR))),
        help="HOYA_BIT_crypto_market_dataset 目錄",
    )
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args(argv)
    return int(run_async(_ingest_once(args.dataset, args.batch_size)))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
