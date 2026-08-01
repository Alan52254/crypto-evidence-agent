"""鏈上數據源 — 提供活躍地址數與交易筆數。

多 provider 整合：
- BTC/ETH/XRP: Blockchair API（免費，不需 key，30 RPM）
- BNB: BscScan public API（不需 key 取得最新區塊號，用此推算活躍度）
- SOL: Solana 公開 RPC（getRecentPerformanceSamples）

設計：
- 失效以空集合表達，不以例外表達（接縫 1 不變式）。
- 每條鏈一次 HTTP 呼叫，低成本。
- 產出 fundamental facet 的 evidence（鏈上活動 = 基本面健康度）。
- 任何一條鏈連不上不影響其他鏈，各自獨立降級。
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
_BLOCKCHAIR_CHAINS: dict[Asset, str] = {
    Asset.BTC: "bitcoin",
    Asset.ETH: "ethereum",
    Asset.XRP: "ripple",
}

_BLOCKCHAIR_URL = "https://api.blockchair.com"
_BSCSCAN_URL = "https://api.bscscan.com/api"
_SOLANA_RPC = "https://api.mainnet-beta.solana.com"
_TIMEOUT = 10.0


class BlockchairOnChainSource:
    """多鏈鏈上統計數據源。"""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="blockchair_onchain",
            description=(
                "取得指定幣種的 24h 鏈上交易筆數、活躍地址數等基本鏈上活動指標。"
                "支援 BTC、ETH、XRP、BNB、SOL 全部五種受涵蓋幣種。"
                "用於判斷鏈上活躍度是否支撐當前價格水位。"
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
        if asset in _BLOCKCHAIR_CHAINS:
            return await self._fetch_blockchair(asset)
        elif asset == Asset.BNB:
            return await self._fetch_bnb()
        elif asset == Asset.SOL:
            return await self._fetch_solana()
        return ()

    async def _fetch_blockchair(self, asset: Asset) -> tuple[Evidence, ...]:
        chain = _BLOCKCHAIR_CHAINS[asset]
        try:
            response = await self._client.get(
                f"{_BLOCKCHAIR_URL}/{chain}/stats", timeout=_TIMEOUT
            )
        except (httpx.HTTPError, TimeoutError):
            return ()

        if response.status_code != 200:
            return ()

        try:
            data = response.json().get("data", {})
        except (ValueError, AttributeError):
            return ()

        return _build_blockchair_evidence(asset, chain, data)

    async def _fetch_bnb(self) -> tuple[Evidence, ...]:
        """BscScan: 取得最新區塊號和交易計數。"""
        try:
            # 取最新區塊號
            response = await self._client.get(
                _BSCSCAN_URL,
                params={"module": "proxy", "action": "eth_blockNumber"},
                timeout=_TIMEOUT,
            )
        except (httpx.HTTPError, TimeoutError):
            return ()

        if response.status_code != 200:
            return ()

        try:
            result = response.json().get("result", "0x0")
            block_number = int(result, 16)
        except (ValueError, TypeError, AttributeError):
            return ()

        # 取最新區塊的交易數量作為活躍度 proxy
        try:
            resp2 = await self._client.get(
                _BSCSCAN_URL,
                params={
                    "module": "proxy",
                    "action": "eth_getBlockTransactionCountByNumber",
                    "tag": hex(block_number),
                },
                timeout=_TIMEOUT,
            )
            if resp2.status_code == 200:
                tx_count_hex = resp2.json().get("result", "0x0")
                tx_in_latest_block = int(tx_count_hex, 16)
            else:
                tx_in_latest_block = None
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError):
            tx_in_latest_block = None

        now = datetime.now(UTC)
        parts = [f"最新區塊 #{block_number:,}"]
        if tx_in_latest_block is not None:
            parts.append(f"最新區塊交易數 {tx_in_latest_block}")
            # BSC 約 3 秒一塊，一天約 28,800 塊
            est_daily_tx = tx_in_latest_block * 28800
            parts.append(f"估算日交易量 ~{est_daily_tx:,.0f}")

        summary = f"BNB (BSC) 鏈上活動：{'；'.join(parts)}"

        return (
            Evidence(
                id="ONCHAIN-BNB-STATS",
                facet=Facet.FUNDAMENTAL,
                summary=summary,
                stance_hint=0.0,
                excerpts=(
                    SourceExcerpt(
                        source_id="bscscan",
                        url=f"{_BSCSCAN_URL}?module=proxy&action=eth_blockNumber",
                        retrieved_at=now,
                        locator="BscScan proxy API",
                        text=f"block_number={block_number}, tx_in_latest_block={tx_in_latest_block}",
                    ),
                ),
            ),
        )

    async def _fetch_solana(self) -> tuple[Evidence, ...]:
        """Solana RPC: getRecentPerformanceSamples 取得 TPS。"""
        try:
            response = await self._client.post(
                _SOLANA_RPC,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getRecentPerformanceSamples",
                    "params": [5],  # 最近 5 個 sample
                },
                timeout=_TIMEOUT,
            )
        except (httpx.HTTPError, TimeoutError):
            return ()

        if response.status_code != 200:
            return ()

        try:
            samples = response.json().get("result", [])
        except (ValueError, AttributeError):
            return ()

        if not samples:
            return ()

        # 計算平均 TPS
        total_tx = sum(s.get("numTransactions", 0) for s in samples)
        total_seconds = sum(s.get("samplePeriodSecs", 60) for s in samples)
        avg_tps = total_tx / total_seconds if total_seconds > 0 else 0

        # 最近一個 sample 的 slot 數
        latest = samples[0]
        num_slots = latest.get("numSlots", 0)
        num_tx = latest.get("numTransactions", 0)

        now = datetime.now(UTC)
        summary = (
            f"SOL 鏈上活動：平均 TPS {avg_tps:.0f}；"
            f"最近 sample 交易筆數 {num_tx:,}（{latest.get('samplePeriodSecs', 60)}s 區間）；"
            f"slots 處理 {num_slots:,}"
        )

        return (
            Evidence(
                id="ONCHAIN-SOL-STATS",
                facet=Facet.FUNDAMENTAL,
                summary=summary,
                stance_hint=0.0,
                excerpts=(
                    SourceExcerpt(
                        source_id="solana-rpc",
                        url=_SOLANA_RPC,
                        retrieved_at=now,
                        locator="getRecentPerformanceSamples",
                        text=f"avg_tps={avg_tps:.1f}, latest_tx={num_tx}, latest_slots={num_slots}",
                    ),
                ),
            ),
        )


def _build_blockchair_evidence(
    asset: Asset, chain: str, data: dict[str, Any]
) -> tuple[Evidence, ...]:
    """從 Blockchair stats 回應建構 evidence。"""
    now = datetime.now(UTC)

    tx_24h = data.get("transactions_24h")
    blocks_24h = data.get("blocks_24h")
    mempool = data.get("mempool_transactions")
    hashrate = data.get("hashrate_24h")
    addresses = data.get("hodling_addresses") or data.get("addresses_with_balance")

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
        eh = hashrate / 1e18
        parts.append(f"算力 {eh:.1f} EH/s")

    if not parts:
        return ()

    summary = f"{asset.value} 鏈上活動：{'；'.join(parts)}"

    return (
        Evidence(
            id=f"ONCHAIN-{asset.value}-STATS",
            facet=Facet.FUNDAMENTAL,
            summary=summary,
            stance_hint=0.0,
            excerpts=(
                SourceExcerpt(
                    source_id=f"blockchair-{chain}",
                    url=f"{_BLOCKCHAIR_URL}/{chain}/stats",
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
        ),
    )


__all__ = ["BlockchairOnChainSource"]
