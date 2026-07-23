"""測試共用設定。

需要真實 Postgres 的測試標記為 `@pytest.mark.postgres`，連不到資料庫時
**自動跳過** —— 這樣 `uv run pytest` 在任何機器上都是綠的，
新加入的人不裝 docker 也能開發，但只要有資料庫就會真的驗證 schema。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from hoyabit_agent.storage.postgres import PostgresAnalysisStore, database_url, reachable

START_POSTGRES = (
    "docker run -d --name hoyabit-pg -e POSTGRES_PASSWORD=hoyabit "
    "-e POSTGRES_DB=hoyabit -p 5433:5432 pgvector/pgvector:pg16"
)


@pytest.fixture
async def store() -> AsyncIterator[PostgresAnalysisStore]:
    """一個 schema 乾淨的 store。每個測試都從空的資料庫開始。"""
    url = database_url()
    if not await reachable(url):
        pytest.skip(f"連不到 {url} —— 跳過需要 Postgres 的測試。\n要跑它們：{START_POSTGRES}")

    built = PostgresAnalysisStore(url)
    await built.reset()
    try:
        yield built
    finally:
        await built.close()
