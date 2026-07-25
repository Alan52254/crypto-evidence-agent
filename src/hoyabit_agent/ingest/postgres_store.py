"""`market_dataset_document` 的 pgvector adapter。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from hoyabit_agent.domain import Asset
from hoyabit_agent.ingest.documents import MarketDocument, MarketIndicators, OhlcvBar
from hoyabit_agent.ingest.embeddings import Vector
from hoyabit_agent.storage.postgres import database_url


def _schema(dimensions: int) -> str:
    return f"""
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE TABLE IF NOT EXISTS market_dataset_document (
        asset                  TEXT NOT NULL,
        as_of_date             DATE NOT NULL,
        window_complete        BOOLEAN NOT NULL,
        ohlcv                  JSONB NOT NULL,
        indicators             JSONB NOT NULL,
        source_file            TEXT NOT NULL,
        source_row_start       INTEGER NOT NULL,
        source_row_end         INTEGER NOT NULL,
        embedding_model        TEXT NOT NULL,
        embedding_dimensions   INTEGER NOT NULL CHECK (embedding_dimensions = {dimensions}),
        embedding              vector({dimensions}) NOT NULL,
        PRIMARY KEY (asset, as_of_date, embedding_model)
    );
    CREATE INDEX IF NOT EXISTS market_document_asset_date
        ON market_dataset_document (asset, as_of_date DESC);
    """


def _literal(vector: Vector) -> str:
    return "[" + ",".join(repr(float(component)) for component in vector) + "]"


class PostgresMarketDocumentStore:
    def __init__(self, dimensions: int = 768, url: str | None = None) -> None:
        self._dimensions = dimensions
        self._url = url or database_url()

    async def _connect(self) -> psycopg.AsyncConnection[Any]:
        return await psycopg.AsyncConnection.connect(self._url, row_factory=dict_row)

    async def migrate(self) -> None:
        async with await self._connect() as connection:
            await connection.execute(_schema(self._dimensions))
            await connection.commit()

    async def upsert(
        self,
        entries: Sequence[tuple[MarketDocument, Vector]],
        *,
        embedding_model: str,
    ) -> int:
        items = list(entries)
        if any(len(vector) != self._dimensions for _, vector in items):
            raise ValueError(f"all embeddings must have {self._dimensions} dimensions")
        await self.migrate()
        keys = [(doc.asset.value, doc.as_of_date, embedding_model) for doc, _ in items]
        async with await self._connect() as connection:
            existing = await self._existing(connection, keys)
            async with connection.transaction():
                for document, vector in items:
                    await connection.execute(
                        "INSERT INTO market_dataset_document "
                        "(asset, as_of_date, window_complete, ohlcv, indicators, source_file, "
                        "source_row_start, source_row_end, embedding_model, "
                        "embedding_dimensions, embedding) "
                        "VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s::vector) "
                        "ON CONFLICT (asset, as_of_date, embedding_model) DO UPDATE SET "
                        "window_complete=EXCLUDED.window_complete, ohlcv=EXCLUDED.ohlcv, "
                        "indicators=EXCLUDED.indicators, source_file=EXCLUDED.source_file, "
                        "source_row_start=EXCLUDED.source_row_start, "
                        "source_row_end=EXCLUDED.source_row_end, "
                        "embedding_dimensions=EXCLUDED.embedding_dimensions, "
                        "embedding=EXCLUDED.embedding",
                        (
                            document.asset.value,
                            document.as_of_date,
                            document.window_complete,
                            json.dumps([_bar_json(bar) for bar in document.ohlcv]),
                            json.dumps(vars(document.indicators), allow_nan=False),
                            str(document.source_file),
                            document.source_row_start,
                            document.source_row_end,
                            embedding_model,
                            self._dimensions,
                            _literal(vector),
                        ),
                    )
            await connection.commit()
        return sum(1 for key in set(keys) if key not in existing)

    async def search(
        self,
        asset: Asset,
        query: Vector,
        *,
        as_of_date: date,
        embedding_model: str,
        limit: int = 5,
    ) -> tuple[MarketDocument, ...]:
        if len(query) != self._dimensions:
            raise ValueError(f"query embedding must have {self._dimensions} dimensions")
        await self.migrate()
        async with await self._connect() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT * FROM market_dataset_document "
                    "WHERE asset=%s AND as_of_date<=%s AND embedding_model=%s "
                    "AND embedding_dimensions=%s "
                    "ORDER BY embedding <=> %s::vector LIMIT %s",
                    (
                        asset.value,
                        as_of_date,
                        embedding_model,
                        self._dimensions,
                        _literal(query),
                        limit,
                    ),
                )
                rows = await cursor.fetchall()
        return tuple(_to_document(row) for row in rows)

    @staticmethod
    async def _existing(
        connection: psycopg.AsyncConnection[Any],
        keys: Sequence[tuple[str, date, str]],
    ) -> set[tuple[str, date, str]]:
        if not keys:
            return set()
        requested = set(keys)
        model = keys[0][2]
        async with connection.cursor() as cursor:
            await cursor.execute(
                "SELECT asset, as_of_date, embedding_model FROM market_dataset_document "
                "WHERE embedding_model=%s AND asset=ANY(%s) AND as_of_date=ANY(%s)",
                (model, list({key[0] for key in keys}), list({key[1] for key in keys})),
            )
            existing = {
                (str(row["asset"]), row["as_of_date"], str(row["embedding_model"]))
                for row in await cursor.fetchall()
            }
        return existing & requested


def _bar_json(bar: OhlcvBar) -> dict[str, str]:
    return {
        "date": bar.date.isoformat(),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
    }


def _to_document(row: dict[str, Any]) -> MarketDocument:
    raw_bars = row["ohlcv"]
    raw_indicators = row["indicators"]
    return MarketDocument(
        asset=Asset(row["asset"]),
        as_of_date=row["as_of_date"],
        ohlcv=tuple(
            OhlcvBar(
                date=date.fromisoformat(item["date"]),
                open=Decimal(item["open"]),
                high=Decimal(item["high"]),
                low=Decimal(item["low"]),
                close=Decimal(item["close"]),
                volume=Decimal(item["volume"]),
            )
            for item in raw_bars
        ),
        indicators=MarketIndicators(**raw_indicators),
        window_complete=bool(row["window_complete"]),
        source_file=Path(row["source_file"]),
        source_row_start=int(row["source_row_start"]),
        source_row_end=int(row["source_row_end"]),
    )


__all__ = ["PostgresMarketDocumentStore"]
