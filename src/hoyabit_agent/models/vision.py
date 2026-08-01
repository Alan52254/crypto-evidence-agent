"""Gemini 多模態圖表解析 —— VisionModelAdapter。

利用 Gemini 2.5 Flash 的 inline_data image input 將圖片轉為結構化
ChartAnalysis。這不是 MCP Tool，而是被 chart_reader MCP Tool 呼叫的
內部模組（models 層）。

設計原則（同 gemini.py）：
* 直接打 REST，不引入 SDK —— 可用 MockTransport 完整測試。
* 失敗以降級表達，不以例外中斷。回傳低 confidence 的空 ChartAnalysis。
* JSON mode（responseMimeType: application/json）強制結構化輸出。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_VISION_MODEL = "gemini-2.5-flash"
API_KEY_ENV = "GEMINI_API_KEY"
DEFAULT_TIMEOUT_SECONDS = 30.0

# -- System prompt -------------------------------------------------------

_VISION_SYSTEM = """\
你是一個專門處理金融與統計圖表的視覺數據提取器（Chart-to-JSON OCR）。

請仔細分析傳入的圖表圖片，並嚴格按照指定 JSON schema 輸出結構化結果。

【提取要求】

1. 圖表基本資訊：
   - chart_type（折線圖=line / K線圖=candlestick / 柱狀圖=bar / 點陣圖=dot_plot / 表格=table / 熱力圖=heatmap）
   - title（圖表標題）
   - x_axis_unit（X 軸名稱、時間範圍或類別）
   - y_axis_unit（Y 軸名稱、單位與數值區間）

2. 精確數據提取：
   - latest_data_point：最新/最右側數據點（日期 + 具體數值）
   - historical_peak：關鍵極值（最高點及其對應時間）
   - data_points：按 x 軸順序排列的可辨識數據點

3. 趨勢描述：
   - trend_direction：up / down / sideways / unclear
   - trend_description：用 2-3 句話總結圖表展現的整體趨勢

4. confidence 自評：
   - >= 0.8：數據點清晰可辨，數值精確
   - 0.5-0.8：部分數值模糊但趨勢可判
   - 0.3-0.5：僅能判斷大致趨勢方向
   - < 0.3：幾乎無法辨識

⚠️ 嚴格規則：
- 若數值無法 100% 精確識別，在 annotation 中標註「估計值」並給出合理區間。
- 看不清的數值寫 null，嚴禁憑空捏造圖表中未顯示的數據。
- 趨勢方向不明確時 trend_direction 寫 "unclear"。
- 嚴格符合指定的 JSON schema，不多不少。
"""

# -- JSON schema for Gemini structured output ----------------------------

_CHART_DATA_POINT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "x_label": {"type": "string"},
        "y_value": {"type": "number", "nullable": True},
        "annotation": {"type": "string"},
    },
    "required": ["x_label", "y_value", "annotation"],
}

_CHART_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "chart_type": {
            "type": "string",
            "enum": ["line", "candlestick", "bar", "dot_plot", "table", "heatmap"],
        },
        "title": {"type": "string"},
        "x_axis_unit": {"type": "string"},
        "y_axis_unit": {"type": "string"},
        "latest_data_point": {**_CHART_DATA_POINT_SCHEMA, "nullable": True},
        "historical_peak": {**_CHART_DATA_POINT_SCHEMA, "nullable": True},
        "data_points": {"type": "array", "items": _CHART_DATA_POINT_SCHEMA},
        "trend_direction": {
            "type": "string",
            "enum": ["up", "down", "sideways", "unclear"],
        },
        "trend_description": {"type": "string"},
        "confidence": {"type": "number"},
        "raw_description": {"type": "string"},
    },
    "required": [
        "chart_type",
        "title",
        "x_axis_unit",
        "y_axis_unit",
        "latest_data_point",
        "historical_peak",
        "data_points",
        "trend_direction",
        "trend_description",
        "confidence",
        "raw_description",
    ],
}


# -- Dataclasses ---------------------------------------------------------


@dataclass(frozen=True)
class ChartDataPoint:
    """圖表上的單一數據點。"""

    x_label: str
    y_value: float | None
    annotation: str = ""


@dataclass(frozen=True)
class ChartAnalysis:
    """結構化的圖表解析結果。"""

    chart_type: str  # line / candlestick / bar / dot_plot / table / heatmap
    title: str
    x_axis_unit: str
    y_axis_unit: str
    latest_data_point: ChartDataPoint | None
    historical_peak: ChartDataPoint | None
    data_points: tuple[ChartDataPoint, ...] = field(default_factory=tuple)
    trend_direction: str = "unclear"  # up / down / sideways / unclear
    trend_description: str = ""
    confidence: float = 0.0
    raw_description: str = ""


# -- Adapter -------------------------------------------------------------


def _empty_analysis() -> ChartAnalysis:
    """降級用的空結果 —— confidence=0 代表幾乎無法辨識。"""
    return ChartAnalysis(
        chart_type="line",
        title="",
        x_axis_unit="",
        y_axis_unit="",
        latest_data_point=None,
        historical_peak=None,
        data_points=(),
        trend_direction="unclear",
        trend_description="",
        confidence=0.0,
        raw_description="（解析失敗或模型無法回應）",
    )


def _parse_data_point(raw: Any) -> ChartDataPoint | None:
    """安全地從 JSON dict 建構 ChartDataPoint。"""
    if not isinstance(raw, dict):
        return None
    x_label = str(raw.get("x_label", ""))
    y_value_raw = raw.get("y_value")
    y_value: float | None = None
    if y_value_raw is not None:
        try:
            y_value = float(y_value_raw)
        except (TypeError, ValueError):
            y_value = None
    annotation = str(raw.get("annotation", ""))
    return ChartDataPoint(x_label=x_label, y_value=y_value, annotation=annotation)


def _parse_chart_analysis(raw: Any) -> ChartAnalysis:
    """從 JSON dict 建構 ChartAnalysis，容許部分欄位缺失。"""
    if not isinstance(raw, dict):
        return _empty_analysis()

    data_points_raw = raw.get("data_points")
    data_points: tuple[ChartDataPoint, ...] = ()
    if isinstance(data_points_raw, list):
        parsed = [_parse_data_point(dp) for dp in data_points_raw]
        data_points = tuple(dp for dp in parsed if dp is not None)

    latest = _parse_data_point(raw.get("latest_data_point"))
    peak = _parse_data_point(raw.get("historical_peak"))

    confidence_raw = raw.get("confidence", 0.0)
    try:
        confidence = max(0.0, min(1.0, float(confidence_raw)))
    except (TypeError, ValueError):
        confidence = 0.0

    chart_type = str(raw.get("chart_type", "line"))
    valid_chart_types = {"line", "candlestick", "bar", "dot_plot", "table", "heatmap"}
    if chart_type not in valid_chart_types:
        chart_type = "line"

    trend_direction = str(raw.get("trend_direction", "unclear"))
    valid_trends = {"up", "down", "sideways", "unclear"}
    if trend_direction not in valid_trends:
        trend_direction = "unclear"

    return ChartAnalysis(
        chart_type=chart_type,
        title=str(raw.get("title", "")),
        x_axis_unit=str(raw.get("x_axis_unit", "")),
        y_axis_unit=str(raw.get("y_axis_unit", "")),
        latest_data_point=latest,
        historical_peak=peak,
        data_points=data_points,
        trend_direction=trend_direction,
        trend_description=str(raw.get("trend_description", "")),
        confidence=confidence,
        raw_description=str(raw.get("raw_description", "")),
    )


class VisionModelAdapter:
    """Gemini 多模態圖表解析配接器。

    不變式：
    * 失敗以降級表達 —— 回傳 confidence=0 的空 ChartAnalysis，不拋例外。
    * 無狀態，同一實例可服務多次呼叫。
    * API key 只出現在查詢參數，不寫進回傳值。
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        model: str = DEFAULT_VISION_MODEL,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls, client: httpx.AsyncClient) -> VisionModelAdapter | None:
        """依環境變數建構。沒有金鑰時回傳 None —— 呼叫端據此降級。"""
        api_key = os.environ.get(API_KEY_ENV, "").strip()
        if not api_key:
            return None
        return cls(client, api_key)

    async def extract_chart_data(
        self,
        image_base64: str,
        *,
        chart_type_hint: str | None = None,
        context_hint: str | None = None,
    ) -> ChartAnalysis:
        """從 base64 圖片擷取結構化圖表數據。

        Parameters
        ----------
        image_base64 : str
            圖片的 base64 編碼字串（不含 data:image/... 前綴）。
        chart_type_hint : str | None
            提示模型此圖表的類型（例如 "candlestick"），可輔助解析。
        context_hint : str | None
            額外語境（例如 "這是 BTC 近 30 天的 K 線圖"）。

        Returns
        -------
        ChartAnalysis
            結構化解析結果。失敗時回傳 confidence=0 的空結果。
        """
        user_text = self._build_user_prompt(chart_type_hint, context_hint)

        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": _VISION_SYSTEM}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "image/png",
                                "data": image_base64,
                            }
                        },
                        {"text": user_text},
                    ],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": _CHART_ANALYSIS_SCHEMA,
            },
        }

        body = await self._post(payload)
        if body is None:
            return _empty_analysis()

        text = self._extract_text(body)
        if text is None:
            return _empty_analysis()

        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return _empty_analysis()

        return _parse_chart_analysis(parsed)

    # -- 內部方法 ---------------------------------------------------------

    def _build_user_prompt(
        self,
        chart_type_hint: str | None,
        context_hint: str | None,
    ) -> str:
        """組合使用者提示詞。"""
        parts: list[str] = ["請解析這張圖表並回傳結構化數據。"]
        if chart_type_hint:
            parts.append(f"圖表類型提示：{chart_type_hint}")
        if context_hint:
            parts.append(f"額外語境：{context_hint}")
        parts.append("嚴格按照 JSON schema 回傳，看不清的數值寫 null。")
        return "\n".join(parts)

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """POST to Gemini REST API。失敗回傳 None，不拋例外。"""
        url = f"{BASE_URL}/models/{self._model}:generateContent"
        try:
            response = await self._client.post(
                url,
                params={"key": self._api_key},
                json=payload,
                timeout=self._timeout_seconds,
            )
        except (httpx.HTTPError, TimeoutError, OSError):
            return None

        if response.status_code != 200:
            return None

        try:
            body = response.json()
        except ValueError:
            return None

        return body if isinstance(body, dict) else None

    @staticmethod
    def _extract_text(body: dict[str, Any]) -> str | None:
        """從 Gemini 回應中取出第一個 text part。"""
        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return None
        first = candidates[0]
        if not isinstance(first, dict):
            return None
        content = first.get("content")
        if not isinstance(content, dict):
            return None
        parts = content.get("parts")
        if not isinstance(parts, list):
            return None
        for part in parts:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    return text
        return None


__all__ = [
    "API_KEY_ENV",
    "BASE_URL",
    "DEFAULT_VISION_MODEL",
    "ChartAnalysis",
    "ChartDataPoint",
    "VisionModelAdapter",
]
