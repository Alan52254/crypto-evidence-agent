"""題型導向的缺口判定 —— 收斂條件的守門人，無需 mock。

這一層存在的理由：停止條件不能交給模型自評。因此測試的核心是
「什麼情況下系統會拒絕收斂」。
"""

from __future__ import annotations

from tests.test_reliability import evidence_from

from hoyabit_agent.domain import Asset, Facet
from hoyabit_agent.gaps import (
    ASYMMETRIC_COMPARISON,
    INSUFFICIENT_SOURCES,
    LOW_QUALITY_ONLY,
    MISSING_FACETS,
    MISSING_OPPOSING,
    MISSING_SUPPORTING,
    UNRESOLVED_CONTRADICTION,
    assess,
)
from hoyabit_agent.question import derive_requirement

SUMMARY_Q = "BTC 現在的走勢如何"
VERIFY_Q = "市場認為 BTC 會漲，請驗證"
COMPARE_Q = "比較 BTC 與 ETH 的強弱"


def four_facets_bullish() -> tuple:
    """四面覆蓋、來源可信、但全部偏多 —— 用來隔離「缺反方」這一個條件。"""
    return (
        evidence_from("BNC-BTC-RSI", "binance:spot", facet=Facet.TECHNICAL, stance_hint=0.5),
        evidence_from("BNC-BTC-OI", "binance:perp", facet=Facet.POSITIONING, stance_hint=0.4),
        evidence_from("NEWS-BTC-1", "coindesk:a", facet=Facet.FUNDAMENTAL, stance_hint=0.6),
        evidence_from("NEWS-BTC-2", "cointelegraph:b", facet=Facet.SENTIMENT, stance_hint=0.5),
    )


def test_a_hypothesis_question_refuses_to_converge_on_one_sided_evidence() -> None:
    """假設驗證題最重要的行為：全部偏多不算驗證完成，反方是必補的。"""
    assessment = assess(four_facets_bullish(), derive_requirement(VERIFY_Q, (Asset.BTC,)))
    assert assessment  # 真值即「還有必補缺口」
    assert MISSING_OPPOSING in assessment.reasons()


def test_adding_opposing_evidence_closes_the_hypothesis_gap() -> None:
    balanced = four_facets_bullish() + (
        evidence_from("NEWS-BTC-3", "blockworks:c", facet=Facet.FUNDAMENTAL, stance_hint=-0.5),
    )
    assessment = assess(balanced, derive_requirement(VERIFY_Q, (Asset.BTC,)))
    assert not assessment.blocking_gaps


def test_a_hypothesis_question_also_demands_the_supporting_side() -> None:
    """反方要求是對稱的 —— 全部偏空同樣不算驗證完成。"""
    bearish = tuple(
        evidence_from(
            item.id,
            *[e.source_id for e in item.excerpts],
            facet=item.facet,
            stance_hint=-item.stance_hint,
        )
        for item in four_facets_bullish()
    )
    assessment = assess(bearish, derive_requirement(VERIFY_Q, (Asset.BTC,)))
    assert MISSING_SUPPORTING in assessment.reasons()


def test_a_market_summary_does_not_force_a_counter_side_but_discloses_it() -> None:
    """摘要題不強制反方，但一面倒仍要寫進限制說明，不能靜默。"""
    assessment = assess(four_facets_bullish(), derive_requirement(SUMMARY_Q, (Asset.BTC,)))
    assert not assessment.blocking_gaps
    assert MISSING_OPPOSING in assessment.reasons()


def test_a_comparison_refuses_to_converge_while_one_side_is_unexplored() -> None:
    """比較結論需要對齊的粒度 —— 一邊三面、一邊零面不能下判斷。"""
    only_btc = (
        evidence_from("BNC-BTC-RSI", "binance:spot", facet=Facet.TECHNICAL, stance_hint=0.3),
        evidence_from("BNC-BTC-OI", "binance:perp", facet=Facet.POSITIONING, stance_hint=0.2),
        evidence_from("NEWS-BTC", "coindesk:x", facet=Facet.FUNDAMENTAL, stance_hint=-0.3),
    )
    assessment = assess(only_btc, derive_requirement(COMPARE_Q, (Asset.BTC, Asset.ETH)))
    assert ASYMMETRIC_COMPARISON in assessment.reasons()
    assert assessment.per_asset_facets["ETH"] == 0


def test_a_symmetric_comparison_converges() -> None:
    symmetric = (
        evidence_from("BNC-BTC-RSI", "binance:spot", facet=Facet.TECHNICAL, stance_hint=0.3),
        evidence_from("BNC-BTC-OI", "binance:perp", facet=Facet.POSITIONING, stance_hint=-0.2),
        evidence_from("BNC-ETH-RSI", "binance:spot2", facet=Facet.TECHNICAL, stance_hint=0.2),
        evidence_from("BNC-ETH-OI", "binance:perp2", facet=Facet.POSITIONING, stance_hint=-0.3),
    )
    assessment = assess(symmetric, derive_requirement(COMPARE_Q, (Asset.BTC, Asset.ETH)))
    assert not assessment.blocking_gaps


def test_media_only_evidence_cannot_support_numeric_analysis() -> None:
    """僅憑媒體轉述不足以支撐數值型判斷 —— 需要可重算的第一手數值。"""
    media_only = (
        evidence_from("N1", "coindesk:a", facet=Facet.TECHNICAL, stance_hint=0.3),
        evidence_from("N2", "cointelegraph:b", facet=Facet.POSITIONING, stance_hint=-0.3),
        evidence_from("N3", "blocktempo:c", facet=Facet.FUNDAMENTAL, stance_hint=0.2),
        evidence_from("N4", "blockworks:d", facet=Facet.SENTIMENT, stance_hint=-0.2),
    )
    assessment = assess(media_only, derive_requirement(SUMMARY_Q, (Asset.BTC,)))
    assert LOW_QUALITY_ONLY in assessment.reasons()


def test_social_only_evidence_fails_the_weighted_source_threshold() -> None:
    """低可信來源的加權較低，因此湊數量無法通過門檻。"""
    social = (
        evidence_from("S1", "reddit:1", facet=Facet.TECHNICAL, stance_hint=0.3),
        evidence_from("S2", "reddit:2", facet=Facet.POSITIONING, stance_hint=-0.3),
        evidence_from("S3", "twitter:3", facet=Facet.FUNDAMENTAL, stance_hint=0.2),
        evidence_from("S4", "twitter:4", facet=Facet.SENTIMENT, stance_hint=-0.2),
    )
    assessment = assess(social, derive_requirement(SUMMARY_Q, (Asset.BTC,)))
    assert INSUFFICIENT_SOURCES in assessment.reasons()


def test_missing_facets_block_convergence() -> None:
    one_facet = (
        evidence_from("E1", "binance:spot", facet=Facet.TECHNICAL, stance_hint=0.3),
        evidence_from("E2", "coindesk:a", facet=Facet.TECHNICAL, stance_hint=-0.3),
    )
    assessment = assess(one_facet, derive_requirement(SUMMARY_Q, (Asset.BTC,)))
    assert MISSING_FACETS in assessment.reasons()
    assert Facet.SENTIMENT in assessment.missing_facets


def test_a_contradiction_within_one_facet_is_disclosed_not_blocking() -> None:
    """矛盾不阻止收斂 —— 它是要被說明的事實，不是要被消滅的狀態。"""
    conflicting = four_facets_bullish() + (
        evidence_from("BNC-BTC-VOL", "binance:vol", facet=Facet.TECHNICAL, stance_hint=-0.6),
    )
    assessment = assess(conflicting, derive_requirement(SUMMARY_Q, (Asset.BTC,)))
    assert UNRESOLVED_CONTRADICTION in assessment.reasons()
    assert all(gap.kind != UNRESOLVED_CONTRADICTION for gap in assessment.blocking_gaps)


def test_an_assessment_describes_which_gaps_must_be_closed() -> None:
    """模型要看得出「哪些是必補」，否則它無法針對缺口選工具。"""
    described = assess(
        four_facets_bullish(), derive_requirement(VERIFY_Q, (Asset.BTC,))
    ).describe()
    assert "必補" in described
    assert MISSING_OPPOSING in described


def test_no_evidence_at_all_blocks_convergence() -> None:
    assessment = assess((), derive_requirement(SUMMARY_Q, (Asset.BTC,)))
    assert assessment
    assert MISSING_FACETS in assessment.reasons()
