"""軌跡渲染器測試 —— 純函數，無 I/O。

驗證的是 ticket 09 的驗收條件：節點理由與參數、證據→判斷連線、
點擊展開來源片段、被丟棄的判斷、預算消耗，都出現在 HTML 裡。
"""

from __future__ import annotations

import json

from hoyabit_agent.domain import (
    AnalysisOutcome,
    Asset,
    Claim,
    Confidence,
    DraftClaim,
    Facet,
    Rejection,
    Report,
    Stance,
    Trace,
    TraceNode,
    TraceNodeKind,
)
from hoyabit_agent.testing import evidence
from hoyabit_agent.viz.trace_html import render_outcome, trace_json


def an_outcome() -> AnalysisOutcome:
    report = Report(
        asset=Asset.BTC,
        stance=Stance.BULLISH,
        confidence=Confidence(value=0.75, facet_stances={f: Stance.NEUTRAL for f in Facet}),
        claims=(Claim("站上季線", ("E1",), Facet.TECHNICAL),),
        dropped_claims=(DraftClaim("因此必漲", (), Facet.TECHNICAL),),
        evidence=(evidence("E1", Facet.TECHNICAL, 0.8, text="收盤站上季線"),),
    )
    trace = Trace(
        run_id="run-1",
        nodes=(
            TraceNode(seq=0, kind=TraceNodeKind.ASSET_GATE, reason="BTC 為受涵蓋幣種"),
            TraceNode(
                seq=1,
                kind=TraceNodeKind.PLAN,
                reason="技術面全缺",
                detail={"binance_spot": '{"interval": "4h"}'},
                gap_before=frozenset(Facet),
                gap_after=frozenset(Facet),
                elapsed_seconds=0.5,
            ),
            TraceNode(
                seq=2,
                kind=TraceNodeKind.GATHER,
                reason="抓到技術面",
                evidence_ids=("E1",),
                gap_before=frozenset(Facet),
                gap_after=frozenset({Facet.SENTIMENT}),
                elapsed_seconds=1.0,
            ),
        ),
    )
    return AnalysisOutcome(run_id="run-1", report=report, trace=trace, rejection=None)


def test_it_renders_a_self_contained_html_document() -> None:
    html = render_outcome(an_outcome())
    assert html.startswith("<!doctype html>")
    assert "</html>" in html
    assert "http://" not in html and "https://cdn" not in html  # 無外部資源


def test_the_report_direction_and_confidence_are_shown() -> None:
    html = render_outcome(an_outcome())
    assert "BTC 分析報告" in html
    assert "bullish" in html
    assert "75%" in html


def test_every_node_shows_its_reason() -> None:
    html = render_outcome(an_outcome())
    assert "技術面全缺" in html
    assert "抓到技術面" in html


def test_the_tool_arguments_the_model_chose_are_shown() -> None:
    """模型自己選的參數是「這是真推理」最直接的證據。"""
    html = render_outcome(an_outcome())
    assert "binance_spot" in html
    assert "4h" in html


def test_the_gap_change_is_shown() -> None:
    html = render_outcome(an_outcome())
    assert "缺口：" in html
    assert "→" in html


def test_a_claim_links_to_the_evidence_it_cites() -> None:
    """證據 → 判斷的連線可視化 —— 這是「點對點之間為什麼」的核心。"""
    html = render_outcome(an_outcome())
    assert 'href="#evi-E1"' in html
    assert 'id="evi-E1"' in html


def test_clicking_evidence_reveals_the_source_excerpt() -> None:
    html = render_outcome(an_outcome())
    assert "收盤站上季線" in html
    assert "擷取於" in html


def test_dropped_claims_are_shown_with_a_reason() -> None:
    html = render_outcome(an_outcome())
    assert "因此必漲" in html
    assert "已被引用檢核丟棄" in html


def test_the_timeline_shows_budget_consumption() -> None:
    """每個節點的 elapsed 用進度條呈現在時間軸上。"""
    html = render_outcome(an_outcome())
    assert "track" in html
    assert "width:" in html


def test_a_rejected_run_renders_the_rejection_and_its_trace() -> None:
    rejected = AnalysisOutcome(
        run_id="run-doge",
        report=None,
        trace=Trace(
            run_id="run-doge",
            nodes=(
                TraceNode(seq=0, kind=TraceNodeKind.ASSET_GATE, reason="DOGE 不在受涵蓋幣種內"),
            ),
        ),
        rejection=Rejection(reason="DOGE 不在受涵蓋幣種內，不予分析"),
    )
    html = render_outcome(rejected)
    assert "已拒絕" in html
    assert "DOGE" in html


def test_html_escapes_untrusted_text() -> None:
    """證據原文可能含 HTML —— 不能讓它注入頁面。"""
    report = Report(
        asset=Asset.BTC,
        stance=Stance.NEUTRAL,
        confidence=Confidence(value=0.5, facet_stances={f: Stance.NEUTRAL for f in Facet}),
        claims=(),
        dropped_claims=(),
        evidence=(evidence("E1", Facet.SENTIMENT, 0.0, text="<script>alert(1)</script>"),),
    )
    outcome = AnalysisOutcome(
        run_id="r", report=report, trace=Trace(run_id="r", nodes=()), rejection=None
    )
    html = render_outcome(outcome)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_trace_json_is_valid_and_ordered() -> None:
    """軌跡檔本身是交付物，可供其他工具消費。"""
    payload = json.loads(trace_json(an_outcome()))
    assert payload["run_id"] == "run-1"
    assert [node["seq"] for node in payload["nodes"]] == [0, 1, 2]
    assert payload["nodes"][1]["detail"] == {"binance_spot": '{"interval": "4h"}'}
