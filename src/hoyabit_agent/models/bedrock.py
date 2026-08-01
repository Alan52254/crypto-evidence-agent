"""Bedrock Claude 模型供應者 — 接縫 3 的 AWS 實作。

使用 boto3 + Converse API。認證透過 AWS CLI session credentials
（`aws configure export-credentials` 注入環境變數）。

為什麼這裡跟 GeminiProvider / GroqProvider 的實作模式不一致：

    其他 provider 直接用 httpx 打 REST endpoint + API key header，
    因為它們的認證模型是「一把 key → 一個 header」。

    Bedrock 的 Bearer Token（bedrock-api-key-xxx）在 2026-08 測試時，
    打 bedrock-runtime 端點持續回 403 "Authentication failed"，
    排查確認 prefix 格式正確、endpoint 正確，推測是 Workshop 帳號的
    IAM policy 缺少 bedrock:CallWithBearerToken 權限（或該功能尚在
    rolling out）。而同一帳號的 SigV4 credentials（來自 aws login）
    呼叫 Converse 正常。

    因此改用 boto3 作為傳輸層：
    - 它內建 SigV4 簽章，比自己實作 httpx SigV4 更可靠
    - boto3 的同步 client 用 asyncio.to_thread 包裝成 async
    - 測試時依然可以 mock（patch boto3.client 的 converse 方法）

    技術債：
    - boto3 1.35.99 不認識 aws login 的 credential provider，
      故啟動時呼叫 `aws configure export-credentials` 把 session token
      注入環境變數。若 AWS CLI 的 login 流程改變，這段可能靜默失效。
    - inference profile ID（如 us.anthropic.claude-sonnet-4-6）不是模型
      名稱本身，是 cross-region routing 的 profile，AWS 更新命名規則時
      需要同步修改 DEFAULT_MODEL。

設計決策：
- 使用 boto3 的 bedrock-runtime client — SigV4 認證最可靠
- 共用 schemas.py 的 parse_claims / parse_scores — 不重複定義解析邏輯
- 工具調用走 Converse API 原生 tool_use（stopReason="tool_use"）
- boto3 client 是同步的，用 asyncio.to_thread 包裝成 async
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from collections.abc import Sequence
from typing import Any

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

logger = logging.getLogger("hoyabit_agent.models.bedrock")

# -- 設定 ---------------------------------------------------------------

BEDROCK_REGION_ENV = "AWS_DEFAULT_REGION"
BEDROCK_MODEL_ENV = "BEDROCK_MODEL"

DEFAULT_REGION = "us-west-2"
DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-6"
MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 5.0


def _ensure_aws_credentials() -> bool:
    """確保環境變數中有 AWS credentials。

    aws login 的 credentials 不會自動被 boto3 讀取，
    需要透過 aws configure export-credentials 取得並注入環境。

    同時檢查 session token 是否已過期（如果有 expiration 資訊）。
    """
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        # 有 credentials，檢查是否已過期
        expiry = os.environ.get("AWS_CREDENTIAL_EXPIRATION", "")
        if expiry:
            from datetime import datetime, timezone
            try:
                # 格式: 2026-08-01T03:23:27+00:00
                exp_dt = datetime.fromisoformat(expiry)
                now = datetime.now(timezone.utc)
                if exp_dt <= now:
                    logger.warning(
                        f"AWS credentials 已過期（{expiry}），嘗試重新匯出"
                    )
                    # 清除過期的，讓下方重新匯出
                    os.environ.pop("AWS_ACCESS_KEY_ID", None)
                    os.environ.pop("AWS_SECRET_ACCESS_KEY", None)
                    os.environ.pop("AWS_SESSION_TOKEN", None)
                    os.environ.pop("AWS_CREDENTIAL_EXPIRATION", None)
                else:
                    return True
            except (ValueError, TypeError):
                pass  # 解析失敗就跳過過期檢查
        else:
            return True

    # 嘗試從 AWS CLI 匯出 credentials
    aws_cli = _find_aws_cli()
    if not aws_cli:
        return False

    try:
        result = subprocess.run(
            [aws_cli, "configure", "export-credentials", "--format", "env-no-export"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return False

        for line in result.stdout.strip().splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                os.environ[key.strip()] = value.strip()
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _find_aws_cli() -> str | None:
    """找到 AWS CLI 執行檔的路徑。"""
    candidates = [
        os.path.expanduser(r"~\AppData\Local\Programs\Amazon\AWSCLIV2\aws.exe"),
        r"C:\Program Files\Amazon\AWSCLIV2\aws.exe",
        "aws",  # fallback to PATH
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return candidates[-1]  # 試試 PATH 上的 aws


class BedrockProvider:
    """滿足 ModelProvider 協定的 AWS Bedrock 實作。

    不變式（與 GeminiProvider 一致）：
    * 失敗以降級表達，不以例外中斷分析回合。
    * 無狀態 — 對話狀態由 GatherContext 明確傳入。
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        region: str = DEFAULT_REGION,
    ) -> None:
        self._model = model
        self._region = region
        self._client: Any = None  # lazy init

    def _get_client(self) -> Any:
        """Lazy-init boto3 client。第一次呼叫時才建立連線。"""
        if self._client is None:
            import boto3
            from botocore.config import Config

            # Claude Sonnet 4.6 的 synthesise 呼叫（大 prompt + 8192 token 回應）
            # 需要較長的 read timeout。boto3 預設 60s 對於複雜推理不夠。
            config = Config(
                read_timeout=300,  # 5 分鐘
                connect_timeout=10,
                retries={"max_attempts": 0},  # 重試由我們自己的邏輯處理
            )
            self._client = boto3.client(
                "bedrock-runtime", region_name=self._region, config=config
            )
        return self._client

    @classmethod
    def from_environment(cls, client: Any = None) -> BedrockProvider | None:
        """依環境變數建構。確認 AWS credentials 可用時才建構。

        `client` 參數保留以相容 factory.py 的呼叫簽章，但不使用
        （boto3 自帶 HTTP 連線管理）。
        """
        if not _ensure_aws_credentials():
            logger.warning("無法取得 AWS credentials，BedrockProvider 不可用")
            return None

        region = os.environ.get(BEDROCK_REGION_ENV, DEFAULT_REGION).strip()
        model = os.environ.get(BEDROCK_MODEL_ENV, DEFAULT_MODEL).strip()
        return cls(model=model, region=region)

    @property
    def model_id(self) -> str:
        """目前使用的模型 ID — 用於報告的 model_used 欄位。"""
        return self._model

    # -- 推理層 ---------------------------------------------------------

    async def plan(
        self, context: GatherContext, tools: tuple[ToolSpec, ...]
    ) -> PlanDecision:
        """以 Converse API 原生 tool_use 決定下一步。"""
        if not tools:
            return PlanDecision(invocations=(), reason="沒有可用的工具")

        kwargs: dict[str, Any] = {
            "modelId": self._model,
            "system": [{"text": PLAN_SYSTEM}],
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": plan_prompt(context)}],
                }
            ],
            "toolConfig": {
                "tools": [_to_converse_tool(spec) for spec in tools],
            },
            "inferenceConfig": {
                "maxTokens": 4096,
                "temperature": 0.2,
            },
        }

        response = await self._converse(kwargs)
        if response is None:
            return PlanDecision(
                invocations=(), reason="Bedrock 暫時無法回應，以現有證據繼續"
            )

        return _parse_plan_response(response)

    async def synthesise(
        self,
        asset: Asset,
        evidence: tuple[Evidence, ...],
        question: str = "請分析當前市場狀況",
    ) -> tuple[DraftClaim, ...]:
        """從證據推出判斷。要求 Claude 以 JSON 格式輸出結構化 claims。"""
        if not evidence:
            return ()

        schema_instruction = (
            "\n\n你必須以下列 JSON 格式回應（不要加任何額外文字，只輸出純 JSON）：\n"
            f"{describe_schema(CLAIMS_SCHEMA)}\n\n"
            "facet 必須是: technical, positioning, fundamental, sentiment\n"
            "role 必須是: fact, inference, conclusion, counter_evidence, risk, "
            "invalidation, watch\n"
            "evidence_ids 必須是上方出現過的真實 ID（如 BNC-SPOT-BTC-4h-RSI14）"
        )

        kwargs: dict[str, Any] = {
            "modelId": self._model,
            "system": [{"text": SYNTHESIS_SYSTEM + schema_instruction}],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"text": synthesis_prompt(asset, evidence, question)}
                    ],
                }
            ],
            "inferenceConfig": {
                "maxTokens": 8192,
                "temperature": 0.1,
            },
        }

        response = await self._converse(kwargs)
        if response is None:
            return ()

        text = _extract_text(response)
        if not text:
            return ()

        parsed = loads(text)
        return parse_claims(parsed)

    # -- 勞務層 ---------------------------------------------------------

    async def label(
        self,
        texts: Sequence[str],
        aspect: LabelAspect = LabelAspect.SENTIMENT,
    ) -> tuple[float, ...]:
        """批次打分。與 Gemini 一致的批次介面 — 一次送一批。"""
        items = list(texts)
        if not items:
            return ()

        listing = "\n".join(
            f"{index + 1}. {text}" for index, text in enumerate(items)
        )

        schema_instruction = (
            "\n\n你必須以下列 JSON 格式回應（不要加任何額外文字，只輸出純 JSON）：\n"
            f"{describe_schema(SCORES_SCHEMA)}\n\n"
            f"共 {len(items)} 則文本，必須回傳 {len(items)} 個分數。"
        )

        kwargs: dict[str, Any] = {
            "modelId": self._model,
            "system": [{"text": LABEL_SYSTEM[aspect] + schema_instruction}],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"text": f"共 {len(items)} 則文本：\n\n{listing}"}
                    ],
                }
            ],
            "inferenceConfig": {
                "maxTokens": 2048,
                "temperature": 0.0,
            },
        }

        response = await self._converse(kwargs)
        if response is None:
            return tuple(0.0 for _ in items)

        text = _extract_text(response)
        if not text:
            return tuple(0.0 for _ in items)

        parsed = loads(text)
        return parse_scores(parsed, len(items)) or tuple(0.0 for _ in items)

    # -- 傳輸層 ---------------------------------------------------------

    async def _converse(self, kwargs: dict[str, Any]) -> dict[str, Any] | None:
        """呼叫 Bedrock Converse API（透過 boto3，包在 asyncio.to_thread 裡）。

        重試邏輯：ThrottlingException 或 5xx 時指數退避。
        """
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await asyncio.to_thread(
                    self._get_client().converse, **kwargs
                )
                return response
            except Exception as exc:
                exc_str = str(exc)
                is_throttle = "ThrottlingException" in exc_str or "Too Many Requests" in exc_str
                is_server = "ServiceUnavailable" in exc_str or "InternalServer" in exc_str

                if (is_throttle or is_server) and attempt < MAX_RETRIES:
                    delay = RETRY_DELAY_SECONDS * (2 ** attempt)
                    logger.warning(
                        f"Bedrock 可重試錯誤 (attempt {attempt + 1}): {exc_str[:200]}. "
                        f"等待 {delay:.0f}s 後重試"
                    )
                    await asyncio.sleep(delay)
                    continue

                logger.error(f"Bedrock 呼叫失敗: {exc_str[:300]}")
                return None

        return None


# -- 工具轉換 -----------------------------------------------------------


def _to_converse_tool(spec: ToolSpec) -> dict[str, Any]:
    """把 ToolSpec 轉成 Bedrock Converse API 的 tool 格式。"""
    return {
        "toolSpec": {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": {"json": spec.parameters},
        }
    }


# -- 回應解析 -----------------------------------------------------------


def _parse_plan_response(response: dict[str, Any]) -> PlanDecision:
    """從 Converse API 回應解析出 PlanDecision。

    Claude 在 tool_use 時，response["output"]["message"]["content"] 包含：
    - text blocks（推理說明）
    - toolUse blocks（工具調用）
    """
    output = response.get("output", {})
    message = output.get("message", {})
    content_blocks = message.get("content", [])

    invocations: list[ToolInvocation] = []
    reason_parts: list[str] = []

    for block in content_blocks:
        if "text" in block:
            reason_parts.append(block["text"].strip())
        elif "toolUse" in block:
            tool_use = block["toolUse"]
            name = tool_use.get("name", "")
            input_data = tool_use.get("input", {})
            if name:
                invocations.append(
                    ToolInvocation(tool=name, arguments=input_data)
                )

    reason = " ".join(reason_parts).strip()

    if not invocations:
        return PlanDecision(
            invocations=(),
            reason=reason or "模型判定證據已足夠，停止蒐集",
        )

    return PlanDecision(
        invocations=tuple(invocations),
        reason=reason or "（模型未說明理由）",
    )


def _extract_text(response: dict[str, Any]) -> str | None:
    """從 Converse API 回應取出文字內容。"""
    output = response.get("output", {})
    message = output.get("message", {})
    content_blocks = message.get("content", [])

    for block in content_blocks:
        if "text" in block:
            return block["text"]
    return None


__all__ = ["BedrockProvider"]
