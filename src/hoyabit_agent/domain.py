"""領域型別。

術語與 CONTEXT.md 一致 —— 證據、證據面、判斷、方向、信心度、推論軌跡
都是有精確定義的詞，不是泛稱。所有型別皆不可變。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


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
    """信心度 —— 五維加權評估（ADR 0002 + 競賽規格）。

    權重：來源品質25% + 覆蓋25% + 時效20% + 一致性20% + 完整性10%
    """

    value: float
    facet_stances: Mapping[Facet, Stance]
    # 五維分解分數 — 讓報告能呈現每個維度的貢獻
    independence: float = 0.0
    """來源品質 (25%) — 基於可信度加權的獨立來源數"""
    coverage: float = 0.0
    """覆蓋 (25%) — 四個證據面中有多少有證據"""
    freshness: float = 0.0
    """時效 (20%) — 最新證據距今多久"""
    agreement: float = 0.0
    """一致性 (20%) — 有表態的面之間方向是否一致"""
    completeness: float = 0.0
    """完整性 (10%) — 證據總數是否足夠"""


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
    analysis_window_start: datetime | None = None
    """分析涵蓋的最早時間點 —— 由證據的 retrieved_at 與內容時間推導。"""
    analysis_window_end: datetime | None = None
    """分析涵蓋的最晚時間點 —— 由證據的 retrieved_at 推導。"""

    def to_markdown(self) -> str:
        """由已過濾的結構化判斷渲染 —— 不是從散文剪裁出來的。"""
        lines = [f"# {self.asset.value} 分析報告", "", f"**分析題目**：{self.question}", ""]
        lines.append(f"**方向**：{self.stance.value}")

        # 時間窗 —— 讓使用者知道這份分析涵蓋的時間範圍
        if self.analysis_window_start and self.analysis_window_end:
            lines.append(
                f"**分析涵蓋時間窗**：{self.analysis_window_start.strftime('%Y-%m-%d %H:%M UTC')}"
                f" ~ {self.analysis_window_end.strftime('%Y-%m-%d %H:%M UTC')}"
            )
        elif self.analysis_window_end:
            lines.append(
                f"**資料截至**：{self.analysis_window_end.strftime('%Y-%m-%d %H:%M UTC')}"
            )

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
            lines.append(
                f"  - 來源品質：{self.confidence.independence:.0%}（權重25%）"
                f"　覆蓋：{self.confidence.coverage:.0%}（25%）"
                f"　時效：{self.confidence.freshness:.0%}（20%）"
                f"　一致性：{self.confidence.agreement:.0%}（20%）"
                f"　完整性：{self.confidence.completeness:.0%}（10%）"
            )

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

        lines.extend(["", "---", "", DISCLAIMER])
        return "\n".join(lines)


@dataclass(frozen=True)
class AnalysisRequest:
    """分析請求 —— 對某個幣種發起分析的意圖。

    `asset` 刻意是字串：閘門的職責就是把未經驗證的輸入擋在推理之外。
    """

    asset: str
    question: str = "請分析當前市場狀況"

    def __post_init__(self) -> None:
        from hoyabit_agent.sanitizer import sanitize_user_question

        object.__setattr__(self, "question", sanitize_user_question(self.question))


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
    "AnalysisRequest",
    "Asset",
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
