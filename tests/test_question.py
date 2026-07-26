"""題型分類 —— 確定性純函數，無需任何 mock。

測試名稱描述的是「使用者可觀察的行為」：什麼樣的題目會被當成哪一類，
以及那個分類帶來什麼證據要求。
"""

from __future__ import annotations

import pytest

from hoyabit_agent.domain import Asset, Facet
from hoyabit_agent.question import (
    QuestionType,
    classify_question,
    derive_requirement,
    mentioned_assets,
)


@pytest.mark.parametrize(
    "question",
    [
        "市場普遍認為 BTC 會走強，請驗證",
        "有人說 ETH 升級後會噴，是不是真的",
        "請驗證這個觀點的正反證據",
        "market thinks BTC will rally, verify",
    ],
)
def test_a_question_carrying_an_existing_claim_is_hypothesis_verification(
    question: str,
) -> None:
    assert classify_question(question, (Asset.BTC,)) is QuestionType.HYPOTHESIS_VERIFICATION


@pytest.mark.parametrize(
    "question",
    ["比較 BTC 與 ETH 的強弱", "BTC vs ETH 誰更值得關注", "compare BTC and ETH"],
)
def test_a_question_asking_which_is_stronger_is_comparative(question: str) -> None:
    assert classify_question(question, (Asset.BTC,)) is QuestionType.COMPARATIVE_ANALYSIS


def test_two_assets_alone_make_a_question_comparative() -> None:
    """即使沒有「比較」字樣，指定兩個標的本身就是比較意圖。"""
    assert (
        classify_question("BTC 與 ETH 近期表現", (Asset.BTC, Asset.ETH))
        is QuestionType.COMPARATIVE_ANALYSIS
    )


def test_a_plain_question_about_one_asset_is_a_market_summary() -> None:
    assert classify_question("BTC 現在的走勢如何", (Asset.BTC,)) is QuestionType.MARKET_SUMMARY


def test_hypothesis_verification_demands_evidence_from_both_directions() -> None:
    """假設驗證題最重要的性質：只有單邊證據不能算蒐集完成。"""
    requirement = derive_requirement("市場認為 BTC 會漲，請驗證", (Asset.BTC,))
    assert requirement.require_both_directions is True
    assert requirement.required_facets == frozenset(Facet)


def test_comparative_analysis_demands_symmetry_rather_than_breadth() -> None:
    """比較題要求兩邊對稱；刻意不要求四面全滿 —— 兩個標的各補四面會吃光預算。"""
    requirement = derive_requirement("比較 BTC 與 ETH", (Asset.BTC, Asset.ETH))
    assert requirement.require_symmetric_coverage is True
    assert requirement.required_facets < frozenset(Facet)


def test_a_comparison_that_also_carries_a_claim_still_demands_both_directions() -> None:
    """「BTC 比 ETH 強，對嗎」同時是比較題與假設題，反方要求不可被吃掉。"""
    requirement = derive_requirement(
        "有人說 BTC 比 ETH 強，請驗證", (Asset.BTC, Asset.ETH)
    )
    assert requirement.question_type is QuestionType.COMPARATIVE_ANALYSIS
    assert requirement.require_symmetric_coverage is True
    assert requirement.require_both_directions is True


def test_market_summary_needs_fewer_independent_sources_than_verification() -> None:
    summary = derive_requirement("BTC 現在如何", (Asset.BTC,))
    verification = derive_requirement("市場認為 BTC 會漲，請驗證", (Asset.BTC,))
    assert summary.minimum_independent_sources < verification.minimum_independent_sources


def test_the_selected_asset_leads_even_when_another_is_mentioned_first() -> None:
    """使用者明確選定的標的比從文字猜出來的更可信，因此永遠排第一。"""
    assert mentioned_assets("ETH 和 BTC 哪個強", Asset.BTC) == (Asset.BTC, Asset.ETH)


def test_only_covered_assets_are_extracted_from_the_question() -> None:
    """幣種閘門是白名單 —— 題目提到 DOGE 不會讓它變成分析標的。"""
    assert mentioned_assets("DOGE 和 BTC 哪個強", Asset.BTC) == (Asset.BTC,)


def test_at_most_two_assets_are_extracted() -> None:
    """命題的比較題是兩兩比較，第三個標的不進入需求。"""
    found = mentioned_assets("BTC ETH SOL XRP 都看一下", Asset.BTC)
    assert len(found) == 2


def test_the_requirement_can_describe_itself_for_the_execution_log() -> None:
    """需求要能被印進提示詞與 Execution Log，否則「為什麼還在蒐集」無法稽核。"""
    described = derive_requirement("市場認為 BTC 會漲，請驗證", (Asset.BTC,)).describe()
    assert "hypothesis_verification" in described
    assert "支持與反對" in described
