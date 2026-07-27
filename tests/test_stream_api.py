import json
from collections.abc import Callable
from datetime import date

import httpx

from hoyabit_agent.domain import (
    AnalysisOutcome,
    AnalysisRegime,
    AnalysisRequest,
    Asset,
    Claim,
    Confidence,
    Facet,
    Report,
    Stance,
    Trace,
    TraceNode,
    TraceNodeKind,
    analysis_regime,
)
from hoyabit_agent.testing import InMemoryAnalysisStore, evidence
from hoyabit_agent.viz.server import create_app


def an_outcome(run_id: str) -> AnalysisOutcome:
    report = Report(
        asset=Asset.BTC,
        stance=Stance.BULLISH,
        confidence=Confidence(0.75, {facet: Stance.NEUTRAL for facet in Facet}),
        claims=(Claim("站上季線", ("E1",), Facet.TECHNICAL),),
        dropped_claims=(),
        evidence=(evidence("E1", Facet.TECHNICAL, 0.8),),
    )
    return AnalysisOutcome(
        run_id,
        report,
        Trace(run_id, (TraceNode(0, TraceNodeKind.REPORT, "完成"),)),
        None,
    )

async def immediate_runner(
    request: AnalysisRequest,
    run_id: str,
    on_trace: Callable[[TraceNode], None],
) -> AnalysisOutcome:
    on_trace(TraceNode(0, TraceNodeKind.PLAN, f"規劃 {request.asset}"))
    return an_outcome(run_id)


async def test_analysis_endpoint_and_sse_share_the_run_contract() -> None:
    store = InMemoryAnalysisStore()
    app = create_app(store, immediate_runner)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        started = await client.post(
            "/api/v1/analyse", json={"asset": "BTC", "question": "驗證盤整假設"}
        )
        assert started.status_code == 202
        body = started.json()
        streamed = await client.get(body["stream_url"])

    assert "event: trace" in streamed.text
    assert "event: complete" in streamed.text
    trace_data = streamed.text.split("event: trace\ndata: ", 1)[1].split("\n\n", 1)[0]
    assert json.loads(trace_data)["reason"] == "規劃 BTC"


async def test_analysis_endpoint_defaults_as_of_date_to_today_for_live_regime() -> None:
    """未帶 as_of_date 的請求(前端目前的唯一行為)必須落在即時模式。

    這個端點驅動的是即時 demo:若 as_of_date 靜默退回資料集截止日
    (2026-05-31),回測 regime 會把所有 live 證據源過濾掉(見 Step 4),
    讓即時展示悄悄變成只查歷史資料集 —— 使用者看不出差異,但系統其實
    什麼都沒抓。
    """
    seen: list[AnalysisRequest] = []

    async def capturing_runner(
        request: AnalysisRequest,
        run_id: str,
        on_trace: Callable[[TraceNode], None],
    ) -> AnalysisOutcome:
        seen.append(request)
        return an_outcome(run_id)

    app = create_app(InMemoryAnalysisStore(), capturing_runner)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/analyse", json={"asset": "BTC", "question": "BTC 現況如何"}
        )
        assert response.status_code == 202
        # 等背景任務跑完 —— 與既有測試相同的手法(打 SSE 直到收到終止事件)。
        await client.get(response.json()["stream_url"])
    assert len(seen) == 1
    regime = analysis_regime(seen[0].as_of_date, today=date.today())
    assert regime is AnalysisRegime.LIVE


async def test_analysis_endpoint_rejects_unsupported_asset() -> None:
    app = create_app(InMemoryAnalysisStore(), immediate_runner)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/analyse", json={"asset": "DOGE", "question": "分析"}
        )
    assert response.status_code == 422
