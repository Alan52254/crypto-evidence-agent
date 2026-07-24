"""背景 ingestion —— 把新聞持續收進向量庫，供分析回合檢索歷史脈絡。

刻意是「最笨但可靠」的做法：定時輪詢、去重、算向量、寫入。不引入串流基礎設施
（Kafka 之類），那對這個規模是純負擔。

⚠️ 這個服務要**賽前提前上線持續運行**，Demo 當天向量庫才不是空的。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from hoyabit_agent.dedup import assign_event_keys
from hoyabit_agent.domain import Asset, Facet, LabelAspect
from hoyabit_agent.ingest.documents import Document, VectorStore
from hoyabit_agent.ingest.embeddings import Embedder
from hoyabit_agent.sources.news import Article, Labeller

# ingestion 的輸入邊界：對某個幣種取回近期文章。刻意只依賴這個小介面，
# `NewsRssSource` 的取數邏輯可被重用而不必把整個證據源搬進來，
# 也讓測試能塞固定文章清單。
ArticleFetch = Callable[[Asset], Awaitable[Sequence[Article]]]


@dataclass
class IngestReport:
    """一次 ingestion 的結果，供排程日誌與監控。"""

    fetched: int = 0
    ingested: int = 0
    per_asset: dict[str, int] = field(default_factory=dict)


class IngestionService:
    """把文章轉成帶向量的文件寫進向量庫。

    情緒分數在 ingest 當下算好並存起來（`labeller`），檢索時不必重算 ——
    檢索路徑上每多一次模型呼叫都是壁鐘預算，那筆帳要在背景就付掉。
    """

    def __init__(
        self,
        fetch: ArticleFetch,
        embedder: Embedder,
        store: VectorStore,
        labeller: Labeller | None = None,
    ) -> None:
        self._fetch = fetch
        self._embedder = embedder
        self._store = store
        self._labeller = labeller

    async def run_once(self, assets: Sequence[Asset]) -> IngestReport:
        report = IngestReport()
        for asset in assets:
            articles = await self._fetch(asset)
            report.fetched += len(articles)
            documents = await self._to_documents(asset, articles)
            if not documents:
                report.per_asset[asset.value] = 0
                continue
            vectors = await self._embedder.embed([doc.text for doc in documents])
            added = await self._store.upsert(list(zip(documents, vectors, strict=True)))
            report.ingested += added
            report.per_asset[asset.value] = added
        return report

    async def _to_documents(
        self, asset: Asset, articles: Sequence[Article]
    ) -> list[Document]:
        if not articles:
            return []
        texts = [f"{a.title}。{a.summary}" for a in articles]
        keys = assign_event_keys([a.title for a in articles])
        scores = await self._score(texts)

        # id 綁 (事件, 媒體)：同一媒體的同一事件只留一份（冪等），
        # 不同媒體的轉載各留一份 —— 歸併交給檢索時的 event_key 處理。
        return [
            Document(
                id=f"{key}:{article.outlet}",
                asset=asset,
                facet=Facet.SENTIMENT,
                stance_hint=score,
                text=text[:600],
                url=article.link,
                published_at=article.published,
                source_id=f"{article.outlet}:{article.link}",
                event_key=key,
            )
            for article, key, score, text in zip(articles, keys, scores, texts, strict=True)
        ]

    async def _score(self, texts: Sequence[str]) -> tuple[float, ...]:
        if self._labeller is None:
            return tuple(0.0 for _ in texts)
        try:
            scores = await self._labeller.label(list(texts), LabelAspect.SENTIMENT)
        except Exception:  # noqa: BLE001 — 打分失敗只讓情緒轉中性，不中斷 ingestion
            return tuple(0.0 for _ in texts)
        return scores if len(scores) == len(texts) else tuple(0.0 for _ in texts)


__all__ = ["ArticleFetch", "IngestReport", "IngestionService"]
