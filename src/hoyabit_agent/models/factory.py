"""模型供應者工廠 — 依環境變數選擇 Provider，支援 fallback。"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from hoyabit_agent.seams import ModelProvider

PROVIDER_ENV = "MODEL_PROVIDER"


async def create_model_provider(client: httpx.AsyncClient) -> "ModelProvider | None":
    """依環境變數建構模型供應者。

    優先級：
    1. MODEL_PROVIDER 環境變數指定的 provider
       - "bedrock": 單用 Bedrock Claude，不設 failover（Phase 1 決策）
       - "groq": Groq primary, Gemini secondary
       - 其他/空: Gemini primary, Groq secondary
    2. 嘗試 Gemini（有 GEMINI_API_KEY 時）
    3. 嘗試 Groq（有 GROQ_API_KEY 時）
    4. 回傳 None（呼叫端據此降級）
    """
    preferred = os.environ.get(PROVIDER_ENV, "").strip().lower()

    # Bedrock 路徑 — 單用，不 failover。
    # Phase 1 決策：先驗證單一模型品質，failover 一致性風險待 Phase 2 評估。
    if preferred == "bedrock":
        from hoyabit_agent.models.bedrock import BedrockProvider

        bedrock_provider = BedrockProvider.from_environment(client)
        if bedrock_provider is not None:
            return bedrock_provider
        # Bedrock key 不存在時，退回下面的 Gemini/Groq 路徑

    from hoyabit_agent.models.gemini import GeminiProvider
    from hoyabit_agent.models.groq import GroqProvider
    from hoyabit_agent.models.resilience import ResilientModelAdapter

    gemini_provider = GeminiProvider.from_environment(client)
    groq_provider = GroqProvider.from_environment(client)

    primary: ModelProvider | None
    secondary: ModelProvider | None

    if preferred == "groq":
        primary = groq_provider
        secondary = gemini_provider
    else:
        primary = gemini_provider
        secondary = groq_provider

    if primary is not None:
        return ResilientModelAdapter(primary, secondary)
    if secondary is not None:
        return ResilientModelAdapter(secondary, None)

    return None


__all__ = ["PROVIDER_ENV", "create_model_provider"]
