"""Candlestick Builder 單元測試。"""

from __future__ import annotations

import httpx
import pytest

from hoyabit_agent.domain import AnalysisRegime, Asset, Facet, FigureKind
from hoyabit_agent.sources.candlestick_builder import CandlestickBuilderSource


@pytest.mark.asyncio
async def test_candlestick_builder_spec() -> None:
    async with httpx.AsyncClient() as client:
        source = CandlestickBuilderSource(client)
        assert source.spec.name == "candlestick_chart_builder"
        assert "interval" in source.spec.parameters["properties"]


def _fake_klines(count: int = 20) -> list[list[object]]:
    return [
        [
            1700000000000 + i * 86400000,
            "65000",
            "66000",
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
        for i in range(count)
    ]


def _source_with_fake_binance() -> CandlestickBuilderSource:
    async def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_fake_klines())

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    return CandlestickBuilderSource(client)


@pytest.mark.asyncio
async def test_candlestick_builder_yields_one_technical_evidence() -> None:
    result = await _source_with_fake_binance().fetch(Asset.BTC, {"interval": "1d", "limit": 20})

    assert len(result) == 1
    evidence = result[0]
    assert evidence.facet == Facet.TECHNICAL
    assert "BTC" in evidence.summary


@pytest.mark.asyncio
async def test_charts_are_carried_as_figures_not_buried_in_the_excerpt() -> None:
    """圖走 `figures`，來源片段只放可引用的文字。

    先前實作把 base64 圖片塞進 `excerpt.text`，造成兩個問題：
    來源片段的語意被破壞（base64 不是任何人能核對的引用），
    而且那段文字會隨證據送進 synthesise 提示詞，用數 KB 的圖片資料
    排擠掉真正的證據。
    """
    result = await _source_with_fake_binance().fetch(Asset.BTC, {"interval": "1d"})
    evidence = result[0]

    assert evidence.figures, "K 線工具必須產出圖表"
    assert all(f.data_uri and f.data_uri.startswith("data:image/svg+xml;base64,")
               for f in evidence.figures)

    excerpt_text = evidence.excerpts[0].text
    assert "base64" not in excerpt_text
    assert "data:image" not in excerpt_text
    # 片段仍必須是有內容、可核對的觀察，不能因為搬走圖就變空殼
    assert "65,800" in excerpt_text or "65800" in excerpt_text


@pytest.mark.asyncio
async def test_figures_describe_what_they_show() -> None:
    """每張圖都要有說明與 alt —— 報告與無障礙讀者都靠它辨識這是哪張圖。"""
    result = await _source_with_fake_binance().fetch(Asset.BTC, {"interval": "4h"})

    for figure in result[0].figures:
        assert figure.caption.strip()
        assert figure.alt.strip()
        assert figure.kind is FigureKind.GENERATED


@pytest.mark.asyncio
async def test_the_chart_builder_is_live_only() -> None:
    """本工具畫的是**現在**的 K 線，在回測模式下會取到截止日之後的價格。

    這是 ADR 0005 的偷看未來風險：`binance.py` 已是 LIVE only，
    圖表工具若宣稱全模式合規，就成了繞過該限制的後門。
    """
    source = _source_with_fake_binance()
    assert source.supported_regimes == frozenset({AnalysisRegime.LIVE})
