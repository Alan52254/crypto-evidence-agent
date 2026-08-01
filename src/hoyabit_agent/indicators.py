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


# ─── MACD ───────────────────────────────────────────────────────


def ema(values: Sequence[float], period: int) -> float | None:
    """指數移動平均。資料不足時回傳 None。"""
    if period <= 0 or len(values) < period:
        return None
    multiplier = 2.0 / (period + 1)
    result = sum(values[:period]) / period  # 以 SMA 作為起始值
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def macd(
    closes: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[float, float, float] | None:
    """MACD 指標。回傳 (DIF, DEA, Histogram) 或 None。

    DIF = EMA(fast) - EMA(slow)
    DEA = EMA(signal) of DIF series
    Histogram = DIF - DEA
    """
    if len(closes) < slow + signal:
        return None

    # 計算完整的 EMA 序列
    fast_mult = 2.0 / (fast + 1)
    slow_mult = 2.0 / (slow + 1)

    # 初始化：用前 N 根的 SMA 作為 EMA 種子
    fast_ema = sum(closes[:fast]) / fast
    slow_ema = sum(closes[:slow]) / slow

    # fast EMA 暖機：從 index=fast 到 index=slow-1 逐步更新 fast_ema，
    # 但此區間 slow_ema 尚未開始（因為 slow SMA 種子用了前 slow 根）。
    # 這段修正了原本 fast_ema 在 14 步內完全未更新的 bug。
    for i in range(fast, slow):
        fast_ema = (closes[i] - fast_ema) * fast_mult + fast_ema

    # 從 index=slow 開始，兩條 EMA 都在更新，開始記錄 DIF 序列
    dif_series: list[float] = []
    for i in range(slow, len(closes)):
        fast_ema = (closes[i] - fast_ema) * fast_mult + fast_ema
        slow_ema = (closes[i] - slow_ema) * slow_mult + slow_ema
        dif_series.append(fast_ema - slow_ema)

    if len(dif_series) < signal:
        return None

    # DEA = EMA of DIF series
    signal_mult = 2.0 / (signal + 1)
    dea = sum(dif_series[:signal]) / signal
    for dif_val in dif_series[signal:]:
        dea = (dif_val - dea) * signal_mult + dea

    dif = dif_series[-1]
    histogram = dif - dea
    return (dif, dea, histogram)


# ─── Stochastic KD ──────────────────────────────────────────────


def stochastic_kd(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    k_period: int = 9,
    d_period: int = 3,
) -> tuple[float, float] | None:
    """隨機指標 KD。回傳 (%K, %D) 或 None。

    %K = (Close - Low_N) / (High_N - Low_N) × 100
    %D = SMA(%K, d_period)
    """
    if len(closes) < k_period + d_period - 1:
        return None
    if len(highs) != len(closes) or len(lows) != len(closes):
        return None

    k_values: list[float] = []
    for i in range(k_period - 1, len(closes)):
        window_high = max(highs[i - k_period + 1: i + 1])
        window_low = min(lows[i - k_period + 1: i + 1])
        if window_high == window_low:
            k_values.append(50.0)
        else:
            k_values.append((closes[i] - window_low) / (window_high - window_low) * 100)

    if len(k_values) < d_period:
        return None

    k = k_values[-1]
    d = sum(k_values[-d_period:]) / d_period
    return (k, d)


# ─── Bollinger Bands ────────────────────────────────────────────


def bollinger_bands(
    closes: Sequence[float],
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[float, float, float, float] | None:
    """布林通道。回傳 (upper, middle, lower, bandwidth%) 或 None。

    middle = SMA(period)
    upper = middle + num_std × StdDev
    lower = middle - num_std × StdDev
    bandwidth = (upper - lower) / middle × 100
    """
    if period <= 0 or len(closes) < period:
        return None

    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((x - middle) ** 2 for x in window) / period
    std_dev = variance ** 0.5

    upper = middle + num_std * std_dev
    lower = middle - num_std * std_dev
    bandwidth = (upper - lower) / middle * 100 if middle > 0 else 0.0

    return (upper, middle, lower, bandwidth)


__all__ = [
    "Kline",
    "bollinger_bands",
    "clamp",
    "ema",
    "funding_to_stance",
    "long_short_to_stance",
    "macd",
    "open_interest_change",
    "order_book_imbalance",
    "relative_spread",
    "relative_to_average",
    "rsi",
    "rsi_to_stance",
    "sma",
    "stochastic_kd",
    "volume_change",
]
