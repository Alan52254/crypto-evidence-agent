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
from datetime import UTC, date, datetime

from hoyabit_agent import gaps as gap_rules
from hoyabit_agent.claim_ledger import LedgerResult, coverage_ratio, verify
from hoyabit_agent.domain import (
    AnalysisOutcome,
    AnalysisRequest,
    Asset,
    Claim,
    ClaimRole,
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
    analysis_regime,
)
from hoyabit_agent.question import EvidenceRequirement, derive_requirement, mentioned_assets
from hoyabit_agent.seams import (
    Arguments,
    Clock,
    EvidenceSource,
    GatherContext,
    ModelProvider,
    PlanDecision,
    Sources,
    ToolAttempt,
    ToolInvocation,
    ToolSpec,
)
from hoyabit_agent.tools import (
    as_of_reference,
    assess_confidence,
    evidence_gap,
    gate_asset,
    merge_independent_evidence,
    overall_stance,
)

DEFAULT_BUDGET_SECONDS = 900.0  # 15 分鐘 —— 上限，不是目標
DEFAULT_IO_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_ITERATIONS = 6
ASSEMBLY_RESERVE_SECONDS = 120.0
"""保留給撰寫、驗證與組裝的時間。

蒐集迴圈不得跑到預算的最後一秒 —— 那會讓 synthesise 沒有時間跑完，
結果是「蒐集了一堆證據但沒有報告」，比少蒐集兩輪糟糕得多。
"""


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
        logger.info(
            f"[Agent Step: {kind.value.upper()}] "
            f"(Elapsed: {self.elapsed:.2f}s) - {reason}{exec_info}"
        )

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
    today: date | None = None,
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

    # 時間立足點 —— 缺口的時效門檻與信心度的新鮮度一律錨定在分析截止日,
    # 不讀現實時鐘（ADR 0005）。回測時,同日證據視為當前,不被誤判為過期。
    as_of_ref = as_of_reference(request.as_of_date)

    # 分析模式由截止日推導 —— 判定回測/即時的唯一合法時鐘讀取點。
    # today 顯式注入(測試可控),production 省略時退回現實日期。
    regime = analysis_regime(request.as_of_date, today=today or datetime.now(UTC).date())

    # 題型分類 —— 決定「這一題需要什麼證據」。確定性判斷，不押在模型回應上。
    # 回測模式下,市場摘要只要求技術面,另三面記為資料不可得(見 derive_requirement)。
    involved = mentioned_assets(request.question, asset)
    requirement: EvidenceRequirement = derive_requirement(
        request.question, involved, regime=regime
    )
    recorder.record(
        TraceNodeKind.GAP_CHECK,
        f"分析模式：{regime.value}\n題型判定：{requirement.question_type.value}\n"
        f"{requirement.describe()}",
    )

    # 蒐集迴圈的硬截止 —— 保留組裝時間，見 ASSEMBLY_RESERVE_SECONDS。
    gather_deadline = max(budget_seconds - ASSEMBLY_RESERVE_SECONDS, budget_seconds * 0.5)

    gathered: tuple[Evidence, ...] = ()
    attempts: tuple[ToolAttempt, ...] = ()
    # 工具名稱只有 `spec.name` 一個來源 —— 模型看到的、MCP 暴露的、
    # 這裡查表用的，永遠是同一個字串。
    #
    # 依分析模式過濾來源 —— 不合規的來源（回測下的 live 工具）在此就被排除，
    # 模型連工具存在都不知道，而不是呼叫後才丟棄結果。這是防堵「偷看未來」
    # 的實體鎖（ADR 0005 / Sol B），比只驗證回傳證據更嚴格。
    registry: dict[str, EvidenceSource] = {
        source.spec.name: source for source in sources if regime in source.supported_regimes
    }
    tools: tuple[ToolSpec, ...] = tuple(
        registry[name].spec for name in sorted(registry)
    )

    assessment = gap_rules.assess(gathered, requirement)
    used_fallback_plan = False
    for _ in range(max_iterations):
        gap = evidence_gap(gathered, now=as_of_ref)
        assessment = gap_rules.assess(gathered, requirement)
        if not assessment:
            # 收斂由**題型規則**判定，不是模型說夠了就停。
            recorder.record(
                TraceNodeKind.GAP_CHECK,
                f"題型 {requirement.question_type.value} 的所有必補缺口已關閉\n"
                f"{assessment.describe()}",
                gap_before=assessment.missing_facets,
            )
            break

        if recorder.elapsed >= gather_deadline:
            recorder.record(
                TraceNodeKind.BUDGET_EXHAUSTED,
                f"蒐集時間預算耗盡（保留 {ASSEMBLY_RESERVE_SECONDS:.0f} 秒組裝），"
                f"以現有證據組裝報告。未關閉的缺口："
                f"{', '.join(g.kind for g in assessment.blocking_gaps) or '無'}",
                gap_before=assessment.missing_facets,
                gap_after=assessment.missing_facets,
            )
            break

        context = GatherContext(
            asset=asset,
            gap=assessment.missing_facets,
            evidence=gathered,
            attempts=attempts,
            question=request.question,
            gap_state=gap,
            requirement_brief=requirement.describe(),
            gap_brief=assessment.describe(),
            analysis_timestamp=datetime.now(UTC).isoformat(),

        )
        decision = await model.plan(context, tools)

        if not decision.invocations and not gathered and not used_fallback_plan:
            # 規劃層不可用（額度用罄、逾時）且**還沒蒐集到任何證據**。
            # 此時直接收斂會產出空報告，但證據源本身是健康的，而且
            # 「有哪些工具、要分析哪個標的」都是已知的 —— 不需要模型也能
            # 組出一個合理的基線計畫。
            #
            # 只在第一輪動用一次：它是保底，不是常態路徑。後續輪次若模型
            # 仍不回應，那代表推理能力確實不可用，應該帶著限制收斂。
            used_fallback_plan = True
            decision = PlanDecision(
                invocations=_fallback_plan(tools, asset),
                reason=(
                    f"規劃層未回應（{decision.reason}）。"
                    "改用保底檢索計畫：對所有可用證據源各取一次預設參數，"
                    "以免在證據源健康的情況下產出空報告。"
                ),
            )
            # 不在這裡記軌跡 —— 下游的 PLAN 節點會帶著這個 reason 與
            # 實際排定的工具一起記錄，重複記會讓軌跡讀者以為跑了兩輪。

        if not decision.invocations:
            # 模型婉拒再蒐集。**模型的婉拒不等於缺口已關閉** —— 若規則仍判定
            # 有必補缺口，那個事實必須留在軌跡上並進入報告的限制說明，
            # 而不是被模型的一句「夠了」蓋過去。
            outstanding = ", ".join(g.kind for g in assessment.blocking_gaps)
            note = (
                f"{decision.reason}\n"
                f"（規則判定仍有未關閉的必補缺口：{outstanding}，將列為報告限制）"
                if outstanding
                else decision.reason
            )
            recorder.record(
                TraceNodeKind.GAP_CHECK,
                note,
                gap_before=assessment.missing_facets,
                gap_after=assessment.missing_facets,
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
            gap_before=gap.missing_facets,
            gap_after=gap.missing_facets,
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
                    gap_before=gap.missing_facets,
                    gap_after=gap.missing_facets,
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
        gap_after = evidence_gap(gathered, now=as_of_ref)
        called = ", ".join(inv.tool for inv in decision.invocations)
        recorder.record(
            TraceNodeKind.GATHER,
            f"自 {called} 蒐集到 {len(fresh)} 項證據",
            evidence_ids=tuple(item.id for item in fresh),
            gap_before=gap.missing_facets,
            gap_after=gap_after.missing_facets,
            executions=completed_records,
        )

    report = _assemble(
        asset,
        gathered,
        await model.synthesise(asset, gathered, request.question),
        recorder,
        request.question,
        assessment,
        as_of=request.as_of_date,
        unavailable_facets=requirement.unavailable_facets,
        boundary_notes=requirement.boundary_notes,
    )
    return AnalysisOutcome(
        run_id=identifier,
        report=report,
        trace=recorder.build(identifier),
        rejection=None,
    )


def _fallback_plan(tools: tuple[ToolSpec, ...], asset: Asset) -> tuple[ToolInvocation, ...]:
    """保底檢索計畫 —— 規劃層不可用時的確定性替代。

    只帶 `asset`，其餘參數交給各證據源的預設值。這是刻意的：
    每個證據源已經對自己的參數有合理預設（見各 `fetch` 的 `bounded_int`），
    在這裡猜參數只會把猜錯的責任從模型移到我們身上。
    """
    return tuple(
        ToolInvocation(tool=spec.name, arguments={"asset": asset.value}) for spec in tools
    )


def _fallback_facts(gathered: tuple[Evidence, ...]) -> tuple[DraftClaim, ...]:
    """把證據摘要直接轉成事實層判斷 —— 推理層不可用時的降級路徑。

    每則判斷只引用產生它的那一項證據，因此必然通過引用檢核：
    這裡沒有任何推論，只有「我們觀察到了什麼」。

    刻意附一則 `WATCH` 判斷說明降級本身 —— 讀者必須看得出這份報告
    為什麼沒有結論，否則「沒有結論」會被誤讀成「市場沒有方向」。
    """
    facts = [
        DraftClaim(
            text=item.summary,
            evidence_ids=(item.id,),
            facet=item.facet,
            role=ClaimRole.FACT,
        )
        for item in gathered
    ]
    if facts:
        facts.append(
            DraftClaim(
                text=(
                    "推理層在本次回合中無法回應（額度用罄或逾時），"
                    "因此報告僅呈現已取得的觀察事實，未產出推論與結論。"
                    "方向與信心度不可據此解讀為市場中性。"
                ),
                evidence_ids=(gathered[0].id,),
                facet=gathered[0].facet,
                role=ClaimRole.WATCH,
            )
        )
    return tuple(facts)


def _assemble(
    asset: Asset,
    gathered: tuple[Evidence, ...],
    drafts: tuple[DraftClaim, ...],
    recorder: _TraceRecorder,
    question: str = "請分析當前市場狀況",
    assessment: gap_rules.GapAssessment | None = None,
    *,
    as_of: date | None = None,
    unavailable_facets: frozenset[Facet] = frozenset(),
    boundary_notes: tuple[str, ...] = (),
) -> Report:
    """組裝階段 —— 判斷先經帳本驗證，再渲染。

    驗證與撰寫刻意由不同階段執行：撰寫者是模型，驗證者是確定性規則。
    同一次模型呼叫既下判斷又自我驗證，等於讓被告當法官。
    """
    recorder.record(TraceNodeKind.SYNTHESISE, f"推理層產出 {len(drafts)} 則待檢核判斷")

    if not drafts and gathered:
        # 推理層不可用（額度用罄、逾時、格式不符）但證據已在手。
        # 「有 N 項證據卻零則判斷」的報告對讀者毫無用處，也違反
        # 「永不因逾時而失敗」的精神 —— 那條不變式的實質是
        # **總是以現有證據交付可讀的東西**。
        #
        # 只補事實層：事實層判斷本質上是證據摘要的複述，確定性可得，
        # 不需要模型。推論與結論刻意不補 —— 那才是真正需要推理的部分，
        # 憑空生成會變成沒有依據的方向性判斷。
        drafts = _fallback_facts(gathered)
        recorder.record(
            TraceNodeKind.SYNTHESISE,
            f"推理層未回應，改以現有證據組出 {len(drafts)} 則事實層判斷。"
            "本次報告不含推論與結論，方向判定不可用 —— 已列為限制。",
        )

    ledger: LedgerResult = verify(drafts, gathered)

    for claim in ledger.unsupported:
        recorder.record(
            TraceNodeKind.CLAIM_DROPPED,
            f"判斷未通過引用驗證，轉為限制說明：{claim.text}"
            f"（{'；'.join(claim.reasons)}）",
        )
    # 爭議判斷刻意**不**記在 CLAIM_DROPPED —— 它們仍在報告裡。
    # 把「移出報告」與「保留但標記」混成同一個節點種類，會讓軌跡讀者
    # 無法分辨系統到底拒絕了什麼。
    for claim in ledger.contested:
        recorder.record(
            TraceNodeKind.SYNTHESISE,
            f"判斷支撐薄弱，保留於報告但標記爭議：{claim.text}"
            f"（{'；'.join(claim.reasons)}）",
        )

    coverage = coverage_ratio(ledger)
    recorder.record(
        TraceNodeKind.SYNTHESISE,
        f"帳本驗證完成：supported={len(ledger.supported)}、"
        f"contested={len(ledger.contested)}、unsupported={len(ledger.unsupported)}；"
        f"結論層證據覆蓋率 {coverage:.0%}",
    )

    # 限制分兩層，因為它們有不同的讀者與不同的安全性：
    #
    # report_limitations —— **資料/方法論**層級，進報告本體給評審看。
    # 回測取不到的面明列為「資料不可得」，未關閉的必補缺口如實揭露。
    # 這些**不含任何被拒判斷的原文**，因此放進報告本體是安全的
    # （見 ADR 0005 / Sol A；命題要求明確指出限制，不把不確定性藏起來）。
    report_limitations = [
        f"{facet.value} 面資料不可得（回測模式僅有資料集 OHLCV，無合規的即時來源）"
        for facet in sorted(unavailable_facets, key=lambda f: f.value)
    ]
    # 邊界聲明（未命中已知題型、預測題劃界）—— 同樣是資料/方法論層級,
    # 不含被拒判斷原文,可安全進報告本體（見 question.EvidenceRequirement）。
    report_limitations.extend(boundary_notes)
    if assessment is not None:
        report_limitations.extend(
            f"未關閉的證據缺口：{gap.detail}" for gap in assessment.blocking_gaps
        )

    # trace_limitations —— 再額外納入被拒/爭議判斷的說明（含原文），僅供推論軌跡稽核。
    # 這些**刻意不進報告本體**：被拒判斷的原文出現在報告裡，會被誤讀成系統的主張。
    trace_limitations = [*report_limitations, *ledger.limitations()]
    if trace_limitations:
        recorder.record(
            TraceNodeKind.REPORT,
            "報告限制說明：\n" + "\n".join(f"- {line}" for line in trace_limitations),
        )

    # 通過驗證（含爭議）的判斷進報告；被拒絕的留在 dropped_claims 供軌跡呈現。
    kept = tuple(
        Claim(
            text=claim.text,
            evidence_ids=claim.evidence_ids,
            facet=claim.facet,
            role=claim.role,
        )
        for claim in ledger.admissible
    )
    dropped = tuple(
        DraftClaim(
            text=claim.text,
            evidence_ids=claim.evidence_ids,
            facet=claim.facet,
            role=claim.role,
        )
        for claim in ledger.unsupported
    )

    confidence = assess_confidence(gathered, as_of=as_of)
    stance = overall_stance(confidence)
    recorder.record(
        TraceNodeKind.REPORT,
        f"方向 {stance.value}，保留 {len(kept)} 則判斷、丟棄 {len(dropped)} 則",
        evidence_ids=tuple(item.id for item in gathered),
    )

    # 從證據的 retrieved_at 推導時間窗 —— 使用者據此判斷報告的時效性
    all_timestamps = [
        excerpt.retrieved_at
        for item in gathered
        for excerpt in item.excerpts
        if excerpt.retrieved_at is not None
    ]
    window_start = min(all_timestamps) if all_timestamps else None
    window_end = max(all_timestamps) if all_timestamps else None

    return Report(
        asset=asset,
        stance=stance,
        confidence=confidence,
        claims=kept,
        dropped_claims=dropped,
        evidence=gathered,
        question=question,
        limitations=tuple(report_limitations),
        analysis_window_start=window_start,
        analysis_window_end=window_end,
    )


__all__ = ["analyse"]
