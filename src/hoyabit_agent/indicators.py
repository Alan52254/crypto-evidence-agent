"""技術與籌碼指標 —— Function 工具，確定性純函數，無 I/O。

每個指標除了回傳數值，也回傳一段**可稽核的算式說明**，
因為證據要能被追問「這個數字怎麼來的」。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# 傾向分數的飽和上限：再極端的數值也不會超過 ±1，
# 避免單一指標的離群值主導整個證據面。
_SATURATION = 1.0


@dataclass(frozen=True)
class Kline:
    """一根 K 線。只保留我們真正會用到的欄位。"""

    open_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def clamp(value: float, limit: float = _SATURATION) -> float:
    return max(-limit, min(limit, value))


def sma(values: Sequence[float], period: int) -> float | None:
    """簡單移動平均。資料不足時回傳 None —— 不以 0 或最後一筆矇混過去。"""
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def rsi(closes: Sequence[float], period: int = 14) -> float | None:
    """相對強弱指標，Wilder 平滑法。

    需要 period + 1 根收盤價才能算出第一個值；不足時回傳 None。
    """
    if period <= 0 or len(closes) < period + 1:
        return None

    # 起始平均取前 period 根的漲跌幅
    gains = 0.0
    losses = 0.0
    for index in range(1, period + 1):
        change = closes[index] - closes[index - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)

    avg_gain = gains / period
    avg_loss = losses / period

    for index in range(period + 1, len(closes)):
        change = closes[index] - closes[index - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def relative_to_average(close: float, average: float) -> float:
    """收盤價相對均線的偏離幅度，轉成傾向分數。

    映射：偏離 10% 對應滿分。這個係數是刻意選的 ——
    加密貨幣日線 10% 的均線乖離已經是明確的趨勢訊號。
    """
    if average <= 0:
        return 0.0
    return clamp((close - average) / average / 0.10)


def rsi_to_stance(value: float) -> float:
    """RSI 轉傾向分數：50 為中性，向兩端線性延伸。

    刻意**不**做「超買反轉」的逆勢解讀 —— 那是一個交易策略假設，
    不是資料本身說的話。逆勢與否交給推理層依全部證據判斷。
    """
    return clamp((value - 50.0) / 50.0)


def volume_change(volumes: Sequence[float], window: int = 5) -> float | None:
    """近期均量相對前一段均量的變化率。資料不足時回傳 None。"""
    if window <= 0 or len(volumes) < window * 2:
        return None
    recent = sum(volumes[-window:]) / window
    earlier = sum(volumes[-window * 2 : -window]) / window
    if earlier <= 0:
        return None
    return (recent - earlier) / earlier


def funding_to_stance(rate: float) -> float:
    """資金費率轉傾向分數。

    正費率代表多方付錢給空方，即市場淨多頭。映射以 0.01%（單期）
    為滿分基準 —— 那大約是 Binance 永續的常見上限區間。

    **這裡刻意只做方向映射，不做「過熱即反轉」的判斷** ——
    那是策略觀點，應由推理層在看到其他證據面後自己決定。
    """
    return clamp(rate / 0.0001)


def open_interest_change(values: Sequence[float]) -> float | None:
    """未平倉合約的變化率。資料不足時回傳 None。"""
    if len(values) < 2 or values[0] <= 0:
        return None
    return (values[-1] - values[0]) / values[0]


def order_book_imbalance(bid_quantity: float, ask_quantity: float) -> float:
    """買賣盤失衡度轉傾向分數。

    掛單量哪一邊厚，代表該價位附近的承接或壓制力量較強。
    映射以 30% 失衡為滿分 —— 盤口天生就不對稱，小幅偏差沒有意義。
    """
    total = bid_quantity + ask_quantity
    if total <= 0:
        return 0.0
    return clamp((bid_quantity - ask_quantity) / total / 0.30)


def relative_spread(best_bid: float, best_ask: float) -> float | None:
    """買賣價差相對中價的比例。報價不合理時回傳 None。"""
    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        return None
    mid = (best_bid + best_ask) / 2
    return (best_ask - best_bid) / mid


def long_short_to_stance(ratio: float) -> float:
    """多空帳戶比轉傾向分數。1.0 為中性，以 ±0.5 的偏離為滿分。"""
    if ratio <= 0:
        return 0.0
    return clamp((ratio - 1.0) / 0.5)


__all__ = [
    "Kline",
    "clamp",
    "funding_to_stance",
    "long_short_to_stance",
    "open_interest_change",
    "order_book_imbalance",
    "relative_spread",
    "relative_to_average",
    "rsi",
    "rsi_to_stance",
    "sma",
    "volume_change",
]
