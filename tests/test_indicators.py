"""指標的單元測試 —— 純函數，無 mock、無 I/O。"""

from __future__ import annotations

import pytest

from hoyabit_agent.indicators import (
    clamp,
    funding_to_stance,
    long_short_to_stance,
    open_interest_change,
    order_book_imbalance,
    relative_spread,
    relative_to_average,
    rsi,
    rsi_to_stance,
    sma,
    volume_change,
)

# --------------------------------------------------------------------------
# 資料不足時回傳 None —— 絕不用 0 或最後一筆矇混
# --------------------------------------------------------------------------


def test_sma_needs_enough_data() -> None:
    assert sma([1.0, 2.0], 5) is None


def test_rsi_needs_period_plus_one_closes() -> None:
    assert rsi([1.0] * 14, period=14) is None
    assert rsi([1.0] * 15, period=14) is not None


def test_volume_change_needs_two_full_windows() -> None:
    assert volume_change([1.0] * 9, window=5) is None
    assert volume_change([1.0] * 10, window=5) is not None


def test_open_interest_change_needs_two_points() -> None:
    assert open_interest_change([100.0]) is None


# --------------------------------------------------------------------------
# 數值正確性
# --------------------------------------------------------------------------


def test_sma_averages_the_last_period_values_only() -> None:
    assert sma([100.0, 200.0, 1.0, 2.0, 3.0], 3) == pytest.approx(2.0)


def test_rsi_is_one_hundred_when_every_bar_rises() -> None:
    closes = [float(i) for i in range(1, 40)]
    assert rsi(closes) == pytest.approx(100.0)


def test_rsi_is_zero_when_every_bar_falls() -> None:
    closes = [float(i) for i in range(40, 1, -1)]
    assert rsi(closes) == pytest.approx(0.0)


def test_rsi_of_a_flat_series_is_neutral() -> None:
    assert rsi([100.0] * 40) == pytest.approx(50.0)


def test_rsi_sits_between_the_extremes_for_mixed_moves() -> None:
    closes = [100.0]
    for index in range(40):
        closes.append(closes[-1] + (2.0 if index % 2 == 0 else -1.0))
    value = rsi(closes)
    assert value is not None
    assert 50.0 < value < 100.0


def test_volume_change_compares_recent_against_the_previous_window() -> None:
    volumes = [10.0] * 5 + [15.0] * 5
    assert volume_change(volumes, window=5) == pytest.approx(0.5)


def test_open_interest_change_is_relative_to_the_first_point() -> None:
    assert open_interest_change([100.0, 110.0, 120.0]) == pytest.approx(0.2)


# --------------------------------------------------------------------------
# 傾向映射：飽和、中性點、方向
# --------------------------------------------------------------------------


def test_clamp_saturates_at_both_ends() -> None:
    assert clamp(5.0) == 1.0
    assert clamp(-5.0) == -1.0
    assert clamp(0.3) == pytest.approx(0.3)


def test_price_at_its_average_is_neutral() -> None:
    assert relative_to_average(100.0, 100.0) == pytest.approx(0.0)


def test_ten_percent_above_the_average_saturates_bullish() -> None:
    assert relative_to_average(110.0, 100.0) == pytest.approx(1.0)


def test_price_below_its_average_is_bearish() -> None:
    assert relative_to_average(95.0, 100.0) < 0


def test_a_non_positive_average_is_treated_as_neutral_not_an_error() -> None:
    assert relative_to_average(100.0, 0.0) == 0.0


def test_rsi_fifty_is_the_neutral_point() -> None:
    assert rsi_to_stance(50.0) == pytest.approx(0.0)


def test_rsi_extremes_saturate() -> None:
    assert rsi_to_stance(100.0) == pytest.approx(1.0)
    assert rsi_to_stance(0.0) == pytest.approx(-1.0)


def test_an_overbought_rsi_is_not_reinterpreted_as_bearish() -> None:
    """逆勢解讀是策略假設，不是資料說的話 —— 交給推理層決定。"""
    assert rsi_to_stance(85.0) > 0


def test_zero_funding_is_neutral() -> None:
    assert funding_to_stance(0.0) == pytest.approx(0.0)


def test_positive_funding_reads_as_net_long() -> None:
    assert funding_to_stance(0.00005) == pytest.approx(0.5)


def test_negative_funding_reads_as_net_short() -> None:
    assert funding_to_stance(-0.0001) == pytest.approx(-1.0)


def test_a_balanced_order_book_is_neutral() -> None:
    assert order_book_imbalance(100.0, 100.0) == pytest.approx(0.0)


def test_a_heavier_bid_side_reads_bullish() -> None:
    assert order_book_imbalance(150.0, 50.0) > 0


def test_order_book_imbalance_saturates() -> None:
    assert order_book_imbalance(100.0, 0.0) == pytest.approx(1.0)
    assert order_book_imbalance(0.0, 100.0) == pytest.approx(-1.0)


def test_an_empty_order_book_is_treated_as_neutral_not_an_error() -> None:
    assert order_book_imbalance(0.0, 0.0) == 0.0


def test_relative_spread_is_measured_against_the_mid_price() -> None:
    assert relative_spread(99.0, 101.0) == pytest.approx(0.02)


def test_a_crossed_or_invalid_book_has_no_spread() -> None:
    assert relative_spread(101.0, 99.0) is None
    assert relative_spread(0.0, 100.0) is None


def test_a_balanced_long_short_ratio_is_neutral() -> None:
    assert long_short_to_stance(1.0) == pytest.approx(0.0)


def test_more_longs_than_shorts_reads_bullish() -> None:
    assert long_short_to_stance(1.25) == pytest.approx(0.5)


def test_a_non_positive_ratio_is_treated_as_neutral_not_an_error() -> None:
    assert long_short_to_stance(0.0) == 0.0
