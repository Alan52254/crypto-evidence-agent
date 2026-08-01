"""AWS Bedrock 模型供應者 —— 接縫 3 的 Claude on AWS 實作。

使用 Bedrock Runtime 的 Converse API（不需要 boto3，直接 REST）。
認證方式：Bedrock API Key（非 IAM SigV4），簡化部署。

選 Claude 3.5 Sonnet 的理由：
- 原生 tool use 支援（plan 層需要）
- 結構化 JSON 輸出穩定（synthesise 層需要）
- 繁體中文金融分析品質佳
- 200K context window（足以容納完整證據清單）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Sequence
from typing import Any, cast

import httpx

from hoyabit_agent.domain import Asset, ClaimRole, DraftClaim, Evidence, Facet, LabelAspect
from hoyabit_agent.models.prompts import (
    LABEL_SYSTEM,
    PLAN_SYSTEM,
    SYNTHESIS_SYSTEM,
    plan_prompt,
    synthesis_prompt,
)
from hoyabit_agent.models.schemas import CLAIMS_SCHEMA, describe_schema, parse_claims
from hoyabit_agent.seams import GatherContext, PlanDecision, ToolInvocation, ToolSpec

logger = logging.getLogger(__name__)

# ─── 設定 ───
BEDROCK_API_KEY_ENV = "BEDROCK_API_KEY"
BEDROCK_REGION_ENV = "BEDROCK_REGION"
BEDROCK_MODEL_ENV = "BEDROCK_MODEL"

DEFAULT_REGION = "us-east-1"
DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-6"
DEFAULT_TIMEOUT_SECONDS = 180.0
MAX_RETRIES = 2


class BedrockProvider:
    """滿足 `ModelProvider` 的 AWS Bedrock (Claude) 實作。

    使用 Bedrock 的 Converse API：
    - POST /model/{modelId}/converse
    - 認證：x-]amz-bedrock-api-key header

    不變式（與 GeminiProvider 相同）：
    * 失敗以降級表達，不以例外中斷分析回合。
    * 無狀態 — 同一實例可服務多個回合。
    * API key 不進日誌、不進回傳值。
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        *,
        region: str = DEFAULT_REGION,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._region = region
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._base_url = f"https://bedrock-runtime.{region}.amazonaws.com"

    @classmethod
    def from_environment(cls, client: httpx.AsyncClient) -> BedrockProvider | None:
        """依環境變數建構。沒有 BEDROCK_API_KEY 時回傳 None。"""
        api_key = os.environ.get(BEDROCK_API_KEY_ENV, "").strip()
        if not api_key:
            return None
        region = os.environ.get(BEDROCK_REGION_ENV, DEFAULT_REGION).strip()
        model = os.environ.get(BEDROCK_MODEL_ENV, DEFAULT_MODEL).strip()
        return cls(client, api_key, region=region, model=model)

    @property
    def model_id(self) -> str:
        """目前使用的模型 ID — 用於報告的 model_used 欄位。"""
        return self._model

    # ─── plan ─── 決定呼叫哪些工具

    async def plan(self, context: GatherContext, tools: tuple[ToolSpec, ...]) -> PlanDecision:
        """使用 Claude 的 tool use 能力決定下一步蒐集動作。"""
        if not tools:
            return PlanDecision(invocations=(), reason="沒有可用的工具")

        bedrock_tools = [_to_bedrock_tool(spec) for spec in tools]

        body = await self._converse(
            system=PLAN_SYSTEM,
            messages=[{"role": "user", "content": [{"text": plan_prompt(context)}]}],
            tools=bedrock_tools,
        )
        if body is None:
            return PlanDecision(invocations=(), reason="Bedrock 暫時無法回應，以現有證據繼續")

        # 解析 Claude 回應中的 tool_use blocks
        output = body.get("output", {})
        message = output.get("message", {})
        content_blocks = message.get("content", [])

        invocations: list[ToolInvocation] = []
        reason_parts: list[str] = []

        for block in content_blocks:
            if "toolUse" in block:
                tool_use = block["toolUse"]
                name = tool_use.get("name", "")
                args = tool_use.get("input", {})
                if name:
                    invocations.append(ToolInvocation(tool=name, arguments=args))
            elif "text" in block:
                reason_parts.append(block["text"])

        reason = "\n".join(reason_parts) or "（Bedrock 未說明理由）"
        return PlanDecision(invocations=tuple(invocations), reason=reason)

    # ─── synthesise ─── 從證據產出結構化判斷

    async def synthesise(
        self,
        asset: Asset,
        evidence: tuple[Evidence, ...],
        question: str = "請分析當前市場狀況",
    ) -> tuple[DraftClaim, ...]:
        """從蒐集的證據推出三層結構判斷。"""
        if not evidence:
            return ()

        schema_instruction = (
            "\n\n你必須以下列 JSON 格式回應（不要加任何額外文字，直接給 JSON）：\n"
            f"{describe_schema(CLAIMS_SCHEMA)}\n\n"
            "facet 必須是: technical, positioning, fundamental, sentiment\n"
            "role 必須是: fact, inference, conclusion, counter_evidence, risk, invalidation, watch\n"
            "evidence_ids 必須是上方出現過的真實 ID（如 BNC-SPOT-BTC-1d-RSI14）"
        )

        body = await self._converse(
            system=SYNTHESIS_SYSTEM + schema_instruction,
            messages=[{
                "role": "user",
                "content": [{"text": synthesis_prompt(asset, evidence, question)}],
            }],
        )
        if body is None:
            return ()

        # 提取回應文字
        text = self._extract_text(body)
        if not text:
            logger.warning("[Bedrock] synthesise 回應為空文字")
            return ()

        # 檢查是否因 maxTokens 被截斷
        stop_reason = body.get("stopReason", "")
        if stop_reason == "max_tokens":
            logger.warning("[Bedrock] synthesise 回應被 maxTokens 截斷（%d chars）", len(text))

        # 解析 JSON
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # 嘗試從 markdown 包裝中提取（Claude 常用 ```json ... ```）
            import re
            # 先試 ```json ... ``` 格式
            code_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
            if code_match:
                try:
                    parsed = json.loads(code_match.group(1))
                except json.JSONDecodeError:
                    pass
                else:
                    return parse_claims(parsed)
            # 再試直接找最外層 { ... }
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                try:
                    parsed = json.loads(match.group())
                except json.JSONDecodeError:
                    logger.warning("[Bedrock] synthesise JSON 解析失敗，前 200 字: %s", text[:200])
                    return ()
            else:
                logger.warning("[Bedrock] synthesise 回應中找不到 JSON，前 200 字: %s", text[:200])
                return ()

        return parse_claims(parsed)

    # ─── label ─── 批次情緒/基本面標註

    async def label(
        self,
        texts: Sequence[str],
        aspect: LabelAspect = LabelAspect.SENTIMENT,
    ) -> tuple[float, ...]:
        """批次打分。"""
        if not texts:
            return ()

        listing = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
        user_content = (
            f"共 {len(texts)} 則文本：\n\n{listing}\n\n"
            f'回傳格式：{{"scores": [0.3, -0.2, ...]}}，數量必須與輸入 ({len(texts)}) 相同。'
        )

        body = await self._converse(
            system=LABEL_SYSTEM[aspect],
            messages=[{"role": "user", "content": [{"text": user_content}]}],
        )
        if body is None:
            return tuple(0.0 for _ in texts)

        text = self._extract_text(body)
        if not text:
            return tuple(0.0 for _ in texts)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return tuple(0.0 for _ in texts)

        scores = parsed.get("scores", [])
        if not isinstance(scores, list) or len(scores) != len(texts):
            return tuple(0.0 for _ in texts)

        try:
            return tuple(max(-1.0, min(1.0, float(s))) for s in scores)
        except (TypeError, ValueError):
            return tuple(0.0 for _ in texts)

    # ─── 內部方法 ───

    async def _converse(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """呼叫 Bedrock Converse API。

        Endpoint: POST /model/{modelId}/converse
        """
        url = f"{self._base_url}/model/{self._model}/converse"

        payload: dict[str, Any] = {
            "system": [{"text": system}],
            "messages": messages,
            "inferenceConfig": {
                "maxTokens": 8192,
                "temperature": 0.1,
            },
        }
        if tools:
            payload["toolConfig"] = {"tools": tools}

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await asyncio.wait_for(
                    self._client.post(url, json=payload, headers=headers),
                    timeout=self._timeout_seconds,
                )
            except (TimeoutError, httpx.HTTPError) as exc:
                logger.warning("[Bedrock] HTTP 異常 (attempt %d): %s", attempt, exc)
                if attempt >= MAX_RETRIES:
                    return None
                await asyncio.sleep(2**attempt)
                continue

            if response.status_code == 200:
                try:
                    return cast(dict[str, Any], response.json())
                except ValueError:
                    return None

            if response.status_code == 429 or response.status_code >= 500:
                logger.warning(
                    "[Bedrock] HTTP %d (attempt %d): %s",
                    response.status_code, attempt, response.text[:200],
                )
                if attempt >= MAX_RETRIES:
                    return None
                await asyncio.sleep(2**attempt)
                continue

            # 4xx 非 429 — 不重試
            logger.warning(
                "[Bedrock] HTTP %d: %s", response.status_code, response.text[:500]
            )
            return None

        return None

    def _extract_text(self, body: dict[str, Any]) -> str:
        """從 Converse API 回應中提取文字內容。"""
        output = body.get("output", {})
        message = output.get("message", {})
        content_blocks = message.get("content", [])
        for block in content_blocks:
            if "text" in block:
                return block["text"]
        return ""


# ─── 工具格式轉換 ───

def _to_bedrock_tool(spec: ToolSpec) -> dict[str, Any]:
    """把 ToolSpec 轉成 Bedrock Converse API 的 tool 格式。

    Bedrock 的 tool schema:
    {
        "toolSpec": {
            "name": "...",
            "description": "...",
            "inputSchema": {"json": {...}}
        }
    }
    """
    # 清理不支援的 JSON Schema 關鍵字
    parameters = _clean_schema(dict(spec.parameters))

    # 確保 asset 欄位存在
    properties = dict(parameters.get("properties", {}) or {})
    properties.setdefault("asset", {
        "type": "string",
        "enum": ["BTC", "ETH", "SOL", "BNB", "XRP"],
        "description": "分析的加密資產",
    })
    parameters["properties"] = properties

    required = list(parameters.get("required", []) or [])
    if "asset" not in required:
        required = ["asset"] + required
    parameters["required"] = required

    return {
        "toolSpec": {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": {"json": parameters},
        }
    }


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """移除 Bedrock 不支援的 JSON Schema 關鍵字。"""
    ALLOWED = {"type", "description", "enum", "properties", "required", "items", "default"}
    cleaned = {k: v for k, v in schema.items() if k in ALLOWED}
    if "properties" in cleaned and isinstance(cleaned["properties"], dict):
        cleaned["properties"] = {
            k: _clean_schema(v) if isinstance(v, dict) else v
            for k, v in cleaned["properties"].items()
        }
    return cleaned


__all__ = [
    "BEDROCK_API_KEY_ENV",
    "BEDROCK_MODEL_ENV",
    "BEDROCK_REGION_ENV",
    "BedrockProvider",
    "DEFAULT_MODEL",
    "DEFAULT_REGION",
]
