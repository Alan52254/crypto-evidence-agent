"""Binance 證據源測試 —— 以 httpx MockTransport 攔截，完全不碰網路。

重點不在「HTTP 有沒有打通」，而在接縫 1 的不變式有沒有被遵守：
每項證據都帶完整來源片段、失效一律回空集合、模型亂給參數不會炸。
"""

from __future__ import annotations

import httpx
import pytest

from hoyabit_agent.domain import Asset, Facet
from hoyabit_agent.sources.binance import (
    WEIGHT_HEADER,
    BinanceDerivativesSource,
    BinanceSpotSource,
    symbol_for,
)


def klines_payload(count: int = 250) -> list[list[object]]:
    rows: list[list[object]] = []
    price = 100.0
    for index in range(count):
        price += 1.0  # 穩定上漲，讓斷言可預測
        rows.append([index * 86_400_000, price - 1, price + 1, price - 2, price, 10.0 + index])
    return rows


def transport_returning(
    routes: dict[str, object],
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncBaseTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        for fragment, payload in routes.items():
            if fragment in request.url.path:
                return httpx.Response(status, json=payload, headers=headers)
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def transport_raising(error: Exception) -> httpx.AsyncBaseTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return httpx.MockTransport(handler)


def client_for(
    routes: dict[str, object],
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=transport_returning(routes, status=status, headers=headers))


# --------------------------------------------------------------------------
# 現貨 → 技術面
# --------------------------------------------------------------------------


async def test_spot_produces_technical_evidence_with_full_provenance() -> None:
    async with client_for({"/api/v3/klines": klines_payload()}) as client:
        found = await BinanceSpotSource(client).fetch(Asset.BTC, {"interval": "1d", "limit": 250})

    assert found
    assert all(item.facet is Facet.TECHNICAL for item in found)
    for item in found:
        assert item.excerpts  # 溯源路徑是不變式
        assert item.excerpts[0].url.startswith("https://api.binance.com")
        assert item.excerpts[0].text  # 算式說明必須在


async def test_spot_evidence_ids_are_stable_across_calls() -> None:
    """證據識別碼必須穩定 —— 否則判斷掛載的引用會在重跑後失效。"""
    async with client_for({"/api/v3/klines": klines_payload()}) as client:
        source = BinanceSpotSource(client)
        first = await source.fetch(Asset.BTC, {"interval": "1d"})
        second = await source.fetch(Asset.BTC, {"interval": "1d"})

    assert [item.id for item in first] == [item.id for item in second]


async def test_a_rising_market_reads_bullish_against_its_moving_average() -> None:
    async with client_for({"/api/v3/klines": klines_payload()}) as client:
        found = await BinanceSpotSource(client).fetch(Asset.BTC, {})

    sma_evidence = [item for item in found if "SMA" in item.id]
    assert sma_evidence
    assert all(item.stance_hint > 0 for item in sma_evidence)


async def test_volume_evidence_carries_no_direction() -> None:
    """量能是強度不是傾向 —— 它不該偷偷替某一方投票。"""
    async with client_for({"/api/v3/klines": klines_payload()}) as client:
        found = await BinanceSpotSource(client).fetch(Asset.BTC, {})

    volume = next(item for item in found if item.id.endswith("-VOL"))
    assert volume.stance_hint == 0.0


async def test_short_history_yields_only_the_indicators_it_can_actually_compute() -> None:
    async with client_for({"/api/v3/klines": klines_payload(count=70)}) as client:
        found = await BinanceSpotSource(client).fetch(Asset.BTC, {"limit": 70})

    ids = {item.id for item in found}
    assert any("SMA60" in i for i in ids)
    assert not any("SMA200" in i for i in ids)  # 資料不足就不生這項證據


# --------------------------------------------------------------------------
# 現貨盤口 → 籌碼面（證據面與資料源正交）
# --------------------------------------------------------------------------


def depth_payload(bid_qty: float = 100.0, ask_qty: float = 100.0) -> dict[str, object]:
    return {
        "bids": [["99.0", str(bid_qty / 2)], ["98.0", str(bid_qty / 2)]],
        "asks": [["101.0", str(ask_qty / 2)], ["102.0", str(ask_qty / 2)]],
    }


def spot_routes(**depth_kwargs: float) -> dict[str, object]:
    return {
        "/api/v3/klines": klines_payload(),
        "/api/v3/depth": depth_payload(**depth_kwargs),
    }


async def test_one_source_produces_two_different_facets() -> None:
    """證據面與資料源正交 —— 同一個來源可以同時給技術面與籌碼面。"""
    async with client_for(spot_routes()) as client:
        found = await BinanceSpotSource(client).fetch(Asset.BTC, {})

    assert {item.facet for item in found} == {Facet.TECHNICAL, Facet.POSITIONING}


async def test_a_heavier_bid_side_reads_bullish() -> None:
    async with client_for(spot_routes(bid_qty=200.0, ask_qty=100.0)) as client:
        found = await BinanceSpotSource(client).fetch(Asset.BTC, {})

    book = next(item for item in found if item.id.endswith("-BOOK"))
    assert book.stance_hint > 0


async def test_a_heavier_ask_side_reads_bearish() -> None:
    async with client_for(spot_routes(bid_qty=100.0, ask_qty=200.0)) as client:
        found = await BinanceSpotSource(client).fetch(Asset.BTC, {})

    book = next(item for item in found if item.id.endswith("-BOOK"))
    assert book.stance_hint < 0


async def test_a_balanced_book_is_neutral() -> None:
    async with client_for(spot_routes()) as client:
        found = await BinanceSpotSource(client).fetch(Asset.BTC, {})

    book = next(item for item in found if item.id.endswith("-BOOK"))
    assert book.stance_hint == pytest.approx(0.0)


async def test_spread_evidence_carries_no_direction() -> None:
    """價差是流動性（強度），不是方向。"""
    async with client_for(spot_routes()) as client:
        found = await BinanceSpotSource(client).fetch(Asset.BTC, {})

    spread = next(item for item in found if item.id.endswith("-SPREAD"))
    assert spread.stance_hint == 0.0


async def test_book_evidence_carries_full_provenance() -> None:
    async with client_for(spot_routes()) as client:
        found = await BinanceSpotSource(client).fetch(Asset.BTC, {})

    for item in found:
        assert item.excerpts
        assert item.excerpts[0].url.startswith("https://api.binance.com")
        assert item.excerpts[0].text


async def test_a_broken_order_book_does_not_lose_the_klines() -> None:
    """部分失效只損失那一面的證據，不是整個來源。"""
    routes = spot_routes()
    routes["/api/v3/depth"] = {"bids": "not a list"}

    async with client_for(routes) as client:
        found = await BinanceSpotSource(client).fetch(Asset.BTC, {})

    assert found
    assert {item.facet for item in found} == {Facet.TECHNICAL}


async def test_an_empty_order_book_yields_no_positioning_evidence() -> None:
    routes = spot_routes()
    routes["/api/v3/depth"] = {"bids": [], "asks": []}

    async with client_for(routes) as client:
        found = await BinanceSpotSource(client).fetch(Asset.BTC, {})

    assert not [item for item in found if item.facet is Facet.POSITIONING]


# --------------------------------------------------------------------------
# 接縫 1 的不變式：失效一律回空集合
# --------------------------------------------------------------------------


async def test_an_http_error_yields_an_empty_set_not_an_exception() -> None:
    async with client_for({"/api/v3/klines": {}}, status=500) as client:
        assert await BinanceSpotSource(client).fetch(Asset.BTC, {}) == ()


async def test_a_network_error_yields_an_empty_set_not_an_exception() -> None:
    transport = transport_raising(httpx.ConnectError("temporary DNS failure"))
    async with httpx.AsyncClient(transport=transport) as client:
        assert await BinanceSpotSource(client).fetch(Asset.BTC, {}) == ()


async def test_an_invalid_json_response_yields_an_empty_set() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await BinanceSpotSource(client).fetch(Asset.BTC, {}) == ()


async def test_a_malformed_payload_yields_an_empty_set() -> None:
    async with client_for({"/api/v3/klines": [["not", "a", "kline"]]}) as client:
        assert await BinanceSpotSource(client).fetch(Asset.BTC, {}) == ()


async def test_an_empty_payload_yields_an_empty_set() -> None:
    async with client_for({"/api/v3/klines": []}) as client:
        assert await BinanceSpotSource(client).fetch(Asset.BTC, {}) == ()


async def test_a_valid_payload_is_not_discarded_just_because_weight_is_high() -> None:
    """weight 已經花掉了，把有效資料丟掉是純粹的浪費。"""
    async with client_for(
        {"/api/v3/klines": klines_payload()},
        headers={WEIGHT_HEADER: "1200"},
    ) as client:
        found = await BinanceSpotSource(client).fetch(Asset.BTC, {})

    assert found  # 這一次的資料照收


async def test_once_the_ceiling_is_reached_no_further_requests_are_sent() -> None:
    """退避必須在**發送前**判斷 —— 收到回應才判斷等於沒有退避。

    被封 IP 的代價遠高於少拿一批證據。
    """
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(
            200, json=klines_payload(), headers={WEIGHT_HEADER: "1200"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = BinanceSpotSource(client)
        await source.fetch(Asset.BTC, {})  # 這一輪把 weight 打到上限
        before = len(sent)
        assert await source.fetch(Asset.BTC, {}) == ()  # 下一輪整個不送

    assert len(sent) == before
    assert source.used_weight == 1200


async def test_weight_stays_at_zero_when_the_header_is_absent() -> None:
    async with client_for({"/api/v3/klines": klines_payload()}) as client:
        source = BinanceSpotSource(client)
        await source.fetch(Asset.BTC, {})
        assert source.used_weight == 0


async def test_an_unparseable_weight_header_is_ignored_rather_than_crashing() -> None:
    async with client_for(
        {"/api/v3/klines": klines_payload()}, headers={WEIGHT_HEADER: "not-a-number"}
    ) as client:
        source = BinanceSpotSource(client)
        assert await source.fetch(Asset.BTC, {})
        assert source.used_weight == 0


# --------------------------------------------------------------------------
# 模型給的參數：亂給也不能炸
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"interval": "沒有這個週期"},
        {"interval": None, "limit": "abc"},
        {"limit": -5},
        {"limit": 999_999},
        {"完全不相干的鍵": True},
    ],
)
async def test_nonsense_arguments_degrade_to_defaults_instead_of_raising(
    arguments: dict[str, object],
) -> None:
    """模型會給出不在 schema 內的東西 —— 那是預期情況，不是錯誤。"""
    async with client_for({"/api/v3/klines": klines_payload()}) as client:
        found = await BinanceSpotSource(client).fetch(Asset.BTC, arguments)
    assert found


# --------------------------------------------------------------------------
# 合約 → 籌碼面
# --------------------------------------------------------------------------


def derivatives_routes() -> dict[str, object]:
    return {
        "/fapi/v1/fundingRate": [{"fundingRate": "0.00005"} for _ in range(30)],
        "/futures/data/openInterestHist": [
            {"sumOpenInterest": str(100.0 + index)} for index in range(30)
        ],
        "/futures/data/globalLongShortAccountRatio": [{"longShortRatio": "1.25"}],
    }


async def test_derivatives_produce_positioning_evidence() -> None:
    async with client_for(derivatives_routes()) as client:
        found = await BinanceDerivativesSource(client).fetch(Asset.BTC, {"period": "1d"})

    assert {item.facet for item in found} == {Facet.POSITIONING}
    assert {item.id.split("-")[-1] for item in found} == {"FUNDING", "OI", "LSR"}
    for item in found:
        assert item.excerpts and item.excerpts[0].text


async def test_positive_funding_and_more_longs_read_bullish() -> None:
    async with client_for(derivatives_routes()) as client:
        found = await BinanceDerivativesSource(client).fetch(Asset.BTC, {})

    by_kind = {item.id.split("-")[-1]: item for item in found}
    assert by_kind["FUNDING"].stance_hint == pytest.approx(0.5)
    assert by_kind["LSR"].stance_hint == pytest.approx(0.5)


async def test_open_interest_alone_carries_no_direction() -> None:
    """未平倉量的增減要配合價格才有意義，本身不表態。"""
    async with client_for(derivatives_routes()) as client:
        found = await BinanceDerivativesSource(client).fetch(Asset.BTC, {})

    open_interest = next(item for item in found if item.id.endswith("-OI"))
    assert open_interest.stance_hint == 0.0


async def test_one_broken_endpoint_does_not_lose_the_other_two() -> None:
    """部分失效只損失那一項證據，不是整個籌碼面。"""
    routes = derivatives_routes()
    routes["/fapi/v1/fundingRate"] = {"bad": "shape"}

    async with client_for(routes) as client:
        found = await BinanceDerivativesSource(client).fetch(Asset.BTC, {})

    kinds = {item.id.split("-")[-1] for item in found}
    assert kinds == {"OI", "LSR"}


async def test_one_endpoint_raising_does_not_lose_the_other_two() -> None:
    """單一 Binance 端點連線失敗時，其他可用端點仍要留下籌碼面證據。"""
    routes = derivatives_routes()

    def handler(request: httpx.Request) -> httpx.Response:
        if "/fapi/v1/fundingRate" in request.url.path:
            raise httpx.ReadTimeout("funding endpoint timed out")
        for fragment, payload in routes.items():
            if fragment in request.url.path:
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        found = await BinanceDerivativesSource(client).fetch(Asset.BTC, {})

    kinds = {item.id.split("-")[-1] for item in found}
    assert kinds == {"OI", "LSR"}


async def test_every_endpoint_failing_yields_an_empty_set() -> None:
    async with client_for({}, status=503) as client:
        assert await BinanceDerivativesSource(client).fetch(Asset.BTC, {}) == ()


def test_symbols_are_quoted_in_usdt() -> None:
    assert symbol_for(Asset.BTC) == "BTCUSDT"
    assert symbol_for(Asset.XRP) == "XRPUSDT"
