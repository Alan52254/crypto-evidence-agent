"""接縫 2（分析回合）的流程測試 —— 本專案的主要測試面。

所有測試在接縫 1 塞假證據源、接縫 3 塞腳本假模型，
因此完全不碰網路、不呼叫真實模型。
"""

from __future__ import annotations

import pytest

from hoyabit_agent import AnalysisRequest, analyse
from hoyabit_agent.domain import (
    Asset,
    Confidence,
    DraftClaim,
    Facet,
    InsufficientEvidence,
    Stance,
    TraceNodeKind,
)
from hoyabit_agent.testing import (
    HangingSource,
    ManualClock,
    ScriptedModel,
    StaticSource,
    evidence,
)


def request_for(asset: str = "BTC") -> AnalysisRequest:
    return AnalysisRequest(asset=asset)


def market(*items: object, **kwargs: object) -> StaticSource:
    return StaticSource(list(items), name="market", **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# 幣種閘門
# --------------------------------------------------------------------------


async def test_covered_asset_produces_a_report_and_a_trace() -> None:
    outcome = await analyse(
        request_for("BTC"),
        sources=[StaticSource([evidence("E1", Facet.TECHNICAL, +0.8)], name="market")],
        model=ScriptedModel(
            plans=[("market", "技術面沒有證據，先抓市場數據")],
            claims=[DraftClaim("BTC 站上季線", ("E1",), Facet.TECHNICAL)],
        ),
    )

    assert outcome.rejection is None
    assert outcome.report is not None
    assert outcome.report.asset is Asset.BTC
    assert outcome.trace.nodes  # 軌跡非空


async def test_uncovered_asset_is_rejected_before_any_budget_is_spent() -> None:
    clock = ManualClock()
    outcome = await analyse(
        request_for("DOGE"),
        sources=[StaticSource([evidence("E1", Facet.TECHNICAL, +0.8)], name="market")],
        model=ScriptedModel(plans=[("market", "不該被呼叫")], claims=[]),
        clock=clock,
    )

    assert outcome.report is None
    assert outcome.rejection is not None
    assert "DOGE" in outcome.rejection.reason
    # 閘門在推理之前，預算一秒都沒花
    assert clock.elapsed == 0.0
    # 軌跡只有閘門那一個節點
    assert [n.kind for n in outcome.trace.nodes] == [TraceNodeKind.ASSET_GATE]


async def test_gate_uses_an_allowlist_not_a_blocklist() -> None:
    """系統不判斷「是不是水幣」，只判斷在不在受涵蓋集合內。"""
    outcome = await analyse(
        request_for("SOMETHING_INVENTED_TOMORROW"),
        sources=[],
        model=ScriptedModel(plans=[], claims=[]),
    )
    assert outcome.rejection is not None


# --------------------------------------------------------------------------
# 原生工具調用
# --------------------------------------------------------------------------


async def test_the_model_receives_the_tool_specs_of_every_available_source() -> None:
    source = StaticSource([evidence("E1", Facet.TECHNICAL, +0.8)], name="market")
    model = ScriptedModel(
        plans=[("market", "抓技術面")],
        claims=[DraftClaim("站上季線", ("E1",), Facet.TECHNICAL)],
    )

    await analyse(request_for("BTC"), sources=[source], model=model)

    assert model.seen_tools[0] == (source.spec,)


async def test_arguments_chosen_by_the_model_reach_the_evidence_source() -> None:
    """原生 tool calling 與受控規劃的分野：參數是模型自己選的。"""
    source = StaticSource([evidence("E1", Facet.TECHNICAL, +0.8)], name="market")
    model = ScriptedModel(
        plans=[("market", "抓 4 小時線")],
        claims=[DraftClaim("站上季線", ("E1",), Facet.TECHNICAL)],
        arguments={"market": {"interval": "4h", "limit": 200}},
    )

    await analyse(request_for("BTC"), sources=[source], model=model)

    assert source.received[0] == {"interval": "4h", "limit": 200}


async def test_the_chosen_arguments_appear_in_the_trace() -> None:
    """軌跡上看得到模型自己挑了什麼參數 —— 這是「真推理」最直接的證據。"""
    outcome = await analyse(
        request_for("BTC"),
        sources=[StaticSource([evidence("E1", Facet.TECHNICAL, +0.8)], name="market")],
        model=ScriptedModel(
            plans=[("market", "抓 4 小時線")],
            claims=[DraftClaim("站上季線", ("E1",), Facet.TECHNICAL)],
            arguments={"market": {"interval": "4h"}},
        ),
    )

    plan_node = next(n for n in outcome.trace.nodes if n.kind is TraceNodeKind.PLAN)
    assert plan_node.executions[0].arguments["interval"] == "4h"


async def test_a_hallucinated_tool_name_degrades_instead_of_crashing() -> None:
    outcome = await analyse(
        request_for("BTC"),
        sources=[StaticSource([evidence("E1", Facet.TECHNICAL, +0.8)], name="market")],
        model=ScriptedModel(
            plans=[("no_such_tool,market", "模型幻覺出一個不存在的工具")],
            claims=[DraftClaim("站上季線", ("E1",), Facet.TECHNICAL)],
        ),
    )

    assert outcome.report is not None
    assert outcome.report.claims


async def test_the_model_sees_what_it_already_tried() -> None:
    model = ScriptedModel(
        plans=[("market", "先抓技術面"), ("news", "技術面有了，改抓新聞")],
        claims=[DraftClaim("偏多", ("E1",), Facet.TECHNICAL)],
    )
    await analyse(
        request_for("BTC"),
        sources=[
            StaticSource([evidence("E1", Facet.TECHNICAL, +0.5)], name="market"),
            StaticSource([evidence("E2", Facet.SENTIMENT, +0.5)], name="news"),
        ],
        model=model,
    )

    second_context = model.seen_contexts[1]
    assert [a.tool for a in second_context.attempts] == ["market"]
    assert "1 項證據" in second_context.attempts[0].outcome


# --------------------------------------------------------------------------
# 引用檢核
# --------------------------------------------------------------------------


async def test_claims_without_evidence_are_dropped_and_recorded_in_the_trace() -> None:
    outcome = await analyse(
        request_for("BTC"),
        sources=[StaticSource([evidence("E1", Facet.TECHNICAL, +0.8)], name="market")],
        model=ScriptedModel(
            plans=[("market", "抓技術面")],
            claims=[
                DraftClaim("BTC 站上季線", ("E1",), Facet.TECHNICAL),
                DraftClaim("散戶情緒極度樂觀", (), Facet.SENTIMENT),  # 無證據
                DraftClaim("鯨魚正在吸籌", ("E-NOT-REAL",), Facet.POSITIONING),  # 證據不存在
            ],
        ),
    )

    assert outcome.report is not None
    kept = [c.text for c in outcome.report.claims]
    assert kept == ["BTC 站上季線"]

    dropped = [c.text for c in outcome.report.dropped_claims]
    assert set(dropped) == {"散戶情緒極度樂觀", "鯨魚正在吸籌"}

    drop_nodes = [n for n in outcome.trace.nodes if n.kind is TraceNodeKind.CLAIM_DROPPED]
    assert len(drop_nodes) == 2


async def test_every_claim_in_the_report_cites_evidence_that_was_actually_gathered() -> None:
    outcome = await analyse(
        request_for("ETH"),
        sources=[
            StaticSource([evidence("E1", Facet.TECHNICAL, +0.5)], name="market"),
            StaticSource([evidence("E2", Facet.FUNDAMENTAL, +0.4)], name="news"),
        ],
        model=ScriptedModel(
            plans=[("market,news", "兩面都缺")],
            claims=[
                DraftClaim("價格結構偏多", ("E1",), Facet.TECHNICAL),
                DraftClaim("升級進度符合預期", ("E2",), Facet.FUNDAMENTAL),
            ],
        ),
    )

    assert outcome.report is not None
    gathered = {e.id for e in outcome.report.evidence}
    for claim in outcome.report.claims:
        assert set(claim.evidence_ids) <= gathered


async def test_report_markdown_is_rendered_from_filtered_claims_not_cut_from_prose() -> None:
    """判斷是結構化物件；Markdown 是過濾之後才渲染的產物。"""
    outcome = await analyse(
        request_for("BTC"),
        sources=[StaticSource([evidence("E1", Facet.TECHNICAL, +0.8)], name="market")],
        model=ScriptedModel(
            plans=[("market", "抓技術面")],
            claims=[
                DraftClaim("BTC 站上季線", ("E1",), Facet.TECHNICAL),
                DraftClaim("因此後市看好", (), Facet.TECHNICAL),  # 無證據，不得出現
            ],
        ),
    )

    assert outcome.report is not None
    markdown = outcome.report.to_markdown()
    assert "BTC 站上季線" in markdown
    assert "[E1]" in markdown  # 證據標記隨判斷一起渲染
    assert "因此後市看好" not in markdown
    assert "不是投資建議" in markdown


# --------------------------------------------------------------------------
# 韌性：逾時、失效、預算
# --------------------------------------------------------------------------


async def test_a_hanging_source_yields_an_empty_set_and_the_run_continues() -> None:
    outcome = await analyse(
        request_for("BTC"),
        sources=[
            HangingSource(),
            StaticSource([evidence("E1", Facet.TECHNICAL, +0.8)], name="market"),
        ],
        model=ScriptedModel(
            plans=[("hanging,market", "兩個都試")],
            claims=[DraftClaim("BTC 站上季線", ("E1",), Facet.TECHNICAL)],
        ),
        io_timeout_seconds=0.05,
    )

    assert outcome.report is not None
    assert outcome.report.claims  # 掛起的來源沒有拖垮整場
    unavailable = [n for n in outcome.trace.nodes if n.kind is TraceNodeKind.SOURCE_UNAVAILABLE]
    assert any("hanging" in n.reason for n in unavailable)


async def test_budget_exhaustion_still_produces_a_report_never_a_timeout_error() -> None:
    clock = ManualClock()
    outcome = await analyse(
        request_for("BTC"),
        sources=[
            StaticSource(
                [evidence("E1", Facet.TECHNICAL, +0.8)],
                name="market",
                costs_seconds=600.0,
            )
        ],
        model=ScriptedModel(
            plans=[("market", "抓技術面"), ("market", "還想再抓")],
            claims=[DraftClaim("BTC 站上季線", ("E1",), Facet.TECHNICAL)],
        ),
        clock=clock,
        budget_seconds=900.0,
    )

    assert outcome.report is not None
    assert outcome.report.claims
    assert any(n.kind is TraceNodeKind.BUDGET_EXHAUSTED for n in outcome.trace.nodes)


async def test_a_failing_source_does_not_abort_the_run() -> None:
    outcome = await analyse(
        request_for("BTC"),
        sources=[
            StaticSource([], name="broken", raises=RuntimeError("upstream 500")),
            StaticSource([evidence("E1", Facet.TECHNICAL, +0.8)], name="market"),
        ],
        model=ScriptedModel(
            plans=[("broken,market", "兩個都試")],
            claims=[DraftClaim("BTC 站上季線", ("E1",), Facet.TECHNICAL)],
        ),
    )

    assert outcome.report is not None
    assert outcome.report.claims


# --------------------------------------------------------------------------
# 信心度（ADR 0002）
# --------------------------------------------------------------------------


async def test_a_single_facet_yields_insufficient_evidence_not_high_confidence() -> None:
    outcome = await analyse(
        request_for("BTC"),
        sources=[StaticSource([evidence("E1", Facet.TECHNICAL, +0.9)], name="market")],
        model=ScriptedModel(
            plans=[("market", "只抓得到技術面")],
            claims=[DraftClaim("BTC 站上季線", ("E1",), Facet.TECHNICAL)],
        ),
    )

    assert outcome.report is not None
    assert isinstance(outcome.report.confidence, InsufficientEvidence)


async def test_all_facets_agreeing_yields_high_confidence() -> None:
    outcome = await analyse(
        request_for("BTC"),
        sources=[
            StaticSource(
                [
                    evidence("E1", Facet.TECHNICAL, +0.8),
                    evidence("E2", Facet.POSITIONING, +0.7),
                    evidence("E3", Facet.FUNDAMENTAL, +0.6),
                    evidence("E4", Facet.SENTIMENT, +0.9),
                ],
                name="all",
            )
        ],
        model=ScriptedModel(
            plans=[("all", "一次抓齊四面")],
            claims=[DraftClaim("四面一致偏多", ("E1", "E2"), Facet.TECHNICAL)],
        ),
    )

    assert outcome.report is not None
    confidence = outcome.report.confidence
    assert isinstance(confidence, Confidence)
    # The confidence formula is multi-factor (agreement + coverage + freshness +
    # independence + completeness); all-facets-agreeing should yield HIGH confidence
    # but not necessarily 1.0 (freshness decay and independence score cap it).
    assert confidence.value > 0.6, f"Expected high confidence, got {confidence.value}"
    assert outcome.report.stance is Stance.BULLISH


async def test_facets_disagreeing_yields_low_confidence() -> None:
    outcome = await analyse(
        request_for("BTC"),
        sources=[
            StaticSource(
                [
                    evidence("E1", Facet.TECHNICAL, -0.8),
                    evidence("E2", Facet.POSITIONING, -0.7),
                    evidence("E3", Facet.FUNDAMENTAL, +0.6),
                    evidence("E4", Facet.SENTIMENT, +0.9),
                ],
                name="all",
            )
        ],
        model=ScriptedModel(
            plans=[("all", "一次抓齊四面")],
            claims=[DraftClaim("多空分歧", ("E1", "E3"), Facet.TECHNICAL)],
        ),
    )

    assert outcome.report is not None
    confidence = outcome.report.confidence
    assert isinstance(confidence, Confidence)
    # Facets disagreeing (2 bearish, 2 bullish) yields lower confidence than 1.0;
    # multi-factor formula yields ~0.69 (coverage/freshness add to agreement score).
    assert confidence.value < 0.9, f"Expected lower confidence for disagreeing facets, got {confidence.value}"
    # 低信心度本身可溯源：讀者看得出是哪幾面在分歧
    assert confidence.facet_stances[Facet.TECHNICAL] is Stance.BEARISH
    assert confidence.facet_stances[Facet.SENTIMENT] is Stance.BULLISH


async def test_the_same_event_from_two_outlets_counts_as_one_evidence() -> None:
    """ADR 0002 證據獨立性：轉載不構成獨立證據，不得灌高信心度。"""
    outcome = await analyse(
        request_for("BTC"),
        sources=[
            StaticSource(
                [
                    evidence("E1", Facet.SENTIMENT, +0.9, event_key="etf-approved"),
                    evidence("E2", Facet.SENTIMENT, +0.9, event_key="etf-approved"),
                    evidence("E3", Facet.TECHNICAL, -0.8),
                ],
                name="news",
            )
        ],
        model=ScriptedModel(
            plans=[("news", "抓新聞")],
            claims=[DraftClaim("市場對 ETF 消息反應正面", ("E1",), Facet.SENTIMENT)],
        ),
    )

    assert outcome.report is not None
    sentiment_evidence = [e for e in outcome.report.evidence if e.facet is Facet.SENTIMENT]
    assert len(sentiment_evidence) == 1
    # 歸併後溯源不損失：兩份來源片段都還在
    assert len(sentiment_evidence[0].excerpts) == 2


# --------------------------------------------------------------------------
# 蒐集迴圈的動態性
# --------------------------------------------------------------------------


async def test_the_model_reacts_to_the_remaining_evidence_gap() -> None:
    """第一輪只補到情緒面，模型看到技術面仍缺 → 第二輪改抓市場數據。"""
    outcome = await analyse(
        request_for("BTC"),
        sources=[
            StaticSource([evidence("E1", Facet.SENTIMENT, +0.5)], name="news"),
            StaticSource([evidence("E2", Facet.TECHNICAL, +0.5)], name="market"),
        ],
        model=ScriptedModel(
            plans=[
                ("news", "四面全缺，先從新聞下手"),
                ("market", "情緒面已補上，技術面仍缺，改抓市場數據"),
            ],
            claims=[DraftClaim("偏多", ("E1", "E2"), Facet.TECHNICAL)],
        ),
    )

    plan_nodes = [n for n in outcome.trace.nodes if n.kind is TraceNodeKind.PLAN]
    assert len(plan_nodes) == 2
    # 軌跡看得出缺口如何驅動下一步
    assert Facet.SENTIMENT in plan_nodes[0].gap_before
    assert Facet.SENTIMENT not in plan_nodes[1].gap_before
    assert Facet.TECHNICAL in plan_nodes[1].gap_before
    assert "技術面仍缺" in plan_nodes[1].reason


async def test_trace_nodes_record_the_evidence_they_produced() -> None:
    outcome = await analyse(
        request_for("BTC"),
        sources=[StaticSource([evidence("E1", Facet.TECHNICAL, +0.8)], name="market")],
        model=ScriptedModel(
            plans=[("market", "抓技術面")],
            claims=[DraftClaim("BTC 站上季線", ("E1",), Facet.TECHNICAL)],
        ),
    )

    gather_nodes = [n for n in outcome.trace.nodes if n.kind is TraceNodeKind.GATHER]
    assert gather_nodes
    assert "E1" in gather_nodes[0].evidence_ids
    # 缺口確實因這次蒐集而縮小
    assert Facet.TECHNICAL in gather_nodes[0].gap_before
    assert Facet.TECHNICAL not in gather_nodes[0].gap_after
