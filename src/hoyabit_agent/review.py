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
   在相關 conclusion 的 text 末尾加一句說明。
4. 回傳格式：JSON array，每個元素含 index（原始位置）和 revised_text。
   只回傳需要修改的項目。不需修改的不要列出。
5. 若所有判斷都夠嚴謹不需修改，回傳空 array: []
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

        # 套用修訂
        result = list(claims)
        for revision in revisions:
            idx = revision.get("index")
            new_text = revision.get("revised_text", "").strip()
            if idx is not None and 0 <= idx < len(result) and new_text:
                original = result[idx]
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
