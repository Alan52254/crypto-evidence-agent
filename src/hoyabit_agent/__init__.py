"""可溯源的加密貨幣分析 Agent。

公開的東西刻意只有兩個：`analyse`（接縫 2）與 `AnalysisRequest`。
其餘型別在 `hoyabit_agent.domain`，接縫介面在 `hoyabit_agent.seams`。
"""

from hoyabit_agent.domain import AnalysisOutcome, AnalysisRequest
from hoyabit_agent.run import analyse

__all__ = ["AnalysisOutcome", "AnalysisRequest", "analyse"]
