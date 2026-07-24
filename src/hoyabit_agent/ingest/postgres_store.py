"""pgvector 向量庫 —— `VectorStore` 的正式實作。

向量以字面量 `'[...]'::vector` 傳遞，因此不需要 pgvector 的 Python 套件，
只要資料庫裝了 vector 擴充即可。維度必須與 embedder 一致。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg.rows import dict_row

from hoyabit_agent.domain import Asset, Facet
from hoyabit_agent.ingest.documents import Document
from hoyabit_agent.ingest.embeddings import Vector
from hoyabit_agent.storage.postgres import database_url


def _schema(dimensions: int) -> str:
    return f"""
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE TABLE IF NOT EXISTS ingested_document (
        id            TEXT PRIMARY KEY,
        asset         TEXT NOT NULL,
        facet         TEXT NOT NULL,
        stance_hint   DOUBLE PRECISION NOT NULL,
        text          TEXT NOT NULL,
        url           TEXT NOT NULL,
        published_at  TIMESTAMPTZ NOT NULL,
        source_id     TEXT NOT NULL,
        event_key     TEXT,
        embedding     vector({dimensions}) NOT NULL
    );
    CREATE INDEX IF NOT EXISTS document_by_asset ON ingested_document (asset);
    """


def _literal(vector: Vector) -> str:
    return "[" + ",".join(repr(float(component)) for component in vector) + "]"


class PostgresVectorStore:
    def __init__(self, dimensions: int, url: str | None = None) -> None:
        self._dimensions = dimensions
        self._url = url or database_url()

    async def _connect(self) -> psycopg.AsyncConnection[Any]:
        return await psycopg.AsyncConnection.connect(self._url, row_factory=dict_row)

    async def migrate(self) -> None:
        async with await self._connect() as connection:
            await connection.execute(_schema(self._dimensions))
            await connection.commit()

    async def reset(self) -> None:
        """清空並重建。只給測試用。"""
        async with await self._connect() as connection:
            await connection.execute("DROP TABLE IF EXISTS ingested_document")
            await connection.execute(_schema(self._dimensions))
            await connection.commit()

    async def upsert(self, entries: Sequence[tuple[Document, Vector]]) -> int:
        await self.migrate()
        entries = list(entries)
        ids = [document.id for document, _ in entries]
        async with await self._connect() as connection:
            existing = await self._existing_ids(connection, ids)
            async with connection.transaction():
                for document, vector in entries:
                    await connection.execute(
                        "INSERT INTO ingested_document (id, asset, facet, stance_hint, text,"
                        " url, published_at, source_id, event_key, embedding)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                        " ON CONFLICT (id) DO UPDATE SET"
                        " stance_hint = EXCLUDED.stance_hint, text = EXCLUDED.text,"
                        " embedding = EXCLUDED.embedding",
                        (
                            document.id,
                            document.asset.value,
                            document.facet.value,
                            document.stance_hint,
                            document.text,
                            document.url,
                            document.published_at,
                            document.source_id,
                            document.event_key,
                            _literal(vector),
                        ),
                    )
            await connection.commit()
        return sum(1 for doc_id in set(ids) if doc_id not in existing)

    @staticmethod
    async def _existing_ids(
        connection: psycopg.AsyncConnection[Any], ids: Sequence[str]
    ) -> set[str]:
        if not ids:
            return set()
        async with connection.cursor() as cursor:
            await cursor.execute(
                "SELECT id FROM ingested_document WHERE id = ANY(%s)", (list(ids),)
            )
            return {str(row["id"]) for row in await cursor.fetchall()}

    async def search(
        self, asset: Asset, query: Vector, limit: int = 5
    ) -> tuple[Document, ...]:
        await self.migrate()
        async with await self._connect() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT * FROM ingested_document WHERE asset = %s"
                    " ORDER BY embedding <=> %s::vector LIMIT %s",
                    (asset.value, _literal(query), limit),
                )
                rows = await cursor.fetchall()
        return tuple(_to_document(row) for row in rows)


def _to_document(row: dict[str, Any]) -> Document:
    return Document(
        id=row["id"],
        asset=Asset(row["asset"]),
        facet=Facet(row["facet"]),
        stance_hint=row["stance_hint"],
        text=row["text"],
        url=row["url"],
        published_at=row["published_at"],
        source_id=row["source_id"],
        event_key=row["event_key"],
    )


__all__ = ["PostgresVectorStore"]
