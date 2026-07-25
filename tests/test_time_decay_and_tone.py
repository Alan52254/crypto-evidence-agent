"""Tests for time-decay weighting and volatility tone calibration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest
from hoyabit_agent.domain import Asset, Claim, Facet, Report, Stance, Confidence
from hoyabit_agent.evidence_quality import calculate_time_decay_weight, calibrate_report_tone


def test_time_decay_recent_vs_old() -> None:
    now = datetime.now(timezone.utc)
    recent = now - timedelta(minutes=5)
    old = now - timedelta(hours=2)
    
    recent_weight = calculate_time_decay_weight(recent, half_life_hours=2.0)
    old_weight = calculate_time_decay_weight(old, half_life_hours=2.0)
    
    assert recent_weight > 0.9
    assert abs(old_weight - 0.5) < 0.05
    assert recent_weight > old_weight


def test_calibrate_report_tone_in_panic() -> None:
    report = Report(
        asset=Asset.BTC,
        stance=Stance.BEARISH,
        confidence=Confidence(0.85, {Facet.TECHNICAL: Stance.BEARISH}),
        claims=(Claim("BTC 單日重挫 20%", (), Facet.TECHNICAL),),
        dropped_claims=(),
        evidence=(),
        question="BTC 大跌分析"
    )
    
    md = calibrate_report_tone(report, market_volatility=0.25)
    assert "HOYA BIT 專屬安納" in md or "法幣獨立信託" in md
    assert "定期定額 (DCA)" in md or "定期定額" in md
