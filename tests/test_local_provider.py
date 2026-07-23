"""地端 provider 測試 —— MockTransport 攔截，不需要真的跑 Ollama。

重點與雲端 provider 相同（接縫 3 的契約），外加地端特有的兩件事：
工具參數是**可能壞掉的 JSON 字串**，以及結構化輸出只能靠提示詞。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from hoyabit_agent.domain import Asset, Facet, LabelAspect
from hoyabit_agent.models.local import (
    BASE_URL_ENV,
    LABOUR_MODEL_ENV,
    MODEL_ENV,
    LocalOpenAIProvider,
)
from hoyabit_agent.seams import GatherContext, ToolSpec
from hoyabit_agent.testing import evidence

SPEC = ToolSpec(
    name="binance_spot",
    description="取得現貨 K 線",
    parameters={"type": "object", "properties": {"interval": {"type": "string"}}},
)


def context() -> GatherContext:
    return GatherContext(asset=Asset.BTC, gap=frozenset(Facet), evidence=(), attempts=())


def responding(
    body: Any, *, status: int = 200, capture: list[httpx.Request] | None = None
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        if isinstance(body, str):
            return httpx.Response(status, content=body.encode())
        return httpx.Response(status, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def chat_reply(
    *, content: str = "", tool_calls: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}]}


def tool_call(name: str, arguments: Any) -> dict[str, Any]:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {"id": "call_1", "type": "function", "function": {"name": name, "arguments": raw}}


def provider(client: httpx.AsyncClient, **kwargs: Any) -> LocalOpenAIProvider:
    kwargs.setdefault("model", "qwen3:8b")
    return LocalOpenAIProvider(client, **kwargs)


# --------------------------------------------------------------------------
# 建構
# --------------------------------------------------------------------------


async def test_without_a_model_the_provider_is_not_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(MODEL_ENV, raising=False)
    async with responding({}) as client:
        assert LocalOpenAIProvider.from_environment(client) is None


async def test_a_model_alone_is_enough_to_enable_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """base URL 有合理預設（Ollama）—— 不該逼使用者多設一個變數。"""
    monkeypatch.setenv(MODEL_ENV, "qwen3:8b")
    monkeypatch.delenv(BASE_URL_ENV, raising=False)
    async with responding({}) as client:
        assert LocalOpenAIProvider.from_environment(client) is not None


async def test_the_base_url_is_configurable_for_other_runtimes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LM Studio 在 1234、llama.cpp 在 8080 —— 同一個 adapter 都要能接。"""
    monkeypatch.setenv(MODEL_ENV, "local-model")
    monkeypatch.setenv(BASE_URL_ENV, "http://localhost:1234/v1")
    captured: list[httpx.Request] = []
    async with responding(chat_reply(content="ok"), capture=captured) as client:
        built = LocalOpenAIProvider.from_environment(client)
        assert built is not None
        await built.plan(context(), (SPEC,))

    assert str(captured[0].url) == "http://localhost:1234/v1/chat/completions"


async def test_the_labour_tier_falls_back_to_the_reasoning_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """地端常常只跑得動一顆模型 —— 務實的預設，不是丟掉分層。"""
    monkeypatch.setenv(MODEL_ENV, "only-model")
    monkeypatch.delenv(LABOUR_MODEL_ENV, raising=False)
    captured: list[httpx.Request] = []
    async with responding(chat_reply(content='{"scores":[0]}'), capture=captured) as client:
        built = LocalOpenAIProvider.from_environment(client)
        assert built is not None
        await built.label(["a"])

    assert json.loads(captured[0].content)["model"] == "only-model"


async def test_the_two_tiers_can_still_be_split_when_both_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MODEL_ENV, "big-local")
    monkeypatch.setenv(LABOUR_MODEL_ENV, "small-local")
    captured: list[httpx.Request] = []
    async with responding(chat_reply(content='{"scores":[0]}'), capture=captured) as client:
        built = LocalOpenAIProvider.from_environment(client)
        assert built is not None
        await built.label(["a"])

    assert json.loads(captured[0].content)["model"] == "small-local"


# --------------------------------------------------------------------------
# 原生 tool calling（OpenAI 格式）
# --------------------------------------------------------------------------


async def test_tool_specs_become_openai_tool_definitions() -> None:
    """一份 ToolSpec 再多一個消費者。"""
    captured: list[httpx.Request] = []
    async with responding(chat_reply(content="ok"), capture=captured) as client:
        await provider(client).plan(context(), (SPEC,))

    sent = json.loads(captured[0].content)
    assert sent["tools"][0]["type"] == "function"
    assert sent["tools"][0]["function"]["name"] == "binance_spot"
    assert sent["tools"][0]["function"]["parameters"] == dict(SPEC.parameters)
    assert sent["tool_choice"] == "auto"


async def test_a_tool_call_becomes_an_invocation_with_its_arguments() -> None:
    reply = chat_reply(
        content="技術面全缺，先取日線。",
        tool_calls=[tool_call("binance_spot", {"interval": "1d", "limit": 250})],
    )
    async with responding(reply) as client:
        decision = await provider(client).plan(context(), (SPEC,))

    assert [inv.tool for inv in decision.invocations] == ["binance_spot"]
    assert decision.invocations[0].arguments == {"interval": "1d", "limit": 250}
    assert "技術面全缺" in decision.reason


async def test_several_tool_calls_become_several_invocations() -> None:
    reply = chat_reply(
        tool_calls=[tool_call("binance_spot", {}), tool_call("crypto_news", {"hours": 24})]
    )
    async with responding(reply) as client:
        decision = await provider(client).plan(context(), (SPEC,))

    assert [inv.tool for inv in decision.invocations] == ["binance_spot", "crypto_news"]


async def test_no_tool_call_means_the_model_wants_to_stop() -> None:
    async with responding(chat_reply(content="四個面都齊了。")) as client:
        decision = await provider(client).plan(context(), (SPEC,))

    assert decision.invocations == ()
    assert "四個面都齊了" in decision.reason


@pytest.mark.parametrize(
    "arguments",
    [
        '{"interval": "1d"',  # 截斷的 JSON
        "not json at all",
        "",
        "   ",
        "[1, 2, 3]",  # 是 JSON 但不是物件
    ],
)
async def test_broken_argument_json_degrades_to_empty_not_a_crash(arguments: str) -> None:
    """小模型常吐出殘缺 JSON —— 那是預期情況，資料源會用預設值跑。"""
    reply = chat_reply(tool_calls=[tool_call("binance_spot", arguments)])
    async with responding(reply) as client:
        decision = await provider(client).plan(context(), (SPEC,))

    assert [inv.tool for inv in decision.invocations] == ["binance_spot"]
    assert decision.invocations[0].arguments == {}


async def test_arguments_given_as_an_object_are_accepted_too() -> None:
    """有些執行環境直接給物件而非 JSON 字串。"""
    reply = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"function": {"name": "binance_spot", "arguments": {"interval": "4h"}}}
                    ]
                }
            }
        ]
    }
    async with responding(reply) as client:
        decision = await provider(client).plan(context(), (SPEC,))

    assert decision.invocations[0].arguments == {"interval": "4h"}


async def test_a_tool_call_without_a_name_is_ignored() -> None:
    reply = {"choices": [{"message": {"tool_calls": [{"function": {"arguments": "{}"}}]}}]}
    async with responding(reply) as client:
        decision = await provider(client).plan(context(), (SPEC,))

    assert decision.invocations == ()


async def test_a_decision_always_carries_a_reason_for_the_trace() -> None:
    reply = chat_reply(tool_calls=[tool_call("binance_spot", {})])
    async with responding(reply) as client:
        decision = await provider(client).plan(context(), (SPEC,))

    assert decision.reason


# --------------------------------------------------------------------------
# 結構化輸出：只能靠提示詞
# --------------------------------------------------------------------------


async def test_the_schema_is_written_into_the_prompt() -> None:
    """地端多半不支援伺服器端 json_schema 強制約束。"""
    captured: list[httpx.Request] = []
    async with responding(chat_reply(content='{"claims":[]}'), capture=captured) as client:
        await provider(client).synthesise(Asset.BTC, (evidence("E1", Facet.TECHNICAL, 0.5),))

    system = json.loads(captured[0].content)["messages"][0]["content"]
    assert "evidence_ids" in system
    assert "只輸出 JSON" in system


async def test_json_mode_is_requested_when_available() -> None:
    captured: list[httpx.Request] = []
    async with responding(chat_reply(content='{"claims":[]}'), capture=captured) as client:
        await provider(client).synthesise(Asset.BTC, (evidence("E1", Facet.TECHNICAL, 0.5),))

    assert json.loads(captured[0].content)["response_format"] == {"type": "json_object"}


async def test_claims_come_back_as_structured_objects() -> None:
    payload = {
        "claims": [{"text": "站上季線", "evidence_ids": ["E1"], "facet": "technical"}]
    }
    async with responding(chat_reply(content=json.dumps(payload))) as client:
        drafts = await provider(client).synthesise(
            Asset.BTC, (evidence("E1", Facet.TECHNICAL, 0.5),)
        )

    assert len(drafts) == 1
    assert drafts[0].text == "站上季線"
    assert drafts[0].evidence_ids == ("E1",)


async def test_json_wrapped_in_prose_or_fences_is_still_parsed() -> None:
    """地端模型常在 JSON 前後夾雜文字或 markdown 圍欄。"""
    payload = {"claims": [{"text": "站上季線", "evidence_ids": ["E1"], "facet": "technical"}]}
    noisy = f"好的，以下是分析結果：\n```json\n{json.dumps(payload)}\n```\n希望有幫助。"
    async with responding(chat_reply(content=noisy)) as client:
        drafts = await provider(client).synthesise(
            Asset.BTC, (evidence("E1", Facet.TECHNICAL, 0.5),)
        )

    assert len(drafts) == 1


async def test_a_malformed_claim_is_dropped_whole() -> None:
    payload = {
        "claims": [
            {"text": "壞的", "evidence_ids": ["E1"], "facet": "不存在"},
            {"text": "好的", "evidence_ids": ["E1"], "facet": "technical"},
        ]
    }
    async with responding(chat_reply(content=json.dumps(payload))) as client:
        drafts = await provider(client).synthesise(
            Asset.BTC, (evidence("E1", Facet.TECHNICAL, 0.5),)
        )

    assert [d.text for d in drafts] == ["好的"]


async def test_unparseable_output_degrades_to_no_claims() -> None:
    async with responding(chat_reply(content="我不太確定該怎麼回答")) as client:
        drafts = await provider(client).synthesise(
            Asset.BTC, (evidence("E1", Facet.TECHNICAL, 0.5),)
        )

    assert drafts == ()


# --------------------------------------------------------------------------
# 打分
# --------------------------------------------------------------------------


async def test_scores_come_back_one_per_text() -> None:
    async with responding(chat_reply(content='{"scores":[0.8,-0.5,0.0]}')) as client:
        assert await provider(client).label(["a", "b", "c"]) == (0.8, -0.5, 0.0)


async def test_all_texts_go_out_in_a_single_request() -> None:
    captured: list[httpx.Request] = []
    body = chat_reply(content=json.dumps({"scores": [0.0] * 30}))
    async with responding(body, capture=captured) as client:
        await provider(client).label([f"t{i}" for i in range(30)])

    assert len(captured) == 1


async def test_the_aspect_changes_the_system_prompt() -> None:
    captured: list[httpx.Request] = []
    async with responding(chat_reply(content='{"scores":[0]}'), capture=captured) as client:
        p = provider(client)
        await p.label(["a"], LabelAspect.SENTIMENT)
        await p.label(["a"], LabelAspect.FUNDAMENTAL)

    first = json.loads(captured[0].content)["messages"][0]["content"]
    second = json.loads(captured[1].content)["messages"][0]["content"]
    assert first != second
    assert "輿論傾向" in first
    assert "實質影響" in second


@pytest.mark.parametrize(
    "content", ['{"scores":[0.5]}', '{"scores":"nope"}', "{}", "garbage"]
)
async def test_a_bad_score_payload_degrades_to_neutral(content: str) -> None:
    async with responding(chat_reply(content=content)) as client:
        assert await provider(client).label(["a", "b"]) == (0.0, 0.0)


async def test_scores_outside_the_range_are_clamped() -> None:
    async with responding(chat_reply(content='{"scores":[5,-9]}')) as client:
        assert await provider(client).label(["a", "b"]) == (1.0, -1.0)


async def test_an_empty_batch_needs_no_request() -> None:
    captured: list[httpx.Request] = []
    async with responding(chat_reply(), capture=captured) as client:
        assert await provider(client).label([]) == ()

    assert captured == []


# --------------------------------------------------------------------------
# 降級
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 404, 500, 503])
async def test_an_http_error_degrades_every_capability(status: int) -> None:
    async with responding({}, status=status) as client:
        p = provider(client)
        assert (await p.plan(context(), (SPEC,))).invocations == ()
        assert await p.synthesise(Asset.BTC, (evidence("E1", Facet.TECHNICAL, 0.5),)) == ()
        assert await p.label(["a"]) == (0.0,)


async def test_a_dead_endpoint_degrades_rather_than_raising() -> None:
    """最常見的地端故障：忘了啟動 Ollama。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        p = provider(client)
        assert (await p.plan(context(), (SPEC,))).invocations == ()
        assert await p.label(["a"]) == (0.0,)


async def test_a_hanging_model_times_out() -> None:
    """地端推論慢，但仍必須有上限 —— 否則 15 分鐘預算破功。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")  # pragma: no cover

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        p = provider(client, timeout_seconds=0.05)
        assert (await p.plan(context(), (SPEC,))).invocations == ()


@pytest.mark.parametrize(
    "body", [{}, {"choices": []}, {"choices": [{}]}, {"choices": [{"message": None}]}]
)
async def test_a_response_missing_the_expected_shape_degrades(body: Any) -> None:
    async with responding(body) as client:
        decision = await provider(client).plan(context(), (SPEC,))

    assert decision.invocations == ()
    assert decision.reason


async def test_an_api_key_is_sent_only_when_configured() -> None:
    captured: list[httpx.Request] = []
    async with responding(chat_reply(content="ok"), capture=captured) as client:
        await provider(client).plan(context(), (SPEC,))
        await provider(client, api_key="secret").plan(context(), (SPEC,))

    assert "authorization" not in captured[0].headers
    assert captured[1].headers["authorization"] == "Bearer secret"
