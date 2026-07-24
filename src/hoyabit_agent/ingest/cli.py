"""`hoyabit-ingest` —— 把近期新聞收進向量庫。

跑一次（`--once`，預設）或持續輪詢（`--interval 秒`）。**這要賽前提前上線**，
Demo 當天向量庫才不是空的。需要 Postgres（向量庫）；連不到就明白報錯，
因為 ingestion 沒有資料庫就毫無意義。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

from hoyabit_agent.domain import Asset
from hoyabit_agent.ingest.embeddings import HashingEmbedder
from hoyabit_agent.ingest.postgres_store import PostgresVectorStore
from hoyabit_agent.ingest.service import IngestionService
from hoyabit_agent.models.gemini import GeminiProvider
from hoyabit_agent.models.local import LocalOpenAIProvider
from hoyabit_agent.sources.news import Article, NewsRssSource
from hoyabit_agent.storage.postgres import database_url, reachable

EMBEDDING_DIMENSIONS = 256
COVERED = tuple(Asset)


async def _ingest_once(interval: float | None) -> int:
    if not await reachable():
        print(f"連不到 {database_url()} —— ingestion 需要向量庫。")
        print("先起一個：docker run -d --name hoyabit-pg -e POSTGRES_PASSWORD=hoyabit "
              "-e POSTGRES_DB=hoyabit -p 5433:5432 pgvector/pgvector:pg16")
        return 1

    async with httpx.AsyncClient(
        timeout=30.0,
        headers={"user-agent": "hoyabit-agent/0.1 (ingest)"},
        follow_redirects=True,
    ) as client:
        news = NewsRssSource(client)
        labeller = LocalOpenAIProvider.from_environment(client) or GeminiProvider.from_environment(
            client
        )

        async def fetch(asset: Asset) -> tuple[Article, ...]:
            return await news.articles(asset)

        service = IngestionService(
            fetch,
            HashingEmbedder(dimensions=EMBEDDING_DIMENSIONS),
            PostgresVectorStore(dimensions=EMBEDDING_DIMENSIONS),
            labeller=labeller,
        )

        while True:
            report = await service.run_once(COVERED)
            per = "、".join(f"{k}:{v}" for k, v in report.per_asset.items())
            print(f"取回 {report.fetched} 篇，新增 {report.ingested} 筆（{per}）")
            if interval is None:
                return 0
            await asyncio.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把近期新聞收進向量庫")
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        metavar="SEC",
        help="持續輪詢的間隔秒數；省略則只跑一次",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_ingest_once(args.interval))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
