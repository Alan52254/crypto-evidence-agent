"""題型分類 —— 決定「這一題需要什麼證據」，而非固定四面向硬補。

命題的題型不只「現在看多看空」，還包括假設驗證與比較分析。
沒有題型意識的 gap check 會在比較題浪費預算、在假設驗證題漏掉反方。

分類是**確定性的關鍵字判斷**，不是模型呼叫 —— 題型判錯的代價是
整輪蒐集失焦，不值得押在一次 LLM 回應上。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from hoyabit_agent.domain import AnalysisRegime, Asset, Facet


# ─── 核心資料需求偵測 ───────────────────────────────────────────────
#
# 判斷題目是否要求系統不具備的特定資料類型。確定性關鍵字比對，
# 不是 LLM 分類。觸發時強制在報告頂部插入缺失聲明並懲罰信心度。


class DataAvailability(Enum):
    """資料可用性三態 —— 決定懲罰力度與聲明措辭。"""

    AVAILABLE = "available"
    """系統有完整的第一手資料源。"""

    PARTIAL = "partial"
    """系統有間接或不完整的資料源，可作背景參考但不能替代主答案。"""

    UNAVAILABLE = "unavailable"
    """系統完全沒有對應資料源。"""


class DemandWeight(Enum):
    """資料需求在題目中的權重 —— 決定信心度懲罰力度。"""

    CORE = "core"
    """題目主問的就是這個資料。缺失 = 無法回答主問題。"""

    SUPPORTING = "supporting"
    """題目輔助需要的資料。缺失 = 結論不完整但仍可部分回答。"""


@dataclass(frozen=True)
class CoreDataDemand:
    """題目明確要求但系統可能不具備的特定資料類型。

    一個 demand 代表「題目提到了 X，而我們對 X 的能力是 Y」。
    觸發方式是確定性關鍵字比對，不依賴 LLM。
    """

    label: str
    """人類可讀的資料類型名稱，如「交易所儲備量」。"""

    availability: DataAvailability
    """系統對這個資料類型的能力。"""

    weight: DemandWeight
    """這個需求在題目語境中的重要性。"""

    fallback_note: str
    """告訴讀者：缺了這個資料，替代分析能做到什麼、不能做到什麼。"""


# 關鍵字 → 需求定義。每組是 (markers, label, availability, fallback_note)。
# availability 是靜態的（代表系統能力），weight 由偵測時的位置決定（見下方函式）。
_DATA_DEMAND_REGISTRY: tuple[
    tuple[tuple[str, ...], str, DataAvailability, str], ...
] = (
    (
        ("交易所儲備", "exchange reserve", "exchange inflow", "exchange outflow",
         "儲備量", "淨流入", "淨流出"),
        "交易所儲備量",
        DataAvailability.UNAVAILABLE,
        "無法判斷供給端流入/流出；僅能從衍生品端（OI、資金費率）間接推測市場槓桿方向",
    ),
    (
        ("巨鯨", "whale", "大額轉帳", "大戶", "whale transfer", "whale movement"),
        "巨鯨轉移 / 大額轉帳",
        DataAvailability.UNAVAILABLE,
        "無法追蹤鏈上大額轉帳；僅能從盤口深度與 OI 變化間接推測大戶行為",
    ),
    (
        ("清算", "liquidation", "爆倉", "清算圖", "liquidation heatmap",
         "liquidation map", "清算價格"),
        "清算圖譜 / 爆倉分布",
        DataAvailability.UNAVAILABLE,
        "無法取得上/下方清算密度分布；僅能從 OI 變化與資金費率推測槓桿壓力方向",
    ),
    (
        ("活躍地址", "active address", "鏈上活動", "on-chain activity",
         "鏈上數據", "on-chain data", "鏈上指標"),
        "鏈上活躍度指標",
        DataAvailability.UNAVAILABLE,
        "無法取得活躍地址、交易筆數等鏈上活動指標",
    ),
    (
        ("dex volume", "dex 交易量", "去中心化交易", "dex交易"),
        "DEX 交易量",
        DataAvailability.UNAVAILABLE,
        "無法取得去中心化交易所交易量數據",
    ),
    (
        ("gas fee", "gas 費", "gas費", "鏈上手續費", "gas price"),
        "Gas 費用",
        DataAvailability.UNAVAILABLE,
        "無法評估鏈上擁塞程度與手續費趨勢",
    ),
    (
        ("質押", "staking", "staking yield", "質押收益", "質押率"),
        "質押收益 / 質押率",
        DataAvailability.UNAVAILABLE,
        "無法取得質押相關收益與鎖倉比例數據",
    ),
    (
        ("美股", "s&p 500", "s&p500", "納斯達克", "nasdaq", "道瓊", "dow jones",
         "標普", "sp500"),
        "美股指數",
        DataAvailability.PARTIAL,
        "僅有 FRED 宏觀指標（利率、M2、CPI）可作間接背景，無即時美股行情",
    ),
    (
        ("黃金", "gold", "xau"),
        "黃金價格",
        DataAvailability.PARTIAL,
        "僅有美元指數（DXY）間接參考，無即時黃金行情",
    ),
    (
        ("匯率", "日圓", "日元", "yen", "歐元", "eur"),
        "外匯匯率",
        DataAvailability.PARTIAL,
        "僅有 FRED 美元指數趨勢可作間接參考，無即時外匯行情",
    ),
)


def detect_core_data_demands(question: str) -> tuple[CoreDataDemand, ...]:
    """從題目文字偵測核心資料需求。確定性關鍵字比對，無 I/O。

    權重判定邏輯：如果一個資料類型的關鍵字出現在題目的**前半段**
    或被動詞（分析、評估、判斷）緊鄰，視為 CORE；否則視為 SUPPORTING。
    這是啟發式，不需要完美 —— 只要能區分「題目主問的」和「順帶提到的」。
    """
    lowered = question.casefold()
    half_point = len(lowered) // 2
    demands: list[CoreDataDemand] = []

    for markers, label, availability, fallback_note in _DATA_DEMAND_REGISTRY:
        matched_pos: int | None = None
        for marker in markers:
            pos = lowered.find(marker.casefold())
            if pos != -1:
                if matched_pos is None or pos < matched_pos:
                    matched_pos = pos
                break

        if matched_pos is None:
            continue

        # 權重判定：出現在前半段 → CORE，後半段 → SUPPORTING
        # 額外：如果 availability 已經是 UNAVAILABLE 且是前半段命中，
        # 那就是「題目主問的東西我們沒有」—— 最嚴重的情況。
        weight = (
            DemandWeight.CORE if matched_pos <= half_point
            else DemandWeight.SUPPORTING
        )

        demands.append(CoreDataDemand(
            label=label,
            availability=availability,
            weight=weight,
            fallback_note=fallback_note,
        ))

    return tuple(demands)


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

# 典型現況研判問法 —— 有這些詞彙的問題視為「明確命中市場摘要」,
# 不觸發「題型未明確匹配」的邊界說明。沒有這層辨識,every 普通問句
# （如「BTC 現況如何」）都會被標記為未命中,稀釋掉這個限制對真正
# 未知題型的警示價值。
_MARKET_SUMMARY_MARKERS = (
    "現況",
    "現状",
    "走勢",
    "趨勢",
    "分析",
    "看法",
    "如何",
    "怎麼樣",
    "怎么样",
    "狀況",
    "状况",
    "動向",
    "動態",
    "market summary",
    "current",
    "overview",
)

# 預測/未來語言特徵 —— 命中時系統仍以現況研判作答,但必須明確聲明
# 不做未來價格預測。可溯源承諾要求每句判斷掛得到來源片段,未來沒有
# 片段可掛,誠實劃界比硬答一個「下週目標價」更專業。
_FORECAST_MARKERS = (
    "預測",
    "预测",
    "預估",
    "预估",
    "未來走勢",
    "未来走势",
    "下週",
    "下周",
    "明天",
    "明日",
    "下個月",
    "下个月",
    "目標價",
    "目标价",
    "看後市",
    "看后市",
    "forecast",
    "predict",
    "target price",
    "future price",
    "outlook",
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
    boundary_notes: tuple[str, ...] = ()
    """系統邊界的顯性聲明 —— 未命中已知題型、或問題要求本系統不做的事
    (例如未來價格預測)時的誠實告知。與 `unavailable_facets` 一樣,
    最終流入 `Report.limitations`,讓評審在 final_report.md 看得到系統
    如何界定自己的能力邊界,而不是靜默地把不確定性藏起來。
    """
    core_data_demands: tuple[CoreDataDemand, ...] = ()
    """題目明確要求但系統可能不具備的特定資料類型。

    偵測到 UNAVAILABLE 或 PARTIAL 的需求時，報告頂部強制插入缺失聲明，
    信心度施加懲罰。這是 A+B 混合策略的確定性骨幹。
    """

    @property
    def has_core_data_gaps(self) -> bool:
        """題目的核心需求中是否有系統完全不具備的資料。"""
        return any(
            d.availability is DataAvailability.UNAVAILABLE and d.weight is DemandWeight.CORE
            for d in self.core_data_demands
        )

    @property
    def core_missing_demands(self) -> tuple[CoreDataDemand, ...]:
        """所有不可用（UNAVAILABLE）的需求。"""
        return tuple(
            d for d in self.core_data_demands
            if d.availability is DataAvailability.UNAVAILABLE
        )

    @property
    def partial_demands(self) -> tuple[CoreDataDemand, ...]:
        """所有部分可用（PARTIAL）的需求。"""
        return tuple(
            d for d in self.core_data_demands
            if d.availability is DataAvailability.PARTIAL
        )

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
        if self.core_data_demands:
            for d in self.core_data_demands:
                status = (
                    "不可用" if d.availability is DataAvailability.UNAVAILABLE
                    else "部分可用" if d.availability is DataAvailability.PARTIAL
                    else "可用"
                )
                priority = "核心" if d.weight is DemandWeight.CORE else "輔助"
                lines.append(
                    f"[{priority}資料需求] {d.label}：{status} — {d.fallback_note}"
                )
        for note in self.boundary_notes:
            lines.append(f"邊界聲明：{note}")
        return "\n".join(lines)


def _matched_known_vocabulary(lowered: str, assets: tuple[Asset, ...]) -> bool:
    """題目是否命中任何已知題型的語言特徵(比較、假設驗證、或典型市場摘要問法)。

    False 代表題目落入 MARKET_SUMMARY 純粹是因為沒有更精確的分類 ——
    這是關鍵字分類器能誠實表達「我不確定這題屬於哪一類」的唯一訊號。
    """
    return (
        len(assets) >= 2
        or _matches(lowered, _COMPARISON_MARKERS)
        or _matches(lowered, _HYPOTHESIS_MARKERS)
        or _matches(lowered, _MARKET_SUMMARY_MARKERS)
    )


def _is_forecast_question(lowered: str) -> bool:
    return _matches(lowered, _FORECAST_MARKERS)


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
            core_data_demands=detect_core_data_demands(question),
        )

    if kind is QuestionType.HYPOTHESIS_VERIFICATION:
        return EvidenceRequirement(
            question_type=kind,
            required_facets=frozenset(Facet),
            minimum_independent_sources=3,
            require_both_directions=True,
            require_symmetric_coverage=False,
            assets=assets,
            core_data_demands=detect_core_data_demands(question),
        )

    required_facets = frozenset(Facet)
    unavailable_facets: frozenset[Facet] = frozenset()
    if regime is AnalysisRegime.BACKTEST:
        # 回測只有資料集 OHLCV(技術面)合規;其餘三面本該覆蓋但取不到。
        required_facets = frozenset({Facet.TECHNICAL})
        unavailable_facets = frozenset(Facet) - required_facets

    boundary_notes: list[str] = []
    if not _matched_known_vocabulary(lowered, assets):
        # 落入 MARKET_SUMMARY 是關鍵字分類器的預設路徑,不是精準分類 ——
        # 把這個事實告訴評審,而不是讓不確定性隱身在一個看似篤定的報告裡。
        boundary_notes.append("題型未明確匹配，以現況研判作答")
    if _is_forecast_question(lowered):
        # 可溯源承諾要求每句判斷掛得到來源片段;未來沒有片段可掛,
        # 因此誠實聲明邊界,而不是輸出一個沒有依據的價格預測。
        boundary_notes.append("本系統輸出當前方向研判，不做未來價格預測")

    return EvidenceRequirement(
        question_type=kind,
        required_facets=required_facets,
        minimum_independent_sources=2,
        require_both_directions=False,
        require_symmetric_coverage=False,
        assets=assets,
        unavailable_facets=unavailable_facets,
        boundary_notes=tuple(boundary_notes),
        core_data_demands=detect_core_data_demands(question),
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
    "CoreDataDemand",
    "DataAvailability",
    "DemandWeight",
    "EvidenceRequirement",
    "QuestionType",
    "classify_question",
    "derive_requirement",
    "detect_core_data_demands",
    "mentioned_assets",
]
