"""Groq 模型供應者 — 接縫 3 的備選實作。

Groq 以 LLaMA 3.3 70B 提供極低延遲推理，作為 Gemini 的 fallback。
延遲約 Gemini 的 1/5，但 context window 較小 (128K vs 1M)。
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping, Sequence
from typing import Any, cast

import httpx

from hoyabit_agent.domain import Asset, ClaimRole, DraftClaim, Evidence, Facet, LabelAspect
from hoyabit_agent.models.prompts import PLAN_SYSTEM, SYNTHESIS_SYSTEM, plan_prompt, synthesis_prompt
from hoyabit_agent.seams import GatherContext, PlanDecision, ToolInvocation, ToolSpec

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_API_KEY_ENV = "GROQ_API_KEY"
GROQ_MODEL_ENV = "GROQ_MODEL"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_RETRIES = 2


class GroqProvider:
    """滿足 ModelProvider 的 Groq 實作。

    使用 OpenAI-compatible API。降級策略與 Gemini 一致：
    失敗回傳空結果，不中斷分析回合。
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        *,
        model: str = DEFAULT_GROQ_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls, client: httpx.AsyncClient) -> GroqProvider | None:
        """依環境變數建構。沒有金鑰時回傳 None。"""
        api_key = os.environ.get(GROQ_API_KEY_ENV, "").strip()
        if not api_key:
            return None
        model = os.environ.get(GROQ_MODEL_ENV, DEFAULT_GROQ_MODEL).strip()
        return cls(client, api_key, model=model)

    async def plan(self, context: GatherContext, tools: tuple[ToolSpec, ...]) -> PlanDecision:
        """使用 Groq 的 tool_use 功能決定下一步。"""
        if not tools:
            return PlanDecision(invocations=(), reason="沒有可用的工具")

        messages = [
            {"role": "system", "content": PLAN_SYSTEM},
            {"role": "user", "content": plan_prompt(context)},
        ]
        # Build a name→spec map so we can coerce argument types after parsing
        spec_by_name = {spec.name: spec for spec in tools}
        groq_tools = [_to_openai_tool(spec) for spec in tools]

        body = await self._chat(messages, tools=groq_tools)
        if body is None:
            return PlanDecision(invocations=(), reason="Groq 暫時無法回應，以現有證據繼續")

        message = body.get("choices", [{}])[0].get("message", {})
        tool_calls = message.get("tool_calls", [])

        if not tool_calls:
            content = message.get("content", "")
            return PlanDecision(invocations=(), reason=content or "模型判定證據已足夠")

        invocations = []
        for call in tool_calls:
            func = call.get("function", {})
            name = func.get("name", "")
            try:
                args = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            if name:
                # Coerce argument types to match the declared schema —
                # Llama often emits integers as strings ("24" instead of 24).
                spec = spec_by_name.get(name)
                if spec is not None:
                    args = _coerce_args(args, spec.parameters)
                invocations.append(ToolInvocation(tool=name, arguments=args))

        reason = message.get("content", "") or "（Groq 未說明理由）"
        return PlanDecision(invocations=tuple(invocations), reason=reason)

    async def synthesise(
        self,
        asset: Asset,
        evidence: tuple[Evidence, ...],
        question: str = "請分析當前市場狀況",
    ) -> tuple[DraftClaim, ...]:
        """從證據推出判斷。使用 JSON mode + schema 描述。"""
        if not evidence:
            return ()

        from hoyabit_agent.models.schemas import CLAIMS_SCHEMA, describe_schema

        schema_instruction = (
            "\n\n你必須以下列 JSON 格式回應（不要加任何額外文字）：\n"
            f"{describe_schema(CLAIMS_SCHEMA)}\n\n"
            "facet 必須是: technical, positioning, fundamental, sentiment\n"
            "role 必須是: fact, inference, conclusion, counter_evidence, risk, invalidation, watch\n"
            "evidence_ids 必須是上方出現過的真實 ID（如 BNC-SPOT-BTC-4h-RSI14）"
        )

        messages = [
            {"role": "system", "content": SYNTHESIS_SYSTEM + schema_instruction},
            {"role": "user", "content": synthesis_prompt(asset, evidence, question)},
        ]

        body = await self._chat(messages, json_mode=True)
        if body is None:
            return ()

        content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from possible markdown wrapping
            import re
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                try:
                    parsed = json.loads(match.group())
                except json.JSONDecodeError:
                    return ()
            else:
                return ()

        return _parse_claims(parsed)

    async def label(
        self,
        texts: Sequence[str],
        aspect: LabelAspect = LabelAspect.SENTIMENT,
    ) -> tuple[float, ...]:
        """批次打分 — Groq 版本，使用 JSON mode。"""
        if not texts:
            return ()

        from hoyabit_agent.models.prompts import LABEL_SYSTEM

        listing = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
        messages = [
            {"role": "system", "content": LABEL_SYSTEM[aspect] + '\n\n回傳格式：{"scores": [0.3, -0.2, ...]}，數量必須與輸入相同。'},
            {"role": "user", "content": f"共 {len(texts)} 則文本：\n\n{listing}"},
        ]

        body = await self._chat(messages, json_mode=True)
        if body is None:
            return tuple(0.0 for _ in texts)

        content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return tuple(0.0 for _ in texts)

        scores = parsed.get("scores", [])
        if not isinstance(scores, list) or len(scores) != len(texts):
            return tuple(0.0 for _ in texts)

        try:
            return tuple(max(-1.0, min(1.0, float(s))) for s in scores)
        except (TypeError, ValueError):
            return tuple(0.0 for _ in texts)

    async def _chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> dict[str, Any] | None:
        """Call Groq's OpenAI-compatible chat API."""
        import logging
        logger = logging.getLogger("hoyabit_agent.groq")

        url = f"{GROQ_BASE_URL}/chat/completions"
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 4096,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
            # Groq requires the word 'json' to appear in messages when using json_object mode
            has_json_word = any("json" in str(m.get("content", "")).lower() for m in messages)
            if not has_json_word:
                # Append instruction to the last user message
                messages = list(messages)  # don't mutate caller's list
                messages[-1] = {
                    **messages[-1],
                    "content": messages[-1].get("content", "") + "\n\nRespond in valid JSON format.",
                }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await asyncio.wait_for(
                    self._client.post(url, json=payload, headers=headers),
                    timeout=self._timeout_seconds,
                )
            except (TimeoutError, httpx.HTTPError):
                if attempt >= MAX_RETRIES:
                    return None
                await asyncio.sleep(2**attempt)
                continue

            if response.status_code == 200:
                try:
                    res_data = response.json()
                    if isinstance(res_data, dict):
                        return cast(dict[str, Any], res_data)
                    return None
                except ValueError:
                    return None
            if response.status_code == 400:
                # Log the error body for debugging
                error_body = response.text[:500]
                logger.warning(f"Groq 400 Bad Request: {error_body}")
                # If tools caused the 400, retry without tools (fallback to text-only)
                if tools:
                    logger.info("Retrying without tools (text-only planning)")
                    payload.pop("tools", None)
                    payload.pop("tool_choice", None)
                    continue
                return None
            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= MAX_RETRIES:
                    return None
                await asyncio.sleep(2**attempt)
                continue
            logger.warning(f"Groq HTTP {response.status_code}: {response.text[:200]}")
            return None
        return None


def _coerce_args(args: dict[str, Any], schema: Mapping[str, Any]) -> dict[str, Any]:
    """Coerce tool call argument types to match the declared JSON Schema.

    Llama models frequently emit integer/number values as strings even when
    the schema declares them as 'integer' or 'number'. This causes a Groq
    400 error because Groq validates argument types server-side.

    We walk the schema's 'properties' and cast each value to the declared
    Python type. Unknown keys are left as-is; conversion failures fall back
    to the original value.
    """
    properties: dict[str, Any] = schema.get("properties", {}) or {}
    if not properties or not isinstance(args, dict):
        return args

    coerced = dict(args)
    for key, prop in properties.items():
        if key not in coerced:
            continue
        declared_type = prop.get("type", "")
        value = coerced[key]
        try:
            if declared_type == "integer":
                if not isinstance(value, int) or isinstance(value, bool):
                    coerced[key] = int(float(value))  # "24" → 24, "3.0" → 3
            elif declared_type == "number":
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    coerced[key] = float(value)
            elif declared_type == "boolean":
                if not isinstance(value, bool):
                    coerced[key] = str(value).lower() in ("true", "1", "yes")
        except (TypeError, ValueError):
            pass  # Leave the original value; bounded_int/choice will handle it
    return coerced


def _clean_property(prop: dict[str, Any]) -> dict[str, Any]:
    """Strip JSON Schema keywords that Groq/Llama rejects.

    Groq's tool parameter schema only supports: type, description, enum,
    properties, required, items. Keywords like minimum/maximum/exclusiveMinimum
    cause a 400 from Groq's validator.
    """
    # Keywords supported by Groq's function-calling schema validator
    GROQ_ALLOWED = {"type", "description", "enum", "properties", "required", "items"}
    cleaned = {k: v for k, v in prop.items() if k in GROQ_ALLOWED}
    # Recursively clean nested properties
    if "properties" in cleaned:
        cleaned["properties"] = {
            k: _clean_property(v) for k, v in cleaned["properties"].items()
        }
    return cleaned


def _to_openai_tool(spec: ToolSpec) -> dict[str, Any]:
    """Convert ToolSpec to OpenAI function calling format (Groq-compatible).

    Key constraints for Groq/Llama:
    - Parameter schema must NOT contain: minimum, maximum, exclusiveMinimum,
      exclusiveMaximum, multipleOf, pattern, etc. Only type/description/enum
      /properties/required/items are accepted.
    - Every required field listed in 'required' must exist in 'properties'.
    """
    raw_params = dict(spec.parameters)
    raw_props = dict(raw_params.get("properties", {}) or {})

    # Ensure 'asset' field is present so Groq knows which coin to look up
    raw_props.setdefault("asset", {
        "type": "string",
        "enum": ["BTC", "ETH", "SOL", "BNB", "XRP"],
        "description": "Target cryptocurrency asset",
    })

    # Strip unsupported JSON Schema keywords from every property
    clean_props = {k: _clean_property(v) for k, v in raw_props.items()}

    # Build the final parameters object with only Groq-supported top-level keys
    required = list(raw_params.get("required", []) or [])
    if "asset" not in required:
        required = ["asset"] + required

    parameters = {
        "type": "object",
        "properties": clean_props,
        "required": required,
    }

    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": parameters,
        },
    }


def _parse_claims(parsed: Any) -> tuple[DraftClaim, ...]:
    """Parse JSON response into DraftClaim objects."""
    claims_data = parsed if isinstance(parsed, list) else parsed.get("claims", [])
    results = []
    for item in claims_data:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        evidence_ids = tuple(str(eid) for eid in item.get("evidence_ids", []))
        try:
            facet = Facet(str(item.get("facet", "technical")))
        except ValueError:
            facet = Facet.TECHNICAL
        try:
            role = ClaimRole(str(item.get("role", "inference")))
        except ValueError:
            role = ClaimRole.INFERENCE
        results.append(DraftClaim(text=text, evidence_ids=evidence_ids, facet=facet, role=role))
    return tuple(results)


__all__ = ["DEFAULT_GROQ_MODEL", "GROQ_API_KEY_ENV", "GROQ_MODEL_ENV", "GroqProvider"]
