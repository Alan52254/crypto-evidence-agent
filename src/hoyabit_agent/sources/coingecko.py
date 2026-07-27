"""CoinGecko Demo API 證據源 — 提供市值、交易量、流通量等補充資料。

月額度 10,000 次，不可作為輪詢主力。只在分析時呼叫一次取快照。
失效以空集合表達，不以例外表達。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx

from hoyabit_agent.domain import Asset, Evidence, Facet, SourceExcerpt
from hoyabit_agent.seams import Arguments, ToolSpec

COINGECKO_API_KEY_ENV = "COINGECKO_API_KEY"
BASE_URL = "https://api.coingecko.com/api/v3"

# CoinGecko 的幣種 ID 對應
_ASSET_IDS: dict[Asset, str] = {
    Asset.BTC: "bitcoin",
    Asset.ETH: "ethereum",
    Asset.SOL: "solana",
    Asset.BNB: "binancecoin",
    Asset.XRP: "ripple",
}


class CoinGeckoSource:
    """CoinGecko Demo API — 提供市值、交易量變化、流通量等基本面補充。

    不變式：
    - 失效回空 tuple，不拋例外
    - 月額度 10,000 次，每次分析只呼叫一次
    - 不需要 API key 也能用（免費層有 rate limit 但不需 key）
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._api_key = os.environ.get(COINGECKO_API_KEY_ENV, "").strip()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="coingecko_market",
            description=(
                "從 CoinGecko 取得該幣種的市值排名、24h 交易量變化率、"
                "流通量佔總供給比例等市場概況數據。"
                "適合補充 Binance 沒有的宏觀市場結構資訊。"
                "月額度有限，不可重複呼叫。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "asset": {
                        "type": "string",
                        "enum": ["BTC", "ETH", "SOL", "BNB", "XRP"],
                        "description": "要查詢的幣種",
                    },
                },
            },
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        """取得 CoinGecko 市場概況。"""
        coin_id = _ASSET_IDS.get(asset)
        if coin_id is None:
            return ()

        data = await self._fetch_coin_data(coin_id)
        if data is None:
            return ()

        return self._parse_evidence(asset, data)

    async def _fetch_coin_data(self, coin_id: str) -> dict | None:
        """呼叫 CoinGecko /coins/{id} 端點。"""
        url = f"{BASE_URL}/coins/{coin_id}"
        params: dict[str, str] = {
            "localization": "false",
            "tickers": "false",
            "market_data": "true",
            "community_data": "false",
            "developer_data": "false",
        }
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._api_key:
            headers["x-cg-demo-api-key"] = self._api_key

        try:
            response = await self._client.get(
                url, params=params, headers=headers, follow_redirects=True
            )
            if response.status_code != 200:
                return None
            return response.json()
        except (httpx.HTTPError, ValueError):
            return None

    def _parse_evidence(self, asset: Asset, data: dict) -> tuple[Evidence, ...]:
        """從 CoinGecko 回傳解析出 Evidence。"""
        market_data = data.get("market_data")
        if not market_data:
            return ()

        # 提取關鍵數據
        current_price = market_data.get("current_price", {}).get("usd")
        market_cap = market_data.get("market_cap", {}).get("usd")
        market_cap_rank = market_data.get("market_cap_rank")
        total_volume = market_data.get("total_volume", {}).get("usd")
        price_change_24h_pct = market_data.get("price_change_percentage_24h")
        price_change_7d_pct = market_data.get("price_change_percentage_7d")
        price_change_30d_pct = market_data.get("price_change_percentage_30d")
        circulating = market_data.get("circulating_supply")
        total_supply = market_data.get("total_supply")
        ath = market_data.get("ath", {}).get("usd")
        ath_change_pct = market_data.get("ath_change_percentage", {}).get("usd")

        # 構造摘要
        parts: list[str] = [f"{asset.value} CoinGecko 市場概況"]
        if market_cap_rank is not None:
            parts.append(f"市值排名 #{market_cap_rank}")
        if market_cap is not None:
            parts.append(f"市值 ${market_cap / 1e9:.1f}B")
        if total_volume is not None:
            parts.append(f"24h 交易量 ${total_volume / 1e9:.2f}B")
        if price_change_24h_pct is not None:
            parts.append(f"24h 漲跌 {price_change_24h_pct:+.2f}%")
        if price_change_7d_pct is not None:
            parts.append(f"7d 漲跌 {price_change_7d_pct:+.2f}%")
        if circulating and total_supply and total_supply > 0:
            ratio = circulating / total_supply * 100
            parts.append(f"流通率 {ratio:.1f}%")

        summary = "；".join(parts)

        # 構造原始文字
        text_lines = [
            f"Source: CoinGecko API /coins/{_ASSET_IDS[asset]}",
            f"Current Price: ${current_price}" if current_price else "",
            f"Market Cap: ${market_cap}" if market_cap else "",
            f"Market Cap Rank: #{market_cap_rank}" if market_cap_rank else "",
            f"24h Volume: ${total_volume}" if total_volume else "",
            f"24h Change: {price_change_24h_pct:+.2f}%" if price_change_24h_pct is not None else "",
            f"7d Change: {price_change_7d_pct:+.2f}%" if price_change_7d_pct is not None else "",
            f"30d Change: {price_change_30d_pct:+.2f}%" if price_change_30d_pct is not None else "",
            f"Circulating Supply: {circulating}" if circulating else "",
            f"Total Supply: {total_supply}" if total_supply else "",
            f"ATH: ${ath} ({ath_change_pct:+.1f}% from ATH)" if ath and ath_change_pct else "",
        ]
        text = "\n".join(line for line in text_lines if line)

        # stance_hint：用 7d 漲跌幅映射（±10% = ±0.5）
        hint = 0.0
        if price_change_7d_pct is not None:
            hint = max(-1.0, min(1.0, price_change_7d_pct / 20.0))

        excerpt = SourceExcerpt(
            source_id=f"coingecko:{asset.value.lower()}",
            url=f"https://www.coingecko.com/en/coins/{_ASSET_IDS[asset]}",
            retrieved_at=datetime.now(UTC),
            locator=f"GET /coins/{_ASSET_IDS[asset]}?market_data=true",
            text=text,
        )

        return (
            Evidence(
                id=f"CG-{asset.value}-MARKET",
                facet=Facet.FUNDAMENTAL,
                summary=summary,
                stance_hint=hint,
                excerpts=(excerpt,),
                event_key=f"coingecko-{asset.value}-snapshot",
            ),
        )


__all__ = ["CoinGeckoSource"]
