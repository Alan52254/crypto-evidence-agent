"""模型輸出的結構定義與解析 —— 純函數，無 I/O，可無 mock 測試。

雲端與地端兩個 provider 共用這一份：它們的**傳輸方式**不同
（Gemini REST vs OpenAI 相容），但要模型吐出什麼、以及吐錯時怎麼辦，
是同一件事。分開寫兩份只會讓兩邊慢慢漂開。
"""

from __future__ import annotations

import json
from typing import Any

from hoyabit_agent.domain import ClaimRole, DraftClaim, Facet
from hoyabit_agent.indicators import clamp

CLAIMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "一則判斷，繁體中文。"},
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "支撐這則判斷的證據 ID，必須來自提供的清單。",
                    },
                    "facet": {"type": "string", "enum": [facet.value for facet in Facet]},
                    "role": {
                        "type": "string",
                        "enum": [role.value for role in ClaimRole],
                    },
                },
                "required": ["text", "evidence_ids", "facet", "role"],
            },
        }
    },
    "required": ["claims"],
}

SCORES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {"type": "number"},
            "description": "每則文本一個 -1 到 1 的分數，順序與輸入相同。",
        }
    },
    "required": ["scores"],
}


def describe_schema(schema: dict[str, Any]) -> str:
    """把 schema 寫進提示詞。

    地端模型多半不支援 `json_schema` 這種伺服器端強制約束，只支援
    「請回 JSON」。把 schema 明寫在提示詞裡是唯一還能提高遵循率的手段。
    """
    return json.dumps(schema, ensure_ascii=False, indent=2)


def loads(text: str) -> Any:
    """寬鬆解析模型吐出的 JSON。

    地端模型常在 JSON 前後夾雜文字或 ```json 圍欄 —— 那是預期情況。
    先直接解析，失敗才退而求其次抓最外層的大括號。
    """
    try:
        return json.loads(text)
    except ValueError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except ValueError:
        return None


def to_draft(raw: Any) -> DraftClaim | None:
    """把一筆模型輸出轉成判斷。任一欄位不合格就整筆丟棄。

    這裡不做「盡量救回來」的事 —— 半個判斷比沒有判斷更危險。
    """
    if not isinstance(raw, dict):
        return None
    text = raw.get("text")
    ids = raw.get("evidence_ids")
    if not isinstance(text, str) or not text.strip():
        return None
    if not isinstance(ids, list):
        return None
    try:
        facet = Facet(raw.get("facet"))
        role = ClaimRole(raw.get("role", ClaimRole.INFERENCE.value))
    except ValueError:
        return None
    return DraftClaim(
        text=text.strip(),
        evidence_ids=tuple(str(item) for item in ids if isinstance(item, str)),
        facet=facet,
        role=role,
    )


def parse_claims(parsed: Any) -> tuple[DraftClaim, ...]:
    """從已解析的 JSON 取出判斷。結構不符時回傳空集合。"""
    if not isinstance(parsed, dict):
        return ()
    raw = parsed.get("claims")
    if not isinstance(raw, list):
        return ()
    drafts = [draft for item in raw if (draft := to_draft(item)) is not None]
    return tuple(drafts)


def parse_scores(parsed: Any, expected: int) -> tuple[float, ...] | None:
    """從已解析的 JSON 取出分數。

    數量對不上就整批作廢 —— 分數與文本的對應關係一旦錯位，
    每一則的溯源都是錯的，那比沒有分數更糟。
    回傳 None 代表呼叫端應退回中性。
    """
    if not isinstance(parsed, dict):
        return None
    raw = parsed.get("scores")
    if not isinstance(raw, list) or len(raw) != expected:
        return None
    try:
        return tuple(clamp(float(value)) for value in raw)
    except (TypeError, ValueError):
        return None


__all__ = [
    "CLAIMS_SCHEMA",
    "SCORES_SCHEMA",
    "describe_schema",
    "loads",
    "parse_claims",
    "parse_scores",
    "to_draft",
]
