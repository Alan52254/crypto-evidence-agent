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


class FigureKind(Enum):
    """圖表的來源方式。決定它的溯源語意。"""

    GENERATED = "generated"
    """由本系統從原始數值繪製 —— 可被重算，屬於高可信度證據的視覺化。"""

    EXTERNAL = "external"
    """外部既有圖片（新聞、研報中的圖表）—— 我們只是引用，不保證其製圖正確。"""


@dataclass(frozen=True)
class Figure:
    """證據的視覺形式 —— 一張圖，連同它的出處與說明。

    刻意獨立於 `SourceExcerpt`：來源片段的語意是「可引用的原文」，
    把 base64 圖片塞進 `excerpt.text` 會造成兩個問題 ——
    它不是可引用的文字，而且那段文字會被送進提示詞，讓數 KB 的
    圖片資料排擠掉真正的證據。

    `data_uri` 與 `source_url` 至少要有一個：前者是我們自己畫的，
    後者是引用外部的。兩者都沒有的 Figure 無法呈現，也無法溯源。
    """

    kind: FigureKind
    caption: str
    data_uri: str | None = None
    source_url: str | None = None
    alt: str = ""

    def __post_init__(self) -> None:
        if not self.data_uri and not self.source_url:
            raise ValueError("Figure 必須有 data_uri 或 source_url 之一，否則無法呈現或溯源")

    @property
    def renderable_src(self) -> str:
        """呈現時要用的來源。自繪圖優先用內嵌資料，避免依賴外部連線。"""
        return self.data_uri or self.source_url or ""


@dataclass(frozen=True)
class Evidence:
    """證據 —— 帶穩定識別碼的不可變事實單元，永遠保有回到來源片段的路徑。

    `event_key` 用於同事件歸併：兩則描述同一事件的證據會被併成一個
    （保留雙方的來源片段），因為轉載不構成獨立證據（ADR 0002）。

    `figures` 是這項證據的視覺形式（K 線圖、走勢圖、新聞中的圖表）。
    圖屬於證據而不是屬於報告：這樣每一張圖都掛在一個有識別碼、有出處的
    觀察上，報告裡的圖因此天生可溯源。
    """

    id: str
    facet: Facet
    summary: str
    stance_hint: float
    excerpts: tuple[SourceExcerpt, ...]
    event_key: str | None = None
    figures: tuple[Figure, ...] = ()


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
    limitations: tuple[str, ...] = ()
    model_used: str = ""
    """產出本報告的模型識別碼（如 anthropic.claude-sonnet-4-6、gemini-3.6-flash）。

    由 run.py 在組裝報告時填入。空字串代表未知（舊報告相容）。
    用途：同一系統可能在不同時刻用不同模型，此欄位讓使用者與回測比較
    能區分「是模型差異」還是「是 prompt/資料差異」造成的輸出不同。
    """
    schema_version: str = "1.0"
    """prompt + JSON schema 的版本標記。

    同一模型不同版本的 prompt 也可能造成輸出差異。記這個版本才能在
    半年後回頭比較時，分辨「是模型換了」還是「是 prompt 改了」。
    變更時機：CLAIMS_SCHEMA 結構改變、或 SYNTHESIS_SYSTEM prompt 大改時遞增。
    """
    """本次分析明確承認的限制 —— 由分析回合動態計算,不是寫死的清單。

    來源:回測模式取不到的證據面、未關閉的必補缺口、被拒絕/爭議的判斷。
    命題明確要求「對不確定性與限制的清楚說明」,所以限制是一等輸出,
    必須進到報告本體讓評審看得到,而非只留在推論軌跡裡。
    """
    analysis_window_start: datetime | None = None
    """分析涵蓋的最早時間點 —— 由證據的 retrieved_at 與內容時間推導。"""
    analysis_window_end: datetime | None = None
    """分析涵蓋的最晚時間點 —— 由證據的 retrieved_at 推導。"""
    review_applied: bool = False
    """這份報告是否經過 Layer 3 review（語氣修飾 + 面向矛盾解釋）。

    False 代表 review 層被跳過（provider 不支援純文字通道），
    報告中的語氣強度與矛盾處理完全由 synthesise 階段的模型原始輸出決定，
    未經額外審查。使用者應據此判斷報告的精修程度。
    """
    synthesis_complete: bool = True
    """推理層（synthesise）是否成功完成。

    False 代表推理層未能產出任何推論/結論（額度用罄、逾時、格式失敗），
    報告僅包含事實層觀察。此時方向（stance）與信心度（confidence）
    **不可被視為有效判斷** — 它們是公式套用的殘餘值，不是推理的結果。
    to_markdown() 會在 False 時於報告最上方顯示醒目警告。
    """

    def to_markdown(self) -> str:
        """由已過濾的結構化判斷渲染 —— 不是從散文剪裁出來的。"""
        lines = [f"# {self.asset.value} 分析報告", "", f"**分析題目**：{self.question}", ""]

        # ─── 推理未完成時，最上方顯示醒目警告，覆蓋正常的方向/信心度 ───
        if not self.synthesis_complete:
            lines.append("## ⚠️ 分析未完成")
            lines.append("")
            lines.append(
                "**推理層未能完成**（額度用罄或逾時）。以下僅呈現已蒐集的原始觀察事實，"
                "**未產出推論與結論**。"
            )
            lines.append("")
            lines.append("**方向**：N/A（推理未完成，不可解讀為市場中性）")
            lines.append("**信心度**：N/A（推理未完成）")
        else:
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

        # 方法論標記 — 讓使用者知道這份報告由哪個模型/schema 版本產出
        if self.model_used:
            lines.append(f"**模型**：{self.model_used} (schema v{self.schema_version})")
        if not self.review_applied:
            lines.append("**⚠️ 注意**：本報告未經 review 層審查（語氣修飾、面向矛盾解釋未套用）")

        if isinstance(self.confidence, InsufficientEvidence):
            if self.synthesis_complete:
                # 正常的證據不足情況（推理有跑但證據不夠）
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
            # else: synthesis_complete=False 時，信心度已在上方顯示 N/A，不重複
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

        # 限制說明是一等輸出 —— 命題要求「對不確定性與限制的清楚說明」，
        # 這是評審在 final_report.md 讀到的誠實邊界，不可只留在推論軌跡。
        if self.limitations:
            lines.extend(["", "## 限制", ""])
            lines.extend(f"- {line}" for line in self.limitations)

        lines.extend(["", "---", "", DISCLAIMER])
        return "\n".join(lines)


def _detect_primary_asset_from_question(question: str) -> str | None:
    """從題目文字推導 primary asset。

    比賽題目格式固定為「請針對【幣種】…」或「分析【幣種】…」，
    且幣種池只有 BTC/ETH/SOL/BNB/XRP 五個。
    規則式偵測即可，不需要 LLM。

    回傳第一個在文字中出現的幣種代號（字串），找不到回傳 None。
    比較題（同時出現兩個幣種）回傳第一個出現的作為 primary。
    """
    import re
    # 用 word boundary 匹配，避免 "SOLANA" 裡的 SOL 被誤抓
    for symbol in ("BTC", "ETH", "SOL", "BNB", "XRP"):
        if re.search(rf"\b{symbol}\b", question, re.IGNORECASE):
            return symbol
    return None


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

        # ─── 題目文字優先：自動推導 primary asset ───
        # 比賽現場高壓情境下，操作者可能在 UI 選了 BTC 但題目問的是 BNB。
        # 以題目文字裡明確出現的幣種為準，自動覆蓋 asset 欄位。
        # 這防止「報告標題寫 BTC 但內容分析 BNB」的不一致問題。
        detected = _detect_primary_asset_from_question(self.question)
        if detected is not None and detected != self.asset:
            import logging
            logging.getLogger(__name__).warning(
                "[AnalysisRequest] 題目文字推導的幣種 (%s) 與 request.asset (%s) 不一致，"
                "以題目文字為準自動覆蓋。",
                detected, self.asset,
            )
            object.__setattr__(self, "asset", detected)


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
    "Figure",
    "FigureKind",
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
