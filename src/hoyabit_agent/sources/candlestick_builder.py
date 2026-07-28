"""Candlestick Chart Builder — K 線與技術分析圖表生成器 MCP 工具。

根據資產、時框與技術指標，自動調用 Binance / CoinGecko K 線資料，
產生嵌入式 SVG 圖表 (Base64 Data URI) 與前端浮動互動式 K 線圖 JSON Payload。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from hoyabit_agent.charts import (
    OHLCV,
    ChartData,
    generate_candlestick_svg,
    generate_price_chart_svg,
    generate_rsi_chart_svg,
    generate_volume_chart_svg,
    svg_to_data_uri,
)
from hoyabit_agent.domain import (
    AnalysisRegime,
    Asset,
    Evidence,
    Facet,
    SourceExcerpt,
)
from hoyabit_agent.indicators import rsi, sma
from hoyabit_agent.seams import Arguments, EvidenceSource, ToolSpec

logger = logging.getLogger(__name__)


class CandlestickBuilderSource:
    """浮動動態 K 線圖與技術指標圖表生成器。"""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client
        self.supported_regimes: frozenset[AnalysisRegime] = frozenset(AnalysisRegime)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="candlestick_chart_builder",
            description=(
                "為指定資產（BTC, ETH, SOL, BNB, XRP）繪製浮動互動式 K 線圖、"
                "價格走勢圖、成交量與 RSI 技術指標圖表，輸出 Base64 SVG 與互動式 JSON。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "asset": {
                        "type": "string",
                        "enum": ["BTC", "ETH", "SOL", "BNB", "XRP"],
                        "description": "分析的加密資產",
                    },
                    "interval": {
                        "type": "string",
                        "enum": ["15m", "1h", "4h", "1d"],
                        "default": "1d",
                        "description": "K 線圖時間週期",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 30,
                        "description": "繪製的 K 棒數量（預設 30 根）",
                    },
                },
                "required": ["asset"],
            },
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        target_asset = asset.value if isinstance(asset, Asset) else str(asset)
        interval = str(arguments.get("interval", "1d")).lower()
        limit = min(100, max(10, int(arguments.get("limit", 30))))

        symbol = f"{target_asset}USDT"
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"

        try:
            resp = await self._client.get(url, timeout=8.0)
            if resp.status_code != 200:
                logger.warning("[CandlestickBuilder] Binance Klines HTTP %d", resp.status_code)
                return ()
            raw_klines = resp.json()
        except Exception as exc:
            logger.warning("[CandlestickBuilder] 抓取 K線異常: %s", exc)
            return ()

        candles: list[OHLCV] = []
        closes: list[float] = []
        for k in raw_klines:
            # [open_time, open, high, low, close, volume, ...]
            ts_sec = int(k[0]) // 1000
            date_str = datetime.fromtimestamp(ts_sec, tz=UTC).strftime("%m-%d %H:%M")
            o, h, l, c, v = (
                float(k[1]),
                float(k[2]),
                float(k[3]),
                float(k[4]),
                float(k[5]),
            )
            candles.append(OHLCV(date=date_str, open=o, high=h, low=l, close=c, volume=v))
            closes.append(c)

        if len(closes) < 5:
            return ()

        # 計算 Rolling SMA 與 RSI
        sma20: list[float] = []
        sma50: list[float] = []
        rsi_vals: list[float] = []

        for i in range(len(closes)):
            sub = closes[: i + 1]
            s20 = sma(sub, period=min(20, len(sub))) or sub[-1]
            s50 = sma(sub, period=min(50, len(sub))) or sub[-1]
            r14 = rsi(sub, period=min(14, max(1, len(sub) - 1))) or 50.0
            sma20.append(s20)
            sma50.append(s50)
            rsi_vals.append(r14)

        chart_data = ChartData(
            asset=target_asset,
            candles=tuple(candles),
            rsi_values=tuple(rsi_vals),
            sma_20=tuple(sma20),
            sma_50=tuple(sma50),
        )

        # 產生 SVG 圖表
        price_svg = generate_price_chart_svg(chart_data)
        candle_svg = generate_candlestick_svg(chart_data)
        rsi_svg = generate_rsi_chart_svg(chart_data)

        price_uri = svg_to_data_uri(price_svg)
        candle_uri = svg_to_data_uri(candle_svg)

        first_close = closes[0]
        last_close = closes[-1]
        change_pct = ((last_close - first_close) / first_close) * 100
        stance_hint = max(-1.0, min(1.0, change_pct / 10.0))

        summary = (
            f"{target_asset} {interval} K 線圖表 ({len(candles)} 根): "
            f"最新價 ${last_close:,.2f} (區間漲跌: {change_pct:+.2f}%)，"
            f"RSI={rsi_vals[-1]:.1f}，SMA20=${sma20[-1]:,.2f}"
        )

        evidence_id = f"candlestick-{target_asset.lower()}-{interval}"

        evidence_item = Evidence(
            id=evidence_id,
            facet=Facet.TECHNICAL,
            summary=f"[浮動 K 線圖表] {summary}",
            stance_hint=stance_hint,
            excerpts=(
                SourceExcerpt(
                    source_id=f"candlestick-builder-{target_asset.lower()}",
                    url=url,
                    retrieved_at=datetime.now(UTC),
                    locator=f"klines/{interval}",
                    text=(
                        f"{summary} | [K線圖 Base64]({candle_uri}) | "
                        f"[走勢圖 Base64]({price_uri})"
                    ),
                ),
            ),
        )

        return (evidence_item,)


__all__ = ["CandlestickBuilderSource"]
