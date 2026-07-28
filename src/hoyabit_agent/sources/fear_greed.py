"""Alternative.me Crypto Fear & Greed Index — 市場情緒量化指標。

免費、無 key、無 rate limit。回傳 0-100 的情緒分數：
- 0-24: Extreme Fear
- 25-49: Fear
- 50-74: Greed
- 75-100: Extreme Greed

失效以空集合表達，不以例外表達。
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from hoyabit_agent.domain import Asset, Evidence, Facet, SourceExcerpt
from hoyabit_agent.seams import Arguments, ToolSpec

API_URL = "https://api.alternative.me/fng/"


class FearGreedSource:
    """Crypto Fear & Greed Index — 情緒面補充證據。"""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="fear_greed_index",
            description=(
                "取得 Crypto Fear & Greed Index（0=極度恐懼, 100=極度貪婪）。"
                "提供當前值與近期歷史趨勢，作為市場整體情緒面的量化補充。"
                "此為全市場指標，不分幣種。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 30,
                        "description": "取最近幾天的歷史，預設 7",
                    },
                },
            },
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        """取得 Fear & Greed Index。asset 參數被忽略（全市場指標）。"""
        days = min(max(int(arguments.get("days", 7)), 1), 30)

        try:
            response = await self._client.get(
                API_URL,
                params={"limit": days, "format": "json"},
                follow_redirects=True,
            )
            if response.status_code != 200:
                return ()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return ()

        entries = data.get("data", [])
        if not entries:
            return ()

        # 最新一筆
        latest = entries[0]
        current_value = int(latest.get("value", 50))
        classification = latest.get("value_classification", "Neutral")

        # 計算趨勢
        values = [int(e.get("value", 50)) for e in entries]
        avg = sum(values) / len(values)
        trend = "上升" if current_value > avg else "下降" if current_value < avg else "持平"

        summary = (
            f"Crypto Fear & Greed Index: {current_value}/100 ({classification})；"
            f"近 {days} 天均值 {avg:.0f}，趨勢{trend}"
        )

        # 構造原始文本
        text_lines = [
            f"Source: Alternative.me Crypto Fear & Greed Index",
            f"Current: {current_value} ({classification})",
            f"Recent {days}d average: {avg:.1f}",
            f"Trend: {trend}",
            f"History: {', '.join(str(v) for v in values[:7])}",
        ]

        # stance_hint: 50=中性, 偏離越大越極端
        # Fear (< 50) 映射為負（市場恐慌 → 偏空情緒）
        # Greed (> 50) 映射為正（市場貪婪 → 偏多情緒）
        hint = (current_value - 50) / 50.0  # -1 to +1
        hint = max(-1.0, min(1.0, hint))

        excerpt = SourceExcerpt(
            source_id="fear-greed-index",
            url="https://alternative.me/crypto/fear-and-greed-index/",
            retrieved_at=datetime.now(UTC),
            locator=f"GET /fng/?limit={days}",
            text="\n".join(text_lines),
        )

        return (
            Evidence(
                id="FGI-CURRENT",
                facet=Facet.SENTIMENT,
                summary=summary,
                stance_hint=hint,
                excerpts=(excerpt,),
                event_key="fear-greed-snapshot",
            ),
        )


__all__ = ["FearGreedSource"]
