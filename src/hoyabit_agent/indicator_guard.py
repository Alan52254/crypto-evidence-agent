"""確定性指標引用檢核 — 掃描 claim text 中的技術指標數值，驗證有 evidence 對應。

比照 `enforce_paired_disclosure` 的模式：在 review 之後、`_assemble` 之前執行。
不依賴 LLM，純 regex + 查表。

設計考量：
- 只抓「指標名稱 + 數字」的組合（如 "RSI 72.3"、"MACD -0.5"）
- 對每個數字檢查是否能在 evidence excerpts/summary 中找到近似值
- 找不到的 → 從 claim text 中移除該數字段落，並在 text 末尾加註
- 不 drop 整則 claim（太激進），只 sanitize 幻覺數字
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from hoyabit_agent.domain import DraftClaim, Evidence

logger = logging.getLogger(__name__)

# 技術指標的 regex pattern：指標名 + 可能的括號參數 + 冒號/等號/空格 + 數字
# 例如："RSI(14) 72.3", "MACD = -1.5", "KD %K 85.2", "布林上軌 $68,500"
_INDICATOR_NAMES = (
    r"RSI",
    r"MACD",
    r"KD",
    r"KDJ",
    r"%K",
    r"%D",
    r"DIF",
    r"DEA",
    r"EMA",
    r"布林",
    r"Bollinger",
    r"BB",
    r"標準差",
    r"StdDev",
    r"波動率?",  # 涵蓋「波動率」「年化波動率」「日均波動」—— 同樣是 prompts.py
    # 禁止自行推算的統計量，只是敘述性寫法而非「名稱=數字」的緊密引用。
    # 「率?」把結尾的「率」一併吃掉，否則「波動」單獨匹配後，middle
    # 區段不含中文字元，後面的「率」會卡住比對。
)

_INDICATOR_PATTERN = re.compile(
    r"(?P<indicator>"
    + "|".join(_INDICATOR_NAMES)
    + r")"
    r"[\s\(\)\d,]*"  # 可能跟括號或參數
    r"(?:約|為|是|≈)?"  # 敘述性連接詞（如「波動率約 47%」），非緊密引用時常見
    r"[=:：\s]*"  # 分隔符
    r"(?P<value>[-+]?\d+[,.]?\d*)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OrphanIndicator:
    """claim 中出現但無 evidence 對應的指標數值。"""

    claim_index: int
    indicator: str
    value: str
    claim_text_snippet: str


def find_orphan_indicators(
    drafts: tuple[DraftClaim, ...],
    evidence: tuple[Evidence, ...],
) -> tuple[OrphanIndicator, ...]:
    """掃描所有 claim，找出無 evidence 對應的指標數值。

    「對應」的定義：claim 引用的 evidence_ids 中，至少有一項的
    summary 或 excerpt.text 包含該數值（允許 ±1% 容差）。
    """
    # 建立 evidence_id → 所有文字內容（summary + excerpts）的查找表
    evidence_text_map: dict[str, str] = {}
    for item in evidence:
        texts = [item.summary]
        for excerpt in item.excerpts:
            texts.append(excerpt.text)
        evidence_text_map[item.id] = " ".join(texts)

    orphans: list[OrphanIndicator] = []

    for idx, claim in enumerate(drafts):
        # Skip paired disclosure text — it's system-generated, not LLM hallucination
        text_to_scan = re.sub(r"（對照：[^）]*）", "", claim.text)
        matches = list(_INDICATOR_PATTERN.finditer(text_to_scan))
        if not matches:
            continue

        # 收集此 claim 引用的所有 evidence 文字
        cited_text = " ".join(
            evidence_text_map.get(eid, "") for eid in claim.evidence_ids
        )

        for match in matches:
            indicator_name = match.group("indicator")
            value_str = match.group("value").replace(",", "")

            try:
                numeric_value = float(value_str)
            except ValueError:
                continue

            # 檢查 cited evidence 中是否包含此數值（±1% 或 ±0.5 絕對容差）
            if not _value_found_in_text(numeric_value, cited_text):
                orphans.append(OrphanIndicator(
                    claim_index=idx,
                    indicator=indicator_name,
                    value=value_str,
                    claim_text_snippet=claim.text[:80],
                ))

    return tuple(orphans)


def enforce_indicator_citations(
    drafts: tuple[DraftClaim, ...],
    evidence: tuple[Evidence, ...],
) -> tuple[DraftClaim, ...]:
    """掃描 claim text，若含技術指標數值但無對應 evidence_id，移除並標記。

    策略：移除幻覺數字所在的句段，在 text 末尾加上警告標記。
    這確保最終報告不包含無法溯源的具體數值，同時保留可溯源的部分。

    若無任何孤兒數字，回傳原始 drafts（零開銷）。
    """
    orphans = find_orphan_indicators(drafts, evidence)
    if not orphans:
        return drafts

    # 按 claim_index 分組
    orphan_by_claim: dict[int, list[OrphanIndicator]] = {}
    for orphan in orphans:
        orphan_by_claim.setdefault(orphan.claim_index, []).append(orphan)

    result = list(drafts)
    for idx, claim_orphans in orphan_by_claim.items():
        claim = result[idx]
        cleaned_text = claim.text

        # 移除包含幻覺數字的片段（指標名+數字的 match span）
        for orphan in claim_orphans:
            # 構建一個 pattern 來找到並移除包含此指標值的局部文字
            # 匹配「指標名...分隔符...數字」的最小包圍片段
            # 關鍵：value 前面必須有明確分隔符（=、:、空格），
            # 避免把 "RSI14" 這種指標名稱裡的數字誤砍。
            removal_pattern = re.compile(
                re.escape(orphan.indicator)
                # 必須跟抽取階段的 _INDICATOR_PATTERN 一樣允許數字 ——
                # 否則像 "RSI(14) = 99.9" 這種帶參數的寫法，"(14)" 裡的
                # 數字會擋住比對，偵測到孤兒數字卻清不掉（只留下警告 log）。
                + r"[\s\(\)\d,]*"
                + r"(?:約|為|是|≈)?"  # 跟抽取階段對稱，否則「波動率約 47%」清不掉
                + r"[=:：\s]*"  # 分隔符（含純空格，不強制要求 = 字元）
                + re.escape(orphan.value)
                + r"[%]?",
                re.IGNORECASE,
            )
            cleaned_text = removal_pattern.sub("", cleaned_text)

        # 清理多餘的空白和標點殘留
        cleaned_text = re.sub(r"\s{2,}", " ", cleaned_text).strip()
        cleaned_text = re.sub(r"[，、；]\s*[，、；]", "，", cleaned_text)

        indicators = ", ".join(
            f"{o.indicator}={o.value}" for o in claim_orphans
        )

        logger.warning(
            "[indicator_guard] claim #%d: removed orphan indicators: %s",
            idx, indicators,
        )

        # 不把警告訊息寫進 claim text — 那是使用者看的報告，
        # 不該出現系統除錯訊息。警告只留在 trace log。
        result[idx] = DraftClaim(
            text=cleaned_text,
            evidence_ids=claim.evidence_ids,
            facet=claim.facet,
            role=claim.role,
        )

    return tuple(result)


def _value_found_in_text(value: float, text: str) -> bool:
    """檢查 text 中是否包含 value 的近似數字。

    容差規則：
    - 絕對容差 ±0.5（涵蓋四捨五入差異）
    - 相對容差 ±1%（涵蓋不同時間點的微小差異）
    取兩者中較大的那個。
    """
    if not text:
        return False

    abs_tolerance = max(0.5, abs(value) * 0.01)

    # 提取 text 中所有數字
    numbers = re.findall(r"[-+]?\d+[,.]?\d*", text)
    for num_str in numbers:
        try:
            num = float(num_str.replace(",", ""))
        except ValueError:
            continue
        if abs(num - value) <= abs_tolerance:
            return True

    return False


__all__ = [
    "OrphanIndicator",
    "enforce_indicator_citations",
    "find_orphan_indicators",
]
