"""MCP server 測試 —— 直接驅動處理器，不啟動 stdio 傳輸。

重點：暴露出去的介面必須與模型看到的、執行器用的**來自同一份 ToolSpec**，
而且幣種閘門不會因為換了呼叫路徑就被繞過。
"""

from __future__ import annotations

import json

import mcp.types as types
import pytest
from mcp.server import Server

from hoyabit_agent.domain import Asset, Facet
from hoyabit_agent.mcp_server import (
    SERVER_NAME,
    build_server,
    render_evidence,
    split_arguments,
    to_mcp_schema,
)
from hoyabit_agent.seams import ToolSpec
from hoyabit_agent.testing import StaticSource, evidence


def source() -> StaticSource:
    return StaticSource(
        [evidence("E1", Facet.TECHNICAL, 0.8, text="收盤站上季線")],
        name="binance_spot",
    )


async def list_tools(server: Server[object, object]) -> list[types.Tool]:
    handler = server.request_handlers[types.ListToolsRequest]
    request = types.ListToolsRequest(method="tools/list")
    result = await handler(request)
    assert isinstance(result.root, types.ListToolsResult)
    return result.root.content if hasattr(result.root, "content") else result.root.tools


async def call(
    server: Server[object, object], name: str, arguments: dict[str, object]
) -> types.CallToolResult:
    handler = server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = await handler(request)
    assert isinstance(result.root, types.CallToolResult)
    return result.root


def text_of(result: types.CallToolResult) -> str:
    block = result.content[0]
    assert isinstance(block, types.TextContent)
    return block.text


# --------------------------------------------------------------------------
# 規格轉換：一份 ToolSpec，第三個消費者
# --------------------------------------------------------------------------


def test_the_mcp_schema_keeps_everything_the_tool_spec_declared() -> None:
    spec = ToolSpec(
        name="binance_spot",
        description="取得現貨 K 線",
        parameters={
            "type": "object",
            "properties": {"interval": {"type": "string", "enum": ["1d"]}},
        },
    )
    schema = to_mcp_schema(spec)
    assert schema["properties"]["interval"] == {"type": "string", "enum": ["1d"]}  # type: ignore[index]


def test_the_mcp_schema_adds_the_asset_because_there_is_no_run_to_carry_it() -> None:
    """分析回合裡幣種是分開傳的；MCP 客戶端沒有回合的概念，所以要補進參數。"""
    schema = to_mcp_schema(source().spec)
    assert "asset" in schema["properties"]  # type: ignore[operator]
    assert "asset" in schema["required"]  # type: ignore[operator]


def test_the_asset_property_enumerates_exactly_the_covered_assets() -> None:
    schema = to_mcp_schema(source().spec)
    listed = schema["properties"]["asset"]["enum"]  # type: ignore[index]
    assert set(listed) == {item.value for item in Asset}


def test_an_existing_required_list_is_preserved() -> None:
    spec = ToolSpec(
        name="t",
        description="d",
        parameters={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
    )
    required = to_mcp_schema(spec)["required"]
    assert isinstance(required, list)
    assert set(required) == {"x", "asset"}


# --------------------------------------------------------------------------
# 工具清單
# --------------------------------------------------------------------------


async def test_every_source_is_exposed_as_a_tool() -> None:
    server = build_server(
        [source(), StaticSource([], name="crypto_news")],
    )
    tools = await list_tools(server)
    assert {tool.name for tool in tools} == {"binance_spot", "crypto_news"}


async def test_the_exposed_description_comes_from_the_same_tool_spec() -> None:
    evidence_source = source()
    tools = await list_tools(build_server([evidence_source]))
    assert tools[0].description == evidence_source.spec.description


async def test_the_server_has_a_stable_name() -> None:
    assert SERVER_NAME == "hoyabit-evidence"


# --------------------------------------------------------------------------
# 呼叫
# --------------------------------------------------------------------------


async def test_calling_a_tool_returns_evidence_with_full_provenance() -> None:
    result = await call(build_server([source()]), "binance_spot", {"asset": "BTC"})
    payload = json.loads(text_of(result))

    assert payload[0]["id"] == "E1"
    assert payload[0]["facet"] == "technical"
    assert payload[0]["excerpts"][0]["text"] == "收盤站上季線"
    assert payload[0]["excerpts"][0]["url"]
    assert payload[0]["excerpts"][0]["retrieved_at"]


async def test_arguments_other_than_the_asset_reach_the_source() -> None:
    evidence_source = source()
    await call(
        build_server([evidence_source]),
        "binance_spot",
        {"asset": "BTC", "interval": "4h", "limit": 120},
    )
    assert evidence_source.received[0] == {"interval": "4h", "limit": 120}


async def test_the_asset_is_not_passed_through_as_a_tool_argument() -> None:
    """幣種在介面上是分開的概念，不該混進資料源的參數裡。"""
    evidence_source = source()
    await call(build_server([evidence_source]), "binance_spot", {"asset": "BTC"})
    assert "asset" not in evidence_source.received[0]


@pytest.mark.parametrize("raw", ["DOGE", "PEPE", "", "not-a-coin"])
async def test_the_asset_gate_is_not_bypassed_by_going_through_mcp(raw: str) -> None:
    """MCP 客戶端不是繞過幣種閘門的後門。

    這裡有兩層防護：schema 的 enum（SDK 依 inputSchema 驗證，擋在最外面）
    與處理器裡的 `gate_asset`。斷言只看「被擋下且資料源沒被呼叫」，
    不綁哪一層擋的、也不綁錯誤訊息的措辭。
    """
    evidence_source = source()
    result = await call(build_server([evidence_source]), "binance_spot", {"asset": raw})

    assert result.isError is True
    assert evidence_source.received == []  # 根本沒去打資料源


async def test_a_missing_asset_is_rejected_rather_than_defaulting_to_bitcoin() -> None:
    evidence_source = source()
    result = await call(build_server([evidence_source]), "binance_spot", {})

    assert result.isError is True
    assert evidence_source.received == []


@pytest.mark.parametrize("raw", [{"asset": "DOGE"}, {"asset": ""}, {}, {"asset": 5}])
def test_the_second_gate_stands_on_its_own(raw: dict[str, object]) -> None:
    """第二層必須自己站得住 —— 換一個不驗證 schema 的客戶端，白名單不能形同虛設。"""
    asset, _ = split_arguments(raw)
    assert asset is None


def test_the_second_gate_lets_covered_assets_through() -> None:
    asset, rest = split_arguments({"asset": "eth", "interval": "4h"})
    assert asset is Asset.ETH
    assert rest == {"interval": "4h"}


async def test_an_unknown_tool_name_is_reported_not_crashed() -> None:
    result = await call(build_server([source()]), "no_such_tool", {"asset": "BTC"})
    assert "沒有名為 no_such_tool 的工具" in text_of(result)


async def test_an_empty_result_says_so_instead_of_returning_empty_json() -> None:
    """空 JSON 陣列讀起來像是「查到了但沒東西」，那是兩件不同的事。"""
    result = await call(
        build_server([StaticSource([], name="binance_spot")]),
        "binance_spot",
        {"asset": "BTC"},
    )
    assert "沒有回傳證據" in text_of(result)


async def test_a_failing_source_does_not_take_the_server_down() -> None:
    broken = StaticSource([], name="binance_spot", raises=RuntimeError("upstream 500"))
    result = await call(build_server([broken]), "binance_spot", {"asset": "BTC"})
    assert result.isError is True


# --------------------------------------------------------------------------
# 呈現
# --------------------------------------------------------------------------


def test_rendered_evidence_preserves_every_excerpt() -> None:
    """用 Kiro 查證資料層時，最重要的就是能看到原文與出處。"""
    payload = json.loads(render_evidence([evidence("E1", Facet.SENTIMENT, -0.5)]))
    assert payload[0]["excerpts"]
    assert payload[0]["stance_hint"] == -0.5


def test_rendered_evidence_is_readable_chinese_not_escaped_bytes() -> None:
    rendered = render_evidence([evidence("E1", Facet.TECHNICAL, 0.5, text="收盤站上季線")])
    assert "收盤站上季線" in rendered


def test_rendering_nothing_yields_an_empty_list() -> None:
    assert json.loads(render_evidence([])) == []
