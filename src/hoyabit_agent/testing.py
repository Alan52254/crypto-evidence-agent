"""測試用的適配器 —— 接縫 1 與接縫 3 的假實作。

有了它們，整條分析流程可以在**不碰網路、不呼叫真實模型**的情況下完整測試。
這是「用最高的接縫」的實際兌現。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextvars import ContextVar
from datetime import UTC, datetime

from hoyabit_agent.domain import (
    Asset,
    DraftClaim,
    Evidence,
    Facet,
    LabelAspect,
    SourceExcerpt,
)
from hoyabit_agent.seams import (
    Arguments,
    GatherContext,
    PlanDecision,
    ToolInvocation,
    ToolSpec,
)

_ACTIVE_CLOCK: ContextVar[ManualClock | None] = ContextVar("_ACTIVE_CLOCK", default=None)


class ManualClock:
    """手動推進的時鐘，讓「預算耗盡」的行為可以被確定性地測試，
    而不需要真的等 15 分鐘。

    建構時會登記為當前的活躍時鐘，`StaticSource` 因此能宣告自己「花掉」多少時間。
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        self._start = start
        _ACTIVE_CLOCK.set(self)

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    @property
    def elapsed(self) -> float:
        return self._now - self._start


def evidence(
    identifier: str,
    facet: Facet,
    stance_hint: float,
    *,
    event_key: str | None = None,
    text: str = "測試用來源片段",
) -> Evidence:
    """建構一項帶完整來源片段的測試證據。

    刻意不提供「沒有來源片段的證據」這種建構方式 ——
    溯源路徑是證據的不變式，測試裡也不例外。
    """
    return Evidence(
        id=identifier,
        facet=facet,
        summary=f"{identifier} ({facet.value})",
        stance_hint=stance_hint,
        excerpts=(
            SourceExcerpt(
                source_id=identifier,
                url=f"https://example.test/{identifier}",
                retrieved_at=datetime(2026, 7, 23, tzinfo=UTC),
                locator="para-1",
                text=text,
            ),
        ),
        event_key=event_key,
    )


class StaticSource:
    """回傳固定證據的證據源。

    `costs_seconds` 讓測試模擬「這個來源很慢」而不需真的等待；
    `raises` 讓測試模擬上游故障。
    """

    def __init__(
        self,
        items: list[Evidence],
        *,
        name: str = "static",
        costs_seconds: float = 0.0,
        raises: Exception | None = None,
    ) -> None:
        self._items = tuple(items)
        self._name = name
        self._costs_seconds = costs_seconds
        self._raises = raises
        self.received: list[Arguments] = []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self._name,
            description="測試用的固定證據源",
            parameters={"type": "object", "properties": {}},
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        self.received.append(dict(arguments))
        clock = _ACTIVE_CLOCK.get()
        if clock is not None and self._costs_seconds:
            clock.advance(self._costs_seconds)
        if self._raises is not None:
            raise self._raises
        return self._items


class HangingSource:
    """永遠不回應的證據源 —— 用來驗證逾時防護確實在運作。

    掛起是比失敗更陰險的故障模式：它不會拋例外，只會安靜地吃掉預算。
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="hanging",
            description="永遠不回應的測試證據源",
            parameters={"type": "object", "properties": {}},
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")  # pragma: no cover


class ScriptedModel:
    """腳本化的假模型。

    `plans` 是一串 (逗號分隔的工具名稱, 理由)，依序回應每一輪規劃。
    腳本用完後回傳空的 invocations，代表模型認為無需再蒐集。

    `arguments` 可指定每個工具被呼叫時要帶的參數，用來驗證參數確實
    從模型流到證據源。
    """

    def __init__(
        self,
        plans: list[tuple[str, str]],
        claims: list[DraftClaim],
        *,
        arguments: dict[str, Arguments] | None = None,
    ) -> None:
        self._plans = list(plans)
        self._claims = tuple(claims)
        self._arguments = arguments or {}
        self._calls = 0
        self.seen_contexts: list[GatherContext] = []
        self.seen_tools: list[tuple[ToolSpec, ...]] = []

    async def plan(self, context: GatherContext, tools: tuple[ToolSpec, ...]) -> PlanDecision:
        self.seen_contexts.append(context)
        self.seen_tools.append(tools)
        if self._calls >= len(self._plans):
            return PlanDecision(invocations=(), reason="腳本已用盡，視為無需再蒐集")
        raw, reason = self._plans[self._calls]
        self._calls += 1
        invocations = tuple(
            ToolInvocation(tool=name, arguments=self._arguments.get(name, {}))
            for name in raw.split(",")
            if name
        )
        return PlanDecision(invocations=invocations, reason=reason)

    async def synthesise(
        self,
        asset: Asset,
        evidence: tuple[Evidence, ...],
    ) -> tuple[DraftClaim, ...]:
        return self._claims

    async def label(
        self,
        texts: Sequence[str],
        aspect: LabelAspect = LabelAspect.SENTIMENT,
    ) -> tuple[float, ...]:
        return tuple(0.0 for _ in texts)


__all__ = ["HangingSource", "ManualClock", "ScriptedModel", "StaticSource", "evidence"]
