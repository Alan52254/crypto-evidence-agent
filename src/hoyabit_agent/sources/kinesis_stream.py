"""Kinesis Data Stream 即時證據源 — 接縫 1 的 EvidenceSource 實作。

從 Amazon Kinesis Data Stream 消費即時行情數據，轉為結構化 Evidence
供分析 Agent 使用。提供最近 N 秒內的即時價格快照作為技術面證據。

Evidence 欄位規格：
- source: "aws_kinesis://ggg"
- fetched_at: UTC 時間戳
- content_reference: 即時價格或數據摘要
- related_claim: 對應報告判斷（由 synthesise 層填充）

穩健性：
- ResourceNotFoundException → 回傳空集合
- 權限問題 / 網路超時 → 降級為空集合，不阻斷分析
- 永不讓主流程崩潰
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from hoyabit_agent.domain import AnalysisRegime, Asset, Evidence, Facet, SourceExcerpt
from hoyabit_agent.seams import Arguments, ToolSpec

logger = logging.getLogger(__name__)

# ─── 設定 ───
AWS_REGION_ENV = "AWS_REGION"
KINESIS_STREAM_ENV = "KINESIS_STREAM_NAME"
DEFAULT_REGION = "us-west-2"
DEFAULT_STREAM = "ggg"
TOOL_NAME = "kinesis_realtime_prices"

# 消費設定
MAX_RECORDS_PER_FETCH = 50
SHARD_ITERATOR_TYPE = "LATEST"  # 只讀最新資料
FETCH_TIMEOUT_SECONDS = 5.0


class KinesisEvidenceSource:
    """從 Kinesis Data Stream 消費即時行情作為技術面證據。

    滿足 EvidenceSource 協定：
    * 失效以空集合表達，不以例外表達
    * 面對無效參數自行降級
    * ResourceNotFoundException / 權限問題 → 空集合
    * 永不阻斷分析回合
    """

    supported_regimes: frozenset[AnalysisRegime] = frozenset({AnalysisRegime.LIVE})

    def __init__(self) -> None:
        self._region = os.environ.get(AWS_REGION_ENV, DEFAULT_REGION)
        self._stream_name = os.environ.get(KINESIS_STREAM_ENV, DEFAULT_STREAM)

        boto_config = Config(
            region_name=self._region,
            connect_timeout=2.0,
            read_timeout=5.0,
            retries={"max_attempts": 2},
        )
        try:
            self._client = boto3.client("kinesis", config=boto_config)
        except Exception as exc:
            logger.warning("[KinesisSource] Failed to create client: %s", exc)
            self._client = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=TOOL_NAME,
            description=(
                "從 Amazon Kinesis 即時串流取得最新的加密貨幣價格快照。\n"
                "資料來源：Binance WebSocket → Kinesis，延遲 < 3 秒。\n"
                "適合取得當下最即時的市場價格作為技術面參考。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "asset": {
                        "type": "string",
                        "enum": ["BTC", "ETH", "SOL", "BNB", "XRP"],
                        "description": "要查詢的加密資產",
                    },
                    "lookback_seconds": {
                        "type": "integer",
                        "minimum": 5,
                        "maximum": 300,
                        "description": "取最近幾秒的資料（預設 30）",
                    },
                },
                "required": ["asset"],
            },
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        """從 Kinesis stream 讀取最新行情並轉為 Evidence。"""
        if self._client is None:
            return ()

        target_symbol = f"{asset.value}USDT"

        # 在 executor 中執行同步的 Kinesis 讀取
        loop = asyncio.get_event_loop()
        try:
            records = await asyncio.wait_for(
                loop.run_in_executor(None, self._consume_latest, target_symbol),
                timeout=FETCH_TIMEOUT_SECONDS,
            )
        except (TimeoutError, Exception) as exc:
            logger.warning("[KinesisSource] Fetch failed: %s", exc)
            return ()

        if not records:
            return ()

        # 組裝 Evidence
        fetched_at = datetime.now(tz=UTC)
        content_reference = self._format_price_summary(records, asset)

        excerpt = SourceExcerpt(
            source_id=f"aws_kinesis://{self._stream_name}",
            url=f"aws_kinesis://{self._stream_name}",
            retrieved_at=fetched_at,
            locator=f"Kinesis stream '{self._stream_name}' (region: {self._region})",
            text=content_reference,
        )

        evidence = Evidence(
            id=f"KINESIS-{asset.value}-{fetched_at.strftime('%H%M%S')}",
            facet=Facet.TECHNICAL,
            summary=(
                f"{asset.value} 即時價格快照 "
                f"(來源: aws_kinesis://{self._stream_name}, "
                f"取得時間: {fetched_at.strftime('%Y-%m-%dT%H:%M:%SZ')})"
            ),
            stance_hint=0.0,
            excerpts=(excerpt,),
            event_key=f"kinesis-{asset.value}-realtime",
        )

        return (evidence,)

    # ─── 內部方法 ───

    def _consume_latest(self, target_symbol: str) -> list[dict[str, Any]]:
        """同步從 Kinesis 讀取最新記錄，篩選指定幣種。"""
        try:
            # 取得所有 shard
            desc = self._client.describe_stream(StreamName=self._stream_name)
            shards = desc["StreamDescription"]["Shards"]
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                logger.warning(
                    "[KinesisSource] Stream '%s' not found in %s",
                    self._stream_name, self._region,
                )
            elif error_code in ("AccessDeniedException", "ExpiredTokenException"):
                logger.warning("[KinesisSource] Permission denied: %s", error_code)
            else:
                logger.warning("[KinesisSource] describe_stream error: %s", exc)
            return []
        except Exception as exc:
            logger.warning("[KinesisSource] describe_stream failed: %s", exc)
            return []

        all_records: list[dict[str, Any]] = []

        for shard in shards:
            shard_id = shard["ShardId"]
            try:
                iter_resp = self._client.get_shard_iterator(
                    StreamName=self._stream_name,
                    ShardId=shard_id,
                    ShardIteratorType=SHARD_ITERATOR_TYPE,
                )
                shard_iterator = iter_resp["ShardIterator"]
            except Exception:
                continue

            # 等一小段時間讓 LATEST iterator 能抓到新資料
            time.sleep(1.5)

            try:
                records_resp = self._client.get_records(
                    ShardIterator=shard_iterator,
                    Limit=MAX_RECORDS_PER_FETCH,
                )
            except Exception:
                continue

            for record in records_resp.get("Records", []):
                try:
                    data = json.loads(record["Data"])
                    if data.get("symbol") == target_symbol:
                        all_records.append(data)
                except (json.JSONDecodeError, KeyError):
                    continue

        return all_records

    def _format_price_summary(
        self, records: list[dict[str, Any]], asset: Asset
    ) -> str:
        """將 Kinesis 記錄格式化為人可讀的價格摘要。"""
        if not records:
            return "無即時資料"

        prices = [r.get("price", 0) for r in records if r.get("price")]
        if not prices:
            return "無有效價格資料"

        latest = records[-1]
        latest_price = latest.get("price", 0)
        latest_time = latest.get("timestamp", "N/A")
        high = max(prices)
        low = min(prices)
        avg = sum(prices) / len(prices)

        lines = [
            f"{asset.value} Kinesis 即時價格快照 (共 {len(records)} 筆):",
            f"  最新價格: ${latest_price:,.2f} (at {latest_time})",
            f"  窗口最高: ${high:,.2f}",
            f"  窗口最低: ${low:,.2f}",
            f"  窗口均價: ${avg:,.2f}",
            f"  價格波動: ${high - low:,.2f} ({(high - low) / avg * 100:.2f}%)",
            f"  資料來源: aws_kinesis://{self._stream_name}",
            f"  取得時間: {datetime.now(tz=UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        ]

        # 附帶 24h 數據（如果有）
        volumes = [r.get("volume_24h", 0) for r in records if r.get("volume_24h")]
        if volumes:
            lines.append(f"  24h 成交量: {volumes[-1]:,.2f}")

        return "\n".join(lines)


__all__ = ["KinesisEvidenceSource", "TOOL_NAME"]
