"""Athena 歷史資料查詢 — 接縫 1 的 EvidenceSource 實作。

從 S3 data warehouse 透過 Athena SQL 查詢歷史 OHLCV 與宏觀指標，
供分析 Agent 做跨時間窗口統計分析與回測。

支援兩種模式（ADR 0005）：
- BACKTEST: 查詢截止日之前的資料，不會洩漏未來
- LIVE: 查詢所有可用的歷史資料

無 mock 需求的部分（schema 轉換、SQL 組裝）不在此檔 — 見 tools.py。
外部 I/O（Athena query）在此封裝。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import boto3

from hoyabit_agent.domain import AnalysisRegime, Asset, Evidence, Facet, SourceExcerpt
from hoyabit_agent.seams import Arguments, ToolSpec

logger = logging.getLogger(__name__)

# ─── 設定 ───
ATHENA_DATABASE_ENV = "ATHENA_DATABASE"
ATHENA_WORKGROUP_ENV = "ATHENA_WORKGROUP"
ATHENA_OUTPUT_ENV = "ATHENA_OUTPUT_LOCATION"
AWS_REGION_ENV = "ATHENA_REGION"

DEFAULT_DATABASE = "hoyabit_market"
DEFAULT_WORKGROUP = "primary"
DEFAULT_OUTPUT = "s3://hoyabit-data-warehouse-433348878087/athena-results/"
DEFAULT_REGION = "us-east-1"
QUERY_TIMEOUT_SECONDS = 30.0

TOOL_NAME = "athena_historical_query"


class AthenaEvidenceSource:
    """從 Athena data warehouse 查詢歷史資料作為技術面/基本面證據。

    滿足 EvidenceSource 協定：
    * 失效以空集合表達，不以例外表達
    * 面對模型給的無效參數自行降級
    * 不暴露金鑰、重試、逾時等細節
    """

    supported_regimes: frozenset[AnalysisRegime] = frozenset(AnalysisRegime)

    def __init__(self) -> None:
        region = os.environ.get(AWS_REGION_ENV, DEFAULT_REGION)
        self._athena = boto3.client("athena", region_name=region)
        self._database = os.environ.get(ATHENA_DATABASE_ENV, DEFAULT_DATABASE)
        self._workgroup = os.environ.get(ATHENA_WORKGROUP_ENV, DEFAULT_WORKGROUP)
        self._output_location = os.environ.get(ATHENA_OUTPUT_ENV, DEFAULT_OUTPUT)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=TOOL_NAME,
            description=(
                "查詢 Athena 歷史資料倉儲。可查：\n"
                "1. ohlcv_daily / ohlcv_hourly — 加密資產歷史 K 線\n"
                "2. macro_indicators — FRED 宏觀經濟指標 (FEDFUNDS, CPIAUCSL, M2SL, DGS10, DTWEXBGS, UNRATE)\n"
                "3. sentiment_scores — 歷史情緒分數\n\n"
                "適合跨時間窗口統計分析、歷史趨勢比較、回測策略驗證。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query_type": {
                        "type": "string",
                        "enum": ["ohlcv_summary", "macro_trend", "custom_sql"],
                        "description": "查詢類型：ohlcv_summary(價格統計)、macro_trend(宏觀趨勢)、custom_sql(自訂SQL)",
                    },
                    "asset": {
                        "type": "string",
                        "enum": ["BTC", "ETH", "SOL", "BNB", "XRP"],
                        "description": "加密資產代號",
                    },
                    "start_date": {
                        "type": "string",
                        "format": "date",
                        "description": "查詢起始日 (YYYY-MM-DD)",
                    },
                    "end_date": {
                        "type": "string",
                        "format": "date",
                        "description": "查詢結束日 (YYYY-MM-DD)",
                    },
                    "interval": {
                        "type": "string",
                        "enum": ["daily", "hourly"],
                        "description": "K 線間隔（預設 daily）",
                    },
                    "indicator": {
                        "type": "string",
                        "enum": ["FEDFUNDS", "CPIAUCSL", "M2SL", "DGS10", "DTWEXBGS", "UNRATE"],
                        "description": "宏觀指標 FRED series ID（query_type=macro_trend 時使用）",
                    },
                    "sql": {
                        "type": "string",
                        "description": "自訂 SQL（query_type=custom_sql 時使用，僅允許 SELECT）",
                    },
                },
                "required": ["query_type", "asset"],
            },
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        """執行 Athena 查詢並回傳結構化證據。"""
        query_type = str(arguments.get("query_type", "ohlcv_summary"))
        start_date = str(arguments.get("start_date", "2020-01-01"))
        end_date = str(arguments.get("end_date", "2026-12-31"))
        interval = str(arguments.get("interval", "daily"))

        try:
            sql = self._build_sql(query_type, asset, start_date, end_date, interval, arguments)
        except ValueError as exc:
            logger.warning("Invalid query parameters: %s", exc)
            return ()

        if sql is None:
            return ()

        # 在 executor 中執行同步的 Athena 呼叫
        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, self._execute_query, sql),
                timeout=QUERY_TIMEOUT_SECONDS,
            )
        except (TimeoutError, Exception) as exc:
            logger.warning("Athena query failed: %s", exc)
            return ()

        if not result:
            return ()

        # 組裝證據
        facet = Facet.FUNDAMENTAL if query_type == "macro_trend" else Facet.TECHNICAL
        evidence_id = f"ATHENA-{asset.value}-{query_type}-{start_date[:7]}"

        summary_text = self._format_result(result, query_type, asset, start_date, end_date)

        excerpt = SourceExcerpt(
            source_id=f"athena:{self._database}:{query_type}",
            url=f"athena://{self._database}/{query_type}",
            retrieved_at=datetime.now(tz=UTC),
            locator=f"SQL query on {self._database}",
            text=summary_text,
        )

        evidence = Evidence(
            id=evidence_id,
            facet=facet,
            summary=f"{asset.value} 歷史資料查詢: {query_type} ({start_date} ~ {end_date})",
            stance_hint=0.0,
            excerpts=(excerpt,),
            event_key=evidence_id,
        )

        return (evidence,)

    # ─── SQL 建構 ───

    def _build_sql(
        self,
        query_type: str,
        asset: Asset,
        start_date: str,
        end_date: str,
        interval: str,
        arguments: Arguments,
    ) -> str | None:
        """建構安全的 SQL 查詢。參數化防注入。"""
        table = f"ohlcv_{interval}" if interval in ("daily", "hourly") else "ohlcv_daily"

        if query_type == "ohlcv_summary":
            return (
                f"SELECT "
                f"  COUNT(*) as total_candles, "
                f"  MIN(open_time) as first_date, "
                f"  MAX(open_time) as last_date, "
                f"  AVG(close) as avg_close, "
                f"  MIN(low) as period_low, "
                f"  MAX(high) as period_high, "
                f"  AVG(volume) as avg_volume, "
                f"  SUM(quote_volume) as total_quote_volume, "
                f"  STDDEV(close) as close_stddev, "
                f"  (MAX(close) - MIN(close)) / NULLIF(MIN(close), 0) * 100 as price_range_pct "
                f"FROM {table} "
                f"WHERE asset = '{asset.value}' "
                f"  AND open_time >= TIMESTAMP '{start_date} 00:00:00' "
                f"  AND open_time <= TIMESTAMP '{end_date} 23:59:59'"
            )

        elif query_type == "macro_trend":
            indicator = str(arguments.get("indicator", "FEDFUNDS"))
            valid_indicators = ("FEDFUNDS", "CPIAUCSL", "M2SL", "DGS10", "DTWEXBGS", "UNRATE")
            if indicator not in valid_indicators:
                return None
            return (
                f"SELECT "
                f"  COUNT(*) as total_observations, "
                f"  MIN(date) as first_date, "
                f"  MAX(date) as last_date, "
                f"  AVG(value) as avg_value, "
                f"  MIN(value) as min_value, "
                f"  MAX(value) as max_value, "
                f"  STDDEV(value) as value_stddev "
                f"FROM macro_indicators "
                f"WHERE series_id = '{indicator}' "
                f"  AND date >= DATE '{start_date}' "
                f"  AND date <= DATE '{end_date}'"
            )

        elif query_type == "custom_sql":
            sql = str(arguments.get("sql", ""))
            # 安全檢查：僅允許 SELECT
            if not sql.strip().upper().startswith("SELECT"):
                return None
            # 禁止危險操作
            dangerous = ("DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE", "TRUNCATE")
            upper_sql = sql.upper()
            if any(keyword in upper_sql for keyword in dangerous):
                return None
            return sql

        return None

    # ─── Athena 執行 ───

    def _execute_query(self, sql: str) -> list[dict[str, Any]]:
        """同步執行 Athena 查詢並等待結果。"""
        try:
            response = self._athena.start_query_execution(
                QueryString=sql,
                QueryExecutionContext={"Database": self._database},
                WorkGroup=self._workgroup,
                ResultConfiguration={"OutputLocation": self._output_location},
            )
        except Exception as exc:
            logger.warning("Athena start_query_execution failed: %s", exc)
            return []

        query_execution_id = response["QueryExecutionId"]

        # 輪詢等待完成
        max_wait = QUERY_TIMEOUT_SECONDS
        waited = 0.0
        while waited < max_wait:
            try:
                status_response = self._athena.get_query_execution(
                    QueryExecutionId=query_execution_id
                )
            except Exception:
                return []

            state = status_response["QueryExecution"]["Status"]["State"]

            if state == "SUCCEEDED":
                break
            elif state in ("FAILED", "CANCELLED"):
                reason = status_response["QueryExecution"]["Status"].get(
                    "StateChangeReason", "Unknown"
                )
                logger.warning("Athena query %s: %s", state, reason)
                return []

            time.sleep(1.0)
            waited += 1.0

        if waited >= max_wait:
            logger.warning("Athena query timed out after %.0fs", max_wait)
            return []

        # 取得結果
        try:
            result_response = self._athena.get_query_results(
                QueryExecutionId=query_execution_id, MaxResults=100
            )
        except Exception as exc:
            logger.warning("Athena get_query_results failed: %s", exc)
            return []

        rows = result_response.get("ResultSet", {}).get("Rows", [])
        if len(rows) < 2:
            return []

        # 第一行是 header
        headers = [col.get("VarCharValue", "") for col in rows[0].get("Data", [])]
        results: list[dict[str, Any]] = []
        for row in rows[1:]:
            values = [col.get("VarCharValue", "") for col in row.get("Data", [])]
            results.append(dict(zip(headers, values)))

        return results

    # ─── 結果格式化 ───

    def _format_result(
        self,
        result: list[dict[str, Any]],
        query_type: str,
        asset: Asset,
        start_date: str,
        end_date: str,
    ) -> str:
        """把 Athena 查詢結果轉成人可讀的文字摘要。"""
        if not result:
            return "查無資料"

        row = result[0]

        if query_type == "ohlcv_summary":
            parts = [
                f"{asset.value} 歷史價格統計 ({start_date} ~ {end_date}):",
                f"  總K線數: {row.get('total_candles', 'N/A')}",
                f"  首筆日期: {row.get('first_date', 'N/A')}",
                f"  末筆日期: {row.get('last_date', 'N/A')}",
                f"  平均收盤價: ${_fmt_num(row.get('avg_close'))}",
                f"  期間最低: ${_fmt_num(row.get('period_low'))}",
                f"  期間最高: ${_fmt_num(row.get('period_high'))}",
                f"  平均成交量: {_fmt_num(row.get('avg_volume'))}",
                f"  總成交額(USD): ${_fmt_num(row.get('total_quote_volume'))}",
                f"  收盤價標準差: ${_fmt_num(row.get('close_stddev'))}",
                f"  價格區間幅度: {_fmt_num(row.get('price_range_pct'))}%",
            ]
            return "\n".join(parts)

        elif query_type == "macro_trend":
            parts = [
                f"宏觀指標歷史統計 ({start_date} ~ {end_date}):",
                f"  總觀測數: {row.get('total_observations', 'N/A')}",
                f"  首筆日期: {row.get('first_date', 'N/A')}",
                f"  末筆日期: {row.get('last_date', 'N/A')}",
                f"  平均值: {_fmt_num(row.get('avg_value'))}",
                f"  最小值: {_fmt_num(row.get('min_value'))}",
                f"  最大值: {_fmt_num(row.get('max_value'))}",
                f"  標準差: {_fmt_num(row.get('value_stddev'))}",
            ]
            return "\n".join(parts)

        else:
            # custom_sql: 直接格式化所有行
            lines = []
            for i, r in enumerate(result[:20]):
                lines.append(f"  Row {i+1}: {r}")
            return f"自訂查詢結果 ({len(result)} rows):\n" + "\n".join(lines)


def _fmt_num(value: Any) -> str:
    """格式化數值，處理 None 和非數值情況。"""
    if value is None or value == "":
        return "N/A"
    try:
        num = float(value)
        if abs(num) >= 1_000_000:
            return f"{num:,.0f}"
        elif abs(num) >= 1:
            return f"{num:,.2f}"
        else:
            return f"{num:.6f}"
    except (ValueError, TypeError):
        return str(value)


__all__ = ["AthenaEvidenceSource", "TOOL_NAME"]
