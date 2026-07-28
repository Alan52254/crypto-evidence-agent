"""Candlestick Builder 單元測試。"""

from __future__ import annotations

import json
import httpx
import pytest

from hoyabit_agent.domain import Asset, Facet
from hoyabit_agent.sources.candlestick_builder import CandlestickBuilderSource


@pytest.mark.asyncio
async def test_candlestick_builder_spec() -> None:
    async with httpx.AsyncClient() as client:
        source = CandlestickBuilderSource(client)
        assert source.spec.name == "candlestick_chart_builder"
        assert "interval" in source.spec.parameters["properties"]


@pytest.mark.asyncio
async def test_candlestick_builder_mock_fetch() -> None:
    # 模擬 Binance K 線回傳數據
    fake_klines = [
        [1700000000000 + i * 86400000, "65000", "66000", "64500", "65800", "1200", 0, "0", 0, "0", "0", "0"]
        for i in range(20)
    ]

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fake_klines)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    source = CandlestickBuilderSource(client)

    result = await source.fetch(Asset.BTC, {"interval": "1d", "limit": 20})

    assert len(result) == 1
    evidence = result[0]
    assert evidence.facet == Facet.TECHNICAL
    assert "BTC" in evidence.summary
    assert "data:image/svg+xml;base64," in evidence.excerpts[0].text
