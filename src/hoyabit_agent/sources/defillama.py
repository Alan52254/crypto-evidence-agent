"""DefiLlama API — 鏈上 TVL 數據，免費、無 key。

提供各鏈的 Total Value Locked，用於判斷鏈上資金活動是否支撐價格。
失效以空集合表達，不以例外表達。
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from hoyabit_agent.domain import Asset, Evidence, Facet, SourceExcerpt
from hoyabit_agent.seams import Arguments, ToolSpec

BASE_URL = "https://api.llama.fi"

# 幣種對應的鏈名稱（DefiLlama slug）
_CHAIN_MAP: dict[Asset, str] = {
    Asset.ETH: "Ethereum",
    Asset.SOL: "Solana",
    Asset.BNB: "BSC",
    Asset.BTC: "Bitcoin",
    Asset.XRP: "Ripple",
}


class DefiLlamaSource:
    """DefiLlama TVL — 鏈上基本面證據。"""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="defillama_tvl",
            description=(
                "從 DefiLlama 取得該幣種對應鏈的 TVL（Total Value Locked）數據。"
                "包含當前 TVL、1d/7d 變化率。"
                "用於判斷鏈上資金活動是否活躍、是否支撐當前價格水位。"
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
        """取得對應鏈的 TVL 數據。"""
        chain_name = _CHAIN_MAP.get(asset)
        if chain_name is None:
            return ()

        # 取所有鏈的 TVL 列表
        chains_data = await self._fetch_chains()
        if chains_data is None:
            return ()

        # 找到對應鏈
        chain_info = None
        for chain in chains_data:
            if chain.get("name", "").lower() == chain_name.lower():
                chain_info = chain
                break

        if chain_info is None:
            return ()

        tvl = chain_info.get("tvl", 0)
        change_1d = chain_info.get("change_1d", 0)
        change_7d = chain_info.get("change_7d", 0)

        # 構造摘要
        tvl_b = tvl / 1e9
        summary = (
            f"{chain_name} 鏈 TVL: ${tvl_b:.2f}B；"
            f"1d 變化 {change_1d:+.2f}%；7d 變化 {change_7d:+.2f}%"
        )

        # 構造原始文本
        text_lines = [
            f"Source: DefiLlama /v2/chains",
            f"Chain: {chain_name}",
            f"TVL: ${tvl:,.0f} (${tvl_b:.2f}B)",
            f"1d Change: {change_1d:+.2f}%",
            f"7d Change: {change_7d:+.2f}%",
        ]

        # stance_hint: TVL 7d 增長 = 偏多，下降 = 偏空
        hint = max(-1.0, min(1.0, change_7d / 10.0))  # ±10% = ±1.0

        excerpt = SourceExcerpt(
            source_id=f"defillama:{chain_name.lower()}",
            url=f"https://defillama.com/chain/{chain_name}",
            retrieved_at=datetime.now(UTC),
            locator=f"GET /v2/chains → {chain_name}",
            text="\n".join(text_lines),
        )

        return (
            Evidence(
                id=f"DL-{asset.value}-TVL",
                facet=Facet.FUNDAMENTAL,
                summary=summary,
                stance_hint=hint,
                excerpts=(excerpt,),
                event_key=f"defillama-{asset.value}-tvl",
            ),
        )

    async def _fetch_chains(self) -> list[dict] | None:
        """取得所有鏈的 TVL 列表。"""
        try:
            response = await self._client.get(
                f"{BASE_URL}/v2/chains",
                follow_redirects=True,
            )
            if response.status_code != 200:
                return None
            data = response.json()
            return data if isinstance(data, list) else None
        except (httpx.HTTPError, ValueError):
            return None


__all__ = ["DefiLlamaSource"]
