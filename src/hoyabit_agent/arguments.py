"""模型給的工具參數的強制轉型 —— Function 工具，確定性純函數。

**模型會給出不在 schema 內的東西** —— 錯的型別、不存在的列舉值、
超出範圍的數字、完全不相干的鍵。那是預期情況，不是錯誤。

這裡集中處理，讓每個證據源不必各自重寫一遍，也保證行為一致：
無法辨識就退回預設值，**絕不拋例外**。
"""

from __future__ import annotations

from typing import Any


def choice(value: Any, allowed: tuple[str, ...], fallback: str) -> str:
    """從允許的列舉值中挑一個。不合法時退回預設。"""
    return value if isinstance(value, str) and value in allowed else fallback


def bounded_int(value: Any, low: int, high: int, fallback: int) -> int:
    """夾在區間內的整數。無法轉型時退回預設，可轉型但超界時夾到邊界。"""
    if isinstance(value, bool):  # bool 是 int 的子型別，但當數字用幾乎必為誤傳
        return fallback
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return fallback


__all__ = ["bounded_int", "choice"]
