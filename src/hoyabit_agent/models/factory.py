"""模型供應者工廠 — 依環境變數選擇 Provider，支援 fallback。"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from hoyabit_agent.seams import ModelProvider

PROVIDER_ENV = "MODEL_PROVIDER"


async def create_model_provider(client: httpx.AsyncClient) -> "ModelProvider | None":
    """依環境變數建構模型供應者，支援 fallback 機制。

    優先級：
    1. MODEL_PROVIDER 環境變數指定的 provider
    2. 嘗試 Gemini（有 GEMINI_API_KEY 時）
    3. 嘗試 Groq（有 GROQ_API_KEY 時）
    4. 回傳 None（呼叫端據此降級）
    """
    preferred = os.environ.get(PROVIDER_ENV, "").strip().lower()

    from hoyabit_agent.models.gemini import GeminiProvider
    from hoyabit_agent.models.groq import GroqProvider

    if preferred == "groq":
        provider = GroqProvider.from_environment(client)
        if provider is not None:
            return provider
        # Fallback to Gemini
        return GeminiProvider.from_environment(client)

    if preferred == "gemini" or not preferred:
        provider = GeminiProvider.from_environment(client)
        if provider is not None:
            return provider
        # Fallback to Groq
        return GroqProvider.from_environment(client)

    # Unknown provider name — try both
    return GeminiProvider.from_environment(client) or GroqProvider.from_environment(client)


__all__ = ["PROVIDER_ENV", "create_model_provider"]
