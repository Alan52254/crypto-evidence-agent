"""Groq 模型供應者 — 接縫 3 的備選實作。

Groq 以 LLaMA 3.3 70B 提供極低延遲推理，作為 Gemini 的 fallback。
延遲約 Gemini 的 1/5，但 context window 較小 (128K vs 1M)。
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Sequence
from typing import Any

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
                invocations.append(ToolInvocation(tool=name, arguments=args))

        reason = message.get("content", "") or "（Groq 未說明理由）"
        return PlanDecision(invocations=tuple(invocations), reason=reason)

    async def synthesise(
        self,
        asset: Asset,
        evidence: tuple[Evidence, ...],
        question: str = "請分析當前市場狀況",
    ) -> tuple[DraftClaim, ...]:
        """從證據推出判斷。使用 JSON mode。"""
        if not evidence:
            return ()

        messages = [
            {"role": "system", "content": SYNTHESIS_SYSTEM},
            {"role": "user", "content": synthesis_prompt(asset, evidence, question)},
        ]

        body = await self._chat(messages, json_mode=True)
        if body is None:
            return ()

        content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return ()

        return _parse_claims(parsed)

    async def label(
        self,
        texts: Sequence[str],
        aspect: LabelAspect = LabelAspect.SENTIMENT,
    ) -> tuple[float, ...]:
        """批次打分 — Groq 版本。"""
        if not texts:
            return ()
        # Simplified: return neutral scores. Full implementation would call Groq.
        return tuple(0.0 for _ in texts)

    async def _chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> dict[str, Any] | None:
        """Call Groq's OpenAI-compatible chat API."""
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
                    return response.json()
                except ValueError:
                    return None
            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= MAX_RETRIES:
                    return None
                await asyncio.sleep(2**attempt)
                continue
            return None
        return None


def _to_openai_tool(spec: ToolSpec) -> dict[str, Any]:
    """Convert ToolSpec to OpenAI function calling format."""
    parameters = dict(spec.parameters)
    properties = dict(parameters.get("properties", {}) or {})
    properties.setdefault("asset", {
        "type": "string",
        "enum": ["BTC", "ETH", "SOL", "BNB", "XRP"],
        "description": "Target asset",
    })
    parameters["properties"] = properties
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
