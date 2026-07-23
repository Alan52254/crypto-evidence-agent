"""詞典式情緒打分 —— Function 工具，確定性純函數，零外部依賴。

這是勞務層的**後備**實作：沒有模型金鑰時，系統仍然完整可跑。
ticket 05 的模型供應者會取代它，但兩者滿足同一個介面
（`label(texts) -> scores`），因此可以直接對調。

刻意保持樸素：它的價值在於「永遠可用、完全確定、可單元測試」，
不在於準確度。準確度是模型的工作。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from hoyabit_agent.domain import LabelAspect

_BULLISH = frozenset(
    {
        "surge", "surges", "rally", "rallies", "soar", "soars", "jump", "jumps",
        "gain", "gains", "rise", "rises", "climb", "climbs", "bullish", "record",
        "high", "breakout", "adoption", "approval", "approved", "inflow",
        "inflows", "upgrade", "partnership", "accumulate", "accumulation",
        "上漲", "突破", "利多", "買盤", "流入", "核准", "新高", "看多", "增持",
    }
)

_BEARISH = frozenset(
    {
        "plunge", "plunges", "crash", "crashes", "drop", "drops", "fall", "falls",
        "slump", "slumps", "sink", "sinks", "bearish", "hack", "hacked", "exploit",
        "lawsuit", "ban", "banned", "outflow", "outflows", "liquidation",
        "liquidations", "selloff", "dump", "fraud", "delay", "delayed",
        "下跌", "暴跌", "利空", "賣壓", "流出", "駭客", "禁令", "新低", "看空", "拋售",
    }
)

_WORD = re.compile(r"[a-z]+")


def score_text(text: str) -> float:
    """單一則文本的傾向分數，落在 −1 到 +1。

    分數屬於**這一則文本**，不屬於任何幣種 —— 幣種層級的是情緒彙總，
    必須能列舉出組成它的每一則片段。
    """
    lowered = text.lower()
    # 英文以詞為單位；中文沒有空格，改以雙字滑窗比對。
    tokens = set(_WORD.findall(lowered))
    tokens.update(lowered[index : index + 2] for index in range(len(lowered) - 1))

    bullish = len(tokens & _BULLISH)
    bearish = len(tokens & _BEARISH)
    if bullish == 0 and bearish == 0:
        return 0.0
    return (bullish - bearish) / (bullish + bearish)


class LexiconLabeller:
    """滿足 `label(texts, aspect) -> scores` 的零依賴實作。

    介面與 `ModelProvider.label` 相同，因此兩者可直接對調。

    **它分不出「輿論傾向」與「實質影響」** —— 詞典只看得到字面。
    這是後備實作的已知限制：兩個面會拿到相同分數，因而永遠一致。
    有模型時這個限制就消失了，所以不值得為它增加複雜度；
    但它意味著**沒有金鑰時的信心度會偏高**，讀報告時要知道這件事。
    """

    async def label(
        self,
        texts: Sequence[str],
        aspect: LabelAspect = LabelAspect.SENTIMENT,
    ) -> tuple[float, ...]:
        return tuple(score_text(text) for text in texts)


__all__ = ["LexiconLabeller", "score_text"]
