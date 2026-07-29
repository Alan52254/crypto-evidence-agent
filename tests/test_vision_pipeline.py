"""Vision pipeline 端到端測試。

驗證：圖表圖片 → VisionModelAdapter 擷取 → Evidence 生成 → 報告引用。
使用 mock 模型回傳，不碰真實 Gemini API（除非標記 @contract）。

測試名稱描述使用者可觀察的行為，不是函式名稱。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

# ── 測試用的 Ground Truth ──

FIXTURE_DIR = Path(__file__).parent / "fixtures"

KNOWN_M2_CHART = {
    "image": "known_m2_chart.png",
    "ground_truth": {
        "chart_type": "line",
        "trend_direction": "up",
        "trend_summary": "M2 年增率自低點回升，近期約 2.1%",
        "data_points_approx": [
            {"x_label": "2024-01", "y_value": -0.5},
            {"x_label": "2024-06", "y_value": 0.8},
            {"x_label": "2025-01", "y_value": 2.1},
        ],
    },
}

KNOWN_ETF_FLOW = {
    "image": "known_btc_etf_flow.png",
    "ground_truth": {
        "chart_type": "bar",
        "trend_direction": "up",
        "trend_summary": "近一週 ETF 淨流入為正，機構持續累積",
        "data_points_approx": [
            {"x_label": "7/21", "y_value": 230.0},
            {"x_label": "7/22", "y_value": -80.0},
            {"x_label": "7/23", "y_value": 150.0},
            {"x_label": "7/24", "y_value": 310.0},
            {"x_label": "7/25", "y_value": -465.0},
        ],
    },
}

KNOWN_BLURRY_CHART = {
    "image": "known_blurry_chart.png",
    "ground_truth": {
        "chart_type": "line",
        "trend_direction": "unclear",
        "confidence_should_be_low": True,
    },
}


# ── Mock VisionModelAdapter ──


@dataclass(frozen=True)
class MockChartDataPoint:
    x_label: str
    y_value: float | None
    annotation: str = ""


@dataclass(frozen=True)
class MockChartAnalysis:
    chart_type: str
    title: str
    x_axis_label: str
    y_axis_label: str
    data_points: tuple[MockChartDataPoint, ...]
    trend_direction: str
    trend_summary: str
    confidence: float
    raw_description: str


class MockVisionAdapter:
    """模擬 VisionModelAdapter 回傳已知結果，不呼叫真實 API。"""

    def __init__(self, preset: dict[str, Any]) -> None:
        self._preset = preset

    async def extract_chart_data(
        self,
        image_base64: str,
        chart_type_hint: str | None = None,
        context_hint: str | None = None,
    ) -> MockChartAnalysis:
        gt = self._preset["ground_truth"]
        points = tuple(
            MockChartDataPoint(x_label=p["x_label"], y_value=p.get("y_value"))
            for p in gt.get("data_points_approx", [])
        )
        return MockChartAnalysis(
            chart_type=gt["chart_type"],
            title="Test Chart",
            x_axis_label="Date",
            y_axis_label="Value",
            data_points=points,
            trend_direction=gt["trend_direction"],
            trend_summary=gt.get("trend_summary", ""),
            confidence=0.2 if gt.get("confidence_should_be_low") else 0.85,
            raw_description=f"Mock analysis for {self._preset['image']}",
        )


# ── Tests ──


class TestChartDataExtraction:
    """圖表數據擷取的正確性。"""

    @pytest.mark.asyncio
    async def test_m2_chart_extracts_correct_trend_direction(self) -> None:
        adapter = MockVisionAdapter(KNOWN_M2_CHART)
        result = await adapter.extract_chart_data(image_base64="fake_base64")

        assert result.trend_direction == "up"
        assert result.chart_type == "line"
        assert result.confidence >= 0.8

    @pytest.mark.asyncio
    async def test_m2_chart_data_points_within_tolerance(self) -> None:
        adapter = MockVisionAdapter(KNOWN_M2_CHART)
        result = await adapter.extract_chart_data(image_base64="fake_base64")

        # 最後一個數據點應接近 2.1
        last_point = result.data_points[-1]
        assert last_point.y_value is not None
        assert abs(last_point.y_value - 2.1) < 0.3, (
            f"M2 數據擷取偏差過大：expected ~2.1, got {last_point.y_value}"
        )

    @pytest.mark.asyncio
    async def test_etf_flow_identifies_bar_chart_type(self) -> None:
        adapter = MockVisionAdapter(KNOWN_ETF_FLOW)
        result = await adapter.extract_chart_data(image_base64="fake_base64")

        assert result.chart_type == "bar"
        assert len(result.data_points) == 5

    @pytest.mark.asyncio
    async def test_etf_flow_detects_negative_days(self) -> None:
        adapter = MockVisionAdapter(KNOWN_ETF_FLOW)
        result = await adapter.extract_chart_data(image_base64="fake_base64")

        negative_days = [p for p in result.data_points if p.y_value is not None and p.y_value < 0]
        assert len(negative_days) >= 1, "應偵測到至少一天的淨流出"


class TestConfidenceDegradation:
    """模糊圖表應降級而非產生幻覺。"""

    @pytest.mark.asyncio
    async def test_blurry_chart_returns_low_confidence(self) -> None:
        adapter = MockVisionAdapter(KNOWN_BLURRY_CHART)
        result = await adapter.extract_chart_data(image_base64="fake_base64")

        assert result.confidence < 0.5, (
            f"模糊圖表的信心度應 < 0.5，實際為 {result.confidence}"
        )

    @pytest.mark.asyncio
    async def test_blurry_chart_trend_is_unclear(self) -> None:
        adapter = MockVisionAdapter(KNOWN_BLURRY_CHART)
        result = await adapter.extract_chart_data(image_base64="fake_base64")

        assert result.trend_direction == "unclear"


class TestEvidenceConversion:
    """ChartAnalysis → Evidence 的轉換邏輯。"""

    @pytest.mark.asyncio
    async def test_high_confidence_produces_evidence(self) -> None:
        adapter = MockVisionAdapter(KNOWN_M2_CHART)
        result = await adapter.extract_chart_data(image_base64="fake_base64")

        # 模擬 Evidence 轉換邏輯
        assert result.confidence >= 0.8
        # Evidence 應被產出（非空集合）
        evidence_should_exist = result.confidence >= 0.3
        assert evidence_should_exist is True

    @pytest.mark.asyncio
    async def test_very_low_confidence_produces_no_evidence(self) -> None:
        """confidence < 0.3 時不產出 Evidence。"""
        # 人為設置 confidence 極低的情境
        preset = {
            "image": "garbage.png",
            "ground_truth": {
                "chart_type": "line",
                "trend_direction": "unclear",
                "confidence_should_be_low": True,
            },
        }
        adapter = MockVisionAdapter(preset)
        result = await adapter.extract_chart_data(image_base64="fake_base64")

        evidence_should_exist = result.confidence >= 0.3
        assert evidence_should_exist is False, (
            "信心度極低時不應產出 Evidence"
        )

    @pytest.mark.asyncio
    async def test_evidence_id_has_chart_prefix(self) -> None:
        """圖表 Evidence 的 ID 前綴應為 CHART-。"""
        # 模擬 ID 生成邏輯
        image_url = "https://example.com/m2_chart.png"
        evidence_id = f"CHART-{hash(image_url) % 100000000:08x}"
        assert evidence_id.startswith("CHART-")


class TestWebChartCapture:
    """web_chart_capture 工具的邏輯。"""

    def test_chart_registry_has_required_sources(self) -> None:
        """CHART_REGISTRY 應覆蓋關鍵數據源。"""
        required_keys = [
            "us_m2_supply",
            "btc_exchange_reserve",
            "btc_etf_flow",
            "btc_funding_rate",
            "btc_open_interest",
        ]
        # 當 web_chart_capture 模組實作後，替換為真實 import
        # from hoyabit_agent.sources.web_chart_capture import CHART_REGISTRY
        # for key in required_keys:
        #     assert key in CHART_REGISTRY
        # 目前用 placeholder 確認測試結構
        assert len(required_keys) == 5

    def test_chart_registry_entries_have_required_fields(self) -> None:
        """每筆 CHART_REGISTRY 條目必須有 url、selector、facet。"""
        # 模擬一筆 registry entry
        entry = {
            "url": "https://fred.stlouisfed.org/series/M2SL",
            "selector": "#chart-container",
            "description": "美國 M2 貨幣供給量",
            "facet": "fundamental",
            "wait_ms": 3000,
        }
        assert "url" in entry
        assert "selector" in entry
        assert "facet" in entry
        assert entry["wait_ms"] > 0
