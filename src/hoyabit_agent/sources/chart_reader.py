"""圖表解析 MCP Tool —— 接收圖片，呼叫 VisionModelAdapter，產出 Evidence。

外部 I/O（下載圖片 + 呼叫 Gemini 模型）→ MCP Tool（規則 7）。
失效以空集合表達，不以例外表達（規則 5）。
"""

from __future__ import annotations

import base64
import io
from datetime import UTC, datetime
from typing import Any

import httpx

from hoyabit_agent.domain import AnalysisRegime, Asset, Evidence, Facet, SourceExcerpt
from hoyabit_agent.models.vision import ChartAnalysis, VisionModelAdapter
from hoyabit_agent.seams import Arguments, ToolSpec


def _infer_facet(context_hint: str | None) -> Facet:
    """由 context_hint 推斷證據面。"""
    if not context_hint:
        return Facet.FUNDAMENTAL
    hint_lower = context_hint.lower()
    if "funding" in hint_lower or "oi" in hint_lower:
        return Facet.POSITIONING
    if "m2" in hint_lower or "cpi" in hint_lower or "gdp" in hint_lower:
        return Facet.FUNDAMENTAL
    if "rsi" in hint_lower or "ema" in hint_lower:
        return Facet.TECHNICAL
    return Facet.FUNDAMENTAL


def trend_to_stance(trend_direction: str, confidence: float) -> float:
    """將趨勢方向和信心度轉為 stance_hint 數值。

    up → +0.4 * confidence
    down → -0.4 * confidence
    sideways/unclear → 0.0
    """
    if trend_direction == "up":
        return 0.4 * confidence
    if trend_direction == "down":
        return -0.4 * confidence
    return 0.0


def _resize_image_bytes(image_bytes: bytes, max_width: int = 1024) -> bytes:
    """壓縮圖片至 max_width 像素寬。如果 Pillow 不可用或解析失敗則原樣回傳。"""
    try:
        from PIL import Image  # noqa: F401 — optional dependency
    except ImportError:
        return image_bytes

    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.width <= max_width:
            return image_bytes

        ratio = max_width / img.width
        new_height = int(img.height * ratio)
        img = img.resize((max_width, new_height), Image.LANCZOS)

        buf = io.BytesIO()
        fmt = img.format or "PNG"
        img.save(buf, format=fmt)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 — 圖片格式無法辨識時跳過壓縮
        return image_bytes


class ChartReaderSource:
    """圖表解析證據源 —— 將圖片經 VisionModelAdapter 轉為結構化 Evidence。

    不變式：
    * 失效（下載失敗、解析失敗）以空 tuple 表達，不拋例外。
    * 只支援 LIVE 模式 —— 圖片是「當下」使用者提供的，無法以截止日限定。
    """

    supported_regimes: frozenset[AnalysisRegime] = frozenset({AnalysisRegime.LIVE})

    def __init__(
        self,
        client: httpx.AsyncClient,
        vision_adapter: VisionModelAdapter,
    ) -> None:
        self._client = client
        self._vision_adapter = vision_adapter

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="chart_reader",
            description=(
                "解析圖表圖片（URL 或本地路徑），產出結構化的技術面或基本面證據。"
                "圖片會經 Gemini Vision 模型辨識趨勢方向與數據點。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "image_url": {
                        "type": "string",
                        "description": "圖片 URL（http/https）或本地檔案路徑。",
                    },
                    "chart_type": {
                        "type": "string",
                        "enum": [
                            "line",
                            "candlestick",
                            "bar",
                            "dot_plot",
                            "table",
                            "heatmap",
                        ],
                        "description": "提示模型此圖表的類型，可輔助解析精度。",
                    },
                    "context_hint": {
                        "type": "string",
                        "description": "額外語境（例如 'BTC funding rate chart'），用於推斷證據面。",
                    },
                },
                "required": ["image_url"],
            },
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        """解析圖片並產出 Evidence。失敗回傳空 tuple。"""
        image_url: str = str(arguments.get("image_url", ""))
        if not image_url:
            return ()

        chart_type: str | None = arguments.get("chart_type")  # type: ignore[assignment]
        context_hint: str | None = arguments.get("context_hint")  # type: ignore[assignment]

        # 1. 載入圖片
        image_bytes = await self._load_image(image_url)
        if image_bytes is None:
            return ()

        # 2. 壓縮至 1024px 寬
        image_bytes = _resize_image_bytes(image_bytes)

        # 3. 轉 base64
        image_base64 = base64.b64encode(image_bytes).decode("ascii")

        # 4. 呼叫 vision adapter
        try:
            chart_analysis: ChartAnalysis = await self._vision_adapter.extract_chart_data(
                image_base64,
                chart_type_hint=chart_type,
                context_hint=context_hint,
            )
        except Exception:  # noqa: BLE001 — 規則 5：不以例外表達
            return ()

        # 5. 依 confidence 決定是否產出 Evidence
        confidence = chart_analysis.confidence
        if confidence < 0.3:
            return ()

        # 決定 summary 前綴 — 必須含「資料來源【圖】」標記
        if confidence < 0.5:
            prefix = "⚠️ 從資料來源【圖】中得知（模糊，僅趨勢判斷）："
        elif confidence < 0.8:
            prefix = "從資料來源【圖】中得知："
        else:
            prefix = "從資料來源【圖】中得知："

        summary = f"{prefix}{chart_analysis.trend_description}"

        # 6. 建構 Evidence
        evidence_id = f"CHART-{hash(image_url) & 0xFFFFFFFF:08x}"
        facet = _infer_facet(context_hint)
        stance_hint = trend_to_stance(chart_analysis.trend_direction, confidence)

        excerpt = SourceExcerpt(
            source_id=evidence_id,
            url=image_url,
            retrieved_at=datetime.now(UTC),
            locator=f"visual chart analysis (confidence: {confidence:.0%})",
            text=chart_analysis.raw_description,
        )

        return (
            Evidence(
                id=evidence_id,
                facet=facet,
                summary=summary,
                stance_hint=stance_hint,
                excerpts=(excerpt,),
            ),
        )

    async def _load_image(self, image_url: str) -> bytes | None:
        """載入圖片。URL 用 HTTP 下載，否則當本地路徑讀檔。失敗回傳 None。"""
        if image_url.startswith(("http://", "https://")):
            try:
                response = await self._client.get(image_url)
                if response.status_code != 200:
                    return None
                return response.content
            except (httpx.HTTPError, OSError):
                return None
        else:
            try:
                with open(image_url, "rb") as f:
                    return f.read()
            except (OSError, IOError):
                return None


__all__ = ["ChartReaderSource", "trend_to_stance"]
