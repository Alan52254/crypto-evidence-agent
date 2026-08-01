"""DynamoDB 快取層 — Session Memory + Query Cache。

兩張表：
1. hoyabit_agent_sessions — 多輪對話歷史記憶
2. hoyabit_query_cache — Athena/Kinesis 查詢結果快取 (TTL 300s)

穩健性原則（與本專案所有證據源一致）：
- DynamoDB 連線失敗 → 平滑 Fallback 至 In-Memory Dict
- 永不拋出未處理例外
- 任何外部 I/O 錯誤以空/降級表達，不阻斷分析回合
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError
except ImportError:  # boto3 是 optional dependency —— 未安裝時整層降級為 in-memory
    boto3 = None  # type: ignore[assignment]
    Config = None  # type: ignore[assignment, misc]
    ClientError = Exception  # type: ignore[assignment, misc]

logger = logging.getLogger(__name__)

# ─── 設定 ───
AWS_REGION_ENV = "AWS_REGION"
DEFAULT_REGION = "us-east-1"

SESSION_TABLE = os.environ.get("DYNAMODB_SESSION_TABLE", "hoyabit-cache")
CACHE_TABLE = os.environ.get("DYNAMODB_CACHE_TABLE", "hoyabit-cache")

DEFAULT_CACHE_TTL_SECONDS = 300  # 5 分鐘
CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 5.0


class DynamoDBCache:
    """DynamoDB 快取客戶端 — Session Memory + Query Cache。

    初始化失敗時自動降級為 In-Memory Dict，保證 100% 不拋 500。
    """

    def __init__(self) -> None:
        self._region = os.environ.get(AWS_REGION_ENV, DEFAULT_REGION)
        self._client: Any | None = None
        self._degraded = False

        # In-Memory Fallback 結構
        self._mem_sessions: dict[str, list[dict[str, str]]] = {}
        self._mem_cache: dict[str, dict[str, Any]] = {}

        self._init_client()

    def _init_client(self) -> None:
        """嘗試建立 DynamoDB client；失敗則標記降級。"""
        if boto3 is None:
            logger.warning("[DynamoDBCache] boto3 未安裝，fallback to in-memory")
            self._client = None
            self._degraded = True
            return
        try:
            boto_config = Config(
                region_name=self._region,
                connect_timeout=CONNECT_TIMEOUT,
                read_timeout=READ_TIMEOUT,
                retries={"max_attempts": 2},
            )
            self._client = boto3.client("dynamodb", config=boto_config)
            logger.info("[DynamoDBCache] Client created (region: %s)", self._region)
        except Exception as exc:
            logger.warning("[DynamoDBCache] Client init failed, fallback to in-memory: %s", exc)
            self._client = None
            self._degraded = True

    @property
    def is_degraded(self) -> bool:
        """是否正在使用 In-Memory Fallback。"""
        return self._degraded

    # ═══════════════════════════════════════════════════════════════════
    # Table Management
    # ═══════════════════════════════════════════════════════════════════

    def ensure_tables_exist(self) -> None:
        """檢查並建立兩張 DynamoDB 資料表（若不存在）。

        - hoyabit_agent_sessions (PK: session_id [S])
        - hoyabit_query_cache (PK: cache_key [S], TTL 屬性: ttl)
        """
        if self._client is None:
            return

        self._create_table_if_not_exists(
            table_name=SESSION_TABLE,
            key_schema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
            attribute_definitions=[{"AttributeName": "session_id", "AttributeType": "S"}],
        )

        self._create_table_if_not_exists(
            table_name=CACHE_TABLE,
            key_schema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            attribute_definitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            ttl_attribute="ttl",
        )

    def _create_table_if_not_exists(
        self,
        table_name: str,
        key_schema: list[dict[str, str]],
        attribute_definitions: list[dict[str, str]],
        ttl_attribute: str | None = None,
    ) -> None:
        """建立單張表（冪等）。"""
        if self._client is None:
            return
        try:
            self._client.describe_table(TableName=table_name)
            logger.info("[DynamoDBCache] Table '%s' already exists", table_name)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                self._do_create_table(table_name, key_schema, attribute_definitions, ttl_attribute)
            else:
                logger.warning("[DynamoDBCache] describe_table error: %s", exc)
                self._degrade()
        except Exception as exc:
            logger.warning("[DynamoDBCache] describe_table failed: %s", exc)
            self._degrade()

    def _do_create_table(
        self,
        table_name: str,
        key_schema: list[dict[str, str]],
        attribute_definitions: list[dict[str, str]],
        ttl_attribute: str | None,
    ) -> None:
        """實際建表並可選啟用 TTL。"""
        if self._client is None:
            return
        try:
            self._client.create_table(
                TableName=table_name,
                KeySchema=key_schema,
                AttributeDefinitions=attribute_definitions,
                BillingMode="PAY_PER_REQUEST",
            )
            logger.info("[DynamoDBCache] Created table '%s'", table_name)

            # 等待表就緒（最多 30 秒）
            waiter = self._client.get_waiter("table_exists")
            waiter.wait(TableName=table_name, WaiterConfig={"Delay": 2, "MaxAttempts": 15})

            # 啟用 TTL
            if ttl_attribute:
                self._client.update_time_to_live(
                    TableName=table_name,
                    TimeToLiveSpecification={
                        "Enabled": True,
                        "AttributeName": ttl_attribute,
                    },
                )
                logger.info("[DynamoDBCache] TTL enabled on '%s.%s'", table_name, ttl_attribute)

        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceInUseException":
                # 並行建立，表已存在
                logger.info("[DynamoDBCache] Table '%s' created concurrently", table_name)
            else:
                logger.warning("[DynamoDBCache] create_table error: %s", exc)
                self._degrade()
        except Exception as exc:
            logger.warning("[DynamoDBCache] create_table failed: %s", exc)
            self._degrade()

    # ═══════════════════════════════════════════════════════════════════
    # Session Memory API
    # ═══════════════════════════════════════════════════════════════════

    def get_session_memory(self, session_id: str) -> list[dict[str, str]]:
        """取得指定 session 的對話歷史。

        Returns:
            list of {"role": "user"|"assistant", "content": "..."}
        """
        if self._degraded or self._client is None:
            return self._mem_sessions.get(session_id, [])

        try:
            resp = self._client.get_item(
                TableName=SESSION_TABLE,
                Key={"pk": {"S": f"session:{session_id}"}},
            )
            item = resp.get("Item")
            if item and "chat_history" in item:
                return json.loads(item["chat_history"]["S"])
            return []
        except Exception as exc:
            logger.warning("[DynamoDBCache] get_session_memory failed: %s", exc)
            self._degrade()
            return self._mem_sessions.get(session_id, [])

    def save_session_memory(self, session_id: str, role: str, content: str) -> None:
        """追加一筆對話至 session 歷史。"""
        entry = {"role": role, "content": content}

        # 始終更新 in-memory（作為 fallback 的一致性保證）
        self._mem_sessions.setdefault(session_id, []).append(entry)

        if self._degraded or self._client is None:
            return

        try:
            history = self.get_session_memory(session_id)
            history.append(entry)
            self._client.put_item(
                TableName=SESSION_TABLE,
                Item={
                    "pk": {"S": f"session:{session_id}"},
                    "chat_history": {"S": json.dumps(history, ensure_ascii=False)},
                    "updated_at": {"N": str(int(time.time()))},
                },
            )
        except Exception as exc:
            logger.warning("[DynamoDBCache] save_session_memory failed: %s", exc)
            self._degrade()

    # ═══════════════════════════════════════════════════════════════════
    # Query Cache API
    # ═══════════════════════════════════════════════════════════════════

    def get_cached_query(self, cache_key: str) -> dict[str, Any] | None:
        """查詢快取。回傳 None 表示 miss 或已過期。"""
        if self._degraded or self._client is None:
            return self._get_mem_cache(cache_key)

        try:
            resp = self._client.get_item(
                TableName=CACHE_TABLE,
                Key={"pk": {"S": f"cache:{cache_key}"}},
            )
            item = resp.get("Item")
            if not item:
                return None

            # 檢查 TTL（DynamoDB TTL 刪除有延遲，需自行驗證）
            ttl_val = int(item.get("ttl", {}).get("N", "0"))
            if ttl_val < int(time.time()):
                return None

            response_data = item.get("response_data", {}).get("S")
            if response_data:
                return json.loads(response_data)
            return None
        except Exception as exc:
            logger.warning("[DynamoDBCache] get_cached_query failed: %s", exc)
            self._degrade()
            return self._get_mem_cache(cache_key)

    def set_cached_query(
        self, cache_key: str, data: dict[str, Any], ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS
    ) -> None:
        """寫入查詢快取並設定 TTL。"""
        now = int(time.time())
        ttl_epoch = now + ttl_seconds

        # 始終更新 in-memory
        self._mem_cache[cache_key] = {"data": data, "ttl": ttl_epoch}

        if self._degraded or self._client is None:
            return

        try:
            self._client.put_item(
                TableName=CACHE_TABLE,
                Item={
                    "pk": {"S": f"cache:{cache_key}"},
                    "response_data": {"S": json.dumps(data, ensure_ascii=False)},
                    "ttl": {"N": str(ttl_epoch)},
                    "created_at": {"N": str(now)},
                },
            )
        except Exception as exc:
            logger.warning("[DynamoDBCache] set_cached_query failed: %s", exc)
            self._degrade()

    # ═══════════════════════════════════════════════════════════════════
    # Cache Key Helpers
    # ═══════════════════════════════════════════════════════════════════

    @staticmethod
    def build_cache_key(tool_name: str, asset: str, arguments: dict[str, Any]) -> str:
        """組裝一致的 cache key — tool + asset + 排序後的參數雜湊。"""
        arg_str = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        return f"{tool_name}:{asset}:{arg_str}"

    # ─── Internal ───

    def _get_mem_cache(self, cache_key: str) -> dict[str, Any] | None:
        """從 in-memory fallback 取得快取（含 TTL 檢查）。"""
        entry = self._mem_cache.get(cache_key)
        if entry is None:
            return None
        if entry["ttl"] < int(time.time()):
            del self._mem_cache[cache_key]
            return None
        return entry["data"]

    def _degrade(self) -> None:
        """切換至降級模式。"""
        if not self._degraded:
            logger.warning("[DynamoDBCache] Degrading to in-memory fallback")
            self._degraded = True


# ─── 模組級單例 ───
_instance: DynamoDBCache | None = None


def get_cache() -> DynamoDBCache:
    """取得全域 DynamoDBCache 單例。"""
    global _instance
    if _instance is None:
        _instance = DynamoDBCache()
    return _instance


__all__ = ["DynamoDBCache", "get_cache"]
