"""擴展 RSS 來源 — Blocktempo、Blockworks、官方公告。

選擇理由：
- Blocktempo：繁中第一大加密媒體，覆蓋台灣監管觀點，與英文源交叉驗證
- Blockworks：機構/ETF 視角，與零售媒體觀點不同
- 官方公告：第一手資訊，最高品質的 fundamental 證據
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from hoyabit_agent.arguments import bounded_int
from hoyabit_agent.dedup import assign_event_keys
from hoyabit_agent.domain import Asset, Evidence, Facet, LabelAspect, SourceExcerpt
from hoyabit_agent.lexicon import LexiconLabeller
from hoyabit_agent.seams import Arguments, ToolSpec
from hoyabit_agent.sources.news import Article, Labeller, mentions, parse_feed


# ── 擴展 Feed 列表 ──

EXTENDED_FEEDS: tuple[tuple[str, str], ...] = (
    ("blocktempo", "https://www.blocktempo.com/feed/"),
    ("blockworks", "https://blockworks.co/feed/"),
)

# 官方公告 — 每個幣種的第一手來源
OFFICIAL_FEEDS: dict[Asset, tuple[tuple[str, str], ...]] = {
    Asset.BTC: (
        ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ),
    Asset.ETH: (
        ("ethereum-blog", "https://blog.ethereum.org/feed.xml"),
    ),
    Asset.SOL: (),   # Solana 沒有公開 RSS feed，由 crypto_news 覆蓋
    Asset.BNB: (),   # Binance 沒有公開 RSS feed，由 crypto_news 覆蓋
    Asset.XRP: (),   # Ripple Insights 無 RSS feed，由 crypto_news 覆蓋
}


class ExtendedNewsSource:
    """Blocktempo + Blockworks RSS — 基本面 + 情緒面的補充證據源。"""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        labeller: Labeller | None = None,
    ) -> None:
        self._client = client
        self._labeller = labeller or LexiconLabeller()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="extended_news",
            description=(
                "取得 Blocktempo（繁中）與 Blockworks（機構觀點）的近期新聞，"
                "提供基本面與情緒面的獨立交叉驗證證據。"
                "與 crypto_news 使用不同媒體來源，避免重複。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 168,
                        "description": "只看最近幾小時內發布的新聞。",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 30,
                        "description": "最多取幾篇文章。",
                    },
                },
            },
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        hours = bounded_int(arguments.get("hours"), 1, 168, 48)
        limit = bounded_int(arguments.get("limit"), 1, 30, 8)

        articles = await self._gather(asset, hours, limit)
        if not articles:
            return ()

        keys = assign_event_keys([a.title for a in articles])
        texts = [f"{a.title}。{a.summary}" for a in articles]

        sentiment, fundamental = await asyncio.gather(
            self._score(texts, LabelAspect.SENTIMENT),
            self._score(texts, LabelAspect.FUNDAMENTAL),
        )

        found: list[Evidence] = []
        for article, key, score, impact in zip(
            articles, keys, sentiment, fundamental, strict=True
        ):
            excerpt = SourceExcerpt(
                source_id=f"{article.outlet}:{article.link}",
                url=article.link,
                retrieved_at=datetime.now(UTC),
                locator=f"{article.outlet} 於 {article.published:%Y-%m-%d %H:%M} 發布",
                text=f"{article.title}。{article.summary}"[:600],
            )
            digest = key.removeprefix("EVT-")

            found.append(Evidence(
                id=f"XNEWS-FUND-{digest}-{article.outlet}",
                facet=Facet.FUNDAMENTAL,
                summary=f"[{article.outlet}] 事件影響 {impact:+.2f}：{article.title}",
                stance_hint=impact,
                excerpts=(excerpt,),
                event_key=f"{key}-fundamental",
            ))
            found.append(Evidence(
                id=f"XNEWS-SENT-{digest}-{article.outlet}",
                facet=Facet.SENTIMENT,
                summary=f"[{article.outlet}] 文本傾向 {score:+.2f}：{article.title}",
                stance_hint=score,
                excerpts=(excerpt,),
                event_key=f"{key}-sentiment",
            ))

        return tuple(found)

    async def _gather(
        self, asset: Asset, hours: int, limit: int
    ) -> tuple[Article, ...]:
        payloads = await asyncio.gather(
            *(self._read(url) for _, url in EXTENDED_FEEDS),
            return_exceptions=True,
        )
        cutoff = datetime.now(UTC).timestamp() - hours * 3600
        relevant: list[Article] = []
        for (outlet, _), payload in zip(EXTENDED_FEEDS, payloads, strict=True):
            if not isinstance(payload, str):
                continue
            for article in parse_feed(payload, outlet):
                if article.published.timestamp() < cutoff:
                    continue
                if not mentions(asset, f"{article.title} {article.summary}"):
                    continue
                relevant.append(article)
        relevant.sort(key=lambda a: a.published, reverse=True)
        return tuple(relevant[:limit])

    async def _read(self, url: str) -> str | None:
        try:
            response = await self._client.get(url, follow_redirects=True)
        except httpx.HTTPError:
            return None
        return response.text if response.status_code == 200 else None

    async def _score(
        self, texts: Sequence[str], aspect: LabelAspect
    ) -> tuple[float, ...]:
        try:
            scores = await self._labeller.label(list(texts), aspect)
        except Exception:
            return tuple(0.0 for _ in texts)
        return scores if len(scores) == len(texts) else tuple(0.0 for _ in texts)


class OfficialAnnouncementSource:
    """官方公告 RSS — 最高品質的 fundamental 證據。"""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        labeller: Labeller | None = None,
    ) -> None:
        self._client = client
        self._labeller = labeller or LexiconLabeller()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="official_announcements",
            description=(
                "取得該幣種官方團隊的公告（Ethereum Blog、Solana News、"
                "Ripple Insights、Binance Blog）。"
                "這是最高品質的基本面證據：第一手、無轉載、無第三方解讀。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 720,
                        "description": "只看最近幾小時內的公告（官方公告較少，建議放寬）。",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "最多取幾篇公告。",
                    },
                },
            },
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        hours = bounded_int(arguments.get("hours"), 1, 720, 168)
        limit = bounded_int(arguments.get("limit"), 1, 10, 5)

        feeds = OFFICIAL_FEEDS.get(asset, ())
        if not feeds:
            return ()

        payloads = await asyncio.gather(
            *(self._read(url) for _, url in feeds),
            return_exceptions=True,
        )

        cutoff = datetime.now(UTC).timestamp() - hours * 3600
        articles: list[Article] = []
        for (outlet, _), payload in zip(feeds, payloads, strict=True):
            if not isinstance(payload, str):
                continue
            for article in parse_feed(payload, outlet):
                if article.published.timestamp() < cutoff:
                    continue
                articles.append(article)

        articles.sort(key=lambda a: a.published, reverse=True)
        articles = articles[:limit]

        if not articles:
            return ()

        texts = [f"{a.title}。{a.summary}" for a in articles]
        impacts = await self._score(texts, LabelAspect.FUNDAMENTAL)

        found: list[Evidence] = []
        for article, impact in zip(articles, impacts, strict=True):
            excerpt = SourceExcerpt(
                source_id=f"official:{article.outlet}:{article.link}",
                url=article.link,
                retrieved_at=datetime.now(UTC),
                locator=f"[官方] {article.outlet} 於 {article.published:%Y-%m-%d %H:%M}",
                text=f"{article.title}。{article.summary}"[:600],
            )
            found.append(Evidence(
                id=f"OFFICIAL-{article.outlet}-{article.published:%Y%m%d%H%M}",
                facet=Facet.FUNDAMENTAL,
                summary=f"[官方公告] {article.title}",
                stance_hint=impact,
                excerpts=(excerpt,),
                event_key=None,  # 官方公告不歸併，每篇都是獨立證據
            ))

        return tuple(found)

    async def _read(self, url: str) -> str | None:
        try:
            response = await self._client.get(url, follow_redirects=True)
        except httpx.HTTPError:
            return None
        return response.text if response.status_code == 200 else None

    async def _score(
        self, texts: Sequence[str], aspect: LabelAspect
    ) -> tuple[float, ...]:
        try:
            scores = await self._labeller.label(list(texts), aspect)
        except Exception:
            return tuple(0.0 for _ in texts)
        return scores if len(scores) == len(texts) else tuple(0.0 for _ in texts)


__all__ = ["ExtendedNewsSource", "OfficialAnnouncementSource"]
