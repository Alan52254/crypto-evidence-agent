"""圖表管線 —— 從證據到報告到 API payload 的端到端串接。

這些測試存在的理由：圖表功能的程式碼原本全部寫好了，但沒有任何一段
把它接起來 —— `enhanced_report_markdown` 的圖表區依賴呼叫端傳入
`chart_data`，而唯一的呼叫端不傳，於是那個分支永遠是 False。
單元測試各自通過，整條路徑卻是死的。這裡守的就是「接起來了」。
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from hoyabit_agent.api_contract import outcome_payload
from hoyabit_agent.domain import (
    AnalysisOutcome,
    AnalysisRequest,
    ClaimRole,
    DraftClaim,
    Facet,
)
from hoyabit_agent.models.prompts import synthesis_prompt
from hoyabit_agent.run import analyse
from hoyabit_agent.sources.candlestick_builder import CandlestickBuilderSource
from hoyabit_agent.testing import ScriptedModel, StaticSource, evidence

FAKE_KLINES = [
    [
        1700000000000 + i * 86400000,
        "65000",
        "66500",
        "64500",
        "65800",
        "1200",
        0,
        "0",
        0,
        "0",
        "0",
        "0",
    ]
    for i in range(30)
]


async def run_with_charts() -> AnalysisOutcome:
    """跑一個帶圖表來源的即時分析回合。

    `as_of_date=date.today()` 與 production 一致（見 `viz/server.py` 的即時
    demo 端點）。用 `AnalysisRequest` 的預設值會落在回測模式，圖表工具
    會被來源過濾正確地排除掉 —— 那是另一條路徑，見本檔最後一個測試。
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=FAKE_KLINES)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    model = ScriptedModel(
        plans=[],
        claims=[
            DraftClaim(
                "BTC 30 日區間震盪，收盤位於區間中段。",
                ("candlestick-btc-1d",),
                Facet.TECHNICAL,
                ClaimRole.FACT,
            ),
            DraftClaim(
                "量能未擴張，突破缺乏跟進買盤。",
                ("E-NEWS",),
                Facet.FUNDAMENTAL,
                ClaimRole.COUNTER_EVIDENCE,
            ),
        ],
    )

    return await analyse(
        AnalysisRequest("BTC", "BTC 近期走勢如何", as_of_date=date.today()),
        sources=[
            CandlestickBuilderSource(client),
            StaticSource([evidence("E-NEWS", Facet.FUNDAMENTAL, -0.4)], name="news"),
        ],
        model=model,
        max_iterations=2,
    )


@pytest.mark.asyncio
async def test_charts_are_gathered_without_the_model_asking_for_them() -> None:
    """技術面圖表是報告的固定組成，不該取決於模型有沒有想到要畫。

    這裡的假模型完全不發工具呼叫（`plans=[]`），圖表仍必須存在 ——
    它來自預取，而非規劃。
    """
    outcome = await run_with_charts()
    assert outcome.report is not None

    figures = [f for item in outcome.report.evidence for f in item.figures]
    assert figures, "預取未取得圖表"


@pytest.mark.asyncio
async def test_the_report_renders_the_gathered_figures() -> None:
    """報告必須真的呈現圖表 —— 這是先前整條路徑斷掉的地方。"""
    outcome = await run_with_charts()
    markdown = outcome_payload(outcome)["enhanced_report_md"]

    figure_count = len(
        [f for item in outcome.report.evidence for f in item.figures]  # type: ignore[union-attr]
    )
    assert "圖表" in markdown
    assert markdown.count("data:image/svg+xml;base64,") >= figure_count


@pytest.mark.asyncio
async def test_each_rendered_figure_cites_its_evidence() -> None:
    """圖也是證據，因此每張圖都要標得出它的證據識別碼。"""
    outcome = await run_with_charts()
    markdown = outcome_payload(outcome)["enhanced_report_md"]

    for item in outcome.report.evidence:  # type: ignore[union-attr]
        if item.figures:
            assert item.id in markdown


@pytest.mark.asyncio
async def test_figures_reach_the_frontend_payload() -> None:
    """前端靠 payload 的 `figures` 呈現圖表，欄位缺一不可。"""
    outcome = await run_with_charts()
    records = [e for e in outcome_payload(outcome)["evidence"] if e.get("figures")]

    assert records, "API payload 未帶 figures"
    for figure in records[0]["figures"]:
        assert {"kind", "caption", "src", "source_url", "alt"} <= set(figure)
        assert figure["src"]


@pytest.mark.asyncio
async def test_image_data_never_reaches_the_source_excerpts() -> None:
    """來源片段的語意是「可引用的原文」，base64 不是任何人能核對的東西。"""
    outcome = await run_with_charts()

    for item in outcome.report.evidence:  # type: ignore[union-attr]
        for excerpt in item.excerpts:
            assert "data:image" not in excerpt.text
            assert "base64" not in excerpt.text


@pytest.mark.asyncio
async def test_image_data_never_reaches_the_synthesis_prompt() -> None:
    """圖片資料進提示詞會用數 KB 的 base64 排擠掉真正的證據。

    這是把圖從 `excerpt.text` 搬到 `figures` 的主要理由之一，
    值得一條專門的迴歸防護。
    """
    outcome = await run_with_charts()
    report = outcome.report
    assert report is not None

    prompt = synthesis_prompt(report.asset, report.evidence, report.question)
    assert "data:image" not in prompt


@pytest.mark.asyncio
async def test_backtest_mode_excludes_the_live_chart_builder() -> None:
    """回測模式不得取到即時 K 線 —— 那是偷看截止日之後的價格（ADR 0005）。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=FAKE_KLINES)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    outcome = await analyse(
        # 不指定 as_of_date → 預設資料集截止日 → 回測模式
        AnalysisRequest("BTC", "BTC 當時的走勢如何"),
        sources=[
            CandlestickBuilderSource(client),
            StaticSource([evidence("E-TECH", Facet.TECHNICAL, 0.3)], name="dataset"),
        ],
        model=ScriptedModel(plans=[], claims=[]),
        max_iterations=1,
    )

    assert outcome.report is not None
    assert all(not item.figures for item in outcome.report.evidence)
