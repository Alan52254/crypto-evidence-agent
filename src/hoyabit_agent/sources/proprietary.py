"""本所自家聚合指標 —— 籌碼面的差異化證據源（issue #5）。

這是「全場只有本所寫得出來」的證據：分幣種成交量、買賣盤比、
**定期定額淨流入**、新增持倉帳戶數。它天然接上 HoyaBit 的產品特色
（定期定額、7 秒鎖價），能寫出別人寫不出的句子 ——
「本所定期定額買盤在 BTC 回檔時淨流入增加」。

## 目前是介面 + 示意資料

原 spec 明訂「僅實作介面與假資料，不接真實內部系統」——
避免時程被內部資料開通流程綁架。因此：

* 每則證據的原文都明確標為**示意資料**，不會被誤當真實成交。
* 這個來源**不掛進預設 live 分析堆疊**（見 cli），真實報告不會被假數字污染。
  它證明接縫存在、可被 MCP 列出、未來真實 adapter 可直接替換。
* source_id 前綴 `hoyabit-` 對應 reliability 的 HIGH 分級（一手來源）。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from hoyabit_agent.domain import AnalysisRegime, Asset, Evidence, Facet, SourceExcerpt
from hoyabit_agent.seams import Arguments, ToolSpec

TOOL_NAME = "hoyabit_flow"

_PLACEHOLDER = "（示意資料，本所內部系統尚未串接）"


def _unit(asset: Asset, salt: str) -> float:
    """由幣種與指標名確定性導出 0..1 的假值 —— 穩定、可測、無隨機。"""
    digest = hashlib.blake2b(f"{asset.value}:{salt}".encode(), digest_size=4).digest()
    return int.from_bytes(digest, "big") / 0xFFFFFFFF


def _signed(asset: Asset, salt: str) -> float:
    """把 0..1 假值映到 −1..+1 的傾向值。"""
    return round(_unit(asset, salt) * 2.0 - 1.0, 2)


class ProprietaryIndicatorsSource:
    """本所自家聚合指標證據源。

    只在 LIVE 合規：示意的當前內部狀態沒有歷史軸，回測用它會 look-ahead。
    """

    @property
    def supported_regimes(self) -> frozenset[AnalysisRegime]:
        return frozenset({AnalysisRegime.LIVE})

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=TOOL_NAME,
            description=(
                "本所自家聚合的籌碼面指標：分幣種成交量、買賣盤比、定期定額淨流入、"
                "新增持倉帳戶數。目前為介面 + 示意資料，尚未串接真實內部系統。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 4,
                        "description": "最多回傳幾項指標（共 4 項）。",
                    }
                },
            },
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        buy_ratio = 0.40 + _unit(asset, "book") * 0.30  # 0.40..0.70
        metrics = (
            (
                "VOL",
                _signed(asset, "vol"),
                f"本所 24h {asset.value} 成交量佔比 {_unit(asset, 'vol') * 100:.1f}%",
            ),
            (
                "BOOK",
                round(buy_ratio * 2 - 1, 2),
                f"本所盤口買量佔比 {buy_ratio * 100:.0f}%（買/賣）",
            ),
            (
                "DCA",
                _signed(asset, "dca"),
                f"本所定期定額 {asset.value} 近 7 日淨流入傾向 {_signed(asset, 'dca'):+.2f}",
            ),
            (
                "ACCT",
                _signed(asset, "acct"),
                f"本所新增 {asset.value} 持倉帳戶數週增傾向 {_signed(asset, 'acct'):+.2f}",
            ),
        )

        found = tuple(
            _evidence(asset, kind, stance, summary) for kind, stance, summary in metrics
        )
        return found[: _limit(arguments)]


def _evidence(asset: Asset, kind: str, stance: float, summary: str) -> Evidence:
    source_id = f"hoyabit-{kind.lower()}-{asset.value}"
    return Evidence(
        id=f"HOYA-{asset.value}-{kind}",
        facet=Facet.POSITIONING,
        summary=f"{summary} {_PLACEHOLDER}",
        stance_hint=stance,
        excerpts=(
            SourceExcerpt(
                source_id=source_id,
                url=f"internal://hoyabit/flow/{kind.lower()}/{asset.value}",
                retrieved_at=datetime.now(UTC),
                locator="本所聚合指標（示意）",
                text=f"{summary}。{_PLACEHOLDER}",
            ),
        ),
    )


def _limit(arguments: Arguments) -> int:
    value = arguments.get("limit")
    if isinstance(value, bool) or not isinstance(value, int):
        return 4
    return max(1, min(4, value))


__all__ = ["TOOL_NAME", "ProprietaryIndicatorsSource"]
