"""新聞 RSS 證據源測試 —— 以 httpx MockTransport 攔截，完全不碰網路。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from hoyabit_agent.domain import Asset, Facet, LabelAspect
from hoyabit_agent.sources.news import NewsRssSource, mentions, parse_feed, strip_markup

FEEDS = (("cointelegraph", "https://feed-a.test/rss"), ("coindesk", "https://feed-b.test/rss"))


def rss(*items: tuple[str, str, str], hours_ago: int = 1) -> str:
    when = format_datetime(datetime.now(UTC) - timedelta(hours=hours_ago))
    body = "".join(
        f"<item><title>{title}</title><link>{link}</link>"
        f"<pubDate>{when}</pubDate><description>{summary}</description></item>"
        for title, link, summary in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{body}</channel></rss>'


def client_for(bodies: dict[str, str], *, status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = bodies.get(str(request.url))
        if body is None:
            return httpx.Response(404, text="")
        return httpx.Response(status, text=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def source_with(
    bodies: dict[str, str], **kwargs: object
) -> tuple[NewsRssSource, httpx.AsyncClient]:
    client = client_for(bodies)
    return NewsRssSource(client, feeds=FEEDS, **kwargs), client  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------


def test_markup_is_stripped_from_summaries() -> None:
    assert strip_markup("<p>Bitcoin <b>surges</b></p>") == "Bitcoin surges"


def test_a_malformed_feed_yields_no_articles_instead_of_raising() -> None:
    assert parse_feed("<rss><unclosed>", "cointelegraph") == ()


def test_items_without_a_title_or_link_are_skipped() -> None:
    xml = '<?xml version="1.0"?><rss><channel><item><title>只有標題</title></item></channel></rss>'
    assert parse_feed(xml, "cointelegraph") == ()


def test_an_unparseable_date_falls_back_to_now_rather_than_dropping_the_article() -> None:
    xml = (
        '<?xml version="1.0"?><rss><channel><item><title>T</title>'
        "<link>https://x.test/1</link><pubDate>不是日期</pubDate></item></channel></rss>"
    )
    articles = parse_feed(xml, "coindesk")
    assert len(articles) == 1
    assert articles[0].published.tzinfo is not None


# --------------------------------------------------------------------------
# 幣種相關性
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("asset", "text"),
    [
        (Asset.BTC, "Bitcoin hits new high"),
        (Asset.BTC, "BTC 比特幣衝高"),
        (Asset.ETH, "Ethereum upgrade ships"),
        (Asset.SOL, "Solana network activity"),
        (Asset.XRP, "Ripple wins case"),
    ],
)
def test_articles_mentioning_the_asset_are_relevant(asset: Asset, text: str) -> None:
    assert mentions(asset, text)


def test_articles_about_other_assets_are_not_relevant() -> None:
    assert not mentions(Asset.BTC, "Solana network activity climbs")


# --------------------------------------------------------------------------
# 證據產出
# --------------------------------------------------------------------------


async def test_one_article_produces_both_a_fundamental_and_a_sentiment_evidence() -> None:
    bodies = {
        FEEDS[0][1]: rss(("Bitcoin ETF sees record inflow", "https://a.test/1", "Bitcoin surges")),
        FEEDS[1][1]: rss(),
    }
    source, client = source_with(bodies)
    async with client:
        found = await source.fetch(Asset.BTC, {})

    assert {item.facet for item in found} == {Facet.FUNDAMENTAL, Facet.SENTIMENT}


async def test_every_evidence_links_back_to_the_original_article() -> None:
    bodies = {
        FEEDS[0][1]: rss(("Bitcoin ETF inflow", "https://a.test/1", "Bitcoin surges")),
        FEEDS[1][1]: rss(),
    }
    source, client = source_with(bodies)
    async with client:
        found = await source.fetch(Asset.BTC, {})

    for item in found:
        assert item.excerpts
        assert item.excerpts[0].url == "https://a.test/1"
        assert "cointelegraph" in item.excerpts[0].locator
        assert item.excerpts[0].text


async def test_the_fundamental_facet_can_take_a_direction() -> None:
    """基本面必須能獨立表態，否則四個面裡永遠只有三個能出聲。"""
    bodies = {
        FEEDS[0][1]: rss(("Bitcoin ETF inflow surges", "https://a.test/1", "Bitcoin surges")),
        FEEDS[1][1]: rss(),
    }
    source, client = source_with(bodies)
    async with client:
        found = await source.fetch(Asset.BTC, {})

    fundamental = next(item for item in found if item.facet is Facet.FUNDAMENTAL)
    assert fundamental.stance_hint > 0


class AspectAwareLabeller:
    """對兩個面給相反的分數 —— 模擬「利多事件、悲觀語氣」。"""

    def __init__(self) -> None:
        self.asked: list[LabelAspect] = []

    async def label(
        self,
        texts: Sequence[str],
        aspect: LabelAspect = LabelAspect.SENTIMENT,
    ) -> tuple[float, ...]:
        self.asked.append(aspect)
        value = 0.9 if aspect is LabelAspect.FUNDAMENTAL else -0.9
        return tuple(value for _ in texts)


async def test_the_two_facets_are_scored_by_asking_two_different_questions() -> None:
    labeller = AspectAwareLabeller()
    bodies = {
        FEEDS[0][1]: rss(("Bitcoin ETF approved", "https://a.test/1", "Bitcoin")),
        FEEDS[1][1]: rss(),
    }
    source, client = source_with(bodies, labeller=labeller)
    async with client:
        await source.fetch(Asset.BTC, {})

    assert set(labeller.asked) == {LabelAspect.SENTIMENT, LabelAspect.FUNDAMENTAL}


async def test_a_bullish_event_reported_in_a_pessimistic_tone_makes_the_facets_disagree() -> None:
    """兩面可以真的相反 —— 那個分歧正是信心度要捕捉的東西。

    若兩面共用同一個分數，它們會永遠一致，系統性地灌高信心度。
    """
    bodies = {
        FEEDS[0][1]: rss(("Bitcoin ETF approved", "https://a.test/1", "Bitcoin")),
        FEEDS[1][1]: rss(),
    }
    source, client = source_with(bodies, labeller=AspectAwareLabeller())
    async with client:
        found = await source.fetch(Asset.BTC, {})

    fundamental = next(item for item in found if item.facet is Facet.FUNDAMENTAL)
    sentiment = next(item for item in found if item.facet is Facet.SENTIMENT)
    assert fundamental.stance_hint > 0 > sentiment.stance_hint


async def test_bullish_wording_scores_positive_without_any_model() -> None:
    """沒有模型金鑰時系統仍完整可跑 —— 詞典後備接手。"""
    bodies = {
        FEEDS[0][1]: rss(("Bitcoin rallies to record high", "https://a.test/1", "surge inflow")),
        FEEDS[1][1]: rss(),
    }
    source, client = source_with(bodies)
    async with client:
        found = await source.fetch(Asset.BTC, {})

    sentiment = next(item for item in found if item.facet is Facet.SENTIMENT)
    assert sentiment.stance_hint > 0


async def test_bearish_wording_scores_negative() -> None:
    bodies = {
        FEEDS[0][1]: rss(("Bitcoin plunges after exchange hack", "https://a.test/1", "selloff")),
        FEEDS[1][1]: rss(),
    }
    source, client = source_with(bodies)
    async with client:
        found = await source.fetch(Asset.BTC, {})

    sentiment = next(item for item in found if item.facet is Facet.SENTIMENT)
    assert sentiment.stance_hint < 0


# --------------------------------------------------------------------------
# 證據獨立性（ADR 0002）
# --------------------------------------------------------------------------


async def test_the_same_story_from_two_outlets_shares_an_event_key() -> None:
    """轉載不構成獨立證據 —— 兩家媒體的同一則報導必須拿到同一個事件鍵。"""
    headline = "Bitcoin spot ETF records largest daily inflow"
    bodies = {
        FEEDS[0][1]: rss((headline, "https://a.test/1", "Bitcoin ETF inflow")),
        FEEDS[1][1]: rss(
            ("Bitcoin spot ETF sees largest daily inflow", "https://b.test/2", "Bitcoin ETF"),
        ),
    }
    source, client = source_with(bodies)
    async with client:
        found = await source.fetch(Asset.BTC, {})

    sentiment_keys = {i.event_key for i in found if i.facet is Facet.SENTIMENT}
    assert len(sentiment_keys) == 1


async def test_unrelated_stories_keep_separate_event_keys() -> None:
    bodies = {
        FEEDS[0][1]: rss(
            ("Bitcoin spot ETF records inflow", "https://a.test/1", "Bitcoin ETF"),
            ("Bitcoin miner capitulation deepens", "https://a.test/2", "Bitcoin mining"),
        ),
        FEEDS[1][1]: rss(),
    }
    source, client = source_with(bodies)
    async with client:
        found = await source.fetch(Asset.BTC, {})

    sentiment_keys = {i.event_key for i in found if i.facet is Facet.SENTIMENT}
    assert len(sentiment_keys) == 2


async def test_fundamental_and_sentiment_of_one_article_do_not_merge_together() -> None:
    """同一事件的兩個面是不同證據，不該被歸併掉其中一個。"""
    bodies = {
        FEEDS[0][1]: rss(("Bitcoin ETF inflow", "https://a.test/1", "Bitcoin surges")),
        FEEDS[1][1]: rss(),
    }
    source, client = source_with(bodies)
    async with client:
        found = await source.fetch(Asset.BTC, {})

    keys = [item.event_key for item in found]
    assert len(set(keys)) == 2


# --------------------------------------------------------------------------
# 韌性
# --------------------------------------------------------------------------


async def test_one_dead_feed_does_not_lose_the_other() -> None:
    bodies = {FEEDS[1][1]: rss(("Bitcoin rallies", "https://b.test/1", "Bitcoin surge"))}
    source, client = source_with(bodies)
    async with client:
        found = await source.fetch(Asset.BTC, {})

    assert found
    assert all("b.test" in item.excerpts[0].url for item in found)


async def test_every_feed_failing_yields_an_empty_set() -> None:
    source, client = source_with({})
    async with client:
        assert await source.fetch(Asset.BTC, {}) == ()


async def test_articles_outside_the_time_window_are_excluded() -> None:
    bodies = {
        FEEDS[0][1]: rss(("Bitcoin rallies", "https://a.test/1", "surge"), hours_ago=100),
        FEEDS[1][1]: rss(),
    }
    source, client = source_with(bodies)
    async with client:
        assert await source.fetch(Asset.BTC, {"hours": 24}) == ()


async def test_the_limit_caps_how_many_articles_become_evidence() -> None:
    items = tuple(
        (f"Bitcoin story number {index}", f"https://a.test/{index}", "Bitcoin")
        for index in range(9)
    )
    bodies = {FEEDS[0][1]: rss(*items), FEEDS[1][1]: rss()}
    source, client = source_with(bodies)
    async with client:
        found = await source.fetch(Asset.BTC, {"limit": 3})

    assert len({item.excerpts[0].url for item in found}) == 3


class BrokenLabeller:
    async def label(self, texts: Sequence[str]) -> tuple[float, ...]:
        raise RuntimeError("model unavailable")


class WrongLengthLabeller:
    async def label(self, texts: Sequence[str]) -> tuple[float, ...]:
        return (0.9,)


@pytest.mark.parametrize("labeller", [BrokenLabeller(), WrongLengthLabeller()])
async def test_a_failing_labeller_degrades_sentiment_to_neutral(labeller: object) -> None:
    """打分失敗只該讓情緒面轉中性，不該中斷整個分析回合。"""
    bodies = {
        FEEDS[0][1]: rss(
            ("Bitcoin rallies", "https://a.test/1", "surge"),
            ("Bitcoin miners capitulate", "https://a.test/2", "selloff"),
        ),
        FEEDS[1][1]: rss(),
    }
    source, client = source_with(bodies, labeller=labeller)
    async with client:
        found = await source.fetch(Asset.BTC, {})

    assert found
    assert all(i.stance_hint == 0.0 for i in found if i.facet is Facet.SENTIMENT)


@pytest.mark.parametrize(
    "arguments",
    [{}, {"hours": "abc"}, {"limit": -1}, {"hours": 99_999}, {"沒有這個鍵": 1}],
)
async def test_nonsense_arguments_degrade_to_defaults(arguments: dict[str, object]) -> None:
    bodies = {
        FEEDS[0][1]: rss(("Bitcoin rallies", "https://a.test/1", "surge")),
        FEEDS[1][1]: rss(),
    }
    source, client = source_with(bodies)
    async with client:
        assert await source.fetch(Asset.BTC, arguments)
