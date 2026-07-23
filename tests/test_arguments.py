"""工具參數強制轉型的單元測試 —— 純函數。

模型會給出不在 schema 內的東西。這裡的每一條都是「那也不能炸」。
"""

from __future__ import annotations

import pytest

from hoyabit_agent.arguments import bounded_int, choice

ALLOWED = ("1h", "4h", "1d")


@pytest.mark.parametrize("value", ["1h", "4h", "1d"])
def test_a_legal_choice_passes_through(value: str) -> None:
    assert choice(value, ALLOWED, "1d") == value


@pytest.mark.parametrize("value", ["2h", "", None, 5, [], {"a": 1}, True])
def test_an_illegal_choice_falls_back(value: object) -> None:
    assert choice(value, ALLOWED, "1d") == "1d"


def test_an_integer_inside_the_range_passes_through() -> None:
    assert bounded_int(50, 1, 100, 10) == 50


def test_values_outside_the_range_are_clamped_to_the_boundary() -> None:
    assert bounded_int(-5, 1, 100, 10) == 1
    assert bounded_int(9_999, 1, 100, 10) == 100


@pytest.mark.parametrize("value", ["abc", None, [], {}, object()])
def test_unconvertible_values_fall_back(value: object) -> None:
    assert bounded_int(value, 1, 100, 10) == 10


def test_numeric_strings_are_accepted() -> None:
    assert bounded_int("42", 1, 100, 10) == 42


def test_floats_are_truncated_toward_zero() -> None:
    assert bounded_int(42.9, 1, 100, 10) == 42


def test_booleans_fall_back_rather_than_counting_as_one_or_zero() -> None:
    """bool 是 int 的子型別，但當數字用幾乎必然是誤傳。"""
    assert bounded_int(True, 1, 100, 10) == 10
    assert bounded_int(False, 1, 100, 10) == 10
