"""記憶體向量庫 —— 讓 ingestion 與檢索能在不起 Postgres 的情況下測試。

搜尋用純 Python 算餘弦。文件量小時完全夠用，且行為與 Postgres 版一致
（同一組 `VectorStore` 不變式），因此測試可以互換。
"""

from __future__ import annotations

from collections.abc import Sequence

from hoyabit_agent.domain import Asset
from hoyabit_agent.ingest.documents import Document, VectorStore
from hoyabit_agent.ingest.embeddings import Vector, cosine


class InMemoryVectorStore(VectorStore):
    def __init__(self) -> None:
        self._entries: dict[str, tuple[Document, Vector]] = {}

    async def upsert(self, entries: Sequence[tuple[Document, Vector]]) -> int:
        added = 0
        for document, vector in entries:
            if document.id not in self._entries:
                added += 1
            self._entries[document.id] = (document, vector)
        return added

    async def search(
        self, asset: Asset, query: Vector, limit: int = 5
    ) -> tuple[Document, ...]:
        scored = [
            (cosine(query, vector), document)
            for document, vector in self._entries.values()
            if document.asset is asset
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return tuple(document for _, document in scored[:limit])

    def __len__(self) -> int:
        return len(self._entries)


__all__ = ["InMemoryVectorStore"]
