"""錯誤前提偵測 —— 確定性純函數，無需任何 mock。

題目本身可能把一個未證實的負面事件當成既定事實陳述
（「SOL 已經停止運作，分析一下影響」）。好的 Agent 不該順著這個前提
往下分析，而是先標記它尚待查證。這裡只測偵測本身：題目文字進，
斷言清單出，不涉及蒐集或驗證。
"""

from __future__ import annotations

import pytest

from hoyabit_agent.premise import PremiseVerificationStatus, detect_asserted_premises


def test_a_question_asserting_a_negative_event_as_fact_is_flagged() -> None:
    premises = detect_asserted_premises("SOL 已經停止運作，分析一下影響")

    assert len(premises) == 1
    assert premises[0].status is PremiseVerificationStatus.ASSERTED_UNVERIFIED
    assert premises[0].marker == "停止運作"


@pytest.mark.parametrize(
    "question",
    [
        "SOL 現況如何",
        "BTC 現在正在上漲，請分析",  # 有「正在」但沒有負面事件詞
        "ETH 最近表現怎麼樣",
    ],
)
def test_a_plain_question_is_not_flagged(question: str) -> None:
    assert detect_asserted_premises(question) == ()
