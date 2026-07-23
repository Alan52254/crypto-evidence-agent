"""同事件歸併的分群 —— Function 工具，確定性純函數。

ADR 0002 的證據獨立性規則要求：同一則新聞被兩家媒體轉載只算一個證據。
但兩家媒體的標題不會逐字相同，所以「同一事件」必須用**近似**判定，
不能用字串相等。

分群在此完成，`tools.merge_independent_evidence` 只做精確鍵的歸併 ——
把模糊的部分關在這一個模組裡，歸併本身維持簡單且可斷言。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

# 這些詞在幾乎每則加密貨幣新聞標題都會出現，對「是不是同一事件」毫無鑑別力。
_NOISE = frozenset(
    {
        "a", "an", "the", "of", "in", "on", "at", "to", "for", "by", "with",
        "and", "or", "as", "is", "are", "was", "were", "be", "been", "it",
        "its", "this", "that", "from", "after", "amid", "over", "into",
        "crypto", "cryptocurrency", "market", "markets", "price", "prices",
        "news", "update", "報導", "新聞", "市場", "價格", "加密", "貨幣",
    }
)

_TOKEN = re.compile(r"[a-z0-9]+|[一-鿿]")


def significant_tokens(title: str) -> frozenset[str]:
    """標題中真正有鑑別力的詞。

    英數以詞為單位、中日韓以字為單位切分 —— 中文標題沒有空格，
    逐字比對在這個用途上已經足夠有效。
    """
    return frozenset(
        token
        for token in _TOKEN.findall(title.lower())
        if token not in _NOISE and (len(token) > 1 or _is_cjk(token))
    )


def _is_cjk(token: str) -> bool:
    return "一" <= token <= "鿿"


def similarity(left: str, right: str) -> float:
    """兩個標題的 Jaccard 相似度，落在 0 到 1。"""
    a = significant_tokens(left)
    b = significant_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def assign_event_keys(titles: Sequence[str], threshold: float = 0.5) -> list[str]:
    """為一組標題指派事件鍵：夠相似的標題拿到同一個鍵。

    採用最先匹配的貪婪分群 —— 對「同一批新聞裡找出轉載」這個用途
    已經足夠，且結果與輸入順序一致因而可測試。

    回傳的鍵是穩定雜湊，同一群的成員拿到同一個字串。
    """
    representatives: list[str] = []
    keys: list[str] = []

    for title in titles:
        for representative in representatives:
            if similarity(title, representative) >= threshold:
                keys.append(_key_for(representative))
                break
        else:
            representatives.append(title)
            keys.append(_key_for(title))

    return keys


def _key_for(title: str) -> str:
    digest = hashlib.sha1(title.lower().encode("utf-8")).hexdigest()  # noqa: S324
    return f"EVT-{digest[:12]}"


__all__ = ["assign_event_keys", "significant_tokens", "similarity"]
