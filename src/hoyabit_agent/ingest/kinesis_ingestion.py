"""Kinesis Data Streams & Firehose 即時串流寫入模組。

提供高吞吐量、低延遲的即時行情與新聞串流寫入能力。
將 Binance WebSocket 價格數據與新聞事件推送至 AWS Kinesis/Firehose，
作為 AlphaSonar ReAct Agent 的即時數據入口。

穩健性保證：
- 遇到 ProvisionedThroughputExceededException 時指數退避重試
- AWS API 連線失敗或超時（>2s）時降級至本地記憶體 Buffer
- 永不讓主流程崩潰

用法：
    from hoyabit_agent.ingest.kinesis_ingestion import (
        KinesisStreamProducer,
        FirehoseDeliveryProducer,
    )

    producer = KinesisStreamProducer()
    producer.put_record("hoyabit-crypto-market-stream", data, "BTC")

    firehose = FirehoseDeliveryProducer()
    firehose.send_to_firehose("hoyabit-market-firehose", data)
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# ─── 設定 ───
AWS_REGION_ENV = "AWS_REGION"
DEFAULT_REGION = "us-west-2"
DEFAULT_KINESIS_STREAM = "ggg"
DEFAULT_FIREHOSE_STREAM = "hoyabit-market-firehose"

# 重試設定
MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 0.1
MAX_BACKOFF_SECONDS = 10.0

# 本地 buffer 容量（fallback 時暫存）
LOCAL_BUFFER_MAX_SIZE = 10000

# boto3 連線逾時
CONNECT_TIMEOUT_SECONDS = 2.0
READ_TIMEOUT_SECONDS = 5.0


class KinesisStreamProducer:
    """Amazon Kinesis Data Streams 寫入器。

    負責將加密貨幣行情（BTC/ETH/SOL/BNB/XRP）或新聞事件
    以 JSON 格式寫入 Kinesis Stream。

    失敗策略：
    - ProvisionedThroughputExceededException → 指數退避重試
    - 其他 ClientError / 網路中斷 → 降級至本地記憶體 Buffer
    - 永不拋出例外中斷主流程
    """

    def __init__(
        self,
        region: str | None = None,
        default_stream_name: str = DEFAULT_KINESIS_STREAM,
    ) -> None:
        """初始化 Kinesis 客戶端。

        Args:
            region: AWS Region，預設從環境變數 AWS_REGION 或 ap-northeast-1。
            default_stream_name: 預設 stream 名稱。
        """
        self._region = region or os.environ.get(AWS_REGION_ENV, DEFAULT_REGION)
        self._default_stream = default_stream_name
        self._local_buffer: deque[dict[str, Any]] = deque(maxlen=LOCAL_BUFFER_MAX_SIZE)

        boto_config = Config(
            region_name=self._region,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            read_timeout=READ_TIMEOUT_SECONDS,
            retries={"max_attempts": 0},  # 我們自己管重試
            max_pool_connections=25,
        )
        try:
            self._client = boto3.client("kinesis", config=boto_config)
        except Exception as exc:
            logger.error("Failed to create Kinesis client: %s", exc)
            self._client = None

    @property
    def local_buffer_size(self) -> int:
        """目前本地 buffer 中暫存的記錄數。"""
        return len(self._local_buffer)

    def put_record(
        self,
        stream_name: str | None = None,
        data: dict[str, Any] | None = None,
        partition_key: str = "DEFAULT",
    ) -> bool:
        """將單筆記錄寫入 Kinesis Data Stream。

        Args:
            stream_name: Kinesis stream 名稱，None 時使用預設。
            data: 要寫入的資料字典（會轉為 JSON）。
            partition_key: 分區鍵，建議用幣種代號（如 "BTC"）。

        Returns:
            True 表示成功寫入 Kinesis；False 表示降級至本地 buffer。
        """
        if data is None:
            return False

        stream = stream_name or self._default_stream
        payload = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")

        if self._client is None:
            self._buffer_locally(stream, data, partition_key)
            return False

        for attempt in range(MAX_RETRIES):
            try:
                self._client.put_record(
                    StreamName=stream,
                    Data=payload,
                    PartitionKey=partition_key,
                )
                return True

            except ClientError as exc:
                error_code = exc.response.get("Error", {}).get("Code", "")

                if error_code == "ProvisionedThroughputExceededException":
                    backoff = min(
                        BASE_BACKOFF_SECONDS * (2 ** attempt),
                        MAX_BACKOFF_SECONDS,
                    )
                    logger.warning(
                        "[Kinesis] Throughput exceeded (attempt %d/%d), "
                        "backing off %.2fs",
                        attempt + 1, MAX_RETRIES, backoff,
                    )
                    time.sleep(backoff)
                    continue

                # 權限問題明確告警（避免靜默失敗）
                if error_code in (
                    "AccessDeniedException",
                    "ExpiredTokenException",
                    "UnrecognizedClientException",
                ):
                    logger.error(
                        "[Kinesis] PERMISSION DENIED on put_record "
                        "(stream=%s, region=%s): %s. "
                        "Check IAM credentials and stream policy.",
                        stream, self._region, error_code,
                    )
                else:
                    logger.warning(
                        "[Kinesis] put_record failed: %s - %s",
                        error_code, exc,
                    )
                break

            except Exception as exc:
                logger.warning("[Kinesis] Unexpected error: %s", exc)
                # 連線關閉時重試
                if "Connection was closed" in str(exc) or "EndpointConnectionError" in str(exc):
                    if attempt < MAX_RETRIES - 1:
                        try:
                            boto_config = Config(
                                region_name=self._region,
                                connect_timeout=CONNECT_TIMEOUT_SECONDS,
                                read_timeout=READ_TIMEOUT_SECONDS,
                                retries={"max_attempts": 0},
                                max_pool_connections=25,
                            )
                            self._client = boto3.client("kinesis", config=boto_config)
                        except Exception:
                            pass
                        time.sleep(BASE_BACKOFF_SECONDS * (2 ** attempt))
                        continue
                break

        # 所有重試失敗 → 降級至本地 buffer
        self._buffer_locally(stream, data, partition_key)
        return False

    def put_records_batch(
        self,
        records: list[dict[str, Any]],
        stream_name: str | None = None,
    ) -> dict[str, int]:
        """批次寫入多筆記錄至 Kinesis Data Stream。

        Args:
            records: 記錄清單，每筆需包含 "data" (dict) 和 "partition_key" (str)。
            stream_name: Kinesis stream 名稱。

        Returns:
            {"succeeded": N, "failed": M, "buffered": K} 統計。
        """
        stream = stream_name or self._default_stream
        stats = {"succeeded": 0, "failed": 0, "buffered": 0}

        if not records:
            return stats

        if self._client is None:
            for rec in records:
                self._buffer_locally(
                    stream,
                    rec.get("data", {}),
                    rec.get("partition_key", "DEFAULT"),
                )
            stats["buffered"] = len(records)
            return stats

        # Kinesis PutRecords 一次最多 500 筆
        batch_size = 500
        for i in range(0, len(records), batch_size):
            batch = records[i : i + batch_size]
            kinesis_records = []

            for rec in batch:
                data = rec.get("data", {})
                pk = rec.get("partition_key", "DEFAULT")
                payload = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
                kinesis_records.append({"Data": payload, "PartitionKey": pk})

            for attempt in range(MAX_RETRIES):
                try:
                    response = self._client.put_records(
                        StreamName=stream,
                        Records=kinesis_records,
                    )
                    failed_count = response.get("FailedRecordCount", 0)
                    stats["succeeded"] += len(kinesis_records) - failed_count

                    if failed_count > 0:
                        # 重送失敗的記錄
                        retry_records = []
                        for idx, result in enumerate(response.get("Records", [])):
                            if "ErrorCode" in result:
                                retry_records.append(kinesis_records[idx])

                        if retry_records and attempt < MAX_RETRIES - 1:
                            kinesis_records = retry_records
                            backoff = min(
                                BASE_BACKOFF_SECONDS * (2 ** attempt),
                                MAX_BACKOFF_SECONDS,
                            )
                            time.sleep(backoff)
                            continue
                        else:
                            # 最後一次重試仍失敗 → buffer
                            for rec_data in retry_records:
                                self._local_buffer.append({
                                    "stream": stream,
                                    "data": rec_data["Data"].decode("utf-8"),
                                    "partition_key": rec_data["PartitionKey"],
                                    "buffered_at": time.time(),
                                })
                            stats["buffered"] += len(retry_records)
                    break

                except ClientError as exc:
                    error_code = exc.response.get("Error", {}).get("Code", "")
                    if error_code == "ProvisionedThroughputExceededException":
                        backoff = min(
                            BASE_BACKOFF_SECONDS * (2 ** attempt),
                            MAX_BACKOFF_SECONDS,
                        )
                        logger.warning(
                            "[Kinesis] Batch throughput exceeded (attempt %d), "
                            "backing off %.2fs",
                            attempt + 1, backoff,
                        )
                        time.sleep(backoff)
                        continue
                    logger.warning("[Kinesis] Batch put_records failed: %s", exc)
                    # 權限問題明確告警
                    if error_code in (
                        "AccessDeniedException",
                        "ExpiredTokenException",
                        "UnrecognizedClientException",
                    ):
                        logger.error(
                            "[Kinesis] PERMISSION DENIED on put_records "
                            "(stream=%s, region=%s): %s. "
                            "Check IAM credentials and stream policy.",
                            stream, self._region, error_code,
                        )
                    # 非限流的 ClientError → 降級至 buffer
                    for rec_data in kinesis_records:
                        self._local_buffer.append({
                            "stream": stream,
                            "data": rec_data["Data"].decode("utf-8"),
                            "partition_key": rec_data["PartitionKey"],
                            "buffered_at": time.time(),
                        })
                    stats["buffered"] += len(kinesis_records)
                    break

                except Exception as exc:
                    logger.warning("[Kinesis] Batch unexpected error: %s", exc)
                    # 連線關閉時重試一次（重建 client）
                    if "Connection was closed" in str(exc) or "EndpointConnectionError" in str(exc):
                        if attempt < MAX_RETRIES - 1:
                            logger.info("[Kinesis] Reconnecting client...")
                            try:
                                boto_config = Config(
                                    region_name=self._region,
                                    connect_timeout=CONNECT_TIMEOUT_SECONDS,
                                    read_timeout=READ_TIMEOUT_SECONDS,
                                    retries={"max_attempts": 0},
                                    max_pool_connections=25,
                                )
                                self._client = boto3.client("kinesis", config=boto_config)
                            except Exception:
                                pass
                            backoff = min(
                                BASE_BACKOFF_SECONDS * (2 ** attempt),
                                MAX_BACKOFF_SECONDS,
                            )
                            time.sleep(backoff)
                            continue
                    break
            else:
                # 全部重試失敗
                for rec_data in kinesis_records:
                    self._local_buffer.append({
                        "stream": stream,
                        "data": rec_data["Data"].decode("utf-8"),
                        "partition_key": rec_data["PartitionKey"],
                        "buffered_at": time.time(),
                    })
                stats["buffered"] += len(kinesis_records)

        stats["failed"] = len(records) - stats["succeeded"] - stats["buffered"]
        return stats

    def flush_buffer(self, stream_name: str | None = None) -> int:
        """嘗試將本地 buffer 的記錄重新發送至 Kinesis。

        Returns:
            成功發送的筆數。
        """
        if not self._local_buffer or self._client is None:
            return 0

        stream = stream_name or self._default_stream
        flushed = 0
        remaining: deque[dict[str, Any]] = deque(maxlen=LOCAL_BUFFER_MAX_SIZE)

        while self._local_buffer:
            item = self._local_buffer.popleft()
            try:
                raw_data = item.get("data", "")
                if isinstance(raw_data, str):
                    payload = raw_data.encode("utf-8")
                else:
                    payload = json.dumps(raw_data, ensure_ascii=False, default=str).encode("utf-8")

                self._client.put_record(
                    StreamName=item.get("stream", stream),
                    Data=payload,
                    PartitionKey=item.get("partition_key", "DEFAULT"),
                )
                flushed += 1
            except Exception:
                remaining.append(item)

        self._local_buffer = remaining
        return flushed

    def _buffer_locally(
        self, stream: str, data: dict[str, Any], partition_key: str
    ) -> None:
        """降級：將記錄暫存至本地記憶體 buffer。"""
        self._local_buffer.append({
            "stream": stream,
            "data": data,
            "partition_key": partition_key,
            "buffered_at": time.time(),
        })
        if len(self._local_buffer) % 100 == 0:
            logger.info(
                "[Kinesis] Local buffer size: %d", len(self._local_buffer)
            )


class FirehoseDeliveryProducer:
    """Amazon Data Firehose 寫入器。

    將結構化的 Evidence/Market 數據發送到 Firehose，
    預備自動落地至 Amazon S3 或 OpenSearch。

    失敗策略與 KinesisStreamProducer 相同：
    指數退避 → 本地 buffer → 永不崩潰。
    """

    def __init__(
        self,
        region: str | None = None,
        default_delivery_stream: str = DEFAULT_FIREHOSE_STREAM,
    ) -> None:
        """初始化 Firehose 客戶端。

        Args:
            region: AWS Region。
            default_delivery_stream: 預設 delivery stream 名稱。
        """
        self._region = region or os.environ.get(AWS_REGION_ENV, DEFAULT_REGION)
        self._default_stream = default_delivery_stream
        self._local_buffer: deque[dict[str, Any]] = deque(maxlen=LOCAL_BUFFER_MAX_SIZE)

        boto_config = Config(
            region_name=self._region,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            read_timeout=READ_TIMEOUT_SECONDS,
            retries={"max_attempts": 0},
        )
        try:
            self._client = boto3.client("firehose", config=boto_config)
        except Exception as exc:
            logger.error("Failed to create Firehose client: %s", exc)
            self._client = None

    @property
    def local_buffer_size(self) -> int:
        """目前本地 buffer 中暫存的記錄數。"""
        return len(self._local_buffer)

    def send_to_firehose(
        self,
        delivery_stream: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> bool:
        """將單筆記錄發送到 Firehose Delivery Stream。

        Args:
            delivery_stream: Firehose delivery stream 名稱。
            data: 要發送的資料字典。

        Returns:
            True 表示成功；False 表示降級至本地 buffer。
        """
        if data is None:
            return False

        stream = delivery_stream or self._default_stream
        # Firehose 需要 newline 分隔的 JSON（便於 S3 落地後查詢）
        payload = json.dumps(data, ensure_ascii=False, default=str) + "\n"

        if self._client is None:
            self._buffer_locally(stream, data)
            return False

        for attempt in range(MAX_RETRIES):
            try:
                self._client.put_record(
                    DeliveryStreamName=stream,
                    Record={"Data": payload.encode("utf-8")},
                )
                return True

            except ClientError as exc:
                error_code = exc.response.get("Error", {}).get("Code", "")

                if error_code in (
                    "ServiceUnavailableException",
                    "InternalFailure",
                ):
                    backoff = min(
                        BASE_BACKOFF_SECONDS * (2 ** attempt),
                        MAX_BACKOFF_SECONDS,
                    )
                    logger.warning(
                        "[Firehose] Service unavailable (attempt %d/%d), "
                        "backing off %.2fs",
                        attempt + 1, MAX_RETRIES, backoff,
                    )
                    time.sleep(backoff)
                    continue

                logger.warning(
                    "[Firehose] put_record failed: %s - %s", error_code, exc
                )
                break

            except Exception as exc:
                logger.warning("[Firehose] Unexpected error: %s", exc)
                break

        self._buffer_locally(stream, data)
        return False

    def send_batch(
        self,
        records: list[dict[str, Any]],
        delivery_stream: str | None = None,
    ) -> dict[str, int]:
        """批次發送多筆記錄至 Firehose。

        Args:
            records: 資料字典清單。
            delivery_stream: Firehose delivery stream 名稱。

        Returns:
            {"succeeded": N, "failed": M, "buffered": K} 統計。
        """
        stream = delivery_stream or self._default_stream
        stats = {"succeeded": 0, "failed": 0, "buffered": 0}

        if not records:
            return stats

        if self._client is None:
            for rec in records:
                self._buffer_locally(stream, rec)
            stats["buffered"] = len(records)
            return stats

        # Firehose PutRecordBatch 一次最多 500 筆
        batch_size = 500
        for i in range(0, len(records), batch_size):
            batch = records[i : i + batch_size]
            firehose_records = [
                {
                    "Data": (
                        json.dumps(rec, ensure_ascii=False, default=str) + "\n"
                    ).encode("utf-8")
                }
                for rec in batch
            ]

            try:
                response = self._client.put_record_batch(
                    DeliveryStreamName=stream,
                    Records=firehose_records,
                )
                failed_count = response.get("FailedPutCount", 0)
                stats["succeeded"] += len(firehose_records) - failed_count
                stats["failed"] += failed_count

            except Exception as exc:
                logger.warning("[Firehose] Batch send failed: %s", exc)
                for rec in batch:
                    self._buffer_locally(stream, rec)
                stats["buffered"] += len(batch)

        return stats

    def _buffer_locally(self, stream: str, data: dict[str, Any]) -> None:
        """降級：將記錄暫存至本地記憶體 buffer。"""
        self._local_buffer.append({
            "stream": stream,
            "data": data,
            "buffered_at": time.time(),
        })
        if len(self._local_buffer) % 100 == 0:
            logger.info(
                "[Firehose] Local buffer size: %d", len(self._local_buffer)
            )


__all__ = [
    "DEFAULT_FIREHOSE_STREAM",
    "DEFAULT_KINESIS_STREAM",
    "FirehoseDeliveryProducer",
    "KinesisStreamProducer",
]
