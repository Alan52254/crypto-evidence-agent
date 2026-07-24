"""軌跡前端 server 測試 —— 以記憶體 store + ASGITransport，不起 Postgres、不開 port。"""

from __future__ import annotations

import json

import httpx

from hoyabit_agent.domain import (
    AnalysisOutcome,
    Asset,
    Claim,
    Confidence,
    Facet,
    Rejection,
    Report,
    Stance,
    Trace,
    TraceNode,
    TraceNodeKind,
)
from hoyabit_agent.testing import InMemoryAnalysisStore, evidence
from hoyabit_agent.viz.server import create_app


def an_outcome(run_id: str = "run-1") -> AnalysisOutcome:
    report = Report(
        asset=Asset.BTC,
        stance=Stance.BULLISH,
        confidence=Confidence(value=0.75, facet_stances={f: Stance.NEUTRAL for f in Facet}),
        claims=(Claim("站上季線", ("E1",), Facet.TECHNICAL),),
        dropped_claims=(),
        evidence=(evidence("E1", Facet.TECHNICAL, 0.8, text="收盤站上季線"),),
    )
    trace = Trace(
        run_id=run_id,
        nodes=(TraceNode(seq=0, kind=TraceNodeKind.REPORT, reason="組裝完成"),),
    )
    return AnalysisOutcome(run_id=run_id, report=report, trace=trace, rejection=None)


def client_for(store: InMemoryAnalysisStore) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app(store))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_the_index_lists_saved_runs_newest_first() -> None:
    store = InMemoryAnalysisStore()
    await store.save(an_outcome("run-1"))
    await store.save(an_outcome("run-2"))

    async with client_for(store) as client:
        response = await client.get("/")

    assert response.status_code == 200
    body = response.text
    assert body.index("run-2") < body.index("run-1")  # 新的在前
    assert '/run/run-1' in body


async def test_the_index_is_friendly_when_empty() -> None:
    async with client_for(InMemoryAnalysisStore()) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "還沒有任何分析回合" in response.text


async def test_a_run_page_renders_the_trace() -> None:
    store = InMemoryAnalysisStore()
    await store.save(an_outcome("run-1"))

    async with client_for(store) as client:
        response = await client.get("/run/run-1")

    assert response.status_code == 200
    assert "BTC 分析報告" in response.text
    assert 'href="#evi-E1"' in response.text


async def test_an_unknown_run_returns_404_not_a_crash() -> None:
    async with client_for(InMemoryAnalysisStore()) as client:
        response = await client.get("/run/nope")

    assert response.status_code == 404
    assert "找不到回合" in response.text


async def test_the_trace_json_endpoint_returns_the_ordered_trace() -> None:
    store = InMemoryAnalysisStore()
    await store.save(an_outcome("run-1"))

    async with client_for(store) as client:
        response = await client.get("/run/run-1/trace.json")

    assert response.status_code == 200
    payload = json.loads(response.text)
    assert payload["run_id"] == "run-1"
    assert payload["nodes"][0]["kind"] == "report"


async def test_the_json_endpoint_404s_for_unknown_runs() -> None:
    async with client_for(InMemoryAnalysisStore()) as client:
        response = await client.get("/run/nope/trace.json")

    assert response.status_code == 404


async def test_a_rejected_run_is_viewable_too() -> None:
    store = InMemoryAnalysisStore()
    await store.save(
        AnalysisOutcome(
            run_id="run-doge",
            report=None,
            trace=Trace(
                run_id="run-doge",
                nodes=(TraceNode(seq=0, kind=TraceNodeKind.ASSET_GATE, reason="DOGE 被拒"),),
            ),
            rejection=Rejection(reason="DOGE 不在受涵蓋幣種內"),
        )
    )

    async with client_for(store) as client:
        response = await client.get("/run/run-doge")

    assert response.status_code == 200
    assert "已拒絕" in response.text


async def test_the_in_memory_store_matches_the_analysis_store_protocol() -> None:
    """記憶體 store 與 Postgres store 必須可互換 —— 兩者都滿足接縫 4。"""
    from hoyabit_agent.seams import AnalysisStore

    store: AnalysisStore = InMemoryAnalysisStore()
    await store.save(an_outcome("run-1"))
    loaded = await store.load("run-1")
    assert loaded is not None
    assert await store.recent() == ("run-1",)
