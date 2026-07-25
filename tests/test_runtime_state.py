from datetime import UTC, datetime, timedelta

from hoyabit_agent.domain import (
    AnalysisRequest,
    DraftClaim,
    Facet,
    ToolExecutionStatus,
    TraceNode,
    TraceNodeKind,
)
from hoyabit_agent.run import analyse
from hoyabit_agent.runtime_events import RuntimeEventBroker
from hoyabit_agent.testing import ScriptedModel, StaticSource, evidence
from hoyabit_agent.tools import evidence_gap


async def test_repeated_tool_calls_are_lossless_records() -> None:
    source = StaticSource([evidence("E1", Facet.TECHNICAL, 0.4)], name="market")
    model = ScriptedModel(
        plans=[("market,market", "比較兩個資產")],
        claims=[DraftClaim("存在差異", ("E1",), Facet.TECHNICAL)],
        arguments={"market": {"interval": "4h"}},
    )
    outcome = await analyse(AnalysisRequest("BTC", "比較 BTC 與 ETH"), [source], model)
    plan = next(node for node in outcome.trace.nodes if node.kind is TraceNodeKind.PLAN)
    gather = next(node for node in outcome.trace.nodes if node.kind is TraceNodeKind.GATHER)
    assert [record.tool for record in plan.executions] == ["market", "market"]
    assert len(gather.executions) == 2
    assert all(record.status is ToolExecutionStatus.SUCCEEDED for record in gather.executions)


def test_evidence_gap_exposes_quality_and_contradictions() -> None:
    positive = evidence("SOURCE-A", Facet.TECHNICAL, 0.8)
    negative = evidence("SOURCE-B", Facet.TECHNICAL, -0.8)
    gap = evidence_gap(
        [positive, negative],
        now=datetime(2026, 7, 24, tzinfo=UTC),
        max_age=timedelta(days=7),
    )
    assert gap.direction_balance is True
    assert gap.independent_sources == 2
    assert gap.fresh is True
    assert gap.contradiction_facets == frozenset({Facet.TECHNICAL})
    assert "unresolved_contradiction" in gap.reasons


async def test_event_broker_replays_then_streams_terminal_event() -> None:
    broker = RuntimeEventBroker()
    broker.begin("run-1")
    broker.publish_trace("run-1", TraceNode(0, TraceNodeKind.PLAN, "規劃"))
    broker.complete("run-1", {"run_id": "run-1", "stance": "neutral"})
    messages = [message async for message in broker.stream("run-1")]
    assert messages[0].startswith("event: trace\n")
    assert messages[1].startswith("event: complete\n")


async def test_analysis_streams_each_trace_node_through_the_public_callback() -> None:
    streamed: list[TraceNode] = []
    source = StaticSource([evidence("E1", Facet.TECHNICAL, 0.4)], name="market")
    model = ScriptedModel(
        plans=[("market", "gather technical evidence")],
        claims=[DraftClaim("supported claim", ("E1",), Facet.TECHNICAL)],
    )

    outcome = await analyse(
        AnalysisRequest("BTC", "analyse BTC"),
        [source],
        model,
        on_trace=streamed.append,
    )

    assert streamed == list(outcome.trace.nodes)
    assert streamed[0].kind is TraceNodeKind.ASSET_GATE
    assert streamed[-1].kind is TraceNodeKind.REPORT