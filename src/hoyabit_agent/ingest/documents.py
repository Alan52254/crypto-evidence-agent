"""競賽市場資料文件與儲存 seam。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Protocol, runtime_checkable

from hoyabit_agent.domain import Asset
from hoyabit_agent.ingest.embeddings import Vector


@dataclass(frozen=True)
class OhlcvBar:
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class MarketIndicators:
    daily_return: float | None
    return_7d: float | None
    return_30d: float | None
    volatility_30d: float | None
    sma_7: float | None
    sma_30: float | None
    rsi_14: float | None
    volume_change_1d: float | None
    volume_sma_30_ratio: float | None


@dataclass(frozen=True)
class MarketDocument:
    """單一資產在 `as_of_date` 可知的、可追溯市場資料窗口。"""

    asset: Asset
    as_of_date: date
    ohlcv: tuple[OhlcvBar, ...]
    indicators: MarketIndicators
    window_complete: bool
    source_file: Path
    source_row_start: int
    source_row_end: int

    @property
    def id(self) -> str:
        return f"{self.asset.value}:{self.as_of_date.isoformat()}"

    def embedding_text(self) -> str:
        values = ", ".join(
            f"{name}={value if value is not None else 'null'}"
            for name, value in vars(self.indicators).items()
        )
        bars = "; ".join(
            f"{bar.date.isoformat()} O={bar.open} H={bar.high} L={bar.low} "
            f"C={bar.close} V={bar.volume}"
            for bar in self.ohlcv
        )
        return (
            f"asset={self.asset.value}; as_of_date={self.as_of_date.isoformat()}; "
            f"window_complete={str(self.window_complete).lower()}; {values}; OHLCV: {bars}"
        )


@runtime_checkable
class MarketDocumentStore(Protocol):
    async def upsert(
        self,
        entries: Sequence[tuple[MarketDocument, Vector]],
        *,
        embedding_model: str,
    ) -> int: ...

    async def search(
        self,
        asset: Asset,
        query: Vector,
        *,
        as_of_date: date,
        embedding_model: str,
        limit: int = 5,
    ) -> tuple[MarketDocument, ...]: ...


__all__ = ["MarketDocument", "MarketDocumentStore", "MarketIndicators", "OhlcvBar"]
