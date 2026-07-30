"""報告限制聲明組裝 — 純函數，無 I/O。

從 `_assemble()` 抽出。職責單一：接收所有限制來源，回傳 `list[str]`。
每一行是一段可直接放進 `Report.limitations` 的人類可讀文字。
"""

from __future__ import annotations

from hoyabit_agent.domain import Facet
from hoyabit_agent.question import CoreDataDemand, DataAvailability, DemandWeight


def build_report_limitations(
    *,
    core_data_demands: tuple[CoreDataDemand, ...] = (),
    unavailable_facets: frozenset[Facet] = frozenset(),
    boundary_notes: tuple[str, ...] = (),
    blocking_gap_details: tuple[str, ...] = (),
) -> list[str]:
    """組裝報告限制聲明（進入 Report.limitations）。

    順序有意義 — 核心缺失 > 回測不可得 > 邊界聲明 > 未關閉缺口。
    讀者最先看到最嚴重的限制。
    """
    limitations: list[str] = []

    # ─── A+B 核心資料缺失聲明（最高優先）───
    unavailable_core = tuple(
        d for d in core_data_demands
        if d.availability is DataAvailability.UNAVAILABLE and d.weight is DemandWeight.CORE
    )
    unavailable_supporting = tuple(
        d for d in core_data_demands
        if d.availability is DataAvailability.UNAVAILABLE and d.weight is DemandWeight.SUPPORTING
    )
    partial = tuple(
        d for d in core_data_demands
        if d.availability is DataAvailability.PARTIAL
    )

    if unavailable_core:
        labels = "、".join(d.label for d in unavailable_core)
        limitations.append(
            f"⚠️ 題目核心資料缺失：本系統不具備 {labels} 的資料源，"
            f"無法直接回答題目的主要問題。以下分析僅為可得面向的替代參考，"
            f"不可視為對主問題的完整回答。"
        )
        for d in unavailable_core:
            limitations.append(f"  • {d.label}：{d.fallback_note}")

    if unavailable_supporting:
        labels = "、".join(d.label for d in unavailable_supporting)
        limitations.append(
            f"輔助資料缺失：{labels} —— 結論完整度受限。"
        )

    if partial:
        for d in partial:
            limitations.append(
                f"部分可用資料：{d.label} — {d.fallback_note}"
            )

    # 回測模式下不可得的面
    limitations.extend(
        f"{facet.value} 面資料不可得（回測模式僅有資料集 OHLCV，無合規的即時來源）"
        for facet in sorted(unavailable_facets, key=lambda f: f.value)
    )

    # 邊界聲明
    limitations.extend(boundary_notes)

    # 未關閉的證據缺口
    limitations.extend(
        f"未關閉的證據缺口：{detail}" for detail in blocking_gap_details
    )

    return limitations


__all__ = ["build_report_limitations"]
