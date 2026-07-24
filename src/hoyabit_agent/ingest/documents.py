"""被 ingest 的文件，與向量儲存介面。

`Document` 是「一則被蒐集起來、已算好向量的原始素材」。它和 `Evidence` 不同：
Evidence 是某一次分析回合的產物，Document 是背景累積的、跨回合共用的脈絡。
檢索時 Document 會被包成帶「歷史」標記的 Evidence（見 `historical.py`）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from hoyabit_agent.domain import Asset, Facet
from hoyabit_agent.ingest.embeddings import Vector


@dataclass(frozen=True)
class Document:
    """一則已 ingest 的素材。`id` 穩定且唯一 —— 同一則再 ingest 不會產生重複。

    `event_key` 讓同事件的轉載可以被歸併（ADR 0002 的證據獨立性延伸到歷史庫）。
    `stance_hint` 在 ingest 當下算好並存起來，檢索時不必重算。
    """

    id: str
    asset: Asset
    facet: Facet
    stance_hint: float
    text: str
    url: str
    published_at: datetime
    source_id: str
    event_key: str | None = None


@runtime_checkable
class VectorStore(Protocol):
    """向量文件庫。

    不變式：

    * `upsert` 以 `Document.id` 為鍵**冪等**：同一個 id 再寫一次會覆蓋而非重複。
      回傳這次**新增**的筆數（既有的不算）。
    * `search` 只回傳同一個 `asset` 的文件，依與查詢向量的餘弦相似度排序（近的在前）。
    * 庫為空時 `search` 回傳空集合，不拋例外 —— 賽前還沒 ingest 也要能跑。
    """

    async def upsert(self, entries: Sequence[tuple[Document, Vector]]) -> int: ...

    async def search(
        self, asset: Asset, query: Vector, limit: int = 5
    ) -> tuple[Document, ...]: ...


__all__ = ["Document", "VectorStore"]
