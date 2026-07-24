"""歷史檢索 —— 把向量庫包成一個證據源。

這是 ticket 08 的兌現點：分析回合除了即時抓的證據，還能撈到**數週前**
的相關報導。回傳的證據帶「歷史」標記，讓報告能區分即時與歷史脈絡。

依 I/O 邊界判準，它有外部 I/O（查向量庫），所以是 MCP 工具。
"""

from __future__ import annotations

from datetime import UTC, datetime

from hoyabit_agent.arguments import bounded_int
from hoyabit_agent.domain import Asset, Evidence, SourceExcerpt
from hoyabit_agent.ingest.documents import Document, VectorStore
from hoyabit_agent.ingest.embeddings import Embedder
from hoyabit_agent.seams import Arguments, ToolSpec
from hoyabit_agent.tools import merge_independent_evidence

TOOL_NAME = "historical_context"


class HistoricalEvidenceSource:
    """把向量庫的相關文件當作歷史情緒面證據回傳。

    query 由標的與模型給的關鍵字組成；沒有關鍵字時就用標的本身檢索。
    同事件跨媒體的轉載在此歸併（ADR 0002 的證據獨立性延伸到歷史庫）。
    """

    def __init__(self, store: VectorStore, embedder: Embedder) -> None:
        self._store = store
        self._embedder = embedder

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=TOOL_NAME,
            description=(
                "從歷史向量庫檢索與該幣種相關的過往報導，作為情緒面的歷史脈絡。"
                "適合回答「這次的走勢與過去哪些事件呼應」。"
                "回傳的每項證據都帶原文與出處，並標明是歷史資料。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "檢索關鍵字。留空則以幣種本身檢索。",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "最多取幾則歷史文件。",
                    },
                },
            },
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        query = str(arguments.get("query") or asset.value).strip() or asset.value
        limit = bounded_int(arguments.get("limit"), 1, 20, 5)

        try:
            (vector,) = await self._embedder.embed([query])
            documents = await self._store.search(asset, vector, limit)
        except Exception:  # noqa: BLE001 — 檢索失效以空集合表達，不中斷分析回合
            return ()

        return merge_independent_evidence(_to_evidence(doc) for doc in documents)


def _to_evidence(document: Document) -> Evidence:
    excerpt = SourceExcerpt(
        source_id=document.source_id,
        url=document.url,
        retrieved_at=datetime.now(UTC),
        locator=f"歷史文件，原發布於 {document.published_at:%Y-%m-%d %H:%M}",
        text=document.text,
    )
    return Evidence(
        id=f"HIST-{document.id}",
        facet=document.facet,
        summary=f"[歷史] {document.text[:60]}",
        stance_hint=document.stance_hint,
        excerpts=(excerpt,),
        # event_key 帶上「歷史」後綴，避免與即時證據被誤併成同一項
        event_key=f"{document.event_key}-historical" if document.event_key else None,
    )


__all__ = ["TOOL_NAME", "HistoricalEvidenceSource"]
