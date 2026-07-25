"""競賽市場資料集 ingestion 的 orchestration module。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from hoyabit_agent.domain import Asset
from hoyabit_agent.ingest.dataset import load_dataset
from hoyabit_agent.ingest.documents import MarketDocumentStore
from hoyabit_agent.ingest.embeddings import Embedder


@dataclass
class IngestReport:
    loaded: int = 0
    ingested: int = 0
    per_asset: dict[str, int] = field(default_factory=dict)


class MarketDatasetIngestionService:
    """讀取、向量化並冪等保存完整 OHLCV 資料集。"""

    def __init__(
        self,
        dataset_dir: Path,
        embedder: Embedder,
        store: MarketDocumentStore,
        *,
        batch_size: int = 100,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self._dataset_dir = dataset_dir
        self._embedder = embedder
        self._store = store
        self._batch_size = batch_size

    async def run_once(self) -> IngestReport:
        documents = load_dataset(self._dataset_dir)
        report = IngestReport(loaded=len(documents))
        for asset in Asset:
            asset_documents = [document for document in documents if document.asset is asset]
            report.per_asset[asset.value] = 0
            for start in range(0, len(asset_documents), self._batch_size):
                batch = asset_documents[start : start + self._batch_size]
                vectors = await self._embedder.embed(
                    [document.embedding_text() for document in batch]
                )
                added = await self._store.upsert(
                    list(zip(batch, vectors, strict=True)),
                    embedding_model=self._embedder.model,
                )
                report.ingested += added
                report.per_asset[asset.value] += added
        return report


__all__ = ["IngestReport", "MarketDatasetIngestionService"]
