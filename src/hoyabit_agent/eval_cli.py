"""`hoyabit-eval` —— 跑評估基準並印出成績單。

預設用離線的假資料源與腳本模型（快、可重現），驗證量測管線本身；
`--live` 則對五個受涵蓋幣種各跑一次真實分析，量測真實表現。
"""

from __future__ import annotations

import argparse
import sys

import httpx

from hoyabit_agent.cli import _pick_model
from hoyabit_agent.config import run_async
from hoyabit_agent.domain import Asset, DraftClaim, Facet
from hoyabit_agent.evaluation import EvalCase, Scorecard, evaluate
from hoyabit_agent.seams import ModelProvider, Sources
from hoyabit_agent.testing import ScriptedModel, StaticSource, evidence

COVERED = tuple(asset.value for asset in Asset)


def _demo_sources() -> Sources:
    return [
        StaticSource(
            [
                evidence("D-TECH", Facet.TECHNICAL, 0.6),
                evidence("D-POS", Facet.POSITIONING, 0.3),
            ],
            name="market",
        ),
        StaticSource(
            [
                evidence("D-FUND", Facet.FUNDAMENTAL, 0.4),
                evidence("D-SENT", Facet.SENTIMENT, -0.5),
            ],
            name="news",
        ),
    ]


def _demo_model() -> ModelProvider:
    return ScriptedModel(
        plans=[("market,news", "四面全缺，一次抓齊")],
        claims=[DraftClaim("四面偏多但情緒轉弱", ("D-TECH", "D-FUND"), Facet.TECHNICAL)],
    )


async def _run_offline() -> Scorecard:
    return await evaluate(
        [EvalCase(asset) for asset in COVERED],
        sources_for=lambda _: _demo_sources(),
        model_for=lambda _: _demo_model(),
    )


async def _run_live() -> Scorecard:
    """以 Gemini 與競賽 OHLCV source 評估五個受涵蓋幣種。"""
    from hoyabit_agent.config import load_dotenv
    from hoyabit_agent.ingest.runtime import build_competition_sources

    load_dotenv()
    async with httpx.AsyncClient(timeout=90.0) as client:
        model, description = _pick_model(client)
        if model is None:
            raise RuntimeError("缺少 GEMINI_API_KEY，正式評估不提供非 Gemini fallback")
        sources: Sources = await build_competition_sources(client, model)
        print(f"（推理層：{description}）\n")
        return await evaluate(
            [EvalCase(asset) for asset in COVERED], sources=sources, model_for=lambda _: model
        )


async def _run(live: bool) -> int:
    card = await (_run_live() if live else _run_offline())
    print(card.to_markdown())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="跑評估基準並印出成績單")
    parser.add_argument("--live", action="store_true", help="對五大幣種各跑一次真實分析（免金鑰）")
    args = parser.parse_args(argv)
    return int(run_async(_run(args.live)))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
