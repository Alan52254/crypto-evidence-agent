from hoyabit_agent.domain import (
    AnalysisOutcome,
    Asset,
    Confidence,
    Facet,
    Report,
    Stance,
    Trace,
    TraceNode,
    TraceNodeKind,
)
from hoyabit_agent.viz.trace_html import render_outcome


def an_outcome() -> AnalysisOutcome:
    report = Report(
        asset=Asset.BTC,
        stance=Stance.NEUTRAL,
        confidence=Confidence(0.5, {facet: Stance.NEUTRAL for facet in Facet}),
        claims=(),
        dropped_claims=(),
        evidence=(),
    )
    trace = Trace(
        "run-ui",
        (
            TraceNode(0, TraceNodeKind.PLAN, "選擇市場工具", elapsed_seconds=1.0),
            TraceNode(1, TraceNodeKind.GATHER, "取得市場證據", elapsed_seconds=2.0),
        ),
    )
    return AnalysisOutcome("run-ui", report, trace, None)


def test_financial_terminal_exposes_agent_behaviour() -> None:
    page = render_outcome(an_outcome())
    assert 'class="terminal-shell"' in page
    assert 'class="sources-pane"' in page
    assert 'class="report-pane"' in page
    assert 'class="trace-pane"' in page
    assert "市場因子覆蓋" in page
    assert "THOUGHT" in page
    assert "OBSERVATION" in page
    assert "900s" in page


def test_financial_terminal_remains_responsive_and_accessible() -> None:
    page = render_outcome(an_outcome())
    assert "@media(max-width:720px)" in page
    assert "prefers-reduced-motion" in page
    assert "focus-visible" in page
    assert "aria-label" in page
