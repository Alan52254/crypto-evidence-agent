"""S3 Parquet 寫入器 — 從 Binance / FRED 抓歷史資料存到 S3 data warehouse。

用法：
    python -m hoyabit_agent.ingest.s3_writer --asset BTC --start 2020-01-01
    python -m hoyabit_agent.ingest.s3_writer --macro --start 2020-01-01
    python -m hoyabit_agent.ingest.s3_writer --asset BTC --interval hourly --start 2024-01-01

無 mock 需求（純 I/O 層），測試用 httpx.MockTransport + moto。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

# ─── 設定 ───
S3_BUCKET_ENV = "S3_DATA_BUCKET"
DEFAULT_BUCKET = "hoyabit-data-warehouse-433348878087"
AWS_REGION_ENV = "BEDROCK_REGION"
DEFAULT_REGION = "us-east-1"

SUPPORTED_ASSETS = ("BTC", "ETH", "SOL", "BNB", "XRP")

# FRED 系列：與分析 Agent 使用的宏觀指標一致
FRED_SERIES = {
    "FEDFUNDS": "Federal Funds Effective Rate",
    "CPIAUCSL": "CPI All Urban Consumers",
    "M2SL": "M2 Money Stock",
    "DGS10": "10-Year Treasury Rate",
    "DTWEXBGS": "Trade-Weighted Dollar Index",
    "UNRATE": "Unemployment Rate",
}

# Binance kline intervals
INTERVAL_MAP = {
    "daily": ("1d", 1),
    "hourly": ("1h", 24),
}


# ─── Schemas ───

OHLCV_SCHEMA = pa.schema([
    ("open_time", pa.timestamp("ms", tz="UTC")),
    ("open", pa.float64()),
    ("high", pa.float64()),
    ("low", pa.float64()),
    ("close", pa.float64()),
    ("volume", pa.float64()),
    ("quote_volume", pa.float64()),
    ("trades", pa.int64()),
])

MACRO_SCHEMA = pa.schema([
    ("date", pa.date32()),
    ("value", pa.float64()),
    ("indicator", pa.string()),
])

SENTIMENT_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("ms", tz="UTC")),
    ("score", pa.float64()),
    ("source", pa.string()),
    ("headline", pa.string()),
])


# ─── Binance OHLCV 抓取 ───

async def fetch_binance_klines(
    client: httpx.AsyncClient,
    asset: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[dict[str, Any]]:
    """從 Binance 公開 API 抓 klines，自動分頁（每次最多 1000 根）。"""
    symbol = f"{asset}USDT"
    url = "https://api.binance.com/api/v3/klines"
    all_klines: list[dict[str, Any]] = []
    current_start = start_ms

    while current_start < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": 1000,
        }
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Binance API error: %s", exc)
            break

        data = resp.json()
        if not data:
            break

        for kline in data:
            all_klines.append({
                "open_time": datetime.fromtimestamp(kline[0] / 1000, tz=timezone.utc),
                "open": float(kline[1]),
                "high": float(kline[2]),
                "low": float(kline[3]),
                "close": float(kline[4]),
                "volume": float(kline[5]),
                "quote_volume": float(kline[7]),
                "trades": int(kline[8]),
            })

        # 下一頁起點
        last_open_time = data[-1][0]
        current_start = last_open_time + 1

        # Rate limit 保護
        await asyncio.sleep(0.2)

    return all_klines


# ─── FRED 抓取 ───

async def fetch_fred_series(
    client: httpx.AsyncClient,
    series_id: str,
    start_date: str,
    api_key: str,
) -> list[dict[str, Any]]:
    """從 FRED API 抓宏觀指標時序資料。"""
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start_date,
        "sort_order": "asc",
    }
    try:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("FRED API error for %s: %s", series_id, exc)
        return []

    data = resp.json()
    observations = data.get("observations", [])

    results: list[dict[str, Any]] = []
    for obs in observations:
        value_str = obs.get("value", ".")
        if value_str == ".":
            continue
        try:
            results.append({
                "date": date.fromisoformat(obs["date"]),
                "value": float(value_str),
                "indicator": series_id,
            })
        except (ValueError, KeyError):
            continue

    return results


# ─── S3 寫入 ───

def _get_s3_client():
    """建立 boto3 S3 client。"""
    import boto3

    region = os.environ.get(AWS_REGION_ENV, DEFAULT_REGION)
    return boto3.client("s3", region_name=region)


def write_parquet_to_s3(
    table: pa.Table,
    bucket: str,
    key: str,
) -> None:
    """把 PyArrow Table 寫成 Parquet 上傳到 S3。"""
    s3 = _get_s3_client()
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    buf.seek(0)
    s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue())
    logger.info("Uploaded: s3://%s/%s (%d rows)", bucket, key, len(table))


def _add_glue_partition(
    database: str,
    table_name: str,
    partition_values: list[str],
    s3_location: str,
) -> None:
    """向 Glue 註冊新的分區。"""
    import boto3

    region = os.environ.get(AWS_REGION_ENV, DEFAULT_REGION)
    glue = boto3.client("glue", region_name=region)

    try:
        glue.create_partition(
            DatabaseName=database,
            TableName=table_name,
            PartitionInput={
                "Values": partition_values,
                "StorageDescriptor": {
                    "Location": s3_location,
                    "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                    "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                    "SerdeInfo": {
                        "SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
                    },
                },
            },
        )
    except glue.exceptions.AlreadyExistsException:
        pass  # 分區已存在，跳過


# ─── 高層寫入函式 ───

async def ingest_ohlcv(
    asset: str,
    interval_key: str,
    start_date: date,
    end_date: date | None = None,
) -> int:
    """抓取 OHLCV 資料並寫入 S3，按月分區。回傳寫入總行數。"""
    if end_date is None:
        end_date = date.today()

    bucket = os.environ.get(S3_BUCKET_ENV, DEFAULT_BUCKET)
    binance_interval, _ = INTERVAL_MAP[interval_key]
    table_name = f"ohlcv_{interval_key}"

    start_ms = int(datetime(start_date.year, start_date.month, start_date.day,
                            tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime(end_date.year, end_date.month, end_date.day,
                          tzinfo=timezone.utc).timestamp() * 1000)

    total_rows = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        klines = await fetch_binance_klines(client, asset, binance_interval, start_ms, end_ms)

    if not klines:
        logger.warning("No klines fetched for %s %s", asset, interval_key)
        return 0

    # 按月分組寫入
    months: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for k in klines:
        ot: datetime = k["open_time"]
        key = (str(ot.year), f"{ot.month:02d}")
        months.setdefault(key, []).append(k)

    for (year, month), rows in sorted(months.items()):
        table = pa.Table.from_pydict(
            {col: [r[col] for r in rows] for col in OHLCV_SCHEMA.names},
            schema=OHLCV_SCHEMA,
        )
        s3_key = f"raw/{table_name}/asset={asset}/year={year}/month={month}/data.parquet"
        write_parquet_to_s3(table, bucket, s3_key)

        # 註冊 Glue 分區
        s3_location = f"s3://{bucket}/raw/{table_name}/asset={asset}/year={year}/month={month}/"
        _add_glue_partition("hoyabit_market", table_name, [asset, year, month], s3_location)

        total_rows += len(rows)

    return total_rows


async def ingest_macro(start_date: date, end_date: date | None = None) -> int:
    """抓取 FRED 宏觀指標並寫入 S3，按 series/year 分區。"""
    fred_key = os.environ.get("FRED_API_KEY", "")
    if not fred_key:
        logger.error("FRED_API_KEY not set")
        return 0

    if end_date is None:
        end_date = date.today()

    bucket = os.environ.get(S3_BUCKET_ENV, DEFAULT_BUCKET)
    total_rows = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        for series_id in FRED_SERIES:
            observations = await fetch_fred_series(
                client, series_id, start_date.isoformat(), fred_key
            )
            if not observations:
                continue

            # 按年分組
            years: dict[str, list[dict[str, Any]]] = {}
            for obs in observations:
                y = str(obs["date"].year)
                years.setdefault(y, []).append(obs)

            for year, rows in sorted(years.items()):
                table = pa.Table.from_pydict(
                    {col: [r[col] for r in rows] for col in MACRO_SCHEMA.names},
                    schema=MACRO_SCHEMA,
                )
                s3_key = f"raw/macro_indicators/series_id={series_id}/year={year}/data.parquet"
                write_parquet_to_s3(table, bucket, s3_key)

                s3_location = f"s3://{bucket}/raw/macro_indicators/series_id={series_id}/year={year}/"
                _add_glue_partition("hoyabit_market", "macro_indicators", [series_id, year], s3_location)

                total_rows += len(rows)

            # Rate limit
            await asyncio.sleep(0.5)

    return total_rows


# ─── CLI 進入點 ───

def main() -> int:
    """CLI: python -m hoyabit_agent.ingest.s3_writer"""
    from hoyabit_agent.config import load_dotenv

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Ingest historical data to S3 Parquet")
    parser.add_argument("--asset", type=str, help="Asset symbol (BTC, ETH, SOL, BNB, XRP)")
    parser.add_argument("--interval", type=str, default="daily", choices=["daily", "hourly"])
    parser.add_argument("--macro", action="store_true", help="Ingest FRED macro indicators")
    parser.add_argument("--start", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--all-assets", action="store_true", help="Ingest all supported assets")

    args = parser.parse_args()
    load_dotenv()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else None

    async def run() -> int:
        total = 0

        if args.macro:
            print(f"Ingesting FRED macro indicators from {start}...")
            rows = await ingest_macro(start, end)
            print(f"  ✓ Macro: {rows} rows written")
            total += rows

        assets = list(SUPPORTED_ASSETS) if args.all_assets else ([args.asset] if args.asset else [])

        for asset in assets:
            if asset not in SUPPORTED_ASSETS:
                print(f"  ✗ Unsupported asset: {asset}")
                continue
            print(f"Ingesting {asset} {args.interval} OHLCV from {start}...")
            rows = await ingest_ohlcv(asset, args.interval, start, end)
            print(f"  ✓ {asset}: {rows} rows written")
            total += rows

        if not args.macro and not assets:
            print("Error: specify --asset, --all-assets, or --macro")
            return 1

        print(f"\nDone. Total rows: {total}")
        return 0

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
