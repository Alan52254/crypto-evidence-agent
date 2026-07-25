from hoyabit_agent.domain import Facet
from hoyabit_agent.evidence_quality import SourceTier, assess_evidence_quality, classify_source
from hoyabit_agent.testing import evidence


def test_classifies_known_source_types() -> None:
    assert classify_source("https://api.binance.com/api/v3/klines") is SourceTier.MARKET_DATA
    assert classify_source("https://www.sec.gov/newsroom") is SourceTier.PRIMARY
    assert classify_source("https://www.reuters.com/markets/") is SourceTier.MEDIA
    assert classify_source("https://www.reddit.com/r/bitcoin/") is SourceTier.SOCIAL


def test_quality_flags_single_source_domain() -> None:
    quality = assess_evidence_quality((evidence("E-1", Facet.TECHNICAL, 0.2),))
    assert quality.independent_domains == 1
    assert quality.limitations
