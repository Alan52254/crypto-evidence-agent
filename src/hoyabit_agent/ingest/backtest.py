"""歷史策略回測引擎 — 用 Athena 撈資料計算策略績效。

用法：
    python -m hoyabit_agent.ingest.backtest --asset BTC --start 2024-01-01 --end 2026-06-30 --strategy sma_cross
    python -m hoyabit_agent.ingest.backtest --asset ETH --start 2025-01-01 --strategy rsi_reversal

支援策略：
    sma_cross   — 短期均線向上穿越長期均線買入，反向賣出
    rsi_reversal — RSI 超賣買入、超買賣出
    momentum    — N 日動量正向買入，負向賣出
    buy_and_hold — 買入持有基準線

輸出：勝率、總報酬、最大回撤、Sharpe Ratio、交易次數。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import boto3

from hoyabit_agent.domain import Asset

logger = logging.getLogger(__name__)

# ─── 設定 ───
ATHENA_DATABASE = "hoyabit_market"
ATHENA_WORKGROUP = "primary"
ATHENA_OUTPUT = "s3://hoyabit-data-warehouse-433348878087/athena-results/"
AWS_REGION = "us-east-1"

SUPPORTED_STRATEGIES = ("sma_cross", "rsi_reversal", "momentum", "buy_and_hold")


# ─── 資料結構 ───

@dataclass(frozen=True)
class Trade:
    """一筆交易記錄。"""
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    return_pct: float
    holding_days: int


@dataclass
class BacktestResult:
    """回測結果摘要。"""
    asset: str
    strategy: str
    start_date: str
    end_date: str
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate_pct: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_trade_return_pct: float = 0.0
    best_trade_pct: float = 0.0
    worst_trade_pct: float = 0.0
    trades: list[Trade] = field(default_factory=list)

    def summary(self) -> str:
        """人可讀的回測摘要。"""
        lines = [
            f"{'='*60}",
            f"  回測結果: {self.asset} | 策略: {self.strategy}",
            f"  期間: {self.start_date} ~ {self.end_date}",
            f"{'='*60}",
            f"  總報酬率: {self.total_return_pct:+.2f}%",
            f"  年化報酬率: {self.annualized_return_pct:+.2f}%",
            f"  最大回撤: {self.max_drawdown_pct:.2f}%",
            f"  Sharpe Ratio: {self.sharpe_ratio:.3f}",
            f"  {'─'*56}",
            f"  總交易次數: {self.total_trades}",
            f"  勝率: {self.win_rate_pct:.1f}%",
            f"  獲利交易: {self.winning_trades} | 虧損交易: {self.losing_trades}",
            f"  平均每筆報酬: {self.avg_trade_return_pct:+.2f}%",
            f"  最佳單筆: {self.best_trade_pct:+.2f}%",
            f"  最差單筆: {self.worst_trade_pct:+.2f}%",
            f"{'='*60}",
        ]
        return "\n".join(lines)


# ─── Athena 查詢 ───

def _query_athena(sql: str) -> list[dict[str, Any]]:
    """同步執行 Athena SQL 並回傳結果。"""
    region = os.environ.get("BEDROCK_REGION", AWS_REGION)
    athena = boto3.client("athena", region_name=region)

    response = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": ATHENA_DATABASE},
        WorkGroup=ATHENA_WORKGROUP,
        ResultConfiguration={"OutputLocation": ATHENA_OUTPUT},
    )

    query_id = response["QueryExecutionId"]

    # 等待完成
    for _ in range(60):
        status = athena.get_query_execution(QueryExecutionId=query_id)
        state = status["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            break
        elif state in ("FAILED", "CANCELLED"):
            reason = status["QueryExecution"]["Status"].get("StateChangeReason", "")
            logger.error("Athena query %s: %s", state, reason)
            return []
        time.sleep(1.0)
    else:
        logger.error("Athena query timed out")
        return []

    # 取結果（分頁，每次最多 1000）
    all_rows: list[dict[str, Any]] = []
    headers: list[str] = []
    next_token: str | None = None

    while True:
        try:
            kwargs: dict[str, Any] = {
                "QueryExecutionId": query_id,
                "MaxResults": 1000,
            }
            if next_token:
                kwargs["NextToken"] = next_token
            page = athena.get_query_results(**kwargs)
        except Exception as exc:
            logger.warning("Athena get_query_results failed: %s", exc)
            return []

        rows = page.get("ResultSet", {}).get("Rows", [])

        if not headers and rows:
            headers = [col.get("VarCharValue", "") for col in rows[0]["Data"]]
            data_rows = rows[1:]
        else:
            data_rows = rows

        for row in data_rows:
            values = [col.get("VarCharValue", "") for col in row["Data"]]
            all_rows.append(dict(zip(headers, values)))

        next_token = page.get("NextToken")
        if not next_token:
            break

    return all_rows


def _fetch_ohlcv(asset: str, start_date: str, end_date: str) -> list[dict[str, float]]:
    """從 Athena 取得日K線資料，回傳按時間排序的 list。"""
    sql = (
        f"SELECT open_time, open, high, low, close, volume "
        f"FROM ohlcv_daily "
        f"WHERE asset = '{asset}' "
        f"  AND open_time >= TIMESTAMP '{start_date} 00:00:00' "
        f"  AND open_time <= TIMESTAMP '{end_date} 23:59:59' "
        f"ORDER BY open_time ASC"
    )
    rows = _query_athena(sql)

    candles = []
    for row in rows:
        try:
            candles.append({
                "date": row.get("open_time", "")[:10],
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": float(row.get("close", 0)),
                "volume": float(row.get("volume", 0)),
            })
        except (ValueError, TypeError):
            continue

    return candles


# ─── 策略實作 ───

def _sma(prices: list[float], period: int) -> list[float | None]:
    """計算簡單移動平均。"""
    result: list[float | None] = []
    for i in range(len(prices)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(prices[i - period + 1 : i + 1]) / period)
    return result


def _rsi(prices: list[float], period: int = 14) -> list[float | None]:
    """計算 Wilder RSI。"""
    result: list[float | None] = [None] * period
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]

    gains = [max(d, 0) for d in deltas[:period]]
    losses = [abs(min(d, 0)) for d in deltas[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        result.append(100.0)
    else:
        result.append(100 - 100 / (1 + avg_gain / avg_loss))

    for i in range(period, len(deltas)):
        gain = max(deltas[i], 0)
        loss = abs(min(deltas[i], 0))
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            result.append(100.0)
        else:
            result.append(100 - 100 / (1 + avg_gain / avg_loss))

    return result


def _strategy_sma_cross(
    candles: list[dict[str, float]], short_period: int = 10, long_period: int = 30
) -> list[Trade]:
    """SMA 交叉策略。"""
    if len(candles) < long_period + 1:
        return []

    closes = [c["close"] for c in candles]
    sma_short = _sma(closes, short_period)
    sma_long = _sma(closes, long_period)

    trades: list[Trade] = []
    position_entry: dict[str, Any] | None = None

    for i in range(long_period, len(candles)):
        s = sma_short[i]
        l = sma_long[i]
        s_prev = sma_short[i - 1]
        l_prev = sma_long[i - 1]

        if s is None or l is None or s_prev is None or l_prev is None:
            continue

        # 黃金交叉 — 買入
        if s_prev <= l_prev and s > l and position_entry is None:
            position_entry = {"date": candles[i]["date"], "price": closes[i], "index": i}

        # 死亡交叉 — 賣出
        elif s_prev >= l_prev and s < l and position_entry is not None:
            ret = (closes[i] - position_entry["price"]) / position_entry["price"] * 100
            trades.append(Trade(
                entry_date=str(position_entry["date"]),
                entry_price=position_entry["price"],
                exit_date=str(candles[i]["date"]),
                exit_price=closes[i],
                return_pct=ret,
                holding_days=i - position_entry["index"],
            ))
            position_entry = None

    # 未平倉：以最後一根收盤平倉
    if position_entry is not None:
        last = candles[-1]
        ret = (last["close"] - position_entry["price"]) / position_entry["price"] * 100
        trades.append(Trade(
            entry_date=str(position_entry["date"]),
            entry_price=position_entry["price"],
            exit_date=str(last["date"]),
            exit_price=last["close"],
            return_pct=ret,
            holding_days=len(candles) - 1 - position_entry["index"],
        ))

    return trades


def _strategy_rsi_reversal(
    candles: list[dict[str, float]], oversold: float = 30, overbought: float = 70
) -> list[Trade]:
    """RSI 反轉策略：超賣買入，超買賣出。"""
    if len(candles) < 16:
        return []

    closes = [c["close"] for c in candles]
    rsi_values = _rsi(closes)

    trades: list[Trade] = []
    position_entry: dict[str, Any] | None = None

    for i in range(15, len(candles)):
        r = rsi_values[i]
        if r is None:
            continue

        if r < oversold and position_entry is None:
            position_entry = {"date": candles[i]["date"], "price": closes[i], "index": i}

        elif r > overbought and position_entry is not None:
            ret = (closes[i] - position_entry["price"]) / position_entry["price"] * 100
            trades.append(Trade(
                entry_date=str(position_entry["date"]),
                entry_price=position_entry["price"],
                exit_date=str(candles[i]["date"]),
                exit_price=closes[i],
                return_pct=ret,
                holding_days=i - position_entry["index"],
            ))
            position_entry = None

    if position_entry is not None:
        last = candles[-1]
        ret = (last["close"] - position_entry["price"]) / position_entry["price"] * 100
        trades.append(Trade(
            entry_date=str(position_entry["date"]),
            entry_price=position_entry["price"],
            exit_date=str(last["date"]),
            exit_price=last["close"],
            return_pct=ret,
            holding_days=len(candles) - 1 - position_entry["index"],
        ))

    return trades


def _strategy_momentum(
    candles: list[dict[str, float]], lookback: int = 20
) -> list[Trade]:
    """動量策略：N 日報酬正向進場，負向出場。"""
    if len(candles) < lookback + 1:
        return []

    closes = [c["close"] for c in candles]
    trades: list[Trade] = []
    position_entry: dict[str, Any] | None = None

    for i in range(lookback, len(candles)):
        momentum = (closes[i] - closes[i - lookback]) / closes[i - lookback]

        if momentum > 0 and position_entry is None:
            position_entry = {"date": candles[i]["date"], "price": closes[i], "index": i}

        elif momentum < 0 and position_entry is not None:
            ret = (closes[i] - position_entry["price"]) / position_entry["price"] * 100
            trades.append(Trade(
                entry_date=str(position_entry["date"]),
                entry_price=position_entry["price"],
                exit_date=str(candles[i]["date"]),
                exit_price=closes[i],
                return_pct=ret,
                holding_days=i - position_entry["index"],
            ))
            position_entry = None

    if position_entry is not None:
        last = candles[-1]
        ret = (last["close"] - position_entry["price"]) / position_entry["price"] * 100
        trades.append(Trade(
            entry_date=str(position_entry["date"]),
            entry_price=position_entry["price"],
            exit_date=str(last["date"]),
            exit_price=last["close"],
            return_pct=ret,
            holding_days=len(candles) - 1 - position_entry["index"],
        ))

    return trades


def _strategy_buy_and_hold(candles: list[dict[str, float]]) -> list[Trade]:
    """買入持有基準策略。"""
    if len(candles) < 2:
        return []

    first = candles[0]
    last = candles[-1]
    ret = (last["close"] - first["close"]) / first["close"] * 100

    return [Trade(
        entry_date=str(first["date"]),
        entry_price=first["close"],
        exit_date=str(last["date"]),
        exit_price=last["close"],
        return_pct=ret,
        holding_days=len(candles) - 1,
    )]


# ─── 績效計算 ───

def _compute_metrics(
    trades: list[Trade],
    candles: list[dict[str, float]],
    asset: str,
    strategy: str,
    start_date: str,
    end_date: str,
) -> BacktestResult:
    """從交易清單計算績效指標。"""
    result = BacktestResult(
        asset=asset,
        strategy=strategy,
        start_date=start_date,
        end_date=end_date,
        trades=trades,
    )

    if not trades:
        return result

    result.total_trades = len(trades)
    returns = [t.return_pct for t in trades]
    result.winning_trades = sum(1 for r in returns if r > 0)
    result.losing_trades = sum(1 for r in returns if r <= 0)
    result.win_rate_pct = result.winning_trades / result.total_trades * 100
    result.avg_trade_return_pct = sum(returns) / len(returns)
    result.best_trade_pct = max(returns)
    result.worst_trade_pct = min(returns)

    # 總報酬（複利）
    cumulative = 1.0
    for r in returns:
        cumulative *= (1 + r / 100)
    result.total_return_pct = (cumulative - 1) * 100

    # 年化報酬
    if candles:
        total_days = len(candles)
        if total_days > 0:
            result.annualized_return_pct = (
                (cumulative ** (365 / total_days)) - 1
            ) * 100

    # 最大回撤（用日收盤價序列）
    if candles:
        closes = [c["close"] for c in candles]
        peak = closes[0]
        max_dd = 0.0
        for price in closes:
            if price > peak:
                peak = price
            dd = (peak - price) / peak * 100
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown_pct = max_dd

    # Sharpe Ratio（假設無風險利率 4%，用日報酬）
    if len(candles) > 1:
        closes = [c["close"] for c in candles]
        daily_returns = [
            (closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(1, len(closes))
        ]
        if daily_returns:
            avg_daily = sum(daily_returns) / len(daily_returns)
            std_daily = math.sqrt(
                sum((r - avg_daily) ** 2 for r in daily_returns) / len(daily_returns)
            )
            if std_daily > 0:
                risk_free_daily = 0.04 / 365
                result.sharpe_ratio = (avg_daily - risk_free_daily) / std_daily * math.sqrt(365)

    return result


# ─── 主函式 ───

def run_backtest(
    asset: str,
    strategy: str,
    start_date: str,
    end_date: str,
) -> BacktestResult:
    """執行回測：從 Athena 撈資料 → 跑策略 → 計算績效。"""
    candles = _fetch_ohlcv(asset, start_date, end_date)

    if not candles:
        logger.warning("No OHLCV data found for %s (%s ~ %s)", asset, start_date, end_date)
        return BacktestResult(
            asset=asset, strategy=strategy, start_date=start_date, end_date=end_date
        )

    # 選擇策略
    if strategy == "sma_cross":
        trades = _strategy_sma_cross(candles)
    elif strategy == "rsi_reversal":
        trades = _strategy_rsi_reversal(candles)
    elif strategy == "momentum":
        trades = _strategy_momentum(candles)
    elif strategy == "buy_and_hold":
        trades = _strategy_buy_and_hold(candles)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")

    return _compute_metrics(trades, candles, asset, strategy, start_date, end_date)


# ─── CLI ───

def main() -> int:
    """CLI: python -m hoyabit_agent.ingest.backtest"""
    from hoyabit_agent.config import load_dotenv

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Backtest strategies on Athena historical data")
    parser.add_argument("--asset", type=str, required=True, help="Asset (BTC, ETH, SOL, BNB, XRP)")
    parser.add_argument("--strategy", type=str, required=True, choices=SUPPORTED_STRATEGIES)
    parser.add_argument("--start", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--compare", action="store_true", help="Compare all strategies")

    args = parser.parse_args()
    load_dotenv()

    end_date = args.end or str(date.today())

    if args.compare:
        print(f"\n比較所有策略: {args.asset} ({args.start} ~ {end_date})\n")
        for strat in SUPPORTED_STRATEGIES:
            result = run_backtest(args.asset, strat, args.start, end_date)
            print(result.summary())
            print()
    else:
        result = run_backtest(args.asset, args.strategy, args.start, end_date)
        print(result.summary())

        if result.trades:
            print(f"\n  最近 5 筆交易:")
            for trade in result.trades[-5:]:
                print(
                    f"    {trade.entry_date} → {trade.exit_date} | "
                    f"${trade.entry_price:,.0f} → ${trade.exit_price:,.0f} | "
                    f"{trade.return_pct:+.2f}% ({trade.holding_days}d)"
                )

    return 0


if __name__ == "__main__":
    sys.exit(main())
