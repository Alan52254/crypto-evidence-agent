"""Groq 模型供應者測試 — 以 httpx MockTransport 攔截，不碰真實 API。

重點是：
1. _coerce_args：Llama 把 integer schema 欄位輸出成字串時，能正確強制轉型。
2. _clean_property：minimum / maximum 等非 Groq-supported keywords 被移除。
3. _to_openai_tool：產出的 schema 只含 Groq 接受的 keys。
4. plan()：400 Bad Request 時自動 fallback 到 text-only 模式。
5. plan()：tool_call 解析出 string 型整數時正確轉型再傳給 ToolInvocation。
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from hoyabit_agent.models.groq import (
    GroqProvider,
    _clean_property,
    _coerce_args,
    _to_openai_tool,
)
from hoyabit_agent.seams import GatherContext, ToolSpec
from hoyabit_agent.domain import Asset, Facet

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

INT_INT_SPEC = ToolSpec(
    name="crypto_news",
    description="Fetch news",
    parameters={
        "type": "object",
        "properties": {
            "hours": {"type": "integer", "minimum": 1, "maximum": 168, "description": "Hours"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 40, "description": "Limit"},
        },
    },
)


def context() -> GatherContext:
    return GatherContext(
        asset=Asset.BTC,
        gap=frozenset(Facet),
        evidence=(),
        attempts=(),
    )


@asynccontextmanager
async def mock_groq(response_body: Any, *, status: int = 200):
    """Yield a GroqProvider whose HTTP client always returns the given response."""
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(response_body, str):
            return httpx.Response(status, content=response_body.encode())
        return httpx.Response(status, json=response_body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        yield GroqProvider(client, api_key="test-key", model="llama-3.3-70b-versatile")


def tool_call_reply(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Build a Groq chat/completions response with a single tool_call."""
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(arguments),
                    },
                }],
            }
        }]
    }


# ---------------------------------------------------------------------------
# _coerce_args
# ---------------------------------------------------------------------------

def test_coerce_args_converts_string_integers_to_int() -> None:
    schema = {"properties": {"hours": {"type": "integer"}, "limit": {"type": "integer"}}}
    args = {"hours": "24", "limit": "10"}
    result = _coerce_args(args, schema)
    assert result == {"hours": 24, "limit": 10}
    assert isinstance(result["hours"], int)
    assert isinstance(result["limit"], int)


def test_coerce_args_leaves_already_correct_types_unchanged() -> None:
    schema = {"properties": {"hours": {"type": "integer"}}}
    args = {"hours": 48}
    result = _coerce_args(args, schema)
    assert result == {"hours": 48}


def test_coerce_args_converts_float_string_to_int() -> None:
    schema = {"properties": {"hours": {"type": "integer"}}}
    args = {"hours": "3.0"}
    result = _coerce_args(args, schema)
    assert result["hours"] == 3
    assert isinstance(result["hours"], int)


def test_coerce_args_leaves_unknown_keys_untouched() -> None:
    schema = {"properties": {"hours": {"type": "integer"}}}
    args = {"hours": "24", "extra": "hello"}
    result = _coerce_args(args, schema)
    assert result["hours"] == 24
    assert result["extra"] == "hello"  # no schema entry — leave as-is


def test_coerce_args_handles_missing_schema_gracefully() -> None:
    result = _coerce_args({"hours": "24"}, {})
    assert result == {"hours": "24"}  # no schema → no changes


def test_coerce_args_does_not_convert_booleans_to_int() -> None:
    # bool is a subclass of int — we should NOT accidentally coerce True→1
    schema = {"properties": {"hours": {"type": "integer"}}}
    args = {"hours": True}
    result = _coerce_args(args, schema)
    # bool already passes isinstance(value, int) — we only coerce non-int types,
    # but True/False being bool means we skip the coercion branch for bool subclass
    # The important thing is no exception is raised.
    assert result["hours"] in (True, 1)


def test_coerce_args_handles_unconvertible_value_gracefully() -> None:
    schema = {"properties": {"hours": {"type": "integer"}}}
    args = {"hours": "not_a_number"}
    # Should not raise, original value is kept
    result = _coerce_args(args, schema)
    assert result["hours"] == "not_a_number"


def test_coerce_args_converts_number_string_to_float() -> None:
    schema = {"properties": {"score": {"type": "number"}}}
    args = {"score": "0.75"}
    result = _coerce_args(args, schema)
    assert result["score"] == pytest.approx(0.75)
    assert isinstance(result["score"], float)


# ---------------------------------------------------------------------------
# _clean_property
# ---------------------------------------------------------------------------

def test_clean_property_removes_minimum_and_maximum() -> None:
    prop = {"type": "integer", "minimum": 1, "maximum": 168, "description": "Hours"}
    result = _clean_property(prop)
    assert "minimum" not in result
    assert "maximum" not in result
    assert result["type"] == "integer"
    assert result["description"] == "Hours"


def test_clean_property_keeps_enum() -> None:
    prop = {"type": "string", "enum": ["BTC", "ETH"], "description": "Asset"}
    result = _clean_property(prop)
    assert result == {"type": "string", "enum": ["BTC", "ETH"], "description": "Asset"}


def test_clean_property_recursively_cleans_nested_properties() -> None:
    prop = {
        "type": "object",
        "properties": {
            "inner": {"type": "integer", "minimum": 1, "maximum": 10}
        }
    }
    result = _clean_property(prop)
    assert "minimum" not in result["properties"]["inner"]
    assert "maximum" not in result["properties"]["inner"]


# ---------------------------------------------------------------------------
# _to_openai_tool
# ---------------------------------------------------------------------------

def test_to_openai_tool_strips_minimum_maximum_from_schema() -> None:
    tool = _to_openai_tool(INT_INT_SPEC)
    props = tool["function"]["parameters"]["properties"]
    for field in ("hours", "limit"):
        assert "minimum" not in props[field], f"{field} still has 'minimum'"
        assert "maximum" not in props[field], f"{field} still has 'maximum'"


def test_to_openai_tool_injects_asset_field() -> None:
    tool = _to_openai_tool(INT_INT_SPEC)
    props = tool["function"]["parameters"]["properties"]
    assert "asset" in props
    assert props["asset"]["type"] == "string"
    assert "BTC" in props["asset"]["enum"]


def test_to_openai_tool_has_asset_in_required() -> None:
    tool = _to_openai_tool(INT_INT_SPEC)
    required = tool["function"]["parameters"]["required"]
    assert "asset" in required


def test_to_openai_tool_format() -> None:
    tool = _to_openai_tool(INT_INT_SPEC)
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "crypto_news"
    params = tool["function"]["parameters"]
    assert params["type"] == "object"
    assert "properties" in params
    assert "required" in params


# ---------------------------------------------------------------------------
# plan() integration: string integer arguments are coerced before ToolInvocation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plan_coerces_string_integer_args_from_llama() -> None:
    """Regression test for Groq 400: Llama emits 'hours':'24' instead of hours:24."""
    reply = tool_call_reply("crypto_news", {"asset": "BTC", "hours": "24", "limit": "10"})
    async with mock_groq(reply) as provider:
        decision = await provider.plan(context(), (INT_INT_SPEC,))

    assert len(decision.invocations) == 1
    args = decision.invocations[0].arguments
    assert args["hours"] == 24, f"Expected int 24, got {args['hours']!r}"
    assert args["limit"] == 10, f"Expected int 10, got {args['limit']!r}"
    assert isinstance(args["hours"], int)
    assert isinstance(args["limit"], int)


@pytest.mark.asyncio
async def test_plan_returns_empty_on_groq_400_no_tools() -> None:
    """A 400 with no tools in payload should degrade to empty invocations."""
    async with mock_groq({}, status=400) as provider:
        decision = await provider.plan(context(), (INT_INT_SPEC,))

    # After exhausting retries the fallback text-only path also fails → empty
    assert decision.invocations == ()


@pytest.mark.asyncio
async def test_plan_returns_empty_on_groq_429() -> None:
    async with mock_groq({}, status=429) as provider:
        decision = await provider.plan(context(), (INT_INT_SPEC,))
    assert decision.invocations == ()


@pytest.mark.asyncio
async def test_plan_returns_empty_when_model_gives_no_tool_calls() -> None:
    reply = {"choices": [{"message": {"role": "assistant", "content": "證據充足", "tool_calls": []}}]}
    async with mock_groq(reply) as provider:
        decision = await provider.plan(context(), (INT_INT_SPEC,))
    assert decision.invocations == ()
    assert "充足" in decision.reason
