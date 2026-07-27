"""持久化測試 —— 打真實 Postgres，避免 schema 漂移。

測試打的是接縫 4（`AnalysisStore`）的介面，不查資料表 ——
從 SQL 側面驗證會讓測試在重構 schema 時破掉，即使行為沒變。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hoyabit_agent.domain import (
    AnalysisOutcome,
    Asset,
    Claim,
    Confidence,
    DraftClaim,
    Evidence,
    Facet,
    Insufficiency,
    InsufficientEvidence,
    Rejection,
    Report,
    SourceExcerpt,
    Stance,
    ToolExecutionRecord,
    ToolExecutionStatus,
    Trace,
    TraceNode,
    TraceNodeKind,
)
from hoyabit_agent.storage.postgres import PostgresAnalysisStore, database_url
from hoyabit_agent.testing import evidence

NOW = datetime(2026, 7, 23, tzinfo=UTC)

pytestmark = pytest.mark.postgres


def with_evidence(*items: Evidence, run_id: str = "run-1") -> AnalysisOutcome:
    """一個只在證據上有差異的回合，讓每個測試只講一件事。"""
    base = an_outcome(run_id)
    assert base.report is not None
    report = Report(
        asset=base.report.asset,
        stance=base.report.stance,
        confidence=base.report.confidence,
        claims=(),
        dropped_claims=(),
        evidence=items,
    )
    return AnalysisOutcome(run_id, report=report, trace=base.trace, rejection=None)


def an_outcome(run_id: str = "run-1") -> AnalysisOutcome:
    found = (evidence("E1", Facet.TECHNICAL, 0.8, text="收盤站上季線"),)
    report = Report(
        asset=Asset.BTC,
        stance=Stance.BULLISH,
        confidence=Confidence(value=1.0, facet_stances={f: Stance.NEUTRAL for f in Facet}),
        claims=(Claim("站上季線", ("E1",), Facet.TECHNICAL),),
        dropped_claims=(DraftClaim("沒有根據的話", (), Facet.SENTIMENT),),
        evidence=found,
    )
    trace = Trace(
        run_id=run_id,
        nodes=(
            TraceNode(seq=0, kind=TraceNodeKind.ASSET_GATE, reason="BTC 為受涵蓋幣種"),
            TraceNode(
                seq=1,
                kind=TraceNodeKind.GATHER,
                reason="抓技術面",
                evidence_ids=("E1",),
                gap_before=frozenset(Facet),
                gap_after=frozenset({Facet.SENTIMENT}),
            ),
        ),
    )
    return AnalysisOutcome(run_id=run_id, report=report, trace=trace, rejection=None)


async def test_a_saved_run_can_be_loaded_back(store: PostgresAnalysisStore) -> None:
    await store.save(an_outcome())
    loaded = await store.load("run-1")

    assert loaded is not None
    assert loaded.run_id == "run-1"
    assert loaded.report is not None
    assert loaded.report.stance is Stance.BULLISH


async def test_loading_a_run_that_was_never_saved_returns_nothing(
    store: PostgresAnalysisStore,
) -> None:
    assert await store.load("no-such-run") is None


async def test_report_limitations_survive_the_round_trip(
    store: PostgresAnalysisStore,
) -> None:
    """限制是一等輸出 —— 重載歷史回合時必須帶回,否則前端歷史會遺失誠實邊界。"""
    base = an_outcome()
    assert base.report is not None
    report = Report(
        asset=base.report.asset,
        stance=base.report.stance,
        confidence=base.report.confidence,
        claims=base.report.claims,
        dropped_claims=base.report.dropped_claims,
        evidence=base.report.evidence,
        question=base.report.question,
        limitations=(
            "positioning 面資料不可得（回測模式僅有資料集 OHLCV，無合規的即時來源）",
            "sentiment 面資料不可得（回測模式僅有資料集 OHLCV，無合規的即時來源）",
        ),
    )
    await store.save(AnalysisOutcome("run-1", report=report, trace=base.trace, rejection=None))

    loaded = await store.load("run-1")

    assert loaded is not None and loaded.report is not None
    assert loaded.report.limitations == report.limitations


# --------------------------------------------------------------------------
# 溯源：判斷 → 證據 → 來源片段
# --------------------------------------------------------------------------


async def test_every_source_excerpt_survives_the_round_trip(
    store: PostgresAnalysisStore,
) -> None:
    """歸併後的證據帶多份來源片段 —— 一份都不能掉，那是溯源的全部價值。"""
    merged = Evidence(
        id="E1",
        facet=Facet.SENTIMENT,
        summary="兩家媒體報導同一事件",
        stance_hint=0.9,
        excerpts=(
            SourceExcerpt("a", "https://a.test/1", NOW, "para-1", "甲媒體的原文"),
            SourceExcerpt("b", "https://b.test/2", NOW, "para-3", "乙媒體的原文"),
        ),
        event_key="EVT-etf",
    )
    await store.save(with_evidence(merged))

    loaded = await store.load("run-1")
    assert loaded is not None and loaded.report is not None
    item = loaded.report.evidence[0]
    assert [e.url for e in item.excerpts] == ["https://a.test/1", "https://b.test/2"]
    assert [e.text for e in item.excerpts] == ["甲媒體的原文", "乙媒體的原文"]
    assert item.event_key == "EVT-etf"


async def test_a_claim_can_be_traced_back_to_the_evidence_it_cites(
    store: PostgresAnalysisStore,
) -> None:
    await store.save(an_outcome())
    loaded = await store.load("run-1")

    assert loaded is not None and loaded.report is not None
    claim = loaded.report.claims[0]
    cited = {item.id for item in loaded.report.evidence}
    assert set(claim.evidence_ids) <= cited
    assert loaded.report.evidence[0].excerpts[0].text == "收盤站上季線"


async def test_dropped_claims_are_kept_too(store: PostgresAnalysisStore) -> None:
    """軌跡前端要顯示「系統拒絕了什麼」—— 那是引用檢核確實在運作的證明。"""
    await store.save(an_outcome())
    loaded = await store.load("run-1")

    assert loaded is not None and loaded.report is not None
    assert [d.text for d in loaded.report.dropped_claims] == ["沒有根據的話"]
    assert [c.text for c in loaded.report.claims] == ["站上季線"]


async def test_evidence_order_is_preserved(store: PostgresAnalysisStore) -> None:
    outcome = with_evidence(
        evidence("E1", Facet.TECHNICAL, 0.5),
        evidence("E2", Facet.SENTIMENT, -0.5),
        evidence("E3", Facet.FUNDAMENTAL, 0.1),
    )
    await store.save(outcome)

    loaded = await store.load("run-1")
    assert loaded is not None and loaded.report is not None
    assert [item.id for item in loaded.report.evidence] == ["E1", "E2", "E3"]


# --------------------------------------------------------------------------
# 情緒分數的層級（ADR 0002）
# --------------------------------------------------------------------------


async def test_there_is_no_asset_level_sentiment_score_column(
    store: PostgresAnalysisStore,
) -> None:
    """不存在「BTC 的情緒分數」—— 那是情緒彙總，必須能列舉出組成它的每一則片段。

    這條刻意查 schema 而非行為：它守的是一個**結構**上的不變式，
    一旦有人在 analysis_run 加了幣種層級的分數欄位，溯源就從那裡開始崩。
    """
    import psycopg

    async with await psycopg.AsyncConnection.connect(database_url()) as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'analysis_run'"
            )
            columns = {str(row[0]) for row in await cursor.fetchall()}

    assert not {name for name in columns if "sentiment" in name or "score" in name}


async def test_each_excerpt_keeps_its_own_evidence_stance(
    store: PostgresAnalysisStore,
) -> None:
    """同一個面的兩項證據可以有相反的傾向 —— 存進去不能被抹平成一個數字。"""
    await store.save(
        with_evidence(
            evidence("E1", Facet.SENTIMENT, 0.9),
            evidence("E2", Facet.SENTIMENT, -0.9),
        )
    )
    loaded = await store.load("run-1")

    assert loaded is not None and loaded.report is not None
    assert sorted(item.stance_hint for item in loaded.report.evidence) == [-0.9, 0.9]


# --------------------------------------------------------------------------
# 推論軌跡
# --------------------------------------------------------------------------


async def test_the_trace_comes_back_in_order(store: PostgresAnalysisStore) -> None:
    """軌跡的意義有一半在「先後」。"""
    await store.save(an_outcome())
    loaded = await store.load("run-1")

    assert loaded is not None
    assert [node.seq for node in loaded.trace.nodes] == [0, 1]
    assert loaded.trace.nodes[0].kind is TraceNodeKind.ASSET_GATE
    assert loaded.trace.nodes[1].kind is TraceNodeKind.GATHER


async def test_the_gap_change_on_each_node_survives(store: PostgresAnalysisStore) -> None:
    """缺口如何驅動下一步，是評審要看的「點對點之間為什麼」。"""
    await store.save(an_outcome())
    loaded = await store.load("run-1")

    assert loaded is not None
    gather = loaded.trace.nodes[1]
    assert gather.gap_before == frozenset(Facet)
    assert gather.gap_after == frozenset({Facet.SENTIMENT})
    assert gather.evidence_ids == ("E1",)


async def test_the_tool_arguments_the_model_chose_survive(
    store: PostgresAnalysisStore,
) -> None:
    """模型自己選的參數是「這是真推理」最直接的證據，不能在存取之間掉。"""
    outcome = an_outcome()
    trace = Trace(
        run_id="run-1",
        nodes=(
            TraceNode(
                seq=0,
                kind=TraceNodeKind.PLAN,
                reason="技術面全缺",
                executions=(
                    ToolExecutionRecord(
                        "binance_spot", Asset.BTC, {"interval": "4h"},
                        ToolExecutionStatus.PLANNED,
                    ),
                ),
            ),
        ),
    )
    await store.save(
        AnalysisOutcome(run_id="run-1", report=outcome.report, trace=trace, rejection=None)
    )

    loaded = await store.load("run-1")
    assert loaded is not None
    assert loaded.trace.nodes[0].executions[0].arguments == {"interval": "4h"}


# --------------------------------------------------------------------------
# 信心度
# --------------------------------------------------------------------------


async def test_a_computed_confidence_survives(store: PostgresAnalysisStore) -> None:
    await store.save(an_outcome())
    loaded = await store.load("run-1")

    assert loaded is not None and loaded.report is not None
    confidence = loaded.report.confidence
    assert isinstance(confidence, Confidence)
    assert confidence.value == 1.0
    assert set(confidence.facet_stances) == set(Facet)


async def test_an_uncomputable_confidence_keeps_its_reason(
    store: PostgresAnalysisStore,
) -> None:
    """「證據面太少」與「無方向訊號」對讀者的意義不同，不能存成同一件事。"""
    outcome = an_outcome()
    assert outcome.report is not None
    report = Report(
        asset=outcome.report.asset,
        stance=Stance.NEUTRAL,
        confidence=InsufficientEvidence(
            facets_present=frozenset({Facet.TECHNICAL}),
            minimum_facets_required=2,
            cause=Insufficiency.NO_DIRECTIONAL_SIGNAL,
            facet_stances={facet: Stance.NEUTRAL for facet in Facet},
        ),
        claims=(),
        dropped_claims=(),
        evidence=outcome.report.evidence,
    )
    await store.save(
        AnalysisOutcome("run-1", report=report, trace=outcome.trace, rejection=None)
    )

    loaded = await store.load("run-1")
    assert loaded is not None and loaded.report is not None
    confidence = loaded.report.confidence
    assert isinstance(confidence, InsufficientEvidence)
    assert confidence.cause is Insufficiency.NO_DIRECTIONAL_SIGNAL
    assert confidence.facets_present == frozenset({Facet.TECHNICAL})


# --------------------------------------------------------------------------
# 被拒絕的回合、冪等、列表
# --------------------------------------------------------------------------


async def test_a_rejected_run_is_still_recorded(store: PostgresAnalysisStore) -> None:
    """「我們拒絕了什麼」本身就是要能稽核的事。"""
    rejected = AnalysisOutcome(
        run_id="run-doge",
        report=None,
        trace=Trace(
            run_id="run-doge",
            nodes=(
                TraceNode(seq=0, kind=TraceNodeKind.ASSET_GATE, reason="DOGE 不在受涵蓋幣種內"),
            ),
        ),
        rejection=Rejection(reason="DOGE 不在受涵蓋幣種內，不予分析"),
    )
    await store.save(rejected)

    loaded = await store.load("run-doge")
    assert loaded is not None
    assert loaded.report is None
    assert loaded.rejection is not None
    assert "DOGE" in loaded.rejection.reason
    assert len(loaded.trace.nodes) == 1


async def test_saving_the_same_run_twice_does_not_duplicate_anything(
    store: PostgresAnalysisStore,
) -> None:
    await store.save(an_outcome())
    await store.save(an_outcome())

    loaded = await store.load("run-1")
    assert loaded is not None and loaded.report is not None
    assert len(loaded.report.evidence) == 1
    assert len(loaded.report.claims) == 1
    assert len(loaded.trace.nodes) == 2
    assert await store.recent() == ("run-1",)


async def test_resaving_a_run_replaces_the_earlier_version(
    store: PostgresAnalysisStore,
) -> None:
    await store.save(an_outcome())
    await store.save(with_evidence(evidence("E9", Facet.POSITIONING, -0.3)))

    loaded = await store.load("run-1")
    assert loaded is not None and loaded.report is not None
    assert [item.id for item in loaded.report.evidence] == ["E9"]


async def test_recent_lists_runs_newest_first(store: PostgresAnalysisStore) -> None:
    for name in ("run-1", "run-2", "run-3"):
        await store.save(an_outcome(name))

    assert await store.recent() == ("run-3", "run-2", "run-1")


async def test_recent_respects_the_limit(store: PostgresAnalysisStore) -> None:
    for name in ("run-1", "run-2", "run-3"):
        await store.save(an_outcome(name))

    assert len(await store.recent(limit=2)) == 2


async def test_recent_is_empty_before_anything_is_saved(
    store: PostgresAnalysisStore,
) -> None:
    assert await store.recent() == ()


async def test_a_store_can_be_used_before_any_migration_is_run(
    store: PostgresAnalysisStore,
) -> None:
    """呼叫端不該需要記得先 migrate —— 那種必須記住的步驟遲早會被忘記。"""
    assert await store.load("anything") is None
    assert await store.recent() == ()
