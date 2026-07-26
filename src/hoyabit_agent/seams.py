"""三個接縫的介面定義。

介面 = 呼叫者必須知道的**全部**事實，不只型別簽章 ——
還包括不變式、錯誤模式、與效能特性。這些寫在 docstring 裡，
因為它們和簽章一樣是介面的一部分。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from hoyabit_agent.domain import (
    AnalysisOutcome,
    Asset,
    DraftClaim,
    Evidence,
    Facet,
    LabelAspect,
)
from hoyabit_agent.tools import EvidenceGap

JsonSchema = Mapping[str, Any]
Arguments = Mapping[str, Any]


@dataclass(frozen=True)
class ToolSpec:
    """一個工具的完整規格：名稱、用途、參數的 JSON Schema。

    **一份規格，三個消費者** —— 這是本專案「混合 MCP」的兌現點：

    1. 交給推理層模型當 function declaration（模型據此決定參數）
    2. 交給 MCP server 當 tool 定義（Kiro / Claude Desktop 據此掛載）
    3. 交給我們自己的執行器當呼叫契約

    三者共用同一份規格，因此不可能出現「模型以為的介面」與
    「MCP 暴露的介面」不一致這種漂移。
    """

    name: str
    description: str
    parameters: JsonSchema


@dataclass(frozen=True)
class ToolInvocation:
    """模型決定的一次工具調用 —— 含它自己選定的參數。

    參數由模型決定（而非我們寫死）正是原生 tool calling 與
    「受控規劃」的分野：軌跡上看得到模型自己挑了什麼時框、什麼關鍵字。
    """

    tool: str
    arguments: Arguments = field(default_factory=dict)


@dataclass(frozen=True)
class ToolAttempt:
    """先前一次工具調用的結果摘要，回饋給模型作為下一步的依據。"""

    tool: str
    arguments: Arguments
    outcome: str


@dataclass(frozen=True)
class GatherContext:
    """蒐集迴圈當下的完整狀態 —— 模型據此決定下一步。

    `gap` 是樞紐：它把「模型該做什麼」從一段模糊的 prompt
    變成一個可計算、可顯示、可斷言的狀態。
    """

    asset: Asset
    gap: frozenset[Facet]
    evidence: tuple[Evidence, ...]
    attempts: tuple[ToolAttempt, ...]
    question: str = "請分析當前市場狀況"
    gap_state: EvidenceGap | None = None
    requirement_brief: str = ""
    """題型與該題的證據需求敘述（由 `question.EvidenceRequirement.describe`）。

    刻意是字串而非結構：它唯一的消費者是提示詞，而把 `EvidenceRequirement`
    的型別耦合進這個介面會讓接縫依賴一個它不需要理解的概念。
    """
    gap_brief: str = ""
    """題型導向的缺口敘述（由 `gaps.GapAssessment.describe`）。

    與 `gap`／`gap_state` 並存而非取代：前兩者是通用的面向缺口，
    這一項才帶得出「缺反方」「兩邊不對稱」這類題型專屬的缺口。
    """


@dataclass(frozen=True)
class PlanDecision:
    """推理層的一次規劃決策：要調用哪些工具、帶什麼參數、以及為什麼。

    `reason` 會原樣進入推論軌跡 —— 它就是評審看到的「模型當時為何這樣選」。
    空的 `invocations` 代表模型認為無需再蒐集，迴圈隨即收斂。
    """

    invocations: tuple[ToolInvocation, ...]
    reason: str


@runtime_checkable
class EvidenceSource(Protocol):
    """接縫 1 —— 證據源。所有外部 I/O 藏在此後。

    不變式（呼叫者可以依賴的事實）：

    * `spec` 是這個來源的**唯一**介面描述。模型、MCP server、執行器
      三方共用它，因此不可能各自對參數有不同理解。
    * 回傳的每項證據**必定**帶至少一則來源片段。溯源能力不是選配。
    * **失效以空集合表達，不以例外表達。** 資料源掛掉、逾時、掛起，
      對呼叫者而言都是「這次沒有證據」，不是錯誤。這條約束把降級
      從呼叫端的責任移進模組內，是這個介面最重要的設計。
    * 不保證證據面：一個資料源可產出任意組合的證據面（正交性）。
    * 面對模型給的無效參數必須自行降級，不得拋例外 ——
      模型會給出不在 schema 內的東西，那是預期情況。
    * 呼叫者不需要知道額度、退避、金鑰、重試或逾時 —— 全部在實作內。
    """

    @property
    def spec(self) -> ToolSpec: ...

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]: ...


@runtime_checkable
class ModelProvider(Protocol):
    """接縫 3 —— 模型供應者。推理層與勞務層可獨立設定不同模型。

    不變式：

    * 輸出**必定**符合給定的結構。實作負責以 structured output 強制約束
      並在不符時重試 —— 呼叫者不需要寫任何解析或校驗。
    * 失敗以降級表達（回傳空結果），不以例外中斷分析回合。
    * 實作是無狀態的：同一個實例可服務多個分析回合，
      對話狀態由 `GatherContext` 明確傳入而非藏在實例裡。
    """

    async def plan(self, context: GatherContext, tools: tuple[ToolSpec, ...]) -> PlanDecision:
        """推理層：依當前證據缺口與先前嘗試，決定下一步調用哪些工具、帶什麼參數。

        這是蒐集迴圈真正動態的地方。
        """
        ...

    async def synthesise(
        self,
        asset: Asset,
        evidence: tuple[Evidence, ...],
        question: str = "請分析當前市場狀況",
    ) -> tuple[DraftClaim, ...]:
        """推理層：從證據推出判斷。

        回傳**結構化物件陣列**，不是 Markdown 散文。引用檢核對這個
        陣列過濾，過濾後才渲染 —— 這樣「無證據不進報告」才是可靠的，
        而不是脆弱的字串處理。
        """
        ...

    async def label(
        self,
        texts: Sequence[str],
        aspect: LabelAspect = LabelAspect.SENTIMENT,
    ) -> tuple[float, ...]:
        """勞務層：對多則來源片段推導傾向分數，各落在 −1 到 +1。

        `aspect` 決定問的是哪個問題 —— 同一篇報導的「輿論傾向」與
        「實質影響」可以相反，那正是信心度要捕捉的分歧。

        **刻意是批次介面。** Gemini 免費層是 10 RPM，逐則呼叫會讓
        30 則片段花掉 3 分鐘的壁鐘預算；一次送一批則是一次呼叫。

        分數永遠屬於一則片段。不存在「BTC 的情緒分數」——
        那是情緒彙總，由多個分數加權而成且必須可列舉組成。
        回傳長度必定與輸入相同。
        """
        ...


@runtime_checkable
class AnalysisStore(Protocol):
    """接縫 4 —— 分析回合的持久化。

    刻意**不**放進 `analyse` 裡：分析回合的不變式是「回傳結果，不產生副作用」，
    把寫入塞進去會讓接縫 2 沒有資料庫就測不動 —— 那是最不划算的交換。

    不變式：

    * `save` 是**原子的**：一次分析要嘛整個進去（回合、軌跡、證據、來源片段），
      要嘛完全沒進去。半個分析回合比沒有更糟，因為判斷會指向不存在的證據。
    * `save` 是**冪等的**：同一個回合識別碼存兩次不會產生重複資料。
    * `load` 取回的物件與存進去的**等價** —— 包括軌跡的順序、
      每項證據的全部來源片段、以及被引用檢核丟棄的判斷。
    * 找不到回合時回傳 None，不拋例外。
    """

    async def save(self, outcome: AnalysisOutcome) -> None: ...

    async def load(self, run_id: str) -> AnalysisOutcome | None: ...

    async def recent(self, limit: int = 20) -> tuple[str, ...]:
        """最近的回合識別碼，新的在前。軌跡前端用它列出可看的回合。"""
        ...


@runtime_checkable
class Clock(Protocol):
    """單調時鐘。以介面呈現，讓預算耗盡的行為可以被確定性地測試。"""

    def now(self) -> float: ...


# 刻意是序列而非 name→source 的字典：工具名稱只有 `spec.name` 一個來源，
# 因此不可能出現「字典鍵」與「模型看到的名稱」分歧這種 bug。
Sources = Sequence[EvidenceSource]

__all__ = [
    "AnalysisStore",
    "Arguments",
    "Clock",
    "EvidenceSource",
    "GatherContext",
    "JsonSchema",
    "ModelProvider",
    "PlanDecision",
    "Sources",
    "ToolAttempt",
    "ToolInvocation",
    "ToolSpec",
]
