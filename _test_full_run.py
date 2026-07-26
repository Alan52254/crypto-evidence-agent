"""End-to-end test: run analyse() with Groq and see where it fails."""
import asyncio
import logging
import httpx
from hoyabit_agent.config import load_dotenv
from hoyabit_agent.domain import AnalysisRequest
from hoyabit_agent.run import analyse
from hoyabit_agent.models.groq import GroqProvider
from hoyabit_agent.sources.binance import BinanceSpotSource, BinanceDerivativesSource
from hoyabit_agent.sources.news import NewsRssSource

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

async def main():
    async with httpx.AsyncClient(timeout=90.0) as client:
        model = GroqProvider.from_environment(client)
        if model is None:
            print("❌ No Groq provider")
            return

        # Use minimal sources for fast testing
        sources = [
            BinanceSpotSource(client),
            BinanceDerivativesSource(client),
            NewsRssSource(client, labeller=model),
        ]

        request = AnalysisRequest("BTC", "BTC 近期走勢如何")
        print("=== Starting analyse() ===")
        outcome = await analyse(request, sources, model, max_iterations=3)

        print(f"\n=== Result ===")
        print(f"Run ID: {outcome.run_id}")
        print(f"Rejected: {outcome.rejection}")
        if outcome.report:
            print(f"Stance: {outcome.report.stance.value}")
            print(f"Claims: {len(outcome.report.claims)}")
            print(f"Evidence: {len(outcome.report.evidence)}")
            for c in outcome.report.claims[:3]:
                print(f"  [{c.role.value}] {c.text[:80]}")
        else:
            print("❌ No report generated")
        print(f"Trace nodes: {len(outcome.trace.nodes)}")
        for node in outcome.trace.nodes:
            print(f"  [{node.kind.value}] {node.reason[:80]}")

asyncio.run(main())
