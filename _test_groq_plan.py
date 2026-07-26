"""Test Groq plan() with real tool schema."""
import asyncio
import json
import httpx
from hoyabit_agent.config import load_dotenv
from hoyabit_agent.models.groq import GroqProvider, _to_openai_tool
from hoyabit_agent.sources.binance import BinanceSpotSource
from hoyabit_agent.seams import GatherContext
from hoyabit_agent.domain import Asset, Facet

load_dotenv()

async def test():
    async with httpx.AsyncClient(timeout=30.0) as client:
        src = BinanceSpotSource(client)
        tool = _to_openai_tool(src.spec)
        print("=== Tool Schema (what we send to Groq) ===")
        print(json.dumps(tool, indent=2, ensure_ascii=False))
        print()

        provider = GroqProvider.from_environment(client)
        if not provider:
            print("❌ No Groq provider (check GROQ_API_KEY)")
            return

        context = GatherContext(
            asset=Asset.BTC,
            gap=frozenset({Facet.TECHNICAL, Facet.POSITIONING}),
            evidence=(),
            attempts=(),
            question="BTC 近期走勢如何",
        )
        print("=== Calling Groq plan() ===")
        result = await provider.plan(context, (src.spec,))
        print(f"Reason: {result.reason[:200]}")
        print(f"Invocations: {len(result.invocations)}")
        for inv in result.invocations:
            print(f"  ✅ {inv.tool}({inv.arguments})")

        if not result.invocations:
            print("⚠️ No tool calls — model returned text-only response")

asyncio.run(test())
