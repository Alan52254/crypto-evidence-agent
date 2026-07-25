from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from hoyabit_agent.domain import Asset
from hoyabit_agent.ingest.dataset import load_asset_windows


def _write_prices(path: Path, rows: int = 31) -> None:
    lines = ["date,open,high,low,close,volume"]
    for day in range(1, rows + 1):
        value = Decimal(day)
        lines.append(f"2026-01-{day:02d},{value},{value},{value},{value},{value * 10}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_dataset_builds_one_traceable_window_per_day(tmp_path: Path) -> None:
    csv_path = tmp_path / "BTC_daily_ohlcv.csv"
    _write_prices(csv_path)

    documents = load_asset_windows(csv_path, Asset.BTC)

    assert len(documents) == 31
    first = documents[0]
    assert first.as_of_date == date(2026, 1, 1)
    assert first.window_complete is False
    assert first.indicators.sma_30 is None
    assert first.source_row_start == 2
    assert first.source_row_end == 2

    last = documents[-1]
    assert len(last.ohlcv) == 30
    assert last.window_complete is True
    assert last.source_row_start == 3
    assert last.source_row_end == 32
    assert last.indicators.daily_return == pytest.approx(1 / 30)
    assert last.indicators.return_30d == pytest.approx(30.0)
    assert last.indicators.sma_30 == pytest.approx(16.5)
    assert last.indicators.volume_sma_30_ratio == pytest.approx(31 / 16.5)


def test_dataset_rejects_non_finite_market_values(tmp_path: Path) -> None:
    csv_path = tmp_path / "BTC_daily_ohlcv.csv"
    csv_path.write_text(
        "date,open,high,low,close,volume\n2026-01-01,1,2,0.5,NaN,10\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="finite"):
        load_asset_windows(csv_path, Asset.BTC)
