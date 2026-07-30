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
from collections.abc import Awaitable, Callable, Mapping
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
    apply_contested_penalty,
    as_of_reference,
    assess_confidence,
    evidence_gap,
    facet_stances,
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
    unavailable_tools: set[str] = set()  # 已確認不可用的工具，不再重試
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

    # 預取低成本來源 —— 不需要模型決定，直接先拿。
    #
    # `candlestick_chart_builder` 也在此：技術面圖表不該取決於模型「有沒有想到
    # 要畫圖」。它是報告的固定組成，缺了它讀者只能讀數字讀不到走勢。
    # 回測模式下它不在 registry 內（LIVE only），因此會自動被跳過。
    # ─── 預取所有涉及的幣種 ───
    # 比較題（BTC vs ETH）或單幣題都走同一條路：對每個 involved asset 都跑 prefetch。
    # 單幣題 involved = (BTC,)，比較題 involved = (BTC, ETH)。
    # 這確保第二個幣種不需要靠模型「記得呼叫工具」才能拿到資料。
    # 共用來源（fred_macro, fear_greed_index）只取一次（它們不分幣種）。
    _per_asset_sources = (
        "binance_spot",
        "binance_derivatives",
        "coingecko_market",
        "market_dataset_context",
        "defillama_tvl",
        "candlestick_chart_builder",
        "official_announcements",
    )
    _global_sources = (
        "fear_greed_index",
        "fred_macro",
    )
    if regime.value == "live":
        all_prefetch_names = set(_per_asset_sources) | set(_global_sources)
        unknown_prefetch = all_prefetch_names - set(registry)
        if unknown_prefetch:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "[prefetch] 以下預取名字不在 LIVE registry 中（可能拼寫錯誤）: %s",
                ", ".join(sorted(unknown_prefetch)),
            )

    _prefetch_invocations: list[tuple[str, Asset]] = []
    for _asset in involved:
        for name in _per_asset_sources:
            if name in registry:
                _prefetch_invocations.append((name, _asset))
    for name in _global_sources:
        if name in registry:
            _prefetch_invocations.append((name, asset))  # 全域來源只用 primary asset
    prefetch_results = await asyncio.gather(
        *(
            _invoke(
                ToolInvocation(name, {"asset": _a.value}),
                registry, _a, io_timeout_seconds,
            )
            for name, _a in _prefetch_invocations
        ),
        return_exceptions=True,
    )
    for result in prefetch_results:
        if isinstance(result, tuple):
            gathered = merge_independent_evidence((*gathered, *result))

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
            used_fallback_plan = True
            decision = PlanDecision(
                invocations=_fallback_plan(tools, asset),
                reason=(
                    f"規劃層未回應（{decision.reason}）。"
                    "改用保底檢索計畫：對所有可用證據源各取一次預設參數，"
                    "以免在證據源健康的情況下產出空報告。"
                ),
            )
        elif not decision.invocations and assessment.missing_facets and not used_fallback_plan:
            # 模型覺得不需要更多資料（可能被預取的證據「騙」了），
            # 但系統的 gap check 說還有缺口。強制用 fallback 補齊。
            used_fallback_plan = True
            missing = ", ".join(f.value for f in assessment.missing_facets)
            decision = PlanDecision(
                invocations=_fallback_plan(tools, asset),
                reason=(
                    f"模型未發出工具呼叫但仍有未關閉缺口（{missing}）。"
                    "改用保底計畫補齊缺失面向。"
                ),
            )

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

        # 過濾掉已確認不可用的工具 — 不再浪費時間重試
        valid_invocations = tuple(
            inv for inv in decision.invocations
            if inv.tool not in unavailable_tools
        )
        if not valid_invocations and not gathered:
            # 所有工具都不可用且沒有任何證據 → 用 fallback plan
            if not used_fallback_plan:
                used_fallback_plan = True
                valid_invocations = tuple(
                    inv for inv in _fallback_plan(tools, asset)
                    if inv.tool not in unavailable_tools
                )
        if not valid_invocations:
            # 沒有可用的工具了，帶著現有證據收斂
            recorder.record(
                TraceNodeKind.BUDGET_EXHAUSTED,
                f"所有可用工具已嘗試或不可用（{', '.join(sorted(unavailable_tools))}），"
                "以現有證據組裝報告。",
                gap_before=gap.missing_facets,
                gap_after=gap.missing_facets,
            )
            break

        planned = tuple(
            ToolExecutionRecord(
                inv.tool,
                gate_asset(str(inv.arguments.get("asset", asset.value))) or asset,
                dict(inv.arguments),
                ToolExecutionStatus.PLANNED,
            )
            for inv in valid_invocations
        )
        recorder.record(
            TraceNodeKind.PLAN,
            decision.reason,
            gap_before=gap.missing_facets,
            gap_after=gap.missing_facets,
            executions=planned,
            detail={inv.tool: _describe(inv.arguments) for inv in valid_invocations},
        )

        results = await asyncio.gather(
            *(_invoke(inv, registry, asset, io_timeout_seconds) for inv in valid_invocations)
        )

        fresh: list[Evidence] = []
        fresh_attempts: list[ToolAttempt] = []
        for invocation, result in zip(valid_invocations, results, strict=True):
            if result is None:
                unavailable_tools.add(invocation.tool)
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
            for inv, res in zip(valid_invocations, results, strict=True)
        )
        attempts = attempts + tuple(fresh_attempts)
        gathered = merge_independent_evidence([*gathered, *fresh])
        gap_after = evidence_gap(gathered, now=as_of_ref)
        called = ", ".join(inv.tool for inv in valid_invocations)
        recorder.record(
            TraceNodeKind.GATHER,
            f"自 {called} 蒐集到 {len(fresh)} 項證據",
            evidence_ids=tuple(item.id for item in fresh),
            gap_before=gap.missing_facets,
            gap_after=gap_after.missing_facets,
            executions=completed_records,
        )

    # ─── 組裝階段 ───
    # 在送入 synthesise 前，把核心資料缺失上下文注入 question 字串。
    # 這讓模型在推理時就知道哪些資料不存在，而不是事後才發現。
    # seam 介面不變（question 本來就是自由文字），只是內容更豐富。
    synthesis_question = request.question
    from hoyabit_agent.question import CoreDataDemand, DataAvailability, DemandWeight as _DW

    _synth_demands = tuple(
        d for d in requirement.core_data_demands if isinstance(d, CoreDataDemand)
    )
    _synth_unavailable = [
        d for d in _synth_demands if d.availability is DataAvailability.UNAVAILABLE
    ]
    if _synth_unavailable:
        gap_labels = "、".join(d.label for d in _synth_unavailable)
        core_labels = [d.label for d in _synth_unavailable if d.weight is _DW.CORE]
        if core_labels:
            synthesis_question = (
                f"{request.question}\n\n"
                f"[系統標記] 本次分析不具備以下核心資料源：{gap_labels}。"
                f"其中「{'、'.join(core_labels)}」為題目主問的資料類型。"
                f"你的分析僅能基於已取得的替代面向證據，不得假裝已回答主問題。"
            )
        else:
            synthesis_question = (
                f"{request.question}\n\n"
                f"[系統標記] 本次分析不具備以下輔助資料源：{gap_labels}。"
                f"結論完整度受限，請在 watch 判斷中說明。"
            )

    drafts = await model.synthesise(asset, gathered, synthesis_question)

    # Post-synthesis review — 輕量自我審查，只修飾語氣不改結構。
    #
    # 只在供應者能做「純文字生成」時啟用。`synthesise` 不適合當審查通道：
    # 它回傳的是 DraftClaim 陣列而非文字，硬用它做審查只會拿到空結果
    # （這裡原本有一個永遠回傳 None 的 `_review_call`，等於審查從未生效）。
    if drafts:
        from hoyabit_agent.review import enforce_paired_disclosure, review_claims

        review_call = _text_generation_channel(model)
        if review_call is not None:
            try:
                drafts = await review_claims(
                    drafts, gathered, facet_stances(gathered), review_call
                )
            except Exception:  # noqa: BLE001 — 審查是修飾，失敗不該中斷分析
                pass

        # 規則式配對揭露（確定性，不依賴 LLM）——
        # 即使審查層未生效或漏掉配對規則，這一步也會補上。
        drafts = enforce_paired_disclosure(drafts, gathered)

    # 確定性指標引用檢核 — 掃描 claim 中的技術指標數值，
    # 標記無 evidence 對應的幻覺數字。在 paired disclosure 之後執行，
    # 因為 paired disclosure 可能新增合法的指標引用。
    if drafts:
        from hoyabit_agent.indicator_guard import enforce_indicator_citations
        drafts = enforce_indicator_citations(drafts, gathered)

    report = _assemble(
        asset,
        gathered,
        drafts,
        recorder,
        request.question,
        assessment,
        as_of=request.as_of_date,
        unavailable_facets=requirement.unavailable_facets,
        boundary_notes=requirement.boundary_notes,
        core_data_demands=requirement.core_data_demands,
    )
    return AnalysisOutcome(
        run_id=identifier,
        report=report,
        trace=recorder.build(identifier),
        rejection=None,
    )


def _text_generation_channel(
    model: ModelProvider,
) -> Callable[[str, str], Awaitable[str | None]] | None:
    """取出一個「給系統指令與使用者訊息、拿回純文字」的通道，供審查層使用。

    `ModelProvider` 介面刻意不含這個能力：三個接縫方法（plan / synthesise /
    label）都是結構化輸出，而審查需要的是自由文字。因此這裡以能力偵測取得，
    偵測不到就回 None，審查層跳過 —— 審查是修飾，不是正確性的必要條件。
    """
    provider = getattr(model, "_primary", model)
    post = getattr(provider, "_post", None)
    model_name = getattr(provider, "_model", None)
    if post is None or model_name is None:
        return None

    async def call(system: str, user: str) -> str | None:
        body = await post(
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
            },
            model=model_name,
        )
        if body is None:
            return None
        candidates = body.get("candidates") or [{}]
        parts = candidates[0].get("content", {}).get("parts") or []
        text = parts[0].get("text") if parts else None
        return text if isinstance(text, str) else None

    return call


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
    core_data_demands: tuple[object, ...] = (),
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
        f"結論層引用有效率 {coverage:.0%}"
        f"（注意：此為引用是否存在的二元檢查，非判斷品質指標）",
    )

    # 限制分兩層，因為它們有不同的讀者與不同的安全性：
    #
    # report_limitations —— **資料/方法論**層級，進報告本體給評審看。
    # 回測取不到的面明列為「資料不可得」，未關閉的必補缺口如實揭露。
    # 這些**不含任何被拒判斷的原文**，因此放進報告本體是安全的
    # （見 ADR 0005 / Sol A；命題要求明確指出限制，不把不確定性藏起來）。
    from hoyabit_agent.limitations import build_report_limitations
    from hoyabit_agent.question import CoreDataDemand

    _typed_demands: tuple[CoreDataDemand, ...] = tuple(
        d for d in core_data_demands if isinstance(d, CoreDataDemand)
    )

    report_limitations = build_report_limitations(
        core_data_demands=_typed_demands,
        unavailable_facets=unavailable_facets,
        boundary_notes=boundary_notes,
        blocking_gap_details=tuple(
            gap.detail for gap in assessment.blocking_gaps
        ) if assessment is not None else (),
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

    # ─── 結論降級標記強制檢查 ───
    # 當核心資料缺失時，結論層不得寫成像有完整答案的主結論。
    # 如果結論文字沒有包含降級標記（替代/補充/假說/無法回答等），
    # 強制把 contested count +1，確保 contested penalty 生效。
    # 這是程式碼層防護，不依賴模型記得寫。
    from hoyabit_agent.domain import ClaimRole, Confidence as _Confidence
    from hoyabit_agent.question import DataAvailability, DemandWeight as _DW

    _DOWNGRADE_MARKERS = (
        "替代", "補充", "背景", "假說", "條件式",
        "無法回答", "無法直接", "無法確認", "無法判定",
        "僅供參考", "僅為", "不可視為",
    )
    _has_core_gaps = any(
        isinstance(d, CoreDataDemand)
        and d.availability is DataAvailability.UNAVAILABLE
        and d.weight is _DW.CORE
        for d in _typed_demands
    )
    _conclusion_missing_downgrade = 0
    if _has_core_gaps:
        for claim in ledger.admissible:
            if claim.role is ClaimRole.CONCLUSION:
                if not any(marker in claim.text for marker in _DOWNGRADE_MARKERS):
                    _conclusion_missing_downgrade += 1
        if _conclusion_missing_downgrade > 0:
            recorder.record(
                TraceNodeKind.REPORT,
                f"結論降級檢查：核心資料缺失但 {_conclusion_missing_downgrade} 則結論"
                f"缺少降級標記（替代/假說/無法回答），強制計入 contested 比例",
            )

    confidence = assess_confidence(
        gathered, as_of=as_of, core_data_demands=_typed_demands,
    )

    # ─── 爭議比例修正 ───
    # contested 判斷代表「引用有效但支撐薄弱或內部矛盾」。
    # 如果多數判斷都是 contested，信心度應該反映這個現實。
    # 邏輯集中在 tools.apply_contested_penalty，此處僅呼叫並記錄。
    contested_count = len(ledger.contested) + _conclusion_missing_downgrade
    confidence, penalty_info = apply_contested_penalty(
        confidence,
        supported_count=len(ledger.supported),
        contested_count=contested_count,
        total_claims=len(ledger.claims),
    )
    if penalty_info:
        recorder.record(
            TraceNodeKind.REPORT,
            f"爭議比例修正：{penalty_info['contested']}/{penalty_info['total_claims']} 則判斷為 contested"
            f"（比例 {penalty_info['contested_ratio']:.0%}），懲罰 -{penalty_info['penalty']:.4f}，"
            f"信心度 {penalty_info['original_confidence']:.4f} → {penalty_info['adjusted_confidence']:.4f}",
        )
    elif isinstance(confidence, _Confidence) and len(ledger.claims) >= 3:
        contested_ratio = contested_count / len(ledger.claims)
        recorder.record(
            TraceNodeKind.REPORT,
            f"爭議比例檢查：{contested_count}/{len(ledger.claims)} 則 contested"
            f"（比例 {contested_ratio:.0%}），未超過 50% 門檻，不扣分",
        )

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
