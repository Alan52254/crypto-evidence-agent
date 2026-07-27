"""開發用啟動腳本 — 不需要 PostgreSQL，用記憶體 store。

用法：
    .venv\Scripts\python.exe dev_server.py

需要 .env 檔中設定 GEMINI_API_KEY 或 GROQ_API_KEY。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

import httpx
import uvicorn

from hoyabit_agent.config import load_dotenv
from hoyabit_agent.domain import AnalysisOutcome, AnalysisRequest, TraceNode
from hoyabit_agent.ingest.runtime import build_competition_sources
from hoyabit_agent.models.factory import create_model_provider
from hoyabit_agent.run import analyse
from hoyabit_agent.testing import InMemoryAnalysisStore
from hoyabit_agent.viz.server import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


async def main() -> None:
    load_dotenv()

    store = InMemoryAnalysisStore()

    async def dev_runner(
        request: AnalysisRequest,
        run_id: str,
        on_trace: Callable[[TraceNode], None],
    ) -> AnalysisOutcome:
        async with httpx.AsyncClient(timeout=90.0) as client:
            model = await create_model_provider(client)
            if model is None:
                raise RuntimeError(
                    "No model provider configured. "
                    "Set GEMINI_API_KEY or GROQ_API_KEY in .env"
                )
            sources = await build_competition_sources(client, model)
            return await analyse(
                request, sources, model, run_id=run_id, on_trace=on_trace
            )

    app = create_app(store, dev_runner)

    config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
