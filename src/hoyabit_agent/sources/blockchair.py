"""Blockchair 鏈上數據源 — 提供活躍地址數與交易筆數。

免費 API，不需要 key，30 RPM 限制。
支援 BTC、ETH、XRP（Blockchair 不支援 BNB 和 SOL）。
BNB/SOL 的請求靜默回傳空集合（降級，不報錯）。

設計：
- 失效以空集合表達，不以例外表達（接縫 1 不變式）。
- 單次 HTTP 呼叫取得整條鏈的 stats，低成本。
- 產出 fundamental facet 的 evidence（鏈上活動 = 基本面健康度）。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from hoyabit_agent.domain import (
    AnalysisRegime,
    Asset,
    Evidence,
    Facet,
    SourceExcerpt,
)
from hoyabit_agent.seams import Arguments, EvidenceSource, ToolSpec

# Blockchair chain 名稱映射
_CHAIN_MAP: dict[Asset, str] = {
    Asset.BTC: "bitcoin",
    Asset.ETH: "ethereum",
    Asset.XRP: "ripple",
}

_BASE_URL = "https://api.blockchair.com"
_TIMEOUT = 10.0


class BlockchairOnChainSource:
    """Blockchair 鏈上統計數據源。"""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="blockchair_onchain",
            description=(
                "取得指定幣種的 24h 鏈上交易筆數、活躍地址數等基本鏈上活動指標。"
                "支援 BTC、ETH、XRP。用於判斷鏈上活躍度是否支撐當前價格水位。"
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
                "required": ["asset"],
            },
        )

    @property
    def supported_regimes(self) -> frozenset[AnalysisRegime]:
        return frozenset({AnalysisRegime.LIVE})

    async def fetch(
        self, asset: Asset, arguments: Arguments
    ) -> tuple[Evidence, ...]:
        chain = _CHAIN_MAP.get(asset)
        if chain is None:
            # BNB/SOL 不支援，靜默回傳空集合
            return ()

        try:
            response = await self._client.get(
                f"{_BASE_URL}/{chain}/stats", timeout=_TIMEOUT
            )
        except (httpx.HTTPError, TimeoutError):
            return ()

        if response.status_code != 200:
            return ()

        try:
            data = response.json().get("data", {})
        except (ValueError, AttributeError):
            return ()

        return _build_evidence(asset, chain, data)


def _build_evidence(
    asset: Asset, chain: str, data: dict[str, Any]
) -> tuple[Evidence, ...]:
    """從 Blockchair stats 回應建構 evidence。"""
    now = datetime.now(UTC)
    found: list[Evidence] = []

    tx_24h = data.get("transactions_24h")
    blocks_24h = data.get("blocks_24h")
    mempool = data.get("mempool_transactions")
    difficulty = data.get("difficulty")
    hashrate = data.get("hashrate_24h")
    addresses = data.get("hodling_addresses") or data.get("addresses_with_balance")

    # 建構摘要
    parts = []
    if tx_24h is not None:
        parts.append(f"24h 交易筆數 {tx_24h:,.0f}")
    if addresses is not None:
        parts.append(f"持幣地址數 {addresses:,.0f}")
    if blocks_24h is not None:
        parts.append(f"24h 出塊 {blocks_24h}")
    if mempool is not None:
        parts.append(f"mempool 待確認 {mempool:,.0f}")
    if hashrate is not None and asset == Asset.BTC:
        # hashrate 單位是 hash/s，轉 EH/s
        eh = hashrate / 1e18
        parts.append(f"算力 {eh:.1f} EH/s")

    if not parts:
        return ()

    summary = f"{asset.value} 鏈上活動：{'；'.join(parts)}"

    # stance_hint：交易筆數本身是中性的（高活躍不代表方向），設為 0
    # 這個 source 的價值在於「有沒有鏈上數據」本身，不在方向判定
    found.append(
        Evidence(
            id=f"ONCHAIN-{asset.value}-STATS",
            facet=Facet.FUNDAMENTAL,
            summary=summary,
            stance_hint=0.0,
            excerpts=(
                SourceExcerpt(
                    source_id=f"blockchair-{chain}",
                    url=f"{_BASE_URL}/{chain}/stats",
                    retrieved_at=now,
                    locator="Blockchair /stats endpoint",
                    text=(
                        f"transactions_24h={tx_24h}, "
                        f"hodling_addresses={addresses}, "
                        f"blocks_24h={blocks_24h}, "
                        f"mempool_transactions={mempool}"
                    ),
                ),
            ),
        )
    )

    return tuple(found)


__all__ = ["BlockchairOnChainSource"]
