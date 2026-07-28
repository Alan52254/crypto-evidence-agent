"""來源可信度分級 —— 確定性映射，不是模型判斷。

為什麼需要它：目前所有證據權重相同，交易所原始數值與社群貼文一樣重。
那會讓「多加一個低品質來源」無成本地抬高信心度 —— 與 ADR 0002
「轉載不構成獨立證據」同一類的漏洞，只是換一個維度。

分級依據是**取得路徑**，不是內容好壞：
交易所端點的數值可以重算，媒體轉述不行。這是可程式化的判準，
「這篇寫得好不好」不是。
"""

from __future__ import annotations

from enum import Enum

from hoyabit_agent.domain import Evidence


class ReliabilityTier(Enum):
    """證據來源的可信層級。數值越高越可信。"""

    HIGH = "high"
    """原始數值或第一手公告：交易所 API、官方部落格、競賽資料集。可重算、無轉述。"""

    MEDIUM = "medium"
    """主流財經媒體的事實報導。有編輯把關，但已經過一層敘事。"""

    LOW = "low"
    """社群貼文、聚合站、二次轉載。無編輯把關或來源不透明。"""

    @property
    def weight(self) -> float:
        """信心度計算用的權重。"""
        return {"high": 1.0, "medium": 0.6, "low": 0.3}[self.value]


# 依 source_id 前綴分級。source_id 的格式由各證據源決定，
# 前綴是我們唯一能穩定依賴的部分。
_HIGH_PREFIXES = (
    "binance",  # 交易所原始端點（舊格式，保留相容性）
    "bnc-",  # 交易所原始端點（BNC-SPOT-*, BNC-PERP-*）
    "dataset",  # 競賽 OHLCV
    "hoyabit-",  # 本所自家聚合指標（一手來源）
    "official",  # 官方公告來源
    "ethereum-blog",
)

_MEDIUM_PREFIXES = (
    "coindesk",
    "cointelegraph",
    "blocktempo",
    "blockworks",
    "coingecko",
)


def tier_for_source(source_id: str) -> ReliabilityTier:
    """判定單一來源片段的可信層級。

    未知來源一律降到 LOW —— 預設保守。若一個新來源實際上很可信，
    那應該是明確加進清單的決定，不是預設繼承來的待遇。
    """
    lowered = source_id.casefold()
    if any(lowered.startswith(prefix) for prefix in _HIGH_PREFIXES):
        return ReliabilityTier.HIGH
    if any(lowered.startswith(prefix) for prefix in _MEDIUM_PREFIXES):
        return ReliabilityTier.MEDIUM
    return ReliabilityTier.LOW


def evidence_tier(evidence: Evidence) -> ReliabilityTier:
    """一項證據的可信層級 —— 取其來源片段中**最高**的那一級。

    刻意取最高而非平均：一項證據若同時有交易所數值與媒體轉述，
    它的可驗證性由交易所那一份決定，媒體那份只是補充敘事。
    """
    if not evidence.excerpts:
        return ReliabilityTier.LOW
    return max(
        (tier_for_source(excerpt.source_id) for excerpt in evidence.excerpts),
        key=lambda tier: tier.weight,
    )


def weighted_source_count(evidence: tuple[Evidence, ...]) -> float:
    """以可信度加權的獨立來源數。

    三個社群貼文（0.3 × 3 = 0.9）不等於一個交易所端點（1.0）——
    這正是「獨立來源數」這個門檻該有的行為。

    **同事件的來源合併計為一個。** 光數 distinct `source_id` 會讓
    ADR 0002 的漏洞從另一道門回來：歸併後的證據仍保留每一家轉載的
    來源片段（溯源不損失是刻意的），若逐一計數，多找一家轉載媒體
    就能無成本地推高獨立來源數。

    因此這裡對 `source_id` 做併集：`event_key` 把描述同一事件的不同
    來源連在一起；同一個 `source_id` 出現在多則證據時（例如一篇文章
    同時產出基本面與情緒面證據）也自然收斂成一個。
    """
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    tiers: dict[str, ReliabilityTier] = {}
    for item in evidence:
        sources = [e.source_id.strip() for e in item.excerpts if e.source_id.strip()]
        if not sources:
            continue
        for source_id in sources:
            find(source_id)
            tier = tier_for_source(source_id)
            existing = tiers.get(source_id)
            if existing is None or tier.weight > existing.weight:
                tiers[source_id] = tier
        # 同一則證據內的來源必然描述同一件事 —— 歸併過的轉載就在這裡收斂。
        for source_id in sources[1:]:
            union(sources[0], source_id)
        # 事件鍵把跨證據、跨面向的同一事件連起來。
        if item.event_key is not None:
            union(sources[0], f"event:{item.event_key}")

    clusters: dict[str, ReliabilityTier] = {}
    for source_id, tier in tiers.items():
        root = find(source_id)
        existing = clusters.get(root)
        if existing is None or tier.weight > existing.weight:
            clusters[root] = tier
    return sum(tier.weight for tier in clusters.values())


def tier_breakdown(evidence: tuple[Evidence, ...]) -> dict[str, int]:
    """各層級的證據數量 —— 寫進報告的「來源品質」段落。"""
    counts = {tier.value: 0 for tier in ReliabilityTier}
    for item in evidence:
        counts[evidence_tier(item).value] += 1
    return counts


__all__ = [
    "ReliabilityTier",
    "evidence_tier",
    "tier_breakdown",
    "tier_for_source",
    "weighted_source_count",
]
