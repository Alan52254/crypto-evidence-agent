"""直接從競賽 CSV 讀取歷史 OHLCV 並計算技術指標 — 不需要 embedding 或 PostgreSQL。

這是 MarketDatasetEvidenceSource 的輕量替代：
- 啟動時把 5 個 CSV 讀進記憶體
- 用程式確定性計算 RSI / SMA / Volume Z-score
- 分析時按幣種 + as_of_date 過濾，回傳 Evidence

適用情境：沒有 PostgreSQL + pgvector、或 Gemini Embedding API 額度不足時。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from hoyabit_agent.arguments import bounded_int
from hoyabit_agent.domain import AnalysisRegime, Asset, Evidence, Facet, SourceExcerpt
from hoyabit_agent.ingest.dataset import (
    DATASET_END_DATE,
    load_asset_windows,
)
from hoyabit_agent.ingest.documents import MarketDocument
from hoyabit_agent.seams import Arguments, ToolSpec

TOOL_NAME = "market_dataset_context"

DEFAULT_DATASET_DIR = (
    Path("(HOYA BIT) 命題數據集 - 2026 雲湧智生：臺灣生成式 AI 應用黑客松競賽")
    / "HOYA_BIT_crypto_market_dataset"
)


class CsvHistoricalSource:
    """從競賽 CSV 直接提供歷史技術面 Evidence，不需要 embedding 或資料庫。"""

    def __init__(self, dataset_dir: Path | None = None) -> None:
        self._dataset_dir = dataset_dir or DEFAULT_DATASET_DIR
        self._documents: dict[Asset, list[MarketDocument]] = {}
        self._loaded = False

    @property
    def supported_regimes(self) -> frozenset[AnalysisRegime]:
        """支援 BACKTEST 與 LIVE 模式 —— 提供 2026 競賽命題 CSV 資料集數據。"""
        return frozenset({AnalysisRegime.BACKTEST, AnalysisRegime.LIVE})

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        data_dir = self._dataset_dir / "data" if (self._dataset_dir / "data").is_dir() else self._dataset_dir
        for asset in Asset:
            path = data_dir / f"{asset.value}_daily_ohlcv.csv"
            if path.is_file():
                docs = load_asset_windows(path, asset)
                self._documents[asset] = sorted(docs, key=lambda d: d.as_of_date)
        self._loaded = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=TOOL_NAME,
            description=(
                "從競賽 OHLCV 資料集檢索歷史技術面證據。資料截止於 "
                "2026-05-31 UTC；包含 RSI、SMA、報酬率、波動率等程式計算指標。"
                "不含新聞、基本面、鏈上或情緒資料。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要檢索的技術面問題（用於篩選相關性）"},
                    "as_of_date": {
                        "type": "string",
                        "format": "date",
                        "description": "分析截止日（YYYY-MM-DD）；預設 2026-05-31",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "回傳窗口數量，預設 3",
                    },
                },
            },
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        """按 as_of_date 過濾，回傳最近的 N 個 30 天窗口。"""
        self._ensure_loaded()

        as_of = _parse_as_of_date(arguments.get("as_of_date"))
        if as_of is None:
            return ()

        limit = bounded_int(arguments.get("limit"), 1, 10, 3)
        docs = self._documents.get(asset, [])
        if not docs:
            return ()

        # 找到 as_of_date 以前（含）的窗口，取最近的 limit 個
        eligible = [d for d in docs if d.as_of_date <= as_of and d.window_complete]
        if not eligible:
            return ()

        selected = eligible[-limit:]
        return tuple(_to_evidence(doc) for doc in selected)


def _parse_as_of_date(raw: object) -> date | None:
    """解析 as_of_date 參數，超出資料集範圍回傳 None。"""
    if raw in (None, ""):
        return DATASET_END_DATE
    try:
        parsed = date.fromisoformat(str(raw))
    except ValueError:
        return None
    return parsed if parsed <= DATASET_END_DATE else None


def _to_evidence(document: MarketDocument) -> Evidence:
    """把一個 MarketDocument 轉成 Evidence。"""
    indicators = document.indicators
    summary_parts = [
        f"{document.asset.value} 截至 {document.as_of_date.isoformat()} UTC",
    ]

    if indicators.rsi_14 is not None:
        summary_parts.append(f"RSI(14)={indicators.rsi_14:.1f}")
    if indicators.sma_30 is not None:
        last_close = float(document.ohlcv[-1].close) if document.ohlcv else None
        if last_close is not None:
            deviation = (last_close - indicators.sma_30) / indicators.sma_30 * 100
            summary_parts.append(f"收盤價相對30日均線偏離{deviation:+.1f}%")
    if indicators.return_7d is not None:
        summary_parts.append(f"7日報酬{indicators.return_7d * 100:+.1f}%")
    if indicators.return_30d is not None:
        summary_parts.append(f"30日報酬{indicators.return_30d * 100:+.1f}%")
    if indicators.volatility_30d is not None:
        summary_parts.append(f"30日年化波動率{indicators.volatility_30d * 100:.0f}%")
    if indicators.volume_sma_30_ratio is not None:
        summary_parts.append(f"成交量/30日均量={indicators.volume_sma_30_ratio:.2f}")

    summary = "；".join(summary_parts)

    # 構造原文（完整的 OHLCV 數值 + 指標）
    text_lines = [f"資料集：競賽 OHLCV（截止 2026-05-31 UTC）"]
    text_lines.append(f"幣種：{document.asset.value}  窗口截止：{document.as_of_date.isoformat()}")
    if document.ohlcv:
        last = document.ohlcv[-1]
        text_lines.append(
            f"最後一根日線：date={last.date} open={last.open} high={last.high} "
            f"low={last.low} close={last.close} volume={last.volume}"
        )
    text_lines.append(f"指標：{indicators}")

    excerpt = SourceExcerpt(
        source_id=f"dataset:{document.asset.value}:{document.as_of_date.isoformat()}",
        url=str(document.source_file),
        retrieved_at=datetime.now(UTC),
        locator=f"CSV rows {document.source_row_start}-{document.source_row_end}",
        text="\n".join(text_lines),
    )

    # stance_hint 用 RSI 和短期報酬做簡單映射
    hint = 0.0
    if indicators.rsi_14 is not None:
        hint += (indicators.rsi_14 - 50.0) / 100.0  # -0.5 ~ +0.5
    if indicators.return_7d is not None:
        hint += max(-0.5, min(0.5, indicators.return_7d * 5))  # 10% return → 0.5
    hint = max(-1.0, min(1.0, hint))

    return Evidence(
        id=f"MARKET-{document.asset.value}-{document.as_of_date.isoformat()}",
        facet=Facet.TECHNICAL,
        summary=summary,
        stance_hint=hint,
        excerpts=(excerpt,),
        event_key=f"dataset-{document.asset.value}-{document.as_of_date.isoformat()}",
    )


__all__ = ["CsvHistoricalSource", "TOOL_NAME"]
