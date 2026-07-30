"""Gemini 模型供應者 —— 接縫 3 的真實實作。

選 Gemini 免費層的理由（見 docs/research/0001）：我們的瓶頸是**單次呼叫
的大小**（證據集連同工具 schema 一起送進去），不是每分鐘幾次請求。
Gemini 3 Flash 免費層是 250K TPM、1M context，在這個維度上遠勝
其他免費方案；原生 function calling 與 JSON mode 也正好是我們要的。

刻意直接打 REST 而不引入 SDK：可以用 MockTransport 完整測試，
而且不受 SDK 版本波動影響 —— 4 週專案禁不起這種波動。
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Sequence
from typing import Any

import httpx

from hoyabit_agent.domain import Asset, DraftClaim, Evidence, LabelAspect
from hoyabit_agent.models.prompts import (
    LABEL_SYSTEM,
    PLAN_SYSTEM,
    SYNTHESIS_SYSTEM,
    plan_prompt,
    synthesis_prompt,
)
from hoyabit_agent.models.schemas import (
    CLAIMS_SCHEMA,
    SCORES_SCHEMA,
    loads,
    parse_claims,
    parse_scores,
)
from hoyabit_agent.seams import GatherContext, PlanDecision, ToolInvocation, ToolSpec

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# 分層（決策 #6）：推理層決定下一步與撰寫判斷，能力天花板決定成敗；
# 勞務層做高頻低階的打分，呼叫次數是推理層的數十倍但每次都簡單。
# 兩層可獨立設定 —— 混用同一顆模型會吃光壁鐘預算並撞速率限制。
DEFAULT_REASONING_MODEL = "gemini-3.6-flash"
DEFAULT_LABOUR_MODEL = DEFAULT_REASONING_MODEL

API_KEY_ENV = "GEMINI_API_KEY"
MODEL_ENV = "GEMINI_MODEL"
LABOUR_MODEL_ENV = "GEMINI_LABOUR_MODEL"

DEFAULT_TIMEOUT_SECONDS = 90.0
MAX_TRANSIENT_ATTEMPTS = 3
MAX_RETRY_DELAY_SECONDS = 30.0


class GeminiProvider:
    """滿足 `ModelProvider` 的 Gemini 實作。

    不變式：

    * **失敗以降級表達，不以例外中斷分析回合。** 網路錯誤、額度用罄、
      回應格式不符，一律回傳空結果或中性分數。
    * **無狀態。** 對話狀態由 `GatherContext` 明確傳入，
      因此同一個實例可以服務多個分析回合。
    * 金鑰只出現在查詢參數中，絕不寫進任何回傳值或例外訊息。
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        *,
        model: str = DEFAULT_REASONING_MODEL,
        labour_model: str = DEFAULT_LABOUR_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        extra_keys: tuple[str, ...] = (),
    ) -> None:
        self._client = client
        self._api_keys = [api_key] + [k for k in extra_keys if k]
        self._current_key_index = 0
        self._model = model
        self._labour_model = labour_model
        self._timeout_seconds = timeout_seconds

    @property
    def _api_key(self) -> str:
        return self._api_keys[self._current_key_index]

    def _rotate_key(self) -> bool:
        """切換到下一把 key。回傳是否成功輪換（有其他 key）。"""
        if len(self._api_keys) <= 1:
            return False
        self._current_key_index = (self._current_key_index + 1) % len(self._api_keys)
        return True

    @classmethod
    def from_environment(cls, client: httpx.AsyncClient) -> GeminiProvider | None:
        """依環境變數建構。沒有金鑰時回傳 None —— 呼叫端據此降級。"""
        api_key = os.environ.get(API_KEY_ENV, "").strip()
        if not api_key:
            return None
        configured_model = os.environ.get(MODEL_ENV, DEFAULT_REASONING_MODEL).strip()
        configured_labour = os.environ.get(LABOUR_MODEL_ENV, DEFAULT_LABOUR_MODEL).strip()
        # 收集所有 GEMINI_API_KEY_N 環境變數（支援無限把 key 輪換）
        extra_keys: list[str] = []
        for i in range(2, 20):  # 支援最多 20 把 key
            key = os.environ.get(f"GEMINI_API_KEY_{i}", "").strip()
            if key:
                extra_keys.append(key)
            else:
                break
        return cls(
            client, api_key,
            model=configured_model,
            labour_model=configured_labour,
            extra_keys=tuple(extra_keys),
        )

    # -- 推理層 ---------------------------------------------------------

    async def plan(self, context: GatherContext, tools: tuple[ToolSpec, ...]) -> PlanDecision:
        """以原生 function calling 決定下一步。

        參數由模型自己填 —— 這是原生工具調用與「受控規劃」的分野。
        """
        if not tools:
            return PlanDecision(invocations=(), reason="沒有可用的工具")

        payload = {
            "systemInstruction": {"parts": [{"text": PLAN_SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": plan_prompt(context)}]}],
            "tools": [{"functionDeclarations": [_declaration(spec) for spec in tools]}],
        }
        body = await self._post(payload, model=self._model)
        if body is None:
            return PlanDecision(invocations=(), reason="模型暫時無法回應，以現有證據繼續")

        parts = _parts(body)
        invocations = tuple(
            ToolInvocation(tool=str(call["name"]), arguments=_arguments(call))
            for part in parts
            if isinstance(call := part.get("functionCall"), dict) and call.get("name")
        )
        reason = " ".join(
            str(part["text"]).strip() for part in parts if isinstance(part.get("text"), str)
        ).strip()

        if not invocations:
            return PlanDecision(invocations=(), reason=reason or "模型判定證據已足夠，停止蒐集")
        return PlanDecision(invocations=invocations, reason=reason or "（模型未說明理由）")

    async def synthesise(
        self,
        asset: Asset,
        evidence: tuple[Evidence, ...],
        question: str = "請分析當前市場狀況",
    ) -> tuple[DraftClaim, ...]:
        """從證據推出判斷。以 JSON schema 強制結構化輸出。

        回傳的是物件陣列而非 Markdown 散文 —— 引用檢核是對陣列過濾，
        絕不事後剪裁句子。
        """
        if not evidence:
            return ()

        payload = {
            "systemInstruction": {"parts": [{"text": SYNTHESIS_SYSTEM}]},
            "contents": [
                {"role": "user", "parts": [{"text": synthesis_prompt(asset, evidence, question)}]}
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": CLAIMS_SCHEMA,
            },
        }
        parsed = await self._structured(payload, model=self._model)
        return parse_claims(parsed)

    # -- 勞務層 ---------------------------------------------------------

    async def label(
        self,
        texts: Sequence[str],
        aspect: LabelAspect = LabelAspect.SENTIMENT,
    ) -> tuple[float, ...]:
        """批次打分。

        `aspect` 換的是提示詞：問「語氣如何」與問「事件影響如何」
        是兩個不同的問題，答案可以相反 —— 那正是信心度要捕捉的分歧。

        **刻意一次送一批**：Gemini 免費層是 10 RPM，逐則呼叫會讓
        30 則文本吃掉 3 分鐘的壁鐘預算，而它們一次就送得完。
        """
        items = list(texts)
        if not items:
            return ()

        listing = "\n".join(f"{index + 1}. {text}" for index, text in enumerate(items))
        payload = {
            "systemInstruction": {"parts": [{"text": LABEL_SYSTEM[aspect]}]},
            "contents": [
                {"role": "user", "parts": [{"text": f"共 {len(items)} 則文本：\n\n{listing}"}]}
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": SCORES_SCHEMA,
            },
        }
        parsed = await self._structured(payload, model=self._labour_model)
        return parse_scores(parsed, len(items)) or tuple(0.0 for _ in items)

    # -- 傳輸 -----------------------------------------------------------

    async def _post(self, payload: dict[str, Any], *, model: str) -> dict[str, Any] | None:
        """Post to Gemini, retrying only temporary quota and service failures.

        429 時自動輪換到下一把 API key，等於額度翻倍。
        """
        url = f"{BASE_URL}/models/{model}:generateContent"
        for attempt in range(MAX_TRANSIENT_ATTEMPTS):
            try:
                response = await asyncio.wait_for(
                    self._client.post(url, params={"key": self._api_key}, json=payload),
                    timeout=self._timeout_seconds,
                )
            except (TimeoutError, httpx.HTTPError):
                return None

            if response.status_code == 200:
                try:
                    body = response.json()
                except ValueError:
                    return None
                return body if isinstance(body, dict) else None

            if response.status_code == 429:
                # 先嘗試切換 key，如果有其他 key 可用就立刻重試
                if self._rotate_key():
                    continue  # 用新 key 立刻重試，不等待
                # 沒有其他 key 了，等一下再試
                await asyncio.sleep(_retry_delay(response, attempt))
                continue

            transient = response.status_code >= 500
            if not transient or attempt + 1 >= MAX_TRANSIENT_ATTEMPTS:
                return None
            await asyncio.sleep(_retry_delay(response, attempt))
        return None
    async def _structured(self, payload: dict[str, Any], *, model: str) -> Any:
        body = await self._post(payload, model=model)
        if body is None:
            return None
        for part in _parts(body):
            text = part.get("text")
            if isinstance(text, str):
                return loads(text)
        return None


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    """Read Google RetryInfo/Retry-After, bounded for the competition budget."""
    header = response.headers.get("Retry-After", "").strip()
    try:
        return min(MAX_RETRY_DELAY_SECONDS, max(0.0, float(header)))
    except ValueError:
        pass
    try:
        details = response.json().get("error", {}).get("details", [])
    except ValueError:
        details = []
    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict):
            continue
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)s", str(detail.get("retryDelay", "")))
        if match:
            return min(MAX_RETRY_DELAY_SECONDS, float(match.group(1)))
    return min(MAX_RETRY_DELAY_SECONDS, float(2**attempt))

def _declaration(spec: ToolSpec) -> dict[str, Any]:
    """把 `ToolSpec` 轉成 Gemini 的 function declaration。

    這是「一份規格三個消費者」的其中一個消費者 —— 模型看到的介面
    與 MCP 暴露的、我們執行器用的，都來自同一份 `ToolSpec`。
    """
    parameters = dict(spec.parameters)
    properties = dict(parameters.get("properties", {}))
    properties.setdefault(
        "asset",
        {
            "type": "string",
            "enum": [asset.value for asset in Asset],
            "description": "Asset to fetch; use both assets for comparison questions.",
        },
    )
    parameters["properties"] = properties
    return {
        "name": spec.name,
        "description": spec.description,
        "parameters": parameters,
    }


def _parts(body: dict[str, Any]) -> list[dict[str, Any]]:
    """從回應中取出 parts。結構不符時回傳空清單，不拋例外。"""
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return []
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
    if not isinstance(content, dict):
        return []
    parts = content.get("parts")
    if not isinstance(parts, list):
        return []
    return [part for part in parts if isinstance(part, dict)]


def _arguments(call: dict[str, Any]) -> dict[str, Any]:
    args = call.get("args")
    return dict(args) if isinstance(args, dict) else {}


__all__ = [
    "API_KEY_ENV",
    "BASE_URL",
    "DEFAULT_LABOUR_MODEL",
    "DEFAULT_REASONING_MODEL",
    "DEFAULT_TIMEOUT_SECONDS",
    "LABOUR_MODEL_ENV",
    "MODEL_ENV",
    "GeminiProvider",
]
