"""題型分類 —— 決定「這一題需要什麼證據」，而非固定四面向硬補。

命題的題型不只「現在看多看空」，還包括假設驗證與比較分析。
沒有題型意識的 gap check 會在比較題浪費預算、在假設驗證題漏掉反方。

分類是**確定性的關鍵字判斷**，不是模型呼叫 —— 題型判錯的代價是
整輪蒐集失焦，不值得押在一次 LLM 回應上。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from hoyabit_agent.domain import AnalysisRegime, Asset, Facet


class QuestionType(Enum):
    """題型。決定證據需求與報告骨架。"""

    MARKET_SUMMARY = "market_summary"
    """單一標的的現況研判。需要四面向覆蓋。"""

    HYPOTHESIS_VERIFICATION = "hypothesis_verification"
    """驗證一個既有說法。**必須同時有支持與反對證據**。"""

    COMPARATIVE_ANALYSIS = "comparative_analysis"
    """比較兩個標的。**兩邊證據粒度必須對稱**。"""


# 假設驗證的語言特徵：題目本身已經帶了一個待驗證的主張。
_HYPOTHESIS_MARKERS = (
    "市場認為",
    "市場普遍認為",
    "有人說",
    "有人認為",
    "外界認為",
    "請驗證",
    "驗證",
    "是否真的",
    "是不是真的",
    "真的嗎",
    "站得住",
    "正反證據",
    "反方",
    "推翻",
    "claim",
    "verify",
    "is it true",
)

# 比較分析的語言特徵。
_COMPARISON_MARKERS = (
    "比較",
    "相比",
    "對比",
    "哪一個",
    "哪個比較",
    "誰更",
    "強弱",
    "相對強度",
    " vs ",
    " vs.",
    "versus",
    "compare",
)


@dataclass(frozen=True)
class EvidenceRequirement:
    """一題所需的證據條件。gap check 直接對它比對。

    刻意是資料而非邏輯 —— 讓「這一題要什麼」可以被印出來、被斷言、
    被寫進 Execution Log，而不是散落在 if 判斷裡。
    """

    question_type: QuestionType
    required_facets: frozenset[Facet]
    minimum_independent_sources: int
    require_both_directions: bool
    """為真時，只有單邊方向的證據不足以收斂 —— 假設驗證的核心要求。"""
    require_symmetric_coverage: bool
    """為真時，兩個標的的證據面數量必須對齊 —— 比較分析的核心要求。"""
    assets: tuple[Asset, ...]
    unavailable_facets: frozenset[Facet] = frozenset()
    """本題型原該覆蓋、但在當前分析模式下取不到的證據面。

    回測模式只有 OHLCV(技術面)可用,籌碼/基本/情緒面無合規來源。與其把它們
    當成永遠關不掉的缺口拖垮收斂,不如在此記下「本該要、但取不到」的意圖,
    交給 _assemble 轉成明確的限制說明(資料不可得) —— 這是誠實,不是遺漏。
    """

    def describe(self) -> str:
        """給模型與 Execution Log 看的敘述。"""
        lines = [f"題型：{self.question_type.value}"]
        lines.append(f"需覆蓋證據面：{', '.join(sorted(f.value for f in self.required_facets))}")
        lines.append(f"最低獨立來源數：{self.minimum_independent_sources}")
        if self.require_both_directions:
            lines.append("**必須同時取得支持與反對證據** —— 只有單邊不足以下判斷。")
        if self.require_symmetric_coverage:
            names = "、".join(a.value for a in self.assets)
            lines.append(f"**{names} 兩邊的證據面覆蓋必須對稱** —— 不可一邊詳一邊略。")
        if self.unavailable_facets:
            facets = "、".join(sorted(f.value for f in self.unavailable_facets))
            lines.append(f"回測模式資料不可得的證據面(將列為限制)：{facets}")
        return "\n".join(lines)


def classify_question(question: str, assets: tuple[Asset, ...]) -> QuestionType:
    """判定題型。

    優先序刻意是「比較 → 假設 → 摘要」：一題可以同時帶比較與假設語言
    （「BTC 比 ETH 強，對嗎」），此時對稱覆蓋是更難滿足的約束，
    先確保它，反方要求由 `derive_requirement` 一併保留。
    """
    lowered = question.casefold()

    if len(assets) >= 2 or _matches(lowered, _COMPARISON_MARKERS):
        return QuestionType.COMPARATIVE_ANALYSIS
    if _matches(lowered, _HYPOTHESIS_MARKERS):
        return QuestionType.HYPOTHESIS_VERIFICATION
    return QuestionType.MARKET_SUMMARY


def derive_requirement(
    question: str,
    assets: tuple[Asset, ...],
    question_type: QuestionType | None = None,
    *,
    regime: AnalysisRegime = AnalysisRegime.LIVE,
) -> EvidenceRequirement:
    """由題型推導證據需求。

    比較題刻意**不要求四面向全滿**：兩個標的各補四面向會吃掉整個
    十五分鐘預算。對稱性比廣度更能回答「誰比較強」。

    `regime` 決定資料現實:回測模式只有 OHLCV(技術面)有合規來源,籌碼/基本/
    情緒面取不到。市場摘要題在回測下改為只深挖技術面,另三面記入
    `unavailable_facets`,交由 _assemble 轉成限制說明,而不是拖垮收斂的死缺口。
    """
    kind = question_type or classify_question(question, assets)
    lowered = question.casefold()
    # 比較題若同時帶了假設語言，反方要求要一併保留。
    hypothesis_flavoured = _matches(lowered, _HYPOTHESIS_MARKERS)

    if kind is QuestionType.COMPARATIVE_ANALYSIS:
        return EvidenceRequirement(
            question_type=kind,
            required_facets=frozenset({Facet.TECHNICAL, Facet.POSITIONING}),
            minimum_independent_sources=3,
            require_both_directions=hypothesis_flavoured,
            require_symmetric_coverage=True,
            assets=assets,
        )

    if kind is QuestionType.HYPOTHESIS_VERIFICATION:
        return EvidenceRequirement(
            question_type=kind,
            required_facets=frozenset(Facet),
            minimum_independent_sources=3,
            require_both_directions=True,
            require_symmetric_coverage=False,
            assets=assets,
        )

    required_facets = frozenset(Facet)
    unavailable_facets: frozenset[Facet] = frozenset()
    if regime is AnalysisRegime.BACKTEST:
        # 回測只有資料集 OHLCV(技術面)合規;其餘三面本該覆蓋但取不到。
        required_facets = frozenset({Facet.TECHNICAL})
        unavailable_facets = frozenset(Facet) - required_facets
    return EvidenceRequirement(
        question_type=kind,
        required_facets=required_facets,
        minimum_independent_sources=2,
        require_both_directions=False,
        require_symmetric_coverage=False,
        assets=assets,
        unavailable_facets=unavailable_facets,
    )


def mentioned_assets(question: str, primary: Asset) -> tuple[Asset, ...]:
    """從題目文字中找出被提及的受涵蓋幣種。

    `primary` 永遠是第一個 —— 它來自使用者明確選定的標的，
    比從文字猜出來的更可信。回傳最多兩個：命題的比較題是兩兩比較。
    """
    lowered = question.casefold()
    found: list[Asset] = [primary]
    for asset in Asset:
        if asset is primary:
            continue
        if re.search(rf"\b{asset.value.casefold()}\b", lowered):
            found.append(asset)
    return tuple(found[:2])


def _matches(lowered_question: str, markers: tuple[str, ...]) -> bool:
    return any(marker.casefold() in lowered_question for marker in markers)


__all__ = [
    "EvidenceRequirement",
    "QuestionType",
    "classify_question",
    "derive_requirement",
    "mentioned_assets",
]
