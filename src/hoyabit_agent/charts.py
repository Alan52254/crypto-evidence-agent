"""報告圖表產生器 — 在分析報告中嵌入 K 線圖、價格走勢、技術指標。

使用 SVG 格式輸出，無需額外圖形庫依賴。
SVG 可直接嵌入 Markdown（前端渲染）或轉為 base64 data URI。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class OHLCV:
    """單根 K 線。"""
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class ChartData:
    """圖表所需的完整資料。"""
    asset: str
    candles: tuple[OHLCV, ...]
    rsi_values: tuple[float, ...] = ()
    sma_20: tuple[float, ...] = ()
    sma_50: tuple[float, ...] = ()


def generate_price_chart_svg(data: ChartData, width: int = 800, height: int = 400) -> str:
    """產生價格走勢折線圖 SVG。"""
    if not data.candles:
        return _empty_chart(data.asset, "價格走勢", width, height)

    prices = [c.close for c in data.candles]
    dates = [c.date for c in data.candles]
    min_p = min(prices) * 0.995
    max_p = max(prices) * 1.005
    price_range = max_p - min_p if max_p > min_p else 1

    margin = {"top": 40, "right": 60, "bottom": 50, "left": 70}
    chart_w = width - margin["left"] - margin["right"]
    chart_h = height - margin["top"] - margin["bottom"]

    def x(i: int) -> float:
        return margin["left"] + (i / max(len(prices) - 1, 1)) * chart_w

    def y(price: float) -> float:
        return margin["top"] + (1 - (price - min_p) / price_range) * chart_h

    # Build path
    points = " ".join(f"{x(i):.1f},{y(p):.1f}" for i, p in enumerate(prices))
    area_points = points + f" {x(len(prices)-1):.1f},{margin['top']+chart_h:.1f} {x(0):.1f},{margin['top']+chart_h:.1f}"

    # Color based on trend
    trend_color = "#10b981" if prices[-1] >= prices[0] else "#ef4444"
    change_pct = ((prices[-1] - prices[0]) / prices[0]) * 100

    # Grid lines
    grid_lines = ""
    for i in range(5):
        gy = margin["top"] + (i / 4) * chart_h
        gp = max_p - (i / 4) * price_range
        grid_lines += f'<line x1="{margin["left"]}" y1="{gy:.1f}" x2="{width-margin["right"]}" y2="{gy:.1f}" stroke="#e5e7eb" stroke-width="0.5"/>\n'
        grid_lines += f'<text x="{margin["left"]-8}" y="{gy:.1f}" text-anchor="end" font-size="10" fill="#6b7280">${gp:,.0f}</text>\n'

    # X axis labels (show first, middle, last)
    x_labels = ""
    label_indices = [0, len(dates) // 2, len(dates) - 1]
    for idx in label_indices:
        if idx < len(dates):
            x_labels += f'<text x="{x(idx):.1f}" y="{height-15}" text-anchor="middle" font-size="10" fill="#6b7280">{dates[idx][-5:]}</text>\n'

    # SMA lines
    sma_lines = ""
    if data.sma_20 and len(data.sma_20) == len(prices):
        sma20_points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(data.sma_20) if v > 0)
        sma_lines += f'<polyline points="{sma20_points}" fill="none" stroke="#f59e0b" stroke-width="1.2" stroke-dasharray="4,2" opacity="0.7"/>\n'
    if data.sma_50 and len(data.sma_50) == len(prices):
        sma50_points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(data.sma_50) if v > 0)
        sma_lines += f'<polyline points="{sma50_points}" fill="none" stroke="#8b5cf6" stroke-width="1.2" stroke-dasharray="4,2" opacity="0.7"/>\n'

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
  <rect width="{width}" height="{height}" fill="#fafbfc" rx="8"/>
  <text x="{margin["left"]}" y="24" font-size="14" font-weight="bold" fill="#1f2937">{data.asset} 價格走勢</text>
  <text x="{width-margin["right"]}" y="24" text-anchor="end" font-size="12" fill="{trend_color}" font-weight="bold">{change_pct:+.2f}%</text>
  {grid_lines}
  <polygon points="{area_points}" fill="{trend_color}" opacity="0.08"/>
  <polyline points="{points}" fill="none" stroke="{trend_color}" stroke-width="2" stroke-linejoin="round"/>
  {sma_lines}
  {x_labels}
  <circle cx="{x(len(prices)-1):.1f}" cy="{y(prices[-1]):.1f}" r="4" fill="{trend_color}"/>
  <text x="{x(len(prices)-1)+8:.1f}" y="{y(prices[-1])+4:.1f}" font-size="11" font-weight="bold" fill="{trend_color}">${prices[-1]:,.2f}</text>
</svg>'''
    return svg


def generate_candlestick_svg(data: ChartData, width: int = 800, height: int = 350) -> str:
    """產生 K 線圖 SVG。"""
    if not data.candles or len(data.candles) < 2:
        return _empty_chart(data.asset, "K 線圖", width, height)

    candles = data.candles
    all_highs = [c.high for c in candles]
    all_lows = [c.low for c in candles]
    min_p = min(all_lows) * 0.998
    max_p = max(all_highs) * 1.002
    price_range = max_p - min_p if max_p > min_p else 1

    margin = {"top": 40, "right": 60, "bottom": 50, "left": 70}
    chart_w = width - margin["left"] - margin["right"]
    chart_h = height - margin["top"] - margin["bottom"]
    candle_w = max(2, chart_w / len(candles) * 0.7)
    gap = chart_w / len(candles)

    def y(price: float) -> float:
        return margin["top"] + (1 - (price - min_p) / price_range) * chart_h

    candle_elements = ""
    for i, c in enumerate(candles):
        cx = margin["left"] + (i + 0.5) * gap
        is_up = c.close >= c.open
        color = "#10b981" if is_up else "#ef4444"
        body_top = y(max(c.open, c.close))
        body_bottom = y(min(c.open, c.close))
        body_height = max(1, body_bottom - body_top)

        # Wick
        candle_elements += f'<line x1="{cx:.1f}" y1="{y(c.high):.1f}" x2="{cx:.1f}" y2="{y(c.low):.1f}" stroke="{color}" stroke-width="1"/>\n'
        # Body
        candle_elements += f'<rect x="{cx - candle_w/2:.1f}" y="{body_top:.1f}" width="{candle_w:.1f}" height="{body_height:.1f}" fill="{color}" rx="1"/>\n'

    # Grid
    grid_lines = ""
    for i in range(5):
        gy = margin["top"] + (i / 4) * chart_h
        gp = max_p - (i / 4) * price_range
        grid_lines += f'<line x1="{margin["left"]}" y1="{gy:.1f}" x2="{width-margin["right"]}" y2="{gy:.1f}" stroke="#e5e7eb" stroke-width="0.5"/>\n'
        grid_lines += f'<text x="{margin["left"]-8}" y="{gy:.1f}" text-anchor="end" font-size="10" fill="#6b7280">${gp:,.0f}</text>\n'

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
  <rect width="{width}" height="{height}" fill="#fafbfc" rx="8"/>
  <text x="{margin["left"]}" y="24" font-size="14" font-weight="bold" fill="#1f2937">{data.asset} K 線圖 ({len(candles)} 根)</text>
  {grid_lines}
  {candle_elements}
</svg>'''
    return svg


def generate_rsi_chart_svg(data: ChartData, width: int = 800, height: int = 150) -> str:
    """產生 RSI 指標圖 SVG。"""
    if not data.rsi_values:
        return _empty_chart(data.asset, "RSI", width, height // 2)

    values = list(data.rsi_values)
    margin = {"top": 30, "right": 60, "bottom": 30, "left": 70}
    chart_w = width - margin["left"] - margin["right"]
    chart_h = height - margin["top"] - margin["bottom"]

    def x(i: int) -> float:
        return margin["left"] + (i / max(len(values) - 1, 1)) * chart_w

    def y(v: float) -> float:
        return margin["top"] + (1 - v / 100) * chart_h

    points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    current_rsi = values[-1] if values else 50
    rsi_color = "#ef4444" if current_rsi > 70 else "#10b981" if current_rsi < 30 else "#6b7280"

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
  <rect width="{width}" height="{height}" fill="#fafbfc" rx="8"/>
  <text x="{margin["left"]}" y="20" font-size="12" font-weight="bold" fill="#1f2937">RSI (14)</text>
  <text x="{width-margin["right"]}" y="20" text-anchor="end" font-size="11" font-weight="bold" fill="{rsi_color}">{current_rsi:.1f}</text>
  <rect x="{margin["left"]}" y="{y(70):.1f}" width="{chart_w}" height="{y(30)-y(70):.1f}" fill="#f3f4f6" opacity="0.5"/>
  <line x1="{margin["left"]}" y1="{y(70):.1f}" x2="{width-margin["right"]}" y2="{y(70):.1f}" stroke="#ef4444" stroke-width="0.8" stroke-dasharray="4,3"/>
  <line x1="{margin["left"]}" y1="{y(30):.1f}" x2="{width-margin["right"]}" y2="{y(30):.1f}" stroke="#10b981" stroke-width="0.8" stroke-dasharray="4,3"/>
  <line x1="{margin["left"]}" y1="{y(50):.1f}" x2="{width-margin["right"]}" y2="{y(50):.1f}" stroke="#9ca3af" stroke-width="0.5" stroke-dasharray="2,2"/>
  <polyline points="{points}" fill="none" stroke="{rsi_color}" stroke-width="1.8" stroke-linejoin="round"/>
  <text x="{width-margin["right"]+5}" y="{y(70)+4:.1f}" font-size="9" fill="#ef4444">70</text>
  <text x="{width-margin["right"]+5}" y="{y(30)+4:.1f}" font-size="9" fill="#10b981">30</text>
</svg>'''
    return svg


def generate_volume_chart_svg(data: ChartData, width: int = 800, height: int = 120) -> str:
    """產生成交量柱狀圖 SVG。"""
    if not data.candles:
        return ""

    volumes = [c.volume for c in data.candles]
    max_vol = max(volumes) if volumes else 1

    margin = {"top": 25, "right": 60, "bottom": 20, "left": 70}
    chart_w = width - margin["left"] - margin["right"]
    chart_h = height - margin["top"] - margin["bottom"]
    bar_w = max(2, chart_w / len(volumes) * 0.7)
    gap = chart_w / len(volumes)

    bars = ""
    for i, (vol, candle) in enumerate(zip(volumes, data.candles)):
        bx = margin["left"] + (i + 0.5) * gap - bar_w / 2
        bh = (vol / max_vol) * chart_h
        by = margin["top"] + chart_h - bh
        color = "#10b98140" if candle.close >= candle.open else "#ef444440"
        bars += f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" fill="{color}" rx="1"/>\n'

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
  <rect width="{width}" height="{height}" fill="#fafbfc" rx="8"/>
  <text x="{margin["left"]}" y="16" font-size="11" font-weight="bold" fill="#1f2937">成交量</text>
  {bars}
</svg>'''
    return svg


def svg_to_data_uri(svg: str) -> str:
    """SVG → base64 data URI，可直接嵌入 Markdown ![](data:...)。"""
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def charts_to_markdown(data: ChartData) -> str:
    """產生完整的圖表 Markdown 區塊，可嵌入分析報告。"""
    sections: list[str] = []

    sections.append(f"## {data.asset} 技術圖表\n")

    # Price chart
    price_svg = generate_price_chart_svg(data)
    sections.append(f"### 價格走勢\n\n![{data.asset} 價格走勢]({svg_to_data_uri(price_svg)})\n")

    # Candlestick
    if len(data.candles) <= 60:  # Only show candles if not too many
        candle_svg = generate_candlestick_svg(data)
        sections.append(f"### K 線圖\n\n![{data.asset} K 線]({svg_to_data_uri(candle_svg)})\n")

    # RSI
    if data.rsi_values:
        rsi_svg = generate_rsi_chart_svg(data)
        sections.append(f"### RSI 指標\n\n![RSI]({svg_to_data_uri(rsi_svg)})\n")

    # Volume
    vol_svg = generate_volume_chart_svg(data)
    if vol_svg:
        sections.append(f"### 成交量\n\n![成交量]({svg_to_data_uri(vol_svg)})\n")

    return "\n".join(sections)


def _empty_chart(asset: str, title: str, width: int, height: int) -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">
  <rect width="{width}" height="{height}" fill="#fafbfc" rx="8"/>
  <text x="{width//2}" y="{height//2}" text-anchor="middle" font-size="14" fill="#9ca3af">{asset} {title} — 資料不足</text>
</svg>'''


__all__ = [
    "ChartData",
    "OHLCV",
    "charts_to_markdown",
    "generate_candlestick_svg",
    "generate_price_chart_svg",
    "generate_rsi_chart_svg",
    "generate_volume_chart_svg",
    "svg_to_data_uri",
]
