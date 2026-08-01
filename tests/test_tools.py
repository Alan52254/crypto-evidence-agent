"""Function 工具的單元測試 —— **完全不需要任何 mock**。

這是「以 I/O 邊界切分工具」買到的東西：線的這一側全是確定性純函數，
測試又快又多，構成測試金字塔的底座。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from hoyabit_agent.domain import (
    Asset,
    Confidence,
    DraftClaim,
    Facet,
    Insufficiency,
    InsufficientEvidence,
    Stance,
)
from hoyabit_agent.testing import evidence
from hoyabit_agent.tools import (
    apply_contested_penalty,
    assess_confidence,
    check_citations,
    evidence_gap,
    facet_stance,
    gate_asset,
    merge_independent_evidence,
    overall_stance,
)

# --------------------------------------------------------------------------
# 幣種閘門
# --------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["BTC", "eth", " sol ", "BnB", "XRP"])
def test_covered_assets_pass_the_gate_regardless_of_casing_or_padding(raw: str) -> None:
    assert gate_asset(raw) is not None


@pytest.mark.parametrize("raw", ["DOGE", "PEPE", "", "BTC/USDT", "所有明天才出現的幣"])
def test_everything_outside_the_allowlist_is_rejected(raw: str) -> None:
    """閘門是白名單比對，不是黑名單偵測 —— 未知的新幣自動被擋。"""
    assert gate_asset(raw) is None


def test_the_allowlist_is_exactly_the_asset_enum() -> None:
    assert {gate_asset(a.value) for a in Asset} == set(Asset)


# --------------------------------------------------------------------------
# 證據缺口
# --------------------------------------------------------------------------


def test_no_evidence_means_every_facet_is_a_gap() -> None:
    assert evidence_gap([]).missing_facets == frozenset(Facet)


def test_a_facet_leaves_the_gap_once_it_has_evidence() -> None:
    gap = evidence_gap([evidence("E1", Facet.TECHNICAL, 0.5)])
    assert Facet.TECHNICAL not in gap
    assert Facet.SENTIMENT in gap


def test_the_gap_is_empty_when_all_four_facets_are_covered() -> None:
    items = [evidence(f"E{i}", facet, 0.5) for i, facet in enumerate(Facet)]
    assert evidence_gap(items).missing_facets == frozenset()


# --------------------------------------------------------------------------
# 證據獨立性（ADR 0002）
# --------------------------------------------------------------------------


def test_the_same_event_from_two_outlets_merges_into_one_evidence() -> None:
    merged = merge_independent_evidence(
        [
            evidence("E1", Facet.SENTIMENT, 0.8, event_key="etf-approved"),
            evidence("E2", Facet.SENTIMENT, 0.6, event_key="etf-approved"),
        ]
    )
    assert len(merged) == 1


def test_merging_preserves_every_source_excerpt() -> None:
    """歸併只影響「算幾個證據」，不損失任何溯源路徑。"""
    merged = merge_independent_evidence(
        [
            evidence("E1", Facet.SENTIMENT, 0.8, event_key="etf-approved"),
            evidence("E2", Facet.SENTIMENT, 0.6, event_key="etf-approved"),
        ]
    )
    assert len(merged[0].excerpts) == 2


def test_distinct_events_are_not_merged() -> None:
    merged = merge_independent_evidence(
        [
            evidence("E1", Facet.SENTIMENT, 0.8, event_key="etf-approved"),
            evidence("E2", Facet.SENTIMENT, 0.6, event_key="hack-reported"),
        ]
    )
    assert len(merged) == 2


def test_the_same_event_on_different_facets_stays_separate() -> None:
    """同一事件可以同時是基本面事實與情緒面素材 —— 兩者不是重複計數。"""
    merged = merge_independent_evidence(
        [
            evidence("E1", Facet.FUNDAMENTAL, 0.8, event_key="etf-approved"),
            evidence("E2", Facet.SENTIMENT, 0.6, event_key="etf-approved"),
        ]
    )
    assert len(merged) == 2


def test_evidence_without_an_event_key_is_never_merged() -> None:
    merged = merge_independent_evidence(
        [
            evidence("E1", Facet.TECHNICAL, 0.8),
            evidence("E2", Facet.TECHNICAL, 0.6),
        ]
    )
    assert len(merged) == 2


# --------------------------------------------------------------------------
# 證據面傾向
# --------------------------------------------------------------------------


def test_a_facet_with_no_evidence_is_neutral() -> None:
    assert facet_stance([]) is Stance.NEUTRAL


def test_weak_signals_stay_neutral_rather_than_being_forced_to_pick_a_side() -> None:
    assert facet_stance([evidence("E1", Facet.TECHNICAL, 0.1)]) is Stance.NEUTRAL


def test_facet_stance_averages_its_evidence() -> None:
    items = [
        evidence("E1", Facet.TECHNICAL, 0.9),
        evidence("E2", Facet.TECHNICAL, -0.3),
    ]
    assert facet_stance(items) is Stance.BULLISH


# --------------------------------------------------------------------------
# 信心度（ADR 0002）
# --------------------------------------------------------------------------


def test_a_single_facet_is_insufficient_evidence_not_perfect_agreement() -> None:
    """少了這條，「只查到一則新聞 → 四面一致 → 高信心」就會發生。"""
    result = assess_confidence([evidence("E1", Facet.TECHNICAL, 0.9)])
    assert isinstance(result, InsufficientEvidence)
    assert result.facets_present == frozenset({Facet.TECHNICAL})


def test_confidence_falls_when_one_facet_disagrees_with_the_rest() -> None:
    """信心度是各面之間的一致程度 —— 三面看多、一面看空應低於全面一致。"""
    unanimous = [
        evidence("E1", Facet.TECHNICAL, 0.8),
        evidence("E2", Facet.POSITIONING, 0.8),
        evidence("E3", Facet.FUNDAMENTAL, 0.8),
        evidence("E4", Facet.SENTIMENT, 0.8),
    ]
    divided = [
        evidence("E1", Facet.TECHNICAL, 0.8),
        evidence("E2", Facet.POSITIONING, -0.8),
        evidence("E3", Facet.FUNDAMENTAL, 0.8),
        evidence("E4", Facet.SENTIMENT, 0.8),
    ]
    agreed = assess_confidence(unanimous)
    split = assess_confidence(divided)
    assert isinstance(agreed, Confidence)
    assert isinstance(split, Confidence)
    assert split.value < agreed.value


def test_confidence_is_itself_traceable_to_the_facets_that_disagree() -> None:
    items = [
        evidence("E1", Facet.TECHNICAL, -0.8),
        evidence("E2", Facet.SENTIMENT, 0.8),
    ]
    result = assess_confidence(items)
    assert isinstance(result, Confidence)
    # 全部四個面都要在 —— 讀者要分得出「這面沉默」與「這面根本沒查到證據」
    assert result.facet_stances == {
        Facet.TECHNICAL: Stance.BEARISH,
        Facet.SENTIMENT: Stance.BULLISH,
        Facet.POSITIONING: Stance.NEUTRAL,
        Facet.FUNDAMENTAL: Stance.NEUTRAL,
    }


def test_silent_facets_do_not_count_as_agreement() -> None:
    """三個面沉默、一個面看空，不是「75% 共識」，是沒有訊號。

    這個 bug 是在真實資料上跑出來的：新聞面與籌碼面當時都不表態，
    舊算法卻回報 0.75 的高信心度。
    """
    items = [
        evidence("E1", Facet.TECHNICAL, -0.9),
        evidence("E2", Facet.POSITIONING, 0.0),
        evidence("E3", Facet.FUNDAMENTAL, 0.0),
        evidence("E4", Facet.SENTIMENT, 0.0),
    ]
    result = assess_confidence(items)
    assert isinstance(result, InsufficientEvidence)
    assert result.cause is Insufficiency.NO_DIRECTIONAL_SIGNAL
    assert result.directional_facets == frozenset({Facet.TECHNICAL})


def test_a_fully_silent_market_yields_no_directional_signal() -> None:
    items = [evidence(f"E{index}", facet, 0.0) for index, facet in enumerate(Facet)]
    result = assess_confidence(items)
    assert isinstance(result, InsufficientEvidence)
    assert result.cause is Insufficiency.NO_DIRECTIONAL_SIGNAL


def test_too_few_facets_is_reported_separately_from_no_direction() -> None:
    """兩種算不出來的原因對讀者的意義不同 —— 一個再找資料有救，一個沒有。"""
    result = assess_confidence([evidence("E1", Facet.TECHNICAL, 0.9)])
    assert isinstance(result, InsufficientEvidence)
    assert result.cause is Insufficiency.TOO_FEW_FACETS


def test_facets_present_counts_evidence_not_opinions() -> None:
    """「有證據」與「有表態」是兩件事，不能混為一談。"""
    result = assess_confidence([evidence("E1", Facet.TECHNICAL, 0.0)])
    assert isinstance(result, InsufficientEvidence)
    assert result.facets_present == frozenset({Facet.TECHNICAL})


def test_insufficient_evidence_still_carries_all_four_facet_stances() -> None:
    """算不出信心度時**更**需要呈現四個面 —— 讀者正是在這時要看出誰沉默。"""
    result = assess_confidence([evidence("E1", Facet.TECHNICAL, 0.9)])
    assert isinstance(result, InsufficientEvidence)
    assert set(result.facet_stances) == set(Facet)


def test_agreement_is_measured_only_among_facets_that_take_a_side() -> None:
    """沉默的面不投票 —— 一致度只由表態的面決定。

    以「其他條件完全相同、只有一個表態面翻向」來隔離一致度的影響：
    覆蓋、來源數、時效、證據數都一樣，唯一差別是兩個表態面是否同向。
    """
    with_silent_facets = [
        evidence("E1", Facet.TECHNICAL, 0.9),
        evidence("E2", Facet.POSITIONING, 0.8),
        evidence("E3", Facet.FUNDAMENTAL, 0.0),
        evidence("E4", Facet.SENTIMENT, 0.0),
    ]
    one_side_flipped = [
        evidence("E1", Facet.TECHNICAL, 0.9),
        evidence("E2", Facet.POSITIONING, -0.8),
        evidence("E3", Facet.FUNDAMENTAL, 0.0),
        evidence("E4", Facet.SENTIMENT, 0.0),
    ]
    agreed = assess_confidence(with_silent_facets)
    disagreed = assess_confidence(one_side_flipped)
    assert isinstance(agreed, Confidence)
    assert isinstance(disagreed, Confidence)
    assert agreed.value > disagreed.value


def test_confidence_still_reports_the_silent_facets_for_traceability() -> None:
    """讀者要看得出誰沉默、誰表態，所以四個面都要出現在報告裡。"""
    items = [
        evidence("E1", Facet.TECHNICAL, 0.9),
        evidence("E2", Facet.POSITIONING, 0.8),
        evidence("E3", Facet.FUNDAMENTAL, 0.0),
        evidence("E4", Facet.SENTIMENT, 0.0),
    ]
    result = assess_confidence(items)
    assert isinstance(result, Confidence)
    assert set(result.facet_stances) == set(Facet)
    assert result.facet_stances[Facet.FUNDAMENTAL] is Stance.NEUTRAL


def test_a_silent_facet_does_not_dilute_the_overall_stance() -> None:
    items = [
        evidence("E1", Facet.TECHNICAL, 0.9),
        evidence("E2", Facet.POSITIONING, 0.8),
        evidence("E3", Facet.FUNDAMENTAL, 0.0),
        evidence("E4", Facet.SENTIMENT, 0.0),
    ]
    assert overall_stance(assess_confidence(items)) is Stance.BULLISH


def test_a_transmitted_story_cannot_inflate_confidence() -> None:
    """兩家媒體轉載同一則新聞，不得讓情緒面「看起來」有兩個獨立證據。

    斷言的是**歸併後與原本只有一份時等價** —— 那才是「轉載不構成獨立證據」
    這條規則的可觀察後果，而不是某個特定分數。
    """
    transmitted_twice = [
        evidence("E1", Facet.SENTIMENT, 0.9, event_key="etf-approved"),
        evidence("E2", Facet.SENTIMENT, 0.9, event_key="etf-approved"),
        evidence("E3", Facet.TECHNICAL, -0.9),
    ]
    reported_once = [
        evidence("E1", Facet.SENTIMENT, 0.9, event_key="etf-approved"),
        evidence("E3", Facet.TECHNICAL, -0.9),
    ]
    merged = assess_confidence(merge_independent_evidence(transmitted_twice))
    single = assess_confidence(merge_independent_evidence(reported_once))
    assert isinstance(merged, Confidence)
    assert isinstance(single, Confidence)
    assert merged.value == pytest.approx(single.value)
    assert merged.facet_stances[Facet.SENTIMENT] is Stance.BULLISH
    assert merged.facet_stances[Facet.TECHNICAL] is Stance.BEARISH


# --------------------------------------------------------------------------
# 整體方向
# --------------------------------------------------------------------------


def test_insufficient_evidence_yields_a_neutral_stance() -> None:
    assert overall_stance(assess_confidence([])) is Stance.NEUTRAL


def test_a_tie_between_facets_yields_neutral_rather_than_picking_a_side() -> None:
    items = [
        evidence("E1", Facet.TECHNICAL, -0.8),
        evidence("E2", Facet.SENTIMENT, 0.8),
    ]
    assert overall_stance(assess_confidence(items)) is Stance.NEUTRAL


def test_the_majority_facet_direction_wins() -> None:
    items = [
        evidence("E1", Facet.TECHNICAL, 0.8),
        evidence("E2", Facet.POSITIONING, 0.8),
        evidence("E3", Facet.SENTIMENT, -0.8),
    ]
    assert overall_stance(assess_confidence(items)) is Stance.BULLISH


# --------------------------------------------------------------------------
# 引用檢核
# --------------------------------------------------------------------------


def test_a_claim_citing_real_evidence_is_kept() -> None:
    kept, dropped = check_citations(
        [DraftClaim("站上季線", ("E1",), Facet.TECHNICAL)],
        [evidence("E1", Facet.TECHNICAL, 0.8)],
    )
    assert len(kept) == 1
    assert not dropped


def test_a_claim_citing_nothing_is_dropped() -> None:
    kept, dropped = check_citations(
        [DraftClaim("後市看好", (), Facet.TECHNICAL)],
        [evidence("E1", Facet.TECHNICAL, 0.8)],
    )
    assert not kept
    assert len(dropped) == 1


def test_a_claim_citing_evidence_that_does_not_exist_is_dropped() -> None:
    """幻覺出來的證據識別碼不構成引用。"""
    kept, dropped = check_citations(
        [DraftClaim("鯨魚吸籌", ("E-HALLUCINATED",), Facet.POSITIONING)],
        [evidence("E1", Facet.TECHNICAL, 0.8)],
    )
    assert not kept
    assert len(dropped) == 1


def test_unknown_citations_are_stripped_but_the_claim_survives_on_its_real_ones() -> None:
    kept, _ = check_citations(
        [DraftClaim("站上季線", ("E1", "E-HALLUCINATED"), Facet.TECHNICAL)],
        [evidence("E1", Facet.TECHNICAL, 0.8)],
    )
    assert kept[0].evidence_ids == ("E1",)


def test_repeated_tool_evidence_id_is_unique_but_keeps_distinct_excerpts() -> None:
    first = evidence("BOOK", Facet.TECHNICAL, 0.2)
    second_base = evidence("BOOK", Facet.TECHNICAL, 0.4)
    second = replace(
        second_base, excerpts=(replace(second_base.excerpts[0], text="updated"),)
    )

    merged = merge_independent_evidence([first, second])

    assert [item.id for item in merged] == ["BOOK"]
    assert merged[0].stance_hint == 0.4
    assert len(merged[0].excerpts) == 2


def test_apply_contested_penalty_low_ratio_has_minimal_impact() -> None:
    conf = Confidence(
        value=0.8,
        facet_stances={},
        independence=0.8,
        coverage=0.8,
        freshness=0.8,
        agreement=0.8,
        completeness=0.8,
    )
    # 1/4 = 25% contested, penalty = 0.25² × 0.40 = 0.025
    res, info = apply_contested_penalty(
        conf, supported_count=3, contested_count=1, total_claims=4
    )
    assert isinstance(res, Confidence)
    assert res.value == round(0.8 - 0.025, 4)
    assert info["contested_ratio"] == 0.25


def test_apply_contested_penalty_high_ratio_applies_strong_deduction() -> None:
    conf = Confidence(
        value=0.8,
        facet_stances={},
        independence=0.8,
        coverage=0.8,
        freshness=0.8,
        agreement=0.8,
        completeness=0.8,
    )
    # 3/4 = 75% contested, penalty = 0.75² × 0.40 = 0.225
    res, info = apply_contested_penalty(
        conf, supported_count=1, contested_count=3, total_claims=4
    )
    assert isinstance(res, Confidence)
    assert res.value == round(0.8 - 0.225, 4)
    assert abs(info["penalty"] - 0.225) < 0.001
    assert info["contested_ratio"] == 0.75
