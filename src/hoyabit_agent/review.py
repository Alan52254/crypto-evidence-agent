"""Post-synthesis review — 輕量自我審查，只修飾語氣不改結構。

在 synthesise 產出判斷之後、check_citations 之前執行。
用一次 LLM call 掃描所有判斷，標記並修正：
1. 過度確定的語氣（非直接觀察卻用確定語氣）
2. 面向矛盾未被解釋
3. 列出但未引用的證據未說明原因

不做的事：不改方向、不加新證據、不刪判斷、不改 evidence_ids。
失敗時回傳原始判斷，不中斷報告。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping

from hoyabit_agent.domain import DraftClaim, Evidence, Facet, Stance

logger = logging.getLogger(__name__)


REVIEW_SYSTEM = """\
你是一個分析報告的品質審查員。你的任務是修飾判斷的語氣，使其更嚴謹。

規則：
1. 只修改 text 欄位的措辭。不得修改 evidence_ids、facet、role。
2. 若 role 是 inference 或 conclusion，檢查是否使用了過度確定的語氣：
   - 「確認」「證實」「必定」「將會」→ 改為「暗示」「可能」「在目前證據下」
   - 「顯示 X 因為 Y」→ 若 Y 不是直接數據觀察，改為「可能因為 Y（需 Z 數據確認）」
3. 若提供的 facet_stances 中有面向與最終方向矛盾，且 text 中未提及該矛盾，
   在相關 conclusion 的 text 末尾加一句說明為何不採信該面向。
4. 配對指標檢查：若判斷中提到 SMA200 但未提到 SMA60（或反之），
   須在同一判斷中補上另一均線的數值作為對照，避免選擇性呈現。
   同理適用於利率族群（FEDFUNDS vs DGS10）。
5. 回傳格式：JSON array，每個元素含 index（原始位置）和 revised_text。
   只回傳需要修改的項目。不需修改的不要列出。
6. 若所有判斷都夠嚴謹不需修改，回傳空 array: []
7. 不得在 revised_text 中引入原始 text 沒有的數字。只能重新措辭，不能添加新事實。
"""


async def review_claims(
    claims: tuple[DraftClaim, ...],
    evidence: tuple[Evidence, ...],
    facet_stances: Mapping[Facet, Stance],
    model_call,  # async (system: str, user: str) -> str | None
) -> tuple[DraftClaim, ...]:
    """審查並修飾判斷語氣。失敗時回傳原始判斷。

    model_call 是一個簡單的 async function:
        async def call(system_prompt: str, user_prompt: str) -> str | None
    回傳模型的文字回應，或 None 表示失敗。
    """
    if not claims:
        return claims

    # 構造審查輸入
    user_prompt = _build_review_prompt(claims, evidence, facet_stances)

    try:
        response = await model_call(REVIEW_SYSTEM, user_prompt)
        if response is None:
            logger.warning("Review model returned None, using original claims")
            return claims

        revisions = _parse_revisions(response)
        if not revisions:
            return claims  # 不需修改

        # 套用修訂（含 3.2 輸出約束驗證）
        result = list(claims)
        for revision in revisions:
            idx = revision.get("index")
            new_text = revision.get("revised_text", "").strip()
            if idx is not None and 0 <= idx < len(result) and new_text:
                original = result[idx]
                # 3.2 驗證：拒絕引入新數字或改變結構的修訂
                if not _validate_revision(original, new_text):
                    logger.warning(f"Review revision #{idx} rejected: introduced new numbers or too different")
                    continue
                result[idx] = DraftClaim(
                    text=new_text,
                    evidence_ids=original.evidence_ids,
                    facet=original.facet,
                    role=original.role,
                )
                logger.info(f"Review revised claim #{idx}: {original.text[:50]}... → {new_text[:50]}...")

        return tuple(result)

    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Review failed ({exc}), using original claims")
        return claims


def _build_review_prompt(
    claims: tuple[DraftClaim, ...],
    evidence: tuple[Evidence, ...],
    facet_stances: Mapping[Facet, Stance],
) -> str:
    """構造給審查模型的 user prompt。"""
    lines: list[str] = []

    # 面向判定
    lines.append("各面向判定：")
    for facet, stance in sorted(facet_stances.items(), key=lambda kv: kv[0].value):
        lines.append(f"  {facet.value}: {stance.value}")
    lines.append("")

    # 列出證據 ID（讓模型知道哪些被列出了）
    evidence_ids = {e.id for e in evidence}
    lines.append(f"可用證據 ID: {', '.join(sorted(evidence_ids))}")
    lines.append("")

    # 判斷清單
    lines.append("待審查判斷：")
    for i, claim in enumerate(claims):
        cited = ", ".join(claim.evidence_ids)
        lines.append(f"[{i}] role={claim.role.value} facet={claim.facet.value}")
        lines.append(f"    text: {claim.text}")
        lines.append(f"    evidence_ids: [{cited}]")
        lines.append("")

    lines.append("請審查上述判斷，回傳需要修改的 JSON array（格式: [{\"index\": N, \"revised_text\": \"...\"}]）。")
    lines.append("若全部合格，回傳 []。")

    return "\n".join(lines)


def _parse_revisions(response: str) -> list[dict]:
    """從模型回應中解析修訂清單。"""
    # 嘗試找到 JSON array
    text = response.strip()

    # 移除可能的 markdown code block
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        text = "\n".join(lines).strip()

    # 找到 [ 開頭的部分
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []

    try:
        result = json.loads(text[start:end + 1])
        if isinstance(result, list):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    return []


__all__ = ["review_claims", "REVIEW_SYSTEM"]


import re


def _validate_revision(original: DraftClaim, revised_text: str) -> bool:
    """3.2 輸出約束驗證 — 防止 review LLM 亂改。

    拒絕條件：
    - revised_text 引入了原始 text 沒有的數字（零容忍）
    - revised_text 長度跟原始差異超過 50%（改太多了）
    """
    # 提取數字 — 零容忍：不允許任何新數字
    original_numbers = set(re.findall(r'\d+\.?\d*', original.text))
    revised_numbers = set(re.findall(r'\d+\.?\d*', revised_text))
    new_numbers = revised_numbers - original_numbers

    if new_numbers:
        return False

    # 長度變化不超過 50%
    original_len = len(original.text)
    if original_len > 0:
        ratio = abs(len(revised_text) - original_len) / original_len
        if ratio > 0.5:
            return False

    return True


# ─── Layer 1.1: 規則式配對揭露（不依賴 LLM）──────────────────────

# 配對指標群組：同群組的成員必須共同出現在同一判斷中
PAIRED_EVIDENCE_GROUPS: dict[str, tuple[str, ...]] = {
    "moving_averages": ("SMA60", "SMA200"),
    "rates": ("FEDFUNDS", "DGS10"),
}


def enforce_paired_disclosure(
    claims: tuple[DraftClaim, ...],
    evidence: tuple[Evidence, ...],
) -> tuple[DraftClaim, ...]:
    """規則式配對揭露 — 不依賴 LLM，程式碼層面強制。

    若某個判斷引用了配對群組的一個成員但沒引用另一個，
    且另一個成員確實存在於 evidence 中，則在該判斷的 text 末尾
    強制附上缺失成員的數值摘要。

    這是 deterministic 的，LLM 不照做也沒關係。
    """
    # 建立 evidence ID → summary 的查找表
    evidence_map: dict[str, str] = {e.id: e.summary for e in evidence}

    # 建立群組 → 實際存在的 evidence IDs 映射
    group_evidence: dict[str, dict[str, str]] = {}
    for group_name, keywords in PAIRED_EVIDENCE_GROUPS.items():
        group_evidence[group_name] = {}
        for eid, summary in evidence_map.items():
            for kw in keywords:
                if kw in eid.upper():
                    group_evidence[group_name][kw] = f"{eid}: {summary}"
                    break

    result = list(claims)
    for i, claim in enumerate(result):
        # 檢查這個 claim 的 evidence_ids 引用了哪些配對群組成員
        cited_ids_upper = " ".join(claim.evidence_ids).upper()

        for group_name, keywords in PAIRED_EVIDENCE_GROUPS.items():
            available = group_evidence.get(group_name, {})
            if len(available) < 2:
                continue  # 這個群組根本沒有兩個成員可配對

            cited_members = [kw for kw in keywords if kw in cited_ids_upper]
            missing_members = [kw for kw in keywords if kw not in cited_ids_upper]

            # 只有「引用了一個但漏了另一個」才觸發
            if cited_members and missing_members:
                for missing_kw in missing_members:
                    if missing_kw in available:
                        supplement = available[missing_kw]
                        # 找到缺失成員的完整 evidence ID
                        missing_eid = supplement.split(":")[0]
                        # 在 text 末尾附上缺失的配對指標
                        addition = f"（對照：{supplement}）"
                        if addition not in claim.text:
                            # 同時把 evidence_id 加入引用列表
                            new_ids = (*claim.evidence_ids, missing_eid)
                            result[i] = DraftClaim(
                                text=f"{claim.text} {addition}",
                                evidence_ids=new_ids,
                                facet=claim.facet,
                                role=claim.role,
                            )
                            claim = result[i]

    return tuple(result)
