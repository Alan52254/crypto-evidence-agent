"""ChartReaderSource 測試 —— 以 mock VisionModelAdapter 驗證行為。

驗證行為：
* 高信心度產出 Evidence
* 低信心度不產出
* URL 圖片下載失敗回傳空
* Evidence ID 前綴 CHART-
* facet 推斷邏輯
* stance_hint 計算
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from hoyabit_agent.domain import AnalysisRegime, Asset, Evidence, Facet
from hoyabit_agent.models.vision import ChartAnalysis, ChartDataPoint
from hoyabit_agent.sources.chart_reader import (
    ChartReaderSource,
    _infer_facet,
    trend_to_stance,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_chart_analysis(
    *,
    confidence: float = 0.85,
    trend_direction: str = "up",
    trend_description: str = "BTC 持續上升趨勢",
    raw_description: str = "Line chart showing upward trend.",
) -> ChartAnalysis:
    """建構測試用的 ChartAnalysis。"""
    return ChartAnalysis(
        chart_type="line",
        title="Test Chart",
        x_axis_unit="day",
        y_axis_unit="USD",
        latest_data_point=ChartDataPoint(x_label="2025-01", y_value=100_000.0),
        historical_peak=ChartDataPoint(x_label="2025-01", y_value=100_000.0),
        data_points=(ChartDataPoint(x_label="2025-01", y_value=100_000.0),),
        trend_direction=trend_direction,
        trend_description=trend_description,
        confidence=confidence,
        raw_description=raw_description,
    )


def _mock_vision_adapter(chart_analysis: ChartAnalysis) -> AsyncMock:
    """建構 mock VisionModelAdapter，extract_chart_data 回傳指定結果。"""
    adapter = AsyncMock()
    adapter.extract_chart_data = AsyncMock(return_value=chart_analysis)
    return adapter


def _responding_image(
    content: bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
    *,
    status: int = 200,
) -> httpx.AsyncClient:
    """回傳指定圖片 content 的 mock client。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=content)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _failing_client() -> httpx.AsyncClient:
    """下載時拋 ConnectError 的 mock client。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ── trend_to_stance ──────────────────────────────────────────────────────


class TestTrendToStance:
    """trend_to_stance 的計算邏輯。"""

    def test_up_with_full_confidence(self) -> None:
        assert trend_to_stance("up", 1.0) == pytest.approx(0.4)

    def test_down_with_full_confidence(self) -> None:
        assert trend_to_stance("down", 1.0) == pytest.approx(-0.4)

    def test_up_with_partial_confidence(self) -> None:
        assert trend_to_stance("up", 0.5) == pytest.approx(0.2)

    def test_down_with_partial_confidence(self) -> None:
        assert trend_to_stance("down", 0.7) == pytest.approx(-0.28)

    def test_sideways_is_neutral(self) -> None:
        assert trend_to_stance("sideways", 0.9) == 0.0

    def test_unclear_is_neutral(self) -> None:
        assert trend_to_stance("unclear", 0.8) == 0.0


# ── _infer_facet ─────────────────────────────────────────────────────────


class TestInferFacet:
    """context_hint → Facet 推斷邏輯。"""

    def test_none_hint_defaults_to_fundamental(self) -> None:
        assert _infer_facet(None) == Facet.FUNDAMENTAL

    def test_empty_hint_defaults_to_fundamental(self) -> None:
        assert _infer_facet("") == Facet.FUNDAMENTAL

    def test_funding_hint_is_positioning(self) -> None:
        assert _infer_facet("BTC funding rate") == Facet.POSITIONING

    def test_oi_hint_is_positioning(self) -> None:
        assert _infer_facet("open interest (OI) chart") == Facet.POSITIONING

    def test_m2_hint_is_fundamental(self) -> None:
        assert _infer_facet("US M2 supply growth") == Facet.FUNDAMENTAL

    def test_cpi_hint_is_fundamental(self) -> None:
        assert _infer_facet("CPI year-over-year") == Facet.FUNDAMENTAL

    def test_gdp_hint_is_fundamental(self) -> None:
        assert _infer_facet("GDP growth chart") == Facet.FUNDAMENTAL

    def test_rsi_hint_is_technical(self) -> None:
        assert _infer_facet("14-period RSI divergence") == Facet.TECHNICAL

    def test_ema_hint_is_technical(self) -> None:
        assert _infer_facet("EMA crossover signal") == Facet.TECHNICAL

    def test_unknown_hint_defaults_to_fundamental(self) -> None:
        assert _infer_facet("some random chart") == Facet.FUNDAMENTAL


# ── ChartReaderSource.fetch ──────────────────────────────────────────────


class TestChartReaderHighConfidence:
    """高信心度（>= 0.8）正確產出 Evidence。"""

    @pytest.mark.asyncio
    async def test_produces_evidence_with_correct_fields(self) -> None:
        analysis = _make_chart_analysis(confidence=0.85)
        adapter = _mock_vision_adapter(analysis)
        client = _responding_image()
        source = ChartReaderSource(client, adapter)

        result = await source.fetch(Asset.BTC, {"image_url": "https://example.com/chart.png"})

        assert len(result) == 1
        evidence = result[0]
        assert isinstance(evidence, Evidence)
        assert evidence.id.startswith("CHART-")
        assert evidence.facet == Facet.FUNDAMENTAL
        assert evidence.summary == "從資料來源【圖】中得知：BTC 持續上升趨勢"  # 含前綴
        assert evidence.stance_hint == pytest.approx(0.4 * 0.85)
        assert len(evidence.excerpts) == 1
        assert evidence.excerpts[0].url == "https://example.com/chart.png"
        assert "85%" in evidence.excerpts[0].locator

    @pytest.mark.asyncio
    async def test_evidence_id_is_deterministic_for_same_url(self) -> None:
        analysis = _make_chart_analysis(confidence=0.9)
        adapter = _mock_vision_adapter(analysis)
        client = _responding_image()
        source = ChartReaderSource(client, adapter)

        r1 = await source.fetch(Asset.BTC, {"image_url": "https://example.com/a.png"})
        r2 = await source.fetch(Asset.BTC, {"image_url": "https://example.com/a.png"})

        assert r1[0].id == r2[0].id


class TestChartReaderLowConfidence:
    """低信心度（< 0.3）不產出 Evidence。"""

    @pytest.mark.asyncio
    async def test_very_low_confidence_returns_empty(self) -> None:
        analysis = _make_chart_analysis(confidence=0.2)
        adapter = _mock_vision_adapter(analysis)
        client = _responding_image()
        source = ChartReaderSource(client, adapter)

        result = await source.fetch(Asset.BTC, {"image_url": "https://example.com/chart.png"})

        assert result == ()

    @pytest.mark.asyncio
    async def test_zero_confidence_returns_empty(self) -> None:
        analysis = _make_chart_analysis(confidence=0.0)
        adapter = _mock_vision_adapter(analysis)
        client = _responding_image()
        source = ChartReaderSource(client, adapter)

        result = await source.fetch(Asset.BTC, {"image_url": "https://example.com/chart.png"})

        assert result == ()


class TestChartReaderMediumConfidence:
    """中等信心度（0.3-0.8）產出帶前綴的 Evidence。"""

    @pytest.mark.asyncio
    async def test_moderate_confidence_has_warning_prefix(self) -> None:
        analysis = _make_chart_analysis(confidence=0.4, trend_description="震盪走勢")
        adapter = _mock_vision_adapter(analysis)
        client = _responding_image()
        source = ChartReaderSource(client, adapter)

        result = await source.fetch(Asset.BTC, {"image_url": "https://example.com/chart.png"})

        assert len(result) == 1
        assert result[0].summary.startswith("⚠️ 從資料來源【圖】中得知（模糊，僅趨勢判斷）：")

    @pytest.mark.asyncio
    async def test_good_confidence_has_parenthetical_prefix(self) -> None:
        analysis = _make_chart_analysis(confidence=0.65, trend_description="上升趨勢")
        adapter = _mock_vision_adapter(analysis)
        client = _responding_image()
        source = ChartReaderSource(client, adapter)

        result = await source.fetch(Asset.BTC, {"image_url": "https://example.com/chart.png"})

        assert len(result) == 1
        assert result[0].summary.startswith("從資料來源【圖】中得知：")


class TestChartReaderDownloadFailure:
    """URL 圖片下載失敗回傳空 tuple。"""

    @pytest.mark.asyncio
    async def test_connection_error_returns_empty(self) -> None:
        adapter = _mock_vision_adapter(_make_chart_analysis())
        client = _failing_client()
        source = ChartReaderSource(client, adapter)

        result = await source.fetch(Asset.BTC, {"image_url": "https://example.com/chart.png"})

        assert result == ()

    @pytest.mark.asyncio
    async def test_non_200_returns_empty(self) -> None:
        adapter = _mock_vision_adapter(_make_chart_analysis())
        client = _responding_image(status=404)
        source = ChartReaderSource(client, adapter)

        result = await source.fetch(Asset.BTC, {"image_url": "https://example.com/chart.png"})

        assert result == ()

    @pytest.mark.asyncio
    async def test_empty_image_url_returns_empty(self) -> None:
        adapter = _mock_vision_adapter(_make_chart_analysis())
        client = _responding_image()
        source = ChartReaderSource(client, adapter)

        result = await source.fetch(Asset.BTC, {"image_url": ""})

        assert result == ()


class TestChartReaderEvidenceId:
    """Evidence ID 前綴 CHART-。"""

    @pytest.mark.asyncio
    async def test_id_starts_with_chart_prefix(self) -> None:
        analysis = _make_chart_analysis(confidence=0.9)
        adapter = _mock_vision_adapter(analysis)
        client = _responding_image()
        source = ChartReaderSource(client, adapter)

        result = await source.fetch(Asset.ETH, {"image_url": "https://example.com/eth.png"})

        assert result[0].id.startswith("CHART-")

    @pytest.mark.asyncio
    async def test_id_has_hex_suffix(self) -> None:
        analysis = _make_chart_analysis(confidence=0.9)
        adapter = _mock_vision_adapter(analysis)
        client = _responding_image()
        source = ChartReaderSource(client, adapter)

        result = await source.fetch(Asset.BTC, {"image_url": "https://example.com/x.png"})

        # Format: CHART-XXXXXXXX (8 hex chars)
        evidence_id = result[0].id
        suffix = evidence_id.removeprefix("CHART-")
        assert len(suffix) == 8
        int(suffix, 16)  # should not raise


class TestChartReaderFacetInference:
    """fetch 依 context_hint 設定正確的 facet。"""

    @pytest.mark.asyncio
    async def test_funding_context_gives_positioning_facet(self) -> None:
        analysis = _make_chart_analysis(confidence=0.9)
        adapter = _mock_vision_adapter(analysis)
        client = _responding_image()
        source = ChartReaderSource(client, adapter)

        result = await source.fetch(
            Asset.BTC,
            {"image_url": "https://x.com/img.png", "context_hint": "funding rate chart"},
        )

        assert result[0].facet == Facet.POSITIONING

    @pytest.mark.asyncio
    async def test_rsi_context_gives_technical_facet(self) -> None:
        analysis = _make_chart_analysis(confidence=0.9)
        adapter = _mock_vision_adapter(analysis)
        client = _responding_image()
        source = ChartReaderSource(client, adapter)

        result = await source.fetch(
            Asset.BTC,
            {"image_url": "https://x.com/img.png", "context_hint": "RSI 14 divergence"},
        )

        assert result[0].facet == Facet.TECHNICAL

    @pytest.mark.asyncio
    async def test_no_context_gives_fundamental_facet(self) -> None:
        analysis = _make_chart_analysis(confidence=0.9)
        adapter = _mock_vision_adapter(analysis)
        client = _responding_image()
        source = ChartReaderSource(client, adapter)

        result = await source.fetch(
            Asset.BTC,
            {"image_url": "https://x.com/img.png"},
        )

        assert result[0].facet == Facet.FUNDAMENTAL


class TestChartReaderStanceHint:
    """stance_hint 正確反映趨勢方向和信心度。"""

    @pytest.mark.asyncio
    async def test_uptrend_positive_stance(self) -> None:
        analysis = _make_chart_analysis(confidence=0.8, trend_direction="up")
        adapter = _mock_vision_adapter(analysis)
        client = _responding_image()
        source = ChartReaderSource(client, adapter)

        result = await source.fetch(Asset.BTC, {"image_url": "https://x.com/img.png"})

        assert result[0].stance_hint == pytest.approx(0.4 * 0.8)

    @pytest.mark.asyncio
    async def test_downtrend_negative_stance(self) -> None:
        analysis = _make_chart_analysis(confidence=0.9, trend_direction="down")
        adapter = _mock_vision_adapter(analysis)
        client = _responding_image()
        source = ChartReaderSource(client, adapter)

        result = await source.fetch(Asset.BTC, {"image_url": "https://x.com/img.png"})

        assert result[0].stance_hint == pytest.approx(-0.4 * 0.9)

    @pytest.mark.asyncio
    async def test_sideways_neutral_stance(self) -> None:
        analysis = _make_chart_analysis(confidence=0.8, trend_direction="sideways")
        adapter = _mock_vision_adapter(analysis)
        client = _responding_image()
        source = ChartReaderSource(client, adapter)

        result = await source.fetch(Asset.BTC, {"image_url": "https://x.com/img.png"})

        assert result[0].stance_hint == 0.0


class TestChartReaderSpec:
    """ToolSpec 的正確性。"""

    def test_spec_name(self) -> None:
        adapter = AsyncMock()
        client = httpx.AsyncClient()
        source = ChartReaderSource(client, adapter)
        assert source.spec.name == "chart_reader"

    def test_spec_requires_image_url(self) -> None:
        adapter = AsyncMock()
        client = httpx.AsyncClient()
        source = ChartReaderSource(client, adapter)
        assert "image_url" in source.spec.parameters["properties"]
        assert "image_url" in source.spec.parameters["required"]

    def test_supported_regimes_is_live_only(self) -> None:
        adapter = AsyncMock()
        client = httpx.AsyncClient()
        source = ChartReaderSource(client, adapter)
        assert source.supported_regimes == frozenset({AnalysisRegime.LIVE})


class TestChartReaderVisionAdapterFailure:
    """VisionModelAdapter 拋例外時回傳空 tuple。"""

    @pytest.mark.asyncio
    async def test_adapter_exception_returns_empty(self) -> None:
        adapter = AsyncMock()
        adapter.extract_chart_data = AsyncMock(side_effect=RuntimeError("model crash"))
        client = _responding_image()
        source = ChartReaderSource(client, adapter)

        result = await source.fetch(Asset.BTC, {"image_url": "https://x.com/img.png"})

        assert result == ()
