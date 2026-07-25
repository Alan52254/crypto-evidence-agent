"""評估基準 —— 跑一組分析並量化「這套 Agent 到底好不好」（ticket 11）。

四項門檻。前三項可從**回合與軌跡**確定性算出，因此可在接縫 2 上、
用腳本模型與假證據源測試，完全不碰網路：

| 基準 | 門檻 | 怎麼算 |
|---|---|---|
| 多輪 tool calling 成功率 | ≥95% | 產出掛證據的報告、且沒有重複的相同工具呼叫 |
| 單次分析壁鐘時間 | ≤15 分鐘 | 軌跡最後一個節點的 elapsed |
| 引用忠實度 | ≥90% | 由外部 judge 判定證據是否支撐該句；無 judge 時誠實回報「未量測」 |
| 單次分析成本 | ≤US$1 | 由外部成本模型估算；無則回報「未量測」 |

**忠實度與成本刻意需要外部注入**：忠實度要靠一個判斷模型，成本要靠 token 記帳，
兩者都不是這一層能憑空得出的。與其假裝算得出來（例如把「引用格式正確」
謊報成「忠實度 100%」），不如誠實地標成「未量測」。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from hoyabit_agent.domain import AnalysisOutcome, AnalysisRequest, Report, TraceNodeKind
from hoyabit_agent.run import analyse
from hoyabit_agent.seams import Clock, ModelProvider, Sources

TOOL_CALLING_SUCCESS_THRESHOLD = 0.95
WALL_CLOCK_THRESHOLD_SECONDS = 900.0
CITATION_FAITHFULNESS_THRESHOLD = 0.90
COST_THRESHOLD_USD = 1.0
MAX_STEPS = 10

# judge：給一則判斷文字與它掛載的證據摘要，回答「證據是否支撐這句話」。
Judge = Callable[[str, tuple[str, ...]], Awaitable[bool]]
# 成本模型：給一次回合，估算它花了多少美元。
CostModel = Callable[[AnalysisOutcome], float]


@dataclass(frozen=True)
class EvalCase:
    """一個評估案例 —— 就是一個要分析的標的。"""

    asset: str


@dataclass(frozen=True)
class RunResult:
    """單次回合的量測。被閘門拒絕的回合不在此列（它們不是模型的表現）。"""

    asset: str
    succeeded: bool
    steps: int
    wall_clock_seconds: float
    kept_claims: int
    faithful_claims: int | None
    cost_usd: float | None


@dataclass(frozen=True)
class Scorecard:
    """一組評估的彙總。"""

    results: tuple[RunResult, ...]
    rejected: int
    faithfulness_measured: bool
    cost_measured: bool

    @property
    def cases_run(self) -> int:
        return len(self.results)

    @property
    def tool_calling_success_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.succeeded) / len(self.results)

    @property
    def max_wall_clock_seconds(self) -> float:
        return max((r.wall_clock_seconds for r in self.results), default=0.0)

    @property
    def citation_faithfulness(self) -> float | None:
        """所有回合合計：忠實的判斷數 / 保留的判斷數。無 judge 時為 None。"""
        if not self.faithfulness_measured:
            return None
        kept = sum(r.kept_claims for r in self.results)
        if kept == 0:
            return None
        faithful = sum(r.faithful_claims or 0 for r in self.results)
        return faithful / kept

    @property
    def mean_cost_usd(self) -> float | None:
        if not self.cost_measured or not self.results:
            return None
        return sum(r.cost_usd or 0.0 for r in self.results) / len(self.results)

    def to_markdown(self) -> str:
        summary = f"跑了 {self.cases_run} 個案例（另有 {self.rejected} 個被幣種閘門拒絕）。"
        lines = ["# 評估成績單", "", summary, ""]
        lines.append("| 基準 | 門檻 | 實測 | 判定 |")
        lines.append("| --- | --- | --- | --- |")

        rate = self.tool_calling_success_rate
        lines.append(
            f"| 多輪 tool calling 成功率 | ≥{int(TOOL_CALLING_SUCCESS_THRESHOLD * 100)}% "
            f"| {rate:.0%} | {_verdict(rate >= TOOL_CALLING_SUCCESS_THRESHOLD)} |"
        )

        wall = self.max_wall_clock_seconds
        lines.append(
            f"| 單次分析壁鐘時間 | ≤{int(WALL_CLOCK_THRESHOLD_SECONDS / 60)} 分鐘 "
            f"| {wall:.1f} 秒 | {_verdict(wall <= WALL_CLOCK_THRESHOLD_SECONDS)} |"
        )

        faith = self.citation_faithfulness
        if faith is None:
            lines.append(
                f"| 引用忠實度 | ≥{int(CITATION_FAITHFULNESS_THRESHOLD * 100)}% "
                "| 未量測（需提供 judge） | — |"
            )
        else:
            lines.append(
                f"| 引用忠實度 | ≥{int(CITATION_FAITHFULNESS_THRESHOLD * 100)}% "
                f"| {faith:.0%} | {_verdict(faith >= CITATION_FAITHFULNESS_THRESHOLD)} |"
            )

        cost = self.mean_cost_usd
        if cost is None:
            lines.append("| 單次分析成本 | ≤US$1 | 未量測（需提供成本模型） | — |")
        else:
            lines.append(
                f"| 單次分析成本 | ≤US$1 | US${cost:.4f} "
                f"| {_verdict(cost <= COST_THRESHOLD_USD)} |"
            )

        return "\n".join(lines)


def _verdict(passed: bool) -> str:
    return "✅ 通過" if passed else "❌ 未達"


async def evaluate(
    cases: Sequence[EvalCase],
    *,
    sources: Sources | None = None,
    model: ModelProvider | None = None,
    sources_for: Callable[[EvalCase], Sources] | None = None,
    model_for: Callable[[EvalCase], ModelProvider] | None = None,
    clock: Clock | None = None,
    judge: Judge | None = None,
    cost_model: CostModel | None = None,
) -> Scorecard:
    """對每個案例跑一次分析並彙總成績單。

    `sources`/`model` 給所有案例共用一組；`sources_for`/`model_for` 讓每個案例
    拿到新的一組（腳本模型是有狀態的，同一組不能跑兩次）。
    """
    results: list[RunResult] = []
    rejected = 0

    for case in cases:
        case_sources = sources_for(case) if sources_for is not None else sources
        case_model = model_for(case) if model_for is not None else model
        if case_sources is None or case_model is None:
            raise ValueError("必須提供 sources/model 或 sources_for/model_for")

        outcome = await analyse(
            AnalysisRequest(asset=case.asset),
            case_sources,
            case_model,
            clock=clock,
        )
        if outcome.rejection is not None:
            rejected += 1
            continue

        results.append(await _measure(case, outcome, judge, cost_model))

    return Scorecard(
        results=tuple(results),
        rejected=rejected,
        faithfulness_measured=judge is not None,
        cost_measured=cost_model is not None,
    )


async def _measure(
    case: EvalCase,
    outcome: AnalysisOutcome,
    judge: Judge | None,
    cost_model: CostModel | None,
) -> RunResult:
    report = outcome.report
    steps = sum(1 for node in outcome.trace.nodes if node.kind is TraceNodeKind.PLAN)
    wall = outcome.trace.nodes[-1].elapsed_seconds if outcome.trace.nodes else 0.0
    kept = len(report.claims) if report is not None else 0

    succeeded = (
        report is not None
        and kept > 0
        and steps <= MAX_STEPS
        and not _has_repeated_call(outcome)
    )

    faithful = await _count_faithful(report, judge) if judge is not None else None
    cost = cost_model(outcome) if cost_model is not None else None

    return RunResult(
        asset=case.asset,
        succeeded=succeeded,
        steps=steps,
        wall_clock_seconds=wall,
        kept_claims=kept,
        faithful_claims=faithful,
        cost_usd=cost,
    )


def _has_repeated_call(outcome: AnalysisOutcome) -> bool:
    """同一個工具帶同一組參數被呼叫超過一次 = 在迴圈裡迷失。

    參數在 PLAN 節點的 detail 裡以 tool→json 的形式記著。
    """
    seen: set[tuple[str, str]] = set()
    for node in outcome.trace.nodes:
        if node.kind is not TraceNodeKind.PLAN:
            continue
        for execution in node.executions:
            key = (
                execution.tool,
                json.dumps(dict(execution.arguments), sort_keys=True, ensure_ascii=False),
            )
            if key in seen:
                return True
            seen.add(key)
    return False


def _canonical(args: str) -> str:
    """把參數字串正規化，好讓「同一組參數」的比較穩定。"""
    try:
        return json.dumps(json.loads(args), sort_keys=True, ensure_ascii=False)
    except ValueError:
        return args


async def _count_faithful(report: Report | None, judge: Judge) -> int:
    if report is None:
        return 0
    by_id = {item.id: item.summary for item in report.evidence}
    faithful = 0
    for claim in report.claims:
        summaries = tuple(by_id[eid] for eid in claim.evidence_ids if eid in by_id)
        if await judge(claim.text, summaries):
            faithful += 1
    return faithful


__all__ = [
    "CITATION_FAITHFULNESS_THRESHOLD",
    "COST_THRESHOLD_USD",
    "MAX_STEPS",
    "TOOL_CALLING_SUCCESS_THRESHOLD",
    "WALL_CLOCK_THRESHOLD_SECONDS",
    "CostModel",
    "EvalCase",
    "Judge",
    "RunResult",
    "Scorecard",
    "evaluate",
]
