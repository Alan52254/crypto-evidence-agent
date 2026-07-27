"""把具時間截斷的市場文件檢索轉成 technical evidence。"""

from __future__ import annotations

from datetime import UTC, date, datetime

from hoyabit_agent.arguments import bounded_int
from hoyabit_agent.domain import Asset, Evidence, Facet, SourceExcerpt
from hoyabit_agent.ingest.dataset import DATASET_END_DATE
from hoyabit_agent.ingest.documents import MarketDocument, MarketDocumentStore
from hoyabit_agent.ingest.embeddings import Embedder
from hoyabit_agent.seams import Arguments, ToolSpec

TOOL_NAME = "market_dataset_context"


class MarketDatasetEvidenceSource:
    def __init__(self, store: MarketDocumentStore, query_embedder: Embedder) -> None:
        self._store = store
        self._embedder = query_embedder

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=TOOL_NAME,
            description=(
                "只從競賽 OHLCV 資料集檢索技術面與趨勢證據。資料截止於 "
                "2026-05-31 UTC；不含新聞、基本面、鏈上或情緒資料。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要檢索的技術面問題"},
                    "as_of_date": {
                        "type": "string",
                        "format": "date",
                        "description": "分析截止日；預設 2026-05-31",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
            },
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        as_of_date = _as_of_date(arguments.get("as_of_date"))
        if as_of_date is None:
            return ()
        query = str(arguments.get("query") or f"{asset.value} 技術面趨勢").strip()
        if not _supports_technical_query(query):
            return ()
        limit = bounded_int(arguments.get("limit"), 1, 20, 5)
        try:
            (vector,) = await self._embedder.embed([query])
            documents = await self._store.search(
                asset,
                vector,
                as_of_date=as_of_date,
                embedding_model=self._embedder.model,
                limit=limit,
            )
        except Exception:  # noqa: BLE001 -- 外部模型或資料庫失效以證據不足表達
            return ()
        return tuple(_to_evidence(document) for document in documents)


_TECHNICAL_QUERY_TERMS = (
    "技術",
    "趨勢",
    "走勢",
    "價格",
    "價量",
    "開盤",
    "最高",
    "最低",
    "收盤",
    "成交量",
    "報酬",
    "波動",
    "均線",
    "移動平均",
    "rsi",
    "ohlcv",
    "price",
    "volume",
    "return",
    "volatility",
    "sma",
    "technical",
    "trend",
)


def _supports_technical_query(query: str) -> bool:
    lowered = query.casefold()
    return any(term.casefold() in lowered for term in _TECHNICAL_QUERY_TERMS)


def insufficiency_reason(arguments: Arguments) -> str | None:
    raw_date = arguments.get("as_of_date")
    if _as_of_date(raw_date) is None:
        return "要求日期超出資料集範圍；資料截止於 2026-05-31 UTC"
    query = str(arguments.get("query") or "技術面趨勢").strip()
    if not _supports_technical_query(query):
        return "競賽 OHLCV 只支持價格、成交量、報酬、波動與技術指標分析"
    return None


def _as_of_date(raw: object) -> date | None:
    if raw in (None, ""):
        return DATASET_END_DATE
    try:
        parsed = date.fromisoformat(str(raw))
    except ValueError:
        return None
    return parsed if parsed <= DATASET_END_DATE else None


def _to_evidence(document: MarketDocument) -> Evidence:
    excerpt = SourceExcerpt(
        source_id=f"dataset:{document.asset.value}:{document.as_of_date.isoformat()}",
        url=document.source_file.as_uri()
        if document.source_file.is_absolute()
        else str(document.source_file),
        retrieved_at=datetime(
            document.as_of_date.year,
            document.as_of_date.month,
            document.as_of_date.day,
            23,
            59,
            59,
            tzinfo=UTC,
        ),
        locator=f"CSV rows {document.source_row_start}-{document.source_row_end}",
        text=document.embedding_text(),
    )
    return Evidence(
        id=f"MARKET-{document.id}",
        facet=Facet.TECHNICAL,
        summary=(
            f"{document.asset.value} 截至 {document.as_of_date.isoformat()} UTC 的 OHLCV "
            f"技術面窗口（資料集截止 2026-05-31 UTC）"
        ),
        stance_hint=0.0,
        excerpts=(excerpt,),
        event_key=document.id,
    )


__all__ = ["MarketDatasetEvidenceSource", "TOOL_NAME", "insufficiency_reason"]
