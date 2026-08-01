"""WebChartCaptureSource 測試 —— 不需要真正的 Playwright。

驗證行為：
* CHART_REGISTRY 涵蓋所有 12 筆預定義圖表
* 每筆 ChartSource 具備 url, selector, facet, description
* spec name 為 web_chart_capture
* spec parameters 含 chart_id enum 與 custom_url
* supported_regimes 只有 LIVE
* chart_id 不存在時回傳空 tuple
* Playwright import 失敗時 graceful degradation
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from hoyabit_agent.domain import AnalysisRegime, Asset, Facet
from hoyabit_agent.models.vision import VisionModelAdapter
from hoyabit_agent.sources.web_chart_capture import (
    CHART_REGISTRY,
    ChartSource,
    WebChartCaptureSource,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_source() -> WebChartCaptureSource:
    """建構測試用的 WebChartCaptureSource（不需真實 adapter）。"""
    client = httpx.AsyncClient()
    adapter = MagicMock(spec=VisionModelAdapter)
    return WebChartCaptureSource(client=client, vision_adapter=adapter)


# ── CHART_REGISTRY 測試 ──────────────────────────────────────────────────


class TestChartRegistry:
    """驗證 CHART_REGISTRY 的完整性。"""

    def test_registry_contains_fifteen_entries(self) -> None:
        """CHART_REGISTRY 應有 15 筆預定義圖表。"""
        assert len(CHART_REGISTRY) == 15

    def test_all_entries_are_chart_source(self) -> None:
        """每筆 entry 都是 ChartSource 實例。"""
        for key, source in CHART_REGISTRY.items():
            assert isinstance(source, ChartSource), f"{key} is not ChartSource"

    def test_each_entry_has_url(self) -> None:
        """每筆 ChartSource 都有非空 url。"""
        for key, source in CHART_REGISTRY.items():
            assert source.url, f"{key} has empty url"
            assert source.url.startswith("https://"), f"{key} url not https"

    def test_each_entry_has_selector(self) -> None:
        """每筆 ChartSource 都有非空 selector。"""
        for key, source in CHART_REGISTRY.items():
            assert source.selector, f"{key} has empty selector"

    def test_each_entry_has_facet(self) -> None:
        """每筆 ChartSource 的 facet 是有效的 Facet enum。"""
        for key, source in CHART_REGISTRY.items():
            assert isinstance(source.facet, Facet), f"{key} facet is not Facet"

    def test_each_entry_has_description(self) -> None:
        """每筆 ChartSource 都有非空 description。"""
        for key, source in CHART_REGISTRY.items():
            assert source.description, f"{key} has empty description"

    def test_expected_chart_ids_present(self) -> None:
        """預期的 15 個 chart_id 全部存在。"""
        expected_ids = {
            "us_m2_supply",
            "us_fed_funds_rate",
            "us_cpi_yoy",
            "dxy_index",
            "btc_exchange_reserve",
            "btc_whale_addresses_1k",
            "btc_exchange_netflow",
            "btc_whale_ratio",
            "btc_etf_flow",
            "eth_etf_flow",
            "btc_funding_rate",
            "btc_open_interest",
            "liquidation_heatmap",
            "eth_gas_burned",
            "defi_tvl_overview",
        }
        assert set(CHART_REGISTRY.keys()) == expected_ids

    def test_chart_source_is_frozen(self) -> None:
        """ChartSource 是 frozen dataclass，不可修改。"""
        source = CHART_REGISTRY["us_m2_supply"]
        with pytest.raises(Exception):  # FrozenInstanceError
            source.url = "https://changed.example.com"  # type: ignore[misc]

    def test_default_wait_ms(self) -> None:
        """預設 wait_ms 為 3000。"""
        for key, source in CHART_REGISTRY.items():
            assert source.wait_ms == 3000, f"{key} has non-default wait_ms"


# ── ToolSpec 測試 ────────────────────────────────────────────────────────


class TestToolSpec:
    """驗證 WebChartCaptureSource 的 spec property。"""

    def test_spec_name(self) -> None:
        """spec name 應為 web_chart_capture。"""
        source = _make_source()
        assert source.spec.name == "web_chart_capture"

    def test_spec_has_chart_id_parameter(self) -> None:
        """spec parameters 包含 chart_id，且有 enum。"""
        source = _make_source()
        props = source.spec.parameters["properties"]
        assert "chart_id" in props
        assert props["chart_id"]["type"] == "string"
        assert "enum" in props["chart_id"]

    def test_spec_chart_id_enum_matches_registry(self) -> None:
        """chart_id 的 enum 值與 CHART_REGISTRY keys 一致。"""
        source = _make_source()
        props = source.spec.parameters["properties"]
        enum_values = set(props["chart_id"]["enum"])
        assert enum_values == set(CHART_REGISTRY.keys())

    def test_spec_has_custom_url_parameter(self) -> None:
        """spec parameters 包含 custom_url。"""
        source = _make_source()
        props = source.spec.parameters["properties"]
        assert "custom_url" in props
        assert props["custom_url"]["type"] == "string"

    def test_spec_has_description(self) -> None:
        """spec 有非空 description。"""
        source = _make_source()
        assert source.spec.description


# ── supported_regimes 測試 ───────────────────────────────────────────────


class TestSupportedRegimes:
    """驗證 supported_regimes 屬性。"""

    def test_only_live_regime(self) -> None:
        """只支援 LIVE 模式。"""
        source = _make_source()
        assert source.supported_regimes == frozenset({AnalysisRegime.LIVE})

    def test_backtest_not_supported(self) -> None:
        """不支援 BACKTEST 模式。"""
        source = _make_source()
        assert AnalysisRegime.BACKTEST not in source.supported_regimes


# ── fetch degradation 測試 ───────────────────────────────────────────────


class TestFetchDegradation:
    """驗證 fetch 在各種失敗情境下的 graceful degradation。"""

    @pytest.mark.asyncio
    async def test_unknown_chart_id_returns_empty(self) -> None:
        """chart_id 不存在且無 custom_url 時回傳空 tuple。"""
        source = _make_source()
        result = await source.fetch(Asset.BTC, {"chart_id": "nonexistent_chart"})
        assert result == ()

    @pytest.mark.asyncio
    async def test_empty_arguments_returns_empty(self) -> None:
        """空 arguments 回傳空 tuple。"""
        source = _make_source()
        result = await source.fetch(Asset.BTC, {})
        assert result == ()

    @pytest.mark.asyncio
    async def test_playwright_import_failure_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Playwright import 失敗時 graceful degradation，回傳空 tuple。"""
        import builtins

        original_import = builtins.__import__

        def _mock_import(name: str, *args: object, **kwargs: object) -> object:
            if "playwright" in name:
                raise ImportError("playwright not installed")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _mock_import)

        source = _make_source()
        result = await source.fetch(Asset.BTC, {"chart_id": "us_m2_supply"})
        assert result == ()

    @pytest.mark.asyncio
    async def test_invalid_custom_url_returns_empty(self) -> None:
        """custom_url 不是 http(s) 時回傳空 tuple。"""
        source = _make_source()
        result = await source.fetch(Asset.BTC, {"custom_url": "not-a-url"})
        assert result == ()

    @pytest.mark.asyncio
    async def test_none_chart_id_without_custom_url_returns_empty(self) -> None:
        """chart_id 為 None 且無 custom_url 回傳空 tuple。"""
        source = _make_source()
        result = await source.fetch(Asset.BTC, {"chart_id": None})
        assert result == ()


# ── ScrapedChart + CHART_ELEMENT_SELECTORS 測試 ──────────────────────────


class TestScrapedChartDataclass:
    def test_scraped_chart_is_frozen(self) -> None:
        from hoyabit_agent.sources.web_chart_capture import ScrapedChart

        sc = ScrapedChart(path="/tmp/x.png", source_id="test-001", url="https://example.com")
        with pytest.raises(Exception):
            sc.path = "/changed"  # type: ignore

    def test_chart_element_selectors_not_empty(self) -> None:
        from hoyabit_agent.sources.web_chart_capture import CHART_ELEMENT_SELECTORS

        assert len(CHART_ELEMENT_SELECTORS) >= 5
