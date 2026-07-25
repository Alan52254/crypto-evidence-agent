"""Deterministic evidence-quality metadata for reports and submission artifacts."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
import math
from datetime import datetime, timezone
from urllib.parse import urlparse

from hoyabit_agent.domain import Evidence, Report


class SourceTier(Enum):
    PRIMARY = "primary_or_official"
    MARKET_DATA = "market_data_api"
    MEDIA = "reputable_media"
    SOCIAL = "public_social"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EvidenceQuality:
    source_count: int
    independent_domains: int
    tiers: dict[str, int]
    limitations: tuple[str, ...]


_PRIMARY = ("sec.gov", "federalreserve.gov", "binance.com", "ethereum.org", "solana.com")
_MARKET = ("api.binance.com", "fapi.binance.com", "coingecko.com")
_MEDIA = ("coindesk.com", "cointelegraph.com", "reuters.com", "bloomberg.com")
_SOCIAL = ("reddit.com", "x.com", "twitter.com")


def classify_source(url: str) -> SourceTier:
    host = (urlparse(url).hostname or "").lower()
    if any(host == item or host.endswith(f".{item}") for item in _MARKET):
        return SourceTier.MARKET_DATA
    if any(host == item or host.endswith(f".{item}") for item in _PRIMARY):
        return SourceTier.PRIMARY
    if any(host == item or host.endswith(f".{item}") for item in _MEDIA):
        return SourceTier.MEDIA
    if any(host == item or host.endswith(f".{item}") for item in _SOCIAL):
        return SourceTier.SOCIAL
    return SourceTier.UNKNOWN


def assess_evidence_quality(evidence: tuple[Evidence, ...]) -> EvidenceQuality:
    excerpts = [excerpt for item in evidence for excerpt in item.excerpts]
    domains = {
        host
        for excerpt in excerpts
        if (host := (urlparse(excerpt.url).hostname or "").lower())
    }
    counts = Counter(classify_source(excerpt.url).value for excerpt in excerpts)
    limitations: list[str] = []
    if len(domains) < 2:
        limitations.append("關鍵證據缺乏兩個以上獨立網域交叉驗證")
    if not counts[SourceTier.PRIMARY.value] and not counts[SourceTier.MARKET_DATA.value]:
        limitations.append("缺少官方來源或可重現的市場資料 API")
    if counts[SourceTier.SOCIAL.value] and len(excerpts) == counts[SourceTier.SOCIAL.value]:
        limitations.append("證據僅來自公開社群，可信度有限")
    return EvidenceQuality(
        source_count=len(excerpts),
        independent_domains=len(domains),
        tiers=dict(sorted(counts.items())),
        limitations=tuple(limitations),
    )


def calculate_time_decay_weight(fetched_at: datetime, half_life_hours: float = 2.0) -> float:
    """Calculate exponential time decay weight based on evidence freshness."""
    import math
    from datetime import timezone

    now = datetime.now(timezone.utc)
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - fetched_at).total_seconds() / 3600.0)
    decay_rate = math.log(2) / half_life_hours
    return math.exp(-decay_rate * age_hours)


def calibrate_report_tone(report: Report, market_volatility: float = 0.0) -> str:
    """Dynamically calibrate report tone in extreme market volatility."""
    md = report.to_markdown()
    if market_volatility > 0.15 or report.stance.value == "bearish":
        panic_notice = (
            "\n\n> 🛡️ **HOYA BIT 專屬安納與風控提示**：當前市場波動劇烈（或處於極端行情）。"
            "\n> HOYA BIT 平台用戶資金享 100% 法幣獨立信託隔離，資產全額受銀行託管保障。"
            "\n> 面對單日大幅波動時，建議秉持理性投資思維，適度採用定期定額 (DCA) 策略進行防禦型佈局。"
        )
        md += panic_notice
    return md


__all__ = [
    "EvidenceQuality",
    "SourceTier",
    "assess_evidence_quality",
    "calculate_time_decay_weight",
    "calibrate_report_tone",
    "classify_source",
]
