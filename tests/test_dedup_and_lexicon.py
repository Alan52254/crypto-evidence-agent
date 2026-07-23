"""同事件分群與詞典打分的單元測試 —— 純函數，無 mock、無 I/O。"""

from __future__ import annotations

import pytest

from hoyabit_agent.dedup import assign_event_keys, significant_tokens, similarity
from hoyabit_agent.lexicon import LexiconLabeller, score_text

# --------------------------------------------------------------------------
# 分群
# --------------------------------------------------------------------------


def test_noise_words_are_not_significant() -> None:
    tokens = significant_tokens("The crypto market price news update of Bitcoin")
    assert tokens == {"bitcoin"}


def test_identical_titles_are_perfectly_similar() -> None:
    assert similarity("Bitcoin ETF inflow", "Bitcoin ETF inflow") == pytest.approx(1.0)


def test_unrelated_titles_are_not_similar() -> None:
    assert similarity("Bitcoin ETF inflow", "Solana validator outage") == pytest.approx(0.0)


def test_an_empty_title_is_never_similar_to_anything() -> None:
    assert similarity("", "Bitcoin ETF inflow") == 0.0


def test_two_outlets_covering_one_story_land_in_the_same_group() -> None:
    keys = assign_event_keys(
        [
            "Bitcoin spot ETF records largest daily inflow",
            "Bitcoin spot ETF sees largest daily inflow",
        ]
    )
    assert keys[0] == keys[1]


def test_distinct_stories_land_in_different_groups() -> None:
    keys = assign_event_keys(
        ["Bitcoin spot ETF records inflow", "Solana validator client outage reported"]
    )
    assert keys[0] != keys[1]


def test_grouping_is_stable_for_the_same_input() -> None:
    titles = ["Bitcoin ETF inflow hits record", "Ethereum upgrade goes live"]
    assert assign_event_keys(titles) == assign_event_keys(titles)


def test_an_empty_input_yields_no_keys() -> None:
    assert assign_event_keys([]) == []


def test_the_threshold_controls_how_aggressively_titles_are_grouped() -> None:
    """這兩則標題共用 3 個詞、聯集 10 個詞，相似度 0.3。

    門檻低於它就歸為同一事件，高於它就分開 —— 預設的 0.5 會分開，
    因為「流入創新高」與「流出反轉」確實是不同的事。
    """
    titles = [
        "Bitcoin spot ETF records largest daily inflow",
        "Bitcoin spot ETF sees outflow reverse",
    ]
    assert similarity(*titles) == pytest.approx(0.3)
    assert len(set(assign_event_keys(titles, threshold=0.25))) == 1
    assert len(set(assign_event_keys(titles))) == 2


def test_chinese_titles_group_by_shared_characters() -> None:
    keys = assign_event_keys(["比特幣現貨 ETF 淨流入創新高", "比特幣現貨 ETF 淨流入新高"])
    assert keys[0] == keys[1]


# --------------------------------------------------------------------------
# 詞典打分
# --------------------------------------------------------------------------


def test_neutral_text_scores_zero() -> None:
    assert score_text("The conference takes place next week") == 0.0


def test_empty_text_scores_zero() -> None:
    assert score_text("") == 0.0


def test_purely_bullish_text_scores_one() -> None:
    assert score_text("Bitcoin rallies to record high on ETF inflow") == pytest.approx(1.0)


def test_purely_bearish_text_scores_minus_one() -> None:
    assert score_text("Exchange hack triggers selloff and liquidation") == pytest.approx(-1.0)


def test_mixed_wording_lands_between_the_extremes() -> None:
    value = score_text("Bitcoin rallies despite exchange hack")
    assert -1.0 < value < 1.0


def test_scoring_is_case_insensitive() -> None:
    assert score_text("BITCOIN RALLIES") == score_text("bitcoin rallies")


def test_chinese_wording_is_scored_too() -> None:
    assert score_text("比特幣突破前高，買盤流入") > 0
    assert score_text("市場暴跌，賣壓沉重") < 0


async def test_the_lexicon_labeller_returns_one_score_per_text() -> None:
    scores = await LexiconLabeller().label(["Bitcoin rallies", "hack triggers selloff", "neutral"])
    assert len(scores) == 3
    assert scores[0] > 0 > scores[1]
    assert scores[2] == 0.0


async def test_the_lexicon_labeller_handles_an_empty_batch() -> None:
    assert await LexiconLabeller().label([]) == ()
