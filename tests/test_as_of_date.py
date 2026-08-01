"""分析截止日 (As-of Date) 與分析模式 (Analysis Regime) —— 純資料與純函數。

as_of_date 是分析請求的一等公民,也是全系統的時間立足點;分析模式由它推導,
不讀現實時鐘。測試名稱描述使用者可觀察的行為:請求帶不帶截止日、
以及某個截止日在某個「今天」之下屬於哪一種模式。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from hoyabit_agent.domain import (
    AnalysisRegime,
    AnalysisRequest,
    Asset,
    Confidence,
    Evidence,
    Facet,
    SourceExcerpt,
    analysis_regime,
)
from hoyabit_agent.question import QuestionType, derive_requirement
from hoyabit_agent.tools import assess_confidence

DATASET_CUTOFF = date(2026, 5, 31)


def test_recognized_market_summary_question_has_no_unmatched_boundary_note() -> None:
    """典型的現況研判問題(命中已知市場摘要詞彙)不該被標「題型未明確匹配」。

    否則這個限制會出現在每一次最普通的分析請求上,稀釋掉它對真正
    未知題型的警示價值。
    """
    requirement = derive_requirement("BTC 現況如何", (Asset.BTC,))

    assert "題型未明確匹配，以現況研判作答" not in requirement.boundary_notes


def test_unrecognized_phrasing_gets_unmatched_boundary_note() -> None:
    """不屬於比較、假設驗證、也不像典型市場摘要問法的題目,必須誠實標記。

    這把「不知道主辦方會問什麼」從風險轉成防護網:任何未預期的題型
    都安全落地為市場摘要,並明確告知評審這是預設路徑,而非精準分類。
    """
    requirement = derive_requirement("BTC 適合當退休金配置嗎", (Asset.BTC,))

    assert "題型未明確匹配，以現況研判作答" in requirement.boundary_notes


def test_forecast_question_gets_forecast_boundary_note() -> None:
    """預測未來走勢的題目,必須明確聲明系統不做未來價格預測。

    系統的可溯源承諾要求每句判斷掛得到來源片段;未來沒有片段可掛,
    因此誠實劃界比硬答一個「下週目標價」更專業。
    """
    requirement = derive_requirement("請預測 BTC 下週的走勢", (Asset.BTC,))

    assert requirement.question_type is QuestionType.MARKET_SUMMARY
    assert "本系統輸出當前方向研判，不做未來價格預測" in requirement.boundary_notes


def test_asserted_premise_question_gets_verification_boundary_note() -> None:
    """題目把未證實的負面事件當既定事實時,必須明確聲明尚待查證。

    「SOL 已經停止運作」不該被系統默默接受為前提往下分析 —— 好的 Agent
    要先標記這個斷言未經查證,而不是順著錯誤前提推論出一份煞有其事的報告。
    """
    requirement = derive_requirement("SOL 已經停止運作，分析一下影響", (Asset.SOL,))

    assert any(
        "尚未經查證" in note and "停止運作" in note
        for note in requirement.boundary_notes
    )


def test_plain_question_has_no_verification_boundary_note() -> None:
    """一般問句不該被誤判成帶著未證實斷言。"""
    requirement = derive_requirement("SOL 現況如何", (Asset.SOL,))

    assert not any("尚未經查證" in note for note in requirement.boundary_notes)


def test_live_market_summary_requires_all_four_facets() -> None:
    """即時模式:市場摘要維持四面向覆蓋(既有行為不變)。"""
    requirement = derive_requirement(
        "BTC 現況如何", (Asset.BTC,), regime=AnalysisRegime.LIVE
    )

    assert requirement.question_type is QuestionType.MARKET_SUMMARY
    assert requirement.required_facets == frozenset(Facet)
    assert requirement.unavailable_facets == frozenset()


def test_backtest_market_summary_focuses_technical_and_marks_rest_unavailable() -> None:
    """回測模式:市場摘要只要求技術面,另三面標為資料不可得(非未關閉缺口)。

    保留原題型的意圖 —— unavailable_facets 記下「本該覆蓋、但回測下取不到」的面,
    _assemble 之後會把它們轉成明確的限制說明。
    """
    requirement = derive_requirement(
        "BTC 現況如何", (Asset.BTC,), regime=AnalysisRegime.BACKTEST
    )

    assert requirement.question_type is QuestionType.MARKET_SUMMARY
    assert requirement.required_facets == frozenset({Facet.TECHNICAL})
    assert requirement.unavailable_facets == frozenset(
        {Facet.POSITIONING, Facet.FUNDAMENTAL, Facet.SENTIMENT}
    )


def _evidence_on(day: date, facet: Facet, stance_hint: float, source: str) -> Evidence:
    """建一項證據,其來源片段擷取於指定日的 UTC 收盤時刻。"""
    return Evidence(
        id=f"{facet.value}-{source}",
        facet=facet,
        summary=f"{facet.value} from {source}",
        stance_hint=stance_hint,
        excerpts=(
            SourceExcerpt(
                source_id=source,
                url=f"https://example.test/{source}",
                retrieved_at=datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC),
                locator="row-1",
                text="片段",
            ),
        ),
        event_key=None,
    )


def test_request_defaults_as_of_date_to_dataset_cutoff() -> None:
    """未指定截止日時,預設為資料集截止日 2026-05-31。"""
    request = AnalysisRequest(asset="BTC")

    assert request.as_of_date == DATASET_CUTOFF


def test_request_preserves_explicit_as_of_date() -> None:
    """明確帶入的截止日必須原樣保留 —— 它是時間立足點,不可被覆寫。"""
    request = AnalysisRequest(asset="BTC", as_of_date=date(2025, 1, 15))

    assert request.as_of_date == date(2025, 1, 15)


def test_past_as_of_date_is_backtest() -> None:
    """截止日早於今天 → 歷史回測模式。"""
    regime = analysis_regime(date(2026, 5, 31), today=date(2026, 7, 27))

    assert regime is AnalysisRegime.BACKTEST


def test_as_of_date_equal_to_today_is_live() -> None:
    """截止日等於今天 → 即時模式(邊界:今天可知的資料是合規的)。"""
    regime = analysis_regime(date(2026, 7, 27), today=date(2026, 7, 27))

    assert regime is AnalysisRegime.LIVE


def test_future_as_of_date_is_live() -> None:
    """截止日不早於今天 → 即時模式。"""
    regime = analysis_regime(date(2026, 8, 1), today=date(2026, 7, 27))

    assert regime is AnalysisRegime.LIVE


def test_confidence_freshness_anchors_to_as_of_not_wall_clock() -> None:
    """同一批證據,在其自身截止日評估時信心度應高於數月後評估。

    這正是被修掉的 bug:新鮮度過去錨定在 datetime.now(),使歷史證據永遠被
    當成過期,系統性壓低信心度。錨定到 as_of 後,回測證據取回滿分新鮮度。
    """
    day = date(2026, 5, 31)
    evidence = (
        _evidence_on(day, Facet.TECHNICAL, 0.6, "dataset:BTC"),
        _evidence_on(day, Facet.POSITIONING, 0.5, "binance:derivatives"),
    )

    fresh = assess_confidence(evidence, as_of=day)
    months_later = assess_confidence(evidence, as_of=date(2026, 8, 31))

    assert isinstance(fresh, Confidence)
    assert isinstance(months_later, Confidence)
    assert fresh.value > months_later.value


@pytest.mark.asyncio
async def test_dataset_evidence_stamped_at_as_of_day_not_wall_clock() -> None:
    """資料集證據的 retrieved_at 必須是它所代表的那一日,不是抓取的現實時刻。

    以現實時鐘蓋章會讓歷史證據看起來「來自未來」(look-ahead bias),使我們剛
    錨定的 as_of 新鮮度評估的是一個不可能發生的現實。日 K 代表當日收盤狀態,
    因此蓋章在該日 UTC 收盤(23:59:59)。
    """
    from decimal import Decimal
    from pathlib import Path

    from hoyabit_agent.ingest.documents import MarketDocument, MarketIndicators, OhlcvBar
    from hoyabit_agent.ingest.embeddings import Vector
    from hoyabit_agent.ingest.historical import MarketDatasetEvidenceSource
    from hoyabit_agent.ingest.memory_store import InMemoryMarketDocumentStore

    class FakeEmbedder:
        dimensions = 2
        model = "gemini-embedding-001"

        async def embed(self, texts: list[str]) -> tuple[Vector, ...]:
            return tuple((1.0, 0.0) for _ in texts)

    day = date(2026, 5, 31)
    bar = OhlcvBar(day, *(Decimal("1") for _ in range(5)))
    document = MarketDocument(
        asset=Asset.BTC,
        as_of_date=day,
        ohlcv=(bar,),
        indicators=MarketIndicators(*(None for _ in range(9))),
        window_complete=False,
        source_file=Path("BTC_daily_ohlcv.csv"),
        source_row_start=2,
        source_row_end=2,
    )
    store = InMemoryMarketDocumentStore()
    await store.upsert([(document, (1.0, 0.0))], embedding_model="gemini-embedding-001")

    source = MarketDatasetEvidenceSource(store, FakeEmbedder())
    (evidence,) = await source.fetch(Asset.BTC, {"as_of_date": "2026-05-31"})

    assert evidence.excerpts[0].retrieved_at == datetime(2026, 5, 31, 23, 59, 59, tzinfo=UTC)


def test_report_to_markdown_renders_limitations_for_judges() -> None:
    """Report.to_markdown() —— 評審看的 final_report.md 路徑 —— 必須呈現限制。

    這是提交物與 CLI 用的渲染器;若限制只出現在 enhanced 版(前端),看
    final_report.md 的評審就看不到系統誠實界定的邊界。
    """
    from hoyabit_agent.domain import Confidence, Report, Stance

    report = Report(
        asset=Asset.BTC,
        stance=Stance.NEUTRAL,
        confidence=Confidence(value=0.5, facet_stances={}),
        claims=(),
        dropped_claims=(),
        evidence=(),
        question="BTC 現況如何",
        limitations=("sentiment 面資料不可得（回測模式僅有資料集 OHLCV）",),
    )

    markdown = report.to_markdown()

    assert "sentiment 面資料不可得" in markdown


def test_enhanced_report_renders_dynamic_limitations_not_static_lies() -> None:
    """報告的「已知限制」必須渲染 report.limitations,而非寫死的清單。

    寫死「社群情緒資料未接入」在即時模式下系統已抓到情緒資料時會自打臉 ——
    限制必須反映本回合的真實狀態,由 report.limitations 動態產生。
    """
    from hoyabit_agent.domain import AnalysisOutcome, Confidence, Report, Stance, Trace
    from hoyabit_agent.report_enhanced import enhanced_report_markdown

    report = Report(
        asset=Asset.BTC,
        stance=Stance.NEUTRAL,
        confidence=Confidence(value=0.5, facet_stances={}),
        claims=(),
        dropped_claims=(),
        evidence=(),
        question="BTC 現況如何",
        limitations=("sentiment 面資料不可得（回測模式僅有資料集 OHLCV，無合規的即時來源）",),
    )
    outcome = AnalysisOutcome(
        run_id="run-x", report=report, trace=Trace(run_id="run-x", nodes=()), rejection=None
    )

    markdown = enhanced_report_markdown(outcome)

    assert "sentiment 面資料不可得" in markdown
    assert "社群情緒資料未接入" not in markdown


@pytest.mark.asyncio
async def test_backtest_regime_never_calls_or_offers_a_live_only_source() -> None:
    """回測模式下,live-only 來源必須被物理擋下 —— 不只是丟棄結果。

    這是防堵「模型幻覺呼叫 live 工具」的實體鎖:即使模型的腳本試圖呼叫兩個
    工具,live-only 來源在回測下**根本不該出現在模型看到的 tools 清單裡**,
    也不該被實際 fetch。只過濾結果、不過濾清單,無法防住模型自己選中它。
    """
    from hoyabit_agent.domain import DraftClaim
    from hoyabit_agent.run import analyse
    from hoyabit_agent.testing import ScriptedModel, StaticSource

    day = date(2026, 5, 31)
    dataset_evidence = _evidence_on(day, Facet.TECHNICAL, 0.6, "dataset:BTC")
    live_evidence = _evidence_on(day, Facet.POSITIONING, 0.5, "binance:derivatives")

    dataset_source = StaticSource(
        [dataset_evidence],
        name="market_dataset_context",
        supported_regimes=frozenset({AnalysisRegime.BACKTEST, AnalysisRegime.LIVE}),
    )
    live_source = StaticSource(
        [live_evidence],
        name="binance_derivatives",
        supported_regimes=frozenset({AnalysisRegime.LIVE}),
    )
    model = ScriptedModel(
        plans=[("market_dataset_context,binance_derivatives", "嘗試蒐集技術與籌碼面")],
        claims=[DraftClaim("技術面偏多", (dataset_evidence.id,), Facet.TECHNICAL)],
    )

    outcome = await analyse(
        AnalysisRequest("BTC", "BTC 現況如何", as_of_date=day),
        [dataset_source, live_source],
        model,
        today=date(2026, 7, 27),
    )

    # 模型從未被告知 live-only 工具存在。
    offered_names = {tool.name for tools in model.seen_tools for tool in tools}
    assert "binance_derivatives" not in offered_names
    assert "market_dataset_context" in offered_names

    # live-only 來源從未被實際呼叫。
    assert live_source.received == []

    # 報告只含合規來源的證據。
    assert outcome.report is not None
    assert all(item.facet is not Facet.POSITIONING for item in outcome.report.evidence)


@pytest.mark.asyncio
async def test_forecast_question_boundary_note_reaches_report_limitations() -> None:
    """預測型問題的邊界聲明必須進到 Report.limitations,而非只留在 trace。

    這是評審讀 final_report.md 會看到的最後一哩:系統誠實聲明「不做預測」,
    而不是靜默地給出一個看似篤定、實則毫無依據的方向研判。
    """
    from hoyabit_agent.domain import DraftClaim
    from hoyabit_agent.run import analyse
    from hoyabit_agent.testing import ScriptedModel, StaticSource

    day = date(2026, 5, 31)
    ev = _evidence_on(day, Facet.TECHNICAL, 0.6, "dataset:BTC")
    source = StaticSource([ev], name="market_dataset_context")
    model = ScriptedModel(
        plans=[("market_dataset_context", "深挖技術面")],
        claims=[DraftClaim("技術面偏多", (ev.id,), Facet.TECHNICAL)],
    )

    outcome = await analyse(
        AnalysisRequest("BTC", "請預測 BTC 下週的走勢", as_of_date=day),
        [source],
        model,
        today=date(2026, 7, 27),
    )

    assert outcome.report is not None
    assert "本系統輸出當前方向研判，不做未來價格預測" in outcome.report.limitations


@pytest.mark.asyncio
async def test_backtest_report_declares_unavailable_facets_as_limitations() -> None:
    """回測模式:報告的 limitations 必須明列三個取不到的面為資料不可得。

    這是「識別限制」評分項的最後一哩:降級的意圖不能只留在 trace,必須進到
    評審會看的 Report 物件。analyse() 依 as_of_date 與注入的 today 推導模式。
    """
    from hoyabit_agent.domain import DraftClaim
    from hoyabit_agent.run import analyse
    from hoyabit_agent.testing import ScriptedModel, StaticSource

    day = date(2026, 5, 31)
    ev = _evidence_on(day, Facet.TECHNICAL, 0.6, "dataset:BTC")
    source = StaticSource([ev], name="market_dataset_context")
    model = ScriptedModel(
        plans=[("market_dataset_context", "深挖技術面")],
        claims=[DraftClaim("技術面偏多", (ev.id,), Facet.TECHNICAL)],
    )

    outcome = await analyse(
        AnalysisRequest("BTC", "BTC 現況如何", as_of_date=day),
        [source],
        model,
        today=date(2026, 7, 27),
    )

    assert outcome.report is not None
    joined = " ".join(outcome.report.limitations)
    assert "資料不可得" in joined
    for facet in ("positioning", "fundamental", "sentiment"):
        assert facet in joined


@pytest.mark.asyncio
async def test_analyse_threads_as_of_into_confidence() -> None:
    """run.analyse() 必須把 request.as_of_date 傳進信心度評估。

    這是 seam 3 的整合契約:若 run.py 忘了傳 as_of,信心度會退回現實時鐘,
    把回測證據當成過期,產出較低的值。斷言報告信心度等於以 as_of 錨定的值。
    """
    from hoyabit_agent.domain import DraftClaim
    from hoyabit_agent.run import analyse
    from hoyabit_agent.testing import ScriptedModel, StaticSource

    day = date(2026, 5, 31)
    gathered = (
        _evidence_on(day, Facet.TECHNICAL, 0.6, "src-a"),
        _evidence_on(day, Facet.POSITIONING, 0.5, "src-b"),
    )
    source = StaticSource(list(gathered), name="market")
    model = ScriptedModel(
        plans=[("market", "蒐集技術與籌碼面")],
        claims=[DraftClaim("偏多", (gathered[0].id,), Facet.TECHNICAL)],
    )

    outcome = await analyse(
        AnalysisRequest("BTC", "BTC 現況如何", as_of_date=day),
        [source],
        model,
    )

    expected = assess_confidence(gathered, as_of=day)
    assert isinstance(outcome.report, object) and outcome.report is not None
    assert isinstance(expected, Confidence)
    assert outcome.report.confidence == expected
