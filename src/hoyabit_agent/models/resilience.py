"""模型容錯適配器 (ResilientModelAdapter) — 隱藏 429 限流與模型備援。

封裝主要模型 (Primary, 如 Gemini) 與備援模型 (Secondary, 如 Groq)，
當主要模型遇 429 限流、網路逾時或拋例外時，透明指數退避重試並自動發起 Failover 切換。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from hoyabit_agent.domain import Asset, DraftClaim, Evidence, LabelAspect
from hoyabit_agent.seams import GatherContext, ModelProvider, PlanDecision, ToolSpec

logger = logging.getLogger("hoyabit_agent.models.resilience")

MAX_RETRIES_PER_PROVIDER = 1
RETRY_DELAY_SECONDS = 1.0


class ResilientModelAdapter:
    """滿足 ModelProvider 介面協定的容錯與備援適配器。"""

    def __init__(
        self,
        primary: ModelProvider,
        secondary: ModelProvider | None = None,
    ) -> None:
        self._primary = primary
        self._secondary = secondary

    async def plan(
        self,
        context: GatherContext,
        tools: tuple[ToolSpec, ...],
    ) -> PlanDecision:
        """執行 plan()：優先使用 Primary，失效/限流時透明切換至 Secondary。"""
        decision = await self._try_provider_plan(self._primary, context, tools, "Primary")
        if decision is not None and (decision.invocations or not _is_rate_limit_or_empty(decision.reason)):
            return decision

        if self._secondary is not None:
            logger.warning("Primary Model 未能取得 valid plan (限流/失敗)，發起 Failover 切換至 Secondary Model")
            sec_decision = await self._try_provider_plan(self._secondary, context, tools, "Secondary")
            if sec_decision is not None:
                return sec_decision

        return decision or PlanDecision(invocations=(), reason="所有模型供應者皆暫時無法回應")

    async def synthesise(
        self,
        asset: Asset,
        evidence: tuple[Evidence, ...],
        question: str = "請分析當前市場狀況",
    ) -> tuple[DraftClaim, ...]:
        """執行 synthesise()：優先使用 Primary，失敗時切換至 Secondary。"""
        claims = await self._try_provider_synthesise(self._primary, asset, evidence, question, "Primary")
        if claims:
            return claims

        if self._secondary is not None:
            logger.warning("Primary Model synthesise() 未產出 claims，嘗試 Secondary Model")
            sec_claims = await self._try_provider_synthesise(self._secondary, asset, evidence, question, "Secondary")
            if sec_claims:
                return sec_claims

        return ()

    async def label(
        self,
        texts: Sequence[str],
        aspect: LabelAspect = LabelAspect.SENTIMENT,
    ) -> tuple[float, ...]:
        """執行 label()：優先使用 Primary，失敗時切換至 Secondary。"""
        scores = await self._try_provider_label(self._primary, texts, aspect, "Primary")
        if scores and any(s != 0.0 for s in scores):
            return scores

        if self._secondary is not None:
            sec_scores = await self._try_provider_label(self._secondary, texts, aspect, "Secondary")
            if sec_scores:
                return sec_scores

        return tuple(0.0 for _ in texts)

    async def _try_provider_plan(
        self,
        provider: ModelProvider,
        context: GatherContext,
        tools: tuple[ToolSpec, ...],
        label: str,
    ) -> PlanDecision | None:
        for attempt in range(MAX_RETRIES_PER_PROVIDER + 1):
            try:
                decision = await provider.plan(context, tools)
                if decision.invocations or not _is_rate_limit_or_empty(decision.reason):
                    return decision
            except Exception as exc:
                logger.warning(f"[{label}] plan() 嘗試 #{attempt+1} 發生例外: {exc}")

            if attempt < MAX_RETRIES_PER_PROVIDER:
                await asyncio.sleep(RETRY_DELAY_SECONDS)
        return None

    async def _try_provider_synthesise(
        self,
        provider: ModelProvider,
        asset: Asset,
        evidence: tuple[Evidence, ...],
        question: str,
        label: str,
    ) -> tuple[DraftClaim, ...]:
        for attempt in range(MAX_RETRIES_PER_PROVIDER + 1):
            try:
                claims = await provider.synthesise(asset, evidence, question)
                if claims:
                    return claims
            except Exception as exc:
                logger.warning(f"[{label}] synthesise() 嘗試 #{attempt+1} 發生例外: {exc}")

            if attempt < MAX_RETRIES_PER_PROVIDER:
                await asyncio.sleep(RETRY_DELAY_SECONDS)
        return ()

    async def _try_provider_label(
        self,
        provider: ModelProvider,
        texts: Sequence[str],
        aspect: LabelAspect,
        label: str,
    ) -> tuple[float, ...]:
        try:
            return await provider.label(texts, aspect)
        except Exception as exc:
            logger.warning(f"[{label}] label() 發生例外: {exc}")
            return ()


def _is_rate_limit_or_empty(reason: str) -> bool:
    r = reason.lower()
    return "429" in r or "too many requests" in r or "無法回應" in r or "未說明理由" in r


__all__ = ["ResilientModelAdapter"]
