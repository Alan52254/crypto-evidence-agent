"""地端模型供應者 —— OpenAI 相容端點。

**一個 adapter 通吃三種本機執行環境**，因為 Ollama、LM Studio、
llama.cpp server 都暴露 `/v1/chat/completions`：

| 執行環境 | base URL |
|---|---|
| Ollama | `http://localhost:11434/v1` |
| LM Studio | `http://localhost:1234/v1` |
| llama.cpp server | `http://localhost:8080/v1` |

同一份程式碼也能接任何 OpenAI 相容的雲端服務，所以這不只是「沒金鑰時的替代品」。

## 與雲端 provider 的兩個真實差異

1. **結構化輸出只能靠提示詞。** 多數地端執行環境不支援伺服器端的
   `json_schema` 強制約束，只支援「請回 JSON」。因此 schema 明寫進提示詞，
   並用寬鬆解析（模型常在 JSON 前後夾雜文字或 ``` 圍欄）。
2. **工具參數是 JSON 字串，而且常常是壞的。** 小模型會吐出殘缺的 JSON。
   那是預期情況 —— 解析失敗時退成空參數，讓資料源用預設值跑，
   而不是讓整個分析回合掛掉。
"""

from __future__ import annotations

import asyncio
import os
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
    describe_schema,
    loads,
    parse_claims,
    parse_scores,
)
from hoyabit_agent.seams import GatherContext, PlanDecision, ToolInvocation, ToolSpec

BASE_URL_ENV = "HOYABIT_LOCAL_BASE_URL"
MODEL_ENV = "HOYABIT_LOCAL_MODEL"
LABOUR_MODEL_ENV = "HOYABIT_LOCAL_LABOUR_MODEL"
API_KEY_ENV = "HOYABIT_LOCAL_API_KEY"

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_TIMEOUT_SECONDS = 180.0
"""地端推論比雲端慢得多，逾時上限放寬 —— 但仍必須有上限。"""

_JSON_INSTRUCTION = """\

**只輸出 JSON，不要有任何其他文字、說明或 markdown 圍欄。**
輸出必須符合這個結構：

{schema}
"""


class LocalOpenAIProvider:
    """滿足 `ModelProvider` 的地端實作，走 OpenAI 相容的 chat completions。

    不變式（與雲端 provider 相同 —— 這是接縫 3 的契約）：

    * **失敗以降級表達，不以例外中斷分析回合。**
    * **無狀態。** 對話狀態由 `GatherContext` 明確傳入。
    * 有硬性逾時上限，掛住的模型不會拖垮 15 分鐘預算。
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str,
        labour_model: str | None = None,
        api_key: str = "",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._model = model
        # 地端常常只跑得動一顆模型 —— 沒指定勞務層就與推理層共用，
        # 這是務實的預設，不是把分層的設計丟掉。
        self._labour_model = labour_model or model
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls, client: httpx.AsyncClient) -> LocalOpenAIProvider | None:
        """依環境變數建構。沒有指定模型時回傳 None —— 呼叫端據此降級。

        只要 `HOYABIT_LOCAL_MODEL` 有值就啟用；base URL 有合理預設（Ollama）。
        """
        model = os.environ.get(MODEL_ENV, "").strip()
        if not model:
            return None
        labour = os.environ.get(LABOUR_MODEL_ENV, "").strip()
        return cls(
            client,
            base_url=os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
            model=model,
            labour_model=labour or None,
            api_key=os.environ.get(API_KEY_ENV, "").strip(),
        )

    # -- 推理層 ---------------------------------------------------------

    async def plan(self, context: GatherContext, tools: tuple[ToolSpec, ...]) -> PlanDecision:
        """以 OpenAI 相容的原生 tool calling 決定下一步。"""
        if not tools:
            return PlanDecision(invocations=(), reason="沒有可用的工具")

        body = await self._chat(
            model=self._model,
            messages=[
                {"role": "system", "content": PLAN_SYSTEM},
                {"role": "user", "content": plan_prompt(context)},
            ],
            tools=[_tool_definition(spec) for spec in tools],
        )
        if body is None:
            return PlanDecision(invocations=(), reason="地端模型暫時無法回應，以現有證據繼續")

        message = _message(body)
        invocations = tuple(
            ToolInvocation(tool=name, arguments=_tool_arguments(call))
            for call in _tool_calls(message)
            if (name := _tool_name(call))
        )
        reason = str(message.get("content") or "").strip()

        if not invocations:
            return PlanDecision(invocations=(), reason=reason or "模型判定證據已足夠，停止蒐集")
        return PlanDecision(invocations=invocations, reason=reason or "（模型未說明理由）")

    async def synthesise(
        self,
        asset: Asset,
        evidence: tuple[Evidence, ...],
    ) -> tuple[DraftClaim, ...]:
        """從證據推出判斷。結構靠提示詞約束 + 寬鬆解析。"""
        if not evidence:
            return ()

        body = await self._chat(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": SYNTHESIS_SYSTEM
                    + _JSON_INSTRUCTION.format(schema=describe_schema(CLAIMS_SCHEMA)),
                },
                {"role": "user", "content": synthesis_prompt(asset, evidence)},
            ],
            json_mode=True,
        )
        return parse_claims(loads(_content(body)))

    # -- 勞務層 ---------------------------------------------------------

    async def label(
        self,
        texts: Sequence[str],
        aspect: LabelAspect = LabelAspect.SENTIMENT,
    ) -> tuple[float, ...]:
        """批次打分。地端沒有 RPM 限制，但一次送一批仍然省下大量往返延遲。"""
        items = list(texts)
        if not items:
            return ()

        listing = "\n".join(f"{index + 1}. {text}" for index, text in enumerate(items))
        body = await self._chat(
            model=self._labour_model,
            messages=[
                {
                    "role": "system",
                    "content": LABEL_SYSTEM[aspect]
                    + _JSON_INSTRUCTION.format(schema=describe_schema(SCORES_SCHEMA)),
                },
                {"role": "user", "content": f"共 {len(items)} 則文本：\n\n{listing}"},
            ],
            json_mode=True,
        )
        return parse_scores(loads(_content(body)), len(items)) or tuple(0.0 for _ in items)

    # -- 傳輸 -----------------------------------------------------------

    async def _chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            response = await asyncio.wait_for(
                self._client.post(
                    f"{self._base_url}/chat/completions", json=payload, headers=headers
                ),
                timeout=self._timeout_seconds,
            )
        except (TimeoutError, httpx.HTTPError):
            return None
        if response.status_code != 200:
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None


def _tool_definition(spec: ToolSpec) -> dict[str, Any]:
    """把 `ToolSpec` 轉成 OpenAI 的 tool 定義。

    這是「一份規格多個消費者」再多一個 —— Gemini 的 functionDeclarations、
    MCP 的 inputSchema、以及這裡，全部來自同一份 `ToolSpec`。
    """
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": dict(spec.parameters),
        },
    }


def _message(body: dict[str, Any] | None) -> dict[str, Any]:
    """從回應取出 message。結構不符時回傳空字典，不拋例外。"""
    if not isinstance(body, dict):
        return {}
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return {}
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    return message if isinstance(message, dict) else {}


def _content(body: dict[str, Any] | None) -> str:
    content = _message(body).get("content")
    return content if isinstance(content, str) else ""


def _tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    calls = message.get("tool_calls")
    if not isinstance(calls, list):
        return []
    return [call for call in calls if isinstance(call, dict)]


def _tool_name(call: dict[str, Any]) -> str:
    function = call.get("function")
    if not isinstance(function, dict):
        return ""
    name = function.get("name")
    return name if isinstance(name, str) and name else ""


def _tool_arguments(call: dict[str, Any]) -> dict[str, Any]:
    """解析工具參數。

    OpenAI 格式的參數是 **JSON 字串**，而小模型常常吐出壞掉的 JSON。
    解析失敗時退成空參數 —— 資料源會用預設值跑，比整個回合掛掉好。
    """
    function = call.get("function")
    if not isinstance(function, dict):
        return {}
    raw = function.get("arguments")
    if isinstance(raw, dict):
        return dict(raw)  # 有些執行環境直接給物件而非字串
    if not isinstance(raw, str) or not raw.strip():
        return {}
    parsed = loads(raw)
    return dict(parsed) if isinstance(parsed, dict) else {}


def probe(base_url: str = DEFAULT_BASE_URL, timeout: float = 3.0) -> tuple[bool, str]:
    """同步探測地端端點是否活著，供 CLI 給出可行動的錯誤訊息。

    回傳 (是否可用, 說明)。用同步 httpx 是因為呼叫點在啟動階段，
    還沒進入 async 迴圈。
    """
    url = f"{base_url.rstrip('/')}/models"
    try:
        response = httpx.get(url, timeout=timeout)
    except httpx.HTTPError as error:
        return False, f"連不上 {url}：{type(error).__name__}"
    if response.status_code != 200:
        return False, f"{url} 回應 {response.status_code}"
    try:
        data = response.json().get("data", [])
        names = [item.get("id") for item in data if isinstance(item, dict)]
    except (ValueError, AttributeError):
        return True, f"{url} 可連線（但模型清單無法解析）"
    return True, f"{url} 可連線，已載入模型：{', '.join(str(n) for n in names) or '（無）'}"


__all__ = [
    "API_KEY_ENV",
    "BASE_URL_ENV",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT_SECONDS",
    "LABOUR_MODEL_ENV",
    "MODEL_ENV",
    "LocalOpenAIProvider",
    "probe",
]
