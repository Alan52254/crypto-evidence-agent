"""Binance 公開端點 —— 技術面與籌碼面的證據源。

選它作為輪詢主力的理由：**不需要金鑰、不需要註冊**，以 IP 計 weight，
且回傳的是原始數值序列而非任何現成結論 —— 符合命題「不能直接調用
已經有結論的 API」的約束。所有指標由我們自己的 Function 工具計算。

現貨端點提供技術面；合約端點（同樣公開免金鑰）提供資金費率、
未平倉合約、多空帳戶比 —— 那才是真正的籌碼面。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from hoyabit_agent.arguments import bounded_int, choice
from hoyabit_agent.domain import AnalysisRegime, Asset, Evidence, Facet, SourceExcerpt
from hoyabit_agent.indicators import (
    Kline,
    funding_to_stance,
    long_short_to_stance,
    open_interest_change,
    order_book_imbalance,
    relative_spread,
    relative_to_average,
    rsi,
    rsi_to_stance,
    sma,
    volume_change,
)
from hoyabit_agent.seams import Arguments, ToolSpec

SPOT_BASE = "https://api.binance.com"
FUTURES_BASE = "https://fapi.binance.com"

# Binance 以 IP 計 weight 並在回應標頭回報用量。接近上限時主動退讓，
# 不硬撞 429 —— 被封 IP 的代價遠高於這次少拿一批證據。
WEIGHT_HEADER = "x-mbx-used-weight-1m"
WEIGHT_CEILING = 1000

_INTERVALS = ("15m", "1h", "4h", "1d")
_PERIODS = ("5m", "15m", "1h", "4h", "1d")


def symbol_for(asset: Asset) -> str:
    return f"{asset.value}USDT"


def _excerpt(source_id: str, url: str, locator: str, text: str) -> SourceExcerpt:
    return SourceExcerpt(
        source_id=source_id,
        url=url,
        retrieved_at=datetime.now(UTC),
        locator=locator,
        text=text,
    )


class _BinanceSource:
    """兩個 Binance 證據源共用的取數與退讓邏輯。

    只回傳**當下**的即時市場狀態,無法以歷史截止日限定,故只在即時模式合規 ——
    回測模式呼叫它等於讓證據悄悄夾帶截止日之後的資訊(ADR 0005 / Sol B)。
    """

    supported_regimes: frozenset[AnalysisRegime] = frozenset({AnalysisRegime.LIVE})

    def __init__(self, client: httpx.AsyncClient, weight_ceiling: int = WEIGHT_CEILING) -> None:
        self._client = client
        self._weight_ceiling = weight_ceiling
        self._used_weight = 0

    @property
    def used_weight(self) -> int:
        """上一次回應回報的已用 weight。接近上限時會停止發送新請求。"""
        return self._used_weight

    async def _get(self, url: str, params: dict[str, Any]) -> Any | None:
        """回傳 None 代表這次拿不到資料。呼叫端一律當作「沒有證據」處理。

        退避是**發送前**判斷的：weight 一旦記錄到接近上限，後續請求直接不送。
        在收到回應之後才判斷是沒有意義的 —— 那一次的 weight 已經花掉了，
        而且會把一份有效的資料白白丟掉。
        """
        if self._used_weight >= self._weight_ceiling:
            return None

        try:
            response = await self._client.get(url, params=params)
        except httpx.HTTPError:
            return None

        self._record_weight(response.headers.get(WEIGHT_HEADER))

        if response.status_code != 200:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def _record_weight(self, raw: str | None) -> None:
        if raw is None:
            return
        try:
            self._used_weight = int(raw)
        except ValueError:
            pass


class BinanceSpotSource(_BinanceSource):
    """現貨市場 → 技術面（K 線）與籌碼面（盤口）證據。

    這個來源刻意同時產出兩個證據面 —— **證據面與資料源是正交的**，
    同一個來源可以產出任意組合的面。把盤口另開一個工具只會讓
    模型要學的工具數量膨脹，卻沒有換到任何東西。
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="binance_spot",
            description=(
                "取得該幣種在 Binance 現貨市場的原始 K 線與盤口掛單，"
                "並由此計算均線位置、RSI、量能變化（技術面）"
                "以及買賣盤失衡與價差（籌碼面）。"
                "回傳的是原始數值與算式，不是任何現成結論。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "interval": {
                        "type": "string",
                        "enum": list(_INTERVALS),
                        "description": "K 線週期。短線分析用 1h/4h，趨勢判斷用 1d。",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 60,
                        "maximum": 500,
                        "description": "取幾根 K 線。要算 200 期均線就至少要 200 根。",
                    },
                    "depth": {
                        "type": "integer",
                        "minimum": 5,
                        "maximum": 500,
                        "description": "盤口取幾檔。檔數越多越能看出深層掛單結構。",
                    },
                },
            },
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        interval = choice(arguments.get("interval"), _INTERVALS, "1d")
        limit = bounded_int(arguments.get("limit"), 60, 500, 200)
        depth = bounded_int(arguments.get("depth"), 5, 500, 100)
        symbol = symbol_for(asset)

        return await self._klines(asset, symbol, interval, limit) + await self._depth(
            symbol, f"BNC-SPOT-{asset.value}", depth
        )

    async def _klines(
        self, asset: Asset, symbol: str, interval: str, limit: int
    ) -> tuple[Evidence, ...]:
        url = f"{SPOT_BASE}/api/v3/klines"
        payload = await self._get(url, {"symbol": symbol, "interval": interval, "limit": limit})
        if not isinstance(payload, list) or not payload:
            return ()

        try:
            klines = [
                Kline(
                    open_time_ms=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
                for row in payload
            ]
        except (IndexError, TypeError, ValueError):
            return ()

        closes = [k.close for k in klines]
        volumes = [k.volume for k in klines]
        latest = closes[-1]
        prefix = f"BNC-SPOT-{asset.value}-{interval}"
        found: list[Evidence] = []

        for period in (60, 200):
            average = sma(closes, period)
            if average is None:
                continue
            found.append(
                Evidence(
                    id=f"{prefix}-SMA{period}",
                    facet=Facet.TECHNICAL,
                    summary=f"{symbol} 收盤 {latest:.2f}，{period} 期均線 {average:.2f}",
                    stance_hint=relative_to_average(latest, average),
                    excerpts=(
                        _excerpt(
                            f"{prefix}-SMA{period}",
                            f"{url}?symbol={symbol}&interval={interval}&limit={limit}",
                            f"最後 {period} 根 K 線收盤價",
                            f"收盤 {latest:.2f}；SMA{period} = {average:.2f}；"
                            f"乖離 {(latest - average) / average:+.2%}",
                        ),
                    ),
                )
            )

        strength = rsi(closes)
        if strength is not None:
            found.append(
                Evidence(
                    id=f"{prefix}-RSI14",
                    facet=Facet.TECHNICAL,
                    summary=f"{symbol} 14 期 RSI 為 {strength:.1f}",
                    stance_hint=rsi_to_stance(strength),
                    excerpts=(
                        _excerpt(
                            f"{prefix}-RSI14",
                            f"{url}?symbol={symbol}&interval={interval}&limit={limit}",
                            "14 期 Wilder 平滑 RSI",
                            f"RSI(14) = {strength:.2f}（50 為中性）",
                        ),
                    ),
                )
            )

        change = volume_change(volumes)
        if change is not None:
            found.append(
                Evidence(
                    id=f"{prefix}-VOL",
                    facet=Facet.TECHNICAL,
                    summary=f"{symbol} 近 5 期均量較前 5 期變化 {change:+.1%}",
                    # 量能本身沒有方向，它是強度不是傾向 —— 故傾向為中性。
                    stance_hint=0.0,
                    excerpts=(
                        _excerpt(
                            f"{prefix}-VOL",
                            f"{url}?symbol={symbol}&interval={interval}&limit={limit}",
                            "近 5 期與前 5 期成交量",
                            f"均量變化 {change:+.2%}",
                        ),
                    ),
                )
            )

        return tuple(found)

    async def _depth(self, symbol: str, prefix: str, depth: int) -> tuple[Evidence, ...]:
        """盤口掛單 → 籌碼面證據。

        盤口失衡反映當下的承接／壓制力量，是最即時的籌碼訊號 ——
        它比資金費率更早反應，因為掛單不需要成交。
        """
        url = f"{SPOT_BASE}/api/v3/depth"
        payload = await self._get(url, {"symbol": symbol, "limit": depth})
        if not isinstance(payload, dict):
            return ()

        try:
            bids = [(float(price), float(qty)) for price, qty in payload["bids"]]
            asks = [(float(price), float(qty)) for price, qty in payload["asks"]]
        except (KeyError, TypeError, ValueError):
            return ()
        if not bids or not asks:
            return ()

        bid_quantity = sum(qty for _, qty in bids)
        ask_quantity = sum(qty for _, qty in asks)
        imbalance = (bid_quantity - ask_quantity) / (bid_quantity + ask_quantity)
        best_bid = bids[0][0]
        best_ask = asks[0][0]
        source = f"{url}?symbol={symbol}&limit={depth}"
        found: list[Evidence] = [
            Evidence(
                id=f"{prefix}-BOOK",
                facet=Facet.POSITIONING,
                summary=(
                    f"{symbol} 前 {len(bids)} 檔買盤量 {bid_quantity:.2f}、"
                    f"賣盤量 {ask_quantity:.2f}"
                ),
                stance_hint=order_book_imbalance(bid_quantity, ask_quantity),
                excerpts=(
                    _excerpt(
                        f"{prefix}-BOOK",
                        source,
                        f"前 {depth} 檔買賣掛單",
                        f"買盤合計 {bid_quantity:.4f}；賣盤合計 {ask_quantity:.4f}；"
                        f"失衡 {imbalance:+.2%}",
                    ),
                ),
            )
        ]

        spread = relative_spread(best_bid, best_ask)
        if spread is not None:
            found.append(
                Evidence(
                    id=f"{prefix}-SPREAD",
                    facet=Facet.POSITIONING,
                    summary=f"{symbol} 買賣價差為中價的 {spread:.4%}",
                    # 價差是流動性（強度），不是方向 —— 故不表態。
                    stance_hint=0.0,
                    excerpts=(
                        _excerpt(
                            f"{prefix}-SPREAD",
                            source,
                            "最佳買價與最佳賣價",
                            f"最佳買 {best_bid:.2f}／最佳賣 {best_ask:.2f}；"
                            f"相對價差 {spread:.6%}",
                        ),
                    ),
                )
            )

        return tuple(found)


class BinanceDerivativesSource(_BinanceSource):
    """合約公開端點 → 籌碼面證據（資金費率、未平倉合約、多空帳戶比）。

    這些端點同樣免金鑰。少了它們，籌碼面永遠是空的，
    信心度也就永遠算不滿四面。
    """

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="binance_derivatives",
            description=(
                "取得該幣種永續合約的資金費率、未平倉合約與多空帳戶比等原始數值，"
                "並由此計算籌碼面證據。回傳原始數值序列，不含任何現成結論。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "enum": list(_PERIODS),
                        "description": "未平倉合約與多空比的取樣週期。",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 2,
                        "maximum": 200,
                        "description": "取幾個資料點。",
                    },
                },
            },
        )

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        period = choice(arguments.get("period"), _PERIODS, "1d")
        limit = bounded_int(arguments.get("limit"), 2, 200, 30)
        symbol = symbol_for(asset)
        prefix = f"BNC-PERP-{asset.value}"
        found: list[Evidence] = []

        found.extend(await self._funding(symbol, prefix, limit))
        found.extend(await self._open_interest(symbol, prefix, period, limit))
        found.extend(await self._long_short(symbol, prefix, period, limit))
        return tuple(found)

    async def _funding(self, symbol: str, prefix: str, limit: int) -> list[Evidence]:
        url = f"{FUTURES_BASE}/fapi/v1/fundingRate"
        payload = await self._get(url, {"symbol": symbol, "limit": limit})
        if not isinstance(payload, list) or not payload:
            return []
        try:
            rates = [float(row["fundingRate"]) for row in payload]
        except (KeyError, TypeError, ValueError):
            return []

        latest = rates[-1]
        average = sum(rates) / len(rates)
        return [
            Evidence(
                id=f"{prefix}-FUNDING",
                facet=Facet.POSITIONING,
                summary=(
                    f"{symbol} 最新資金費率 {latest:+.4%}，"
                    f"近 {len(rates)} 期均值 {average:+.4%}"
                ),
                stance_hint=funding_to_stance(average),
                excerpts=(
                    _excerpt(
                        f"{prefix}-FUNDING",
                        f"{url}?symbol={symbol}&limit={limit}",
                        f"近 {len(rates)} 期資金費率",
                        f"最新 {latest:+.6f}；均值 {average:+.6f}；"
                        f"正值代表多方付費給空方（市場淨多頭）",
                    ),
                ),
            )
        ]

    async def _open_interest(
        self, symbol: str, prefix: str, period: str, limit: int
    ) -> list[Evidence]:
        url = f"{FUTURES_BASE}/futures/data/openInterestHist"
        payload = await self._get(url, {"symbol": symbol, "period": period, "limit": limit})
        if not isinstance(payload, list) or len(payload) < 2:
            return []
        try:
            values = [float(row["sumOpenInterest"]) for row in payload]
        except (KeyError, TypeError, ValueError):
            return []

        change = open_interest_change(values)
        if change is None:
            return []
        return [
            Evidence(
                id=f"{prefix}-OI",
                facet=Facet.POSITIONING,
                summary=f"{symbol} 未平倉合約近 {len(values)} 期變化 {change:+.1%}",
                # 未平倉量增減代表資金進出的規模，方向要配合價格才有意義，
                # 因此本身不表態 —— 由推理層結合技術面證據判讀。
                stance_hint=0.0,
                excerpts=(
                    _excerpt(
                        f"{prefix}-OI",
                        f"{url}?symbol={symbol}&period={period}&limit={limit}",
                        f"近 {len(values)} 期未平倉合約",
                        f"起 {values[0]:.2f} → 迄 {values[-1]:.2f}；變化 {change:+.2%}",
                    ),
                ),
            )
        ]

    async def _long_short(
        self, symbol: str, prefix: str, period: str, limit: int
    ) -> list[Evidence]:
        url = f"{FUTURES_BASE}/futures/data/globalLongShortAccountRatio"
        payload = await self._get(url, {"symbol": symbol, "period": period, "limit": limit})
        if not isinstance(payload, list) or not payload:
            return []
        try:
            ratio = float(payload[-1]["longShortRatio"])
        except (IndexError, KeyError, TypeError, ValueError):
            return []

        return [
            Evidence(
                id=f"{prefix}-LSR",
                facet=Facet.POSITIONING,
                summary=f"{symbol} 全市場多空帳戶比 {ratio:.2f}",
                stance_hint=long_short_to_stance(ratio),
                excerpts=(
                    _excerpt(
                        f"{prefix}-LSR",
                        f"{url}?symbol={symbol}&period={period}&limit={limit}",
                        "最新一期多空帳戶比",
                        f"多空帳戶比 = {ratio:.4f}（1.0 為均衡）",
                    ),
                ),
            )
        ]


__all__ = ["BinanceDerivativesSource", "BinanceSpotSource", "symbol_for"]
