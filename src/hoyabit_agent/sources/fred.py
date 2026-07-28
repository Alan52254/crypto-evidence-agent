"""FRED API（Federal Reserve Economic Data）— 宏觀經濟指標。

需要免費 API key（https://fred.stlouisfed.org/docs/api/api_key.html）。
提供聯邦基金利率、M2 貨幣供給、CPI、美元指數、10年期美債殖利率。
失效以空集合表達，不以例外表達。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import httpx

from hoyabit_agent.domain import Asset, Evidence, Facet, SourceExcerpt
from hoyabit_agent.seams import Arguments, ToolSpec

FRED_API_KEY_ENV = "FRED_API_KEY"
BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# 宏觀指標定義
_MACRO_SERIES = {
    "FEDFUNDS": "聯邦基金利率",
    "M2SL": "M2 貨幣供給（十億美元）",
    "CPIAUCSL": "消費者物價指數 CPI",
    "DTWEXBGS": "美元指數（貿易加權）",
    "DGS10": "10 年期美債殖利率",
}


class FredMacroSource:
    """FRED 宏觀經濟指標 — 基本面證據。

    不變式：
    - 沒有 FRED_API_KEY 時回傳空 tuple（降級設計）
    - 單次呼叫取所有 5 個指標的最近值
    - 失效回空集合，不拋例外
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._api_key = os.environ.get(FRED_API_KEY_ENV, "").strip()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="fred_macro",
            description=(
                "從 FRED（美國聯邦儲備經濟數據庫）取得宏觀經濟指標：\n"
                "- 聯邦基金利率（Fed Funds Rate）\n"
                "- M2 貨幣供給量\n"
                "- CPI 消費者物價指數\n"
                "- 美元指數\n"
                "- 10年期美債殖利率\n"
                "這些指標用於判斷全球流動性環境對加密貨幣的影響。"
                "每個指標回傳最近 6 個月的趨勢。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "months": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 12,
                        "description": "取最近幾個月的資料，預設 6",
                    },
                },
            },
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        """取得宏觀經濟指標。asset 參數被忽略（宏觀指標不分幣種）。"""
        if not self._api_key:
            return ()

        months = min(max(int(arguments.get("months", 6)), 1), 12)
        start_date = (datetime.now(UTC) - timedelta(days=months * 30)).strftime("%Y-%m-%d")

        results: list[Evidence] = []
        for series_id, description in _MACRO_SERIES.items():
            evidence = await self._fetch_series(series_id, description, start_date)
            if evidence is not None:
                results.append(evidence)

        return tuple(results)

    async def _fetch_series(
        self, series_id: str, description: str, start_date: str
    ) -> Evidence | None:
        """取得單一 FRED 時間序列。"""
        try:
            response = await self._client.get(
                BASE_URL,
                params={
                    "series_id": series_id,
                    "api_key": self._api_key,
                    "file_type": "json",
                    "observation_start": start_date,
                    "sort_order": "desc",
                    "limit": "10",
                },
                follow_redirects=True,
            )
            if response.status_code != 200:
                return None
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return None

        observations = data.get("observations", [])
        if not observations:
            return None

        # 取最新值和前一個值算變化
        latest = observations[0]
        latest_value = latest.get("value", ".")
        latest_date = latest.get("date", "")

        if latest_value == ".":  # FRED 用 "." 表示缺失
            return None

        try:
            current = float(latest_value)
        except ValueError:
            return None

        # 計算趨勢
        prev_value = None
        for obs in observations[1:]:
            v = obs.get("value", ".")
            if v != ".":
                try:
                    prev_value = float(v)
                except ValueError:
                    continue
                break

        change_str = ""
        if prev_value is not None and prev_value != 0:
            change = ((current - prev_value) / prev_value) * 100
            change_str = f"（較前期 {change:+.2f}%）"

        summary = f"{description}: {current:.2f}{change_str}（截至 {latest_date}）"

        text_lines = [
            f"Source: FRED API series {series_id}",
            f"Description: {description}",
            f"Latest value: {current:.4f} (as of {latest_date})",
            f"Previous: {prev_value}" if prev_value else "",
            f"Observations retrieved: {len(observations)}",
        ]

        # stance_hint 基於指標性質：
        # - 利率上升 / 美元走強 → 對 crypto 偏空
        # - M2 增加 / CPI 上升 → 對 crypto 偏多（通膨避險敘事）
        hint = 0.0
        if series_id in ("FEDFUNDS", "DGS10", "DTWEXBGS"):
            # 利率/美元：上升偏空，下降偏多
            if prev_value is not None and prev_value != 0:
                hint = -((current - prev_value) / prev_value) * 5
        elif series_id in ("M2SL", "CPIAUCSL"):
            # M2/CPI：上升偏多（流動性增加 / 通膨避險）
            if prev_value is not None and prev_value != 0:
                hint = ((current - prev_value) / prev_value) * 5
        hint = max(-1.0, min(1.0, hint))

        excerpt = SourceExcerpt(
            source_id=f"fred:{series_id.lower()}",
            url=f"https://fred.stlouisfed.org/series/{series_id}",
            retrieved_at=datetime.now(UTC),
            locator=f"GET /fred/series/observations?series_id={series_id}",
            text="\n".join(line for line in text_lines if line),
        )

        return Evidence(
            id=f"FRED-{series_id}",
            facet=Facet.FUNDAMENTAL,
            summary=summary,
            stance_hint=hint,
            excerpts=(excerpt,),
            event_key=f"fred-{series_id.lower()}",
        )


__all__ = ["FredMacroSource"]
