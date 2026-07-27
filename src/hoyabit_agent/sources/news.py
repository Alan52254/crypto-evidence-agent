"""新聞 RSS —— 基本面與情緒面的證據源。

性價比最高的一層：**免金鑰、免註冊、無額度限制**，且 RSS 天然帶
發布時間與原文連結，正好是溯源需要的東西。

一篇文章同時產出兩種證據 —— 基本面（發生了什麼事）與情緒面
（文本的傾向）—— 因為**證據面與資料源是正交的**。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol
from xml.etree import ElementTree

import httpx

from hoyabit_agent.arguments import bounded_int
from hoyabit_agent.dedup import assign_event_keys
from hoyabit_agent.domain import AnalysisRegime, Asset, Evidence, Facet, LabelAspect, SourceExcerpt
from hoyabit_agent.lexicon import LexiconLabeller
from hoyabit_agent.seams import Arguments, ToolSpec

FEEDS: tuple[tuple[str, str], ...] = (
    ("cointelegraph", "https://cointelegraph.com/rss"),
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
)

# 幣種的別名。命中任一即視為該篇文章與此幣種相關。
_ALIASES: dict[Asset, frozenset[str]] = {
    Asset.BTC: frozenset({"bitcoin", "btc", "比特幣"}),
    Asset.ETH: frozenset({"ethereum", "ether", "eth", "以太"}),
    Asset.SOL: frozenset({"solana", "sol"}),
    Asset.BNB: frozenset({"bnb", "binance coin", "binancecoin"}),
    Asset.XRP: frozenset({"xrp", "ripple"}),
}

_TAGS = re.compile(r"<[^>]+>")


class Labeller(Protocol):
    """情緒打分的能力 —— `ModelProvider.label` 與 `LexiconLabeller` 都滿足它。

    刻意只依賴這個最小介面而非整個 `ModelProvider`：新聞源不需要規劃
    或撰寫判斷的能力，要求它們只會讓這個來源更難替換與測試。
    """

    async def label(
        self,
        texts: Sequence[str],
        aspect: LabelAspect = LabelAspect.SENTIMENT,
    ) -> tuple[float, ...]: ...


@dataclass(frozen=True)
class Article:
    title: str
    link: str
    published: datetime
    summary: str
    outlet: str


def strip_markup(text: str) -> str:
    return _TAGS.sub("", text).strip()


def mentions(asset: Asset, text: str) -> bool:
    lowered = text.lower()
    return any(alias in lowered for alias in _ALIASES[asset])


def parse_feed(xml: str, outlet: str) -> tuple[Article, ...]:
    """解析 RSS 2.0。格式壞掉時回傳空集合，不拋例外。"""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return ()

    articles: list[Article] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        articles.append(
            Article(
                title=title,
                link=link,
                published=_published(item.findtext("pubDate")),
                summary=strip_markup(item.findtext("description") or ""),
                outlet=outlet,
            )
        )
    return tuple(articles)


def _published(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(UTC)
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return datetime.now(UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class NewsRssSource:
    """新聞 RSS → 基本面 + 情緒面證據。

    `labeller` 預設是零依賴的詞典打分，因此**沒有模型金鑰也能完整運作**；
    ticket 05 的模型供應者滿足同一個介面，可直接對調。

    只抓得到當下的最新文章,無法以歷史截止日限定,只在即時模式合規。
    """

    supported_regimes: frozenset[AnalysisRegime] = frozenset({AnalysisRegime.LIVE})

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        labeller: Labeller | None = None,
        feeds: Sequence[tuple[str, str]] = FEEDS,
    ) -> None:
        self._client = client
        self._labeller = labeller or LexiconLabeller()
        self._feeds = tuple(feeds)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="crypto_news",
            description=(
                "取得與該幣種相關的近期新聞原文（Cointelegraph、CoinDesk），"
                "產出基本面證據（發生了什麼事）與情緒面證據（文本傾向）。"
                "同一事件被多家媒體轉載時會歸併為單一證據，"
                "以免轉載被誤計為多個獨立證據。"
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
                        "maximum": 40,
                        "description": "最多取幾篇文章。",
                    },
                },
            },
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        hours = bounded_int(arguments.get("hours"), 1, 168, 24)
        limit = bounded_int(arguments.get("limit"), 1, 40, 12)

        articles = await self._gather(asset, hours, limit)
        if not articles:
            return ()

        keys = assign_event_keys([article.title for article in articles])
        texts = [f"{a.title}。{a.summary}" for a in articles]

        # 兩次批次打分，問的是兩個不同的問題：
        # 情緒面問「這段文字的語氣如何」，基本面問「這個事件的實質影響如何」。
        # 一則利多事件可以被以懷疑的語氣報導 —— 那個分歧正是信心度要捕捉的。
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

            found.append(
                Evidence(
                    id=f"NEWS-FUND-{digest}-{article.outlet}",
                    facet=Facet.FUNDAMENTAL,
                    summary=f"事件實質影響 {impact:+.2f}：{article.title}",
                    stance_hint=impact,
                    excerpts=(excerpt,),
                    event_key=f"{key}-fundamental",
                )
            )
            found.append(
                Evidence(
                    id=f"NEWS-SENT-{digest}-{article.outlet}",
                    facet=Facet.SENTIMENT,
                    summary=f"{article.outlet} 報導的文本傾向 {score:+.2f}：{article.title}",
                    stance_hint=score,
                    excerpts=(excerpt,),
                    event_key=f"{key}-sentiment",
                )
            )

        return tuple(found)

    async def articles(
        self, asset: Asset, *, hours: int = 168, limit: int = 40
    ) -> tuple[Article, ...]:
        """取回近期相關文章的原始物件。

        背景 ingestion 用它重用取數與過濾邏輯，而不必把整個證據源搬過去。
        預設看一週、取多一些，因為 ingestion 是要累積庫存而非單次分析。
        """
        return await self._gather(asset, hours, limit)

    async def _gather(self, asset: Asset, hours: int, limit: int) -> tuple[Article, ...]:
        payloads = await asyncio.gather(
            *(self._read(url) for _, url in self._feeds), return_exceptions=True
        )

        cutoff = datetime.now(UTC).timestamp() - hours * 3600
        relevant: list[Article] = []
        for (outlet, _), payload in zip(self._feeds, payloads, strict=True):
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
        """單一 feed 讀取。失效回傳 None —— 一個來源掛掉不該拖垮其他來源。"""
        try:
            response = await self._client.get(url, follow_redirects=True)
        except httpx.HTTPError:
            return None
        return response.text if response.status_code == 200 else None

    async def _score(self, texts: Sequence[str], aspect: LabelAspect) -> tuple[float, ...]:
        """批次打分。

        刻意一次送一批而非逐則呼叫：Gemini 免費層是 10 RPM，
        逐則打分會讓 30 篇文章吃掉 3 分鐘的壁鐘預算。
        打分失敗時全部退回中性，不中斷分析回合。
        """
        try:
            scores = await self._labeller.label(list(texts), aspect)
        except Exception:  # noqa: BLE001 — 打分失敗只該讓該面轉中性
            return tuple(0.0 for _ in texts)
        if len(scores) != len(texts):
            return tuple(0.0 for _ in texts)
        return scores


__all__ = ["FEEDS", "Article", "Labeller", "NewsRssSource", "mentions", "parse_feed"]
