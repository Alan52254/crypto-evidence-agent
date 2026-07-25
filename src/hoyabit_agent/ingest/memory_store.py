"""市場文件 store 的 in-memory adapter。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from hoyabit_agent.domain import Asset
from hoyabit_agent.ingest.documents import MarketDocument, MarketDocumentStore
from hoyabit_agent.ingest.embeddings import Vector, cosine


class InMemoryMarketDocumentStore(MarketDocumentStore):
    def __init__(self) -> None:
        self._entries: dict[tuple[str, date, str], tuple[MarketDocument, Vector]] = {}

    async def upsert(
        self,
        entries: Sequence[tuple[MarketDocument, Vector]],
        *,
        embedding_model: str,
    ) -> int:
        added = 0
        for document, vector in entries:
            key = (document.asset.value, document.as_of_date, embedding_model)
            added += key not in self._entries
            self._entries[key] = (document, vector)
        return added

    async def search(
        self,
        asset: Asset,
        query: Vector,
        *,
        as_of_date: date,
        embedding_model: str,
        limit: int = 5,
    ) -> tuple[MarketDocument, ...]:
        scored = [
            (cosine(query, vector), document)
            for (stored_asset, stored_date, model), (document, vector) in self._entries.items()
            if stored_asset == asset.value
            and stored_date <= as_of_date
            and model == embedding_model
        ]
        scored.sort(key=lambda item: (item[0], item[1].as_of_date), reverse=True)
        return tuple(document for _, document in scored[:limit])

    def __len__(self) -> int:
        return len(self._entries)


__all__ = ["InMemoryMarketDocumentStore"]
