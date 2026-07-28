"""Vision OCR Chart Extractor 單元測試。"""

from __future__ import annotations

import httpx
import pytest

from hoyabit_agent.domain import Asset, Facet
from hoyabit_agent.sources.chart_ocr import ChartOCRSource


@pytest.mark.asyncio
async def test_chart_ocr_spec() -> None:
    async with httpx.AsyncClient() as client:
        source = ChartOCRSource(client)
        assert source.spec.name == "ocr_chart_extractor"
        assert "image_url" in source.spec.parameters["properties"]


@pytest.mark.asyncio
async def test_chart_ocr_invalid_url() -> None:
    async with httpx.AsyncClient() as client:
        source = ChartOCRSource(client)
        # Invalid URL should fail gracefully without exception
        result = await source.fetch(Asset.BTC, {"image_url": "invalid_url"})
        assert result == ()


@pytest.mark.asyncio
async def test_chart_ocr_mock_fetch() -> None:
    class MockModelProvider:
        async def analyze_image(self, bytes_data: bytes, mime: str, asset: str, context: str) -> dict:
            return {
                "summary": "Glassnode 交易所比特幣儲備量下降 4.2%",
                "stance_hint": 0.45,
                "facet": "fundamental",
            }

    async with httpx.AsyncClient() as client:
        source = ChartOCRSource(client, model_provider=MockModelProvider())
        
        # Test with mock httpx response handler
        async def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"\x89PNG\r\n\x1a\n", headers={"content-type": "image/png"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
        source = ChartOCRSource(client, model_provider=MockModelProvider())

        res = await source.fetch(
            Asset.BTC,
            {
                "image_url": "https://example.com/glassnode-reserve.png",
                "context_description": "Glassnode 儲備量圖",
            },
        )

        assert len(res) == 1
        evidence = res[0]
        assert evidence.facet == Facet.FUNDAMENTAL
        assert "Glassnode" in evidence.summary
        assert evidence.stance_hint == 0.45
        assert evidence.excerpts[0].url == "https://example.com/glassnode-reserve.png"
