"""評估基準測試 —— 在接縫 2 上跑，用腳本模型與假證據源，不碰網路。

四項門檻（ticket 11）：多輪 tool calling 成功率、單次壁鐘時間、
引用忠實度、單次成本。前三項可從回合與軌跡確定性算出，因此可測。
"""

from __future__ import annotations

import pytest

from hoyabit_agent.domain import DraftClaim, Facet
from hoyabit_agent.evaluation import (
    CITATION_FAITHFULNESS_THRESHOLD,
    TOOL_CALLING_SUCCESS_THRESHOLD,
    WALL_CLOCK_THRESHOLD_SECONDS,
    EvalCase,
    evaluate,
)
from hoyabit_agent.testing import ManualClock, ScriptedModel, StaticSource, evidence


def source(name: str, *items: object) -> StaticSource:
    return StaticSource(list(items), name=name)  # type: ignore[arg-type]


def four_facet_sources() -> list[StaticSource]:
    return [
        source(
            "market",
            evidence("E1", Facet.TECHNICAL, 0.8),
            evidence("E2", Facet.POSITIONING, 0.7),
        ),
        source(
            "news",
            evidence("E3", Facet.FUNDAMENTAL, 0.6),
            evidence("E4", Facet.SENTIMENT, 0.5),
        ),
    ]


def good_model() -> ScriptedModel:
    return ScriptedModel(
        plans=[("market,news", "四面全缺，一次抓齊")],
        claims=[DraftClaim("四面一致偏多", ("E1", "E3"), Facet.TECHNICAL)],
    )


# --------------------------------------------------------------------------
# 成功率
# --------------------------------------------------------------------------


async def test_a_run_that_reaches_a_cited_report_counts_as_success() -> None:
    card = await evaluate(
        [EvalCase("BTC")], sources=four_facet_sources(), model=good_model()
    )
    assert card.tool_calling_success_rate == pytest.approx(1.0)


async def test_a_run_that_produces_no_kept_claim_is_not_a_success() -> None:
    model = ScriptedModel(
        plans=[("market,news", "抓資料")],
        claims=[DraftClaim("沒有掛證據的話", (), Facet.TECHNICAL)],  # 會被丟棄
    )
    card = await evaluate([EvalCase("BTC")], sources=four_facet_sources(), model=model)
    assert card.tool_calling_success_rate == pytest.approx(0.0)


async def test_repeating_an_identical_tool_call_fails_the_run() -> None:
    """同工具同參數呼叫兩次 = 在迴圈裡迷失，那不算成功。"""
    model = ScriptedModel(
        plans=[
            ("market", "先抓 market"),
            ("market", "又抓一次一模一樣的 market"),
        ],
        claims=[DraftClaim("偏多", ("E1",), Facet.TECHNICAL)],
        arguments={"market": {"interval": "1d"}},
    )
    card = await evaluate(
        [EvalCase("BTC")],
        sources=[source("market", evidence("E1", Facet.TECHNICAL, 0.8))],
        model=model,
    )
    assert card.tool_calling_success_rate == pytest.approx(0.0)


async def test_the_success_rate_is_averaged_across_cases() -> None:
    good = EvalCase("BTC")
    bad = EvalCase("ETH")
    # 兩個 case 共用一組會成功的資料源與模型腳本，但腳本只夠一次 —— 用兩個獨立跑
    card = await evaluate(
        [good, bad],
        sources_for=lambda _: four_facet_sources(),
        model_for=lambda _: good_model(),
    )
    assert card.tool_calling_success_rate == pytest.approx(1.0)
    assert card.cases_run == 2


async def test_a_rejected_asset_is_not_counted_as_a_failed_run() -> None:
    """幣種閘門拒絕不是模型的失敗 —— 那是系統正確地擋下來。"""
    card = await evaluate(
        [EvalCase("DOGE")], sources=four_facet_sources(), model=good_model()
    )
    assert card.cases_run == 0
    assert card.rejected == 1


# --------------------------------------------------------------------------
# 壁鐘時間
# --------------------------------------------------------------------------


async def test_wall_clock_is_measured_per_run() -> None:
    clock = ManualClock()
    slow = StaticSource(
        [evidence("E1", Facet.TECHNICAL, 0.8)], name="market", costs_seconds=42.0
    )
    card = await evaluate(
        [EvalCase("BTC")],
        sources=[slow],
        model=ScriptedModel(
            plans=[("market", "抓")],
            claims=[DraftClaim("偏多", ("E1",), Facet.TECHNICAL)],
        ),
        clock=clock,
    )
    assert card.max_wall_clock_seconds == pytest.approx(42.0)


async def test_wall_clock_within_the_budget_passes_its_threshold() -> None:
    card = await evaluate(
        [EvalCase("BTC")], sources=four_facet_sources(), model=good_model()
    )
    assert card.max_wall_clock_seconds <= WALL_CLOCK_THRESHOLD_SECONDS


# --------------------------------------------------------------------------
# 引用忠實度
# --------------------------------------------------------------------------


async def test_citation_faithfulness_uses_the_judge_when_given() -> None:
    """有 judge 時，忠實度是「judge 認為證據確實支撐該句」的比例。"""

    async def judge(claim_text: str, evidence_summaries: tuple[str, ...]) -> bool:
        return "偏多" in claim_text  # 只認可含「偏多」的判斷

    model = ScriptedModel(
        plans=[("market,news", "抓")],
        claims=[
            DraftClaim("四面一致偏多", ("E1",), Facet.TECHNICAL),
            DraftClaim("純屬臆測", ("E3",), Facet.FUNDAMENTAL),
        ],
    )
    card = await evaluate(
        [EvalCase("BTC")], sources=four_facet_sources(), model=model, judge=judge
    )
    assert card.citation_faithfulness == pytest.approx(0.5)


async def test_without_a_judge_faithfulness_is_reported_as_not_measured() -> None:
    """沒有 judge 就誠實說「未量測」，不假裝 100%。"""
    card = await evaluate(
        [EvalCase("BTC")], sources=four_facet_sources(), model=good_model()
    )
    assert card.citation_faithfulness is None


# --------------------------------------------------------------------------
# 成績單
# --------------------------------------------------------------------------


async def test_the_scorecard_flags_each_threshold() -> None:
    card = await evaluate(
        [EvalCase("BTC")], sources=four_facet_sources(), model=good_model()
    )
    text = card.to_markdown()
    assert "多輪 tool calling 成功率" in text
    assert "壁鐘時間" in text
    assert str(int(TOOL_CALLING_SUCCESS_THRESHOLD * 100)) in text


async def test_faithfulness_row_says_not_measured_when_no_judge() -> None:
    card = await evaluate(
        [EvalCase("BTC")], sources=four_facet_sources(), model=good_model()
    )
    assert "未量測" in card.to_markdown()


def test_thresholds_match_the_ticket() -> None:
    assert TOOL_CALLING_SUCCESS_THRESHOLD == 0.95
    assert WALL_CLOCK_THRESHOLD_SECONDS == 900.0
    assert CITATION_FAITHFULNESS_THRESHOLD == 0.90
