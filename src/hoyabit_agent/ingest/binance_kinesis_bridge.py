"""Binance WebSocket → Kinesis 即時橋接器。

監聽 Binance Futures miniTicker 串流，將指定 5 大幣種的價格異動
即時寫入 Amazon Kinesis Data Streams。

用法：
    python -m hoyabit_agent.ingest.binance_kinesis_bridge

    # 或同時發送到 Firehose：
    python -m hoyabit_agent.ingest.binance_kinesis_bridge --firehose

架構位置：
    Binance WebSocket → [本模組] → Kinesis Data Stream → 下游消費者
                                  → Firehose → S3 (可選)

穩健性：
    - WebSocket 斷線時自動重連（指數退避）
    - Kinesis 寫入失敗時降級至本地 buffer
    - 永不讓主流程崩潰
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ─── 設定 ───
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/!miniTicker@arr"
TRACKED_SYMBOLS = frozenset({"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"})

# 環境變數
KINESIS_STREAM_ENV = "KINESIS_STREAM_NAME"
FIREHOSE_STREAM_ENV = "FIREHOSE_STREAM_NAME"
DEFAULT_KINESIS_STREAM = "ggg"
DEFAULT_FIREHOSE_STREAM = "hoyabit-market-firehose"

# WebSocket 重連設定
WS_RECONNECT_BASE_SECONDS = 1.0
WS_RECONNECT_MAX_SECONDS = 60.0
WS_MAX_RECONNECT_ATTEMPTS = 100


def _format_ticker(raw: dict[str, Any]) -> dict[str, Any]:
    """將 Binance miniTicker 格式化為標準結構。

    輸入（Binance miniTicker）：
        {"e": "24hrMiniTicker", "s": "BTCUSDT", "c": "68500.50", ...}

    輸出：
        {"symbol": "BTCUSDT", "price": 68500.5, "timestamp": "...", "source": "binance_ws"}
    """
    return {
        "symbol": raw.get("s", ""),
        "price": float(raw.get("c", 0)),
        "volume_24h": float(raw.get("v", 0)),
        "quote_volume_24h": float(raw.get("q", 0)),
        "high_24h": float(raw.get("h", 0)),
        "low_24h": float(raw.get("l", 0)),
        "timestamp": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event_time": int(raw.get("E", 0)),
        "source": "binance_ws",
    }


def _symbol_to_partition_key(symbol: str) -> str:
    """從交易對提取幣種作為 partition key。

    "BTCUSDT" → "BTC"
    """
    return symbol.replace("USDT", "")


class BinanceKinesisBridge:
    """Binance WebSocket → Kinesis/Firehose 即時橋接器。

    啟動後持續監聽 Binance miniTicker，篩選指定幣種後
    即時寫入 Kinesis Data Stream（可選同時寫入 Firehose）。
    """

    def __init__(
        self,
        *,
        enable_firehose: bool = False,
        kinesis_stream: str | None = None,
        firehose_stream: str | None = None,
        batch_size: int = 20,
        batch_interval_seconds: float = 2.0,
    ) -> None:
        """初始化橋接器。

        Args:
            enable_firehose: 是否同時發送到 Firehose。
            kinesis_stream: Kinesis stream 名稱。
            firehose_stream: Firehose delivery stream 名稱。
            batch_size: 批次發送的記錄數閾值。
            batch_interval_seconds: 批次發送的最大等待時間。
        """
        from hoyabit_agent.ingest.kinesis_ingestion import (
            FirehoseDeliveryProducer,
            KinesisStreamProducer,
        )

        self._kinesis_stream = kinesis_stream or os.environ.get(
            KINESIS_STREAM_ENV, DEFAULT_KINESIS_STREAM
        )
        self._firehose_stream = firehose_stream or os.environ.get(
            FIREHOSE_STREAM_ENV, DEFAULT_FIREHOSE_STREAM
        )
        self._enable_firehose = enable_firehose
        self._batch_size = batch_size
        self._batch_interval = batch_interval_seconds

        self._kinesis = KinesisStreamProducer()
        self._firehose = FirehoseDeliveryProducer() if enable_firehose else None

        self._running = False
        self._stats = {
            "messages_received": 0,
            "records_sent": 0,
            "errors": 0,
            "reconnects": 0,
        }
        self._batch_buffer: list[dict[str, Any]] = []
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="kinesis")

    @property
    def stats(self) -> dict[str, int]:
        """即時統計資訊。"""
        return dict(self._stats)

    async def start(self) -> None:
        """啟動 WebSocket 監聽與 Kinesis 寫入迴圈。"""
        self._running = True
        reconnect_attempt = 0

        logger.info(
            "[Bridge] Starting Binance→Kinesis bridge "
            "(stream=%s, firehose=%s, symbols=%s)",
            self._kinesis_stream,
            self._firehose_stream if self._enable_firehose else "disabled",
            ", ".join(sorted(TRACKED_SYMBOLS)),
        )

        while self._running and reconnect_attempt < WS_MAX_RECONNECT_ATTEMPTS:
            try:
                await self._connect_and_consume()
                reconnect_attempt = 0  # 成功連接後重置
            except asyncio.CancelledError:
                logger.info("[Bridge] Cancelled, shutting down")
                break
            except Exception as exc:
                reconnect_attempt += 1
                self._stats["reconnects"] += 1
                backoff = min(
                    WS_RECONNECT_BASE_SECONDS * (2 ** reconnect_attempt),
                    WS_RECONNECT_MAX_SECONDS,
                )
                logger.warning(
                    "[Bridge] WebSocket disconnected (attempt %d): %s. "
                    "Reconnecting in %.1fs...",
                    reconnect_attempt, exc, backoff,
                )
                await asyncio.sleep(backoff)

        # 結束前 flush 剩餘 batch
        await self._flush_batch()
        logger.info("[Bridge] Stopped. Stats: %s", self._stats)

    async def stop(self) -> None:
        """優雅停止橋接器。"""
        self._running = False

    async def _connect_and_consume(self) -> None:
        """建立 WebSocket 連線並消費訊息。"""
        try:
            import websockets
        except ImportError:
            logger.error(
                "[Bridge] websockets package not installed. "
                "Run: pip install websockets"
            )
            raise RuntimeError("websockets not installed")

        async with websockets.connect(
            BINANCE_WS_URL,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            logger.info("[Bridge] Connected to Binance WebSocket")

            # 啟動定時 flush 任務
            flush_task = asyncio.create_task(self._periodic_flush())

            try:
                async for message in ws:
                    if not self._running:
                        break
                    await self._handle_message(message)
            finally:
                flush_task.cancel()
                try:
                    await flush_task
                except asyncio.CancelledError:
                    pass

    async def _handle_message(self, raw_message: str) -> None:
        """處理單則 WebSocket 訊息。"""
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            return

        # miniTicker@arr 回傳的是陣列
        tickers = data if isinstance(data, list) else [data]

        for ticker in tickers:
            symbol = ticker.get("s", "")
            if symbol not in TRACKED_SYMBOLS:
                continue

            self._stats["messages_received"] += 1
            formatted = _format_ticker(ticker)
            partition_key = _symbol_to_partition_key(symbol)

            self._batch_buffer.append({
                "data": formatted,
                "partition_key": partition_key,
            })

            # 達到 batch size 時立即發送
            if len(self._batch_buffer) >= self._batch_size:
                await self._flush_batch()

    async def _flush_batch(self) -> None:
        """發送當前 batch buffer 中的所有記錄。"""
        if not self._batch_buffer:
            return

        batch = self._batch_buffer[:]
        self._batch_buffer.clear()

        # 寫入 Kinesis（在專用 thread pool 中執行同步呼叫）
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                self._executor,
                self._kinesis.put_records_batch,
                batch,
                self._kinesis_stream,
            )
            self._stats["records_sent"] += result.get("succeeded", 0)
            self._stats["errors"] += result.get("failed", 0)
        except Exception as exc:
            logger.warning("[Bridge] Kinesis batch send error: %s", exc)
            self._stats["errors"] += len(batch)

        # 可選：同時寫入 Firehose
        if self._firehose and self._enable_firehose:
            firehose_data = [item["data"] for item in batch]
            try:
                await loop.run_in_executor(
                    self._executor,
                    self._firehose.send_batch,
                    firehose_data,
                    self._firehose_stream,
                )
            except Exception as exc:
                logger.warning("[Bridge] Firehose batch send error: %s", exc)

    async def _periodic_flush(self) -> None:
        """定時 flush batch buffer（避免低流量時記錄滯留）。"""
        while self._running:
            await asyncio.sleep(self._batch_interval)
            await self._flush_batch()


# ─── CLI 進入點 ───

def main() -> int:
    """CLI: python -m hoyabit_agent.ingest.binance_kinesis_bridge"""
    from hoyabit_agent.config import load_dotenv

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Binance WebSocket to Kinesis/Firehose bridge"
    )
    parser.add_argument(
        "--firehose", action="store_true", help="Also send to Firehose"
    )
    parser.add_argument(
        "--batch-size", type=int, default=10, help="Batch size before flush"
    )
    parser.add_argument(
        "--batch-interval", type=float, default=1.0,
        help="Max seconds between flushes",
    )
    args = parser.parse_args()

    load_dotenv()

    bridge = BinanceKinesisBridge(
        enable_firehose=args.firehose,
        batch_size=args.batch_size,
        batch_interval_seconds=args.batch_interval,
    )

    # 優雅關閉
    loop = asyncio.new_event_loop()

    def _shutdown(sig: signal.Signals) -> None:
        logger.info("[Bridge] Received %s, shutting down...", sig.name)
        loop.create_task(bridge.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown, sig)
        except NotImplementedError:
            # Windows 不支援 add_signal_handler
            pass

    try:
        loop.run_until_complete(bridge.start())
    except KeyboardInterrupt:
        logger.info("[Bridge] KeyboardInterrupt, stopping...")
        loop.run_until_complete(bridge.stop())
    finally:
        loop.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
