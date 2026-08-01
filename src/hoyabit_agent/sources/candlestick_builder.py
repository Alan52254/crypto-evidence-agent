"""Candlestick Chart Builder — K 線與技術分析圖表生成器 MCP 工具。

根據資產、時框與技術指標，自動調用 Binance / CoinGecko K 線資料，
產生嵌入式 SVG 圖表 (Base64 Data URI) 與前端浮動互動式 K 線圖 JSON Payload。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from hoyabit_agent.charts import (
    OHLCV,
    ChartData,
    generate_candlestick_svg,
    generate_price_chart_svg,
    generate_rsi_chart_svg,
    svg_to_data_uri,
)
from hoyabit_agent.domain import (
    AnalysisRegime,
    Asset,
    Evidence,
    Facet,
    Figure,
    FigureKind,
    SourceExcerpt,
)
from hoyabit_agent.indicators import rsi, sma
from hoyabit_agent.seams import Arguments, ToolSpec

logger = logging.getLogger(__name__)


class CandlestickBuilderSource:
    """浮動動態 K 線圖與技術指標圖表生成器。"""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client
        # 只在即時模式合規：本工具畫的是**現在**的 Binance K 線。
        # 在回測模式下它會取到截止日之後的價格，那是偷看未來（ADR 0005）。
        # `binance.py` 已經是 LIVE only，這裡宣告全模式是不一致的漏洞。
        self.supported_regimes: frozenset[AnalysisRegime] = frozenset(
            {AnalysisRegime.LIVE}
        )

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
            # httpx 的逾時/連線類例外（ReadTimeout、ConnectError）常常
            # str() 出來是空字串——只印 %s 會讓 log 變成看不出原因的
            # "抓取 K線異常: "，跟 bedrock.py 先前那個 90s/180s timeout
            # 案例同一種坑。改印例外類別名稱，至少能分辨是逾時、連線被拒
            # 還是別的問題。
            logger.warning(
                "[CandlestickBuilder] 抓取 K線異常: %s: %s", type(exc).__name__, exc
            )
            return ()

        candles: list[OHLCV] = []
        closes: list[float] = []
        for k in raw_klines:
            # [open_time, open, high, low, close, volume, ...]
            ts_sec = int(k[0]) // 1000
            date_str = datetime.fromtimestamp(ts_sec, tz=UTC).strftime("%m-%d %H:%M")
            open_, high, low, close, volume = (
                float(k[1]),
                float(k[2]),
                float(k[3]),
                float(k[4]),
                float(k[5]),
            )
            candles.append(
                OHLCV(
                    date=date_str,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                )
            )
            closes.append(close)

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

        # 圖走 `figures`，不進 `excerpt.text`。
        # 來源片段的語意是「可引用的原文」——把數 KB 的 base64 塞進去會
        # 讓它被送進 synthesise 提示詞，排擠掉真正的證據，而且 base64
        # 本身不是任何人能引用或核對的東西。
        window = f"{candles[0].date} ~ {candles[-1].date}"
        figures = (
            Figure(
                kind=FigureKind.GENERATED,
                caption=f"{target_asset} {interval} K 線（{len(candles)} 根，{window}）",
                data_uri=svg_to_data_uri(candle_svg),
                alt=f"{target_asset} {interval} candlestick chart",
            ),
            Figure(
                kind=FigureKind.GENERATED,
                caption=f"{target_asset} {interval} 收盤走勢與 SMA20/SMA50（{window}）",
                data_uri=svg_to_data_uri(price_svg),
                alt=f"{target_asset} {interval} price trend with moving averages",
            ),
            Figure(
                kind=FigureKind.GENERATED,
                caption=f"{target_asset} {interval} RSI(14)，最新 {rsi_vals[-1]:.1f}",
                data_uri=svg_to_data_uri(rsi_svg),
                alt=f"{target_asset} {interval} RSI indicator",
            ),
        )

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
                    locator=f"klines/{interval} [{window}]",
                    text=(
                        f"{summary}；區間最高 ${max(c.high for c in candles):,.2f}、"
                        f"最低 ${min(c.low for c in candles):,.2f}；"
                        f"圖表由本系統自 Binance 原始 K 線繪製（{len(figures)} 張）"
                    ),
                ),
            ),
            figures=figures,
        )

        return (evidence_item,)


__all__ = ["CandlestickBuilderSource"]
