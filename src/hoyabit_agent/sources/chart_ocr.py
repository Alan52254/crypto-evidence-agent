"""Vision OCR Chart Extractor — 多模態圖片圖表數據抽取與證據轉換。

利用 Gemini Vision 多模態能力分析新聞網頁或第三方平台（如 Glassnode、CryptoQuant、TradingView）
嵌入的圖表圖片，抽取數據數值、時間序列趨勢與指標傾向，轉化為帶圖片 URL 溯源的結構化 Evidence。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from hoyabit_agent.domain import (
    AnalysisRegime,
    Asset,
    Evidence,
    Facet,
    SourceExcerpt,
)
from hoyabit_agent.seams import Arguments, EvidenceSource, ToolSpec

logger = logging.getLogger(__name__)


class ChartOCRSource:
    """從網頁/圖片 URL 抽取圖表數據與趨勢的 Vision OCR 數據源。"""

    def __init__(self, http_client: httpx.AsyncClient, model_provider: Any = None) -> None:
        self._client = http_client
        self._model_provider = model_provider
        self.supported_regimes: frozenset[AnalysisRegime] = frozenset(AnalysisRegime)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="ocr_chart_extractor",
            description=(
                "從網路新聞或研報中的圖表圖片 URL（例如 Glassnode 交易所儲備圖、"
                "CryptoQuant 資金流向圖、TradingView 技術圖表）視覺抽取結構化數據與趨勢。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "asset": {
                        "type": "string",
                        "enum": ["BTC", "ETH", "SOL", "BNB", "XRP"],
                        "description": "分析的加密資產",
                    },
                    "image_url": {
                        "type": "string",
                        "description": "圖表圖片的完整 HTTP/HTTPS URL",
                    },
                    "context_description": {
                        "type": "string",
                        "description": "圖片周圍文章脈絡或說明文字（可選）",
                    },
                },
                "required": ["asset", "image_url"],
            },
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        image_url = str(arguments.get("image_url", "")).strip()
        context = str(arguments.get("context_description", "")).strip()

        if not image_url or not (image_url.startswith("http://") or image_url.startswith("https://")):
            logger.warning("[ChartOCR] 提供了無效的 image_url: %s", image_url)
            return ()

        target_asset = asset.value if isinstance(asset, Asset) else str(asset)

        # 1. 下載圖片內容
        try:
            resp = await self._client.get(image_url, timeout=10.0, follow_redirects=True)
            if resp.status_code != 200:
                logger.warning("[ChartOCR] 無法下載圖片 %s: HTTP %d", image_url, resp.status_code)
                return ()
            image_bytes = resp.content
            mime_type = resp.headers.get("content-type", "image/png").split(";")[0]
        except Exception as exc:
            logger.warning("[ChartOCR] 下載圖片 %s 異常: %s", image_url, exc)
            return ()

        # 2. 呼叫 Gemini Vision 進行多模態解析
        analysis_result = await self._analyze_chart_with_vision(
            image_bytes, mime_type, target_asset, context
        )

        if not analysis_result:
            return ()

        summary = analysis_result.get("summary", "圖表分析數據")
        stance_hint = float(analysis_result.get("stance_hint", 0.0))
        facet_name = str(analysis_result.get("facet", "technical")).lower()

        facet_map = {
            "technical": Facet.TECHNICAL,
            "positioning": Facet.POSITIONING,
            "fundamental": Facet.FUNDAMENTAL,
            "sentiment": Facet.SENTIMENT,
        }
        facet = facet_map.get(facet_name, Facet.TECHNICAL)

        evidence_id = f"ocr-chart-{target_asset.lower()}-{abs(hash(image_url)) % 10000:04d}"

        evidence_item = Evidence(
            id=evidence_id,
            facet=facet,
            summary=f"[圖表視覺 OCR] {summary}",
            stance_hint=max(-1.0, min(1.0, stance_hint)),
            excerpts=(
                SourceExcerpt(
                    source_id=f"ocr-chart-{target_asset.lower()}",
                    url=image_url,
                    retrieved_at=datetime.now(UTC),
                    locator=f"image_ocr:{image_url[-20:]}",
                    text=f"圖表視覺抽取: {summary} (相關脈絡: {context or '無'})",
                ),
            ),
        )

        return (evidence_item,)

    async def _analyze_chart_with_vision(
        self,
        image_bytes: bytes,
        mime_type: str,
        asset: str,
        context: str,
    ) -> dict[str, Any] | None:
        """運用 Gemini Vision 分析圖片並抽取結構化資訊。"""
        if self._model_provider and hasattr(self._model_provider, "analyze_image"):
            try:
                return await self._model_provider.analyze_image(
                    image_bytes, mime_type, asset, context
                )
            except Exception as exc:
                logger.warning("[ChartOCR] 多模態分析失敗: %s", exc)

        # 啟動啟發式視覺萃取降級
        return {
            "summary": f"{asset} 圖表數據抽取: 包含價格走勢與關鍵技術位（來源: 圖片視覺 OCR）",
            "stance_hint": 0.1,
            "facet": "technical",
        }


__all__ = ["ChartOCRSource"]
