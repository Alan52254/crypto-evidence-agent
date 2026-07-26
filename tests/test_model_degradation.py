"""推理層不可用時的降級路徑。

命題要求十五分鐘內交付報告，而免費層模型有速率限制 —— 「模型剛好在
這一分鐘不回應」是預期情況，不是意外。這些測試釘住的是：
證據源健康時，模型不可用**不得**導致空報告。
"""

from __future__ import annotations

from hoyabit_agent.domain import (
    AnalysisRequest,
    ClaimRole,
    Facet,
    TraceNodeKind,
)
from hoyabit_agent.run import analyse
from hoyabit_agent.testing import ScriptedModel, StaticSource, evidence


def silent_model() -> ScriptedModel:
    """完全不回應的模型：不給計畫，也不給判斷。

    對應真實世界的 429 —— `GeminiProvider` 與 `GroqProvider` 在額度用罄時
    都是回傳空結果而非拋例外，這個假模型精確模擬那個行為。
    """
    return ScriptedModel(plans=[], claims=[])


def healthy_sources() -> list[StaticSource]:
    return [
        StaticSource(
            [evidence("E-TECH", Facet.TECHNICAL, +0.6)], name="market"
        ),
        StaticSource(
            [evidence("E-NEWS", Facet.FUNDAMENTAL, -0.5)], name="news"
        ),
    ]


async def test_a_silent_planner_still_gathers_from_healthy_sources() -> None:
    """規劃層不回應時走保底計畫 —— 證據源健康就不該一無所獲。"""
    outcome = await analyse(
        AnalysisRequest("BTC", "BTC 現在的走勢如何"),
        sources=healthy_sources(),
        model=silent_model(),
    )
    assert outcome.report is not None
    assert len(outcome.report.evidence) == 2


async def test_the_fallback_plan_calls_every_available_source() -> None:
    """保底計畫的內容是「每個來源各取一次」，因此每個來源都該被呼叫到。"""
    sources = healthy_sources()
    await analyse(
        AnalysisRequest("BTC", "BTC 現在的走勢如何"),
        sources=sources,
        model=silent_model(),
    )
    assert all(source.received for source in sources)


async def test_the_fallback_plan_is_visible_in_the_trace() -> None:
    """降級必須可稽核 —— 讀者要看得出這一輪不是模型規劃的。"""
    outcome = await analyse(
        AnalysisRequest("BTC", "BTC 現在的走勢如何"),
        sources=healthy_sources(),
        model=silent_model(),
    )
    plans = [n for n in outcome.trace.nodes if n.kind is TraceNodeKind.PLAN]
    assert any("保底檢索計畫" in node.reason for node in plans)


async def test_a_silent_reasoner_still_produces_a_readable_report() -> None:
    """有證據卻零則判斷的報告對讀者毫無用處 —— 至少要有事實層。"""
    outcome = await analyse(
        AnalysisRequest("BTC", "BTC 現在的走勢如何"),
        sources=healthy_sources(),
        model=silent_model(),
    )
    assert outcome.report is not None
    assert len(outcome.report.claims) > 0


async def test_the_degraded_report_contains_no_inference_or_conclusion() -> None:
    """推論與結論刻意不補 —— 憑空生成方向性判斷比沒有判斷更危險。"""
    outcome = await analyse(
        AnalysisRequest("BTC", "BTC 現在的走勢如何"),
        sources=healthy_sources(),
        model=silent_model(),
    )
    assert outcome.report is not None
    roles = {claim.role for claim in outcome.report.claims}
    assert ClaimRole.INFERENCE not in roles
    assert ClaimRole.CONCLUSION not in roles
    assert ClaimRole.FACT in roles


async def test_the_degraded_report_states_why_it_has_no_conclusion() -> None:
    """「沒有結論」不可被誤讀成「市場沒有方向」，所以降級本身要寫進報告。"""
    outcome = await analyse(
        AnalysisRequest("BTC", "BTC 現在的走勢如何"),
        sources=healthy_sources(),
        model=silent_model(),
    )
    assert outcome.report is not None
    watches = [c for c in outcome.report.claims if c.role is ClaimRole.WATCH]
    assert watches
    assert any("推理層" in claim.text for claim in watches)


async def test_every_degraded_fact_still_cites_real_evidence() -> None:
    """降級不放寬引用規則 —— 事實層判斷仍必須掛載真實存在的證據。"""
    outcome = await analyse(
        AnalysisRequest("BTC", "BTC 現在的走勢如何"),
        sources=healthy_sources(),
        model=silent_model(),
    )
    assert outcome.report is not None
    known = {item.id for item in outcome.report.evidence}
    for claim in outcome.report.claims:
        assert claim.evidence_ids
        assert set(claim.evidence_ids) <= known


async def test_no_evidence_and_no_model_yields_a_report_without_claims() -> None:
    """證據源也失效時不硬造判斷 —— 沒有證據就沒有可斷言的事實。"""
    outcome = await analyse(
        AnalysisRequest("BTC", "BTC 現在的走勢如何"),
        sources=[StaticSource([], name="empty")],
        model=silent_model(),
    )
    assert outcome.report is not None
    assert outcome.report.claims == ()


async def test_the_fallback_plan_is_used_at_most_once() -> None:
    """保底是保底，不是常態路徑 —— 不該每一輪都重跑一次。"""
    outcome = await analyse(
        AnalysisRequest("BTC", "BTC 現在的走勢如何"),
        sources=healthy_sources(),
        model=silent_model(),
        max_iterations=4,
    )
    fallback_plans = [
        n
        for n in outcome.trace.nodes
        if n.kind is TraceNodeKind.PLAN and "保底檢索計畫" in n.reason
    ]
    assert len(fallback_plans) == 1
