"""領域型別。

術語與 CONTEXT.md 一致 —— 證據、證據面、判斷、方向、信心度、推論軌跡
都是有精確定義的詞，不是泛稱。所有型別皆不可變。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import ClassVar


class Asset(Enum):
    """受涵蓋幣種。這個列舉**就是**白名單 —— 系統不判斷任何資產是不是水幣，
    只判斷它在不在這個集合內。"""

    BTC = "BTC"
    ETH = "ETH"
    SOL = "SOL"
    BNB = "BNB"
    XRP = "XRP"


class Facet(Enum):
    """證據面。恰好四種，互斥且窮盡。與資料源正交 ——
    同一個資料源可以產出多個面的證據。"""

    TECHNICAL = "technical"
    POSITIONING = "positioning"
    FUNDAMENTAL = "fundamental"
    SENTIMENT = "sentiment"


class LabelAspect(Enum):
    """對一段文本打分時，究竟在問哪個問題。

    同一篇報導可以「事件本身利多、但評論語氣悲觀」——
    那正是信心度要捕捉的分歧。若兩面共用同一個分數，
    它們會永遠一致，系統性地灌高信心度。
    """

    SENTIMENT = "sentiment"
    """這段文字**透露的輿論傾向**是什麼。"""

    FUNDAMENTAL = "fundamental"
    """這個事件對該資產的**實質影響**是什麼。"""


class Stance(Enum):
    """方向。刻意不叫「建議」或「訊號」—— 那些詞帶投資顧問意涵。"""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass(frozen=True)
class SourceExcerpt:
    """來源片段 —— 證據所指向的原始文本或數值本身，連同完整出處。"""

    source_id: str
    url: str
    retrieved_at: datetime
    locator: str
    text: str


@dataclass(frozen=True)
class Evidence:
    """證據 —— 帶穩定識別碼的不可變事實單元，永遠保有回到來源片段的路徑。

    `event_key` 用於同事件歸併：兩則描述同一事件的證據會被併成一個
    （保留雙方的來源片段），因為轉載不構成獨立證據（ADR 0002）。
    """

    id: str
    facet: Facet
    summary: str
    stance_hint: float
    excerpts: tuple[SourceExcerpt, ...]
    event_key: str | None = None


class ClaimRole(Enum):
    FACT = "fact"
    INFERENCE = "inference"
    CONCLUSION = "conclusion"
    COUNTER_EVIDENCE = "counter_evidence"
    RISK = "risk"
    INVALIDATION = "invalidation"
    WATCH = "watch"


@dataclass(frozen=True)
class DraftClaim:
    """推理層輸出的判斷 —— 尚未通過引用檢核。

    刻意是結構化物件而非散文：引用檢核是對物件陣列做過濾，
    Markdown 在過濾之後才渲染。絕不事後剪裁句子。
    """

    text: str
    evidence_ids: tuple[str, ...]
    facet: Facet
    role: ClaimRole = ClaimRole.INFERENCE


@dataclass(frozen=True)
class Claim:
    """通過引用檢核的判斷。存在即代表它掛載了至少一個真實存在的證據。"""

    text: str
    evidence_ids: tuple[str, ...]
    facet: Facet
    role: ClaimRole = ClaimRole.INFERENCE


@dataclass(frozen=True)
class Confidence:
    """信心度 —— 證據面之間的一致程度（ADR 0002），而非模型的自我感覺。"""

    value: float
    facet_stances: Mapping[Facet, Stance]


class Insufficiency(Enum):
    """信心度算不出來的兩種原因。分開表達，因為對讀者的意義不同。"""

    TOO_FEW_FACETS = "too_few_facets"
    """蒐集到的證據面太少 —— 再找資料可能有救。"""

    NO_DIRECTIONAL_SIGNAL = "no_directional_signal"
    """證據面夠多，但幾乎都不表態 —— 市場當下就是沒有方向。"""


@dataclass(frozen=True)
class InsufficientEvidence:
    """第三態：證據不足，信心度**無法計算**。

    與「低信心度」語意不同 —— 少了它，只蒐到一個面時會得出
    「四面一致 → 高信心」的荒謬結果。
    """

    facets_present: frozenset[Facet]
    minimum_facets_required: int
    cause: Insufficiency = Insufficiency.TOO_FEW_FACETS
    directional_facets: frozenset[Facet] = frozenset()
    facet_stances: Mapping[Facet, Stance] = field(default_factory=dict)
    """全部四個面的傾向。算不出信心度時**更**需要呈現 ——
    讀者正是在這個情況下要看出是誰沉默。"""


ConfidenceResult = Confidence | InsufficientEvidence


class ToolExecutionStatus(Enum):
    PLANNED = "planned"
    SUCCEEDED = "succeeded"
    UNAVAILABLE = "unavailable"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


@dataclass(frozen=True)
class ToolExecutionRecord:
    """One lossless tool execution; repeated tool names remain separate records."""

    tool: str
    asset: Asset
    arguments: Mapping[str, object]
    status: ToolExecutionStatus
    observation: str = ""
    evidence_ids: tuple[str, ...] = ()
    duration_seconds: float = 0.0


class TraceNodeKind(Enum):
    ASSET_GATE = "asset_gate"
    PLAN = "plan"
    GATHER = "gather"
    SOURCE_UNAVAILABLE = "source_unavailable"
    GAP_CHECK = "gap_check"
    BUDGET_EXHAUSTED = "budget_exhausted"
    SYNTHESISE = "synthesise"
    CLAIM_DROPPED = "claim_dropped"
    REPORT = "report"


@dataclass(frozen=True)
class TraceNode:
    """軌跡節點 —— 推論軌跡中的單一決策點。

    節點與證據之間的連線，就是命題所說的「點對點之間為什麼」。
    """

    seq: int
    kind: TraceNodeKind
    reason: str
    evidence_ids: tuple[str, ...] = ()
    gap_before: frozenset[Facet] = frozenset()
    gap_after: frozenset[Facet] = frozenset()
    elapsed_seconds: float = 0.0
    executions: tuple[ToolExecutionRecord, ...] = ()
    gap_state: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Trace:
    """推論軌跡 —— 交付物，不是除錯副產品。"""

    run_id: str
    nodes: tuple[TraceNode, ...]


@dataclass(frozen=True)
class Rejection:
    reason: str


DISCLAIMER = "本報告由自動化系統依公開資料生成，僅供資訊參考，不是投資建議。"


@dataclass(frozen=True)
class Report:
    """分析報告 —— 一個方向、一個信心度、一組全部掛載了證據的判斷。

    這是純資料的結構化結論物件。單幣分析為純函數且輸出此型別，
    使得日後加入跨幣比較層是 fan-out 而非重構。
    """

    asset: Asset
    stance: Stance
    confidence: ConfidenceResult
    claims: tuple[Claim, ...]
    dropped_claims: tuple[DraftClaim, ...]
    evidence: tuple[Evidence, ...]
    question: str = "請分析當前市場狀況"
    limitations: tuple[str, ...] = ()
    """本次分析明確承認的限制 —— 由分析回合動態計算,不是寫死的清單。

    來源:回測模式取不到的證據面、未關閉的必補缺口、被拒絕/爭議的判斷。
    命題明確要求「對不確定性與限制的清楚說明」,所以限制是一等輸出,
    必須進到報告本體讓評審看得到,而非只留在推論軌跡裡。
    """

    def to_markdown(self) -> str:
        """由已過濾的結構化判斷渲染 —— 不是從散文剪裁出來的。"""
        lines = [f"# {self.asset.value} 分析報告", "", f"**分析題目**：{self.question}", ""]
        lines.append(f"**方向**：{self.stance.value}")

        if isinstance(self.confidence, InsufficientEvidence):
            present = "、".join(sorted(f.value for f in self.confidence.facets_present)) or "無"
            if self.confidence.cause is Insufficiency.NO_DIRECTIONAL_SIGNAL:
                lines.append(
                    f"**信心度**：無法計算 —— 蒐集到 {present} 面的證據，"
                    f"但其中表態的不足 {self.confidence.minimum_facets_required} 面。"
                    "當下沒有足夠的方向性訊號。"
                )
            else:
                lines.append(
                    f"**信心度**：證據不足，無法計算"
                    f"（僅蒐集到 {present} 面，需至少 "
                    f"{self.confidence.minimum_facets_required} 面）"
                )
        else:
            lines.append(f"**信心度**：{self.confidence.value:.2f}（證據面之間的一致程度）")

        # 四個面的傾向**不論算不算得出信心度都要呈現** —— 算不出來時更需要，
        # 因為讀者正是在那個情況下要看出是誰沉默、誰根本沒查到證據。
        lines.extend(["", "| 證據面 | 傾向 | 證據 |", "| --- | --- | --- |"])
        counts = Counter(item.facet for item in self.evidence)
        for facet, stance in sorted(
            self.confidence.facet_stances.items(), key=lambda kv: kv[0].value
        ):
            found = counts[facet]
            lines.append(f"| {facet.value} | {stance.value} | {found} 項 |" if found else
                         f"| {facet.value} | 未蒐集到證據 | 0 項 |")

        lines.extend(["", "## 判斷", ""])
        for claim in self.claims:
            citations = "".join(f"[{eid}]" for eid in claim.evidence_ids)
            lines.append(f"- {claim.text} {citations}")

        # 被丟棄的判斷刻意**不**渲染進報告本文 —— 它們留在 `dropped_claims`
        # 與推論軌跡中，供軌跡前端呈現給檢視者。報告只呈現站得住的內容。

        # 限制說明是一等輸出 —— 命題要求「對不確定性與限制的清楚說明」，
        # 這是評審在 final_report.md 讀到的誠實邊界，不可只留在推論軌跡。
        if self.limitations:
            lines.extend(["", "## 限制", ""])
            lines.extend(f"- {line}" for line in self.limitations)

        lines.extend(["", "---", "", DISCLAIMER])
        return "\n".join(lines)


@dataclass(frozen=True)
class AnalysisRequest:
    """分析請求 —— 對某個幣種發起分析的意圖。

    `asset` 刻意是字串：閘門的職責就是把未經驗證的輸入擋在推理之外。

    `as_of_date` 是這次分析的**時間立足點** —— 允許使用資料的最後 UTC 日期,
    也是全系統的總開關(推導分析模式、過濾合規來源、錨定新鮮度)。
    未指定時預設為資料集截止日 2026-05-31。
    """

    DATASET_CUTOFF: ClassVar[date] = date(2026, 5, 31)

    asset: str
    question: str = "請分析當前市場狀況"
    as_of_date: date = DATASET_CUTOFF

    def __post_init__(self) -> None:
        from hoyabit_agent.sanitizer import sanitize_user_question

        object.__setattr__(self, "question", sanitize_user_question(self.question))


class AnalysisRegime(Enum):
    """分析模式 —— 由分析截止日推導出的行為分歧,非獨立輸入。

    後續管線(來源過濾、缺口需求、新鮮度)一律 branch 在這個語意開關上,
    而不是散落的 `is_live` 布林旗標。
    """

    BACKTEST = "backtest"
    """截止日早於今天:只有截止日當下可知的資料合規,live 工具會偷看未來。"""

    LIVE = "live"
    """截止日不早於今天:即時來源合規。"""


def analysis_regime(as_of_date: date, *, today: date) -> AnalysisRegime:
    """由分析截止日推導分析模式。

    `today` 是**顯式注入的參考點**,刻意不在此讀 `datetime.now()` ——
    時間是被傳進來的值,不是隱藏的全域副作用。這是「時光機」測試基底的前提:
    同一組輸入永遠得到同一個模式,CI 不會因為現實時鐘跨過某個門檻而隨機失敗。

    截止日等於今天算 LIVE:「截至今天」可知的資料本就合規,不算偷看未來。
    """
    return AnalysisRegime.BACKTEST if as_of_date < today else AnalysisRegime.LIVE


@dataclass(frozen=True)
class AnalysisOutcome:
    """分析回合的產物。被閘門拒絕時 `report` 為 None，但軌跡永遠存在。"""

    run_id: str
    report: Report | None
    trace: Trace
    rejection: Rejection | None


__all__ = [
    "DISCLAIMER",
    "AnalysisOutcome",
    "AnalysisRegime",
    "AnalysisRequest",
    "Asset",
    "analysis_regime",
    "Claim",
    "ClaimRole",
    "Confidence",
    "ConfidenceResult",
    "DraftClaim",
    "Evidence",
    "Facet",
    "Insufficiency",
    "InsufficientEvidence",
    "LabelAspect",
    "Rejection",
    "Report",
    "SourceExcerpt",
    "Stance",
    "Trace",
    "TraceNode",
    "TraceNodeKind",
    "ToolExecutionRecord",
    "ToolExecutionStatus",
]
