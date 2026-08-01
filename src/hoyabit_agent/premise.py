"""錯誤前提偵測 —— 題目是否把未證實的負面事件當成既定事實。

「SOL 已經停止運作，分析一下影響」跟「SOL 現況如何」語法上都是正常問句，
但前者夾帶了一個尚待查證的斷言。好的 Agent 不該默默接受這個前提往下推論。

判斷是**確定性關鍵字比對**，不是模型呼叫 —— 跟 question.py 的
detect_core_data_demands 同一個判準：誤判的代價不值得押在一次 LLM 回應上。
這裡只做偵測；是否真的查證屬實，交由後續蒐集與證據比對。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PremiseVerificationStatus(Enum):
    """斷言的查證狀態。"""

    ASSERTED_UNVERIFIED = "asserted_unverified"
    """題目把一個負面事件當既定事實陳述，但系統尚未查證。"""


@dataclass(frozen=True)
class AssertedPremise:
    """題目中偵測到的一個既定事實斷言。"""

    claim: str
    """人類可讀的斷言內容，如「SOL 已經停止運作」。"""

    marker: str
    """命中的關鍵詞。"""

    status: PremiseVerificationStatus


# 「既定事實」語氣詞 —— 題目用這些詞把後面的事件描述成已發生，而非提問。
_ASSERTION_MARKERS = ("已經", "已", "剛", "正在", "already")

# 負面事件詞 —— 只有跟既定事實語氣詞同時出現才觸發，避免「BTC 現在正在上漲」
# 這類正常敘述被誤判。
_NEGATIVE_EVENT_MARKERS = (
    "停止運作", "停止出塊", "崩盤", "被駭", "被攻擊", "倒閉", "下架",
    "脫鉤", "歸零", "跑路", "stopped working", "hacked", "collapsed",
)


def detect_asserted_premises(question: str) -> tuple[AssertedPremise, ...]:
    """從題目文字偵測「已經 X」型的既定事實斷言（X 為負面事件）。

    確定性關鍵字比對，無 I/O：既定事實語氣詞與負面事件詞必須同時出現，
    只看語氣詞或只看事件詞都會誤判太多正常問句。
    """
    if not any(marker in question for marker in _ASSERTION_MARKERS):
        return ()

    premises: list[AssertedPremise] = []
    for event_marker in _NEGATIVE_EVENT_MARKERS:
        if event_marker in question:
            premises.append(
                AssertedPremise(
                    claim=question.strip(),
                    marker=event_marker,
                    status=PremiseVerificationStatus.ASSERTED_UNVERIFIED,
                )
            )

    return tuple(premises)


__all__ = [
    "AssertedPremise",
    "PremiseVerificationStatus",
    "detect_asserted_premises",
]
