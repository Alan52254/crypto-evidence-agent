"""接縫 2 —— 分析回合。最高的接縫，也是主要測試面。

外層是固定的骨架（閘門 → 規劃 → 蒐集 → 缺口檢查 → 組裝），保證一定收斂；
**蒐集迴圈內部是真正的 ReAct** —— 呼叫哪些工具、要不要再挖一輪，
全由模型依據當前的證據缺口決定。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable, Mapping

from hoyabit_agent.domain import (
    AnalysisOutcome,
    AnalysisRequest,
    Asset,
    DraftClaim,
    Evidence,
    Facet,
    Rejection,
    Report,
    ToolExecutionRecord,
    ToolExecutionStatus,
    Trace,
    TraceNode,
    TraceNodeKind,
)
from hoyabit_agent.seams import (
    Arguments,
    Clock,
    EvidenceSource,
    GatherContext,
    ModelProvider,
    Sources,
    ToolAttempt,
    ToolInvocation,
    ToolSpec,
)
from hoyabit_agent.tools import (
    assess_confidence,
    check_citations,
    evidence_gap,
    gate_asset,
    merge_independent_evidence,
    overall_stance,
)

DEFAULT_BUDGET_SECONDS = 900.0  # 15 分鐘 —— 上限，不是目標
DEFAULT_IO_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_ITERATIONS = 6


class _SystemClock:
    def now(self) -> float:
        return time.monotonic()


class _TraceRecorder:
    """推論軌跡的建構器 —— 分析回合內唯一有副作用的部件，故意隔離。"""

    def __init__(
        self, clock: Clock, on_trace: Callable[[TraceNode], None] | None = None
    ) -> None:
        self._clock = clock
        self._start = clock.now()
        self._nodes: list[TraceNode] = []
        self._on_trace = on_trace

    @property
    def elapsed(self) -> float:
        return self._clock.now() - self._start

    def record(
        self,
        kind: TraceNodeKind,
        reason: str,
        *,
        evidence_ids: tuple[str, ...] = (),
        gap_before: frozenset[Facet] = frozenset(),
        gap_after: frozenset[Facet] = frozenset(),
        executions: tuple[ToolExecutionRecord, ...] = (),
        gap_state: Mapping[str, object] | None = None,
        detail: Mapping[str, str] | None = None,
    ) -> None:
        node = TraceNode(
            seq=len(self._nodes),
            kind=kind,
            reason=reason,
            evidence_ids=evidence_ids,
            gap_before=gap_before,
            gap_after=gap_after,
            elapsed_seconds=self.elapsed,
            executions=executions,
            gap_state=gap_state or {},
        )
        self._nodes.append(node)

        import logging
        logger = logging.getLogger("hoyabit_agent.trace")
        exec_info = ""
        if executions:
            details = [f"{e.tool}({e.arguments}) -> {e.status.value}" for e in executions]
            exec_info = f" | Executions: [{', '.join(details)}]"
        logger.info(f"🤖 [Agent Step: {kind.value.upper()}] (Elapsed: {self.elapsed:.2f}s) - {reason}{exec_info}")

        if self._on_trace is not None:
            self._on_trace(node)

    def build(self, run_id: str) -> Trace:
        return Trace(run_id=run_id, nodes=tuple(self._nodes))


async def _invoke(
    invocation: ToolInvocation,
    registry: Mapping[str, EvidenceSource],
    asset: Asset,
    timeout_seconds: float,
) -> tuple[Evidence, ...] | None:
    """執行模型決定的一次工具調用。

    回傳 None 代表「該來源暫時不可用」—— 掛起、逾時、例外一律等價。
    逾時上限是硬性的：沒有它，一個網路波動造成的 hang 就能把 15 分鐘
    預算耗在無意義的等待上，而不是花在推理。

    模型幻覺出不存在的工具名稱也走這條路 —— 那同樣是「拿不到證據」，
    不是需要中斷分析的錯誤。
    """
    source = registry.get(invocation.tool)
    if source is None:
        return None
    try:
        return await asyncio.wait_for(
            source.fetch(asset, invocation.arguments), timeout=timeout_seconds
        )
    except TimeoutError:
        return None
    except Exception:  # noqa: BLE001 — 資料源失效是預期情況，不是錯誤
        return None


def _describe(arguments: Arguments) -> str:
    if not arguments:
        return "（未指定參數）"
    return json.dumps(arguments, ensure_ascii=False, sort_keys=True)


async def analyse(
    request: AnalysisRequest,
    sources: Sources,
    model: ModelProvider,
    *,
    clock: Clock | None = None,
    budget_seconds: float = DEFAULT_BUDGET_SECONDS,
    io_timeout_seconds: float = DEFAULT_IO_TIMEOUT_SECONDS,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    run_id: str | None = None,
    on_trace: Callable[[TraceNode], None] | None = None,
) -> AnalysisOutcome:
    """對一個受涵蓋幣種跑一次分析回合。

    不變式（呼叫者可以依賴的事實）：

    * **接受依賴，不自行建構** —— `sources` 與 `model` 由外部注入。
      這是可測試性的全部基礎。
    * **回傳結果，不產生副作用** —— 持久化由呼叫者處理。
    * **永不因逾時而失敗。** 預算耗盡時回傳「以現有證據組裝的報告」，
      因此呼叫者不需要寫任何逾時處理。
    * 被閘門拒絕時 `report` 為 None，但軌跡永遠存在。
    """
    the_clock = clock or _SystemClock()
    recorder = _TraceRecorder(the_clock, on_trace)
    identifier = run_id or str(uuid.uuid4())

    asset = gate_asset(request.asset)
    if asset is None:
        reason = f"{request.asset} 不在受涵蓋幣種內，不予分析"
        recorder.record(TraceNodeKind.ASSET_GATE, reason)
        return AnalysisOutcome(
            run_id=identifier,
            report=None,
            trace=recorder.build(identifier),
            rejection=Rejection(reason=reason),
        )

    recorder.record(TraceNodeKind.ASSET_GATE, f"{asset.value} 為受涵蓋幣種，進入分析")

    gathered: tuple[Evidence, ...] = ()
    attempts: tuple[ToolAttempt, ...] = ()
    # 工具名稱只有 `spec.name` 一個來源 —— 模型看到的、MCP 暴露的、
    # 這裡查表用的，永遠是同一個字串。
    registry: dict[str, EvidenceSource] = {source.spec.name: source for source in sources}
    tools: tuple[ToolSpec, ...] = tuple(
        registry[name].spec for name in sorted(registry)
    )

    for _ in range(max_iterations):
        gap = evidence_gap(gathered)
        if not gap:
            recorder.record(TraceNodeKind.GAP_CHECK, "四個證據面皆已達最低證據數", gap_before=gap)
            break

        if recorder.elapsed >= budget_seconds:
            recorder.record(
                TraceNodeKind.BUDGET_EXHAUSTED,
                "時間預算耗盡，以現有證據組裝報告",
                gap_before=gap,
                gap_after=gap,
            )
            break

        context = GatherContext(
            asset=asset,
            gap=gap,
            evidence=gathered,
            attempts=attempts,
            question=request.question,
            gap_state=gap,
        )
        decision = await model.plan(context, tools)

        if not decision.invocations:
            # 模型判定無需再蒐集。這不是一次規劃 —— PLAN 節點的語意是
            # 「決定去蒐集什麼」，婉拒屬於缺口檢查的結果。
            recorder.record(
                TraceNodeKind.GAP_CHECK, decision.reason, gap_before=gap, gap_after=gap
            )
            break

        planned = tuple(
            ToolExecutionRecord(
                inv.tool,
                gate_asset(str(inv.arguments.get("asset", asset.value))) or asset,
                dict(inv.arguments),
                ToolExecutionStatus.PLANNED,
            )
            for inv in decision.invocations
        )
        recorder.record(
            TraceNodeKind.PLAN,
            decision.reason,
            gap_before=gap,
            gap_after=gap,
            executions=planned,
            detail={inv.tool: _describe(inv.arguments) for inv in decision.invocations},
        )

        results = await asyncio.gather(
            *(_invoke(inv, registry, asset, io_timeout_seconds) for inv in decision.invocations)
        )

        fresh: list[Evidence] = []
        fresh_attempts: list[ToolAttempt] = []
        for invocation, result in zip(decision.invocations, results, strict=True):
            if result is None:
                recorder.record(
                    TraceNodeKind.SOURCE_UNAVAILABLE,
                    f"證據源 {invocation.tool} 暫時不可用，改由其他來源補足",
                    gap_before=gap,
                    gap_after=gap,
                    detail={invocation.tool: _describe(invocation.arguments)},
                )
                fresh_attempts.append(
                    ToolAttempt(invocation.tool, invocation.arguments, "unavailable")
                )
                continue
            fresh.extend(result)
            fresh_attempts.append(
                ToolAttempt(invocation.tool, invocation.arguments, f"{len(result)} 項證據")
            )

        completed_records = tuple(
            ToolExecutionRecord(
                inv.tool,
                gate_asset(str(inv.arguments.get("asset", asset.value))) or asset,
                dict(inv.arguments),
                ToolExecutionStatus.UNAVAILABLE if res is None else ToolExecutionStatus.SUCCEEDED,
                "unavailable" if res is None else f"{len(res)} 項證據",
                tuple(item.id for item in res) if res else (),
            )
            for inv, res in zip(decision.invocations, results, strict=True)
        )
        attempts = attempts + tuple(fresh_attempts)
        gathered = merge_independent_evidence([*gathered, *fresh])
        gap_after = evidence_gap(gathered)
        called = ", ".join(inv.tool for inv in decision.invocations)
        recorder.record(
            TraceNodeKind.GATHER,
            f"自 {called} 蒐集到 {len(fresh)} 項證據",
            evidence_ids=tuple(item.id for item in fresh),
            gap_before=gap,
            gap_after=gap_after,
            executions=completed_records,
        )

    report = _assemble(
        asset,
        gathered,
        await model.synthesise(asset, gathered, request.question),
        recorder,
        request.question,
    )
    return AnalysisOutcome(
        run_id=identifier,
        report=report,
        trace=recorder.build(identifier),
        rejection=None,
    )


def _assemble(
    asset: Asset,
    gathered: tuple[Evidence, ...],
    drafts: tuple[DraftClaim, ...],
    recorder: _TraceRecorder,
    question: str = "請分析當前市場狀況",
) -> Report:
    """組裝階段 —— 對結構化判斷陣列過濾，過濾後才渲染。"""
    recorder.record(TraceNodeKind.SYNTHESISE, f"推理層產出 {len(drafts)} 則待檢核判斷")

    kept, dropped = check_citations(drafts, gathered)
    for draft in dropped:
        recorder.record(
            TraceNodeKind.CLAIM_DROPPED,
            f"判斷未掛載有效證據，已丟棄：{draft.text}",
        )

    confidence = assess_confidence(gathered)
    stance = overall_stance(confidence)
    recorder.record(
        TraceNodeKind.REPORT,
        f"方向 {stance.value}，保留 {len(kept)} 則判斷、丟棄 {len(dropped)} 則",
        evidence_ids=tuple(item.id for item in gathered),
    )

    return Report(
        asset=asset,
        stance=stance,
        confidence=confidence,
        claims=kept,
        dropped_claims=dropped,
        evidence=gathered,
        question=question,
    )


__all__ = ["analyse"]
