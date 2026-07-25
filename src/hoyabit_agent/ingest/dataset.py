"""把競賽 OHLCV CSV 轉成可重現的 30 日市場文件。"""

from __future__ import annotations

import csv
import math
import statistics
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from hoyabit_agent.domain import Asset
from hoyabit_agent.ingest.documents import MarketDocument, MarketIndicators, OhlcvBar

DATASET_ENV = "HOYABIT_DATASET_DIR"
DATASET_END_DATE = date(2026, 5, 31)
WINDOW_DAYS = 30
EXPECTED_COLUMNS = ("date", "open", "high", "low", "close", "volume")


def load_asset_windows(csv_path: Path, asset: Asset) -> tuple[MarketDocument, ...]:
    bars = _read_bars(csv_path)
    rsi_values = _wilder_rsi([bar.close for bar in bars], 14)
    documents: list[MarketDocument] = []
    for index, bar in enumerate(bars):
        start = max(0, index - WINDOW_DAYS + 1)
        window = tuple(bars[start : index + 1])
        documents.append(
            MarketDocument(
                asset=asset,
                as_of_date=bar.date,
                ohlcv=window,
                indicators=_indicators(bars, index, rsi_values[index]),
                window_complete=len(window) == WINDOW_DAYS,
                source_file=csv_path,
                source_row_start=start + 2,
                source_row_end=index + 2,
            )
        )
    return tuple(documents)


def load_dataset(dataset_dir: Path) -> tuple[MarketDocument, ...]:
    data_dir = dataset_dir / "data" if (dataset_dir / "data").is_dir() else dataset_dir
    documents: list[MarketDocument] = []
    for asset in Asset:
        path = data_dir / f"{asset.value}_daily_ohlcv.csv"
        if not path.is_file():
            raise FileNotFoundError(f"missing dataset file: {path}")
        documents.extend(load_asset_windows(path, asset))
    return tuple(documents)


def _read_bars(path: Path) -> list[OhlcvBar]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != EXPECTED_COLUMNS:
            raise ValueError(f"unexpected OHLCV columns in {path}")
        bars = [_parse_bar(row, path, line) for line, row in enumerate(reader, start=2)]
    if any(left.date >= right.date for left, right in zip(bars, bars[1:], strict=False)):
        raise ValueError(f"dates must be strictly increasing in {path}")
    return bars


def _parse_bar(row: dict[str, str], path: Path, line: int) -> OhlcvBar:
    try:
        values = [Decimal(row[name]) for name in EXPECTED_COLUMNS[1:]]
        parsed_date = date.fromisoformat(row["date"])
    except (InvalidOperation, ValueError, KeyError) as error:
        raise ValueError(f"invalid OHLCV row {path}:{line}") from error
    if any(not value.is_finite() for value in values):
        raise ValueError(f"OHLCV values must be finite at {path}:{line}")
    open_, high, low, close, volume = values
    if min(open_, high, low, close, volume) < 0:
        raise ValueError(f"OHLCV values must be non-negative at {path}:{line}")
    if high < max(open_, close, low) or low > min(open_, close, high):
        raise ValueError(f"invalid OHLCV range at {path}:{line}")
    return OhlcvBar(parsed_date, open_, high, low, close, volume)


def _ratio(current: Decimal, previous: Decimal) -> float | None:
    if previous == 0:
        return None
    result = float(current / previous - 1)
    return result if math.isfinite(result) else None


def _mean(values: Sequence[Decimal]) -> float | None:
    if not values:
        return None
    result = float(sum(values) / Decimal(len(values)))
    return result if math.isfinite(result) else None


def _indicators(bars: Sequence[OhlcvBar], index: int, rsi: float | None) -> MarketIndicators:
    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars]
    volatility: float | None = None
    if index >= 30:
        daily_returns = [
            _ratio(closes[position], closes[position - 1])
            for position in range(index - 29, index + 1)
        ]
        if all(value is not None for value in daily_returns):
            volatility = statistics.stdev(
                value for value in daily_returns if value is not None
            ) * math.sqrt(365)
    volume_mean = _mean(volumes[index - 29 : index + 1]) if index >= 29 else None
    volume_ratio = float(volumes[index]) / volume_mean if volume_mean not in (None, 0.0) else None
    return MarketIndicators(
        daily_return=_ratio(closes[index], closes[index - 1]) if index >= 1 else None,
        return_7d=_ratio(closes[index], closes[index - 7]) if index >= 7 else None,
        return_30d=_ratio(closes[index], closes[index - 30]) if index >= 30 else None,
        volatility_30d=volatility,
        sma_7=_mean(closes[index - 6 : index + 1]) if index >= 6 else None,
        sma_30=_mean(closes[index - 29 : index + 1]) if index >= 29 else None,
        rsi_14=rsi,
        volume_change_1d=_ratio(volumes[index], volumes[index - 1]) if index >= 1 else None,
        volume_sma_30_ratio=volume_ratio,
    )


def _wilder_rsi(closes: Sequence[Decimal], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return result
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    gains = [max(change, Decimal(0)) for change in changes]
    losses = [max(-change, Decimal(0)) for change in changes]
    average_gain = sum(gains[:period]) / Decimal(period)
    average_loss = sum(losses[:period]) / Decimal(period)
    result[period] = _rsi(average_gain, average_loss)
    for position in range(period, len(changes)):
        average_gain = (average_gain * Decimal(period - 1) + gains[position]) / Decimal(period)
        average_loss = (average_loss * Decimal(period - 1) + losses[position]) / Decimal(period)
        result[position + 1] = _rsi(average_gain, average_loss)
    return result


def _rsi(gain: Decimal, loss: Decimal) -> float:
    if gain == 0 and loss == 0:
        return 50.0
    if loss == 0:
        return 100.0
    return float(Decimal(100) - Decimal(100) / (Decimal(1) + gain / loss))


__all__ = ["DATASET_END_DATE", "DATASET_ENV", "WINDOW_DAYS", "load_asset_windows", "load_dataset"]
