"""自家聚合指標證據源測試 —— 純確定性假資料，無 I/O、無 mock。

這個來源提供「全場只有本所寫得出來」的籌碼面證據（分幣種成交量、買賣盤比、
定期定額淨流入、新增持倉帳戶數）。目前是**介面 + 示意資料**，不接真實內部系統
（原 spec 明訂），因此每則證據都標示為示意資料。
"""

from __future__ import annotations

import pytest

from hoyabit_agent.domain import AnalysisRegime, Asset, Facet
from hoyabit_agent.reliability import ReliabilityTier, evidence_tier
from hoyabit_agent.sources.proprietary import TOOL_NAME, ProprietaryIndicatorsSource


def source() -> ProprietaryIndicatorsSource:
    return ProprietaryIndicatorsSource()


# --------------------------------------------------------------------------
# 介面契約（接縫 1）
# --------------------------------------------------------------------------


async def test_it_produces_positioning_evidence() -> None:
    found = await source().fetch(Asset.BTC, {})
    assert found
    assert {item.facet for item in found} == {Facet.POSITIONING}


async def test_every_evidence_carries_a_source_excerpt() -> None:
    found = await source().fetch(Asset.BTC, {})
    for item in found:
        assert item.excerpts
        assert item.excerpts[0].text
        assert item.excerpts[0].source_id


async def test_the_placeholder_nature_is_unmistakable_in_every_excerpt() -> None:
    """假資料不能被誤當真 —— 每則都要標示示意資料。"""
    found = await source().fetch(Asset.BTC, {})
    assert all("示意" in item.excerpts[0].text for item in found)


async def test_the_four_signature_metrics_are_present() -> None:
    found = await source().fetch(Asset.BTC, {})
    kinds = {item.id.rsplit("-", 1)[-1] for item in found}
    assert kinds == {"VOL", "BOOK", "DCA", "ACCT"}


async def test_output_is_deterministic_for_the_same_asset() -> None:
    first = await source().fetch(Asset.ETH, {})
    second = await source().fetch(Asset.ETH, {})
    assert [(i.id, i.stance_hint) for i in first] == [(j.id, j.stance_hint) for j in second]


async def test_different_assets_get_different_evidence_ids() -> None:
    btc = await source().fetch(Asset.BTC, {})
    eth = await source().fetch(Asset.ETH, {})
    assert {i.id for i in btc}.isdisjoint({j.id for j in eth})


# --------------------------------------------------------------------------
# 可信度分級：自家聚合資料是一手來源 → HIGH
# --------------------------------------------------------------------------


async def test_proprietary_evidence_is_graded_high_tier() -> None:
    """本所自家聚合資料是一手來源，可信度應為 HIGH。"""
    found = await source().fetch(Asset.BTC, {})
    assert all(evidence_tier(item) is ReliabilityTier.HIGH for item in found)


# --------------------------------------------------------------------------
# 分析模式：即時的內部狀態 → 只在 LIVE 合規
# --------------------------------------------------------------------------


def test_it_is_live_only_to_avoid_look_ahead_in_backtest() -> None:
    """示意的當前內部狀態沒有歷史軸，回測用它會有 look-ahead bias。"""
    assert source().supported_regimes == frozenset({AnalysisRegime.LIVE})


# --------------------------------------------------------------------------
# 韌性：亂參數不炸、非受涵蓋幣種
# --------------------------------------------------------------------------


@pytest.mark.parametrize("arguments", [{}, {"unknown": 1}, {"limit": "x"}, {"limit": None}])
async def test_nonsense_arguments_do_not_raise(arguments: dict[str, object]) -> None:
    assert await source().fetch(Asset.SOL, arguments)


# --------------------------------------------------------------------------
# MCP：一份 ToolSpec
# --------------------------------------------------------------------------


def test_it_exposes_a_named_tool_spec() -> None:
    spec = source().spec
    assert spec.name == TOOL_NAME
    assert spec.description
    assert "示意" in spec.description  # 描述也要誠實標明


def test_the_source_satisfies_the_evidence_source_protocol() -> None:
    from hoyabit_agent.seams import EvidenceSource

    assert isinstance(source(), EvidenceSource)
