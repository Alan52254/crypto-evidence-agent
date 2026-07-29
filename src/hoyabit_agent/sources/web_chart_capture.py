"""網頁圖表截圖 MCP Tool —— 用 Playwright headless browser 截取預定義頁面的圖表。

外部 I/O（瀏覽器截圖 + Gemini Vision 模型解析）→ MCP Tool（規則 7）。
失效以空集合表達，不以例外表達（規則 5）。

Playwright 是 optional dependency —— import 失敗時 graceful degradation，
回傳空 tuple 而非 crash。
"""

from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from hoyabit_agent.domain import AnalysisRegime, Asset, Evidence, Facet, SourceExcerpt
from hoyabit_agent.models.vision import ChartAnalysis, VisionModelAdapter
from hoyabit_agent.seams import Arguments, ToolSpec
from hoyabit_agent.sources.chart_reader import _infer_facet, trend_to_stance


# ── ScrapedChart dataclass ────────────────────────────────────────────────


@dataclass(frozen=True)
class ScrapedChart:
    """從新聞頁面自動擷取的圖表截圖。"""

    path: str  # /tmp/scraped_charts/xxx.png
    source_id: str  # 來源新聞的 source_id
    url: str  # 原始頁面 URL
    title: str = ""  # 新聞標題


# 圖表元素偵測用的 CSS selectors
CHART_ELEMENT_SELECTORS: tuple[str, ...] = (
    "img[src*='chart']",
    "img[src*='graph']",
    "canvas",
    "svg.chart",
    ".chart-container",
    ".highcharts-container",
    "[data-chart]",
    ".tradingview-widget-container",
)

SCRAPED_CHARTS_DIR = Path("/tmp/scraped_charts")


# ── ChartSource dataclass ────────────────────────────────────────────────


@dataclass(frozen=True)
class ChartSource:
    """預定義圖表來源的描述。"""

    url: str
    selector: str
    description: str
    facet: Facet
    wait_ms: int = 3000


# ── CHART_REGISTRY ───────────────────────────────────────────────────────

CHART_REGISTRY: dict[str, ChartSource] = {
    # 宏觀經濟
    "us_m2_supply": ChartSource(
        url="https://fred.stlouisfed.org/series/M2SL",
        selector="#chart-container",
        description="US M2 Money Supply（美國 M2 貨幣供給）",
        facet=Facet.FUNDAMENTAL,
    ),
    "us_fed_funds_rate": ChartSource(
        url="https://fred.stlouisfed.org/series/FEDFUNDS",
        selector="#chart-container",
        description="US Federal Funds Rate（美國聯邦基金利率）",
        facet=Facet.FUNDAMENTAL,
    ),
    "us_cpi_yoy": ChartSource(
        url="https://fred.stlouisfed.org/series/CPIAUCSL",
        selector="#chart-container",
        description="US CPI Year-over-Year（美國 CPI 年增率）",
        facet=Facet.FUNDAMENTAL,
    ),
    "dxy_index": ChartSource(
        url="https://www.tradingview.com/chart/?symbol=TVC:DXY",
        selector=".chart-markup-table",
        description="DXY US Dollar Index（美元指數）",
        facet=Facet.FUNDAMENTAL,
    ),
    # 鏈上/交易所
    "btc_exchange_reserve": ChartSource(
        url="https://www.coinglass.com/Balance",
        selector=".chart-container",
        description="BTC Exchange Reserve（BTC 交易所儲備量）",
        facet=Facet.POSITIONING,
    ),
    "btc_whale_addresses_1k": ChartSource(
        url="https://www.coinglass.com/whale",
        selector=".chart-container",
        description="Bitcoin Whale Addresses (1,000+ BTC) — 持有千枚以上 BTC 的巨鯨地址數量與餘額變化",
        facet=Facet.POSITIONING,
    ),
    "btc_exchange_netflow": ChartSource(
        url="https://www.coinglass.com/flow",
        selector=".chart-container",
        description="BTC Exchange Netflow — 交易所比特幣淨流入/流出（正值=流入=潛在賣壓，負值=流出=累積）",
        facet=Facet.POSITIONING,
    ),
    "btc_whale_ratio": ChartSource(
        url="https://www.coinglass.com/whale",
        selector=".whale-ratio-chart",
        description="Exchange Whale Ratio — 交易所入金中巨鯨佔比（高值=巨鯨主導交易所流入=潛在拋壓）",
        facet=Facet.POSITIONING,
    ),
    "btc_etf_flow": ChartSource(
        url="https://www.coinglass.com/bitcoin-etf",
        selector=".etf-chart",
        description="BTC ETF Flow（BTC ETF 資金流向）",
        facet=Facet.POSITIONING,
    ),
    "eth_etf_flow": ChartSource(
        url="https://www.coinglass.com/ethereum-etf",
        selector=".etf-chart",
        description="ETH ETF Flow（ETH ETF 資金流向）",
        facet=Facet.POSITIONING,
    ),
    # 衍生品
    "btc_funding_rate": ChartSource(
        url="https://www.coinglass.com/FundingRate",
        selector=".funding-chart",
        description="BTC Funding Rate（BTC 資金費率）",
        facet=Facet.POSITIONING,
    ),
    "btc_open_interest": ChartSource(
        url="https://www.coinglass.com/BitcoinOpenInterest",
        selector=".oi-chart",
        description="BTC Open Interest（BTC 未平倉合約）",
        facet=Facet.POSITIONING,
    ),
    "liquidation_heatmap": ChartSource(
        url="https://www.coinglass.com/LiquidationData",
        selector=".heatmap-container",
        description="Liquidation Heatmap（清算熱力圖）",
        facet=Facet.POSITIONING,
    ),
    # 鏈上活動
    "eth_gas_burned": ChartSource(
        url="https://ultrasound.money/",
        selector=".burn-chart",
        description="ETH Gas Burned（ETH 燃燒量）",
        facet=Facet.FUNDAMENTAL,
    ),
    "defi_tvl_overview": ChartSource(
        url="https://defillama.com/",
        selector=".chart-wrapper",
        description="DeFi TVL Overview（DeFi 總鎖倉量）",
        facet=Facet.FUNDAMENTAL,
    ),
}


# ── 彈窗關閉用的 selectors ───────────────────────────────────────────────

_DISMISS_SELECTORS: tuple[str, ...] = (
    'button:has-text("Accept")',
    'button:has-text("Close")',
    'button:has-text("Got it")',
    ".cookie-banner button",
    '[aria-label="Close"]',
)

# ── 超時常數 ─────────────────────────────────────────────────────────────

_GOTO_TIMEOUT_MS = 10_000
_OVERALL_TIMEOUT_SECONDS = 15.0


# ── WebChartCaptureSource ────────────────────────────────────────────────


class WebChartCaptureSource:
    """網頁圖表截圖證據源 —— Playwright headless 截圖後經 Vision 解析產出 Evidence。

    不變式：
    * 失效（Playwright import 失敗、截圖失敗、Vision 解析失敗）以空 tuple 表達。
    * 只支援 LIVE 模式 —— 截圖是「當下」頁面狀態。
    * 每次 fetch 啟動並關閉 browser，不長時間佔資源。
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
            name="web_chart_capture",
            description=(
                "從預定義網頁截取圖表截圖並解析。支援宏觀經濟指標、"
                "鏈上數據、衍生品數據等 15 種預定義圖表來源。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "chart_id": {
                        "type": "string",
                        "enum": list(CHART_REGISTRY.keys()),
                        "description": "預定義圖表 ID。",
                    },
                    "custom_url": {
                        "type": "string",
                        "description": "自訂 URL，只在 chart_id 無法涵蓋時使用。",
                    },
                },
                "required": [],
            },
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        """截取網頁圖表並產出 Evidence。失敗回傳空 tuple。"""
        # 1. 解析 chart_source
        chart_source = self._resolve_chart_source(arguments)
        if chart_source is None:
            return ()

        # 2. 嘗試 import playwright
        try:
            from playwright.async_api import async_playwright  # noqa: F401
        except ImportError:
            return ()

        # 3. 整體 timeout 保護
        try:
            result = await asyncio.wait_for(
                self._capture_and_analyse(chart_source, asset),
                timeout=_OVERALL_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            return ()

        return result

    def _resolve_chart_source(self, arguments: Arguments) -> ChartSource | None:
        """從 arguments 解析出 ChartSource。找不到回傳 None。"""
        chart_id: str | None = arguments.get("chart_id")  # type: ignore[assignment]
        custom_url: str | None = arguments.get("custom_url")  # type: ignore[assignment]

        if chart_id and chart_id in CHART_REGISTRY:
            return CHART_REGISTRY[chart_id]

        if custom_url and isinstance(custom_url, str) and custom_url.startswith(("http://", "https://")):
            return ChartSource(
                url=custom_url,
                selector="body",
                description=f"Custom chart: {custom_url}",
                facet=Facet.FUNDAMENTAL,
            )

        # chart_id 不存在且無 custom_url → 回傳 None
        return None

    async def _capture_and_analyse(
        self,
        chart_source: ChartSource,
        asset: Asset,
    ) -> tuple[Evidence, ...]:
        """核心截圖 + 解析流程。由 fetch 在 timeout 保護下呼叫。"""
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-gpu"],
            )
            try:
                context = await browser.new_context(
                    device_scale_factor=2,
                    viewport={"width": 1280, "height": 800},
                )
                page = await context.new_page()

                # 導航
                await page.goto(
                    chart_source.url,
                    timeout=_GOTO_TIMEOUT_MS,
                    wait_until="domcontentloaded",
                )

                # 等待 JS 渲染
                await page.wait_for_timeout(chart_source.wait_ms)

                # 嘗試關閉彈窗
                await self._dismiss_popups(page)

                # 截圖
                screenshot_bytes = await self._take_screenshot(page, chart_source.selector)

            finally:
                await browser.close()

        # 轉 base64
        image_base64 = base64.b64encode(screenshot_bytes).decode("ascii")

        # 呼叫 Vision adapter 解析
        try:
            chart_analysis: ChartAnalysis = await self._vision_adapter.extract_chart_data(
                image_base64,
                context_hint=chart_source.description,
            )
        except Exception:  # noqa: BLE001
            return ()

        # confidence 門檻
        if chart_analysis.confidence < 0.3:
            return ()

        # 建構 Evidence
        return self._build_evidence(chart_source, chart_analysis)

    @staticmethod
    async def _dismiss_popups(page: Any) -> None:
        """嘗試關閉常見彈窗。每個 selector 獨立 try/except。"""
        for selector in _DISMISS_SELECTORS:
            try:
                btn = page.locator(selector).first
                await btn.click(timeout=1000)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    async def _take_screenshot(page: Any, selector: str) -> bytes:
        """嘗試元素截圖，失敗時 fallback 為全頁截圖。"""
        try:
            element = page.locator(selector).first
            # 確認元素存在
            await element.wait_for(timeout=3000)
            return await element.screenshot()
        except Exception:  # noqa: BLE001
            return await page.screenshot(full_page=True)

    def _build_evidence(
        self,
        chart_source: ChartSource,
        chart_analysis: ChartAnalysis,
    ) -> tuple[Evidence, ...]:
        """從 ChartAnalysis 建構 Evidence tuple。"""
        confidence = chart_analysis.confidence

        # 決定 summary 前綴 — 必須含「資料來源【圖】」標記
        if confidence < 0.5:
            prefix = "⚠️ 從資料來源【圖】中得知（模糊，僅趨勢判斷）："
        elif confidence < 0.8:
            prefix = "從資料來源【圖】中得知："
        else:
            prefix = "從資料來源【圖】中得知："

        summary = f"{prefix}{chart_analysis.trend_description}"

        evidence_id = f"WEBCHART-{hash(chart_source.url) & 0xFFFFFFFF:08x}"
        stance_hint = trend_to_stance(chart_analysis.trend_direction, confidence)

        excerpt = SourceExcerpt(
            source_id=evidence_id,
            url=chart_source.url,
            retrieved_at=datetime.now(UTC),
            locator=f"web chart capture: {chart_source.description} (confidence: {confidence:.0%})",
            text=chart_analysis.raw_description,
        )

        return (
            Evidence(
                id=evidence_id,
                facet=chart_source.facet,
                summary=summary,
                stance_hint=stance_hint,
                excerpts=(excerpt,),
            ),
        )


# ── scan_page_for_charts ─────────────────────────────────────────────────


async def scan_page_for_charts(
    page: Any,
    url: str,
    source_id: str,
    title: str = "",
) -> tuple[ScrapedChart, ...]:
    """掃描頁面中的圖表元素，逐一截圖並存入 /tmp/scraped_charts/。

    每頁最多擷取 3 張圖表。失敗靜默跳過（規則 5）。
    """
    SCRAPED_CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    found: list[ScrapedChart] = []

    for selector in CHART_ELEMENT_SELECTORS:
        if len(found) >= 3:
            break
        try:
            elements = await page.locator(selector).all()
        except Exception:
            continue

        for i, el in enumerate(elements):
            if len(found) >= 3:
                break
            try:
                screenshot = await el.screenshot(timeout=5000)
                timestamp = int(time.time() * 1000)
                filename = f"{source_id}_{len(found)}_{timestamp}.png"
                filepath = SCRAPED_CHARTS_DIR / filename
                filepath.write_bytes(screenshot)
                found.append(ScrapedChart(
                    path=str(filepath),
                    source_id=source_id,
                    url=url,
                    title=title,
                ))
            except Exception:
                continue

    return tuple(found)


__all__ = [
    "CHART_ELEMENT_SELECTORS",
    "CHART_REGISTRY",
    "ChartSource",
    "SCRAPED_CHARTS_DIR",
    "ScrapedChart",
    "WebChartCaptureSource",
    "scan_page_for_charts",
]
