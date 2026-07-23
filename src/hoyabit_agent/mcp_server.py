"""把證據源以 MCP server 暴露出去。

這是 `ToolSpec` 的**第三個消費者**：同一份規格已經餵給了 Gemini 的
function declarations 與我們自己的執行器，這裡再餵給 MCP。三者共用
同一個來源，因此不可能出現「模型以為的介面」與「Kiro 看到的介面」不一致。

用途是把資料層掛到 Kiro / Claude Desktop，用自然語言直接查證
「BTC 現在的 RSS 新聞有哪些」「這個 RSI 是用哪段 K 線算的」——
Demo 現場即席證明資料層是真的，不是預錄的。

依 docs/research/0001，SDK 釘在 1.x：2026-07-28 規格是破壞性改版，
4 週內不碰 2.x。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from hoyabit_agent.domain import Asset, Evidence
from hoyabit_agent.seams import Arguments, EvidenceSource, JsonSchema, ToolSpec
from hoyabit_agent.tools import gate_asset

SERVER_NAME = "hoyabit-evidence"

ASSET_PROPERTY: JsonSchema = {
    "type": "string",
    "enum": [asset.value for asset in Asset],
    "description": "受涵蓋幣種。其餘一律拒絕 —— 系統只分析這五種資產。",
}


def to_mcp_schema(spec: ToolSpec) -> dict[str, object]:
    """把 `ToolSpec` 轉成 MCP 的 inputSchema。

    分析回合裡幣種是分開傳的（每個回合固定一個標的），但 MCP 客戶端
    是逐次呼叫、沒有回合的概念，所以這裡必須把 `asset` 補進參數 ——
    這是兩種呼叫情境的真實差異，不是轉換的疏漏。
    """
    parameters = dict(spec.parameters)
    properties = dict(parameters.get("properties", {}) or {})
    properties["asset"] = ASSET_PROPERTY

    required = list(parameters.get("required", []) or [])
    if "asset" not in required:
        required.append("asset")

    return {**parameters, "type": "object", "properties": properties, "required": required}


def split_arguments(arguments: Arguments) -> tuple[Asset | None, Arguments]:
    """把 MCP 的參數拆成「幣種」與「要轉給資料源的其餘參數」。

    幣種閘門在這裡是**第二層**：SDK 會先依 inputSchema 的 enum 擋一次。
    但第二層必須自己站得住 —— 不能假設外層一定會先擋，
    否則換一個不驗證 schema 的客戶端，白名單就形同虛設。
    """
    asset = gate_asset(str(arguments.get("asset", "")))
    rest = {key: value for key, value in arguments.items() if key != "asset"}
    return asset, rest


def render_evidence(items: Sequence[Evidence]) -> str:
    """把證據轉成給人／給模型看的 JSON。

    **完整保留來源片段** —— MCP 客戶端拿到的東西必須和分析回合內部
    看到的一樣可溯源，否則「用 Kiro 查證資料層」就查不到真正重要的部分。
    """
    return json.dumps(
        [
            {
                "id": item.id,
                "facet": item.facet.value,
                "summary": item.summary,
                "stance_hint": item.stance_hint,
                "event_key": item.event_key,
                "excerpts": [
                    {
                        "text": excerpt.text,
                        "url": excerpt.url,
                        "locator": excerpt.locator,
                        "retrieved_at": excerpt.retrieved_at.isoformat(),
                    }
                    for excerpt in item.excerpts
                ],
            }
            for item in items
        ],
        ensure_ascii=False,
        indent=2,
    )


def build_server(sources: Sequence[EvidenceSource]) -> Server[object, object]:
    """組出一個暴露這些證據源的 MCP server。

    `sources` 由外部注入，因此測試可以塞假證據源，
    不必啟動真正的 stdio 傳輸。
    """
    registry = {source.spec.name: source for source in sources}
    server: Server[object, object] = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=source.spec.name,
                description=source.spec.description,
                inputSchema=to_mcp_schema(source.spec),
            )
            for source in registry.values()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: Arguments) -> list[types.ContentBlock]:
        source = registry.get(name)
        if source is None:
            return [types.TextContent(type="text", text=f"沒有名為 {name} 的工具")]

        asset, rest = split_arguments(arguments)
        if asset is None:
            allowed = "、".join(item.value for item in Asset)
            return [
                types.TextContent(
                    type="text",
                    text=f"{arguments.get('asset')!r} 不在受涵蓋幣種內。可用：{allowed}",
                )
            ]

        found = await source.fetch(asset, rest)
        if not found:
            return [
                types.TextContent(
                    type="text",
                    text=f"{name} 這次沒有回傳證據（來源暫時不可用，或該期間沒有相符資料）。",
                )
            ]
        return [types.TextContent(type="text", text=render_evidence(found))]

    return server


async def serve(sources: Sequence[EvidenceSource]) -> None:  # pragma: no cover - I/O 迴圈
    """以 stdio 傳輸執行 —— Kiro 與 Claude Desktop 都用這個。"""
    server = build_server(sources)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


async def _serve_live() -> None:  # pragma: no cover - I/O 迴圈
    """以真實證據源啟動。與分析回合共用同一份實作，不會出現兩套邏輯。"""
    import httpx

    from hoyabit_agent.models.gemini import GeminiProvider
    from hoyabit_agent.sources.binance import BinanceDerivativesSource, BinanceSpotSource
    from hoyabit_agent.sources.news import NewsRssSource

    async with httpx.AsyncClient(
        timeout=30.0,
        headers={"user-agent": "hoyabit-agent/0.1 (mcp)"},
        follow_redirects=True,
    ) as client:
        await serve(
            [
                BinanceSpotSource(client),
                BinanceDerivativesSource(client),
                NewsRssSource(client, labeller=GeminiProvider.from_environment(client)),
            ]
        )


def main() -> int:  # pragma: no cover - 進入點
    """`hoyabit-mcp` 的進入點，供 Kiro / Claude Desktop 以 stdio 啟動。"""
    import asyncio

    asyncio.run(_serve_live())
    return 0


__all__ = [
    "ASSET_PROPERTY",
    "SERVER_NAME",
    "build_server",
    "main",
    "render_evidence",
    "serve",
    "split_arguments",
    "to_mcp_schema",
]
