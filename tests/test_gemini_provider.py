"""Gemini 模型供應者測試 —— 以 httpx MockTransport 攔截，不碰真實 API。

重點是接縫 3 的不變式：輸出必定符合結構、失敗一律降級不中斷、
金鑰不外洩。模型「真的聰不聰明」不是單元測試能回答的問題，
那是 ticket 11 的評估基準要量的。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from hoyabit_agent.domain import Asset, Facet
from hoyabit_agent.models.gemini import (
    API_KEY_ENV,
    LABOUR_MODEL_ENV,
    MODEL_ENV,
    GeminiProvider,
)
from hoyabit_agent.seams import GatherContext, ToolAttempt, ToolSpec
from hoyabit_agent.testing import evidence

SPEC = ToolSpec(
    name="binance_spot",
    description="取得現貨 K 線",
    parameters={"type": "object", "properties": {"interval": {"type": "string"}}},
)


def context(gap: frozenset[Facet] | None = None, **kwargs: Any) -> GatherContext:
    return GatherContext(
        asset=Asset.BTC,
        gap=gap if gap is not None else frozenset(Facet),
        evidence=kwargs.get("evidence", ()),
        attempts=kwargs.get("attempts", ()),
    )


def responding(
    body: Any,
    *,
    status: int = 200,
    capture: list[httpx.Request] | None = None,
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        if isinstance(body, str):
            return httpx.Response(status, content=body.encode())
        return httpx.Response(status, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def gemini_reply(*parts: dict[str, Any]) -> dict[str, Any]:
    return {"candidates": [{"content": {"parts": list(parts)}}]}


def json_reply(payload: Any) -> dict[str, Any]:
    return gemini_reply({"text": json.dumps(payload)})


# --------------------------------------------------------------------------
# 建構
# --------------------------------------------------------------------------


async def test_without_an_api_key_the_provider_is_not_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """沒有金鑰時回傳 None，讓呼叫端明確地降級，而不是在執行到一半才爆。"""
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    async with responding({}) as client:
        assert GeminiProvider.from_environment(client) is None


async def test_a_blank_api_key_counts_as_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, "   ")
    async with responding({}) as client:
        assert GeminiProvider.from_environment(client) is None


async def test_environment_rejects_a_non_architectural_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "k")
    monkeypatch.setenv(MODEL_ENV, "gemini-3-pro")
    async with responding({}) as client:
        with pytest.raises(ValueError, match="gemini-3.6-flash"):
            GeminiProvider.from_environment(client)


async def test_the_two_tiers_use_two_different_models() -> None:
    """決策 #6：混用同一顆模型會吃光壁鐘預算並撞速率限制。"""
    captured: list[httpx.Request] = []
    async with responding(json_reply({"scores": [0.0], "claims": []}), capture=captured) as client:
        provider = GeminiProvider(client, "k", model="reasoning-model", labour_model="labour-model")
        await provider.synthesise(Asset.BTC, (evidence("E1", Facet.TECHNICAL, 0.5),))
        await provider.label(["a"])

    assert "reasoning-model:generateContent" in str(captured[0].url)
    assert "labour-model:generateContent" in str(captured[1].url)


async def test_planning_uses_the_reasoning_tier() -> None:
    captured: list[httpx.Request] = []
    async with responding(gemini_reply({"text": "ok"}), capture=captured) as client:
        await GeminiProvider(
            client, "k", model="reasoning-model", labour_model="labour-model"
        ).plan(context(), (SPEC,))

    assert "reasoning-model:generateContent" in str(captured[0].url)


async def test_environment_rejects_a_separate_labour_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "k")
    monkeypatch.setenv(MODEL_ENV, "gemini-3.6-flash")
    monkeypatch.setenv(LABOUR_MODEL_ENV, "small")
    async with responding({}) as client:
        with pytest.raises(ValueError, match="gemini-3.6-flash"):
            GeminiProvider.from_environment(client)


async def test_a_hanging_model_times_out_instead_of_blowing_the_budget() -> None:
    """「永不因逾時而失敗」的破口 —— 掛住的 provider 會讓回合超過 15 分鐘上限。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")  # pragma: no cover

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GeminiProvider(client, "k", timeout_seconds=0.05)
        decision = await provider.plan(context(), (SPEC,))
        assert decision.invocations == ()
        assert await provider.label(["a"]) == (0.0,)


# --------------------------------------------------------------------------
# 規劃：原生 function calling
# --------------------------------------------------------------------------


async def test_tool_specs_become_function_declarations() -> None:
    """一份規格三個消費者 —— 模型看到的就是 ToolSpec 本身。"""
    captured: list[httpx.Request] = []
    async with responding(gemini_reply({"text": "ok"}), capture=captured) as client:
        await GeminiProvider(client, "k").plan(context(), (SPEC,))

    sent = json.loads(captured[0].content)
    declarations = sent["tools"][0]["functionDeclarations"]
    assert declarations[0]["name"] == "binance_spot"
    assert declarations[0]["description"] == SPEC.description
    assert declarations[0]["parameters"]["properties"]["interval"] == {"type": "string"}
    assert declarations[0]["parameters"]["properties"]["asset"]["enum"] == [
        "BTC",
        "ETH",
        "SOL",
        "BNB",
        "XRP",
    ]


async def test_a_function_call_becomes_an_invocation_with_its_arguments() -> None:
    reply = gemini_reply(
        {"text": "技術面全缺，先取日線。"},
        {"functionCall": {"name": "binance_spot", "args": {"interval": "1d", "limit": 250}}},
    )
    async with responding(reply) as client:
        decision = await GeminiProvider(client, "k").plan(context(), (SPEC,))

    assert [inv.tool for inv in decision.invocations] == ["binance_spot"]
    assert decision.invocations[0].arguments == {"interval": "1d", "limit": 250}
    assert "技術面全缺" in decision.reason


async def test_several_function_calls_become_several_invocations() -> None:
    reply = gemini_reply(
        {"functionCall": {"name": "binance_spot", "args": {}}},
        {"functionCall": {"name": "crypto_news", "args": {"hours": 24}}},
    )
    async with responding(reply) as client:
        decision = await GeminiProvider(client, "k").plan(context(), (SPEC,))

    assert [inv.tool for inv in decision.invocations] == ["binance_spot", "crypto_news"]


async def test_no_function_call_means_the_model_wants_to_stop() -> None:
    reply = gemini_reply({"text": "四個面都齊了，不需要再蒐集。"})
    async with responding(reply) as client:
        decision = await GeminiProvider(client, "k").plan(context(), (SPEC,))

    assert decision.invocations == ()
    assert "不需要再蒐集" in decision.reason


async def test_a_call_without_a_name_is_ignored_rather_than_crashing() -> None:
    reply = gemini_reply({"functionCall": {"args": {"interval": "1d"}}})
    async with responding(reply) as client:
        decision = await GeminiProvider(client, "k").plan(context(), (SPEC,))

    assert decision.invocations == ()


async def test_missing_arguments_default_to_empty_not_none() -> None:
    reply = gemini_reply({"functionCall": {"name": "binance_spot"}})
    async with responding(reply) as client:
        decision = await GeminiProvider(client, "k").plan(context(), (SPEC,))

    assert decision.invocations[0].arguments == {}


async def test_a_decision_always_carries_a_reason_for_the_trace() -> None:
    """理由會原樣進推論軌跡，不能是空字串。"""
    reply = gemini_reply({"functionCall": {"name": "binance_spot", "args": {}}})
    async with responding(reply) as client:
        decision = await GeminiProvider(client, "k").plan(context(), (SPEC,))

    assert decision.reason


async def test_with_no_tools_available_the_model_is_not_even_called() -> None:
    captured: list[httpx.Request] = []
    async with responding(gemini_reply(), capture=captured) as client:
        decision = await GeminiProvider(client, "k").plan(context(), ())

    assert decision.invocations == ()
    assert captured == []


async def test_previous_attempts_are_shown_to_the_model() -> None:
    captured: list[httpx.Request] = []
    attempts = (ToolAttempt("crypto_news", {"hours": 24}, "unavailable"),)
    async with responding(gemini_reply({"text": "ok"}), capture=captured) as client:
        await GeminiProvider(client, "k").plan(context(attempts=attempts), (SPEC,))

    sent = json.loads(captured[0].content)
    prompt = sent["contents"][0]["parts"][0]["text"]
    assert "crypto_news" in prompt
    assert "unavailable" in prompt


async def test_the_remaining_gap_is_shown_to_the_model() -> None:
    captured: list[httpx.Request] = []
    async with responding(gemini_reply({"text": "ok"}), capture=captured) as client:
        await GeminiProvider(client, "k").plan(context(frozenset({Facet.SENTIMENT})), (SPEC,))

    prompt = json.loads(captured[0].content)["contents"][0]["parts"][0]["text"]
    assert "sentiment" in prompt


# --------------------------------------------------------------------------
# 組裝：結構化輸出
# --------------------------------------------------------------------------


async def test_claims_come_back_as_structured_objects_not_prose() -> None:
    reply = json_reply(
        {
            "claims": [
                {
                    "text": "日線站上季線且量能放大",
                    "evidence_ids": ["E1", "E2"],
                    "facet": "technical",
                }
            ]
        }
    )
    async with responding(reply) as client:
        drafts = await GeminiProvider(client, "k").synthesise(
            Asset.BTC, (evidence("E1", Facet.TECHNICAL, 0.5),)
        )

    assert len(drafts) == 1
    assert drafts[0].text == "日線站上季線且量能放大"
    assert drafts[0].evidence_ids == ("E1", "E2")
    assert drafts[0].facet is Facet.TECHNICAL


async def test_a_json_schema_is_enforced_on_the_response() -> None:
    captured: list[httpx.Request] = []
    async with responding(json_reply({"claims": []}), capture=captured) as client:
        await GeminiProvider(client, "k").synthesise(
            Asset.BTC, (evidence("E1", Facet.TECHNICAL, 0.5),)
        )

    config = json.loads(captured[0].content)["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert "responseSchema" in config


async def test_the_evidence_ids_and_source_text_are_shown_to_the_model() -> None:
    captured: list[httpx.Request] = []
    async with responding(json_reply({"claims": []}), capture=captured) as client:
        await GeminiProvider(client, "k").synthesise(
            Asset.BTC, (evidence("E1", Facet.TECHNICAL, 0.5, text="收盤站上季線"),)
        )

    prompt = json.loads(captured[0].content)["contents"][0]["parts"][0]["text"]
    assert "[E1]" in prompt
    assert "收盤站上季線" in prompt


@pytest.mark.parametrize(
    "raw",
    [
        {"evidence_ids": ["E1"], "facet": "technical"},  # 缺 text
        {"text": "   ", "evidence_ids": ["E1"], "facet": "technical"},  # 空白 text
        {"text": "有話要說", "evidence_ids": "E1", "facet": "technical"},  # ids 不是陣列
        {"text": "有話要說", "evidence_ids": ["E1"], "facet": "不存在的面"},
        {"text": "有話要說", "evidence_ids": ["E1"]},  # 缺 facet
        "根本不是物件",
    ],
)
async def test_a_malformed_claim_is_dropped_whole(raw: Any) -> None:
    """半個判斷比沒有判斷更危險 —— 不做「盡量救回來」。"""
    async with responding(json_reply({"claims": [raw]})) as client:
        drafts = await GeminiProvider(client, "k").synthesise(
            Asset.BTC, (evidence("E1", Facet.TECHNICAL, 0.5),)
        )

    assert drafts == ()


async def test_good_claims_survive_alongside_malformed_ones() -> None:
    reply = json_reply(
        {
            "claims": [
                {"text": "壞的", "evidence_ids": ["E1"], "facet": "不存在"},
                {"text": "好的", "evidence_ids": ["E1"], "facet": "technical"},
            ]
        }
    )
    async with responding(reply) as client:
        drafts = await GeminiProvider(client, "k").synthesise(
            Asset.BTC, (evidence("E1", Facet.TECHNICAL, 0.5),)
        )

    assert [d.text for d in drafts] == ["好的"]


async def test_with_no_evidence_the_model_is_not_even_called() -> None:
    """沒有證據就不可能有掛載證據的判斷 —— 省一次呼叫。"""
    captured: list[httpx.Request] = []
    async with responding(json_reply({"claims": []}), capture=captured) as client:
        assert await GeminiProvider(client, "k").synthesise(Asset.BTC, ()) == ()

    assert captured == []


# --------------------------------------------------------------------------
# 打分：批次
# --------------------------------------------------------------------------


async def test_scores_come_back_one_per_text() -> None:
    async with responding(json_reply({"scores": [0.8, -0.5, 0.0]})) as client:
        scores = await GeminiProvider(client, "k").label(["a", "b", "c"])

    assert scores == (0.8, -0.5, 0.0)


async def test_all_texts_go_out_in_a_single_request() -> None:
    """免費層是 10 RPM —— 逐則呼叫會吃掉壁鐘預算。"""
    captured: list[httpx.Request] = []
    async with responding(json_reply({"scores": [0.0] * 30}), capture=captured) as client:
        await GeminiProvider(client, "k").label([f"text {index}" for index in range(30)])

    assert len(captured) == 1


async def test_scores_outside_the_range_are_clamped() -> None:
    async with responding(json_reply({"scores": [5.0, -9.0]})) as client:
        scores = await GeminiProvider(client, "k").label(["a", "b"])

    assert scores == (1.0, -1.0)


@pytest.mark.parametrize(
    "payload",
    [
        {"scores": [0.5]},  # 數量對不上
        {"scores": "not a list"},
        {"scores": [0.5, "abc"]},
        {},
    ],
)
async def test_a_bad_score_payload_degrades_to_neutral(payload: Any) -> None:
    async with responding(json_reply(payload)) as client:
        assert await GeminiProvider(client, "k").label(["a", "b"]) == (0.0, 0.0)


async def test_an_empty_batch_needs_no_request() -> None:
    captured: list[httpx.Request] = []
    async with responding(json_reply({"scores": []}), capture=captured) as client:
        assert await GeminiProvider(client, "k").label([]) == ()

    assert captured == []


# --------------------------------------------------------------------------
# 降級：任何失敗都不中斷分析回合
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
async def test_an_http_error_degrades_every_capability(status: int) -> None:
    async with responding({}, status=status) as client:
        provider = GeminiProvider(client, "k")
        assert (await provider.plan(context(), (SPEC,))).invocations == ()
        assert await provider.synthesise(Asset.BTC, (evidence("E1", Facet.TECHNICAL, 0.5),)) == ()
        assert await provider.label(["a"]) == (0.0,)


async def test_a_network_error_degrades_rather_than_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GeminiProvider(client, "k")
        assert (await provider.plan(context(), (SPEC,))).invocations == ()
        assert await provider.label(["a"]) == (0.0,)


@pytest.mark.parametrize(
    "body",
    [{}, {"candidates": []}, {"candidates": [{}]}, {"candidates": [{"content": {}}]}],
)
async def test_a_response_missing_the_expected_shape_degrades(body: Any) -> None:
    async with responding(body) as client:
        decision = await GeminiProvider(client, "k").plan(context(), (SPEC,))

    assert decision.invocations == ()
    assert decision.reason


async def test_invalid_json_in_a_structured_response_degrades() -> None:
    async with responding(gemini_reply({"text": "{ not json"})) as client:
        drafts = await GeminiProvider(client, "k").synthesise(
            Asset.BTC, (evidence("E1", Facet.TECHNICAL, 0.5),)
        )

    assert drafts == ()


async def test_a_non_json_body_degrades() -> None:
    async with responding("<html>502 Bad Gateway</html>") as client:
        assert await GeminiProvider(client, "k").label(["a"]) == (0.0,)


# --------------------------------------------------------------------------
# 金鑰
# --------------------------------------------------------------------------


async def test_the_api_key_travels_as_a_query_parameter_not_in_the_body() -> None:
    captured: list[httpx.Request] = []
    async with responding(gemini_reply({"text": "ok"}), capture=captured) as client:
        await GeminiProvider(client, "secret-key").plan(context(), (SPEC,))

    assert captured[0].url.params["key"] == "secret-key"
    assert "secret-key" not in captured[0].content.decode()


async def test_the_api_key_never_appears_in_what_the_provider_returns() -> None:
    async with responding({}, status=401) as client:
        decision = await GeminiProvider(client, "secret-key").plan(context(), (SPEC,))

    assert "secret-key" not in decision.reason


async def test_transient_quota_limit_retries_then_returns_the_model_result() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "0"},
                json={"error": {"message": "quota window", "details": []}},
            )
        return httpx.Response(200, json=json_reply({"claims": []}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        drafts = await GeminiProvider(client, "k").synthesise(
            Asset.BTC, (evidence("E1", Facet.TECHNICAL, 0.5),)
        )

    assert drafts == ()
    assert calls == 2