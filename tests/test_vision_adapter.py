"""VisionModelAdapter 測試 —— 以 httpx MockTransport 攔截，不碰真實 API。

驗證行為：
* JSON mode 回傳正確解析為 ChartAnalysis
* 網路錯誤、非 200、格式錯誤 → 降級為 confidence=0 的空結果
* API key 來自環境變數
* 結構化解析容許部分欄位缺失
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from hoyabit_agent.models.vision import (
    API_KEY_ENV,
    ChartAnalysis,
    ChartDataPoint,
    VisionModelAdapter,
    _empty_analysis,
    _parse_chart_analysis,
    _parse_data_point,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def gemini_reply(text: str) -> dict[str, Any]:
    """模擬 Gemini 回應結構。"""
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def responding(
    body: Any,
    *,
    status: int = 200,
    capture: list[httpx.Request] | None = None,
) -> httpx.AsyncClient:
    """回傳 mock transport 的 AsyncClient。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        if isinstance(body, str):
            return httpx.Response(status, content=body.encode())
        return httpx.Response(status, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


VALID_CHART_JSON = json.dumps(
    {
        "chart_type": "line",
        "title": "US M2 Supply YoY",
        "x_axis_unit": "month",
        "y_axis_unit": "percent",
        "latest_data_point": {"x_label": "2025-01", "y_value": 2.1, "annotation": ""},
        "historical_peak": {"x_label": "2021-02", "y_value": 27.0, "annotation": "peak"},
        "data_points": [
            {"x_label": "2024-01", "y_value": -0.5, "annotation": ""},
            {"x_label": "2024-06", "y_value": 0.8, "annotation": ""},
            {"x_label": "2025-01", "y_value": 2.1, "annotation": ""},
        ],
        "trend_direction": "up",
        "trend_description": "M2 年增率自低點回升",
        "confidence": 0.85,
        "raw_description": "Line chart showing M2 YoY change recovering from negative.",
    }
)


# ── Unit Tests: Parsing ──────────────────────────────────────────────────


class TestParseDataPoint:
    """ChartDataPoint 解析的邊界情況。"""

    def test_valid_data_point(self) -> None:
        raw = {"x_label": "2024-01", "y_value": 1.5, "annotation": "test"}
        result = _parse_data_point(raw)
        assert result == ChartDataPoint(x_label="2024-01", y_value=1.5, annotation="test")

    def test_null_y_value_is_preserved(self) -> None:
        raw = {"x_label": "2024-01", "y_value": None, "annotation": ""}
        result = _parse_data_point(raw)
        assert result is not None
        assert result.y_value is None

    def test_non_dict_returns_none(self) -> None:
        assert _parse_data_point("not a dict") is None
        assert _parse_data_point(None) is None
        assert _parse_data_point(42) is None

    def test_invalid_y_value_becomes_none(self) -> None:
        raw = {"x_label": "2024-01", "y_value": "not_a_number", "annotation": ""}
        result = _parse_data_point(raw)
        assert result is not None
        assert result.y_value is None


class TestParseChartAnalysis:
    """ChartAnalysis 解析容許部分缺失。"""

    def test_full_valid_json(self) -> None:
        raw = json.loads(VALID_CHART_JSON)
        result = _parse_chart_analysis(raw)
        assert result.chart_type == "line"
        assert result.title == "US M2 Supply YoY"
        assert result.confidence == 0.85
        assert result.trend_direction == "up"
        assert len(result.data_points) == 3

    def test_non_dict_returns_empty_analysis(self) -> None:
        result = _parse_chart_analysis("garbage")
        assert result.confidence == 0.0

    def test_invalid_chart_type_defaults_to_line(self) -> None:
        raw = json.loads(VALID_CHART_JSON)
        raw["chart_type"] = "unknown_type"
        result = _parse_chart_analysis(raw)
        assert result.chart_type == "line"

    def test_invalid_trend_defaults_to_unclear(self) -> None:
        raw = json.loads(VALID_CHART_JSON)
        raw["trend_direction"] = "bearish"
        result = _parse_chart_analysis(raw)
        assert result.trend_direction == "unclear"

    def test_confidence_clamped_to_zero_one(self) -> None:
        raw = json.loads(VALID_CHART_JSON)
        raw["confidence"] = 1.5
        result = _parse_chart_analysis(raw)
        assert result.confidence == 1.0

        raw["confidence"] = -0.3
        result = _parse_chart_analysis(raw)
        assert result.confidence == 0.0

    def test_missing_data_points_gives_empty_tuple(self) -> None:
        raw = json.loads(VALID_CHART_JSON)
        del raw["data_points"]
        result = _parse_chart_analysis(raw)
        assert result.data_points == ()


# ── Integration Tests: VisionModelAdapter ────────────────────────────────


class TestExtractChartData:
    """VisionModelAdapter.extract_chart_data 的行為。"""

    @pytest.mark.asyncio
    async def test_successful_extraction_returns_chart_analysis(self) -> None:
        client = responding(gemini_reply(VALID_CHART_JSON))
        adapter = VisionModelAdapter(client, "fake-key")

        result = await adapter.extract_chart_data("base64image")

        assert isinstance(result, ChartAnalysis)
        assert result.chart_type == "line"
        assert result.confidence == 0.85
        assert result.trend_direction == "up"
        assert len(result.data_points) == 3

    @pytest.mark.asyncio
    async def test_network_error_degrades_gracefully(self) -> None:
        """網路錯誤 → confidence=0 空結果，不拋例外。"""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = VisionModelAdapter(client, "fake-key")

        result = await adapter.extract_chart_data("base64image")

        assert result.confidence == 0.0
        assert result.trend_direction == "unclear"

    @pytest.mark.asyncio
    async def test_non_200_degrades_gracefully(self) -> None:
        """非 200 → 降級。"""
        client = responding({"error": "rate limited"}, status=429)
        adapter = VisionModelAdapter(client, "fake-key")

        result = await adapter.extract_chart_data("base64image")

        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_malformed_json_degrades_gracefully(self) -> None:
        """模型回傳非 JSON → 降級。"""
        client = responding(gemini_reply("this is not json at all"))
        adapter = VisionModelAdapter(client, "fake-key")

        result = await adapter.extract_chart_data("base64image")

        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_empty_candidates_degrades_gracefully(self) -> None:
        """空 candidates → 降級。"""
        client = responding({"candidates": []})
        adapter = VisionModelAdapter(client, "fake-key")

        result = await adapter.extract_chart_data("base64image")

        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_hints_included_in_request(self) -> None:
        """chart_type_hint 和 context_hint 被帶入請求。"""
        captured: list[httpx.Request] = []
        client = responding(gemini_reply(VALID_CHART_JSON), capture=captured)
        adapter = VisionModelAdapter(client, "fake-key")

        await adapter.extract_chart_data(
            "base64image",
            chart_type_hint="candlestick",
            context_hint="BTC 30 天 K 線",
        )

        assert len(captured) == 1
        body = json.loads(captured[0].content)
        user_parts = body["contents"][0]["parts"]
        # 應有 inlineData 和 text 兩部分
        assert any("inlineData" in p for p in user_parts)
        text_part = next(p["text"] for p in user_parts if "text" in p)
        assert "candlestick" in text_part
        assert "BTC 30 天 K 線" in text_part

    @pytest.mark.asyncio
    async def test_request_uses_json_mode(self) -> None:
        """請求帶有 responseMimeType: application/json。"""
        captured: list[httpx.Request] = []
        client = responding(gemini_reply(VALID_CHART_JSON), capture=captured)
        adapter = VisionModelAdapter(client, "fake-key")

        await adapter.extract_chart_data("base64image")

        body = json.loads(captured[0].content)
        gen_config = body["generationConfig"]
        assert gen_config["responseMimeType"] == "application/json"

    @pytest.mark.asyncio
    async def test_request_includes_inline_data(self) -> None:
        """圖片以 inlineData 方式傳入。"""
        captured: list[httpx.Request] = []
        client = responding(gemini_reply(VALID_CHART_JSON), capture=captured)
        adapter = VisionModelAdapter(client, "fake-key")

        await adapter.extract_chart_data("SGVsbG8=")

        body = json.loads(captured[0].content)
        inline_part = next(
            p for p in body["contents"][0]["parts"] if "inlineData" in p
        )
        assert inline_part["inlineData"]["mimeType"] == "image/png"
        assert inline_part["inlineData"]["data"] == "SGVsbG8="

    @pytest.mark.asyncio
    async def test_api_key_in_query_params_not_body(self) -> None:
        """金鑰只出現在 query params，不在 body 中。"""
        captured: list[httpx.Request] = []
        client = responding(gemini_reply(VALID_CHART_JSON), capture=captured)
        adapter = VisionModelAdapter(client, "secret-key-123")

        await adapter.extract_chart_data("base64image")

        request = captured[0]
        assert "key=secret-key-123" in str(request.url)
        body_text = request.content.decode()
        assert "secret-key-123" not in body_text


class TestFromEnvironment:
    """from_environment classmethod 的行為。"""

    def test_returns_none_without_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(API_KEY_ENV, raising=False)
        client = httpx.AsyncClient()
        result = VisionModelAdapter.from_environment(client)
        assert result is None

    def test_returns_adapter_with_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(API_KEY_ENV, "test-key-456")
        client = httpx.AsyncClient()
        result = VisionModelAdapter.from_environment(client)
        assert result is not None
        assert isinstance(result, VisionModelAdapter)

    def test_strips_whitespace_from_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(API_KEY_ENV, "  ")
        client = httpx.AsyncClient()
        result = VisionModelAdapter.from_environment(client)
        assert result is None


class TestDataclassImmutability:
    """Frozen dataclass 不可修改。"""

    def test_chart_data_point_is_frozen(self) -> None:
        dp = ChartDataPoint(x_label="2024-01", y_value=1.0)
        with pytest.raises(AttributeError):
            dp.x_label = "changed"  # type: ignore[misc]

    def test_chart_analysis_is_frozen(self) -> None:
        analysis = _empty_analysis()
        with pytest.raises(AttributeError):
            analysis.confidence = 0.9  # type: ignore[misc]
