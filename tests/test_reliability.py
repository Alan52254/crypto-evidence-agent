"""來源可信度分級 —— 確定性映射，無需 mock。

這一層的存在理由是堵住「多加一個低品質來源就能抬高信心度」的漏洞，
所以測試的重點也在那裡。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hoyabit_agent.domain import Evidence, Facet, SourceExcerpt
from hoyabit_agent.reliability import (
    ReliabilityTier,
    evidence_tier,
    tier_breakdown,
    tier_for_source,
    weighted_source_count,
)


def evidence_from(
    identifier: str,
    *sources: str,
    facet: Facet = Facet.TECHNICAL,
    stance_hint: float = 0.0,
    event_key: str | None = None,
) -> Evidence:
    """建構一項證據，並明確指定它的來源識別碼。"""
    return Evidence(
        id=identifier,
        facet=facet,
        summary=f"{identifier} summary",
        stance_hint=stance_hint,
        excerpts=tuple(
            SourceExcerpt(
                source_id=source,
                url=f"https://example.test/{source}",
                retrieved_at=datetime(2026, 7, 23, tzinfo=UTC),
                locator="para-1",
                text="片段",
            )
            for source in sources
        ),
        event_key=event_key,
    )


@pytest.mark.parametrize("source", ["binance:spot", "dataset:BTC", "official:ripple"])
def test_first_hand_and_recomputable_sources_are_high_tier(source: str) -> None:
    """交易所端點與官方公告可以被重算或直接查核，媒體轉述不行。"""
    assert tier_for_source(source) is ReliabilityTier.HIGH


@pytest.mark.parametrize("source", ["coindesk:abc", "cointelegraph:x", "blocktempo:y"])
def test_mainstream_outlets_are_medium_tier(source: str) -> None:
    assert tier_for_source(source) is ReliabilityTier.MEDIUM


def test_an_unknown_source_is_treated_as_low_tier() -> None:
    """預設保守 —— 提升一個來源的待遇必須是明確的決定，不能靠預設繼承。"""
    assert tier_for_source("some-random-aggregator:1") is ReliabilityTier.LOW


def test_evidence_takes_the_highest_tier_among_its_excerpts() -> None:
    """一項證據若同時有交易所數值與媒體轉述，可驗證性由前者決定。"""
    item = evidence_from("E1", "coindesk:a", "binance:spot")
    assert evidence_tier(item) is ReliabilityTier.HIGH


def test_three_social_posts_are_worth_less_than_one_exchange_endpoint() -> None:
    """這正是「獨立來源數」這個門檻該有的行為。"""
    social = (
        evidence_from("S1", "reddit:1"),
        evidence_from("S2", "reddit:2"),
        evidence_from("S3", "reddit:3"),
    )
    exchange = (evidence_from("E1", "binance:spot"),)
    assert weighted_source_count(social) < weighted_source_count(exchange)


def test_the_same_source_appearing_twice_counts_once() -> None:
    """一篇文章同時產出基本面與情緒面證據，仍然只是一個來源。"""
    once = (evidence_from("E1", "coindesk:article-1"),)
    twice = (
        evidence_from("E1", "coindesk:article-1", facet=Facet.FUNDAMENTAL),
        evidence_from("E2", "coindesk:article-1", facet=Facet.SENTIMENT),
    )
    assert weighted_source_count(twice) == pytest.approx(weighted_source_count(once))


def test_a_transmitted_story_does_not_add_an_independent_source() -> None:
    """歸併後的證據保留每一家轉載的片段（溯源不損失），但只算一個來源。

    少了這條，多找一家轉載媒體就能無成本地推高獨立來源數 ——
    ADR 0002 的漏洞從另一道門回來。
    """
    single_outlet = (evidence_from("E1", "coindesk:a", event_key="etf-approved"),)
    two_outlets = (
        evidence_from("E1", "coindesk:a", "cointelegraph:b", event_key="etf-approved"),
    )
    assert weighted_source_count(two_outlets) == pytest.approx(
        weighted_source_count(single_outlet)
    )


def test_genuinely_independent_sources_do_add_up() -> None:
    """歸併規則不能反過來把真正獨立的來源也吃掉。"""
    one = (evidence_from("E1", "binance:spot"),)
    two = (
        evidence_from("E1", "binance:spot"),
        evidence_from("E2", "coindesk:a"),
    )
    assert weighted_source_count(two) > weighted_source_count(one)


def test_tier_breakdown_reports_every_level_even_when_empty() -> None:
    """報告的「來源品質」段落要看得出哪一級是零，而不是那一列消失。"""
    breakdown = tier_breakdown((evidence_from("E1", "binance:spot"),))
    assert set(breakdown) == {tier.value for tier in ReliabilityTier}
    assert breakdown["high"] == 1
    assert breakdown["low"] == 0


def test_evidence_without_excerpts_is_low_tier() -> None:
    """沒有來源片段就沒有可驗證性 —— 不該享有任何可信度待遇。"""
    bare = Evidence(
        id="E1", facet=Facet.TECHNICAL, summary="s", stance_hint=0.0, excerpts=()
    )
    assert evidence_tier(bare) is ReliabilityTier.LOW
