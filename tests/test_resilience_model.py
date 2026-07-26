"""ResilientModelAdapter 單元與容錯測試 — 以 TDD 驗證 API 429 及失敗切換。"""

from __future__ import annotations

import asyncio
from typing import Sequence
import pytest

from hoyabit_agent.domain import Asset, ClaimRole, DraftClaim, Evidence, Facet, LabelAspect
from hoyabit_agent.models.resilience import ResilientModelAdapter
from hoyabit_agent.seams import GatherContext, ModelProvider, PlanDecision, ToolInvocation, ToolSpec

# ---------------------------------------------------------------------------
# Stub / Mock Providers for TDD
# ---------------------------------------------------------------------------

class SucceedingProvider:
    """始終成功的 Provider 樁物件。"""

    def __init__(self, name: str = "primary") -> None:
        self.name = name

    async def plan(self, context: GatherContext, tools: tuple[ToolSpec, ...]) -> PlanDecision:
        return PlanDecision(
            invocations=(ToolInvocation("binance_spot", {"interval": "4h"}),),
            reason=f"{self.name} success",
        )

    async def synthesise(
        self,
        asset: Asset,
        evidence: tuple[Evidence, ...],
        question: str = "請分析當前市場狀況",
    ) -> tuple[DraftClaim, ...]:
        return (DraftClaim("看多趨勢", ("E1",), Facet.TECHNICAL, ClaimRole.INFERENCE),)

    async def label(
        self,
        texts: Sequence[str],
        aspect: LabelAspect = LabelAspect.SENTIMENT,
    ) -> tuple[float, ...]:
        return tuple(0.8 for _ in texts)


class FailingProvider:
    """始終失敗（拋出 429 或回傳空/異常結果）的 Provider 樁物件。"""

    def __init__(self, reason: str = "HTTP 429 Too Many Requests") -> None:
        self.reason = reason

    async def plan(self, context: GatherContext, tools: tuple[ToolSpec, ...]) -> PlanDecision:
        return PlanDecision(invocations=(), reason=self.reason)

    async def synthesise(
        self,
        asset: Asset,
        evidence: tuple[Evidence, ...],
        question: str = "請分析當前市場狀況",
    ) -> tuple[DraftClaim, ...]:
        return ()

    async def label(
        self,
        texts: Sequence[str],
        aspect: LabelAspect = LabelAspect.SENTIMENT,
    ) -> tuple[float, ...]:
        return tuple(0.0 for _ in texts)


class ExceptionThrowingProvider:
    """呼叫時直接拋出 exceptions (例如 Timeout / Connection Refused) 的樁物件。"""

    async def plan(self, context: GatherContext, tools: tuple[ToolSpec, ...]) -> PlanDecision:
        raise RuntimeError("Connection refused by primary LLM")

    async def synthesise(
        self,
        asset: Asset,
        evidence: tuple[Evidence, ...],
        question: str = "請分析當前市場狀況",
    ) -> tuple[DraftClaim, ...]:
        raise TimeoutError("LLM response timeout")

    async def label(
        self,
        texts: Sequence[str],
        aspect: LabelAspect = LabelAspect.SENTIMENT,
    ) -> tuple[float, ...]:
        raise RuntimeError("API error")


def dummy_context() -> GatherContext:
    return GatherContext(asset=Asset.BTC, gap=frozenset(Facet), evidence=(), attempts=())


# ---------------------------------------------------------------------------
# Unit & Integration Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_primary_provider_succeeds_directly() -> None:
    primary = SucceedingProvider("gemini")
    secondary = SucceedingProvider("groq")
    adapter = ResilientModelAdapter(primary, secondary)

    decision = await adapter.plan(dummy_context(), ())
    assert decision.invocations[0].tool == "binance_spot"
    assert "gemini" in decision.reason


@pytest.mark.asyncio
async def test_primary_fails_with_429_switches_to_secondary() -> None:
    primary = FailingProvider("Gemini 429 Too Many Requests")
    secondary = SucceedingProvider("groq")
    adapter = ResilientModelAdapter(primary, secondary)

    decision = await adapter.plan(dummy_context(), ())
    assert len(decision.invocations) == 1
    assert decision.invocations[0].tool == "binance_spot"
    assert "groq" in decision.reason


@pytest.mark.asyncio
async def test_primary_throws_exception_switches_to_secondary() -> None:
    primary = ExceptionThrowingProvider()
    secondary = SucceedingProvider("groq")
    adapter = ResilientModelAdapter(primary, secondary)

    decision = await adapter.plan(dummy_context(), ())
    assert len(decision.invocations) == 1
    assert "groq" in decision.reason

    claims = await adapter.synthesise(Asset.BTC, ())
    assert len(claims) == 1
    assert claims[0].text == "看多趨勢"


@pytest.mark.asyncio
async def test_both_providers_fail_degrades_gracefully() -> None:
    primary = FailingProvider("Gemini 429")
    secondary = FailingProvider("Groq 429")
    adapter = ResilientModelAdapter(primary, secondary)

    decision = await adapter.plan(dummy_context(), ())
    assert decision.invocations == ()
    assert "所有模型" in decision.reason or "無法回應" in decision.reason

    claims = await adapter.synthesise(Asset.BTC, ())
    assert claims == ()
