"""獨立 Athena 連線測試腳本 — 發送 SELECT 1 驗證 boto3→Athena 通路。

用法：
    python test_athena_push.py
"""

import os
import sys
import time
from pathlib import Path

# 載入 .env
env_path = Path(__file__).parent / ".env"
if env_path.is_file():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get("ATHENA_REGION", "us-east-1")
DATABASE = os.environ.get("ATHENA_DATABASE", "hoyabit_market")
WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "primary")
OUTPUT_LOCATION = os.environ.get(
    "ATHENA_OUTPUT_LOCATION",
    "s3://hoyabit-data-warehouse-433348878087/athena-results/",
)

SQL = "SELECT 1 AS test_value;"


def main() -> int:
    print(f"[Config]")
    print(f"  Region:          {REGION}")
    print(f"  Database:        {DATABASE}")
    print(f"  Workgroup:       {WORKGROUP}")
    print(f"  OutputLocation:  {OUTPUT_LOCATION}")
    print(f"  SQL:             {SQL}")
    print()

    client = boto3.client("athena", region_name=REGION)

    # 1. 發起查詢
    print("[Step 1] start_query_execution ...")
    try:
        response = client.start_query_execution(
            QueryString=SQL,
            QueryExecutionContext={"Database": DATABASE},
            WorkGroup=WORKGROUP,
            ResultConfiguration={"OutputLocation": OUTPUT_LOCATION},
        )
    except ClientError as exc:
        print(f"  FAILED: {exc}")
        return 1

    query_id = response["QueryExecutionId"]
    print(f"  QueryExecutionId: {query_id}")
    print()

    # 2. 輪詢等待結果
    print("[Step 2] Polling query status ...")
    max_wait = 30
    waited = 0.0
    state = "QUEUED"

    while waited < max_wait:
        try:
            status_resp = client.get_query_execution(QueryExecutionId=query_id)
        except ClientError as exc:
            print(f"  get_query_execution FAILED: {exc}")
            return 1

        state = status_resp["QueryExecution"]["Status"]["State"]
        print(f"  [{waited:.0f}s] State: {state}")

        if state == "SUCCEEDED":
            break
        elif state in ("FAILED", "CANCELLED"):
            reason = status_resp["QueryExecution"]["Status"].get(
                "StateChangeReason", "Unknown"
            )
            print(f"  Query {state}: {reason}")
            return 1

        time.sleep(1.0)
        waited += 1.0

    if state != "SUCCEEDED":
        print(f"  Timed out after {max_wait}s (last state: {state})")
        return 1

    print()

    # 3. 取得結果
    print("[Step 3] get_query_results ...")
    try:
        result_resp = client.get_query_results(
            QueryExecutionId=query_id, MaxResults=10
        )
    except ClientError as exc:
        print(f"  FAILED: {exc}")
        return 1

    rows = result_resp.get("ResultSet", {}).get("Rows", [])
    print(f"  Rows returned: {len(rows)}")
    for i, row in enumerate(rows):
        values = [col.get("VarCharValue", "") for col in row.get("Data", [])]
        label = "Header" if i == 0 else f"Row {i}"
        print(f"  {label}: {values}")

    print()
    print("SUCCESS - Athena query executed and result retrieved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
